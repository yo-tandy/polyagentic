"""Update an existing task on the board."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext
from core.message import Message

if TYPE_CHECKING:
    from core.agent import Agent


class UpdateTask(BaseAction):

    name = "update_task"
    description = "Update an existing task on the board."
    produces_messages = False

    fields = [
        ActionField("task_id", "string", required=True,
                     description="The task ID to update"),
        ActionField("status", "string",
                     description="New status",
                     enum=["draft", "pending", "in_progress", "review",
                           "done", "paused"]),
        ActionField("progress_note", "string",
                     description="Brief status update"),
        ActionField("completion_summary", "string",
                     description="Summary when marking done"),
        ActionField("reviewer", "string",
                     description="Agent ID to review"),
        ActionField("paused_summary", "string",
                     description="State snapshot when pausing"),
        ActionField("labels", "array",
                     description="Tags for the task"),
        ActionField("outcome", "string",
                     description="Review outcome",
                     enum=["approved", "rejected", "complete"]),
    ]

    async def execute(
        self, agent: Agent, action: dict, original_msg: Message,
        ctx: ActionContext,
    ) -> list[Message]:
        if not agent._task_board:
            return []
        task_id = action.get("task_id")
        if not task_id:
            return []
        updates: dict[str, Any] = {"_agent_id": agent.agent_id}
        for key in (
            "status", "assignee", "role", "priority", "reviewer",
            "progress_note", "completion_summary", "review_output",
            "paused_summary", "labels", "outcome", "category", "phase_id",
        ):
            if key in action:
                updates[key] = action[key]
        await agent._task_board.update_task(task_id, **updates)
        return []
