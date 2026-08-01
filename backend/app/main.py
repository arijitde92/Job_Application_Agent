"""
app.main
--------
FastAPI application entry point: CORS, lifespan events, and router registration.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import update

from app.core.config import get_settings
from app.core.database import engine, Base
from app.core.logging import get_logger
from app.api.v1 import auth, github, resumes, jobs

settings = get_settings()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables; best-effort fail jobs stuck >3h; dispose engine on shutdown."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Lightweight migration: create_all only creates missing tables, it
        # never alters existing ones. Add resumes.parsed_resume_path (written
        # by the resume_analyzer agent) if the column is missing. Idempotent
        # and best-effort, same policy as the cleanup below.
        try:
            def _add_parsed_resume_column(sync_conn):
                from sqlalchemy import inspect, text
                columns = {c["name"] for c in inspect(sync_conn).get_columns("resumes")}
                if "parsed_resume_path" not in columns:
                    sync_conn.execute(text(
                        "ALTER TABLE resumes ADD COLUMN parsed_resume_path VARCHAR(500) NULL"
                    ))
                    logger.info("Added resumes.parsed_resume_path column")
            await conn.run_sync(_add_parsed_resume_column)
        except Exception as exc:  # noqa: BLE001 — never let migration break startup
            logger.warning("Startup parsed_resume_path migration skipped: %s", exc)

        # Same lightweight-migration policy: add the jobs columns for the
        # docx/pdf tailored-resume artifacts (written by the crew runner) if
        # they are missing. Idempotent and best-effort.
        try:
            def _add_tailored_artifact_columns(sync_conn):
                from sqlalchemy import inspect, text
                columns = {c["name"] for c in inspect(sync_conn).get_columns("jobs")}
                for column in ("tailored_resume_md_gcs_path", "tailored_resume_pdf_gcs_path"):
                    if column not in columns:
                        sync_conn.execute(text(
                            f"ALTER TABLE jobs ADD COLUMN {column} VARCHAR(500) NULL"
                        ))
                        logger.info("Added jobs.%s column", column)
            await conn.run_sync(_add_tailored_artifact_columns)
        except Exception as exc:  # noqa: BLE001 — never let migration break startup
            logger.warning("Startup tailored-artifact-columns migration skipped: %s", exc)

        # Best-effort: fail jobs left pending/processing for >3h (orphaned by a
        # prior restart). Reuses THIS connection — no new session/checkout — and
        # swallows all errors so it can never abort startup or kill the proxy.
        try:
            from app.models import Job
            cutoff = datetime.utcnow() - timedelta(hours=3)
            await conn.execute(
                update(Job)
                .where(
                    Job.status.in_(("pending", "processing")),
                    Job.created_at < cutoff,
                )
                .values(
                    status="failed",
                    error_message="Job was stuck in progress for over 3 hours "
                                  "(likely a server restart). Please retry.",
                )
            )
        except Exception as exc:  # noqa: BLE001 — never let cleanup break startup
            logger.warning("Startup stuck-job cleanup skipped: %s", exc)

    yield
    await engine.dispose()


app = FastAPI(
    title="Job Application Agent API",
    description="AI-powered resume tailoring and interview preparation",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow frontend dev server and production origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers (versioned under /api) ────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(github.router)
app.include_router(resumes.router)
app.include_router(jobs.router)


@app.get("/api/health", tags=["health"])
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "job-application-agent"}
