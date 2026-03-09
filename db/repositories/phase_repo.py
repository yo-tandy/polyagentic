"""Phase repository — CRUD for project phases."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from db.models.phase import PhaseModel
from db.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class PhaseRepository(BaseRepository):

    async def create(self, project_id: str, **kwargs: Any) -> PhaseModel:
        async with self._session() as session:
            phase = PhaseModel(project_id=project_id, **kwargs)
            session.add(phase)
            await session.commit()
            await session.refresh(phase)
            return phase

    async def get(self, phase_id: str) -> PhaseModel | None:
        async with self._session() as session:
            return await session.get(PhaseModel, phase_id)

    async def get_all(self, project_id: str) -> list[PhaseModel]:
        async with self._session() as session:
            stmt = select(PhaseModel).where(
                PhaseModel.project_id == project_id,
            ).order_by(PhaseModel.ordering)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update(self, phase_id: str, **kwargs: Any) -> PhaseModel | None:
        async with self._session() as session:
            phase = await session.get(PhaseModel, phase_id)
            if not phase:
                return None
            for k, v in kwargs.items():
                if hasattr(phase, k):
                    setattr(phase, k, v)
            await session.commit()
            await session.refresh(phase)
            return phase
