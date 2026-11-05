"""
app.services.pipeline_runner
----------------------------
Async wrapper that runs the LangGraph job-application pipeline for one job,
with progress tracking for the SSE endpoint.

The pipeline is synchronous (its tools use blocking HTTP and DB clients), so
it runs in a thread pool to keep the event loop free. Progress comes from the
graph's own event stream (``stream_mode="updates"``), so each step reported to
the frontend reflects a node that actually finished.
"""

import asyncio
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from langsmith import traceable
from sqlalchemy import update

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger, log_token_usage
from app.services.gcs_service import download_file, upload_interview_materials, upload_tailored_resume

logger = get_logger(__name__)
settings = get_settings()

# Thread pool for running the synchronous pipeline.
_executor = ThreadPoolExecutor(max_workers=4)

# In-memory progress store: {job_id: {"current_step": str, "error": str|None}}
progress_store: Dict[int, Dict[str, Any]] = {}

# app/services/pipeline_runner.py → parents[2] resolves to the `backend/` directory.
_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"


def _update_progress(job_id: int, step: str, error: str = None):
    """Update the in-memory progress store for SSE broadcasting."""
    progress_store[job_id] = {"current_step": step, "error": error}
    logger.info("pipeline_runner: Job %d → step: %s", job_id, step)


def _trace_inputs(inputs: dict) -> dict:
    """Keep raw resume bytes out of the LangSmith root span's inputs."""
    redacted = dict(inputs)
    if isinstance(redacted.get("resume_bytes"), (bytes, bytearray)):
        redacted["resume_bytes"] = f"<{len(redacted['resume_bytes'])} bytes>"
    return redacted


def _stream_pipeline(job_id: int, graph, initial_state: dict, config, include_github: bool) -> dict:
    """
    Run the graph, advancing the frontend's progress steps as nodes finish.

    The step keys are the ones the frontend's ProgressTracker already knows:
    ``building_profile`` once the resume analysis (and GitHub summary, when
    included) finished, ``tailoring_resume`` once the profile is built.

    Returns:
        The final graph state (the merged updates of every node).
    """
    from app.services.pipeline.graph import GITHUB_SUMMARY, PROFILE, RESUME_ANALYSIS

    before_profile = {RESUME_ANALYSIS, GITHUB_SUMMARY} if include_github else {RESUME_ANALYSIS}
    finished: set[str] = set()
    state = dict(initial_state)
    for update_chunk in graph.stream(initial_state, config, stream_mode="updates"):
        for node, delta in update_chunk.items():
            finished.add(node)
            if delta:
                state.update(delta)
            if node in before_profile and before_profile <= finished:
                _update_progress(job_id, "building_profile")
            elif node == PROFILE:
                _update_progress(job_id, "tailoring_resume")
    return state


