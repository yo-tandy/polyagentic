"""Request a capability the agent doesn't have — any agent can use this.

The handler creates a pending MCP server record and sends a message to Rory
(Robot Resources) to orchestrate discovery and deployment.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext
from core.message import Message, MessageType

if TYPE_CHECKING:
    from core.agent import Agent

logger = logging.getLogger(__name__)


class RequestCapability(BaseAction):

    name = "request_capability"
    description = "Flag a missing capability so that Robot Resources can find and deploy an MCP server for you."

    fields = [
        ActionField("capability", "string", required=True,
                     description="What capability is needed (e.g. 'PostgreSQL database access')"),
        ActionField("context", "string",
                     description="Why you need this capability"),
    ]

    example = {
        "action": "request_capability",
        "capability": "PostgreSQL database access",
        "context": "I need to query the project's database to inspect schema and run migrations",
    }

    async def execute(
        self, agent: Agent, action: dict, original_msg: Message,
        ctx: ActionContext,
    ) -> list[Message]:
        capability = action.get("capability", "")
        context = action.get("context", "")

        if not capability:
            logger.warning("Agent %s sent empty capability request", agent.agent_id)
            return []

        # Create a pending MCP server record if mcp_manager is available
        mcp_manager = agent.deps.get("mcp_manager")
        if mcp_manager:
            await mcp_manager.request_server({
                "server_id": capability.lower().replace(" ", "-")[:50],
                "name": capability,
                "requested_by": agent.agent_id,
                "reason": context,
            })

        # Send a task message to Rory describing the need
        rory_message = (
            f"Agent '{agent.agent_id}' ({agent.name}) needs a capability it doesn't have:\n\n"
            f"**Requested capability:** {capability}\n"
        )
        if context:
            rory_message += f"**Context:** {context}\n"
        rory_message += (
            f"\nPlease search for an appropriate MCP server using `search_mcp_registry`, "
            f"evaluate the results, and propose a solution to the user. "
            f"The server should be deployed to agent '{agent.agent_id}'."
        )

        logger.info(
            "Agent %s requesting capability: %s → forwarding to rory",
            agent.agent_id, capability,
        )

        # Broadcast event for dashboard visibility
        if agent._broker:
            await agent._broker.broadcast_event({
                "event_type": "mcp_request",
                "data": {
                    "agent_id": agent.agent_id,
                    "capability": capability,
                    "context": context,
                },
            })

        return [Message(
            sender=agent.agent_id,
            recipient="rory",
            type=MessageType.TASK,
            content=rory_message,
            metadata={"capability_request": capability},
        )]
