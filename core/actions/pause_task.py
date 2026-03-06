"""Pause an agent's in-progress task."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext
from core.message import Message, MessageType

if TYPE_CHECKING:
    from core.agent import Agent


class PauseTask(BaseAction):

    name = "pause_task"
    description = "Pause an agent's in-progress task."
    fields = [
        ActionField("agent_id", "string", required=True,
                     description="Agent to pause"),
        ActionField("task_id", "string", required=True,
                     description="Task to pause"),
    ]

    async def execute(
        self, agent: Agent, action: dict, original_msg: Message,
        ctx: ActionContext,
    ) -> list[Message]:
        target_agent = action.get("agent_id", "")
        target_task_id = action.get("task_id", "")
        if not target_agent or not target_task_id:
            return []
        return [Message(
            sender=agent.agent_id,
            recipient=target_agent,
            type=MessageType.SYSTEM,
            content=(
                f"PAUSE TASK {target_task_id}: Summarize your current "
                f"progress, save your state using update_task with a "
                f"paused_summary, and stop working."
            ),
            task_id=target_task_id,
            metadata={"command": "pause_task"},
        )]
