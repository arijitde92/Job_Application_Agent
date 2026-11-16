"""
app.services.pipeline.tools.latex_tools
---------------------------------------
Per-run LangChain tools for the resume strategist's LaTeX → PDF step.

Two deterministic tools, called in order by the agent:

1. ``generate_resume_latex`` — the agent passes the tailored resume as
   structured arguments, validated by the :class:`TailoredResume` Pydantic
   schema (its ``args_schema``). The tool renders the ``.tex`` with the
   deterministic renderer, uploads it to GCS and records the path on
   ``jobs.tailored_resume_latex_gcs_path``.
2. ``compile_resume_pdf`` — takes no arguments; compiles the ``.tex`` from
   step 1 with Tectonic into the run's temporary work directory. The PDF is
   never uploaded: it is a temporary file only (the pipeline runner seeds the
   short-lived PDF cache with it and deletes the work directory).

Like the other identity-bound tools, the job identity, the work directory and
the storage locations are closured into the tools via :class:`ResumeLatexContext`
— the LLM never chooses paths. A shared ``ctx.state`` dict carries the outcome
to the pipeline runner.

Each tool allows ``_MAX_ATTEMPTS`` calls; the terminal error tells the agent to
fall back to the ``generate_resume_docx`` tool. The tools never raise: every
failure is an "ERROR: ..." string the agent can react to.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import ValidationError

from app.core.logging import get_logger
from app.services.latex.compiler import compile_latex_to_pdf
from app.services.latex.layout import plan_layout
from app.services.latex.renderer import render_resume_latex
from app.services.latex.schemas import TailoredResume
from app.services.pipeline.tools.docx_tools import build_resume_filename

logger = get_logger(__name__)

# Calls (successful or not) each tool accepts before refusing further calls.
_MAX_ATTEMPTS = 3
# Validation problems reported back to the agent per failed call.
_MAX_REPORTED_ERRORS = 8

_FALLBACK_HINT = (
    "Do NOT call generate_resume_latex or compile_resume_pdf again. Fall back "
    "to the generate_resume_docx tool if it is available; otherwise output the "
    "complete tailored resume in Markdown as your final answer."
)


@dataclass
class ResumeLatexContext:
    """Per-run identity, work directory and shared state of the LaTeX tools."""

    user_id: int
    job_id: int
    applicant_name: str
    company_name: str
    job_name: str
    # Existing temp directory owned by the pipeline runner: the .tex and the
    # compiled PDF are written here and removed with it after the run.
    workdir: str
    # True (standalone CLI / tests): never upload to GCS or touch the DB.
    local_only: bool = False
    state: Dict[str, Any] = field(default_factory=dict)

    @property
    def tex_filename(self) -> str:
        """{applicant}_{company}_{job}_resume.tex — names the GCS blob."""
        return build_resume_filename(self.applicant_name, self.company_name, self.job_name, "tex")


def format_validation_error(error: ValidationError) -> str:
    """Compact, agent-actionable summary of a schema validation failure."""
    problems = []
    for err in error.errors()[:_MAX_REPORTED_ERRORS]:
        location = ".".join(str(p) for p in err.get("loc", ())) or "resume"
        message = str(err.get("msg", "invalid value")).removeprefix("Value error, ")
        problems.append(f"{location}: {message}")
    extra = len(error.errors()) - len(problems)
    if extra > 0:
        problems.append(f"... and {extra} more problem(s)")
    return "; ".join(problems)


def _record_latex_path(ctx: ResumeLatexContext, gcs_path: str | None) -> None:
    """Set jobs.tailored_resume_latex_gcs_path (sync session — tools run in a thread)."""
    from sqlalchemy import update
    from sqlalchemy.orm import Session

    from app.core.database import get_sync_engine
    from app.models import Job

    with Session(get_sync_engine()) as session:
        session.execute(
            update(Job).where(Job.id == ctx.job_id).values(tailored_resume_latex_gcs_path=gcs_path)
        )
        session.commit()


def make_generate_resume_latex_tool(ctx: ResumeLatexContext) -> BaseTool:
    """
    Build the generate_resume_latex tool bound to one run's ``ctx``.

    Its arguments are the fields of :class:`TailoredResume`; LangChain
    validates them against the schema before the function runs, and a
    validation failure is returned to the agent as an ERROR observation.
    """

    def _consume_attempt() -> tuple[int, int] | None:
        """Count one call; None when the cap is already exhausted."""
        attempts = ctx.state.get("latex_attempts", 0)
        if attempts >= _MAX_ATTEMPTS:
            return None
        ctx.state["latex_attempts"] = attempts + 1
        return attempts + 1, _MAX_ATTEMPTS - attempts - 1

    def _cap_error() -> str:
        logger.warning("latex_tools: generate attempt cap reached for job %d", ctx.job_id)
        return f"ERROR: generate_resume_latex failed {_MAX_ATTEMPTS} times. {_FALLBACK_HINT}"

    def _on_validation_error(error: ValidationError) -> str:
        counted = _consume_attempt()
        if counted is None:
            return _cap_error()
        attempt, remaining = counted
        summary = format_validation_error(error)
        logger.warning(
            "latex_tools: invalid resume content for job %d (attempt %d/%d): %s",
            ctx.job_id, attempt, _MAX_ATTEMPTS, summary,
        )
        return (
            f"ERROR: The resume content failed validation — {summary}. Fix these "
            f"fields and call generate_resume_latex again. You have {remaining} "
            "attempt(s) left."
        )

    def generate_resume_latex(**fields: Any) -> str:
        counted = _consume_attempt()
        if counted is None:
            return _cap_error()
        attempt, remaining = counted

        # LangChain already validated the arguments; validating again turns
        # the plain dicts back into the typed model (normalisers are idempotent).
        try:
            resume = TailoredResume.model_validate(fields)
        except ValidationError as e:
            return (
                f"ERROR: The resume content failed validation — {format_validation_error(e)}. "
                f"Fix these fields and call generate_resume_latex again. You have "
                f"{remaining} attempt(s) left."
            )

        # 1. Render deterministically and write into the run's work directory.
        #    The layout (section order, internships shown or not) is decided
        #    from the content — never by the LLM.
        layout = plan_layout(resume)
        tex_source = render_resume_latex(resume, layout=layout)
        tex_path = os.path.join(ctx.workdir, "resume.tex")
        try:
            with open(tex_path, "w", encoding="utf-8") as f:
                f.write(tex_source)
        except OSError as e:
            logger.error("latex_tools: writing %s failed for job %d: %s", tex_path, ctx.job_id, e)
            return (
                f"ERROR: The LaTeX file could not be saved: {e}. Call "
                f"generate_resume_latex again. You have {remaining} attempt(s) left."
            )

        # A new .tex invalidates any earlier compilation.
        ctx.state.pop("pdf_path", None)
        ctx.state.update({"tex_path": tex_path, "tex_source": tex_source})

        # 2. Upload to GCS and record the path on the job — durable artifact.
        if not ctx.local_only:
            try:
                from app.services.gcs_service import upload_tailored_resume

                gcs_path = upload_tailored_resume(ctx.user_id, ctx.job_id, tex_source, ctx.tex_filename)
            except Exception as e:  # noqa: BLE001 — surface any GCS failure to the agent
                logger.error(
                    "latex_tools: GCS upload of the .tex failed for job %d: %s",
                    ctx.job_id, e, exc_info=True,
                )
                return (
                    f"ERROR: The LaTeX file was generated but the upload to cloud "
                    f"storage failed: {e}. Call generate_resume_latex again with the "
                    f"same content. You have {remaining} attempt(s) left."
                )
            ctx.state["gcs_path"] = gcs_path
            try:
                _record_latex_path(ctx, gcs_path)
            except Exception as e:  # noqa: BLE001 — surface any DB failure to the agent
                logger.error(
                    "latex_tools: DB update of tailored_resume_latex_gcs_path failed "
                    "for job %d: %s", ctx.job_id, e, exc_info=True,
                )
                return (
                    f"ERROR: The LaTeX file was uploaded but recording it in the "
                    f"database failed: {e}. Call generate_resume_latex again with "
                    f"the same content. You have {remaining} attempt(s) left."
                )
            logger.info("latex_tools: .tex for job %d stored at %s", ctx.job_id, gcs_path)

        ctx.state["latex_ok"] = True
        ctx.state["layout"] = layout.describe()
        logger.info(
            "latex_tools: LaTeX resume generated for job %d (attempt %d, %d chars, %s)",
            ctx.job_id, attempt, len(tex_source), layout.describe(),
        )
        return (
            f"SUCCESS: The LaTeX resume was generated and saved using the "
            f"{layout.describe()}. Now call compile_resume_pdf (no arguments) to "
            f"compile it into the PDF."
        )

    return StructuredTool.from_function(
        func=generate_resume_latex,
        name="generate_resume_latex",
        description=(
            "Render the tailored resume as a LaTeX document (the source of the final "
            "PDF resume) and save it. Call this AFTER you have finished tailoring "
            "the content, passing the complete tailored resume as structured "
            "arguments: personal_information (copied exactly from the parsed "
            "resume), summary, experience, education, skills (a LIST of "
            "{category, skills}), projects, publications, certifications — omit "
            "sections the applicant has no content for. Dates are short uppercase "
            'month-year like "SEP 2025", and "PRESENT" for a current position. '
            "**bold** markers are allowed in bullets and the summary; write all "
            "other text plainly (no LaTeX commands — escaping is automatic). "
            'Returns "SUCCESS: ..." (then call compile_resume_pdf) or '
            '"ERROR: <what to fix>".'
        ),
        args_schema=TailoredResume,
        handle_validation_error=_on_validation_error,
    )


def make_compile_resume_pdf_tool(ctx: ResumeLatexContext) -> BaseTool:
    """Build the compile_resume_pdf tool bound to one run's ``ctx``."""

    def compile_resume_pdf() -> str:
        """
        Compile the LaTeX resume saved by generate_resume_latex into the final
        PDF. Takes no arguments. Call it once after generate_resume_latex
        returned SUCCESS (and again after every successful regeneration).

        Returns:
            str: "SUCCESS: ..." when the PDF was compiled, otherwise
                 "ERROR: <what went wrong and how to proceed>".
        """
        if not ctx.state.get("latex_ok") or not ctx.state.get("tex_source"):
            return (
                "ERROR: There is no LaTeX resume to compile yet. Call "
                "generate_resume_latex with the tailored resume content first."
            )
        if ctx.state.get("pdf_path"):
            return (
                "SUCCESS: The PDF resume has already been compiled. Do NOT call "
                "compile_resume_pdf again."
            )

        attempts = ctx.state.get("compile_attempts", 0)
        if attempts >= _MAX_ATTEMPTS:
            return f"ERROR: compile_resume_pdf failed {_MAX_ATTEMPTS} times. {_FALLBACK_HINT}"
        ctx.state["compile_attempts"] = attempts + 1
        remaining = _MAX_ATTEMPTS - attempts - 1

        result = compile_latex_to_pdf(ctx.state["tex_source"], ctx.workdir)
        if not result.ok:
            logger.warning(
                "latex_tools: PDF compilation failed for job %d (attempt %d/%d)",
                ctx.job_id, attempts + 1, _MAX_ATTEMPTS,
            )
            if remaining == 0:
                return (
                    f"ERROR: The PDF could not be compiled: {result.error}\n"
                    f"{_FALLBACK_HINT}"
                )
            return (
                f"ERROR: The PDF could not be compiled. Compiler output:\n{result.error}\n"
                "The template is fixed, so the cause is usually unusual characters "
                "or symbols in the content. Remove or simplify the offending text, "
                "call generate_resume_latex again with the corrected content, then "
                f"call compile_resume_pdf. You have {remaining} compile attempt(s) left."
            )

        ctx.state["pdf_path"] = result.pdf_path
        logger.info("latex_tools: PDF resume compiled for job %d → %s", ctx.job_id, result.pdf_path)
        return (
            "SUCCESS: The PDF resume was compiled. Do NOT call any document tool "
            "again (no generate_resume_docx). Now output the complete tailored "
            "resume in Markdown as your final answer."
        )

    return StructuredTool.from_function(func=compile_resume_pdf, name="compile_resume_pdf")
