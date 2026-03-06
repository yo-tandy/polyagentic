"""Write a new document to the knowledge base."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext, infer_doc_category
from core.message import Message

if TYPE_CHECKING:
    from core.agent import Agent

logger = logging.getLogger(__name__)


class WriteDocument(BaseAction):

    name = "write_document"
    description = "Write a new document to the knowledge base."
    produces_messages = False

    fields = [
        ActionField("title", "string", required=True,
                     description="Document title"),
        ActionField("content", "string", required=True,
                     description="Document content in markdown"),
        ActionField("category", "string",
                     description="Document category",
                     enum=["specs", "design", "architecture",
                           "planning", "history"]),
    ]

    async def execute(
        self, agent: Agent, action: dict, original_msg: Message,
        ctx: ActionContext,
    ) -> list[Message]:
        if not agent._knowledge_base:
            return []
        title = action.get("title", "")
        category = action.get("category", "") or infer_doc_category(title)
        content = action.get("content", "")
        if not title or not content:
            logger.warning(
                "Agent %s write_document missing fields: title=%s, "
                "category=%s, content_len=%d",
                agent.agent_id, bool(title), bool(category), len(content),
            )
            return []
        if not category:
            category = "specs"
        try:
            await agent._knowledge_base.add_document(
                title=title, category=category,
                content=content, created_by=agent.agent_id,
            )
            ctx.kb_changed = True
        except ValueError as e:
            logger.warning(
                "KB write_document error from %s: %s", agent.agent_id, e,
            )
        return []
