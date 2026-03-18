"""Read a document from the knowledge base."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext
from core.message import Message, MessageType

if TYPE_CHECKING:
    from core.agent import Agent

logger = logging.getLogger(__name__)


class ReadDocument(BaseAction):

    name = "read_document"
    description = "Read the full content of a document from the project knowledge base."
    produces_messages = True

    fields = [
        ActionField("doc_id", "string", required=True,
                     description="Document ID from the KB index (e.g. 'doc-abc123')"),
    ]

    example = {"action": "read_document", "doc_id": "doc-abc123"}

    async def execute(
        self, agent: Agent, action: dict, original_msg: Message,
        ctx: ActionContext,
    ) -> list[Message]:
        doc_id = action.get("doc_id", "").strip()
        if not doc_id:
            return [Message(
                sender="system",
                recipient=agent.agent_id,
                type=MessageType.SYSTEM,
                content="[read_document] Missing required field `doc_id`.",
            )]

        kb = agent._knowledge_base
        if not kb:
            return [Message(
                sender="system",
                recipient=agent.agent_id,
                type=MessageType.SYSTEM,
                content="[read_document] Knowledge base not available.",
            )]

        content = await kb.get_document_content(doc_id)
        if content is None:
            return [Message(
                sender="system",
                recipient=agent.agent_id,
                type=MessageType.SYSTEM,
                content=f"[read_document] Document '{doc_id}' not found.",
            )]

        doc = await kb.get_document(doc_id)
        title = doc.get("title", doc_id) if doc else doc_id
        category = doc.get("category", "") if doc else ""

        header = f"[Document: {title}]"
        if category:
            header += f" (category: {category})"

        logger.info(
            "Agent %s read KB document %s (%s, %d chars)",
            agent.agent_id, doc_id, title, len(content),
        )

        return [Message(
            sender="system",
            recipient=agent.agent_id,
            type=MessageType.SYSTEM,
            content=f"{header}\n\n{content}",
        )]
