from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

CONSECUTIVE_ERROR_THRESHOLD = 3


class SessionState(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    KILLED = "killed"


def _new_record(session_id: str = "", state: str = "active") -> dict:
    return {
        "session_id": session_id,
        "state": state,
        "request_count": 0,
        "error_count": 0,
        "consecutive_errors": 0,
        "total_duration_ms": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_used_at": None,
        "paused_at": None,
        "killed_at": None,
    }


class SessionStore:
    def __init__(self, path: Path):
        self.path = path
        self._store: dict[str, dict] = {}
        self.load()

    # ── Public API (backward-compatible) ────────────────────────

    def get(self, agent_id: str) -> str | None:
        """Return session_id string for an agent, or None."""
        rec = self._store.get(agent_id)
        if rec is None:
            return None
        sid = rec.get("session_id", "")
        return sid if sid else None

    def set(self, agent_id: str, session_id: str):
        """Create or update session record, preserving stats."""
        rec = self._store.get(agent_id)
        if rec is None:
            rec = _new_record(session_id)
            self._store[agent_id] = rec
        else:
            old_sid = rec.get("session_id", "")
            rec["session_id"] = session_id
            # If session_id changed (new session), reset consecutive errors
            if session_id and session_id != old_sid:
                rec["consecutive_errors"] = 0
                if rec.get("state") == SessionState.KILLED.value:
                    rec["state"] = SessionState.ACTIVE.value
        self.save()

    # ── Rich metadata API ───────────────────────────────────────

    def get_info(self, agent_id: str) -> dict | None:
        return self._store.get(agent_id)

    def get_all_info(self) -> dict[str, dict]:
        return dict(self._store)

    def get_state(self, agent_id: str) -> SessionState:
        rec = self._store.get(agent_id)
        if rec is None:
            return SessionState.ACTIVE
        return SessionState(rec.get("state", "active"))

    def set_state(self, agent_id: str, state: SessionState):
        rec = self._store.get(agent_id)
        if rec is None:
            rec = _new_record()
            self._store[agent_id] = rec
        rec["state"] = state.value
        now = datetime.now(timezone.utc).isoformat()
        if state == SessionState.PAUSED:
            rec["paused_at"] = now
        elif state == SessionState.KILLED:
            rec["killed_at"] = now
        self.save()

    def is_paused(self, agent_id: str) -> bool:
        return self.get_state(agent_id) == SessionState.PAUSED

    def is_killed(self, agent_id: str) -> bool:
        return self.get_state(agent_id) == SessionState.KILLED

    def get_model(self, agent_id: str) -> str | None:
        """Return persisted model override for an agent, or None."""
        rec = self._store.get(agent_id)
        return rec.get("model") if rec else None

    def set_model(self, agent_id: str, model: str):
        """Persist a model override for an agent."""
        rec = self._store.get(agent_id)
        if rec is None:
            rec = _new_record()
            self._store[agent_id] = rec
        rec["model"] = model
        self.save()

    def record_request(
        self, agent_id: str, duration_ms: int, is_error: bool
    ) -> bool:
        """Record a request and return True if auto-pause was triggered."""
        rec = self._store.get(agent_id)
        if rec is None:
            rec = _new_record()
            self._store[agent_id] = rec

        rec["request_count"] = rec.get("request_count", 0) + 1
        rec["total_duration_ms"] = rec.get("total_duration_ms", 0) + duration_ms
        rec["last_used_at"] = datetime.now(timezone.utc).isoformat()

        if is_error:
            rec["error_count"] = rec.get("error_count", 0) + 1
            rec["consecutive_errors"] = rec.get("consecutive_errors", 0) + 1
        else:
            rec["consecutive_errors"] = 0

        auto_paused = False
        if rec["consecutive_errors"] >= CONSECUTIVE_ERROR_THRESHOLD:
            if rec.get("state") != SessionState.PAUSED.value:
                rec["state"] = SessionState.PAUSED.value
                rec["paused_at"] = datetime.now(timezone.utc).isoformat()
                auto_paused = True
                logger.warning(
                    "Auto-pausing session for %s after %d consecutive errors",
                    agent_id,
                    rec["consecutive_errors"],
                )

        self.save()
        return auto_paused

    def clear_session(self, agent_id: str):
        """Clear session for kill flow — reset to active with fresh counters.

        Preserves the model override so a reset doesn't lose the user's
        model choice.
        """
        old = self._store.get(agent_id, {})
        rec = _new_record()
        if "model" in old:
            rec["model"] = old["model"]
        self._store[agent_id] = rec
        self.save()

    # ── Persistence ─────────────────────────────────────────────

    def save(self):
        self.path.write_text(json.dumps({"sessions": self._store}, indent=2))

    def load(self):
        if not self.path.exists():
            self._store = {}
            return
        try:
            raw = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            self._store = {}
            return

        if isinstance(raw, dict) and "sessions" in raw:
            # New format
            self._store = raw["sessions"]
        elif isinstance(raw, dict):
            # Old flat format: { agent_id: session_id_str }
            self._store = {}
            for agent_id, value in raw.items():
                if isinstance(value, str):
                    self._store[agent_id] = _new_record(value)
                elif isinstance(value, dict):
                    # Already migrated entry mixed in
                    self._store[agent_id] = value
            logger.info("Migrated %d sessions from old format", len(self._store))
            self.save()
        else:
            self._store = {}
