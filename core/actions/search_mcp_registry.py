"""Search the MCP server registry — Rory only.

Queries the built-in catalog and the official MCP registry, returning
results as a system message back to Rory for evaluation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext
from core.message import Message, MessageType

if TYPE_CHECKING:
    from core.agent import Agent

logger = logging.getLogger(__name__)


class SearchMcpRegistry(BaseAction):

    name = "search_mcp_registry"
    description = "Search the MCP server registry for tools that match a query."

    fields = [
        ActionField("query", "string", required=True,
                     description="Search query (e.g. 'postgres database', 'slack messaging')"),
    ]

    example = {
        "action": "search_mcp_registry",
        "query": "postgres database",
    }

    async def execute(
        self, agent: Agent, action: dict, original_msg: Message,
        ctx: ActionContext,
    ) -> list[Message]:
        query = action.get("query", "")
        if not query:
            logger.warning("Agent %s sent empty MCP registry search", agent.agent_id)
            return []

        mcp_registry = agent.deps.get("mcp_registry")
        if not mcp_registry:
            logger.error(
                "Agent %s tried search_mcp_registry but has no mcp_registry dep",
                agent.agent_id,
            )
            return [Message(
                sender="system",
                recipient=agent.agent_id,
                type=MessageType.SYSTEM,
                content="[MCP Registry Error] No MCP registry available. Cannot search for servers.",
            )]

        logger.info("Agent %s searching MCP registry: %r", agent.agent_id, query)

        try:
            results = await mcp_registry.search(query)
        except Exception as e:
            logger.exception("MCP registry search failed for %r", query)
            return [Message(
                sender="system",
                recipient=agent.agent_id,
                type=MessageType.SYSTEM,
                content=f"[MCP Registry Error] Search failed: {e}",
            )]

        if not results:
            return [Message(
                sender="system",
                recipient=agent.agent_id,
                type=MessageType.SYSTEM,
                content=(
                    f"[MCP Registry Search: '{query}']\n\n"
                    f"No MCP servers found matching '{query}'. "
                    f"Try a different search query or inform the user that "
                    f"no suitable MCP server was found."
                ),
            )]

        # Format results for Rory to evaluate
        lines = [f"[MCP Registry Search Results: '{query}']\n"]
        lines.append(f"Found {len(results)} server(s):\n")

        for i, r in enumerate(results[:10], 1):
            lines.append(f"**{i}. {r.name}** (`{r.package}`)")
            lines.append(f"   {r.description[:200]}")
            lines.append(f"   Install: {r.install_method} | Command: `{r.command} {' '.join(r.args)}`")
            if r.env_required:
                lines.append(f"   Required env vars: {', '.join(r.env_required)}")
            lines.append(f"   Source: {r.source}")
            lines.append("")

        lines.append(
            "Evaluate these results and propose the best match to the user via `respond_to_user`. "
            "Include what the server does, what env vars the user needs to provide, "
            "and which agent will receive the capability. Wait for user approval before deploying."
        )

        return [Message(
            sender="system",
            recipient=agent.agent_id,
            type=MessageType.SYSTEM,
            content="\n".join(lines),
        )]
