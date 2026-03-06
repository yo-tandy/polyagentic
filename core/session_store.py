"""Session store — DB-backed with in-memory cache for hot-path reads.

The message loop calls ``is_paused`` / ``is_killed`` on every iteration,
so we keep a lightweight cache that is updated on every write and can be
refreshed from DB at startup.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum

from db.repositories.session_repo import SessionRepository

logger = logging.getLogger(__name__)

CONSECUTIVE_ERROR_THRESHOLD = 3


class SessionState(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    KILLED = "killed"


class SessionStore:
    """DB-backed session store with in-memory cache.

    All mutating methods are async (they hit the DB).
    Read-only ``is_paused`` / ``is_killed`` / ``get`` use the cache.
    """

    def __init__(self, repo: SessionRepository, project_id: str):
        self._repo = repo
        self._project_id = project_id
        # In-memory cache: agent_id -> dict with session fields
        self._cache: dict[str, dict] = {}

    async def load(self) -> None:
        """Populate cache from DB at startup."""
        records = await self._repo.get_all(self._project_id)
        for agent_id, rec in records.items():
            self._cache[agent_id] = self._record_to_dict(rec)
        logger.info("Loaded %d session records from DB", len(self._cache))

    @staticmethod
    def _record_to_dict(rec) -> dict:
        """Convert an AgentSession ORM object to a plain dict for the cache."""
        return {
            "session_id": rec.session_id or "",
            "state": rec.state or "active",
            "request_count": rec.request_count or 0,
            "error_count": rec.error_count or 0,
            "consecutive_errors": rec.consecutive_errors or 0,
            "total_duration_ms": rec.total_duration_ms or 0,
            "total_cost_usd": rec.total_cost_usd or 0.0,
            "total_input_tokens": rec.total_input_tokens or 0,
            "total_output_tokens": rec.total_output_tokens or 0,
            "created_at": rec.created_at.isoformat() if rec.created_at else None,
            "last_used_at": rec.last_used_at.isoformat() if rec.last_used_at else None,
            "paused_at": rec.paused_at.isoformat() if rec.paused_at else None,
            "killed_at": rec.killed_at.isoformat() if rec.killed_at else None,
            "model": rec.model,
            "prompt_hash": rec.prompt_hash,
        }

    # ── Cached reads (sync — used in hot path) ──────────────────

    def get(self, agent_id: str) -> str | None:
        """Return session_id string for an agent, or None."""
        rec = self._cache.get(agent_id)
        if rec is None:
            return None
        sid = rec.get("session_id", "")
        return sid if sid else None

    def get_info(self, agent_id: str) -> dict | None:
        return self._cache.get(agent_id)

    def get_all_info(self) -> dict[str, dict]:
        return dict(self._cache)

    def get_state(self, agent_id: str) -> SessionState:
        rec = self._cache.get(agent_id)
        if rec is None:
            return SessionState.ACTIVE
        return SessionState(rec.get("state", "active"))

    def is_paused(self, agent_id: str) -> bool:
        return self.get_state(agent_id) == SessionState.PAUSED

    def is_killed(self, agent_id: str) -> bool:
        return self.get_state(agent_id) == SessionState.KILLED

    def get_model(self, agent_id: str) -> str | None:
        rec = self._cache.get(agent_id)
        return rec.get("model") if rec else None

    def get_prompt_hash(self, agent_id: str) -> str | None:
        rec = self._cache.get(agent_id)
        return rec.get("prompt_hash") if rec else None

    # ── Async writes (hit DB + update cache) ─────────────────────

    async def set(self, agent_id: str, session_id: str):
        """Create or update session record."""
        await self._repo.set_session_id(self._project_id, agent_id, session_id)
        # Update cache
        if agent_id not in self._cache:
            self._cache[agent_id] = {"session_id": "", "state": "active"}
        old_sid = self._cache[agent_id].get("session_id", "")
        self._cache[agent_id]["session_id"] = session_id
        if session_id and session_id != old_sid:
            self._cache[agent_id]["consecutive_errors"] = 0
            if self._cache[agent_id].get("state") == SessionState.KILLED.value:
                self._cache[agent_id]["state"] = SessionState.ACTIVE.value

    async def set_state(self, agent_id: str, state: SessionState):
        await self._repo.set_state(self._project_id, agent_id, state.value)
        if agent_id not in self._cache:
            self._cache[agent_id] = {"session_id": "", "state": "active"}
        self._cache[agent_id]["state"] = state.value
        now = datetime.now(timezone.utc).isoformat()
        if state == SessionState.PAUSED:
            self._cache[agent_id]["paused_at"] = now
        elif state == SessionState.KILLED:
            self._cache[agent_id]["killed_at"] = now

    async def set_model(self, agent_id: str, model: str):
        await self._repo.set_model(self._project_id, agent_id, model)
        if agent_id not in self._cache:
            self._cache[agent_id] = {"session_id": "", "state": "active"}
        self._cache[agent_id]["model"] = model

    async def set_prompt_hash(self, agent_id: str, prompt_hash: str):
        await self._repo.set_prompt_hash(self._project_id, agent_id, prompt_hash)
        if agent_id not in self._cache:
            self._cache[agent_id] = {"session_id": "", "state": "active"}
        self._cache[agent_id]["prompt_hash"] = prompt_hash

    async def record_request(
        self,
        agent_id: str,
        duration_ms: int,
        is_error: bool,
        cost_usd: float = 0.0,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> bool:
        """Record a request. Returns True if auto-pause was triggered."""
        auto_paused = await self._repo.record_request(
            self._project_id, agent_id,
            duration_ms=duration_ms,
            is_error=is_error,
            cost_usd=cost_usd,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            consecutive_error_threshold=CONSECUTIVE_ERROR_THRESHOLD,
        )
        # Refresh this agent's cache from DB
        rec = await self._repo.get(self._project_id, agent_id)
        if rec:
            self._cache[agent_id] = self._record_to_dict(rec)
        return auto_paused

    async def invalidate_session(self, agent_id: str):
        """Clear session_id but keep accumulated stats."""
        await self._repo.invalidate_session(self._project_id, agent_id)
        if agent_id in self._cache:
            self._cache[agent_id]["session_id"] = ""
            self._cache[agent_id]["prompt_hash"] = None
            self._cache[agent_id]["consecutive_errors"] = 0
            if self._cache[agent_id].get("state") == SessionState.KILLED.value:
                self._cache[agent_id]["state"] = SessionState.ACTIVE.value

    async def clear_session(self, agent_id: str):
        """Full reset — clear session AND all accumulated stats."""
        old_model = self._cache.get(agent_id, {}).get("model")
        await self._repo.clear_session(self._project_id, agent_id)
        # Preserve model override
        if old_model:
            await self._repo.set_model(self._project_id, agent_id, old_model)
        # Refresh cache
        rec = await self._repo.get(self._project_id, agent_id)
        if rec:
            self._cache[agent_id] = self._record_to_dict(rec)