@traceable(name="tailor_resume_job", process_inputs=_trace_inputs)
def _run_pipeline_sync(
    job_id: int, user_id: int, user_email: str,
    github_username: str = None, github_url: str = None,
    resume_bytes: bytes = None, resume_filename: str = None, job_url: str = None,
    resume_id: int = None, user_name: str = None, job_description_text: str = None,
):
    """
    Synchronous pipeline execution — runs in a thread.

    The raw resume file bytes (.pdf/.docx/.md) are parsed to text BEFORE the
    graph is built; a ResumeParsingError aborts the run so no agent starts
    and the job is marked failed (the UI offers a retry).

    Job details come from scraping ``job_url`` (LinkedIn) or, when the user
    pasted/uploaded the description instead, from ``job_description_text``.

    When no GitHub URL is supplied the graph is built without the GitHub
    summarizer node and tailors the resume from the resume + job details alone.

    With ``LANGSMITH_TRACING=true`` this function is the root span of the
    job's trace; the graph run nests under it.
    """
    from dotenv import load_dotenv
    load_dotenv()

    include_github = bool(github_url)

    from app.services.pipeline.tracing import annotate_current_trace
    annotate_current_trace(
        metadata={"job_id": job_id, "user_id": user_id, "resume_id": resume_id},
        tags=["web", "with-github" if include_github else "no-github"],
    )

    # Step 1: Extract job details
    _update_progress(job_id, "extracting_job_info")
    try:
        if job_description_text:
            from app.services.extractors.text_job_extractor import extract_job_details_from_text
            job_details = extract_job_details_from_text(job_description_text)
        else:
            from app.services.extractors.linkedin_extractor import extract_linkedin_job_details
            job_details = extract_linkedin_job_details(job_url)
        job_info = job_details.to_dict()
        job_info.pop("about_company", None)
        job_details_json = json.dumps(job_info, indent=2)
    except Exception as e:
        logger.error("pipeline_runner: Failed to extract job details for job %d: %s", job_id, e)
        raise RuntimeError(f"Failed to extract job details: {e}")

    # Step 2: Parse the resume document (.pdf/.docx/.md) into text.
    # Failure here must stop everything — no agents, no LLM tokens.
    _update_progress(job_id, "parsing_resume")
    from app.services.resume_parser import ResumeParsingError, extract_resume_text

    suffix = Path(resume_filename).suffix.lower() if resume_filename else ".md"
    with tempfile.NamedTemporaryFile(mode="wb", suffix=suffix, delete=False, prefix="resume_raw_") as f:
        f.write(resume_bytes)
        raw_resume_path = f.name

    try:
        resume_text = extract_resume_text(raw_resume_path)
    except ResumeParsingError as e:
        logger.error(
            "pipeline_runner: Resume parsing failed for job %d (resume %s, file '%s'): %s",
            job_id, resume_id, resume_filename, e, exc_info=True,
        )
        if os.path.exists(raw_resume_path):
            os.unlink(raw_resume_path)
        raise RuntimeError(f"Resume parsing failed: {e}")

    # Step 3: Write extracted text to a temp .md for the agents' read_resume
    # tool. The "searching_projects" step only applies when GitHub repos are
    # indexed; without GitHub we advance straight to building the profile.
    _update_progress(job_id, "searching_projects" if include_github else "building_profile")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, prefix="resume_", encoding="utf-8") as f:
        f.write(resume_text)
        resume_path = f.name

    # Initialized before the try so the cleanup in `finally` can reference
    # them even when the graph build fails before they are assigned.
    docx_output = None
    pdf_output = None
    run_log = None

    try:
        # Step 4: Build the graph and run it
        from langchain_core.callbacks import UsageMetadataCallbackHandler

        from app.services.docx_to_pdf import convert_docx_to_pdf
        from app.services.pipeline.graph import NODE_NAMES, PipelineInputs, build_pipeline
        from app.services.pipeline.tools import (
            GithubIndexContext,
            ResumeAnalysisContext,
            ResumeDocxContext,
            build_docx_filename,
            github_username_from_url,
        )
        from app.services.pipeline.tracing import PipelineFileLogger, build_run_config

        applicant_name = user_email.split("@")[0].replace(".", "_")
        docx_output = tempfile.mktemp(suffix="_resume.docx", prefix=f"job{job_id}_")

        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_log_path = str(_LOG_DIR / f"{ts}_job{job_id}_pipeline.log")

        # Identity context for the resume_analyzer's save tool: the parsed
        # JSON is uploaded to GCS and recorded on resumes.parsed_resume_path.
        # resume_text is the faithfulness judge's reference.
        resume_ctx = ResumeAnalysisContext(
            user_id=user_id,
            resume_id=resume_id,
            user_name=user_name or applicant_name,
            local_only=False,
            resume_text=resume_text,
        )

        # GitHub identity for the indexing / search tools. Scoping the vector
        # search to this account is what keeps one applicant's run from
        # retrieving another applicant's repository content.
        github_ctx = None
        if include_github:
            github_ctx = GithubIndexContext(
                github_url=github_url,
                github_username=github_username or github_username_from_url(github_url),
            )

        # Identity + output path for the resume strategist's .docx tool. The
        # formatted filename ({applicant}_{company}_{job}_resume.docx) names
        # the GCS blob and the browser download; the temp path is local only.
        docx_filename = build_docx_filename(
            applicant_name, job_details.company_name, job_details.job_name
        )
        docx_ctx = ResumeDocxContext(
            applicant_name=applicant_name,
            company_name=job_details.company_name,
            job_name=job_details.job_name,
            job_id=job_id,
            output_path=docx_output,
            display_name=user_name or applicant_name,
        )

        graph = build_pipeline(
            include_github=include_github, resume_path=resume_path,
            resume_ctx=resume_ctx, github_ctx=github_ctx, docx_ctx=docx_ctx,
        )

        inputs = PipelineInputs(
            applicant_name=applicant_name,
            job_posting_url=job_url or "N/A (job description provided directly)",
            job_name=job_details.job_name,
            company_name=job_details.company_name,
            job_details_json=job_details_json,
        )
        if include_github:
            inputs["github_url"] = github_url

        usage = UsageMetadataCallbackHandler()
        run_log = PipelineFileLogger(run_log_path, node_names=NODE_NAMES)
        config = build_run_config(
            include_github=include_github, job_id=job_id, user_id=user_id,
            resume_id=resume_id, callbacks=[usage, run_log], extra_tags=["web"],
        )
        logger.info("pipeline_runner: Pipeline run log → %s", run_log_path)

        state = _stream_pipeline(job_id, graph, {"inputs": inputs}, config, include_github)
        logger.info("pipeline_runner: Pipeline completed for job %d. Log saved to %s", job_id, run_log_path)
        log_token_usage(usage.usage_metadata, logger)
        if resume_ctx.state.get("gcs_path"):
            logger.info(
                "pipeline_runner: Parsed resume for job %d stored at %s",
                job_id, resume_ctx.state["gcs_path"],
            )

        tailored_resume_content = state.get("tailored_resume_md", "")
        interview_content = state.get("interview_materials_md", "")

        # The strategist's generate_resume_docx tool writes the .docx to
        # docx_ctx.output_path and records it in ctx.state on success. The
        # bytes (not the temp paths) go into the result so this thread can
        # clean up its temps regardless of what the async side does.
        tailored_resume_docx = None
        tailored_resume_pdf = None
        if docx_ctx.state.get("docx_path") and os.path.exists(docx_output):
            with open(docx_output, "rb") as f:
                tailored_resume_docx = f.read()
            logger.info(
                "pipeline_runner: .docx resume generated for job %d (%d bytes, %d "
                "tool attempt(s))",
                job_id, len(tailored_resume_docx), docx_ctx.state.get("attempts", 0),
            )
            pdf_output = convert_docx_to_pdf(docx_output)
            if pdf_output:
                with open(pdf_output, "rb") as f:
                    tailored_resume_pdf = f.read()
                logger.info("pipeline_runner: .docx converted to PDF for job %d", job_id)
            else:
                logger.warning(
                    "pipeline_runner: PDF conversion failed for job %d — the .docx "
                    "is kept, the in-browser preview will fall back to Markdown",
                    job_id,
                )
        else:
            logger.info(
                "pipeline_runner: no .docx generated for job %d (%d tool attempt(s)) "
                "— falling back to the Markdown resume",
                job_id, docx_ctx.state.get("attempts", 0),
            )

        return {
            "tailored_resume": tailored_resume_content,
            "tailored_resume_docx": tailored_resume_docx,
            "tailored_resume_pdf": tailored_resume_pdf,
            "docx_filename": docx_filename,
            "interview_materials": interview_content,
            "job_details": job_details,
        }

    finally:
        if run_log is not None:
            run_log.close()
        # Clean up temp files
        for path in (resume_path, raw_resume_path, docx_output, pdf_output):
            if path and os.path.exists(path):
                os.unlink(path)


