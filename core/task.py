from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum

from core.constants import gen_id


class TaskStatus(str, Enum):
    DRAFT = "draft"
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    BLOCKED = "blocked"
    PAUSED = "paused"
    DONE = "done"
    CANCELLED = "cancelled"


@dataclass
class Task:
    title: str
    description: str
    created_by: str
    id: str = field(default_factory=lambda: gen_id("task-", 8))
    status: TaskStatus = TaskStatus.PENDING
    assignee: str | None = None
    role: str | None = None  # target role for unassigned tasks (e.g. "backend_developer")
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    priority: int = 3  # 1=critical, 2=high, 3=medium, 4=low, 5=backlog
    estimate: int | None = None  # story points: 1, 2, 3, 5, 8, 13
    labels: list[str] = field(default_factory=list)  # e.g. ["phase-1", "documentation"]
    reviewer: str | None = None
    paused_summary: str | None = None
    outcome: str | None = None  # "approved", "rejected", "complete" — set when task moves to done
    category: str = "operational"
    phase_id: str | None = None
    branch: str | None = None
    parent_task_id: str | None = None
    subtasks: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    progress_notes: list[dict] = field(default_factory=list)
    completion_summary: str | None = None
    review_output: str | None = None
    started_at: str | None = None    # set when task moves to in_progress
    completed_at: str | None = None  # set when task moves to done

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> Task:
        data = data.copy()
        data["status"] = TaskStatus(data["status"])
        data.setdefault("priority", 3)
        data.setdefault("category", "operational")
        data.setdefault("phase_id", None)
        data.setdefault("role", None)
        data.setdefault("labels", [])
        data.setdefault("reviewer", None)
        data.setdefault("paused_summary", None)
        data.setdefault("estimate", None)
        data.setdefault("outcome", None)
        data.setdefault("progress_notes", [])
        data.setdefault("completion_summary", None)
        data.setdefault("review_output", None)
        data.setdefault("started_at", None)
        data.setdefault("completed_at", None)
        return cls(**data)

    def touch(self):
        self.updated_at = datetime.now(timezone.utc).isoformat()
