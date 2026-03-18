"""Delegate a task to another team member."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext
from core.message import Message, MessageType
from core.task import TaskStatus

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
        ActionField("estimate", "integer",
                     description="Story point estimate (Fibonacci: 1, 2, 3, 5, 8, 13)"),
        ActionField("initial_status", "string",
                     description="Initial status: pending (default for operational) or draft (default for project, awaits estimation)",
                     default=None,
                     enum=["draft", "pending"]),
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
        estimate = action.get("estimate")
        default_status = "pending" if category == "operational" else "draft"
        initial_status_str = action.get("initial_status", default_status)
        initial_status = TaskStatus(initial_status_str)

        # Smart routing: agents with _registry check if target exists
        registry = agent.deps.get("registry")
        if registry:
            is_known_agent = bool(registry.get(to))
        else:
            is_known_agent = True  # No registry → send directly

        task_id = None
        if agent._task_board:
            is_draft = initial_status == TaskStatus.DRAFT

            if category == "project":
                # Project tasks: role-based by default — any agent with
                # the matching role can pick it up.  The agent claims
                # assignee when it transitions to IN_PROGRESS.
                task_assignee = None
                if role:
                    task_role = role
                elif is_known_agent and registry:
                    target_agent = registry.get(to)
                    task_role = target_agent.role if target_agent else to
                else:
                    task_role = to
            else:
                # Operational: always direct assignment
                task_assignee = to if is_known_agent and not is_draft else None
                task_role = role or (to if (registry and not is_known_agent) else None)

            task = await agent._task_board.create_task(
                title=title,
                description=desc,
                created_by=agent.agent_id,
                assignee=task_assignee,
                role=task_role,
                priority=priority,
                labels=labels,
                category=category,
                phase_id=phase_id,
                estimate=estimate,
                initial_status=initial_status,
            )
            task_id = task.id

        # Don't send message for draft tasks — Jerry will notify when scheduling
        if is_known_agent and to and initial_status != TaskStatus.DRAFT:
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
