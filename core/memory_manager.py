from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_MEMORY_CHARS = 2000  # Per memory type, to keep prompt sizes reasonable


class MemoryManager:
    """Manages agent memory files — personality (global) and project (scoped)."""

    def __init__(
        self,
        global_memory_dir: Path,
        project_memory_dir: Path | None = None,
    ):
        self.global_memory_dir = global_memory_dir
        self.project_memory_dir = project_memory_dir

    # ── Personality memory (global, survives project switches) ──

    def get_personality_memory(self, agent_id: str) -> str:
        path = self.global_memory_dir / agent_id / "personality.md"
        if path.exists():
            try:
                return path.read_text()[:MAX_MEMORY_CHARS]
            except OSError:
                logger.warning("Failed to read personality memory for %s", agent_id)
        return ""

    def update_personality_memory(self, agent_id: str, content: str):
        path = self.global_memory_dir / agent_id / "personality.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        logger.info("Updated personality memory for %s (%d chars)", agent_id, len(content))

    # ── Project memory (project-scoped) ──

    def get_project_memory(self, agent_id: str) -> str:
        if not self.project_memory_dir:
            return ""
        path = self.project_memory_dir / agent_id / "project.md"
        if path.exists():
            try:
                return path.read_text()[:MAX_MEMORY_CHARS]
            except OSError:
                logger.warning("Failed to read project memory for %s", agent_id)
        return ""

    def update_project_memory(self, agent_id: str, content: str):
        if not self.project_memory_dir:
            logger.warning("No project memory dir set, cannot update for %s", agent_id)
            return
        path = self.project_memory_dir / agent_id / "project.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        logger.info("Updated project memory for %s (%d chars)", agent_id, len(content))

    # ── Combined ──

    def get_combined_memory(self, agent_id: str) -> str:
        """Return both memories formatted for prompt injection."""
        parts = []
        personality = self.get_personality_memory(agent_id)
        if personality:
            parts.append(f"## Your Personality & Skills Memory\n{personality}")
        project = self.get_project_memory(agent_id)
        if project:
            parts.append(f"## Your Project Memory\n{project}")
        return "\n\n".join(parts)
