"""MCP Manager — lifecycle for discovering, deploying, and connecting MCP servers.

Handles:
  - Creating DB records for requested MCP servers
  - Installing MCP packages into agent containers (or locally)
  - Generating per-agent MCP config JSON files
  - Connecting agents to MCP servers by setting config paths + invalidating sessions
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.constants import MCP_PACKAGE_NAME_PATTERN

if TYPE_CHECKING:
    from core.container_manager import ContainerManager
    from core.session_store import SessionStore
    from db.repositories.mcp_repo import MCPRepository

logger = logging.getLogger(__name__)


class MCPManager:
    """Manages MCP server lifecycle: request → deploy → connect."""

    def __init__(
        self,
        mcp_repo: MCPRepository,
        container_manager: ContainerManager | None,
        project_id: str,
        config_dir: Path,
        messages_dir: Path,
    ):
        self._repo = mcp_repo
        self._container_manager = container_manager
        self._project_id = project_id
        self._config_dir = config_dir
        self._messages_dir = messages_dir
        self._config_dir.mkdir(parents=True, exist_ok=True)

    async def request_server(self, data: dict[str, Any]) -> int:
        """Create a pending MCP server record.

        Deduplicates by server_id + project. Returns the DB id.
        """
        server_id = data.get("server_id", "")
        if not server_id:
            server_id = data.get("name", "unknown").lower().replace(" ", "-")

        # Check for existing record
        existing = await self._repo.get_by_server_id(server_id, self._project_id)
        if existing and existing.status in ("installed", "deploying", "proposed"):
            logger.info(
                "MCP server '%s' already exists with status '%s'",
                server_id, existing.status,
            )
            return existing.id

        server = await self._repo.create(
            server_id=server_id,
            name=data.get("name", server_id),
            description=data.get("description", ""),
            package_name=data.get("package", ""),
            command=data.get("command", "npx"),
            args=data.get("args", []),
            env_template=data.get("env_template", {}),
            install_method=data.get("install_method", "npx"),
            status="pending",
            requested_by=data.get("requested_by"),
            requested_reason=data.get("reason", ""),
            project_id=self._project_id,
        )
        logger.info("Created MCP server request: %s (id=%d)", server_id, server.id)
        return server.id

    async def deploy_to_container(
        self,
        server_db_id: int,
        agent_id: str,
        env_values: dict[str, str],
    ) -> bool:
        """Install an MCP package into an agent's Docker container.

        Returns True on success.
        """
        server = await self._repo.get(server_db_id)
        if not server:
            logger.error("MCP server record %d not found", server_db_id)
            return False

        if not self._container_manager:
            logger.error("No container manager available — cannot deploy to container")
            return False

        container_name = self._container_manager.get_container_name(agent_id)
        if not container_name:
            logger.error("No container found for agent '%s'", agent_id)
            return False

        await self._repo.update_status(server_db_id, "deploying")

        try:
            # Install the package inside the container
            package = server.package_name
            install_method = server.install_method or "npx"

            # Validate package name to prevent command injection
            if not package or not MCP_PACKAGE_NAME_PATTERN.match(package):
                logger.error("Invalid MCP package name rejected: %r", package)
                await self._repo.update_status(
                    server_db_id, "failed",
                    error_message=f"Invalid package name: {package!r}",
                )
                return False

            # Use list-form subprocess (no shell) to avoid injection
            if install_method == "uvx":
                install_args = ["pip", "install", package]
            else:
                install_args = ["npm", "install", "-g", package]

            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", container_name, *install_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

            if proc.returncode != 0:
                err = stderr.decode(errors="replace")[:500]
                logger.error(
                    "Failed to install %s in container %s: %s",
                    package, container_name, err,
                )
                await self._repo.update_status(
                    server_db_id, "failed",
                    error_message=f"Install failed: {err}",
                )
                return False

            # Update env values and mark as installed
            await self._repo.update_env(server_db_id, env_values)
            await self._repo.add_agent(server_db_id, agent_id)
            await self._repo.set_container_installed(server_db_id, True)
            await self._repo.update_status(server_db_id, "installed")

            # Rebuild MCP config for this agent
            await self.build_mcp_config(agent_id)

            logger.info(
                "Deployed MCP server '%s' to container %s for agent '%s'",
                server.server_id, container_name, agent_id,
            )
            return True

        except asyncio.TimeoutError:
            await self._repo.update_status(
                server_db_id, "failed",
                error_message="Installation timed out (120s)",
            )
            return False
        except Exception as e:
            await self._repo.update_status(
                server_db_id, "failed",
                error_message=str(e)[:500],
            )
            logger.exception("Error deploying MCP server to container")
            return False

    async def deploy_local(
        self,
        server_db_id: int,
        agent_id: str,
        env_values: dict[str, str],
    ) -> bool:
        """For non-containerized agents: record the server and generate config.

        The MCP server will be launched by Claude CLI via npx/uvx on demand.
        No package installation needed — npx/uvx handles it.
        Returns True on success.
        """
        server = await self._repo.get(server_db_id)
        if not server:
            logger.error("MCP server record %d not found", server_db_id)
            return False

        await self._repo.update_status(server_db_id, "deploying")

        try:
            await self._repo.update_env(server_db_id, env_values)
            await self._repo.add_agent(server_db_id, agent_id)
            await self._repo.update_status(server_db_id, "installed")

            # Rebuild MCP config for this agent
            await self.build_mcp_config(agent_id)

            logger.info(
                "Deployed MCP server '%s' locally for agent '%s'",
                server.server_id, agent_id,
            )
            return True

        except Exception as e:
            await self._repo.update_status(
                server_db_id, "failed",
                error_message=str(e)[:500],
            )
            logger.exception("Error deploying MCP server locally")
            return False

    async def build_mcp_config(self, agent_id: str) -> Path | None:
        """Generate a per-agent MCP config JSON file.

        Returns the path to the config file, or None if agent has no servers.
        """
        servers = await self._repo.get_for_agent(agent_id, self._project_id)
        if not servers:
            # Remove stale config
            config_path = self._config_dir / f"{agent_id}_mcp.json"
            if config_path.exists():
                config_path.unlink()
            return None

        mcp_servers = {}
        for s in servers:
            entry = {
                "command": s.command or "npx",
                "args": list(s.args or []),
            }
            # Merge env_template keys with actual values
            env = {}
            for key in (s.env_template or {}):
                env[key] = (s.env_values or {}).get(key, "")
            # Also include any extra env values
            for key, val in (s.env_values or {}).items():
                if key not in env:
                    env[key] = val
            if env:
                entry["env"] = env
            mcp_servers[s.server_id] = entry

        config = {"mcpServers": mcp_servers}
        config_path = self._config_dir / f"{agent_id}_mcp.json"
        config_path.write_text(json.dumps(config, indent=2))

        # Also write to messages dir if it exists (for containerized agents)
        agent_msg_dir = self._messages_dir / agent_id
        if agent_msg_dir.is_dir():
            container_config = agent_msg_dir / "mcp_config.json"
            container_config.write_text(json.dumps(config, indent=2))

        logger.info(
            "Built MCP config for agent '%s' with %d servers: %s",
            agent_id, len(mcp_servers), config_path,
        )
        return config_path

    def get_mcp_config_path(self, agent_id: str) -> Path | None:
        """Return the MCP config file path if it exists, else None."""
        config_path = self._config_dir / f"{agent_id}_mcp.json"
        return config_path if config_path.exists() else None

    async def connect_to_agent(
        self,
        agent: Any,
        session_store: SessionStore,
    ) -> None:
        """Set an agent's MCP config path and invalidate its session.

        The next invocation will pick up --mcp-config and discover new tools.
        """
        config_path = self.get_mcp_config_path(agent.agent_id)
        if config_path:
            agent.mcp_config_path = config_path
            await session_store.invalidate_session(agent.agent_id)
            logger.info(
                "Connected agent '%s' to MCP config at %s (session invalidated)",
                agent.agent_id, config_path,
            )

    async def get_agent_servers(self, agent_id: str) -> list[dict]:
        """Get summary of installed MCP servers for an agent."""
        servers = await self._repo.get_for_agent(agent_id, self._project_id)
        return [
            {
                "server_id": s.server_id,
                "name": s.name,
                "package": s.package_name,
                "status": s.status,
            }
            for s in servers
        ]

    async def remove_server(self, server_db_id: int) -> bool:
        """Remove an MCP server record and rebuild affected agent configs."""
        server = await self._repo.get(server_db_id)
        if not server:
            return False

        agent_ids = list(server.agent_ids or [])
        await self._repo.delete(server_db_id)

        # Rebuild configs for affected agents
        for agent_id in agent_ids:
            await self.build_mcp_config(agent_id)

        logger.info("Removed MCP server '%s' (id=%d)", server.server_id, server_db_id)
        return True
