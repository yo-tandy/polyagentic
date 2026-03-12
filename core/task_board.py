"""Task board — DB-backed with in-memory cache.

All read methods are sync (cache-based) for hot-path performance.
All write methods are async (hit DB + refresh cache).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.task import Task, TaskStatus
from db.repositories.task_repo import TaskRepository

logger = logging.getLogger(__name__)

PRIVILEGED_AGENTS = {"user", "manny", "jerry"}

VALID_TRANSITIONS = {
    TaskStatus.DRAFT:       {TaskStatus.PENDING, TaskStatus.IN_PROGRESS},
    TaskStatus.PENDING:     {TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED, TaskStatus.PAUSED, TaskStatus.DRAFT},
    TaskStatus.IN_PROGRESS: {TaskStatus.REVIEW, TaskStatus.DONE, TaskStatus.BLOCKED, TaskStatus.PAUSED, TaskStatus.PENDING},
    TaskStatus.REVIEW:      {TaskStatus.DONE, TaskStatus.IN_PROGRESS, TaskStatus.PENDING},
    TaskStatus.BLOCKED:     {TaskStatus.PENDING, TaskStatus.IN_PROGRESS},
    TaskStatus.PAUSED:      {TaskStatus.IN_PROGRESS, TaskStatus.PENDING},
    TaskStatus.DONE:        {TaskStatus.PENDING},  # reopen
}


class TaskBoard:
    def __init__(self, repo: TaskRepository, project_id: str):
        self._repo = repo
        self._project_id = project_id
        self._tasks: dict[str, Task] = {}
        self._on_update_callback = None
        self._privileged_agents: set[str] = set(PRIVILEGED_AGENTS)

    async def load(self) -> None:
        """Populate in-memory cache from DB at startup."""
        records = await self._repo.get_all(self._project_id)
        self._tasks = {}
        for rec in records:
            task = self._db_to_task(rec)
            self._tasks[task.id] = task
        logger.info("Loaded %d tasks from DB", len(self._tasks))

    def set_on_update(self, callback):
        """Set callback invoked after any task create/update. Signature: callback(task_id)."""
        self._on_update_callback = callback

    def set_privileged_agents(self, agents: set[str]):
        """Override the set of privileged agents (from team structure)."""
        self._privileged_agents = agents

    def _notify(self, task_id: str):
        if self._on_update_callback:
            self._on_update_callback(task_id)

    async def create_task(self, title: str, description: str, created_by: str,
                          assignee: str | None = None, role: str | None = None,
                          parent_task_id: str | None = None,
                          priority: int = 3, labels: list[str] | None = None,
                          category: str = "operational",
                          phase_id: str | None = None,
                          initial_status: TaskStatus | None = None) -> Task:
        status = initial_status or TaskStatus.PENDING
        task = Task(
            title=title,
            description=description,
            created_by=created_by,
            status=status,
            assignee=assignee,
            role=role,
            priority=priority,
            labels=labels or [],
            parent_task_id=parent_task_id,
            category=category,
            phase_id=phase_id,
        )
        # Write to DB
        await self._repo.create(
            project_id=self._project_id,
            id=task.id,
            title=task.title,
            description=task.description,
            status=task.status.value,
            assignee=task.assignee,
            reviewer=task.reviewer,
            role=task.role,
            priority=task.priority,
            labels=task.labels,
            parent_task_id=task.parent_task_id,
            created_by=task.created_by,
            category=task.category,
            phase_id=task.phase_id,
        )
        # Update cache
        self._tasks[task.id] = task
        if parent_task_id and parent_task_id in self._tasks:
            self._tasks[parent_task_id].subtasks.append(task.id)
        self._notify(task.id)
        return task

    async def update_task(self, task_id: str, **kwargs) -> Task | None:
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
                    if agent_id in self._privileged_agents:
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
                    kwargs["reviewer"] = "jerry"

                # Default outcome when moving to done without one
                if new_status == TaskStatus.DONE and not kwargs.get("outcome") and not task.outcome:
                    kwargs["outcome"] = "complete"

                # Clear outcome when re-opening (leaving done)
                if task.status == TaskStatus.DONE and new_status != TaskStatus.DONE:
                    kwargs.setdefault("outcome", None)

        for key, value in kwargs.items():
            if hasattr(task, key):
                setattr(task, key, value)
        task.touch()

        # Write to DB
        db_kwargs = {}
        for key, value in kwargs.items():
            if key == "status" and isinstance(value, TaskStatus):
                db_kwargs["status"] = value.value
            elif hasattr(task, key):
                db_kwargs[key] = value
        if note_text:
            db_kwargs["progress_note"] = note_text
            db_kwargs["_agent_id"] = agent_id
        await self._repo.update(task_id, **db_kwargs)

        self._notify(task_id)
        return task

    async def delete_task(self, task_id: str) -> bool:
        """Delete a task from DB and cache."""
        task = self._tasks.get(task_id)
        if not task:
            return False
        result = await self._repo.delete(task_id)
        if result:
            del self._tasks[task_id]
            if task.parent_task_id and task.parent_task_id in self._tasks:
                parent = self._tasks[task.parent_task_id]
                parent.subtasks = [s for s in parent.subtasks if s != task_id]
            self._notify(task_id)
        return result

    def list_tasks(self) -> list[Task]:
        """Alias for get_all_tasks (used by activation check)."""
        return list(self._tasks.values())

    # ── Reads (sync, from cache) ─────────────────────────────────

    def get_task(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def get_all_tasks(self) -> list[Task]:
        return list(self._tasks.values())

    def get_tasks_by_status(self, status: TaskStatus) -> list[Task]:
        return [t for t in self._tasks.values() if t.status == status]

    def get_tasks_by_assignee(self, agent_id: str) -> list[Task]:
        return [t for t in self._tasks.values() if t.assignee == agent_id]

    def get_tasks_for_agent(self, agent_id: str, agent_role: str | None = None) -> list[Task]:
        """Return agent's tasks ordered: review-for-me first, then by priority, then created_at."""
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

    def get_tasks_by_phase(self, phase_id: str) -> list[Task]:
        return [t for t in self._tasks.values() if t.phase_id == phase_id]

    def get_tasks_by_category(self, category: str) -> list[Task]:
        return [t for t in self._tasks.values() if t.category == category]

    def is_phase_complete(self, phase_id: str) -> bool:
        phase_tasks = self.get_tasks_by_phase(phase_id)
        return len(phase_tasks) > 0 and all(t.status == TaskStatus.DONE for t in phase_tasks)

    def to_summary(self) -> list[dict]:
        return [t.to_dict() for t in self._tasks.values()]

    # ── DB → Task conversion ────────────────────────────────────

    @staticmethod
    def _db_to_task(rec) -> Task:
        """Convert a TaskModel ORM object to a core.task.Task."""
        # Preserve DB timestamps (datetime → ISO string)
        created = getattr(rec, "created_at", None)
        updated = getattr(rec, "updated_at", None)
        created_str = created.isoformat() if created else datetime.now(timezone.utc).isoformat()
        updated_str = updated.isoformat() if updated else created_str

        # Convert progress notes from ORM relationship to dicts
        notes = []
        for pn in getattr(rec, "progress_notes", None) or []:
            notes.append({
                "timestamp": pn.created_at,
                "agent": pn.agent_id,
                "note": pn.note,
            })

        return Task(
            id=rec.id,
            title=rec.title,
            description=rec.description or "",
            created_by=rec.created_by or "unknown",
            assignee=rec.assignee,
            role=rec.role,
            priority=rec.priority or 3,
            labels=rec.labels or [],
            parent_task_id=rec.parent_task_id,
            status=TaskStatus(rec.status) if rec.status else TaskStatus.PENDING,
            reviewer=rec.reviewer,
            category=getattr(rec, "category", None) or "operational",
            phase_id=getattr(rec, "phase_id", None),
            created_at=created_str,
            updated_at=updated_str,
            branch=rec.branch,
            paused_summary=rec.paused_summary,
            outcome=rec.outcome,
            completion_summary=rec.completion_summary,
            review_output=rec.review_output,
            progress_notes=notes,
            subtasks=rec.subtasks or [],
            messages=rec.messages or [],
        )
