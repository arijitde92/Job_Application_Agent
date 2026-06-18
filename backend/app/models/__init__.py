"""
models.py
---------
SQLAlchemy ORM models for the Job Application Agent web app.

Tables:
    - users: User accounts (email/password or OAuth)
    - github_profiles: Linked GitHub accounts per user
    - resumes: Uploaded resume files tracked in GCS
    - jobs: Crew execution records with status tracking
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Enum, ForeignKey, DateTime, UniqueConstraint, Index
)
from sqlalchemy.orm import relationship
from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    email = Column(String(255), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False, default="")  # Empty for OAuth-only users
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    github_profiles = relationship("GithubProfile", back_populates="user", cascade="all, delete-orphan")
    resumes = relationship("Resume", back_populates="user", cascade="all, delete-orphan")
    jobs = relationship("Job", back_populates="user", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_email", "email"),
    )


class GithubProfile(Base):
    __tablename__ = "github_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    github_username = Column(String(100), nullable=False)
    github_url = Column(String(500), nullable=False)
    bq_dataset_name = Column(String(255), nullable=False)  # e.g. "user_email_github_bq_db"
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="github_profiles")
    jobs = relationship("Job", back_populates="github_profile")

    __table_args__ = (
        UniqueConstraint("user_id", "github_username", name="uq_user_github"),
    )


class Resume(Base):
    __tablename__ = "resumes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    original_filename = Column(String(255), nullable=False)
    gcs_path = Column(String(500), nullable=False)  # gs://bucket/uploads/<user_id>/<filename>.md
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="resumes")
    jobs = relationship("Job", back_populates="resume")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    github_profile_id = Column(Integer, ForeignKey("github_profiles.id", ondelete="RESTRICT"), nullable=False)
    resume_id = Column(Integer, ForeignKey("resumes.id", ondelete="RESTRICT"), nullable=False)
    linkedin_job_url = Column(String(500), nullable=False)

    # Extracted job details
    job_name = Column(String(255), nullable=True)
    company_name = Column(String(255), nullable=True)
    location = Column(String(255), nullable=True)
    seniority_level = Column(String(100), nullable=True)
    employment_type = Column(String(100), nullable=True)
    job_function = Column(String(255), nullable=True)
    industry = Column(String(255), nullable=True)
    job_description = Column(Text, nullable=True)
    requirements = Column(Text, nullable=True)

    # Output paths
    tailored_resume_gcs_path = Column(String(500), nullable=True)
    interview_materials_gcs_path = Column(String(500), nullable=True)

    # Status tracking
    status = Column(
        Enum("pending", "processing", "completed", "failed", name="job_status"),
        default="pending",
        nullable=False,
    )
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    # Relationships
    user = relationship("User", back_populates="jobs")
    github_profile = relationship("GithubProfile", back_populates="jobs")
    resume = relationship("Resume", back_populates="jobs")
