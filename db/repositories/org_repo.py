"""Organization repository — CRUD for organizations."""

from __future__ import annotations

import logging

from sqlalchemy import select

from db.models.organization import Organization
from db.models.user import User
from db.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class OrgRepository(BaseRepository):

    async def create(self, id: str, name: str) -> Organization:
        return await self._create(Organization(id=id, name=name))

    async def get(self, org_id: str) -> Organization | None:
        return await self._get_by_id(Organization, org_id)

    async def update_name(self, org_id: str, name: str) -> None:
        await self._update_by_id(Organization, org_id, name=name)

    async def list_members(self, org_id: str) -> list[User]:
        return await self._list_all(
            User,
            User.org_id == org_id,
            User.is_active == True,  # noqa: E712
            order_by=User.created_at,
        )

    async def ensure_default(self) -> Organization:
        """Create the 'default' org if it doesn't exist. Returns it."""
        org = await self._get_by_id(Organization, "default")
        if org:
            return org
        org = await self._create(Organization(id="default", name="Default Organization"))
        logger.info("Created default organization")
        return org
