"""Agent role definitions — replaces per-agent Python files.

Each role defines: prompt content, tool permissions, session behaviour,
budget, dependencies, and allowed actions.  Agent instances reference
a role_id and inherit its configuration.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base, TimestampMixin, TenantMixin


class AgentRole(Base, TimestampMixin, TenantMixin):
    __tablename__ = "agent_roles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    role_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    prompt_content: Mapped[str] = mapped_column(Text, default="")
    allowed_tools: Mapped[str] = mapped_column(String(50), default="dev")
    use_session: Mapped[bool] = mapped_column(Boolean, default=True)
    stateless: Mapped[bool] = mapped_column(Boolean, default=False)
    max_task_context_items: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timeout: Mapped[int] = mapped_column(Integer, default=300)
    max_budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    deps: Mapped[list] = mapped_column(JSON, default=list)
    allowed_actions: Mapped[list] = mapped_column(JSON, default=list)
    provider: Mapped[str] = mapped_column(String(20), default="claude-cli")
    fallback_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
