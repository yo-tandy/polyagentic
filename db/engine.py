"""Database engine factory and initialization.

The only environment-based configuration: ``DATABASE_URL``.
Everything else is stored in the ``config_entries`` table.
"""

from __future__ import annotations

import logging
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./polyagentic.db",
)

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


async def init_db(url: str | None = None) -> None:
    """Create engine, create tables if they don't exist, seed defaults.

    In production, Alembic migrations should be used instead of
    ``create_all``.
    """
    global _engine, _session_factory

    effective_url = url or DATABASE_URL
    logger.info("Initializing database: %s", effective_url.split("@")[-1])

    connect_args = {}
    if effective_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    _engine = create_async_engine(
        effective_url,
        echo=False,
        connect_args=connect_args,
    )
    _session_factory = async_sessionmaker(
        _engine, class_=AsyncSession, expire_on_commit=False,
    )

    # Create all tables (dev mode — production uses Alembic)
    from db.models import Base  # noqa: F811 — triggers all model imports
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Lightweight column migrations for SQLite (create_all won't add new columns)
    async with _engine.begin() as conn:
        for stmt in [
            "ALTER TABLE agent_sessions ADD COLUMN last_error TEXT",
            "ALTER TABLE documents ADD COLUMN upload_path VARCHAR(1000)",
            "ALTER TABLE documents ADD COLUMN file_type VARCHAR(20)",
            "ALTER TABLE documents ADD COLUMN file_size INTEGER",
            "ALTER TABLE tasks ADD COLUMN category VARCHAR(20) DEFAULT 'operational'",
            "ALTER TABLE tasks ADD COLUMN phase_id VARCHAR(20) REFERENCES phases(id)",
            # Provider support (Phase 7)
            "ALTER TABLE agent_roles ADD COLUMN provider VARCHAR(20) DEFAULT 'claude-cli'",
            "ALTER TABLE agent_roles ADD COLUMN fallback_provider VARCHAR(20)",
            "ALTER TABLE team_agent_defs ADD COLUMN provider VARCHAR(20) DEFAULT 'claude-cli'",
            "ALTER TABLE team_agent_defs ADD COLUMN fallback_provider VARCHAR(20)",
            # User attribution (Phase 5)
            "ALTER TABLE message_log ADD COLUMN user_id VARCHAR(64)",
        ]:
            try:
                await conn.execute(text(stmt))
                logger.info("Migration applied: %s", stmt)
            except Exception:
                pass  # Column already exists

    # Seed default organization (existing data uses tenant_id='default')
    async with _session_factory() as session:
        from db.models.organization import Organization
        org = await session.get(Organization, "default")
        if not org:
            session.add(Organization(id="default", name="Default Organization"))
            await session.commit()
            logger.info("Seeded default organization")

    logger.info("Database tables created/verified")


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the async session factory.  Must call ``init_db`` first."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized — call init_db() first")
    return _session_factory


def get_engine():
    """Return the async engine.  Must call ``init_db`` first."""
    if _engine is None:
        raise RuntimeError("Database not initialized — call init_db() first")
    return _engine
