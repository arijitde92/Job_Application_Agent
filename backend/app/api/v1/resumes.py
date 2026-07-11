"""
resume_router.py
----------------
Endpoints for uploading, listing, previewing, and deleting resumes.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.models import User, Resume
from app.schemas import ResumeResponse
from app.api.deps import get_current_user
from app.services.gcs_service import upload_resume, generate_signed_url, download_file, delete_file
from app.core.config import get_settings

settings = get_settings()
router = APIRouter(prefix="/api/resumes", tags=["resumes"])

ALLOWED_RESUME_EXTENSIONS = {".md", ".pdf", ".docx"}


@router.post("/upload", response_model=ResumeResponse, status_code=status.HTTP_201_CREATED)
async def upload_resume_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a resume file (.md, .pdf or .docx) to GCS."""
    # Validate file extension
    if not file.filename or Path(file.filename).suffix.lower() not in ALLOWED_RESUME_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only Markdown (.md), PDF (.pdf) or Word (.docx) files are accepted.",
        )

    # Read file content
    file_bytes = await file.read()

    # Validate file size
    if len(file_bytes) > settings.max_resume_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Maximum allowed size is {settings.MAX_RESUME_SIZE_MB} MB.",
        )

    # Upload to GCS
    try:
        gcs_path = upload_resume(current_user.id, file_bytes, file.filename)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload resume: {e}",
        )

    # Create DB record
    resume = Resume(
        user_id=current_user.id,
        original_filename=file.filename,
        gcs_path=gcs_path,
    )
    db.add(resume)
    await db.commit()
    await db.refresh(resume)
    return resume


@router.get("", response_model=list[ResumeResponse])
async def list_resumes(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all resumes uploaded by the authenticated user."""
    result = await db.execute(
        select(Resume).where(Resume.user_id == current_user.id).order_by(Resume.uploaded_at.desc())
    )
    return result.scalars().all()


@router.get("/{resume_id}/preview")
async def preview_resume(
    resume_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a signed URL for previewing a resume."""
    result = await db.execute(
        select(Resume).where(
            Resume.id == resume_id,
            Resume.user_id == current_user.id,
        )
    )
    resume = result.scalar_one_or_none()
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found.")

    try:
        signed_url = generate_signed_url(resume.gcs_path)
        return {"signed_url": signed_url}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate preview URL: {e}",
        )


@router.get("/{resume_id}/content")
async def get_resume_content(
    resume_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the raw markdown content of a resume for in-browser rendering."""
    result = await db.execute(
        select(Resume).where(
            Resume.id == resume_id,
            Resume.user_id == current_user.id,
        )
    )
    resume = result.scalar_one_or_none()
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found.")

    # Binary resumes (.pdf/.docx) cannot be rendered as markdown text —
    # the /preview signed-URL endpoint handles those.
    if Path(resume.original_filename or "").suffix.lower() != ".md":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="In-browser text preview is only available for Markdown resumes. "
                   "Use the preview endpoint for PDF/DOCX files.",
        )

    try:
        content_bytes = download_file(resume.gcs_path)
        return {"content": content_bytes.decode("utf-8"), "filename": resume.original_filename}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read resume: {e}")


@router.delete("/{resume_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_resume(
    resume_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Delete a resume. Fails if the resume has been used in a job application
    (ON DELETE RESTRICT enforced at the DB level).
    """
    result = await db.execute(
        select(Resume).where(
            Resume.id == resume_id,
            Resume.user_id == current_user.id,
        )
    )
    resume = result.scalar_one_or_none()
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found.")

    try:
        # Delete from GCS
        try:
            delete_file(resume.gcs_path)
        except Exception:
            pass  # GCS file might not exist; still remove DB record

        # Delete DB record
        await db.delete(resume)
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete this resume — it has been used in a job application.",
        )
