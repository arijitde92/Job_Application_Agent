"""
app.services.latex.service
--------------------------
On-demand PDF for a job whose tailored resume is stored as LaTeX in GCS.

Used by the API when a user views or downloads a resume (including returning
users, long after the pipeline ran): the ``.tex`` is fetched from GCS and
compiled with the same :func:`compile_latex_to_pdf` function the agent's
``compile_resume_pdf`` tool calls. The PDF only ever lives in a temporary
directory and the short-lived :mod:`pdf_cache`.

Blocking (GCS download + subprocess) — call it from a worker thread.
"""

from __future__ import annotations

import shutil
import tempfile

from app.core.logging import get_logger
from app.services.latex import pdf_cache
from app.services.latex.compiler import compile_latex_to_pdf

logger = get_logger(__name__)


class LatexCompileError(RuntimeError):
    """The stored LaTeX source could not be compiled to a PDF."""


def get_resume_pdf_bytes(job_id: int, tex_gcs_path: str) -> bytes:
    """
    Return the compiled PDF of a job's stored LaTeX resume.

    Args:
        job_id: The job (cache key).
        tex_gcs_path: ``gs://`` path of the job's ``.tex`` file.

    Returns:
        The PDF bytes.

    Raises:
        LatexCompileError: The LaTeX could not be compiled.
        Exception: The GCS download failed.
    """
    from app.services.gcs_service import download_file

    tex_source = download_file(tex_gcs_path).decode("utf-8")

    cached = pdf_cache.get(job_id, tex_source)
    if cached is not None:
        logger.info("latex_service: serving cached PDF for job %d", job_id)
        return cached

    workdir = tempfile.mkdtemp(prefix=f"job{job_id}_pdf_")
    try:
        result = compile_latex_to_pdf(tex_source, workdir)
        if not result.ok:
            raise LatexCompileError(result.error)
        with open(result.pdf_path, "rb") as f:
            pdf = f.read()
        pdf_cache.put(job_id, tex_source, pdf)
        logger.info("latex_service: compiled PDF on demand for job %d (%d bytes)", job_id, len(pdf))
        return pdf
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
