"""User model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.models.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    """A user authenticated via Google OAuth."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    picture_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    google_sub: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True,
    )
    org_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("organizations.id"), nullable=False, index=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(  # noqa: F821
        back_populates="users",
    )
