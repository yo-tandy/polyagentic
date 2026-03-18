"""Project repository — CRUD for projects and custom agent definitions."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select, update, delete

from db.models.project import Project, CustomAgentDef
from db.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class ProjectRepository(BaseRepository):

    async def create(
        self, id: str, name: str, description: str = "",
        tenant_id: str = "default", **extra: Any,
    ) -> Project:
        async with self._session() as session:
            project = Project(
                id=id, name=name, description=description,
                tenant_id=tenant_id, **extra,
            )
            session.add(project)
            await session.commit()
            await session.refresh(project)
            return project

    async def get(self, project_id: str) -> Project | None:
        async with self._session() as session:
            return await session.get(Project, project_id)

    async def list_all(self, tenant_id: str = "default") -> list[Project]:
        async with self._session() as session:
            stmt = select(Project).where(
                Project.tenant_id == tenant_id,
            ).order_by(Project.created_at)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_active(self, tenant_id: str = "default") -> Project | None:
        async with self._session() as session:
            stmt = select(Project).where(
                Project.tenant_id == tenant_id,
                Project.is_active == True,  # noqa: E712
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def set_active(
        self, project_id: str, tenant_id: str = "default",
    ) -> None:
        async with self._session() as session:
            # Deactivate all
            await session.execute(
                update(Project)
                .where(Project.tenant_id == tenant_id)
                .values(is_active=False)
            )
            # Activate target
            await session.execute(
                update(Project)
                .where(Project.id == project_id)
                .values(is_active=True)
            )
            await session.commit()

    async def update(self, project_id: str, **kwargs: Any) -> Project | None:
        async with self._session() as session:
            project = await session.get(Project, project_id)
            if not project:
                return None
            for k, v in kwargs.items():
                if hasattr(project, k):
                    setattr(project, k, v)
            await session.commit()
            await session.refresh(project)
            return project

    async def set_running(
        self, project_id: str, is_running: bool,
    ) -> None:
        async with self._session() as session:
            await session.execute(
                update(Project)
                .where(Project.id == project_id)
                .values(is_running=is_running)
            )
            await session.commit()

    async def get_running(
        self, tenant_id: str = "default",
    ) -> list[Project]:
        async with self._session() as session:
            stmt = select(Project).where(
                Project.tenant_id == tenant_id,
                Project.is_running == True,  # noqa: E712
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def clear_running(self, tenant_id: str = "default") -> None:
        """Mark all projects as not running (used at startup)."""
        async with self._session() as session:
            await session.execute(
                update(Project)
                .where(Project.tenant_id == tenant_id)
                .values(is_running=False)
            )
            await session.commit()

    async def delete(self, project_id: str) -> bool:
        async with self._session() as session:
            stmt = delete(Project).where(Project.id == project_id)
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    # ── Custom agent definitions ──────────────────────────────────────

    async def get_custom_agents(self, project_id: str) -> list[CustomAgentDef]:
        async with self._session() as session:
            stmt = select(CustomAgentDef).where(
                CustomAgentDef.project_id == project_id,
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def add_custom_agent(
        self, project_id: str, agent_def: dict,
    ) -> CustomAgentDef:
        async with self._session() as session:
            cad = CustomAgentDef(
                project_id=project_id,
                name=agent_def["name"],
                role=agent_def.get("role", ""),
                system_prompt=agent_def.get("system_prompt", ""),
                model=agent_def.get("model", "sonnet"),
                allowed_tools=agent_def.get(
                    "allowed_tools", "Bash,Edit,Write,Read,Glob,Grep",
                ),
            )
            session.add(cad)
            await session.commit()
            await session.refresh(cad)
            return cad

    async def remove_custom_agent(
        self, project_id: str, agent_name: str,
    ) -> bool:
        async with self._session() as session:
            stmt = delete(CustomAgentDef).where(
                CustomAgentDef.project_id == project_id,
                CustomAgentDef.name == agent_name,
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0
