"""Role repository — CRUD + seeding for agent_roles table."""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field

from sqlalchemy import select, delete, func

from db.models.role import AgentRole
from db.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


# ── Dataclass for passing role data around ──────────────────────────

@dataclass
class RoleDefinition:
    """Plain data container loaded from the agent_roles DB table."""

    role_id: str
    prompt_content: str = ""
    allowed_tools: str = "dev"
    use_session: bool = True
    stateless: bool = False
    max_task_context_items: int | None = 20
    timeout: int = 300
    max_budget_usd: float | None = None
    deps: list[str] = field(default_factory=list)
    allowed_actions: list[str] = field(default_factory=list)
    provider: str = "claude-cli"
    fallback_provider: str | None = None


# ── Universal actions available to every role ───────────────────────

UNIVERSAL_ACTIONS = [
    "respond_to_user",
    "delegate",
    "update_task",
    "update_memory",
    "write_document",
    "update_document",
    "read_document",
    "resolve_comments",
    "start_conversation",
    "end_conversation",
]

# ── Seed data ───────────────────────────────────────────────────────

DEFAULT_ROLE_SEEDS: list[dict] = [
    {
        "role_id": "manager",
        "prompt_source": "manny",
        "allowed_tools": "none",
        "use_session": False,
        "stateless": True,
        "max_task_context_items": None,
        "timeout": 120,
        "max_budget_usd": 0.25,
        "deps": ["registry"],
        "allowed_actions": UNIVERSAL_ACTIONS + ["pause_task", "start_task"],
    },
    {
        "role_id": "project_manager",
        "prompt_source": "jerry",
        "allowed_tools": "readonly",
        "use_session": True,
        "stateless": False,
        "max_task_context_items": None,
        "timeout": 300,
        "max_budget_usd": None,
        "deps": [],
        "allowed_actions": UNIVERSAL_ACTIONS + [
            "assign_ticket", "create_batch_tickets",
            "create_phase", "update_phase",
        ],
    },
    {
        "role_id": "product_manager",
        "prompt_source": "perry",
        "allowed_tools": "readonly",
        "use_session": True,
        "stateless": False,
        "max_task_context_items": 20,
        "timeout": 300,
        "max_budget_usd": None,
        "deps": [],
        "allowed_actions": UNIVERSAL_ACTIONS,
    },
    {
        "role_id": "integrator",
        "prompt_source": "innes",
        "allowed_tools": "dev",
        "use_session": True,
        "stateless": False,
        "max_task_context_items": 20,
        "timeout": 300,
        "max_budget_usd": None,
        "deps": ["git_manager", "project_store"],
        "allowed_actions": UNIVERSAL_ACTIONS + [
            "create_repo", "review_pr", "merge_pr", "request_changes",
            "request_capability",
        ],
    },
    {
        "role_id": "robot_resources",
        "prompt_source": "rory",
        "allowed_tools": "dev",
        "use_session": True,
        "stateless": False,
        "max_task_context_items": 20,
        "timeout": 300,
        "max_budget_usd": None,
        "deps": [
            "registry", "git_manager", "session_store",
            "workspace_path", "messages_dir", "worktrees_dir",
            "container_manager", "project_store", "team_structure",
            "mcp_manager", "mcp_registry", "template_repo",
        ],
        "allowed_actions": UNIVERSAL_ACTIONS + [
            "recruit_agent", "search_agent_repository",
            "search_mcp_registry", "deploy_mcp",
        ],
    },
    {
        "role_id": "engineer",
        "prompt_source": "engineer",
        "allowed_tools": "dev",
        "use_session": True,
        "stateless": False,
        "max_task_context_items": 20,
        "timeout": 300,
        "max_budget_usd": None,
        "deps": ["template_repo"],
        "allowed_actions": UNIVERSAL_ACTIONS + ["request_capability"],
    },
]


# ── Repository ──────────────────────────────────────────────────────

