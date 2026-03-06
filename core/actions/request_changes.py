"""Request changes on a pull request."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext
from core.message import Message, MessageType

if TYPE_CHECKING:
    from core.agent import Agent

logger = logging.getLogger(__name__)


class RequestChanges(BaseAction):

    name = "request_changes"
    description = "Request changes on a pull request."
    allowed_agents = {"innes"}

    fields = [
        ActionField("pr_number", "integer", required=True,
                     description="PR number"),
        ActionField("message", "string", required=True,
                     description="Change request details"),
    ]

    async def execute(
        self, agent: Agent, action: dict, original_msg: Message,
        ctx: ActionContext,
    ) -> list[Message]:
        pr_number = action.get("pr_number", "?")
        feedback = action.get("message", "Changes requested.")
        logger.info("request_changes on PR #%s", pr_number)
        return [Message(
            sender=agent.agent_id,
            recipient=original_msg.sender,
            type=MessageType.RESPONSE,
            content=f"Changes requested on PR #{pr_number}: {feedback}",
            task_id=original_msg.task_id,
            parent_message_id=original_msg.id,
        )]
