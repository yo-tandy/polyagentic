"""Send a message to the user."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext
from core.message import Message, MessageType

if TYPE_CHECKING:
    from core.agent import Agent


class RespondToUser(BaseAction):

    name = "respond_to_user"
    description = "Send a message to the user."
    fields = [
        ActionField("message", "string", required=True,
                     description="The message content"),
        ActionField("suggested_answers", "array",
                     description="Up to 3 quick-reply options"),
    ]

    example = {
        "action": "respond_to_user",
        "message": "Here's what I found...",
        "suggested_answers": ["Continue", "Show details", "Stop"],
    }

    async def execute(
        self, agent: Agent, action: dict, original_msg: Message,
        ctx: ActionContext,
    ) -> list[Message]:
        suggested = action.get("suggested_answers", [])
        meta: dict[str, Any] = {}
        if suggested:
            meta["suggested_answers"] = suggested[:3]
        return [Message(
            sender=agent.agent_id,
            recipient="user",
            type=MessageType.CHAT,
            content=action.get("message", ""),
            task_id=original_msg.task_id,
            parent_message_id=original_msg.id,
            metadata=meta if meta else None,
        )]
