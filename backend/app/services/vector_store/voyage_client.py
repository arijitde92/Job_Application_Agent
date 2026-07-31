"""
app.services.vector_store.voyage_client
---------------------------------------
Voyage AI embeddings and reranking for the GitHub RAG pipeline.

Two models, both configurable via ``Settings``:

* ``voyage-code-3``    — embeddings. Code-specialised, which is what the corpus
  is (``.py`` / ``.ipynb`` / ``.md`` / ``.txt`` repo files).
* ``rerank-2.5-lite``  — second-stage reranker. The vector search over-fetches
  candidates and the reranker orders them by actual relevance to the query.

Three things here are load-bearing:

1. **``input_type`` is asymmetric.** Documents must be embedded with
   ``input_type="document"`` and queries with ``input_type="query"``. Voyage
   prepends a different prompt in each case; mixing them silently degrades
   retrieval quality with no error.

2. **Embedding requests have two simultaneous limits** — at most 1000 texts AND
   at most 120,000 tokens per request. Batching on text count alone
   intermittently 400s on token-dense files, so :func:`_iter_batches` packs
   greedily against both. Note that ``voyageai.VOYAGE_EMBED_BATCH_SIZE`` (128)
   is a stale internal SDK constant, not the API limit — do not use it.

3. **Rerank billing is quadratic in the query.** Voyage charges
   ``query_tokens x n_documents + sum(document_tokens)``, against a 600k
   per-request cap. The caller composes queries that embed a whole job
   description, so queries are truncated to :data:`MAX_QUERY_CHARS` here, at the
   boundary, rather than trusting every call site to do it.
"""

from __future__ import annotations

import threading
from typing import Iterator, List, Sequence, Tuple

import voyageai

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Request limits ────────────────────────────────────────────────────────────
# The Voyage API allows 1000 texts / 120,000 tokens per embedding request. We
# stay comfortably under both: token counting is an estimate when the tokenizer
# is unavailable (see _count_tokens), and a rejected batch costs a round trip.
MAX_TEXTS_PER_REQUEST = 100
MAX_TOKENS_PER_REQUEST = 100_000

# Used only when the Voyage tokenizer cannot be loaded. Deliberately pessimistic
# (real code tokenizes at roughly 3-4 chars/token) so the estimate over-counts
# and batches come out smaller rather than over the limit.
_CHARS_PER_TOKEN_ESTIMATE = 2.5

# Rerank query cap. ~1300 tokens, so even 100 candidates stay far below the
# 600k total-token cap and the 8k query-token cap.
MAX_QUERY_CHARS = 4000

_client: voyageai.Client | None = None
_client_lock = threading.Lock()


