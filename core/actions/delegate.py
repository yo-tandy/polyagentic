"""Delegate a task to another team member."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext
from core.message import Message, MessageType

if TYPE_CHECKING:
    from core.agent import Agent


class Delegate(BaseAction):

    name = "delegate"
    description = "Delegate a task to another team member."
    fields = [
        ActionField("to", "string", required=True,
                     description="Target agent ID"),
        ActionField("task_description", "string", required=True,
                     description="Detailed description with acceptance criteria"),
        ActionField("task_title", "string",
                     description="Short title for the task"),
        ActionField("priority", "integer",
                     description="Priority 1-5 (1=critical)", default=3),
        ActionField("labels", "array",
                     description="Tags for the task"),
        ActionField("role", "string",
                     description="Target role if agent ID unknown"),
        ActionField("category", "string",
                     description="Task category: operational or project",
                     default="operational",
                     enum=["operational", "project"]),
        ActionField("phase_id", "string",
                     description="Phase ID for project tasks"),
    ]

    example = {
        "action": "delegate",
        "to": "agent_id",
        "task_title": "Implement feature",
        "task_description": "Build the login page...",
        "priority": 3,
    }

    async def execute(
        self, agent: Agent, action: dict, original_msg: Message,
        ctx: ActionContext,
    ) -> list[Message]:
        to = action.get("to", "")
        title = action.get("task_title", "Task")
        desc = action.get("task_description", "")
        priority = action.get("priority", 3)
        labels = action.get("labels", [])
        role = action.get("role", None)
        category = action.get("category", "operational")
        phase_id = action.get("phase_id")

        # Smart routing: agents with _registry check if target exists
        registry = agent.deps.get("registry")
        if registry:
            is_known_agent = bool(registry.get(to))
        else:
            is_known_agent = True  # No registry → send directly

        task_id = None
        if agent._task_board:
            task = await agent._task_board.create_task(
                title=title,
                description=desc,
                created_by=agent.agent_id,
                assignee=to if is_known_agent else None,
                role=role or (to if (registry and not is_known_agent) else None),
                priority=priority,
                labels=labels,
                category=category,
                phase_id=phase_id,
            )
            task_id = task.id

        if is_known_agent and to:
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
