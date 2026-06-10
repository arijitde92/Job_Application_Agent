"""
job_router.py
-------------
Endpoints for starting crew execution, listing jobs, checking status/progress,
downloading tailored resumes and interview materials.
"""

import asyncio
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import User, Job, GithubProfile, Resume
from backend.schemas import TailorJobRequest, JobResponse, JobDetailResponse
from backend.auth import get_current_user
from backend.gcs import download_file
from backend.crew_runner import run_crew_for_job, progress_store

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.post("/tailor", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def tailor_resume(
    req: TailorJobRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Start a crew execution to tailor a resume for a job posting."""
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
    """Download the tailored resume .md file."""
    result = await db.execute(
        select(Job).where(Job.id == job_id, Job.user_id == current_user.id)
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if not job.tailored_resume_gcs_path:
        raise HTTPException(status_code=404, detail="Tailored resume not available yet.")
    content = download_file(job.tailored_resume_gcs_path)
    fname = f"{(job.company_name or 'tailored')}_{(job.job_name or 'resume')}_resume.md".replace(" ", "_")
    return Response(content=content, media_type="text/markdown",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


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
