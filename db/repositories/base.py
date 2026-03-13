"""Base repository — holds the session factory and common CRUD helpers."""

from __future__ import annotations

from typing import Any, TypeVar

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

T = TypeVar("T")


class BaseRepository:
    """Base class for all repositories.

    Provides generic CRUD helpers that eliminate boilerplate in simple
    repositories while remaining optional for complex ones.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    def _session(self) -> AsyncSession:
        """Create a new async session."""
        return self._session_factory()

    # ------------------------------------------------------------------
    # Generic CRUD helpers
    # ------------------------------------------------------------------

    async def _get_by_id(self, model_class: type[T], id_value: Any) -> T | None:
        """Fetch a single row by primary key."""
        async with self._session() as session:
            return await session.get(model_class, id_value)

    async def _create(self, instance: Any) -> Any:
        """Add, commit, refresh and return *instance*."""
        async with self._session() as session:
            session.add(instance)
            await session.commit()
            await session.refresh(instance)
            return instance

    async def _update_by_id(
        self,
        model_class: type[T],
        id_value: Any,
        **kwargs: Any,
    ) -> T | None:
        """Update specific columns on a row identified by primary key.

        Returns the refreshed instance, or ``None`` if the row was not found.
        """
        async with self._session() as session:
            instance = await session.get(model_class, id_value)
            if instance is None:
                return None
            for key, value in kwargs.items():
                if hasattr(instance, key):
                    setattr(instance, key, value)
            await session.commit()
            await session.refresh(instance)
            return instance

    async def _delete_by_id(self, model_class: type[T], id_value: Any) -> bool:
        """Delete a row by primary key.  Returns ``True`` if a row was removed."""
        async with self._session() as session:
            stmt = delete(model_class).where(model_class.id == id_value)  # type: ignore[attr-defined]
            result = await session.execute(stmt)
            await session.commit()
            return (result.rowcount or 0) > 0

    async def _list_all(
        self,
        model_class: type[T],
        *filters: Any,
        order_by: Any | None = None,
    ) -> list[T]:
        """Return rows matching optional *filters*, ordered by *order_by*."""
        async with self._session() as session:
            stmt = select(model_class)
            if filters:
                stmt = stmt.where(*filters)
            if order_by is not None:
                stmt = stmt.order_by(order_by)
            result = await session.execute(stmt)
            return list(result.scalars().all())
