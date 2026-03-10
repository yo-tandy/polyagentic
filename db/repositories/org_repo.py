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
        async with self._session() as session:
            org = Organization(id=id, name=name)
            session.add(org)
            await session.commit()
            await session.refresh(org)
            return org

    async def get(self, org_id: str) -> Organization | None:
        async with self._session() as session:
            return await session.get(Organization, org_id)

    async def update_name(self, org_id: str, name: str) -> None:
        async with self._session() as session:
            org = await session.get(Organization, org_id)
            if org:
                org.name = name
                await session.commit()

    async def list_members(self, org_id: str) -> list[User]:
        async with self._session() as session:
            stmt = (
                select(User)
                .where(User.org_id == org_id, User.is_active == True)  # noqa: E712
                .order_by(User.created_at)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def ensure_default(self) -> Organization:
        """Create the 'default' org if it doesn't exist. Returns it."""
        async with self._session() as session:
            org = await session.get(Organization, "default")
            if org:
                return org
            org = Organization(id="default", name="Default Organization")
            session.add(org)
            await session.commit()
            await session.refresh(org)
            logger.info("Created default organization")
            return org
