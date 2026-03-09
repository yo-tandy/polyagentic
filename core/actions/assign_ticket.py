"""Create and assign a task ticket (Jerry-specific)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext
from core.message import Message, MessageType
from core.task import TaskStatus

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
        ActionField("category", "string",
                     description="Task category: operational or project",
                     default="operational",
                     enum=["operational", "project"]),
        ActionField("phase_id", "string",
                     description="Phase ID for project tasks"),
        ActionField("initial_status", "string",
                     description="Initial status: draft (unassigned) or pending",
                     enum=["draft", "pending"]),
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
        category = action.get("category", "operational")
        phase_id = action.get("phase_id")
        initial_status_str = action.get("initial_status")
        initial_status = TaskStatus(initial_status_str) if initial_status_str else None

        task_id = None
        if agent._task_board:
            task = await agent._task_board.create_task(
                title=title,
                description=desc,
                created_by=agent.agent_id,
                assignee=to if not initial_status or initial_status != TaskStatus.DRAFT else None,
                priority=priority,
                labels=labels,
                category=category,
                phase_id=phase_id,
                initial_status=initial_status,
            )
            task_id = task.id

        # Don't send message for draft tickets (they're not actionable yet)
        if to and (not initial_status or initial_status != TaskStatus.DRAFT):
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
