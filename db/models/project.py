"""Project and custom agent definition models."""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.models.base import Base, TimestampMixin, TenantMixin


class Project(Base, TimestampMixin, TenantMixin):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="active")
    main_branch: Mapped[str] = mapped_column(String(100), default="main")
    github_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    is_running: Mapped[bool] = mapped_column(Boolean, default=False)

    custom_agents: Mapped[list[CustomAgentDef]] = relationship(
        back_populates="project", cascade="all, delete-orphan",
    )


class CustomAgentDef(Base, TenantMixin):
    __tablename__ = "custom_agent_defs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("projects.id"), nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(200), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(50), default="sonnet")
    allowed_tools: Mapped[str] = mapped_column(
        String(500), default="Bash,Edit,Write,Read,Glob,Grep",
    )

    project: Mapped[Project] = relationship(back_populates="custom_agents")
