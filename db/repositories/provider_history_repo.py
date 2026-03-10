"""Provider history repository — CRUD for API provider conversation messages."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import delete, func, select

from db.models.provider_history import ProviderMessage
from db.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class ProviderHistoryRepository(BaseRepository):

    async def create_session(self, project_id: str, agent_id: str) -> str:
        """Create a new conversation session. Returns the session ID."""
        session_id = f"psess_{uuid.uuid4().hex[:16]}"
        logger.info(
            "Created provider session %s for agent %s",
            session_id, agent_id,
        )
        return session_id

    async def append(
        self,
        session_id: str,
        project_id: str,
        agent_id: str,
        role: str,
        content: str | None = None,
        tool_calls: list[dict] | None = None,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
    ) -> None:
        """Append a message to the conversation history."""
        async with self._session() as session:
            # Get next sequence number
            stmt = select(func.max(ProviderMessage.sequence)).where(
                ProviderMessage.session_id == session_id,
            )
            result = await session.execute(stmt)
            max_seq = result.scalar() or 0

            msg = ProviderMessage(
                session_id=session_id,
                agent_id=agent_id,
                project_id=project_id,
                sequence=max_seq + 1,
                role=role,
                content=content,
                tool_calls_json=json.dumps(tool_calls) if tool_calls else None,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
            )
            session.add(msg)
            await session.commit()

    async def get_history(self, session_id: str) -> list[dict]:
        """Get full conversation history for replay."""
        async with self._session() as session:
            stmt = (
                select(ProviderMessage)
                .where(ProviderMessage.session_id == session_id)
                .order_by(ProviderMessage.sequence)
            )
            result = await session.execute(stmt)
            records = result.scalars().all()

            messages = []
            for rec in records:
                msg: dict[str, Any] = {
                    "role": rec.role,
                }
                if rec.content is not None:
                    msg["content"] = rec.content
                if rec.tool_calls_json:
                    msg["tool_calls"] = json.loads(rec.tool_calls_json)
                if rec.tool_call_id:
                    msg["tool_call_id"] = rec.tool_call_id
                if rec.tool_name:
                    msg["tool_name"] = rec.tool_name
                messages.append(msg)

            return messages

    async def get_message_count(self, session_id: str) -> int:
        """Get number of messages in a session."""
        async with self._session() as session:
            stmt = select(func.count()).where(
                ProviderMessage.session_id == session_id,
            )
            result = await session.execute(stmt)
            return result.scalar() or 0

    async def clear_session(self, session_id: str) -> None:
        """Delete all messages for a session."""
        async with self._session() as session:
            stmt = delete(ProviderMessage).where(
                ProviderMessage.session_id == session_id,
            )
            await session.execute(stmt)
            await session.commit()
            logger.info("Cleared provider session %s", session_id)
