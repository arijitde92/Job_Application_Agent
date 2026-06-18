"""
app.services.crew_runner
------------------------
Async wrapper for CrewAI crew execution with progress tracking via SSE.
Runs the agent pipeline in a thread pool to avoid blocking the async event loop.
"""

import asyncio
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.services.gcs_service import download_file, upload_tailored_resume, upload_interview_materials
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

# Thread pool for running synchronous crew code
_executor = ThreadPoolExecutor(max_workers=4)

# In-memory progress store: {job_id: {"current_step": str, "error": str|None}}
progress_store: Dict[int, Dict[str, Any]] = {}

# ── Crew verbose log path ─────────────────────────────────────────────────────
# app/services/crew_runner.py → parents[2] resolves to the `backend/` directory.
_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"


def _update_progress(job_id: int, step: str, error: str = None):
    """Update the in-memory progress store for SSE broadcasting."""
    progress_store[job_id] = {"current_step": step, "error": error}
    logger.info("crew_runner: Job %d → step: %s", job_id, step)


def _run_crew_sync(
    job_id: int, user_id: int, user_email: str,
    github_username: str, github_url: str, bq_dataset_name: str,
    resume_content: str, job_url: str,
):
    """
    Synchronous crew execution — runs in a thread.
    This imports and uses the existing crew pipeline.
    """
    from dotenv import load_dotenv
    load_dotenv()

    # Step 1: Extract job details
    _update_progress(job_id, "extracting_job_info")
    try:
        from app.services.extractors.linkedin_extractor import extract_linkedin_job_details
        job_details = extract_linkedin_job_details(job_url)
        job_info = job_details.to_dict()
        job_info.pop("about_company", None)
        job_details_json = json.dumps(job_info, indent=2)
    except Exception as e:
        logger.error("crew_runner: Failed to extract job details for job %d: %s", job_id, e)
        raise RuntimeError(f"Failed to extract job details: {e}")


    # Step 2: Write resume to temp file for crew to read
    _update_progress(job_id, "searching_projects")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, prefix="resume_") as f:
        f.write(resume_content)
        resume_path = f.name

    try:
        # Step 3: Build crew and run
        _update_progress(job_id, "building_profile")

        from app.services.crew.crew import build_crew
        from app.services.crew.tasks import (
            resume_strategy_task, interview_preparation_task,
        )

        applicant_name = user_email.split("@")[0].replace(".", "_")

        # Build output paths for this job
        resume_output = tempfile.mktemp(suffix="_resume.md", prefix=f"job{job_id}_")
        interview_output = tempfile.mktemp(suffix="_interview.md", prefix=f"job{job_id}_")

        # Override output_file dynamically
        resume_strategy_task.output_file = resume_output
        interview_preparation_task.output_file = interview_output

        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        crew_log_path = str(_LOG_DIR / f"{ts}_crew")

        crew = build_crew(output_log_file=crew_log_path, verbose=True, tracing=False)
        logger.info("crew_runner: Crew verbose log → %s", crew_log_path)

        _update_progress(job_id, "tailoring_resume")

        job_application_inputs = {
            "applicant_name": applicant_name,
            "job_posting_url": job_url,
            "job_name": job_details.job_name,
            "company_name": job_details.company_name,
            "github_url": github_url,
            "resume_path": resume_path,
            "job_details_json": job_details_json,
            "bq_dataset_name": bq_dataset_name,
        }

        result = crew.kickoff(inputs=job_application_inputs)
        logger.info("crew_runner: Crew execution completed for job %d. Log saved to %s", job_id, crew_log_path)

        # Read output files
        tailored_resume_content = ""
        interview_content = ""

        if os.path.exists(resume_output):
            with open(resume_output, "r") as f:
                tailored_resume_content = f.read()
        elif hasattr(result, "raw"):
            tailored_resume_content = result.raw

        if os.path.exists(interview_output):
            with open(interview_output, "r") as f:
                interview_content = f.read()

        return {
            "tailored_resume": tailored_resume_content,
            "interview_materials": interview_content,
            "job_details": job_details,
        }

    finally:
        # Clean up temp files
        for path in [resume_path]:
            if os.path.exists(path):
                os.unlink(path)



async def run_crew_for_job(
    job_id: int, user_id: int, user_email: str,
    github_profile, resume_gcs_path: str, job_url: str,
):
    """
    Async entry point for crew execution. Updates job status in DB.
    Runs the synchronous crew pipeline in a thread pool executor.
    """
    _update_progress(job_id, "pending")

    try:
        # Download resume from GCS
        resume_bytes = download_file(resume_gcs_path)
        resume_content = resume_bytes.decode("utf-8")

        # Update status to processing
        async with AsyncSessionLocal() as db:
            from app.models import Job
            await db.execute(
                update(Job).where(Job.id == job_id).values(status="processing")
            )
            await db.commit()

        # Run crew in thread pool
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            _executor, _run_crew_sync,
            job_id, user_id, user_email,
            github_profile.github_username, github_profile.github_url,
            github_profile.bq_dataset_name, resume_content, job_url,
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
        logger.info("crew_runner: Job details saved for job %d", job_id)

        # Upload results to GCS
        _update_progress(job_id, "uploading_results")

        tailored_gcs = None
        interview_gcs = None

        if result["tailored_resume"]:
            tailored_gcs = upload_tailored_resume(
                user_id, job_id, result["tailored_resume"], "resume.md"
            )
        if result["interview_materials"]:
            interview_gcs = upload_interview_materials(
                user_id, job_id, result["interview_materials"], "interview.md"
            )

        # Update job record
        async with AsyncSessionLocal() as db:
            from app.models import Job
            await db.execute(
                update(Job).where(Job.id == job_id).values(
                    status="completed",
                    tailored_resume_gcs_path=tailored_gcs,
                    interview_materials_gcs_path=interview_gcs,
                    completed_at=datetime.utcnow(),
                )
            )
            await db.commit()

        _update_progress(job_id, "completed")
        logger.info("crew_runner: Job %d completed successfully", job_id)

    except Exception as e:
        logger.error("crew_runner: Job %d failed: %s", job_id, e, exc_info=True)
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
            logger.error("crew_runner: Failed to update job %d status: %s", job_id, db_err)
