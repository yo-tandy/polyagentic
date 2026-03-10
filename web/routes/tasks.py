from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


def _get_user(request: Request) -> dict:
    return getattr(request.state, "user", {})


@router.get("/tasks")
async def get_tasks(
    request: Request,
    category: str | None = None,
    phase_id: str | None = None,
):
    task_board = request.app.state.task_board
    tasks = task_board.to_summary()
    if category:
        tasks = [t for t in tasks if t.get("category") == category]
    if phase_id:
        tasks = [t for t in tasks if t.get("phase_id") == phase_id]
    return {"tasks": tasks}


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

    user = _get_user(request)
    updates = {"_agent_id": user.get("id", "user")}  # privileged caller
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


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str, request: Request):
    task_board = request.app.state.task_board
    task = task_board.get_task(task_id)
    if task is None:
        return {"error": "Task not found"}
    result = await task_board.delete_task(task_id)
    if not result:
        return {"error": "Delete failed"}
    return {"deleted": task_id}
