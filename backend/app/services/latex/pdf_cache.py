"""
app.services.latex.pdf_cache
----------------------------
Short-lived, server-local store of compiled resume PDFs.

Compiled PDFs are never uploaded to GCS — the LaTeX source in GCS is the
durable artifact and the PDF is regenerated from it on demand. This cache only
avoids recompiling when a user views and then downloads the same resume, or
views it right after the pipeline compiled it.

* Files live in ``<system temp>/resume_pdf_cache/`` and expire after
  ``RESUME_PDF_CACHE_TTL_MINUTES``; expired files are purged on every access,
  and the whole directory is purged on application startup.
* The key includes a hash of the LaTeX source, so a changed ``.tex`` can never
  be served a stale PDF.
* Writes are atomic (temp file + ``os.replace``), so concurrent readers never
  see a half-written PDF. A cache miss on another server instance simply
  recompiles.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def cache_dir() -> Path:
    """The cache directory (created on demand)."""
    path = Path(tempfile.gettempdir()) / "resume_pdf_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ttl_seconds() -> float:
    return get_settings().RESUME_PDF_CACHE_TTL_MINUTES * 60


def _key_path(job_id: int, tex_source: str) -> Path:
    digest = hashlib.sha256(tex_source.encode("utf-8")).hexdigest()[:16]
    return cache_dir() / f"job{job_id}_{digest}.pdf"


def purge_expired() -> None:
    """Delete cached PDFs older than the TTL."""
    cutoff = time.time() - _ttl_seconds()
    for path in cache_dir().glob("*.pdf"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass  # concurrently removed / still being written — next purge


def purge_all() -> None:
    """Delete the whole cache (application startup)."""
    shutil.rmtree(cache_dir(), ignore_errors=True)


def get(job_id: int, tex_source: str) -> bytes | None:
    """The cached PDF for this exact LaTeX source, or None."""
    purge_expired()
    path = _key_path(job_id, tex_source)
    try:
        return path.read_bytes()
    except OSError:
        return None


def put(job_id: int, tex_source: str, pdf: bytes | str) -> None:
    """
    Cache a compiled PDF for ``job_id`` + ``tex_source``.

    Args:
        pdf: The PDF bytes, or the path of a compiled PDF to copy.
    """
    data = pdf if isinstance(pdf, bytes) else Path(pdf).read_bytes()
    evict(job_id)  # one live PDF per job
    target = _key_path(job_id, tex_source)
    fd, tmp = tempfile.mkstemp(dir=str(cache_dir()), suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, target)
    except OSError as e:
        logger.warning("pdf_cache: could not cache PDF for job %d: %s", job_id, e)
        if os.path.exists(tmp):
            os.unlink(tmp)


def evict(job_id: int) -> None:
    """Remove every cached PDF of ``job_id``."""
    for path in cache_dir().glob(f"job{job_id}_*.pdf"):
        try:
            path.unlink()
        except OSError:
            pass
