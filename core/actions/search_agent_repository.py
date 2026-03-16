"""Search the agent repository for reusable agent templates."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext
from core.message import Message, MessageType

if TYPE_CHECKING:
    from core.agent import Agent

logger = logging.getLogger(__name__)


class SearchAgentRepository(BaseAction):

    name = "search_agent_repository"
    description = "Search the agent repository for existing agent templates matching a role or skill."
    produces_messages = True

    fields = [
        ActionField("query", "string", required=True,
                     description="Role, skill, or technology to search for"),
    ]

    async def execute(
        self, agent: Agent, action: dict, original_msg: Message,
        ctx: ActionContext,
    ) -> list[Message]:
        template_repo = agent.deps.get("template_repo")
        if not template_repo:
            return [Message(
                sender="system",
                recipient=agent.agent_id,
                type=MessageType.SYSTEM,
                content="Agent repository is not available.",
            )]

        query = action.get("query", "")
        if not query:
            return []

        results = await template_repo.search(query)

        if not results:
            return [Message(
                sender="system",
                recipient=agent.agent_id,
                type=MessageType.SYSTEM,
                content=(
                    f"No agent templates found matching '{query}'. "
                    "Proceed with creating a new agent from scratch."
                ),
            )]

        lines = [f"Found {len(results)} template(s) matching '{query}':\n"]
        for t in results:
            personality_preview = (t.personality[:150] + "...") if len(t.personality) > 150 else t.personality
            tags_str = ", ".join(t.tags) if t.tags else "none"
            lines.append(
                f"- **{t.name}** (template_id: `{t.id}`)\n"
                f"  Title: {t.title}\n"
                f"  Personality: {personality_preview}\n"
                f"  Model: {t.model} | Tools: {t.allowed_tools or 'default'}\n"
                f"  Scope: {t.scope} | Tags: {tags_str}\n"
            )
        lines.append(
            "\nPresent these candidates to the user via `respond_to_user`. "
            "If the user selects one, use `recruit_agent` with the `template_id`. "
            "If the user rejects all candidates, ask for their feedback on what's "
            "wrong and use it to create a better agent from scratch."
        )

        return [Message(
            sender="system",
            recipient=agent.agent_id,
            type=MessageType.SYSTEM,
            content="\n".join(lines),
        )]
