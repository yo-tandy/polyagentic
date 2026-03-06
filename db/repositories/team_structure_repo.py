"""Team structure repository — CRUD for team agent definitions and meta."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select, delete

from db.models.team_structure import TeamAgentDef, TeamStructureMeta
from db.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class TeamStructureRepository(BaseRepository):

    # ── Meta ──────────────────────────────────────────────────────────

    async def get_meta(
        self,
        tenant_id: str = "default",
        project_id: str | None = None,
    ) -> TeamStructureMeta | None:
        async with self._session() as session:
            stmt = select(TeamStructureMeta).where(
                TeamStructureMeta.tenant_id == tenant_id,
                TeamStructureMeta.project_id == project_id,
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_effective_meta(
        self,
        tenant_id: str = "default",
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Get meta with project override merged over global default."""
        global_meta = await self.get_meta(tenant_id, None)
        result = {
            "user_facing_agent": "manny",
            "privileged_agents": ["manny", "jerry"],
            "checkpoint_agent": "jerry",
        }
        if global_meta:
            result["user_facing_agent"] = global_meta.user_facing_agent
            result["privileged_agents"] = global_meta.privileged_agents
            result["checkpoint_agent"] = global_meta.checkpoint_agent

        if project_id:
            project_meta = await self.get_meta(tenant_id, project_id)
            if project_meta:
                result["user_facing_agent"] = project_meta.user_facing_agent
                result["privileged_agents"] = project_meta.privileged_agents
                result["checkpoint_agent"] = project_meta.checkpoint_agent

        return result

    async def upsert_meta(
        self,
        tenant_id: str = "default",
        project_id: str | None = None,
        **kwargs: Any,
    ) -> TeamStructureMeta:
        async with self._session() as session:
            stmt = select(TeamStructureMeta).where(
                TeamStructureMeta.tenant_id == tenant_id,
                TeamStructureMeta.project_id == project_id,
            )
            result = await session.execute(stmt)
            meta = result.scalar_one_or_none()
            if meta:
                for k, v in kwargs.items():
                    if hasattr(meta, k):
                        setattr(meta, k, v)
            else:
                meta = TeamStructureMeta(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    **kwargs,
                )
                session.add(meta)
            await session.commit()
            await session.refresh(meta)
            return meta

    # ── Agent definitions ─────────────────────────────────────────────

    async def get_agents(
        self,
        tenant_id: str = "default",
        project_id: str | None = None,
    ) -> list[TeamAgentDef]:
        async with self._session() as session:
            stmt = select(TeamAgentDef).where(
                TeamAgentDef.tenant_id == tenant_id,
                TeamAgentDef.project_id == project_id,
            ).order_by(TeamAgentDef.agent_id)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_merged_agents(
        self,
        tenant_id: str = "default",
        project_id: str | None = None,
    ) -> dict[str, TeamAgentDef]:
        """Get agent defs with project overrides merged over global defaults."""
        global_agents = await self.get_agents(tenant_id, None)
        by_id = {a.agent_id: a for a in global_agents}

        if project_id:
            project_agents = await self.get_agents(tenant_id, project_id)
            for pa in project_agents:
                by_id[pa.agent_id] = pa  # project override wins

        # Filter out disabled agents
        return {aid: a for aid, a in by_id.items() if a.enabled}

    async def upsert_agent(
        self,
        agent_data: dict,
        tenant_id: str = "default",
        project_id: str | None = None,
    ) -> TeamAgentDef:
        async with self._session() as session:
            agent_id = agent_data["agent_id"]
            stmt = select(TeamAgentDef).where(
                TeamAgentDef.tenant_id == tenant_id,
                TeamAgentDef.project_id == project_id,
                TeamAgentDef.agent_id == agent_id,
            )
            result = await session.execute(stmt)
            agent_def = result.scalar_one_or_none()

            if agent_def:
                for k, v in agent_data.items():
                    if k != "agent_id" and hasattr(agent_def, k):
                        setattr(agent_def, k, v)
            else:
                agent_def = TeamAgentDef(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    **agent_data,
                )
                session.add(agent_def)

            await session.commit()
            await session.refresh(agent_def)
            return agent_def

    async def delete_agent(
        self,
        agent_id: str,
        tenant_id: str = "default",
        project_id: str | None = None,
    ) -> bool:
        """Delete a team agent definition. Returns True if deleted."""
        async with self._session() as session:
            stmt = delete(TeamAgentDef).where(
                TeamAgentDef.tenant_id == tenant_id,
                TeamAgentDef.project_id == project_id,
                TeamAgentDef.agent_id == agent_id,
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    # ── Seeding from YAML ─────────────────────────────────────────────

    async def seed_from_yaml_if_empty(
        self,
        yaml_path: Path | None = None,
        tenant_id: str = "default",
    ) -> int:
        """Seed team structure from YAML if no global defs exist.

        Returns number of agent defs seeded.
        """
        existing = await self.get_agents(tenant_id, None)
        if existing:
            logger.info("Team structure already seeded, skipping")
            return 0

        if yaml_path is None:
            yaml_path = Path(__file__).parent.parent.parent / "team_structure.yaml"

        if not yaml_path.exists():
            logger.warning("No team_structure.yaml found at %s", yaml_path)
            return 0

        with open(yaml_path) as f:
            data = yaml.safe_load(f) or {}

        # Seed meta
        await self.upsert_meta(
            tenant_id=tenant_id,
            user_facing_agent=data.get("user_facing_agent", "manny"),
            privileged_agents=data.get("privileged_agents", ["manny", "jerry"]),
            checkpoint_agent=data.get("checkpoint_agent", "jerry"),
        )

        # Seed agent definitions
        agents = data.get("agents", {})
        count = 0
        for agent_id, agent_data in agents.items():
            await self.upsert_agent(
                agent_data={
                    "agent_id": agent_id,
                    "class_name": agent_data.get("class", "CustomAgent"),
                    "module_path": agent_data.get("module", "agents.custom_agent"),
                    "name": agent_data.get("name", agent_id),
                    "role": agent_data.get("role", ""),
                    "description": agent_data.get("description", ""),
                    "model": agent_data.get("model", "sonnet"),
                    "is_fixed": agent_data.get("is_fixed", False),
                    "needs_worktree": agent_data.get("needs_worktree", True),
                    "configure_extras": agent_data.get("configure_extras", []),
                    "routing_rules": agent_data.get("routing_rules", []),
                    "enabled": agent_data.get("enabled", True),
                },
                tenant_id=tenant_id,
            )
            count += 1

        logger.info("Seeded %d agent definitions from YAML", count)
        return count
