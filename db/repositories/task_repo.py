"""Task repository — CRUD for tasks and progress notes."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, delete

from db.models.task import TaskModel, TaskProgressNote
from db.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class TaskRepository(BaseRepository):

    async def create(self, project_id: str, **kwargs: Any) -> TaskModel:
        async with self._session() as session:
            task = TaskModel(project_id=project_id, **kwargs)
            session.add(task)
            await session.commit()
            await session.refresh(task)
            return task

    async def get(self, task_id: str) -> TaskModel | None:
        async with self._session() as session:
            return await session.get(TaskModel, task_id)

    async def get_all(self, project_id: str) -> list[TaskModel]:
        async with self._session() as session:
            stmt = select(TaskModel).where(
                TaskModel.project_id == project_id,
            ).order_by(TaskModel.priority, TaskModel.created_at)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_by_status(
        self, project_id: str, status: str,
    ) -> list[TaskModel]:
        async with self._session() as session:
            stmt = select(TaskModel).where(
                TaskModel.project_id == project_id,
                TaskModel.status == status,
            ).order_by(TaskModel.priority)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_by_assignee(
        self, project_id: str, agent_id: str,
    ) -> list[TaskModel]:
        async with self._session() as session:
            stmt = select(TaskModel).where(
                TaskModel.project_id == project_id,
                TaskModel.assignee == agent_id,
            ).order_by(TaskModel.priority)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_for_agent(
        self, project_id: str, agent_id: str, agent_role: str | None = None,
    ) -> list[TaskModel]:
        """Get tasks assigned to agent, where agent is reviewer, or
        unassigned tasks matching the agent's role."""
        async with self._session() as session:
            # Tasks assigned to this agent
            stmt1 = select(TaskModel).where(
                TaskModel.project_id == project_id,
                TaskModel.assignee == agent_id,
            )
            result1 = await session.execute(stmt1)
            my_tasks = list(result1.scalars().all())

            # Tasks where agent is reviewer
            stmt2 = select(TaskModel).where(
                TaskModel.project_id == project_id,
                TaskModel.reviewer == agent_id,
            )
            result2 = await session.execute(stmt2)
            review_tasks = list(result2.scalars().all())

            # Unassigned tasks matching role
            role_tasks: list[TaskModel] = []
            if agent_role:
                stmt3 = select(TaskModel).where(
                    TaskModel.project_id == project_id,
                    TaskModel.assignee == None,  # noqa: E711
                    TaskModel.role == agent_role,
                    TaskModel.status == "pending",
                )
                result3 = await session.execute(stmt3)
                role_tasks = list(result3.scalars().all())

            # Deduplicate and sort: review tasks first, then by priority
            seen = set()
            combined: list[TaskModel] = []
            for t in review_tasks + my_tasks + role_tasks:
                if t.id not in seen:
                    seen.add(t.id)
                    combined.append(t)
            return combined

    async def update(self, task_id: str, **kwargs: Any) -> TaskModel | None:
        """Update task fields.  Handles progress_note specially."""
        async with self._session() as session:
            task = await session.get(TaskModel, task_id)
            if not task:
                return None

            progress_note = kwargs.pop("progress_note", None)
            agent_id = kwargs.pop("_agent_id", "system")

            for k, v in kwargs.items():
                if hasattr(task, k):
                    setattr(task, k, v)

            if progress_note:
                note = TaskProgressNote(
                    task_id=task_id,
                    agent_id=agent_id,
                    note=progress_note,
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
                session.add(note)

            await session.commit()
            await session.refresh(task)
            return task

    async def add_progress_note(
        self, task_id: str, agent_id: str, note: str,
    ) -> None:
        async with self._session() as session:
            pn = TaskProgressNote(
                task_id=task_id,
                agent_id=agent_id,
                note=note,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            session.add(pn)
            await session.commit()

    async def get_progress_notes(
        self, task_id: str,
    ) -> list[TaskProgressNote]:
        async with self._session() as session:
            stmt = select(TaskProgressNote).where(
                TaskProgressNote.task_id == task_id,
            ).order_by(TaskProgressNote.created_at)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def delete(self, task_id: str) -> bool:
        async with self._session() as session:
            stmt = delete(TaskModel).where(TaskModel.id == task_id)
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0
