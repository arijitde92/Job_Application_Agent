import requests
import base64
import re
import mimetypes
from urllib.parse import urlparse
from io import BytesIO
from pypdf import PdfReader
from docx import Document
import pandas as pd
import openpyxl
import os  # Added for directory and file operations
from dotenv import load_dotenv
from app.core.logging import get_logger
logger = get_logger(__name__)
# MIME types considered non-textual
BINARY_MIME_PREFIXES = [
    'image', 'audio', 'video', 'application/octet-stream', 'model'
]

# Helper: Extract repo owner and name from URL
def parse_github_url(repo_url):
    parsed = urlparse(repo_url)
    path_parts = parsed.path.strip("/").split("/")
    if len(path_parts) < 2:
        raise ValueError("Invalid GitHub repository URL")
    return path_parts[0], path_parts[1]

# Helper: Check if the file is a binary file
def is_binary_file(mime_type):
    if not mime_type:
        return False
    return any(mime_type.startswith(prefix) for prefix in BINARY_MIME_PREFIXES)

# Helper: Extract text from PDF
def extract_pdf_text(data):
    reader = PdfReader(BytesIO(data))
    text = ''
    for page in reader.pages:
        text += page.extract_text() or ''
    return text

# Helper: Extract text from DOCX
def extract_docx_text(data):
    doc = Document(BytesIO(data))
    return '\n'.join([para.text for para in doc.paragraphs])

# Helper: Extract text from Excel files
def extract_excel_text(data):
    excel = pd.read_excel(BytesIO(data), sheet_name=None)
    text = ''
    for sheet_name, df in excel.items():
        text += f'[{sheet_name}]\n{df.to_string(index=False)}\n\n'
    return text

# Helper: Get file content based on type
def get_file_content(file_info):
    mime_type, _ = mimetypes.guess_type(file_info['name'])

    if is_binary_file(mime_type):
        return None

    # Download raw content
    download_url = file_info['download_url']
    response = requests.get(download_url)
    if response.status_code != 200:
        return None
    data = response.content

    if file_info['name'].endswith('.pdf'):
        return extract_pdf_text(data)
    elif file_info['name'].endswith('.docx'):
        return extract_docx_text(data)
    elif file_info['name'].endswith(('.xlsx', '.xls')):
        return extract_excel_text(data)
    else:
        try:
            return data.decode('utf-8')
        except UnicodeDecodeError:
            return None

# Helper: Get file content based on type
def download_file(file_info, base_dir):
    """
    Download the file from GitHub and save it to the specified base_dir, preserving the path.
    """
    download_url = file_info['download_url']
    rel_path = file_info['path']
    save_path = os.path.join(base_dir, rel_path)
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    response = requests.get(download_url)
    if response.status_code == 200:
        with open(save_path, 'wb') as f:
            f.write(response.content)
        return True
    return False

# Recursive function to process repo contents and download files
def fetch_repo_files(owner, repo, path="", base_dir=None):
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    response = requests.get(url)
    if response.status_code != 200:
        return None

    contents = response.json()
    result = {}
    file_downloaded = False  # Track if any file is downloaded

    for item in contents:
        name = item['name']
        if name.startswith('.'):
            continue
        if item['type'] == 'dir':
            sub_result = fetch_repo_files(owner, repo, item['path'], base_dir=base_dir)
            if sub_result:
                file_downloaded = True
            # result.update(sub_result)  # Not needed for download logic
        elif item['type'] == 'file':
            if base_dir:
                if download_file(item, base_dir):
                    file_downloaded = True
            # Optionally, you can still extract text if needed:
            # content = get_file_content(item)
            # if content is not None:
            #     result[item['path']] = content
            result[item['path']] = f"Downloaded to {os.path.join(base_dir, item['path'])}" if base_dir else None

    return base_dir if file_downloaded else None

# Main function
def extract_github_repo_contents(repo_url, save_to_disk=True):
    owner, repo = parse_github_url(repo_url)
    base_dir = None
    if save_to_disk:
        base_dir = os.path.join('github_user_data', owner, repo)
        os.makedirs(base_dir, exist_ok=True)
    return fetch_repo_files(owner, repo, base_dir=base_dir)

# === RAG Section: LangChain GitHub Loader → Voyage embeddings → Weaviate ===
#
# LangChain is retained only for loading repo files and splitting them; the
# vector store and embeddings are the native Weaviate and Voyage AI SDKs
# (see app.services.vector_store). This replaces the previous
# BigQueryVectorStore + Vertex text-embedding-005 pipeline.
#
# Retrieval is two-stage: a vector search over-fetches candidates, then
# Voyage's rerank-2.5-lite reorders them by actual relevance to the query.
# Cosine distance alone puts chunks that merely share vocabulary with the job
# description ahead of chunks that answer it.
from typing import Any, Dict, List, Optional, Sequence, Tuple

from langchain_community.document_loaders import GithubFileLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.services.vector_store import voyage_client, weaviate_store

# Chunking. Kept at the pre-migration values so retrieval behaviour stays
# comparable to the BigQuery baseline; voyage-code-3 accepts up to 32k tokens
# per text, so there is headroom to enlarge these later.
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 50

# Candidates fetched from Weaviate before reranking. More candidates give the
# reranker more to work with, but rerank cost scales with the count.
RERANK_CANDIDATE_MULTIPLIER = 5
MIN_RERANK_CANDIDATES = 25
MAX_RERANK_CANDIDATES = 100


