"""
app.services.latex.compiler
---------------------------
Compile LaTeX source to PDF with Tectonic (https://tectonic-typesetting.github.io).

Tectonic is a single self-contained binary (XeTeX engine) that fetches the
TeX packages it needs on first use and caches them; ``TECTONIC_ONLY_CACHED``
forbids network access once the cache is pre-warmed (e.g. in the Docker image).

The compiler never raises: every failure — missing binary, timeout, a LaTeX
error — comes back as ``CompileResult(ok=False, error=...)`` with an excerpt
of the log that is short enough to hand back to the agent. Success is judged
by a real PDF existing, never by the exit code alone.

Documents are compiled with ``--untrusted`` (no shell-escape, no other
insecure features) — the content is escaped by the renderer as well, this is
defence in depth.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_PDF_MAGIC = b"%PDF"
_TEX_FILENAME = "resume.tex"
_MAX_ERROR_CHARS = 1500


@dataclass(frozen=True)
class CompileResult:
    """Outcome of one compilation."""

    ok: bool
    pdf_path: str | None = None
    error: str = ""


def _resolve_binary() -> str | None:
    configured = get_settings().TECTONIC_PATH
    if os.path.isfile(configured):
        return configured
    return shutil.which(configured)


def _error_excerpt(output: str) -> str:
    """The lines of a Tectonic/TeX log that explain a failure, capped in size."""
    lines = output.splitlines()
    picked: list[str] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("!") or stripped.lower().startswith("error"):
            picked.append(stripped)
            # A TeX error ("! Undefined control sequence.") is followed by the
            # offending input line ("l.42 ..."), which is what identifies it.
            picked.extend(l.strip() for l in lines[i + 1:i + 3] if l.strip())
    text = "\n".join(dict.fromkeys(picked)) or output.strip()[-_MAX_ERROR_CHARS:]
    return text[:_MAX_ERROR_CHARS]


def compile_latex_to_pdf(tex_source: str, workdir: str, timeout: int | None = None) -> CompileResult:
    """
    Compile ``tex_source`` into ``<workdir>/resume.pdf``.

    Args:
        tex_source: The complete LaTeX document.
        workdir: An existing directory owned by the caller; the .tex, the PDF
            and Tectonic's intermediates are written here.
        timeout: Seconds before the compilation is killed (defaults to
            ``LATEX_COMPILE_TIMEOUT_SECONDS``).

    Returns:
        The :class:`CompileResult`; ``pdf_path`` is set only when ``ok``.
    """
    settings = get_settings()
    timeout = timeout or settings.LATEX_COMPILE_TIMEOUT_SECONDS

    binary = _resolve_binary()
    if binary is None:
        logger.error(
            "latex_compiler: Tectonic not found (TECTONIC_PATH=%r) — cannot compile",
            settings.TECTONIC_PATH,
        )
        return CompileResult(
            ok=False,
            error="The LaTeX compiler (Tectonic) is not installed on the server.",
        )

    work = Path(workdir)
    tex_path = work / _TEX_FILENAME
    pdf_path = work / (tex_path.stem + ".pdf")
    tex_path.write_text(tex_source, encoding="utf-8")
    if pdf_path.exists():
        pdf_path.unlink()

    cmd = [binary, "-X", "compile", "--untrusted", "--outdir", str(work)]
    if settings.TECTONIC_ONLY_CACHED:
        cmd.append("--only-cached")
    cmd.append(str(tex_path))

    try:
        result = subprocess.run(
            cmd, cwd=str(work), capture_output=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning("latex_compiler: compilation timed out after %ds", timeout)
        return CompileResult(ok=False, error=f"LaTeX compilation timed out after {timeout} seconds.")
    except OSError as e:
        logger.error("latex_compiler: could not run Tectonic: %s", e)
        return CompileResult(ok=False, error=f"Could not run the LaTeX compiler: {e}")

    output = (result.stdout or b"").decode(errors="replace") + "\n" + \
        (result.stderr or b"").decode(errors="replace")
    ok = pdf_path.exists() and pdf_path.read_bytes()[:4] == _PDF_MAGIC
    if result.returncode != 0 or not ok:
        excerpt = _error_excerpt(output)
        logger.warning(
            "latex_compiler: compilation failed (exit %d, pdf=%s): %s",
            result.returncode, ok, excerpt[:500],
        )
        return CompileResult(ok=False, error=excerpt or f"Tectonic exited with code {result.returncode}.")

    logger.info("latex_compiler: compiled %s (%d bytes)", pdf_path, pdf_path.stat().st_size)
    return CompileResult(ok=True, pdf_path=str(pdf_path))
