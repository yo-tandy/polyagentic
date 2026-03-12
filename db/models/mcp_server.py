"""MCP server records — tracks discovered, proposed, and installed MCP servers."""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base, TimestampMixin, TenantMixin


class MCPServer(Base, TimestampMixin, TenantMixin):
    __tablename__ = "mcp_servers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    server_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    package_name: Mapped[str] = mapped_column(String(300), default="")
    command: Mapped[str] = mapped_column(String(100), default="npx")
    args: Mapped[list] = mapped_column(JSON, default=list)
    env_template: Mapped[dict] = mapped_column(JSON, default=dict)
    env_values: Mapped[dict] = mapped_column(JSON, default=dict)
    install_method: Mapped[str] = mapped_column(String(20), default="npx")

    # Lifecycle
    status: Mapped[str] = mapped_column(
        String(20), default="pending", index=True,
    )  # pending | searching | proposed | deploying | installed | failed | rejected

    # Relationships
    requested_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    managed_by: Mapped[str] = mapped_column(String(100), default="rory")
    requested_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_ids: Mapped[list] = mapped_column(JSON, default=list)

    # Scope
    project_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    container_installed: Mapped[bool] = mapped_column(Boolean, default=False)
