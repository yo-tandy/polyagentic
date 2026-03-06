"""Create and assign a task ticket (Jerry-specific)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext
from core.message import Message, MessageType

if TYPE_CHECKING:
    from core.agent import Agent


class AssignTicket(BaseAction):

    name = "assign_ticket"
    description = "Create and assign a task ticket."
    fields = [
        ActionField("to", "string", required=True,
                     description="Assignee agent ID"),
        ActionField("task_description", "string", required=True,
                     description="Detailed description"),
        ActionField("task_title", "string",
                     description="Short title"),
        ActionField("priority", "integer",
                     description="Priority 1-5", default=3),
        ActionField("labels", "array", description="Tags"),
    ]

    async def execute(
        self, agent: Agent, action: dict, original_msg: Message,
        ctx: ActionContext,
    ) -> list[Message]:
        to = action.get("to", "")
        title = action.get("task_title", "Task")
        desc = action.get("task_description", "")
        priority = action.get("priority", 3)
        labels = action.get("labels", [])

        task_id = None
        if agent._task_board:
            task = await agent._task_board.create_task(
                title=title,
                description=desc,
                created_by=agent.agent_id,
                assignee=to,
                priority=priority,
                labels=labels,
            )
            task_id = task.id

        if to:
            return [Message(
                sender=agent.agent_id,
                recipient=to,
                type=MessageType.TASK,
                content=desc,
                task_id=task_id,
                parent_message_id=original_msg.id,
                metadata={"task_title": title},
            )]
        return []
