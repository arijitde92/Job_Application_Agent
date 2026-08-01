"""
gcs.py
------
Google Cloud Storage helpers for uploading, downloading, and generating
signed URLs for resume and interview material files.

Bucket structure:
    gs://<bucket>/uploads/<user_id>/<filename>           — User-uploaded original resumes (.md/.pdf/.docx)
    gs://<bucket>/parsed/<user_id>/<name>_<user_id>_<resume_id>_parsed_resume.json — Agent-parsed resume JSON
    gs://<bucket>/tailored/<user_id>/<job_id>_resume.md  — Tailored resume, Markdown (always)
    gs://<bucket>/tailored/<user_id>/<job_id>_<applicant>_<company>_<job>_resume.docx — Tailored resume, Word (when the docx service succeeded)
    gs://<bucket>/tailored/<user_id>/<job_id>_<applicant>_<company>_<job>_resume.pdf  — Tailored resume, PDF (when docx→pdf conversion succeeded)
    gs://<bucket>/tailored/<user_id>/<job_id>_interview.md — Agent-generated interview materials
"""

import datetime
from pathlib import Path

from google.cloud import storage

from app.core.config import get_settings

settings = get_settings()

_RESUME_CONTENT_TYPES = {
    ".md": "text/markdown",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _get_client() -> storage.Client:
    """Return a GCS client."""
    return storage.Client(project=settings.GCP_PROJECT_ID)


def _get_bucket():
    """Return the configured GCS bucket."""
    client = _get_client()
    return client.bucket(settings.GCS_BUCKET_NAME)


# ── Upload functions ──────────────────────────────────────────────────────────

def upload_resume(user_id: int, file_bytes: bytes, filename: str) -> str:
    """
    Upload a user's original resume to GCS.

    Args:
        user_id: The user's database ID.
        file_bytes: Raw file content.
        filename: Original filename.

    Returns:
        The GCS path (gs://bucket/uploads/<user_id>/<filename>).

    Raises:
        ValueError: If file exceeds max size.
    """
    if len(file_bytes) > settings.max_resume_bytes:
        raise ValueError(
            f"File too large. Maximum allowed size is {settings.MAX_RESUME_SIZE_MB} MB."
        )

    blob_path = f"uploads/{user_id}/{filename}"
    content_type = _RESUME_CONTENT_TYPES.get(
        Path(filename).suffix.lower(), "application/octet-stream"
    )
    bucket = _get_bucket()
    blob = bucket.blob(blob_path)
    blob.upload_from_string(file_bytes, content_type=content_type)

    gcs_path = f"gs://{settings.GCS_BUCKET_NAME}/{blob_path}"
    return gcs_path


def upload_parsed_resume(user_id: int, filename: str, content: str) -> str:
    """
    Upload the resume_analyzer agent's parsed-resume JSON to GCS.

    The blob path is deterministic (parsed/<user_id>/<filename>), so a retried
    job simply overwrites the previous upload.

    Args:
        user_id: The user's database ID.
        filename: Parsed-resume filename
            (<user_name>_<user_id>_<resume_id>_parsed_resume.json).
        content: JSON content of the parsed resume.

    Returns:
        The GCS path (gs://bucket/parsed/<user_id>/<filename>).
    """
    blob_path = f"parsed/{user_id}/{filename}"
    bucket = _get_bucket()
    blob = bucket.blob(blob_path)
    blob.upload_from_string(content.encode("utf-8"), content_type="application/json")

    return f"gs://{settings.GCS_BUCKET_NAME}/{blob_path}"


def upload_tailored_resume(
    user_id: int, job_id: int, content: str | bytes, filename: str
) -> str:
    """
    Upload an agent-generated tailored resume artifact to GCS.

    Args:
        user_id: The user's database ID.
        job_id: The job record ID.
        content: The artifact — Markdown text, or the raw bytes of a
            generated .docx/.pdf.
        filename: Desired filename; its suffix picks the content type.

    Returns:
        The GCS path.
    """
    data = content.encode("utf-8") if isinstance(content, str) else content
    content_type = _RESUME_CONTENT_TYPES.get(
        Path(filename).suffix.lower(), "application/octet-stream"
    )
    blob_path = f"tailored/{user_id}/{job_id}_{filename}"
    bucket = _get_bucket()
    blob = bucket.blob(blob_path)
    blob.upload_from_string(data, content_type=content_type)

    return f"gs://{settings.GCS_BUCKET_NAME}/{blob_path}"


def upload_interview_materials(user_id: int, job_id: int, content: str, filename: str) -> str:
    """
    Upload agent-generated interview materials to GCS.

    Args:
        user_id: The user's database ID.
        job_id: The job record ID.
        content: Markdown content of the interview materials.
        filename: Desired filename.

    Returns:
        The GCS path.
    """
    blob_path = f"tailored/{user_id}/{job_id}_{filename}"
    bucket = _get_bucket()
    blob = bucket.blob(blob_path)
    blob.upload_from_string(content.encode("utf-8"), content_type="text/markdown")

    return f"gs://{settings.GCS_BUCKET_NAME}/{blob_path}"


# ── Download functions ────────────────────────────────────────────────────────

def download_file(gcs_path: str) -> bytes:
    """
    Download a file from GCS.

    Args:
        gcs_path: Full GCS path (gs://bucket/path/to/file).

    Returns:
        File content as bytes.
    """
    # Parse gs://bucket/path
    path = gcs_path.replace(f"gs://{settings.GCS_BUCKET_NAME}/", "")
    bucket = _get_bucket()
    blob = bucket.blob(path)
    return blob.download_as_bytes()


def delete_file(gcs_path: str) -> None:
    """
    Delete a file from GCS.

    Args:
        gcs_path: Full GCS path (gs://bucket/path/to/file).
    """
    path = gcs_path.replace(f"gs://{settings.GCS_BUCKET_NAME}/", "")
    bucket = _get_bucket()
    blob = bucket.blob(path)
    blob.delete()


# ── Signed URL ────────────────────────────────────────────────────────────────

def generate_signed_url(gcs_path: str, expiry_minutes: int = 15) -> str:
    """
    Generate a signed URL for temporary access to a GCS file.

    Args:
        gcs_path: Full GCS path.
        expiry_minutes: URL validity duration in minutes.

    Returns:
        A signed URL string.
    """
    path = gcs_path.replace(f"gs://{settings.GCS_BUCKET_NAME}/", "")
    bucket = _get_bucket()
    blob = bucket.blob(path)

    url = blob.generate_signed_url(
        version="v4",
        expiration=datetime.timedelta(minutes=expiry_minutes),
        method="GET",
    )
    return url