async def run_pipeline_for_job(
    job_id: int, user_id: int, user_email: str,
    github_profile, resume_gcs_path: str, job_url: str = None,
    resume_id: int = None, user_name: str = None, resume_filename: str = None,
    job_description_text: str = None,
):
    """
    Async entry point for one job's pipeline run. Updates job status in DB.
    Runs the synchronous pipeline in a thread pool executor.

    Exactly one of ``job_url`` (LinkedIn) / ``job_description_text`` (pasted or
    uploaded by the user) identifies the job.

    ``github_profile`` may be ``None`` when the user tailors a resume without a
    GitHub profile; the pipeline then runs without the GitHub summarizer.

    ``resume_id`` / ``user_name`` / ``resume_filename`` identify the resume for
    the resume_analyzer agent (parsed-resume filename, GCS blob, and the
    resumes.parsed_resume_path DB update). The raw bytes are kept binary —
    PDF/DOCX resumes must not be utf-8 decoded.
    """
    _update_progress(job_id, "pending")

    try:
        # Download resume from GCS (raw bytes — may be a binary .pdf/.docx)
        resume_bytes = download_file(resume_gcs_path)

        # Update status to processing
        async with AsyncSessionLocal() as db:
            from app.models import Job
            await db.execute(
                update(Job).where(Job.id == job_id).values(status="processing")
            )
            await db.commit()

        # github_profile may be None when the user tailors without GitHub.
        # Note: github_profile.bq_dataset_name is no longer read — the vector
        # store moved from per-user BigQuery datasets to a single Weaviate
        # collection scoped by github_username. The column is left in place.
        gh_username = github_profile.github_username if github_profile else None
        gh_url = github_profile.github_url if github_profile else None

        # Run the pipeline in the thread pool
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            _executor, _run_pipeline_sync,
            job_id, user_id, user_email,
            gh_username, gh_url,
            resume_bytes, resume_filename, job_url,
            resume_id, user_name, job_description_text,
        )

        # Save extracted job details to DB on the correct async event loop
        job_details = result["job_details"]
        async with AsyncSessionLocal() as db:
            from app.models import Job
            await db.execute(
                update(Job).where(Job.id == job_id).values(
                    job_name=job_details.job_name,
                    company_name=job_details.company_name,
                    location=job_details.location,
                    seniority_level=job_details.seniority_level,
                    employment_type=job_details.employment_type,
                    job_function=job_details.job_function,
                    industry=job_details.industry,
                    job_description=job_details.job_description,
                    requirements=job_details.requirements,
                )
            )
            await db.commit()
        logger.info("pipeline_runner: Job details saved for job %d", job_id)

        # Upload results to GCS
        _update_progress(job_id, "uploading_results")

        md_gcs = None
        docx_gcs = None
        pdf_gcs = None
        interview_gcs = None

        # The Markdown resume is always uploaded — it is the preview fallback
        # and the download target when no .docx was generated.
        if result["tailored_resume"]:
            md_gcs = upload_tailored_resume(
                user_id, job_id, result["tailored_resume"], "resume.md"
            )
            logger.info(
                "pipeline_runner: Tailored resume (md) for job %d stored at %s",
                job_id, md_gcs,
            )
        if result.get("tailored_resume_docx"):
            docx_gcs = upload_tailored_resume(
                user_id, job_id, result["tailored_resume_docx"], result["docx_filename"]
            )
            logger.info(
                "pipeline_runner: Tailored resume (docx) for job %d stored at %s",
                job_id, docx_gcs,
            )
        if result.get("tailored_resume_pdf"):
            pdf_filename = result["docx_filename"].removesuffix(".docx") + ".pdf"
            pdf_gcs = upload_tailored_resume(
                user_id, job_id, result["tailored_resume_pdf"], pdf_filename
            )
            logger.info(
                "pipeline_runner: Tailored resume (pdf) for job %d stored at %s",
                job_id, pdf_gcs,
            )
        if result["interview_materials"]:
            interview_gcs = upload_interview_materials(
                user_id, job_id, result["interview_materials"], "interview.md"
            )

        # The download target: the .docx replaces the .md when it exists.
        tailored_gcs = docx_gcs or md_gcs

        # Update job record. All three tailored-resume columns are set every
        # run (None when absent) so a retried job never keeps a stale
        # artifact path from an earlier run.
        async with AsyncSessionLocal() as db:
            from app.models import Job
            await db.execute(
                update(Job).where(Job.id == job_id).values(
                    status="completed",
                    tailored_resume_gcs_path=tailored_gcs,
                    tailored_resume_md_gcs_path=md_gcs,
                    tailored_resume_pdf_gcs_path=pdf_gcs,
                    interview_materials_gcs_path=interview_gcs,
                    completed_at=datetime.utcnow(),
                )
            )
            await db.commit()

        _update_progress(job_id, "completed")
        logger.info("pipeline_runner: Job %d completed successfully", job_id)

    except Exception as e:
        logger.error("pipeline_runner: Job %d failed: %s", job_id, e, exc_info=True)
        _update_progress(job_id, "failed", error=str(e))

        # Update job status to failed
        try:
            async with AsyncSessionLocal() as db:
                from app.models import Job
                await db.execute(
                    update(Job).where(Job.id == job_id).values(
                        status="failed", error_message=str(e)[:2000],
                    )
                )
                await db.commit()
        except Exception as db_err:
            logger.error("pipeline_runner: Failed to update job %d status: %s", job_id, db_err)
