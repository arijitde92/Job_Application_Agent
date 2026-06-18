"""
github_router.py
----------------
CRUD endpoints for managing GitHub profiles linked to a user account.
"""

import re
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.models import User, GithubProfile
from app.schemas import GithubProfileCreate, GithubProfileResponse
from app.api.deps import get_current_user

router = APIRouter(prefix="/api/github", tags=["github"])


def _sanitize_dataset_name(email: str) -> str:
    """
    Generate a BigQuery-safe dataset name from a user's email.
    Replaces all non-alphanumeric characters with underscores.
    Example: arijitde2050@gmail.com → arijitde2050_gmail_com_github_bq_db
    """
    sanitized = re.sub(r"[^a-zA-Z0-9]", "_", email)
    return f"{sanitized}_github_bq_db"


@router.post("", response_model=GithubProfileResponse, status_code=status.HTTP_201_CREATED)
async def add_github_profile(
    req: GithubProfileCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add a GitHub profile (username) for the authenticated user."""
    # Check for duplicate
    existing = await db.execute(
        select(GithubProfile).where(
            GithubProfile.user_id == current_user.id,
            GithubProfile.github_username == req.github_username,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"GitHub username '{req.github_username}' is already linked to your account.",
        )

    profile = GithubProfile(
        user_id=current_user.id,
        github_username=req.github_username,
        github_url=f"https://github.com/{req.github_username}",
        bq_dataset_name=_sanitize_dataset_name(current_user.email),
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile


@router.get("", response_model=list[GithubProfileResponse])
async def list_github_profiles(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all GitHub profiles for the authenticated user."""
    result = await db.execute(
        select(GithubProfile).where(GithubProfile.user_id == current_user.id)
    )
    return result.scalars().all()


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_github_profile(
    profile_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Delete a GitHub profile. Fails if the profile has been used in a job
    (ON DELETE RESTRICT enforced at the DB level).
    """
    result = await db.execute(
        select(GithubProfile).where(
            GithubProfile.id == profile_id,
            GithubProfile.user_id == current_user.id,
        )
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="GitHub profile not found.")

    try:
        await db.delete(profile)
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete this GitHub profile — it has been used in a job application.",
        )