class RoleRepository(BaseRepository):
    """CRUD for the agent_roles table."""

    # ── Queries ────────────────────────────────────────────────────

    async def get_all(self, tenant_id: str = "default") -> list[RoleDefinition]:
        """Return every role as a RoleDefinition."""
        async with self._session() as session:
            stmt = select(AgentRole).where(AgentRole.tenant_id == tenant_id)
            result = await session.execute(stmt)
            return [self._to_dataclass(row) for row in result.scalars().all()]

    async def get(self, role_id: str, tenant_id: str = "default") -> RoleDefinition | None:
        """Return a single role, or None."""
        async with self._session() as session:
            stmt = select(AgentRole).where(
                AgentRole.tenant_id == tenant_id,
                AgentRole.role_id == role_id,
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return self._to_dataclass(row) if row else None

    async def get_all_as_dict(self, tenant_id: str = "default") -> dict[str, RoleDefinition]:
        """Return all roles keyed by role_id."""
        roles = await self.get_all(tenant_id)
        return {r.role_id: r for r in roles}

    # ── Mutations ──────────────────────────────────────────────────

    async def upsert(self, role_id: str, data: dict, tenant_id: str = "default") -> None:
        """Insert or update a role."""
        async with self._session() as session:
            stmt = select(AgentRole).where(
                AgentRole.tenant_id == tenant_id,
                AgentRole.role_id == role_id,
            )
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()
            if existing:
                for k, v in data.items():
                    if hasattr(existing, k):
                        setattr(existing, k, v)
            else:
                session.add(AgentRole(tenant_id=tenant_id, role_id=role_id, **data))
            await session.commit()

    async def delete(self, role_id: str, tenant_id: str = "default") -> bool:
        """Delete a role. Returns True if a row was deleted."""
        async with self._session() as session:
            stmt = delete(AgentRole).where(
                AgentRole.tenant_id == tenant_id,
                AgentRole.role_id == role_id,
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    # ── Seeding ────────────────────────────────────────────────────

    async def seed_defaults_if_empty(self, tenant_id: str = "default") -> int:
        """Seed default roles if the table is empty.

        Uses the existing prompt_loader to compose prompt content from
        the .md inheritance chain at seed time.
        """
        async with self._session() as session:
            stmt = select(func.count()).select_from(AgentRole).where(
                AgentRole.tenant_id == tenant_id,
            )
            result = await session.execute(stmt)
            count = result.scalar() or 0
            if count > 0:
                logger.info("Roles table already seeded (%d rows), skipping", count)
                return 0

        # Import here so prompt_loader is only needed at seed time
        from core.prompt_loader import load_prompt

        inserted = 0
        for seed in DEFAULT_ROLE_SEEDS:
            seed = copy.deepcopy(seed)
            prompt_source = seed.pop("prompt_source")
            try:
                prompt_content = load_prompt(prompt_source)
            except FileNotFoundError:
                logger.warning(
                    "Prompt file for role '%s' (source: %s) not found, using empty",
                    seed["role_id"], prompt_source,
                )
                prompt_content = ""
            seed["prompt_content"] = prompt_content
            await self.upsert(seed.pop("role_id"), seed, tenant_id)
            inserted += 1

        logger.info("Seeded %d default roles", inserted)
        return inserted

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _to_dataclass(row: AgentRole) -> RoleDefinition:
        return RoleDefinition(
            role_id=row.role_id,
            prompt_content=row.prompt_content or "",
            allowed_tools=row.allowed_tools or "dev",
            use_session=row.use_session,
            stateless=row.stateless,
            max_task_context_items=row.max_task_context_items,
            timeout=row.timeout or 300,
            max_budget_usd=row.max_budget_usd,
            deps=row.deps or [],
            allowed_actions=row.allowed_actions or [],
            provider=row.provider or "claude-cli",
            fallback_provider=row.fallback_provider,
        )
