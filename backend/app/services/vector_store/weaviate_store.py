"""
app.services.vector_store.weaviate_store
----------------------------------------
Weaviate Cloud collection holding GitHub repo chunks, with self-provided
(bring-your-own) vectors produced by :mod:`app.services.vector_store.voyage_client`.

Replaces the former BigQuery vector store. One shared collection holds every
user's chunks; isolation comes from ``repo_username`` / ``repo_name`` property
filters applied on every read (see :func:`build_filter`).

Design notes:

* **Client lifetime.** The v4 client owns HTTP and gRPC connection pools, so it
  is created once per process and closed at exit. The crew runs under
  ``crew_runner._executor`` (a ThreadPoolExecutor), hence the lock around
  construction.

* **Deterministic object IDs.** Each chunk's UUID is a UUIDv5 of
  ``owner/repo/file_path#chunk_index``, so re-indexing a repo *overwrites* its
  chunks instead of appending a second copy — the old BigQuery path had no such
  key and silently duplicated on every re-run.

* **``Tokenization.FIELD`` on the filter properties is load-bearing.** With
  Weaviate's default ``WORD`` tokenization, ``Filter.by_property("repo_name")
  .equal("Online_Programming_Assignment_Portal")`` matches on individual tokens
  rather than the whole string, which would leak other repos into a filtered
  search.
"""

from __future__ import annotations

import atexit
import threading
from typing import Any, Dict, List, Optional, Sequence

import weaviate
from weaviate.classes.config import Configure, DataType, Property, Tokenization
from weaviate.classes.init import AdditionalConfig, Auth, Timeout
from weaviate.classes.query import Filter, FilterReturn, MetadataQuery
from weaviate.util import generate_uuid5

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_client: weaviate.WeaviateClient | None = None
_client_lock = threading.Lock()
_collection_ready = False

# Batch import tuning. Weaviate accepts far larger batches, but 200 objects of
# ~1000 characters each keeps a single request comfortably sized.
_IMPORT_BATCH_SIZE = 200
_IMPORT_CONCURRENCY = 2


def _normalise_url(url: str) -> str:
    """
    Accept a bare cluster host as well as a full URL.

    The value copied out of the Weaviate Cloud console is usually a bare host
    (``xyz.c0.eu-central-1.aws.weaviate.cloud``), which the client rejects.
    """
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    return url


