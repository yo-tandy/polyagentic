"""End an active conversation with the user."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext
from core.message import Message, MessageType

if TYPE_CHECKING:
    from core.agent import Agent

logger = logging.getLogger(__name__)


class EndConversation(BaseAction):

    name = "end_conversation"
    description = "End an active conversation with the user."
    produces_messages = False

    fields = [
        ActionField("summary", "string", required=True,
                     description="Summary of discussion and decisions"),
    ]

    async def execute(
        self, agent: Agent, action: dict, original_msg: Message,
        ctx: ActionContext,
    ) -> list[Message]:
        if not agent._conversation_manager:
            return []
        summary = action.get("summary", "")
        conv = await agent._conversation_manager.close_by_agent(agent.agent_id)
        if not conv:
            return []

        # Save summary to knowledge base
        if summary and agent._knowledge_base:
            try:
                await agent._knowledge_base.add_document(
                    title=conv.get("title", "Conversation Summary"),
                    category="specs",
                    content=summary,
                    created_by=agent.agent_id,
                )
                ctx.kb_changed = True
            except Exception:
                logger.exception(
                    "Failed to save conversation summary to KB"
                )

        # Send summary to the user-facing agent
        if summary and agent._broker:
            ufa = agent._user_facing_agent
            summary_msg = Message(
                sender=agent.agent_id,
                recipient=ufa,
                type=MessageType.RESPONSE,
                content=(
                    f"Conversation completed: "
                    f"'{conv.get('title', 'Conversation')}'\n\n"
                    f"Summary:\n{summary}"
                ),
                metadata={"conversation_summary": True},
            )
            await agent._broker.deliver(summary_msg)

        return []
