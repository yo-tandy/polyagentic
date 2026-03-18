"""Project store — DB-backed with in-memory cache.

Still creates project directories on disk (needed for messages, worktrees,
workspace). But project metadata, custom agents, and active-project state
are all stored in the DB.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from db.repositories.project_repo import ProjectRepository

logger = logging.getLogger(__name__)

KB_CATEGORIES = ["specs", "design", "architecture", "planning", "history"]


class ProjectStore:
    """Manages project lifecycle: create, list, switch, get current."""

    def __init__(self, repo: ProjectRepository, base_dir: Path):
        self._repo = repo
        self.base_dir = base_dir
        self.projects_dir = base_dir / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        # In-memory cache of project dicts
        self._cache: list[dict] = []
        self._active_project_id: str | None = None

    async def load(self) -> None:
        """Load projects from DB into cache."""
        records = await self._repo.list_all()
        self._cache = [self._project_to_dict(r) for r in records]
        active = await self._repo.get_active()
        self._active_project_id = active.id if active else None
        logger.info("Loaded %d projects from DB (active: %s)", len(self._cache), self._active_project_id)

    # ── CRUD ──

    async def create_project(self, name: str, description: str = "") -> dict:
        """Create a new project with isolated directory structure."""
        project_id = _slugify(name)
        if not project_id:
            raise ValueError("Project name must contain at least one alphanumeric character")

        # Ensure unique id
        existing_ids = {p["id"] for p in self._cache}
        if project_id in existing_ids:
            suffix = 2
            while f"{project_id}-{suffix}" in existing_ids:
                suffix += 1
            project_id = f"{project_id}-{suffix}"

        project_dir = self.projects_dir / project_id

        # Create directory tree (still needed for file-based operations)
        for subdir in ["messages", "workspace", "worktrees", "memory", "uploads"]:
            (project_dir / subdir).mkdir(parents=True, exist_ok=True)

        # Write to DB
        rec = await self._repo.create(
            id=project_id, name=name, description=description,
        )
        project_meta = self._project_to_dict(rec)
        self._cache.append(project_meta)
        logger.info("Created project '%s' (id: %s)", name, project_id)
        return project_meta

    def list_projects(self) -> list[dict]:
        return list(self._cache)

    def get_project(self, project_id: str) -> dict | None:
        for p in self._cache:
            if p["id"] == project_id:
                return p
        return None

    def get_active_project_id(self) -> str | None:
        return self._active_project_id

    def get_active_project(self) -> dict | None:
        if self._active_project_id:
            return self.get_project(self._active_project_id)
        return None

    async def set_active_project(self, project_id: str):
        if not self.get_project(project_id):
            raise ValueError(f"Project '{project_id}' not found")
        await self._repo.set_active(project_id)
        self._active_project_id = project_id
        logger.info("Set active project to '%s'", project_id)

    def get_project_dir(self, project_id: str) -> Path:
        return self.projects_dir / project_id

    async def update_project(self, project_id: str, **kwargs) -> dict | None:
        rec = await self._repo.update(project_id, **kwargs)
        if not rec:
            return None
        updated = self._project_to_dict(rec)
        # Update cache
        for i, p in enumerate(self._cache):
            if p["id"] == project_id:
                self._cache[i] = updated
                break
        return updated

    async def delete_project(self, project_id: str) -> bool:
        result = await self._repo.delete(project_id)
        if not result:
            return False
        self._cache = [p for p in self._cache if p["id"] != project_id]
        if self._active_project_id == project_id:
            self._active_project_id = None
        logger.info("Deleted project '%s' from DB", project_id)
        return True

    # ── Custom agents (per-project) ──

    async def get_custom_agents(self, project_id: str) -> list[dict]:
        records = await self._repo.get_custom_agents(project_id)
        return [
            {
                "name": r.name,
                "role": r.role,
                "system_prompt": r.system_prompt,
                "model": r.model,
                "allowed_tools": r.allowed_tools,
            }
            for r in records
        ]

    async def add_custom_agent(self, project_id: str, agent_def: dict):
        await self._repo.add_custom_agent(project_id, agent_def)
        logger.info("Saved custom agent '%s' to project '%s'", agent_def.get("name"), project_id)

    async def remove_custom_agent(self, project_id: str, agent_name: str):
        await self._repo.remove_custom_agent(project_id, agent_name)
        logger.info("Removed custom agent '%s' from project '%s'", agent_name, project_id)

    # ── Path helpers (still file-based) ──

    def get_messages_dir(self, project_id: str) -> Path:
        return self.get_project_dir(project_id) / "messages"

    def get_workspace_dir(self, project_id: str) -> Path:
        return self.get_project_dir(project_id) / "workspace"

    def get_worktrees_dir(self, project_id: str) -> Path:
        return self.get_project_dir(project_id) / "worktrees"

    def get_docs_dir(self, project_id: str) -> Path:
        return self.get_project_dir(project_id) / "docs"

    def get_project_memory_dir(self, project_id: str) -> Path:
        return self.get_project_dir(project_id) / "memory"

    def get_team_structure_path(self, project_id: str) -> Path:
        return self.get_project_dir(project_id) / "team_structure.yaml"

    async def set_running(self, project_id: str, is_running: bool) -> None:
        await self._repo.set_running(project_id, is_running)
        # Update cache
        for p in self._cache:
            if p["id"] == project_id:
                p["is_running"] = is_running
                break

    # ── Helpers ──

    @staticmethod
    def _project_to_dict(rec) -> dict:
        return {
            "id": rec.id,
            "name": rec.name,
            "description": rec.description or "",
            "created_at": rec.created_at.isoformat() if rec.created_at else "",
            "updated_at": rec.updated_at.isoformat() if rec.updated_at else "",
            "status": rec.status or "active",
            "main_branch": rec.main_branch or "main",
            "github_url": rec.github_url,
            "is_running": getattr(rec, "is_running", False) or False,
        }


def _slugify(text: str) -> str:
    """Convert name to a URL-safe project ID."""
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")[:40]
