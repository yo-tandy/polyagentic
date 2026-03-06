"""Mark document comments as resolved."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext
from core.message import Message

if TYPE_CHECKING:
    from core.agent import Agent

logger = logging.getLogger(__name__)


class ResolveComments(BaseAction):

    name = "resolve_comments"
    description = "Mark document comments as resolved."
    allowed_agents = None  # all agents
    produces_messages = False

    fields = [
        ActionField("doc_id", "string", required=True,
                     description="Document ID"),
        ActionField("resolutions", "array", required=True,
                     description='List of {"comment_id": "...", '
                                 '"resolution": "..."}'),
    ]

    async def execute(
        self, agent: Agent, action: dict, original_msg: Message,
        ctx: ActionContext,
    ) -> list[Message]:
        if not agent._knowledge_base:
            return []
        doc_id = action.get("doc_id", "")
        resolutions = action.get("resolutions", [])
        if not doc_id or not resolutions:
            logger.warning(
                "Agent %s resolve_comments missing fields", agent.agent_id,
            )
            return []

        # Verify that the agent also edited the document in this response
        edit_verified = bool(
            ctx.edited_doc_ids and doc_id in ctx.edited_doc_ids
        )
        if not edit_verified:
            logger.warning(
                "Agent %s resolved comments on %s WITHOUT editing "
                "the document",
                agent.agent_id, doc_id,
            )

        resolved = await agent._knowledge_base.resolve_comments(
            doc_id, resolutions, edit_verified=edit_verified,
        )
        if resolved and agent._broker:
            logger.info(
                "Agent %s resolved %d comment(s) on %s (edit_verified=%s)",
                agent.agent_id, len(resolved), doc_id, edit_verified,
            )
            await agent._broker.broadcast_event({
                "event_type": "comments_updated",
                "data": {"doc_id": doc_id},
            })

        # Auto-complete the current task if all assigned comments resolved
        if resolved and agent.current_task_id and agent._task_board:
            all_comments = await agent._knowledge_base.get_comments(doc_id)
            remaining = [
                c for c in all_comments
                if c["status"] == "open"
                and c.get("assigned_to") == agent.agent_id
            ]
            if not remaining:
                verified_str = (
                    "with verified edits"
                    if edit_verified
                    else "WITHOUT document edits (unverified)"
                )
                await agent._task_board.update_task(
                    agent.current_task_id,
                    status="done",
                    _agent_id=agent.agent_id,
                    completion_summary=(
                        f'Resolved {len(resolved)} comment(s) '
                        f'on "{doc_id}" {verified_str}.'
                    ),
                )
                logger.info(
                    "Auto-completed task %s after resolving all comments",
                    agent.current_task_id,
                )

        return []
