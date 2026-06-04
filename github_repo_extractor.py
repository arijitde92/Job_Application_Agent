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
from logger import get_logger
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

# === New Section: LangChain GitHub Loader + BigQuery Vector Store ===
from langchain_community.document_loaders import GithubFileLoader
from langchain_google_community import BigQueryVectorStore
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
import os

# GCP details
PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "project-69718baa-b6cc-44ec-9fb")
DATASET_NAME = os.environ.get("GCP_DATASET_NAME", "job_applier_app")
LOCATION = os.environ.get("GCP_LOCATION", "asia-south2")
TABLE_NAME = os.environ.get("GCP_TABLE_NAME", "github_repo_data")

# Function to process a GitHub repo and store embeddings in BigQuery
def process_github_repo_to_bq(repo_url, branch="main", file_filter=None, access_token=None):
    """
    Loads files from a GitHub repo, creates embeddings, and stores them in BigQuery.
    """
    # Extract owner and repo first, and validate the URL
    try:
        owner, repo = parse_github_url(repo_url)
    except Exception as e:
        raise ValueError(f"Invalid GitHub repository URL '{repo_url}': {e}")

    # Check if data already exists in BigQuery
    from google.cloud import bigquery
    client = bigquery.Client(project=PROJECT_ID)
    
    # Query to check for existing data
    query = f"""
    SELECT COUNT(*) as count
    FROM `{PROJECT_ID}.{DATASET_NAME}.{TABLE_NAME}`
    WHERE repo_username = '{owner}'
    AND repo_name = '{repo}'
    """
    try:
        query_job = client.query(query)
        results = query_job.result()
        
        # Check if data exists
        for row in results:
            if row.count > 0:
                logger.info("github_repo_extractor.py: Data for %s/%s already exists in BigQuery. Skipping processing.", owner, repo)
                return
    except Exception as e:
        logger.error("github_repo_extractor.py: Error while querying BigQuery Table with query: %s. Error: %s", query, e)
    
    logger.info("github_repo_extractor.py: No existing data found for %s/%s. Proceeding with processing.", owner, repo)
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
        logger.warning("github_repo_extractor.py: No documents loaded from %s", repo_url)
        return

    # Add repo_username and repo_name as metadata to each document
    for doc in documents:
        if not hasattr(doc, 'metadata') or doc.metadata is None:
            doc.metadata = {}
        doc.metadata['repo_username'] = owner
        doc.metadata['repo_name'] = repo

    # 2. Split documents into chunks
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=50,
        separators=["\n\n", "\n", ".", "!", "?", ",", " ", ""],
    )
    doc_splits = text_splitter.split_documents(documents)
    logger.info("github_repo_extractor.py: Loaded and split %d document chunks from %s", len(doc_splits), repo_url)

    # 3. Create Vertex AI Embeddings using the new genai package
    embedding_model = GoogleGenerativeAIEmbeddings(
        model="text-embedding-005", project=PROJECT_ID, vertexai=True
    )

    # 4. Create BigQuery Vector Store
    bq_store = BigQueryVectorStore(
        project_id=PROJECT_ID,
        location=LOCATION,
        dataset_name=DATASET_NAME,
        table_name=TABLE_NAME,
        embedding=embedding_model,
    )

    # 5. Add documents to the vector store in batches to avoid Vertex AI limits
    # Max token limit per request is 20,000 tokens. With chunk_size=1000 characters,
    # 20 chunks is ~5,000-8,000 tokens, which safely stays under the limit.
    batch_size = 20
    doc_ids = []
    for i in range(0, len(doc_splits), batch_size):
        batch = doc_splits[i:i + batch_size]
        try:
            ids = bq_store.add_documents(batch)
            if ids:
                doc_ids.extend(ids)
            logger.info("github_repo_extractor.py: Added batch of %d documents.", len(batch))
        except Exception as e:
            logger.error("github_repo_extractor.py: Error adding batch to BigQuery: %s", e)
            
    logger.info("github_repo_extractor.py: Added %d documents to BigQuery vector store.", len(doc_ids))

def query_github_vector_store(query, top_k=5):
    """
    Query the BigQuery vector store for relevant document chunks using a natural language query.
    Returns a list of (content, metadata) tuples.
    """
    # 1. Create Vertex AI Embeddings (same as used for ingestion)
    embedding_model = GoogleGenerativeAIEmbeddings(
        model="text-embedding-005", project=PROJECT_ID, vertexai=True
    )

    # 2. Create BigQuery Vector Store
    bq_store = BigQueryVectorStore(
        project_id=PROJECT_ID,
        location=LOCATION,
        dataset_name=DATASET_NAME,
        table_name=TABLE_NAME,
        embedding=embedding_model,
    )

    # 3. Query the vector store
    results = bq_store.similarity_search(query, k=top_k)
    # Each result is a Document object with .page_content and .metadata
    return [(doc.page_content, doc.metadata) for doc in results]

# Example usage
if __name__ == "__main__":
    repo_url = "https://github.com/arijitde92/Online_Programming_Assignment_Portal"  # Replace with actual URL
    load_dotenv()
    # Optionally, set your GitHub access token for private repos or higher rate limits
    GITHUB_TOKEN = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
    process_github_repo_to_bq(
        repo_url,
        file_filter=lambda file_path: file_path.endswith(('.py', '.ipynb', '.md', '.txt')),
        access_token=GITHUB_TOKEN
    )
    # Example query
    query = "How do I run the chatbot locally?"
    results = query_github_vector_store(query)
    logger.info("github_repo_extractor.py: Top relevant chunks:")
    for i, (content, metadata) in enumerate(results, 1):
        logger.info("github_repo_extractor.py: Result %d:", i)
        logger.info("github_repo_extractor.py: Metadata: %s", metadata)
        logger.info("github_repo_extractor.py: Content: %s%s", content[:500], "..." if len(content) > 500 else "")
