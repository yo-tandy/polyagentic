"""Update an existing document in the knowledge base."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext
from core.message import Message

if TYPE_CHECKING:
    from core.agent import Agent


class UpdateDocument(BaseAction):

    name = "update_document"
    description = "Update an existing document in the knowledge base."
    produces_messages = False

    fields = [
        ActionField("doc_id", "string", required=True,
                     description="Document ID to update"),
        ActionField("content", "string", required=True,
                     description="Full updated content in markdown"),
    ]

    async def execute(
        self, agent: Agent, action: dict, original_msg: Message,
        ctx: ActionContext,
    ) -> list[Message]:
        if not agent._knowledge_base:
            return []
        doc_id = action.get("doc_id", "")
        content = action.get("content", "")
        if not doc_id or not content:
            return []
        await agent._knowledge_base.update_document(
            doc_id=doc_id, content=content, updated_by=agent.agent_id,
        )
        ctx.kb_changed = True
        return []
