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

    # ── Weaviate (GitHub repo vector store) ───────────────────────────────
    # Replaces the former BigQuery vector store. WEAVIATE_URL may be given
    # with or without a scheme — the store normalises it to https://.
    WEAVIATE_URL: str = Field(default="", description="Weaviate Cloud cluster URL or host")
    WEAVIATE_API_KEY: str = Field(default="", description="Weaviate Cloud API key")
    WEAVIATE_COLLECTION_NAME: str = Field(
        default="GithubRepoData",
        description="Weaviate collection holding GitHub repo chunks (must start uppercase)",
    )

    # ── Voyage AI (embeddings + reranking) ────────────────────────────────
    VOYAGE_API_KEY: str = Field(default="", description="Voyage AI API key")
    VOYAGE_EMBED_MODEL: str = Field(default="voyage-code-3", description="Voyage embedding model")
    VOYAGE_EMBED_DIMENSION: int = Field(
        default=1024,
        description="Voyage embedding output dimension (voyage-code-3 supports 256/512/1024/2048)",
    )
    VOYAGE_RERANK_MODEL: str = Field(default="rerank-2.5-lite", description="Voyage reranker model")

    # ── API Keys (existing) ───────────────────────────────────────────────
    BRIGHT_DATA_API_KEY: str = Field(default="", description="Bright Data MCP API key")
    GITHUB_PERSONAL_ACCESS_TOKEN: str = Field(default="", description="GitHub PAT")
    GEMINI_API_KEY: str = Field(default="", description="Google Gemini API key")
    SERPER_API_KEY: str = Field(default="", description="Serper search API key")
    ZAI_API_KEY: str = Field(default="", description="Z.ai API key (GLM models)")
    ZAI_BASE_URL: str = Field(
        default="",
        description="Override for Z.ai's OpenAI-compatible base URL; blank uses https://api.z.ai/api/paas/v4",
    )

    # ── Resume DOCX service ───────────────────────────────────────────────
    RESUME_DOCX_SERVICE_URL: str = Field(
        default=(
            "http://resume-service-alb-1115872566.us-east-2.elb.amazonaws.com"
            "/api/v1/resume/generate"
        ),
        description="POST endpoint of the external resume .docx generation service",
    )
    RESUME_DOCX_TIMEOUT_SECONDS: int = Field(
        default=120,
        description="Read timeout (seconds) for the resume .docx generation service",
    )

    # ── Crew AI ───────────────────────────────────────────────────────────
    CREWAI_TOOLS_ALLOW_UNSAFE_PATHS: bool = Field(default=True, description="Allow CrewAI tools to access unsafe paths")
    CREWAI_TRACING_ENABLED: bool = Field(default=True, description="Enable CrewAI tracing")

    # ── Resume Upload ─────────────────────────────────────────────────────
    MAX_RESUME_SIZE_MB: int = Field(default=10, description="Max resume upload size in MB")

    # ── CORS ──────────────────────────────────────────────────────────────
    CORS_ORIGINS: str = Field(
        default="http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173",
        description="Comma-separated list of allowed CORS origins",
    )

    @property
    def cors_origins(self) -> list[str]:
        """Parse CORS_ORIGINS into a list of origin strings."""
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

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
    settings = Settings()
    import os
    if settings.GOOGLE_APPLICATION_CREDENTIALS:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = settings.GOOGLE_APPLICATION_CREDENTIALS
    # Propagate CrewAI variables to os.environ for libraries that read them directly
    os.environ["CREWAI_TOOLS_ALLOW_UNSAFE_PATHS"] = str(settings.CREWAI_TOOLS_ALLOW_UNSAFE_PATHS).lower()
    os.environ["CREWAI_TRACING_ENABLED"] = str(settings.CREWAI_TRACING_ENABLED).lower()
    return settings
