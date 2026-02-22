from __future__ import annotations

import json
from pathlib import Path


class SessionStore:
    def __init__(self, path: Path):
        self.path = path
        self._store: dict[str, str] = {}
        self.load()

    def get(self, agent_id: str) -> str | None:
        return self._store.get(agent_id)

    def set(self, agent_id: str, session_id: str):
        self._store[agent_id] = session_id
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self._store, indent=2))

    def load(self):
        if self.path.exists():
            try:
                self._store = json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                self._store = {}
