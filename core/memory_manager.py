from __future__ import annotations

import logging

from db.repositories.memory_repo import MemoryRepository

logger = logging.getLogger(__name__)

MAX_MEMORY_CHARS = 2000  # Per memory type, to keep prompt sizes reasonable


class MemoryManager:
    """Manages agent memory — personality (global) and project (scoped).

    Backed by the ``agent_memories`` DB table via MemoryRepository.
    """

    def __init__(
        self,
        repo: MemoryRepository,
        project_id: str | None = None,
        max_chars: int = MAX_MEMORY_CHARS,
    ):
        self._repo = repo
        self._project_id = project_id
        self._max_chars = max_chars
        # In-memory cache for sync access (populated by async get_combined_memory)
        self._cache: dict[str, str] = {}

    # ── Personality memory (global, survives project switches) ──

    async def get_personality_memory(self, agent_id: str) -> str:
        content = await self._repo.get(
            agent_id, "personality", project_id=None,
        )
        return content[:self._max_chars] if content else ""

    async def update_personality_memory(self, agent_id: str, content: str):
        await self._repo.update(
            agent_id, "personality", content, project_id=None,
        )
        logger.info("Updated personality memory for %s (%d chars)", agent_id, len(content))

    # ── Project memory (project-scoped) ──

    async def get_project_memory(self, agent_id: str) -> str:
        if not self._project_id:
            return ""
        content = await self._repo.get(
            agent_id, "project", project_id=self._project_id,
        )
        return content[:self._max_chars] if content else ""

    async def update_project_memory(self, agent_id: str, content: str):
        if not self._project_id:
            logger.warning("No project_id set, cannot update project memory for %s", agent_id)
            return
        await self._repo.update(
            agent_id, "project", content, project_id=self._project_id,
        )
        logger.info("Updated project memory for %s (%d chars)", agent_id, len(content))

    # ── Combined ──

    async def get_combined_memory(self, agent_id: str) -> str:
        """Return both memories formatted for prompt injection."""
        parts = []
        personality = await self.get_personality_memory(agent_id)
        if personality:
            parts.append(f"## Your Personality & Skills Memory\n{personality}")
        project = await self.get_project_memory(agent_id)
        if project:
            parts.append(f"## Your Project Memory\n{project}")
        result = "\n\n".join(parts)
        self._cache[agent_id] = result
        return result

    def get_combined_memory_sync(self, agent_id: str) -> str:
        """Return cached combined memory for sync callers.

        Returns the value last fetched by ``get_combined_memory()``.
        Safe to call from sync code (e.g. ``_render_prompt_template``).
        """
        return self._cache.get(agent_id, "")
