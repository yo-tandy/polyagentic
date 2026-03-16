"""Agent template records — reusable agent configurations for the agent repository."""

from __future__ import annotations

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base, TimestampMixin, TenantMixin


class AgentTemplate(Base, TimestampMixin, TenantMixin):
    __tablename__ = "agent_templates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope: Mapped[str] = mapped_column(String(20), default="org", index=True)
    # "global" = visible to all orgs, "org" = scoped to tenant_id

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Human display name, e.g. "Freddy"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # Role / job title, e.g. "Senior Frontend Developer"

    personality: Mapped[str] = mapped_column(Text, default="")
    # System prompt personality description

    model: Mapped[str] = mapped_column(String(20), default="sonnet")
    allowed_tools: Mapped[str] = mapped_column(String(500), default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)

    # Links this template to a live agent for personality sync
    source_agent_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True,
    )
