"""
job_router.py
-------------
Endpoints for starting crew execution, listing jobs, checking status/progress,
downloading tailored resumes and interview materials.
"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.models import User, Job, GithubProfile, Resume
from app.schemas import TailorJobRequest, JobResponse, JobDetailResponse
from app.api.deps import get_current_user
from app.services.gcs_service import _RESUME_CONTENT_TYPES, download_file, delete_file
from app.services.crew_runner import run_crew_for_job, progress_store

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.post("/tailor", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def tailor_resume(
    req: TailorJobRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Start a crew execution to tailor a resume for a job posting.

    A GitHub profile is optional — when ``github_profile_id`` is omitted the
    resume is tailored from the job description + uploaded resume alone.
    """
    github_profile = None
    if req.github_profile_id is not None:
        gh_result = await db.execute(
            select(GithubProfile).where(
                GithubProfile.id == req.github_profile_id,
                GithubProfile.user_id == current_user.id,
            )
        )
        github_profile = gh_result.scalar_one_or_none()
        if not github_profile:
            raise HTTPException(status_code=404, detail="GitHub profile not found.")

    resume_result = await db.execute(
        select(Resume).where(Resume.id == req.resume_id, Resume.user_id == current_user.id)
    )
    resume = resume_result.scalar_one_or_none()
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found.")

    job = Job(
        user_id=current_user.id,
        github_profile_id=req.github_profile_id,
        resume_id=req.resume_id,
        linkedin_job_url=req.linkedin_job_url,
        status="pending",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    asyncio.create_task(
        run_crew_for_job(
            job_id=job.id, user_id=current_user.id, user_email=current_user.email,
            github_profile=github_profile, resume_gcs_path=resume.gcs_path,
            job_url=req.linkedin_job_url,
            resume_id=resume.id,
            user_name=f"{current_user.first_name} {current_user.last_name}",
            resume_filename=resume.original_filename,
        )
    )
    return job


@router.get("", response_model=list[JobResponse])
async def list_jobs(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all jobs for the authenticated user, newest first."""
    result = await db.execute(
        select(Job).where(Job.user_id == current_user.id).order_by(Job.created_at.desc())
    )
    return result.scalars().all()


@router.get("/{job_id}", response_model=JobDetailResponse)
async def get_job_detail(
    job_id: int, current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get detailed information about a specific job."""
    result = await db.execute(
        select(Job).where(Job.id == job_id, Job.user_id == current_user.id)
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(
    job_id: int, current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Delete a generated/tailored resume job. Removes the tailored resume and
    interview materials from GCS, then deletes the DB record.
    """
    result = await db.execute(
        select(Job).where(Job.id == job_id, Job.user_id == current_user.id)
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    # Best-effort cleanup of GCS artifacts; missing files should not block
    # deletion. A set — on md-fallback jobs tailored_resume_gcs_path and
    # tailored_resume_md_gcs_path are the same blob.
    for gcs_path in {
        job.tailored_resume_gcs_path,
        job.tailored_resume_md_gcs_path,
        job.tailored_resume_pdf_gcs_path,
        job.interview_materials_gcs_path,
    }:
        if gcs_path:
            try:
                delete_file(gcs_path)
            except Exception:
                pass

    await db.delete(job)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{job_id}/retry", response_model=JobResponse)
async def retry_job(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-run a failed or stuck job: reset to pending, clear error, relaunch crew."""
    result = await db.execute(
        select(Job).where(Job.id == job_id, Job.user_id == current_user.id)
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status == "completed":
        raise HTTPException(status_code=409, detail="Completed jobs cannot be retried.")
    if job.status == "processing":
        raise HTTPException(status_code=409, detail="Job is already running.")

    # Re-fetch GitHub profile (optional; FK is SET NULL so it may be None already).
    github_profile = None
    if job.github_profile_id is not None:
        gh_result = await db.execute(
            select(GithubProfile).where(
                GithubProfile.id == job.github_profile_id,
                GithubProfile.user_id == current_user.id,
            )
        )
        github_profile = gh_result.scalar_one_or_none()  # gone → run without GitHub

    # Resume must still exist.
    resume_result = await db.execute(
        select(Resume).where(Resume.id == job.resume_id, Resume.user_id == current_user.id)
    )
    resume = resume_result.scalar_one_or_none()
    if not resume:
        raise HTTPException(
            status_code=409,
            detail="The resume for this job no longer exists. Cannot retry.",
        )

    await db.execute(
        update(Job).where(Job.id == job_id).values(status="pending", error_message=None)
    )
    await db.commit()
    await db.refresh(job)

    # Pre-seed progress so a re-attaching SSE stream doesn't see the stale
    # terminal 'failed' step and close instantly.
    progress_store[job_id] = {"current_step": "pending", "error": None}

    asyncio.create_task(
        run_crew_for_job(
            job_id=job.id, user_id=current_user.id, user_email=current_user.email,
            github_profile=github_profile, resume_gcs_path=resume.gcs_path,
            job_url=job.linkedin_job_url,
            resume_id=resume.id,
            user_name=f"{current_user.first_name} {current_user.last_name}",
            resume_filename=resume.original_filename,
        )
    )
    return job


@router.get("/{job_id}/progress")
async def stream_job_progress(
    job_id: int, current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """SSE endpoint for real-time crew execution progress."""
    result = await db.execute(
        select(Job).where(Job.id == job_id, Job.user_id == current_user.id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Job not found.")

    async def event_stream():
        last_step = None
        while True:
            progress = progress_store.get(job_id, {})
            current_step = progress.get("current_step", "pending")
            error = progress.get("error")
            if current_step != last_step:
                last_step = current_step
                err_str = f'"{error}"' if error else "null"
                yield f'data: {{"step": "{current_step}", "error": {err_str}}}\n\n'
            if current_step in ("completed", "failed"):
                break
            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/{job_id}/resume/download")
async def download_tailored_resume(
    job_id: int, current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Download the tailored resume — the generated .docx when the document
    service succeeded, otherwise the Markdown file."""
    result = await db.execute(
        select(Job).where(Job.id == job_id, Job.user_id == current_user.id)
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if not job.tailored_resume_gcs_path:
        raise HTTPException(status_code=404, detail="Tailored resume not available yet.")
    content = download_file(job.tailored_resume_gcs_path)
    suffix = Path(job.tailored_resume_gcs_path).suffix.lower() or ".md"
    media_type = _RESUME_CONTENT_TYPES.get(suffix, "application/octet-stream")
    fname = (
        f"{(job.company_name or 'tailored')}_{(job.job_name or 'resume')}"
        f"_resume{suffix}"
    ).replace(" ", "_")
    return Response(content=content, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.get("/{job_id}/resume/pdf")
async def get_tailored_resume_pdf(
    job_id: int, current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Serve the PDF version of the tailored resume for in-browser viewing."""
    result = await db.execute(
        select(Job).where(Job.id == job_id, Job.user_id == current_user.id)
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if not job.tailored_resume_pdf_gcs_path:
        raise HTTPException(status_code=404, detail="PDF version not available.")
    content = download_file(job.tailored_resume_pdf_gcs_path)
    fname = Path(job.tailored_resume_pdf_gcs_path).name
    return Response(content=content, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{fname}"'})


@router.get("/{job_id}/resume/content")
async def get_tailored_resume_content(
    job_id: int, current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the tailored resume's Markdown content for in-browser viewing.

    Sourced from the always-uploaded Markdown artifact; for pre-docx-feature
    jobs it falls back to tailored_resume_gcs_path when that is a .md file.
    """
    result = await db.execute(
        select(Job).where(Job.id == job_id, Job.user_id == current_user.id)
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    md_path = job.tailored_resume_md_gcs_path
    if not md_path and job.tailored_resume_gcs_path and \
            job.tailored_resume_gcs_path.lower().endswith(".md"):
        md_path = job.tailored_resume_gcs_path
    if not md_path:
        raise HTTPException(status_code=404, detail="Tailored resume not available yet.")
    content = download_file(md_path)
    return {
        "content": content.decode("utf-8"), "job_name": job.job_name,
        "company_name": job.company_name,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }


@router.get("/{job_id}/interview/download")
async def download_interview_materials(
    job_id: int, current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Download the interview materials .md file."""
    result = await db.execute(
        select(Job).where(Job.id == job_id, Job.user_id == current_user.id)
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if not job.interview_materials_gcs_path:
        raise HTTPException(status_code=404, detail="Interview materials not available yet.")
    content = download_file(job.interview_materials_gcs_path)
    fname = f"{(job.company_name or 'interview')}_{(job.job_name or 'prep')}_interview.md".replace(" ", "_")
    return Response(content=content, media_type="text/markdown",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.get("/{job_id}/interview/content")
async def get_interview_content(
    job_id: int, current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get interview materials markdown content for in-browser viewing."""
    result = await db.execute(
        select(Job).where(Job.id == job_id, Job.user_id == current_user.id)
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if not job.interview_materials_gcs_path:
        raise HTTPException(status_code=404, detail="Interview materials not available yet.")
    content = download_file(job.interview_materials_gcs_path)
    return {
        "content": content.decode("utf-8"), "job_name": job.job_name,
        "company_name": job.company_name,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }
