"""Action error repository — CRUD for validation error records."""

from __future__ import annotations

from sqlalchemy import select

from db.models.action_error import ActionError
from db.repositories.base import BaseRepository


class ActionErrorRepository(BaseRepository):

    async def create(
        self,
        project_id: str,
        agent_id: str,
        action_name: str,
        errors: list[str],
        payload: dict,
        tenant_id: str = "default",
    ) -> ActionError:
        """Persist a validation error record."""
        async with self._session() as session:
            entry = ActionError(
                project_id=project_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
                action_name=action_name,
                errors=errors,
                payload=payload,
            )
            session.add(entry)
            await session.commit()
            await session.refresh(entry)
            return entry

    async def get_recent(
        self,
        project_id: str,
        limit: int = 50,
        tenant_id: str = "default",
    ) -> list[ActionError]:
        """Return recent validation errors, newest first."""
        async with self._session() as session:
            stmt = (
                select(ActionError)
                .where(
                    ActionError.project_id == project_id,
                    ActionError.tenant_id == tenant_id,
                )
                .order_by(ActionError.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())
