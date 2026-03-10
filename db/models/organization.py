"""Organization model."""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.models.base import Base, TimestampMixin


class Organization(Base, TimestampMixin):
    """An organization that owns projects and contains users."""

    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # Relationships
    users: Mapped[list["User"]] = relationship(  # noqa: F821
        back_populates="organization", lazy="selectin",
    )
