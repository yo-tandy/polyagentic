"""Invite link model for organization enrollment."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base, TimestampMixin


class InviteLink(Base, TimestampMixin):
    """A shareable link that allows new users to join an organization."""

    __tablename__ = "invite_links"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id"), nullable=False, index=True,
    )
    created_by_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id"), nullable=False,
    )
    token: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False, index=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
