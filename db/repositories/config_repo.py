"""Configuration repository — CRUD for config_entries table."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select, delete

from db.models.config import ConfigEntry
from db.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


def _cast_value(raw: str, value_type: str) -> Any:
    """Convert stored string value to its typed representation."""
    if value_type == "int":
        return int(raw)
    if value_type == "float":
        return float(raw)
    if value_type == "bool":
        return raw.lower() in ("true", "1", "yes")
    if value_type == "json":
        return json.loads(raw)
    return raw  # string


class ConfigRepository(BaseRepository):
    """CRUD for the config_entries table."""

    async def get(
        self,
        scope: str,
        key: str,
        scope_id: str | None = None,
        tenant_id: str = "default",
    ) -> str | None:
        """Get raw string value for a config entry."""
        async with self._session() as session:
            stmt = select(ConfigEntry.value).where(
                ConfigEntry.tenant_id == tenant_id,
                ConfigEntry.scope == scope,
                ConfigEntry.scope_id == scope_id,
                ConfigEntry.key == key,
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return row

    async def get_typed(
        self,
        scope: str,
        key: str,
        scope_id: str | None = None,
        tenant_id: str = "default",
    ) -> Any:
        """Get a config value cast to its declared type."""
        async with self._session() as session:
            stmt = select(ConfigEntry.value, ConfigEntry.value_type).where(
                ConfigEntry.tenant_id == tenant_id,
                ConfigEntry.scope == scope,
                ConfigEntry.scope_id == scope_id,
                ConfigEntry.key == key,
            )
            result = await session.execute(stmt)
            row = result.one_or_none()
            if row is None:
                return None
            return _cast_value(row[0], row[1])

    async def set(
        self,
        scope: str,
        key: str,
        value: str,
        value_type: str = "string",
        scope_id: str | None = None,
        description: str | None = None,
        tenant_id: str = "default",
    ) -> None:
        """Upsert a config entry."""
        async with self._session() as session:
            stmt = select(ConfigEntry).where(
                ConfigEntry.tenant_id == tenant_id,
                ConfigEntry.scope == scope,
                ConfigEntry.scope_id == scope_id,
                ConfigEntry.key == key,
            )
            result = await session.execute(stmt)
            entry = result.scalar_one_or_none()

            if entry:
                entry.value = value
                entry.value_type = value_type
                if description is not None:
                    entry.description = description
            else:
                entry = ConfigEntry(
                    tenant_id=tenant_id,
                    scope=scope,
                    scope_id=scope_id,
                    key=key,
                    value=value,
                    value_type=value_type,
                    description=description,
                )
                session.add(entry)

            await session.commit()

    async def get_all_for_scope(
        self,
        scope: str,
        scope_id: str | None = None,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        """Get all config entries for a scope as a typed dict."""
        async with self._session() as session:
            stmt = select(ConfigEntry).where(
                ConfigEntry.tenant_id == tenant_id,
                ConfigEntry.scope == scope,
                ConfigEntry.scope_id == scope_id,
            )
            result = await session.execute(stmt)
            entries = result.scalars().all()
            return {
                e.key: _cast_value(e.value, e.value_type)
                for e in entries
            }

    async def get_system_config(
        self, tenant_id: str = "default",
    ) -> dict[str, Any]:
        """Shortcut: get all system-scope config as a typed dict."""
        return await self.get_all_for_scope("system", None, tenant_id)

    async def get_agent_config(
        self, agent_id: str, tenant_id: str = "default",
    ) -> dict[str, Any]:
        """Get all config entries for a specific agent."""
        return await self.get_all_for_scope("agent", agent_id, tenant_id)

    async def list_all(
        self, tenant_id: str = "default",
    ) -> list[dict]:
        """List all config entries (for API)."""
        async with self._session() as session:
            stmt = select(ConfigEntry).where(
                ConfigEntry.tenant_id == tenant_id,
            ).order_by(ConfigEntry.scope, ConfigEntry.key)
            result = await session.execute(stmt)
            entries = result.scalars().all()
            return [
                {
                    "id": e.id,
                    "scope": e.scope,
                    "scope_id": e.scope_id,
                    "key": e.key,
                    "value": e.value,
                    "value_type": e.value_type,
                    "description": e.description,
                }
                for e in entries
            ]

    async def delete(
        self,
        entry_id: int,
        tenant_id: str = "default",
    ) -> bool:
        """Delete a config entry by ID."""
        async with self._session() as session:
            stmt = delete(ConfigEntry).where(
                ConfigEntry.id == entry_id,
                ConfigEntry.tenant_id == tenant_id,
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def seed_defaults(self, defaults: list[dict]) -> int:
        """Seed default config entries if the table is empty.

        Returns the number of entries seeded.
        """
        async with self._session() as session:
            count_stmt = select(ConfigEntry.id).limit(1)
            result = await session.execute(count_stmt)
            if result.scalar_one_or_none() is not None:
                logger.info("Config table already seeded, skipping")
                return 0

            for d in defaults:
                session.add(ConfigEntry(
                    tenant_id=d.get("tenant_id", "default"),
                    scope=d["scope"],
                    scope_id=d.get("scope_id"),
                    key=d["key"],
                    value=d["value"],
                    value_type=d.get("value_type", "string"),
                    description=d.get("description"),
                ))

            await session.commit()
            logger.info("Seeded %d default config entries", len(defaults))
            return len(defaults)
