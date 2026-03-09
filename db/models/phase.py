"""Phase model — first-class project phase entity."""

from __future__ import annotations

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base, TimestampMixin, TenantMixin


class PhaseModel(Base, TimestampMixin, TenantMixin):
    __tablename__ = "phases"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="planning")
    ordering: Mapped[int] = mapped_column(Integer, default=0)
    planning_doc_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    review_doc_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
