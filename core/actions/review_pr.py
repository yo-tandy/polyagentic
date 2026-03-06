"""Review a pull request."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext
from core.message import Message, MessageType

if TYPE_CHECKING:
    from core.agent import Agent


class ReviewPr(BaseAction):

    name = "review_pr"
    description = "Review a pull request."
    allowed_agents = {"innes"}

    fields = [
        ActionField("pr_number", "integer", required=True,
                     description="PR number"),
        ActionField("verdict", "string",
                     description="Review verdict"),
        ActionField("message", "string",
                     description="Review message"),
    ]

    async def execute(
        self, agent: Agent, action: dict, original_msg: Message,
        ctx: ActionContext,
    ) -> list[Message]:
        git_manager = getattr(agent, "_git_manager", None)
        pr_number = action.get("pr_number")
        if not pr_number or not git_manager:
            return []
        await git_manager.get_pull_request(int(pr_number))
        verdict = action.get("verdict", "pending")
        review_msg = action.get(
            "message", f"PR #{pr_number} reviewed: {verdict}",
        )
        return [Message(
            sender=agent.agent_id,
            recipient="user",
            type=MessageType.CHAT,
            content=review_msg,
            task_id=original_msg.task_id,
            parent_message_id=original_msg.id,
        )]
