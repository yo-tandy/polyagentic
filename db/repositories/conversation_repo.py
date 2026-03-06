"""Conversation repository — CRUD for conversations and messages."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from db.models.conversation import Conversation, ConversationMessage
from db.repositories.base import BaseRepository


class ConversationRepository(BaseRepository):

    async def start(
        self,
        project_id: str,
        agent_id: str,
        title: str,
        goals: list[str],
        tenant_id: str = "default",
    ) -> Conversation:
        """Start a new conversation (or return existing active one)."""
        existing = await self.get_by_agent(project_id, agent_id, tenant_id)
        if existing:
            return existing

        async with self._session() as session:
            conv = Conversation(
                id=str(uuid.uuid4()),
                project_id=project_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
                title=title,
                goals=goals,
            )
            session.add(conv)
            await session.commit()
            await session.refresh(conv)
            return conv

    async def get(self, conv_id: str) -> Conversation | None:
        async with self._session() as session:
            return await session.get(Conversation, conv_id)

    async def get_active(
        self, project_id: str, tenant_id: str = "default",
    ) -> list[Conversation]:
        async with self._session() as session:
            stmt = select(Conversation).where(
                Conversation.project_id == project_id,
                Conversation.tenant_id == tenant_id,
                Conversation.state == "active",
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_by_agent(
        self,
        project_id: str,
        agent_id: str,
        tenant_id: str = "default",
    ) -> Conversation | None:
        async with self._session() as session:
            stmt = select(Conversation).where(
                Conversation.project_id == project_id,
                Conversation.tenant_id == tenant_id,
                Conversation.agent_id == agent_id,
                Conversation.state == "active",
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def record_message(
        self, conv_id: str, sender: str, content: str,
    ) -> None:
        async with self._session() as session:
            msg = ConversationMessage(
                conversation_id=conv_id,
                sender=sender,
                content=content,
            )
            session.add(msg)
            await session.commit()

    async def close(self, conv_id: str) -> Conversation | None:
        async with self._session() as session:
            conv = await session.get(Conversation, conv_id)
            if not conv:
                return None
            conv.state = "closed"
            conv.closed_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(conv)
            return conv

    async def close_by_agent(
        self,
        project_id: str,
        agent_id: str,
        tenant_id: str = "default",
    ) -> Conversation | None:
        conv = await self.get_by_agent(project_id, agent_id, tenant_id)
        if not conv:
            return None
        return await self.close(conv.id)

    async def get_messages(self, conv_id: str) -> list[ConversationMessage]:
        async with self._session() as session:
            stmt = select(ConversationMessage).where(
                ConversationMessage.conversation_id == conv_id,
            ).order_by(ConversationMessage.created_at)
            result = await session.execute(stmt)
            return list(result.scalars().all())
