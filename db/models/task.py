"""Task and progress note models."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, JSON, String, Text, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.models.base import Base, TimestampMixin, TenantMixin


class TaskModel(Base, TimestampMixin, TenantMixin):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    assignee: Mapped[str | None] = mapped_column(String(100), nullable=True)
    role: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reviewer: Mapped[str | None] = mapped_column(String(100), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=3)
    labels: Mapped[list] = mapped_column(JSON, default=list)
    branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    parent_task_id: Mapped[str | None] = mapped_column(
        String(20), ForeignKey("tasks.id"), nullable=True,
    )
    category: Mapped[str] = mapped_column(String(20), default="operational")
    phase_id: Mapped[str | None] = mapped_column(
        String(20), ForeignKey("phases.id"), nullable=True, index=True,
    )
    subtasks: Mapped[list] = mapped_column(JSON, default=list)
    messages: Mapped[list] = mapped_column(JSON, default=list)
    paused_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(50), nullable=True)
    completion_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_output: Mapped[str | None] = mapped_column(Text, nullable=True)

    progress_notes: Mapped[list[TaskProgressNote]] = relationship(
        back_populates="task", cascade="all, delete-orphan",
        order_by="TaskProgressNote.created_at",
    )


class TaskProgressNote(Base):
    __tablename__ = "task_progress_notes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("tasks.id"), nullable=False, index=True,
    )
    agent_id: Mapped[str] = mapped_column(String(100), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(50), nullable=False)

    task: Mapped[TaskModel] = relationship(back_populates="progress_notes")
