"""
config.py
---------
Application settings loaded from environment variables via Pydantic BaseSettings.
All existing env vars (GCP, Gemini, Bright Data, etc.) are preserved for
backward compatibility with the standalone CLI pipeline.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache


class Settings(BaseSettings):
    """Application configuration — sourced from .env or environment variables."""

    # ── MySQL / Cloud SQL ─────────────────────────────────────────────────
    MYSQL_HOST: str = Field(default="127.0.0.1", description="Cloud SQL MySQL host")
    MYSQL_PORT: int = Field(default=3306, description="Cloud SQL MySQL port")
    MYSQL_USER: str = Field(default="root", description="MySQL user")
    MYSQL_PASSWORD: str = Field(default="", description="MySQL password")
    MYSQL_DATABASE: str = Field(default="job_applier", description="MySQL database name")

    # ── GCS ────────────────────────────────────────────────────────────────
    GCS_BUCKET_NAME: str = Field(
        default="job-applier-resumes",
        description="GCS bucket for resume storage",
    )

    # ── JWT ────────────────────────────────────────────────────────────────
    JWT_SECRET_KEY: str = Field(
        default="CHANGE_ME_IN_PRODUCTION",
        description="Secret key for signing JWT tokens",
    )
    JWT_ALGORITHM: str = Field(default="HS256")
    JWT_EXPIRY_MINUTES: int = Field(default=1440, description="Token expiry in minutes (default 24h)")

    # ── Google OAuth ──────────────────────────────────────────────────────
    GOOGLE_OAUTH_CLIENT_ID: str = Field(default="", description="Google OAuth 2.0 Client ID")
    GOOGLE_OAUTH_CLIENT_SECRET: str = Field(default="", description="Google OAuth 2.0 Client Secret")

    # ── GCP (existing) ────────────────────────────────────────────────────
    GOOGLE_APPLICATION_CREDENTIALS: str = Field(default="", description="Path to GCP service account JSON")
    GCP_PROJECT_ID: str = Field(default="", description="GCP project ID")
    GCP_LOCATION: str = Field(default="asia-south2", description="GCP region")
    GCP_DATASET_NAME: str = Field(default="job_applier_app", description="BigQuery dataset name")
    GCP_TABLE_NAME: str = Field(default="github_repo_data", description="BigQuery table name")

    # ── API Keys (existing) ───────────────────────────────────────────────
    BRIGHT_DATA_API_KEY: str = Field(default="", description="Bright Data MCP API key")
    GITHUB_PERSONAL_ACCESS_TOKEN: str = Field(default="", description="GitHub PAT")
    GEMINI_API_KEY: str = Field(default="", description="Google Gemini API key")
    SERPER_API_KEY: str = Field(default="", description="Serper search API key")

    # ── Resume Upload ─────────────────────────────────────────────────────
    MAX_RESUME_SIZE_MB: int = Field(default=10, description="Max resume upload size in MB")

    @property
    def mysql_url(self) -> str:
        """Construct the async MySQL connection URL for SQLAlchemy."""
        import urllib.parse
        encoded_password = urllib.parse.quote_plus(self.MYSQL_PASSWORD)
        return (
            f"mysql+asyncmy://{self.MYSQL_USER}:{encoded_password}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}"
        )

    @property
    def mysql_url_sync(self) -> str:
        """Construct the sync MySQL connection URL for SQLAlchemy (used in migrations)."""
        import urllib.parse
        encoded_password = urllib.parse.quote_plus(self.MYSQL_PASSWORD)
        return (
            f"mysql+pymysql://{self.MYSQL_USER}:{encoded_password}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}"
        )

    @property
    def max_resume_bytes(self) -> int:
        return self.MAX_RESUME_SIZE_MB * 1024 * 1024

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


@lru_cache()
def get_settings() -> Settings:
    """Return a cached Settings instance (reads .env once)."""
    return Settings()
