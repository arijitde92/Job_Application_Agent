"""
schemas.py
----------
Pydantic request/response schemas for the FastAPI endpoints.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field


# ── Auth ──────────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=8)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class GoogleOAuthRequest(BaseModel):
    id_token: str = Field(..., description="Google OAuth ID token from frontend")


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ── GitHub Profiles ───────────────────────────────────────────────────────────

class GithubProfileCreate(BaseModel):
    github_username: str = Field(..., min_length=1, max_length=100)


class GithubProfileResponse(BaseModel):
    id: int
    github_username: str
    github_url: str
    bq_dataset_name: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Resumes ───────────────────────────────────────────────────────────────────

class ResumeResponse(BaseModel):
    id: int
    original_filename: str
    gcs_path: str
    uploaded_at: datetime

    model_config = {"from_attributes": True}


# ── Jobs ──────────────────────────────────────────────────────────────────────

class TailorJobRequest(BaseModel):
    linkedin_job_url: str = Field(..., description="LinkedIn job posting URL")
    github_profile_id: Optional[int] = Field(None, description="ID of the GitHub profile to use (optional)")
    resume_id: int = Field(..., description="ID of the uploaded resume to use")


class JobResponse(BaseModel):
    id: int
    linkedin_job_url: str
    job_name: Optional[str] = None
    company_name: Optional[str] = None
    location: Optional[str] = None
    seniority_level: Optional[str] = None
    employment_type: Optional[str] = None
    job_function: Optional[str] = None
    industry: Optional[str] = None
    status: str
    error_message: Optional[str] = None
    tailored_resume_gcs_path: Optional[str] = None
    interview_materials_gcs_path: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class JobDetailResponse(JobResponse):
    """Extended job response including the full description and requirements."""
    job_description: Optional[str] = None
    requirements: Optional[str] = None

    model_config = {"from_attributes": True}