def process_github_repo_to_vector_store(
    repo_url: str,
    branch: str = "main",
    file_filter=None,
    access_token: Optional[str] = None,
) -> int:
    """
    Index one GitHub repository into the Weaviate vector store.

    Loads the repo's files, splits them into chunks, embeds the chunks with
    Voyage ``voyage-code-3``, and upserts them keyed by a deterministic UUID of
    ``owner/repo/file_path#chunk_index`` — so re-indexing overwrites rather
    than duplicating.

    Repos that already have chunks indexed are skipped.

    Args:
        repo_url: Full GitHub repository URL.
        branch: Branch to read files from.
        file_filter: Predicate on the file path; defaults to accepting all files.
        access_token: GitHub PAT, for private repos and higher rate limits.

    Returns:
        The number of chunks written (0 when skipped or when nothing loaded).
    """
    try:
        owner, repo = parse_github_url(repo_url)
    except Exception as e:
        raise ValueError(f"Invalid GitHub repository URL '{repo_url}': {e}")

    if weaviate_store.repo_exists(owner, repo):
        logger.info(
            "github_extractor: %s/%s is already indexed. Skipping processing.", owner, repo
        )
        return 0

    logger.info(
        "github_extractor: No existing data found for %s/%s. Proceeding with processing.",
        owner, repo,
    )

    # 1. Load files from GitHub
    loader = GithubFileLoader(
        repo=f"{owner}/{repo}",
        branch=branch,
        github_api_url="https://api.github.com",
        access_token=access_token,
        file_filter=file_filter if file_filter else (lambda file_path: True),
    )
    documents = loader.load()
    if not documents:
        logger.warning("github_extractor: No documents loaded from %s", repo_url)
        return 0

    # 2. Split into chunks
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ".", "!", "?", ",", " ", ""],
    )
    doc_splits = text_splitter.split_documents(documents)
    logger.info(
        "github_extractor: Loaded and split %d document chunks from %s",
        len(doc_splits), repo_url,
    )
    if not doc_splits:
        return 0

    # Number chunks per source file so the UUID is stable across re-runs.
    chunks: List[Dict[str, Any]] = []
    per_file_counter: Dict[str, int] = {}
    for split in doc_splits:
        metadata = split.metadata or {}
        file_path = metadata.get("path") or metadata.get("source") or "unknown"
        index = per_file_counter.get(file_path, 0)
        per_file_counter[file_path] = index + 1
        chunks.append(
            {"content": split.page_content, "file_path": file_path, "chunk_index": index}
        )

    # 3. Embed with Voyage
    vectors = voyage_client.embed_documents([c["content"] for c in chunks])

    # 4. Upsert into Weaviate
    written = weaviate_store.upsert_chunks(owner, repo, chunks, vectors)
    logger.info(
        "github_extractor: Indexed %d chunks for %s/%s into Weaviate.", written, owner, repo
    )
    return written


def query_github_vector_store(
    query: str,
    top_k: int = 5,
    repo_username: Optional[str] = None,
    repo_names: Optional[Sequence[str]] = None,
) -> List[Tuple[str, Dict[str, Any]]]:
    """
    Retrieve the most relevant repo chunks for a natural-language query.

    Vector search over-fetches candidates, then ``rerank-2.5-lite`` reorders
    them and the top ``top_k`` are returned as ``(content, metadata)`` tuples.
    Metadata carries ``repo_username`` / ``repo_name`` / ``file_path`` /
    ``chunk_index`` plus the ``distance`` and ``relevance_score``.

    Args:
        repo_username: Scope results to one GitHub account. **Pass this** —
            without it the search spans every indexed user's repositories.
        repo_names: Optionally narrow to specific repositories.
    """
    if repo_username is None:
        logger.warning(
            "github_extractor: query_github_vector_store called without "
            "repo_username — results span all indexed users."
        )

    fetch_k = min(
        max(top_k * RERANK_CANDIDATE_MULTIPLIER, MIN_RERANK_CANDIDATES),
        MAX_RERANK_CANDIDATES,
    )

    query_vector = voyage_client.embed_query(query)
    candidates = weaviate_store.search(
        query_vector, limit=fetch_k, repo_username=repo_username, repo_names=repo_names
    )
    if not candidates:
        logger.info("github_extractor: Vector search returned no candidates.")
        return []

    ranked = voyage_client.rerank(query, [c["content"] for c in candidates], top_k=top_k)

    results: List[Tuple[str, Dict[str, Any]]] = []
    for index, score in ranked:
        candidate = candidates[index]
        content = candidate.pop("content")
        candidate["relevance_score"] = score
        results.append((content, candidate))
    return results


# Example usage
if __name__ == "__main__":
    repo_url = "https://github.com/arijitde92/Online_Programming_Assignment_Portal"
    load_dotenv()
    # Optionally, set your GitHub access token for private repos or higher rate limits
    GITHUB_TOKEN = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
    owner, _ = parse_github_url(repo_url)
    process_github_repo_to_vector_store(
        repo_url,
        file_filter=lambda file_path: file_path.endswith(('.py', '.ipynb', '.md', '.txt')),
        access_token=GITHUB_TOKEN
    )
    logger.info(
        "github_extractor: %d chunks indexed for %s",
        weaviate_store.count_chunks(repo_username=owner), owner,
    )
    # Example query
    query = "How do I run the chatbot locally?"
    results = query_github_vector_store(query, repo_username=owner)
    logger.info("github_extractor: Top relevant chunks:")
    for i, (content, metadata) in enumerate(results, 1):
        logger.info("github_extractor: Result %d:", i)
        logger.info("github_extractor: Metadata: %s", metadata)
        logger.info("github_extractor: Content: %s%s", content[:500], "..." if len(content) > 500 else "")
    weaviate_store.close_client()
