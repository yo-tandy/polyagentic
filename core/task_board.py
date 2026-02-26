from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from core.task import Task, TaskStatus

logger = logging.getLogger(__name__)

PRIVILEGED_AGENTS = {"user", "dev_manager", "project_manager"}

VALID_TRANSITIONS = {
    TaskStatus.PENDING:     {TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED, TaskStatus.PAUSED},
    TaskStatus.IN_PROGRESS: {TaskStatus.REVIEW, TaskStatus.BLOCKED, TaskStatus.PAUSED, TaskStatus.PENDING},
    TaskStatus.REVIEW:      {TaskStatus.DONE, TaskStatus.IN_PROGRESS, TaskStatus.PENDING},
    TaskStatus.BLOCKED:     {TaskStatus.PENDING, TaskStatus.IN_PROGRESS},
    TaskStatus.PAUSED:      {TaskStatus.IN_PROGRESS, TaskStatus.PENDING},
    TaskStatus.DONE:        {TaskStatus.PENDING},  # reopen
}


class TaskBoard:
    def __init__(self, persistence_path: Path):
        self.persistence_path = persistence_path
        self._tasks: dict[str, Task] = {}
        self._on_update_callback = None
        self.load()

    def set_on_update(self, callback):
        """Set callback invoked after any task create/update. Signature: callback(task_id)."""
        self._on_update_callback = callback

    def _notify(self, task_id: str):
        if self._on_update_callback:
            self._on_update_callback(task_id)

    def create_task(self, title: str, description: str, created_by: str,
                    assignee: str | None = None, role: str | None = None,
                    parent_task_id: str | None = None,
                    priority: int = 3, labels: list[str] | None = None) -> Task:
        task = Task(
            title=title,
            description=description,
            created_by=created_by,
            assignee=assignee,
            role=role,
            priority=priority,
            labels=labels or [],
            parent_task_id=parent_task_id,
        )
        self._tasks[task.id] = task
        if parent_task_id and parent_task_id in self._tasks:
            self._tasks[parent_task_id].subtasks.append(task.id)
        self.save()
        self._notify(task.id)
        return task

    def update_task(self, task_id: str, **kwargs) -> Task | None:
        task = self._tasks.get(task_id)
        if not task:
            return None

        # Handle progress_note as an append operation
        note_text = kwargs.pop("progress_note", None)
        agent_id = kwargs.pop("_agent_id", "unknown")
        if note_text:
            task.progress_notes.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "agent": agent_id,
                "note": note_text,
            })

        # Status transition validation
        if "status" in kwargs:
            raw_status = kwargs["status"]
            if isinstance(raw_status, str):
                try:
                    new_status = TaskStatus(raw_status)
                except ValueError:
                    logger.warning(
                        "Invalid task status '%s' for task %s — ignoring status update",
                        raw_status, task_id,
                    )
                    kwargs.pop("status")
                    new_status = None
            else:
                new_status = raw_status

            if new_status is not None:
                allowed = VALID_TRANSITIONS.get(task.status, set())
                if new_status != task.status and new_status not in allowed:
                    if agent_id in PRIVILEGED_AGENTS:
                        logger.warning(
                            "Privileged override: %s -> %s for task %s by %s",
                            task.status.value, new_status.value, task_id, agent_id,
                        )
                    else:
                        logger.warning(
                            "Rejected invalid transition %s -> %s for task %s by %s",
                            task.status.value, new_status.value, task_id, agent_id,
                        )
                        return None
                kwargs["status"] = new_status

                # Default reviewer when moving to review
                if new_status == TaskStatus.REVIEW and not kwargs.get("reviewer") and not task.reviewer:
                    kwargs["reviewer"] = "project_manager"

        for key, value in kwargs.items():
            if hasattr(task, key):
                setattr(task, key, value)
        task.touch()
        self.save()
        self._notify(task_id)
        return task

    def get_task(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def get_all_tasks(self) -> list[Task]:
        return list(self._tasks.values())

    def get_tasks_by_status(self, status: TaskStatus) -> list[Task]:
        return [t for t in self._tasks.values() if t.status == status]

    def get_tasks_by_assignee(self, agent_id: str) -> list[Task]:
        return [t for t in self._tasks.values() if t.assignee == agent_id]

    def get_tasks_for_agent(self, agent_id: str, agent_role: str | None = None) -> list[Task]:
        """Return agent's tasks ordered: review-for-me first, then by priority, then created_at.

        Also includes unassigned pending tasks that match agent_role.
        """
        tasks = []
        for t in self._tasks.values():
            if t.assignee == agent_id or t.reviewer == agent_id:
                tasks.append(t)
            elif (agent_role and t.role and t.role == agent_role
                  and t.assignee is None and t.status == TaskStatus.PENDING):
                tasks.append(t)

        def sort_key(t):
            is_review_for_me = (t.status == TaskStatus.REVIEW and t.reviewer == agent_id)
            status_order = 0 if is_review_for_me else 1
            return (status_order, t.priority, t.created_at)

        return sorted(tasks, key=sort_key)

    def save(self):
        data = {tid: t.to_dict() for tid, t in self._tasks.items()}
        self.persistence_path.write_text(json.dumps(data, indent=2))

    def load(self):
        if self.persistence_path.exists():
            try:
                data = json.loads(self.persistence_path.read_text())
                self._tasks = {tid: Task.from_dict(td) for tid, td in data.items()}
            except (json.JSONDecodeError, OSError):
                self._tasks = {}

    def to_summary(self) -> list[dict]:
        return [t.to_dict() for t in self._tasks.values()]