def get_client() -> voyageai.Client:
    """Return the process-wide Voyage client, creating it on first use."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                settings = get_settings()
                if not settings.VOYAGE_API_KEY:
                    raise RuntimeError(
                        "VOYAGE_API_KEY is not set — add it to backend/.env "
                        "(see .env.example)."
                    )
                # The SDK defaults to max_retries=0; without this a single
                # transient 429/503 aborts a whole repo ingestion.
                _client = voyageai.Client(api_key=settings.VOYAGE_API_KEY, max_retries=3)
                logger.info("voyage_client: Voyage client initialised.")
    return _client


# ── Batching ──────────────────────────────────────────────────────────────────

def _iter_batches(
    token_counts: Sequence[int],
    max_texts: int = MAX_TEXTS_PER_REQUEST,
    max_tokens: int = MAX_TOKENS_PER_REQUEST,
) -> Iterator[Tuple[int, int]]:
    """
    Pack items into request-sized batches, yielding ``(start, end)`` slices.

    Greedy on both axes: a batch is closed when adding the next item would
    exceed either ``max_texts`` or ``max_tokens``. A single item larger than
    ``max_tokens`` still gets its own batch — the API truncates it to the
    model's context window rather than failing (``truncation=True``).

    Pure function over token counts, so it is unit-testable without a network
    call or an API key. Slices are contiguous, in order, and cover every index
    exactly once.
    """
    start = 0
    batch_tokens = 0
    for i, count in enumerate(token_counts):
        if i > start and (i - start >= max_texts or batch_tokens + count > max_tokens):
            yield start, i
            start = i
            batch_tokens = 0
        batch_tokens += count
    if start < len(token_counts):
        yield start, len(token_counts)


def _count_tokens(texts: Sequence[str], model: str) -> List[int]:
    """
    Per-text token counts for batching.

    Uses Voyage's own tokenizer, which downloads from the HuggingFace Hub on
    first use. That is a network dependency at ingest time, so a failure falls
    back to a character-based estimate instead of aborting the run — the counts
    only need to be good enough to size batches.
    """
    try:
        encodings = get_client().tokenize(list(texts), model=model)
        return [len(enc.ids) for enc in encodings]
    except Exception as e:  # tokenizer download failed, offline, etc.
        logger.warning(
            "voyage_client: Voyage tokenizer unavailable (%s); "
            "falling back to a character-based token estimate.", e,
        )
        return [int(len(t) / _CHARS_PER_TOKEN_ESTIMATE) + 1 for t in texts]


# ── Embeddings ────────────────────────────────────────────────────────────────

def embed_documents(texts: Sequence[str]) -> List[List[float]]:
    """
    Embed corpus chunks for storage. Returns one vector per input, in order.

    Uses ``input_type="document"`` — see the module docstring on asymmetry.
    """
    if not texts:
        return []

    settings = get_settings()
    model = settings.VOYAGE_EMBED_MODEL
    client = get_client()

    token_counts = _count_tokens(texts, model)
    vectors: List[List[float]] = []
    total_tokens = 0

    for start, end in _iter_batches(token_counts):
        batch = list(texts[start:end])
        result = client.embed(
            batch,
            model=model,
            input_type="document",
            output_dimension=settings.VOYAGE_EMBED_DIMENSION,
            truncation=True,
        )
        vectors.extend(result.embeddings)
        total_tokens += result.total_tokens
        logger.debug(
            "voyage_client: Embedded chunks [%d:%d] (%d texts, %d tokens).",
            start, end, end - start, result.total_tokens,
        )

    logger.info(
        "voyage_client: Embedded %d chunks with %s (%d tokens).",
        len(vectors), model, total_tokens,
    )
    return vectors


def embed_query(text: str) -> List[float]:
    """
    Embed a single search query. Uses ``input_type="query"``.

    The query is truncated to :data:`MAX_QUERY_CHARS` so it matches exactly what
    :func:`rerank` scores against.
    """
    settings = get_settings()
    result = get_client().embed(
        [truncate_query(text)],
        model=settings.VOYAGE_EMBED_MODEL,
        input_type="query",
        output_dimension=settings.VOYAGE_EMBED_DIMENSION,
        truncation=True,
    )
    return result.embeddings[0]


# ── Reranking ─────────────────────────────────────────────────────────────────

def truncate_query(text: str) -> str:
    """Bound a query to :data:`MAX_QUERY_CHARS`, logging when it bites."""
    if len(text) <= MAX_QUERY_CHARS:
        return text
    logger.info(
        "voyage_client: Query truncated from %d to %d characters.",
        len(text), MAX_QUERY_CHARS,
    )
    return text[:MAX_QUERY_CHARS]


def rerank(query: str, documents: Sequence[str], top_k: int) -> List[Tuple[int, float]]:
    """
    Rerank ``documents`` against ``query``.

    Returns ``(original_index, relevance_score)`` pairs, best first, at most
    ``top_k`` of them. Indices refer to positions in the ``documents`` argument
    so the caller can carry its own metadata alongside.

    On any API failure this degrades to the input order (with ``0.0`` scores)
    rather than raising: a reranker outage should cost result quality, not the
    whole crew run.
    """
    if not documents:
        return []

    settings = get_settings()
    try:
        result = get_client().rerank(
            query=truncate_query(query),
            documents=list(documents),
            model=settings.VOYAGE_RERANK_MODEL,
            top_k=top_k,
            truncation=True,
        )
    except Exception as e:
        logger.error(
            "voyage_client: Reranking failed (%s); falling back to vector-search order.", e,
        )
        return [(i, 0.0) for i in range(min(top_k, len(documents)))]

    logger.info(
        "voyage_client: Reranked %d candidates to top %d with %s (%d tokens).",
        len(documents), len(result.results), settings.VOYAGE_RERANK_MODEL,
        result.total_tokens,
    )
    return [(r.index, r.relevance_score) for r in result.results]
