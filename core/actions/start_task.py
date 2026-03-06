"""Start or resume a paused task."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext
from core.message import Message, MessageType

if TYPE_CHECKING:
    from core.agent import Agent


class StartTask(BaseAction):

    name = "start_task"
    description = "Start or resume a paused task."
    allowed_agents = {"manny", "dev_manager"}

    fields = [
        ActionField("agent_id", "string", required=True,
                     description="Agent to assign"),
        ActionField("task_id", "string", required=True,
                     description="Task to start/resume"),
    ]

    async def execute(
        self, agent: Agent, action: dict, original_msg: Message,
        ctx: ActionContext,
    ) -> list[Message]:
        target_agent = action.get("agent_id", "")
        target_task_id = action.get("task_id", "")
        task = (
            agent._task_board.get_task(target_task_id)
            if agent._task_board else None
        )
        content = ""
        if task:
            content = task.description
            if task.paused_summary:
                content += (
                    f"\n\n--- RESUMED TASK ---\n"
                    f"Previous state when paused:\n{task.paused_summary}"
                )
            await agent._task_board.update_task(
                target_task_id, assignee=target_agent,
                _agent_id=agent.agent_id,
            )
        return [Message(
            sender=agent.agent_id,
            recipient=target_agent,
            type=MessageType.TASK,
            content=content or f"Start working on task {target_task_id}",
            task_id=target_task_id,
            parent_message_id=original_msg.id,
            metadata={"task_title": task.title if task else "Task"},
        )]
