"""
database.py
-----------
SQLAlchemy async engine, session factory, and dependency injection for FastAPI.
Uses asyncmy driver for Cloud SQL MySQL connectivity.
"""

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

# ── Monkeypatch SQLAlchemy asyncmy ping issue ──────────────────────────────
# In PyMySQL 1.2.0+, ping() defaults reconnect to False.
# SQLAlchemy's asyncmy adapter signature requires reconnect to be explicitly passed,
# but SQLAlchemy's pymysql base dialect calls ping() without args when _send_false_to_ping is False.
try:
    from sqlalchemy.dialects.mysql.asyncmy import AsyncAdapt_asyncmy_connection
    _orig_ping = AsyncAdapt_asyncmy_connection.ping
    def _patched_ping(self, reconnect: bool = False) -> None:
        return _orig_ping(self, reconnect)
    AsyncAdapt_asyncmy_connection.ping = _patched_ping
except ImportError:
    pass

settings = get_settings()

# ── Async engine ──────────────────────────────────────────────────────────────
engine = create_async_engine(
    settings.mysql_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

# ── Session factory ───────────────────────────────────────────────────────────
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ── Declarative Base ──────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


# ── Dependency for FastAPI routes ─────────────────────────────────────────────
async def get_db() -> AsyncSession:
    """Yield an async DB session and ensure it is closed after use."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
