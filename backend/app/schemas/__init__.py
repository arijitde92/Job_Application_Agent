"""
schemas.py
----------
Pydantic request/response schemas for the FastAPI endpoints.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field, model_validator

from app.services.extractors.text_job_extractor import (
    MAX_JOB_DESCRIPTION_CHARS,
    MIN_JOB_DESCRIPTION_CHARS,
)


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
    parsed_resume_path: Optional[str] = None
    uploaded_at: datetime

    model_config = {"from_attributes": True}


# ── Jobs ──────────────────────────────────────────────────────────────────────

class TailorJobRequest(BaseModel):
    """Exactly one job source is required: a LinkedIn URL or the description text
    (pasted, or extracted from an uploaded file via /api/jobs/description/extract)."""
    linkedin_job_url: Optional[str] = Field(None, max_length=500, description="LinkedIn job posting URL")
    job_description_text: Optional[str] = Field(
        None, max_length=MAX_JOB_DESCRIPTION_CHARS,
        description="Full job description text, used instead of a LinkedIn URL",
    )
    github_profile_id: Optional[int] = Field(None, description="ID of the GitHub profile to use (optional)")
    resume_id: int = Field(..., description="ID of the uploaded resume to use")

    @model_validator(mode="after")
    def _exactly_one_job_source(self) -> "TailorJobRequest":
        # Blank strings count as "not provided" so the form can send both keys.
        self.linkedin_job_url = (self.linkedin_job_url or "").strip() or None
        self.job_description_text = (self.job_description_text or "").strip() or None
        if bool(self.linkedin_job_url) == bool(self.job_description_text):
            raise ValueError(
                "Provide either a LinkedIn job URL or a job description, not both."
                if self.linkedin_job_url else
                "A LinkedIn job URL or a job description is required."
            )
        if self.job_description_text and \
                len("".join(self.job_description_text.split())) < MIN_JOB_DESCRIPTION_CHARS:
            raise ValueError(
                "The job description is too short — paste the full job posting."
            )
        return self


class JobDescriptionTextResponse(BaseModel):
    """Text extracted from an uploaded job description file."""
    filename: str
    text: str


class JobResponse(BaseModel):
    id: int
    github_profile_id: Optional[int] = None
    # None when the job was created from a pasted/uploaded job description.
    linkedin_job_url: Optional[str] = None
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
    tailored_resume_md_gcs_path: Optional[str] = None
    tailored_resume_pdf_gcs_path: Optional[str] = None
    interview_materials_gcs_path: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class JobDetailResponse(JobResponse):
    """Extended job response including the full description and requirements."""
    job_description: Optional[str] = None
    requirements: Optional[str] = None

    model_config = {"from_attributes": True}
