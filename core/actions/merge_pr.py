"""Merge a pull request."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext
from core.message import Message, MessageType

if TYPE_CHECKING:
    from core.agent import Agent

logger = logging.getLogger(__name__)


class MergePr(BaseAction):

    name = "merge_pr"
    description = "Merge a pull request."
    allowed_agents = {"innes"}

    fields = [
        ActionField("pr_number", "integer", required=True,
                     description="PR number"),
        ActionField("method", "string",
                     description="Merge method",
                     enum=["squash", "merge", "rebase"],
                     default="squash"),
    ]

    async def execute(
        self, agent: Agent, action: dict, original_msg: Message,
        ctx: ActionContext,
    ) -> list[Message]:
        git_manager = getattr(agent, "_git_manager", None)
        pr_number = action.get("pr_number")
        method = action.get("method", "squash")
        if not pr_number or not git_manager:
            return []
        try:
            await git_manager.merge_pull_request(
                int(pr_number), method=method,
            )
            return [Message(
                sender=agent.agent_id,
                recipient="user",
                type=MessageType.CHAT,
                content=f"Merged PR #{pr_number} via {method}.",
                task_id=original_msg.task_id,
                parent_message_id=original_msg.id,
            )]
        except RuntimeError as e:
            logger.error("merge_pr failed: %s", e)
            return [Message(
                sender=agent.agent_id,
                recipient=original_msg.sender,
                type=MessageType.RESPONSE,
                content=f"Failed to merge PR #{pr_number}: {e}",
                task_id=original_msg.task_id,
                parent_message_id=original_msg.id,
            )]
