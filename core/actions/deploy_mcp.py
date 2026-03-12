"""Deploy an MCP server to an agent — Rory only.

Installs the MCP package into the target agent's container (or locally),
generates the MCP config, and refreshes the agent's session so it picks
up the new tools.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext
from core.message import Message, MessageType

if TYPE_CHECKING:
    from core.agent import Agent

logger = logging.getLogger(__name__)


class DeployMcp(BaseAction):

    name = "deploy_mcp"
    description = "Deploy an MCP server to a target agent's container."

    fields = [
        ActionField("server_name", "string", required=True,
                     description="Short name for the MCP server (e.g. 'postgres')"),
        ActionField("package", "string", required=True,
                     description="Package name (e.g. '@modelcontextprotocol/server-postgres')"),
        ActionField("target_agent", "string", required=True,
                     description="Agent ID to deploy the server to"),
        ActionField("install_method", "string",
                     description="Installation method: npx, uvx, or docker",
                     default="npx",
                     enum=["npx", "uvx", "docker"]),
        ActionField("env", "object",
                     description="Environment variables as key-value pairs"),
    ]

    example = {
        "action": "deploy_mcp",
        "server_name": "postgres",
        "package": "@modelcontextprotocol/server-postgres",
        "target_agent": "backend_api",
        "install_method": "npx",
        "env": {"DATABASE_URL": "postgres://user:pass@host/db"},
    }

    async def execute(
        self, agent: Agent, action: dict, original_msg: Message,
        ctx: ActionContext,
    ) -> list[Message]:
        server_name = action.get("server_name", "")
        package = action.get("package", "")
        target_agent_id = action.get("target_agent", "")
        install_method = action.get("install_method", "npx")
        env_values = action.get("env", {}) or {}

        if not server_name or not package or not target_agent_id:
            return [Message(
                sender="system",
                recipient=agent.agent_id,
                type=MessageType.SYSTEM,
                content="[Deploy MCP Error] Missing required fields: server_name, package, and target_agent are all required.",
            )]

        mcp_manager = agent.deps.get("mcp_manager")
        if not mcp_manager:
            return [Message(
                sender="system",
                recipient=agent.agent_id,
                type=MessageType.SYSTEM,
                content="[Deploy MCP Error] No MCP manager available.",
            )]

        registry = agent.deps.get("registry")
        if not registry:
            return [Message(
                sender="system",
                recipient=agent.agent_id,
                type=MessageType.SYSTEM,
                content="[Deploy MCP Error] No agent registry available.",
            )]

        target_agent = registry.get(target_agent_id)
        if not target_agent:
            return [Message(
                sender="system",
                recipient=agent.agent_id,
                type=MessageType.SYSTEM,
                content=f"[Deploy MCP Error] Agent '{target_agent_id}' not found in registry.",
            )]

        # Determine command/args from install method and package
        if install_method == "npx":
            command = "npx"
            args = ["-y", package]
        elif install_method == "uvx":
            command = "uvx"
            args = [package]
        else:
            command = "npx"
            args = ["-y", package]

        logger.info(
            "Agent %s deploying MCP server '%s' (%s) to agent '%s'",
            agent.agent_id, server_name, package, target_agent_id,
        )

        # Create or find the MCP server record
        server_db_id = await mcp_manager.request_server({
            "server_id": server_name,
            "name": server_name,
            "package": package,
            "command": command,
            "args": args,
            "install_method": install_method,
            "env_template": {k: "" for k in env_values},
            "requested_by": target_agent_id,
            "reason": f"Deployed by {agent.agent_id}",
        })

        # Check if target agent has a container
        container_manager = agent.deps.get("container_manager")
        has_container = False
        if container_manager:
            has_container = container_manager.get_container_name(target_agent_id) is not None

        # Deploy
        if has_container:
            success = await mcp_manager.deploy_to_container(
                server_db_id, target_agent_id, env_values,
            )
        else:
            success = await mcp_manager.deploy_local(
                server_db_id, target_agent_id, env_values,
            )

        if not success:
            return [Message(
                sender="system",
                recipient=agent.agent_id,
                type=MessageType.SYSTEM,
                content=(
                    f"[Deploy MCP Failed] Failed to deploy '{server_name}' to agent '{target_agent_id}'. "
                    f"Check server logs for details."
                ),
            )]

        # Connect agent to MCP config and invalidate session
        session_store = agent.deps.get("session_store")
        if session_store:
            await mcp_manager.connect_to_agent(target_agent, session_store)

        # Notify the target agent
        target_notification = Message(
            sender="system",
            recipient=target_agent_id,
            type=MessageType.SYSTEM,
            content=(
                f"MCP server '{server_name}' has been deployed to your environment. "
                f"Your session has been refreshed — you now have access to new tools "
                f"provided by the {server_name} MCP server. "
                f"Use these tools as needed for your current and future tasks."
            ),
        )

        # Broadcast WebSocket event for dashboard
        if agent._broker:
            await agent._broker.broadcast_event({
                "event_type": "mcp_installed",
                "data": {
                    "server_name": server_name,
                    "package": package,
                    "target_agent": target_agent_id,
                    "deployed_by": agent.agent_id,
                    "container": has_container,
                },
            })

        # Confirmation message back to Rory
        deploy_method = "container" if has_container else "local"
        confirmation = Message(
            sender="system",
            recipient=agent.agent_id,
            type=MessageType.SYSTEM,
            content=(
                f"[MCP Deployed Successfully]\n"
                f"Server: {server_name} ({package})\n"
                f"Target agent: {target_agent_id}\n"
                f"Method: {deploy_method}\n"
                f"The agent's session has been invalidated — it will pick up the new "
                f"MCP tools on its next invocation. Inform the user of the successful deployment."
            ),
        )

        logger.info(
            "MCP server '%s' deployed to agent '%s' via %s",
            server_name, target_agent_id, deploy_method,
        )

        return [target_notification, confirmation]
