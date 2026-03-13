"""Phase repository — CRUD for project phases."""

from __future__ import annotations

import logging
from typing import Any

from db.models.phase import PhaseModel
from db.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class PhaseRepository(BaseRepository):

    async def create(self, project_id: str, **kwargs: Any) -> PhaseModel:
        return await self._create(PhaseModel(project_id=project_id, **kwargs))

    async def get(self, phase_id: str) -> PhaseModel | None:
        return await self._get_by_id(PhaseModel, phase_id)

    async def get_all(self, project_id: str) -> list[PhaseModel]:
        return await self._list_all(
            PhaseModel,
            PhaseModel.project_id == project_id,
            order_by=PhaseModel.ordering,
        )

    async def update(self, phase_id: str, **kwargs: Any) -> PhaseModel | None:
        return await self._update_by_id(PhaseModel, phase_id, **kwargs)
