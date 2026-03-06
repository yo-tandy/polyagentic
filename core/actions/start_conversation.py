"""Start an interactive conversation with the user."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext
from core.message import Message, MessageType

if TYPE_CHECKING:
    from core.agent import Agent

logger = logging.getLogger(__name__)


class StartConversation(BaseAction):

    name = "start_conversation"
    description = "Start an interactive conversation with the user."
    produces_messages = False

    fields = [
        ActionField("title", "string", required=True,
                     description="Conversation topic"),
        ActionField("goals", "array", required=True,
                     description="What you want to learn or decide"),
    ]

    async def execute(
        self, agent: Agent, action: dict, original_msg: Message,
        ctx: ActionContext,
    ) -> list[Message]:
        if not agent._conversation_manager:
            logger.warning("No conversation_manager for %s", agent.agent_id)
            return []
        goals = action.get("goals", [])
        title = action.get("title", "Conversation")
        conv = await agent._conversation_manager.start(agent.agent_id, goals, title)

        if agent._broker:
            msg = Message(
                sender="system",
                recipient=agent.agent_id,
                type=MessageType.CONVERSATION,
                content=(
                    f"Conversation started: {title}. "
                    f"Goals: {', '.join(goals)}"
                ),
                metadata={"conversation_id": conv["id"]},
            )
            await agent._broker.deliver(msg)

        return []
