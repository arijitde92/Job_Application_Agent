"""
app.services.docx_to_pdf
------------------------
Convert a .docx file to PDF with LibreOffice in headless mode.

LibreOffice is a system dependency (``apt-get install libreoffice-writer``);
when its binary is missing, or the conversion fails for any reason, the
converter logs and returns ``None`` instead of raising — the caller
(``app.services.crew_runner``) then simply ships the .docx without a PDF and
the frontend falls back to the Markdown preview.

Two LibreOffice quirks are handled here:

* Concurrent invocations fight over the shared user profile and fail with a
  lock error. The crew runner executes up to 4 jobs in parallel, so every
  call gets its own throwaway profile via ``-env:UserInstallation=...``.
* ``soffice`` can exit 0 without producing any output, so success is judged
  by the output PDF existing — never by the return code alone.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)


def convert_docx_to_pdf(
    docx_path: str, outdir: str | None = None, timeout: int = 120
) -> str | None:
    """
    Convert ``docx_path`` to a PDF next to it (or in ``outdir``).

    Returns:
        The generated PDF's path, or ``None`` when LibreOffice is not
        installed or the conversion failed.
    """
    binary = shutil.which("soffice") or shutil.which("libreoffice")
    if binary is None:
        logger.warning(
            "docx_to_pdf: LibreOffice not found (install libreoffice-writer) — "
            "skipping PDF conversion of '%s'", docx_path,
        )
        return None

    outdir = outdir or str(Path(docx_path).parent)
    expected_pdf = Path(outdir) / (Path(docx_path).stem + ".pdf")
    profile_dir = tempfile.mkdtemp(prefix="lo_profile_")
    try:
        result = subprocess.run(
            [
                binary,
                f"-env:UserInstallation=file://{profile_dir}",
                "--headless",
                "--convert-to", "pdf",
                "--outdir", outdir,
                docx_path,
            ],
            capture_output=True,
            timeout=timeout,
        )
        if not expected_pdf.exists():
            logger.warning(
                "docx_to_pdf: conversion of '%s' produced no PDF (exit %d): %s",
                docx_path, result.returncode,
                (result.stderr or result.stdout or b"").decode(errors="replace")[:500],
            )
            return None
        logger.info("docx_to_pdf: converted '%s' → '%s'", docx_path, expected_pdf)
        return str(expected_pdf)
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning("docx_to_pdf: conversion of '%s' failed: %s", docx_path, e)
        return None
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)
