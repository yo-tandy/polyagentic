"""Session repository — CRUD for agent_sessions table."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from db.models.session import AgentSession
from db.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class SessionRepository(BaseRepository):

    async def get(
        self, project_id: str, agent_id: str,
    ) -> AgentSession | None:
        async with self._session() as session:
            stmt = select(AgentSession).where(
                AgentSession.project_id == project_id,
                AgentSession.agent_id == agent_id,
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_or_create(
        self, project_id: str, agent_id: str,
    ) -> AgentSession:
        record = await self.get(project_id, agent_id)
        if record:
            return record
        async with self._session() as session:
            record = AgentSession(
                project_id=project_id,
                agent_id=agent_id,
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def set_session_id(
        self, project_id: str, agent_id: str, session_id: str,
    ) -> None:
        async with self._session() as sess:
            record = await self._get_or_create_in(sess, project_id, agent_id)
            record.session_id = session_id
            record.last_used_at = datetime.now(timezone.utc)
            await sess.commit()

    async def get_all(self, project_id: str) -> dict[str, AgentSession]:
        async with self._session() as session:
            stmt = select(AgentSession).where(
                AgentSession.project_id == project_id,
            )
            result = await session.execute(stmt)
            records = result.scalars().all()
            return {r.agent_id: r for r in records}

    async def set_state(
        self, project_id: str, agent_id: str, state: str,
    ) -> None:
        async with self._session() as sess:
            record = await self._get_or_create_in(sess, project_id, agent_id)
            record.state = state
            now = datetime.now(timezone.utc)
            if state == "paused":
                record.paused_at = now
            elif state == "killed":
                record.killed_at = now
            await sess.commit()

    async def record_request(
        self,
        project_id: str,
        agent_id: str,
        duration_ms: int = 0,
        is_error: bool = False,
        cost_usd: float = 0.0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        consecutive_error_threshold: int = 3,
    ) -> bool:
        """Record a request.  Returns True if auto-paused due to errors."""
        async with self._session() as sess:
            record = await self._get_or_create_in(sess, project_id, agent_id)
            record.request_count += 1
            record.total_duration_ms += duration_ms
            record.total_cost_usd += cost_usd
            record.total_input_tokens += input_tokens
            record.total_output_tokens += output_tokens
            record.last_used_at = datetime.now(timezone.utc)

            if is_error:
                record.error_count += 1
                record.consecutive_errors += 1
            else:
                record.consecutive_errors = 0

            auto_paused = False
            if record.consecutive_errors >= consecutive_error_threshold:
                record.state = "paused"
                record.paused_at = datetime.now(timezone.utc)
                auto_paused = True
                logger.warning(
                    "Auto-pausing %s after %d consecutive errors",
                    agent_id, record.consecutive_errors,
                )

            await sess.commit()
            return auto_paused

    async def set_model(
        self, project_id: str, agent_id: str, model: str | None,
    ) -> None:
        async with self._session() as sess:
            record = await self._get_or_create_in(sess, project_id, agent_id)
            record.model = model
            await sess.commit()

    async def set_prompt_hash(
        self, project_id: str, agent_id: str, hash_val: str,
    ) -> None:
        async with self._session() as sess:
            record = await self._get_or_create_in(sess, project_id, agent_id)
            record.prompt_hash = hash_val
            await sess.commit()

    async def invalidate_session(
        self, project_id: str, agent_id: str,
    ) -> None:
        """Clear session_id but keep stats."""
        async with self._session() as sess:
            record = await self._get_or_create_in(sess, project_id, agent_id)
            record.session_id = ""
            await sess.commit()

    async def clear_session(
        self, project_id: str, agent_id: str,
    ) -> None:
        """Full reset — clear session_id and all stats."""
        async with self._session() as sess:
            record = await self._get_or_create_in(sess, project_id, agent_id)
            record.session_id = ""
            record.state = "active"
            record.request_count = 0
            record.error_count = 0
            record.consecutive_errors = 0
            record.total_duration_ms = 0
            record.total_cost_usd = 0.0
            record.total_input_tokens = 0
            record.total_output_tokens = 0
            record.paused_at = None
            record.killed_at = None
            await sess.commit()

    # ── Internal helper ───────────────────────────────────────────────

    async def _get_or_create_in(
        self, sess: Any, project_id: str, agent_id: str,
    ) -> AgentSession:
        """Get or create within an existing session (no commit)."""
        stmt = select(AgentSession).where(
            AgentSession.project_id == project_id,
            AgentSession.agent_id == agent_id,
        )
        result = await sess.execute(stmt)
        record = result.scalar_one_or_none()
        if not record:
            record = AgentSession(
                project_id=project_id,
                agent_id=agent_id,
            )
            sess.add(record)
            await sess.flush()
        return record
