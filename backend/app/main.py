"""
app.main
--------
FastAPI application entry point: CORS, lifespan events, and router registration.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.database import engine, Base
from app.api.v1 import auth, github, resumes, jobs

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create database tables on startup and dispose the engine on shutdown."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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