def get_client() -> weaviate.WeaviateClient:
    """Return the process-wide Weaviate client, connecting on first use."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                settings = get_settings()
                if not settings.WEAVIATE_URL or not settings.WEAVIATE_API_KEY:
                    raise RuntimeError(
                        "WEAVIATE_URL / WEAVIATE_API_KEY are not set — add them "
                        "to backend/.env (see .env.example)."
                    )
                _client = weaviate.connect_to_weaviate_cloud(
                    cluster_url=_normalise_url(settings.WEAVIATE_URL),
                    auth_credentials=Auth.api_key(settings.WEAVIATE_API_KEY),
                    additional_config=AdditionalConfig(
                        timeout=Timeout(init=30, query=60, insert=120)
                    ),
                )
                logger.info("weaviate_store: Connected to Weaviate Cloud.")
    return _client


def close_client() -> None:
    """Close the client and release its connection pools."""
    global _client, _collection_ready
    with _client_lock:
        if _client is not None:
            try:
                _client.close()
                logger.info("weaviate_store: Weaviate client closed.")
            except Exception as e:
                logger.warning("weaviate_store: Error closing Weaviate client: %s", e)
            _client = None
            _collection_ready = False


atexit.register(close_client)


def ensure_collection():
    """
    Create the collection if it does not exist, and return it.

    Idempotent and cheap after the first call — the ``exists`` round trip is
    skipped once this process has confirmed the collection.
    """
    global _collection_ready
    settings = get_settings()
    name = settings.WEAVIATE_COLLECTION_NAME
    client = get_client()

    if not _collection_ready:
        with _client_lock:
            if not _collection_ready:
                if client.collections.exists(name):
                    logger.info("weaviate_store: Using existing collection '%s'.", name)
                else:
                    client.collections.create(
                        name,
                        # Vectors are supplied by Voyage, not by a Weaviate
                        # vectorizer module. (Note: the older
                        # `vectorizer_config=Configure.Vectorizer.none()` form
                        # is deprecated in favour of this one.)
                        vector_config=Configure.Vectors.self_provided(),
                        properties=[
                            Property(name="content", data_type=DataType.TEXT),
                            Property(
                                name="repo_username", data_type=DataType.TEXT,
                                tokenization=Tokenization.FIELD, index_searchable=False,
                            ),
                            Property(
                                name="repo_name", data_type=DataType.TEXT,
                                tokenization=Tokenization.FIELD, index_searchable=False,
                            ),
                            Property(
                                name="file_path", data_type=DataType.TEXT,
                                tokenization=Tokenization.FIELD, index_searchable=False,
                            ),
                            Property(name="chunk_index", data_type=DataType.INT),
                        ],
                    )
                    logger.info("weaviate_store: Created collection '%s'.", name)
                _collection_ready = True

    return client.collections.use(name)


def build_filter(
    repo_username: Optional[str] = None,
    repo_names: Optional[Sequence[str]] = None,
) -> Optional[FilterReturn]:
    """
    Compose the tenancy filter for a search.

    ``repo_username`` scopes results to one GitHub account — this is what keeps
    one applicant's crew from retrieving another applicant's repo content.
    ``repo_names`` optionally narrows further to specific repositories.
    """
    clauses: List[FilterReturn] = []
    if repo_username:
        clauses.append(Filter.by_property("repo_username").equal(repo_username))
    if repo_names:
        clauses.append(Filter.by_property("repo_name").contains_any(list(repo_names)))
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else Filter.all_of(clauses)


def repo_exists(owner: str, repo: str) -> bool:
    """
    True when this repo already has chunks indexed.

    Replaces the previous BigQuery ``COUNT(*)`` check, which f-string
    interpolated ``owner`` / ``repo`` straight into SQL.
    """
    collection = ensure_collection()
    try:
        result = collection.aggregate.over_all(
            filters=build_filter(repo_username=owner, repo_names=[repo]),
            total_count=True,
        )
    except Exception as e:
        # Treat an aggregate failure as "unknown" and let ingestion proceed —
        # deterministic UUIDs make a redundant re-index harmless.
        logger.warning(
            "weaviate_store: Existence check failed for %s/%s (%s); proceeding with indexing.",
            owner, repo, e,
        )
        return False
    return (result.total_count or 0) > 0


def upsert_chunks(
    owner: str,
    repo: str,
    chunks: Sequence[Dict[str, Any]],
    vectors: Sequence[Sequence[float]],
) -> int:
    """
    Insert or overwrite chunk objects.

    ``chunks`` items carry ``content``, ``file_path`` and ``chunk_index``;
    ``vectors`` is the parallel list of embeddings. Returns the number of
    objects successfully written.
    """
    if len(chunks) != len(vectors):
        raise ValueError(
            f"chunk/vector count mismatch: {len(chunks)} chunks vs {len(vectors)} vectors"
        )
    if not chunks:
        return 0

    collection = ensure_collection()
    with collection.batch.fixed_size(
        batch_size=_IMPORT_BATCH_SIZE, concurrent_requests=_IMPORT_CONCURRENCY
    ) as batch:
        for chunk, vector in zip(chunks, vectors):
            file_path = chunk["file_path"]
            chunk_index = chunk["chunk_index"]
            batch.add_object(
                properties={
                    "content": chunk["content"],
                    "repo_username": owner,
                    "repo_name": repo,
                    "file_path": file_path,
                    "chunk_index": chunk_index,
                },
                vector=list(vector),
                uuid=generate_uuid5(f"{owner}/{repo}/{file_path}#{chunk_index}"),
            )

    failed = collection.batch.failed_objects
    if failed:
        logger.error(
            "weaviate_store: %d of %d objects failed to import for %s/%s. First error: %s",
            len(failed), len(chunks), owner, repo, failed[0].message,
        )

    # Vector indexing is asynchronous: objects are queryable by filter as soon
    # as the batch returns, but near_vector will not see them until the HNSW
    # index catches up. Without this wait, a search issued right after
    # ingestion silently returns zero results.
    try:
        collection.batch.wait_for_vector_indexing()
    except Exception as e:
        logger.warning(
            "weaviate_store: Could not confirm vector indexing for %s/%s: %s", owner, repo, e,
        )

    return len(chunks) - len(failed)


def search(
    vector: Sequence[float],
    limit: int,
    repo_username: Optional[str] = None,
    repo_names: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Vector search, returning candidate dicts ordered by ascending distance.

    Each result carries ``content`` plus its metadata (``repo_username``,
    ``repo_name``, ``file_path``, ``chunk_index``, ``distance``). Only
    ``distance`` is meaningful here — Weaviate populates ``score`` /
    ``explain_score`` for BM25 and hybrid queries, not for ``near_vector``.
    """
    collection = ensure_collection()
    response = collection.query.near_vector(
        near_vector=list(vector),
        limit=limit,
        filters=build_filter(repo_username, repo_names),
        return_metadata=MetadataQuery(distance=True),
    )
    return [
        {
            "content": obj.properties.get("content", ""),
            "repo_username": obj.properties.get("repo_username"),
            "repo_name": obj.properties.get("repo_name"),
            "file_path": obj.properties.get("file_path"),
            "chunk_index": obj.properties.get("chunk_index"),
            "distance": obj.metadata.distance,
        }
        for obj in response.objects
    ]


def count_chunks(
    repo_username: Optional[str] = None,
    repo_names: Optional[Sequence[str]] = None,
) -> int:
    """Number of indexed chunks matching the filter. Used by tests and the CLI."""
    collection = ensure_collection()
    result = collection.aggregate.over_all(
        filters=build_filter(repo_username, repo_names), total_count=True
    )
    return result.total_count or 0
