from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


@router.get("/tasks")
async def get_tasks(request: Request):
    task_board = request.app.state.task_board
    return {"tasks": task_board.to_summary()}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, request: Request):
    task_board = request.app.state.task_board
    task = task_board.get_task(task_id)
    if task is None:
        return {"error": "Task not found"}
    return task.to_dict()


class TaskUpdateRequest(BaseModel):
    status: str | None = None
    priority: int | None = None
    assignee: str | None = None
    reviewer: str | None = None
    labels: list[str] | None = None
    outcome: str | None = None
    role: str | None = None


@router.patch("/tasks/{task_id}")
async def update_task(task_id: str, body: TaskUpdateRequest, request: Request):
    task_board = request.app.state.task_board
    task = task_board.get_task(task_id)
    if task is None:
        return {"error": "Task not found"}

    updates = {"_agent_id": "user"}  # privileged caller
    if body.status is not None:
        updates["status"] = body.status
    if body.priority is not None:
        updates["priority"] = body.priority
    if body.assignee is not None:
        updates["assignee"] = body.assignee
    if body.reviewer is not None:
        updates["reviewer"] = body.reviewer
    if body.labels is not None:
        updates["labels"] = body.labels
    if body.outcome is not None:
        updates["outcome"] = body.outcome
    if body.role is not None:
        updates["role"] = body.role

    result = await task_board.update_task(task_id, **updates)
    if result is None:
        return {"error": "Update failed (invalid transition?)"}
    return result.to_dict()
