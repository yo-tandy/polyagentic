"""Memory repository — CRUD for agent_memories table."""

from __future__ import annotations

from sqlalchemy import select

from db.models.memory import AgentMemory
from db.repositories.base import BaseRepository


class MemoryRepository(BaseRepository):

    async def get(
        self,
        agent_id: str,
        memory_type: str,
        project_id: str | None = None,
        tenant_id: str = "default",
    ) -> str:
        """Get memory content.  Returns empty string if not found."""
        async with self._session() as session:
            stmt = select(AgentMemory.content).where(
                AgentMemory.tenant_id == tenant_id,
                AgentMemory.agent_id == agent_id,
                AgentMemory.memory_type == memory_type,
                AgentMemory.project_id == project_id,
            )
            result = await session.execute(stmt)
            content = result.scalar_one_or_none()
            return content or ""

    async def update(
        self,
        agent_id: str,
        memory_type: str,
        content: str,
        project_id: str | None = None,
        tenant_id: str = "default",
    ) -> None:
        """Upsert memory content."""
        async with self._session() as session:
            stmt = select(AgentMemory).where(
                AgentMemory.tenant_id == tenant_id,
                AgentMemory.agent_id == agent_id,
                AgentMemory.memory_type == memory_type,
                AgentMemory.project_id == project_id,
            )
            result = await session.execute(stmt)
            record = result.scalar_one_or_none()

            if record:
                record.content = content
            else:
                record = AgentMemory(
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    memory_type=memory_type,
                    project_id=project_id,
                    content=content,
                )
                session.add(record)

            await session.commit()
