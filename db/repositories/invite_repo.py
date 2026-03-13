"""Invite link repository — CRUD for organization invite links."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update

from db.models.invite import InviteLink
from db.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class InviteRepository(BaseRepository):

    async def create(
        self, *, id: str, org_id: str, created_by_user_id: str,
        token: str, expires_at: datetime | None = None,
        max_uses: int | None = None,
    ) -> InviteLink:
        return await self._create(InviteLink(
            id=id, org_id=org_id, created_by_user_id=created_by_user_id,
            token=token, expires_at=expires_at, max_uses=max_uses,
        ))

    async def get_by_token(self, token: str) -> InviteLink | None:
        async with self._session() as session:
            stmt = select(InviteLink).where(InviteLink.token == token)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_active(self, org_id: str) -> list[InviteLink]:
        return await self._list_all(
            InviteLink,
            InviteLink.org_id == org_id,
            InviteLink.is_active == True,  # noqa: E712
            order_by=InviteLink.created_at.desc(),
        )

    async def increment_use_count(self, invite_id: str) -> None:
        async with self._session() as session:
            invite = await session.get(InviteLink, invite_id)
            if invite:
                invite.use_count += 1
                # Auto-deactivate if max_uses reached
                if invite.max_uses and invite.use_count >= invite.max_uses:
                    invite.is_active = False
                await session.commit()

    async def deactivate(self, invite_id: str) -> None:
        async with self._session() as session:
            await session.execute(
                update(InviteLink)
                .where(InviteLink.id == invite_id)
                .values(is_active=False)
            )
            await session.commit()

    def is_valid(self, invite: InviteLink) -> bool:
        """Check if an invite link is currently valid."""
        if not invite.is_active:
            return False
        if invite.expires_at and invite.expires_at < datetime.now(timezone.utc):
            return False
        if invite.max_uses and invite.use_count >= invite.max_uses:
            return False
        return True
