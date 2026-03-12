"""MCP server repository — CRUD for MCP server records."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select, and_

from db.models.mcp_server import MCPServer
from db.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class MCPRepository(BaseRepository):

    async def create(self, **kwargs: Any) -> MCPServer:
        async with self._session() as session:
            server = MCPServer(**kwargs)
            session.add(server)
            await session.commit()
            await session.refresh(server)
            return server

    async def get(self, server_db_id: int) -> MCPServer | None:
        async with self._session() as session:
            return await session.get(MCPServer, server_db_id)

    async def get_by_server_id(
        self, server_id: str, project_id: str | None = None,
    ) -> MCPServer | None:
        async with self._session() as session:
            conditions = [MCPServer.server_id == server_id]
            if project_id:
                conditions.append(MCPServer.project_id == project_id)
            stmt = select(MCPServer).where(and_(*conditions)).limit(1)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_all(
        self,
        project_id: str | None = None,
        status: str | None = None,
    ) -> list[MCPServer]:
        async with self._session() as session:
            conditions = []
            if project_id:
                conditions.append(MCPServer.project_id == project_id)
            if status:
                conditions.append(MCPServer.status == status)
            stmt = (
                select(MCPServer)
                .where(and_(*conditions)) if conditions
                else select(MCPServer)
            )
            stmt = stmt.order_by(MCPServer.created_at.desc())
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_for_agent(
        self, agent_id: str, project_id: str | None = None,
    ) -> list[MCPServer]:
        """Get all installed MCP servers for a specific agent."""
        async with self._session() as session:
            conditions = [MCPServer.status == "installed"]
            if project_id:
                conditions.append(MCPServer.project_id == project_id)
            stmt = select(MCPServer).where(and_(*conditions))
            result = await session.execute(stmt)
            servers = result.scalars().all()
            # Filter by agent_id in the JSON list
            return [s for s in servers if agent_id in (s.agent_ids or [])]

    async def update_status(
        self,
        server_db_id: int,
        status: str,
        error_message: str | None = None,
    ) -> None:
        async with self._session() as session:
            server = await session.get(MCPServer, server_db_id)
            if server:
                server.status = status
                if error_message is not None:
                    server.error_message = error_message
                await session.commit()

    async def update_env(self, server_db_id: int, env_values: dict) -> None:
        async with self._session() as session:
            server = await session.get(MCPServer, server_db_id)
            if server:
                server.env_values = env_values
                await session.commit()

    async def add_agent(self, server_db_id: int, agent_id: str) -> None:
        async with self._session() as session:
            server = await session.get(MCPServer, server_db_id)
            if server:
                ids = list(server.agent_ids or [])
                if agent_id not in ids:
                    ids.append(agent_id)
                    server.agent_ids = ids
                    await session.commit()

    async def set_container_installed(
        self, server_db_id: int, installed: bool = True,
    ) -> None:
        async with self._session() as session:
            server = await session.get(MCPServer, server_db_id)
            if server:
                server.container_installed = installed
                await session.commit()

    async def delete(self, server_db_id: int) -> None:
        async with self._session() as session:
            server = await session.get(MCPServer, server_db_id)
            if server:
                await session.delete(server)
                await session.commit()
