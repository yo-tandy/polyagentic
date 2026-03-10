"""User repository — CRUD for users."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from db.models.user import User
from db.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class UserRepository(BaseRepository):

    async def create(
        self, *, id: str, email: str, name: str, google_sub: str,
        org_id: str, picture_url: str | None = None,
    ) -> User:
        async with self._session() as session:
            user = User(
                id=id, email=email, name=name, google_sub=google_sub,
                org_id=org_id, picture_url=picture_url,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    async def get(self, user_id: str) -> User | None:
        async with self._session() as session:
            return await session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        async with self._session() as session:
            stmt = select(User).where(User.email == email)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_by_google_sub(self, google_sub: str) -> User | None:
        async with self._session() as session:
            stmt = select(User).where(User.google_sub == google_sub)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def update_last_login(self, user_id: str) -> None:
        async with self._session() as session:
            user = await session.get(User, user_id)
            if user:
                user.last_login_at = datetime.now(timezone.utc)
                await session.commit()

    async def update_profile(
        self, user_id: str, *, name: str | None = None,
        picture_url: str | None = None,
    ) -> None:
        async with self._session() as session:
            user = await session.get(User, user_id)
            if user:
                if name is not None:
                    user.name = name
                if picture_url is not None:
                    user.picture_url = picture_url
                await session.commit()
