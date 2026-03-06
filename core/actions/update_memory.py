"""Save notes to agent memory."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext
from core.message import Message

if TYPE_CHECKING:
    from core.agent import Agent

logger = logging.getLogger(__name__)


class UpdateMemory(BaseAction):

    name = "update_memory"
    description = "Save notes to your persistent memory."
    allowed_agents = None  # all agents
    produces_messages = False

    fields = [
        ActionField("memory_type", "string", required=True,
                     description="Type of memory",
                     enum=["project", "personality"]),
        ActionField("content", "string", required=True,
                     description="Updated memory content "
                                 "(re-summarize, don't just append)"),
    ]

    async def execute(
        self, agent: Agent, action: dict, original_msg: Message,
        ctx: ActionContext,
    ) -> list[Message]:
        if not agent._memory_manager:
            return []
        memory_type = action.get("memory_type", "")
        content = action.get("content", "")
        if not content:
            return []
        if memory_type == "personality":
            await agent._memory_manager.update_personality_memory(
                agent.agent_id, content,
            )
        elif memory_type == "project":
            await agent._memory_manager.update_project_memory(
                agent.agent_id, content,
            )
        else:
            logger.warning(
                "Unknown memory_type '%s' from %s",
                memory_type, agent.agent_id,
            )
        return []
