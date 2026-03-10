"""Team structure models — replaces team_structure.yaml."""

from __future__ import annotations

from sqlalchemy import Boolean, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base, TimestampMixin, TenantMixin


class TeamAgentDef(Base, TimestampMixin, TenantMixin):
    __tablename__ = "team_agent_defs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_id: Mapped[str] = mapped_column(String(100), nullable=False)
    role_id: Mapped[str] = mapped_column(String(100), nullable=True)  # references agent_roles.role_id
    class_name: Mapped[str] = mapped_column(String(100), nullable=True)  # legacy, nullable now
    module_path: Mapped[str] = mapped_column(String(200), nullable=True)  # legacy, nullable now
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(50), default="sonnet")
    is_fixed: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_worktree: Mapped[bool] = mapped_column(Boolean, default=True)
    configure_extras: Mapped[list] = mapped_column(JSON, default=list)
    routing_rules: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    prompt_append: Mapped[str] = mapped_column(Text, default="")  # project-specific prompt additions
    allowed_actions: Mapped[list | None] = mapped_column(JSON, nullable=True)  # null = use role default
    provider: Mapped[str] = mapped_column(String(20), default="claude-cli")
    fallback_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)


class TeamStructureMeta(Base, TenantMixin):
    __tablename__ = "team_structure_meta"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_facing_agent: Mapped[str] = mapped_column(String(100), default="manny")
    privileged_agents: Mapped[list] = mapped_column(
        JSON, default=lambda: ["manny", "jerry"],
    )
    checkpoint_agent: Mapped[str] = mapped_column(String(100), default="jerry")
