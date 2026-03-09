"""Phase board — DB-backed with in-memory cache.

Follows the same pattern as TaskBoard: sync reads from cache,
async writes hit DB + refresh cache.
"""

from __future__ import annotations

import logging
import uuid

from db.repositories.phase_repo import PhaseRepository

logger = logging.getLogger(__name__)

VALID_PHASE_TRANSITIONS = {
    "planning":           {"awaiting_approval"},
    "awaiting_approval":  {"in_progress", "planning"},
    "in_progress":        {"review"},
    "review":             {"completed", "in_progress"},
    "completed":          set(),
}


class PhaseBoard:
    def __init__(self, repo: PhaseRepository, project_id: str):
        self._repo = repo
        self._project_id = project_id
        self._phases: dict[str, dict] = {}
        self._on_update_callback = None

    async def load(self) -> None:
        """Populate in-memory cache from DB at startup."""
        records = await self._repo.get_all(self._project_id)
        self._phases = {r.id: self._to_dict(r) for r in records}
        logger.info("Loaded %d phases from DB", len(self._phases))

    def set_on_update(self, callback):
        """Set callback invoked after any phase create/update. Signature: callback(phase_id)."""
        self._on_update_callback = callback

    async def create_phase(
        self, name: str, description: str, created_by: str, ordering: int = 0,
    ) -> dict:
        phase_id = f"phase-{uuid.uuid4().hex[:8]}"
        rec = await self._repo.create(
            project_id=self._project_id,
            id=phase_id,
            name=name,
            description=description,
            status="planning",
            ordering=ordering,
            created_by=created_by,
        )
        phase = self._to_dict(rec)
        self._phases[phase_id] = phase
        self._notify(phase_id)
        return phase

    async def update_phase(self, phase_id: str, **kwargs) -> dict | None:
        phase = self._phases.get(phase_id)
        if not phase:
            logger.warning("Phase %s not found", phase_id)
            return None

        # Validate status transition
        if "status" in kwargs:
            new_status = kwargs["status"]
            allowed = VALID_PHASE_TRANSITIONS.get(phase["status"], set())
            if new_status not in allowed:
                logger.warning(
                    "Invalid phase transition %s -> %s for %s",
                    phase["status"], new_status, phase_id,
                )
                return None

        # Write to DB
        await self._repo.update(phase_id, **kwargs)

        # Update cache
        phase.update(kwargs)
        self._notify(phase_id)
        return phase

    def get_phase(self, phase_id: str) -> dict | None:
        return self._phases.get(phase_id)

    def get_all_phases(self) -> list[dict]:
        return sorted(self._phases.values(), key=lambda p: p.get("ordering", 0))

    def get_current_phase(self) -> dict | None:
        """Return the first non-completed phase."""
        for p in self.get_all_phases():
            if p["status"] != "completed":
                return p
        return None

    def to_summary(self) -> list[dict]:
        return self.get_all_phases()

    def _notify(self, phase_id: str):
        if self._on_update_callback:
            self._on_update_callback(phase_id)

    @staticmethod
    def _to_dict(rec) -> dict:
        return {
            "id": rec.id,
            "name": rec.name,
            "description": rec.description,
            "status": rec.status,
            "ordering": rec.ordering,
            "planning_doc_id": rec.planning_doc_id,
            "review_doc_id": rec.review_doc_id,
            "created_by": rec.created_by,
        }
