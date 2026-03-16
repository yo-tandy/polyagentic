"""Agent template repository — CRUD + search for reusable agent configurations."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import or_, select, cast, String as SAString

from core.constants import gen_id
from db.models.agent_template import AgentTemplate
from db.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class AgentTemplateRepository(BaseRepository):

    async def create(
        self,
        name: str,
        title: str,
        scope: str = "org",
        personality: str = "",
        model: str = "sonnet",
        allowed_tools: str = "",
        tags: list[str] | None = None,
        source_agent_id: str | None = None,
        tenant_id: str = "default",
    ) -> AgentTemplate:
        async with self._session() as session:
            tmpl = AgentTemplate(
                id=gen_id("tmpl_"),
                scope=scope,
                name=name,
                title=title,
                personality=personality,
                model=model,
                allowed_tools=allowed_tools,
                tags=tags or [],
                source_agent_id=source_agent_id,
                tenant_id=tenant_id,
            )
            session.add(tmpl)
            await session.commit()
            await session.refresh(tmpl)
            return tmpl

    async def get(self, template_id: str) -> AgentTemplate | None:
        return await self._get_by_id(AgentTemplate, template_id)

    async def get_all(
        self, tenant_id: str = "default", scope: str | None = None,
    ) -> list[AgentTemplate]:
        """Return templates visible to this tenant.

        Visibility: scope='global' OR (scope='org' AND tenant_id matches).
        If *scope* is provided, filter to just that scope.
        """
        async with self._session() as session:
            if scope:
                conditions = [AgentTemplate.scope == scope]
                if scope == "org":
                    conditions.append(AgentTemplate.tenant_id == tenant_id)
            else:
                conditions = [
                    or_(
                        AgentTemplate.scope == "global",
                        (AgentTemplate.tenant_id == tenant_id),
                    )
                ]
            stmt = (
                select(AgentTemplate)
                .where(*conditions)
                .order_by(AgentTemplate.name)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def search(
        self, query: str, tenant_id: str = "default",
    ) -> list[AgentTemplate]:
        """Fuzzy search on title, personality, and tags (as text)."""
        async with self._session() as session:
            like = f"%{query}%"
            visibility = or_(
                AgentTemplate.scope == "global",
                (AgentTemplate.tenant_id == tenant_id),
            )
            match = or_(
                AgentTemplate.title.ilike(like),
                AgentTemplate.personality.ilike(like),
                cast(AgentTemplate.tags, SAString).ilike(like),
            )
            stmt = (
                select(AgentTemplate)
                .where(visibility, match)
                .order_by(AgentTemplate.name)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_by_source_agent(
        self, agent_id: str, tenant_id: str = "default",
    ) -> AgentTemplate | None:
        """Find the template linked to a live agent (for personality sync)."""
        async with self._session() as session:
            stmt = (
                select(AgentTemplate)
                .where(AgentTemplate.source_agent_id == agent_id)
                .limit(1)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_source_agent_ids(
        self, tenant_id: str = "default",
    ) -> set[str]:
        """Return the set of all source_agent_ids for the blue-diamond check."""
        async with self._session() as session:
            stmt = (
                select(AgentTemplate.source_agent_id)
                .where(AgentTemplate.source_agent_id.is_not(None))
            )
            result = await session.execute(stmt)
            return {row[0] for row in result.all()}

    async def update(self, template_id: str, **kwargs: Any) -> AgentTemplate | None:
        async with self._session() as session:
            tmpl = await session.get(AgentTemplate, template_id)
            if not tmpl:
                return None
            for key, value in kwargs.items():
                if hasattr(tmpl, key):
                    setattr(tmpl, key, value)
            await session.commit()
            await session.refresh(tmpl)
            return tmpl

    async def delete(self, template_id: str) -> bool:
        return await self._delete_by_id(AgentTemplate, template_id)
