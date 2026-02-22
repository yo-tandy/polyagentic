from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

KB_CATEGORIES = ["specs", "design", "architecture", "planning", "history"]


class ProjectStore:
    """Manages project lifecycle: create, list, switch, get current."""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.projects_dir = base_dir / "projects"
        self.registry_path = base_dir / "projects.json"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self._registry = self._load_registry()

    # ── CRUD ──

    def create_project(self, name: str, description: str = "") -> dict:
        """Create a new project with isolated directory structure."""
        project_id = _slugify(name)
        if not project_id:
            raise ValueError("Project name must contain at least one alphanumeric character")

        # Ensure unique id
        existing_ids = {p["id"] for p in self._registry.get("projects", [])}
        if project_id in existing_ids:
            suffix = 2
            while f"{project_id}-{suffix}" in existing_ids:
                suffix += 1
            project_id = f"{project_id}-{suffix}"

        project_dir = self.projects_dir / project_id
        now = datetime.now(timezone.utc).isoformat()

        # Create directory tree
        for subdir in [
            "messages",
            "workspace",
            "worktrees",
            "memory",
        ]:
            (project_dir / subdir).mkdir(parents=True, exist_ok=True)

        # Knowledge base directories
        for cat in KB_CATEGORIES:
            (project_dir / "docs" / cat).mkdir(parents=True, exist_ok=True)

        # KB index
        kb_index_path = project_dir / "docs" / "_index.json"
        if not kb_index_path.exists():
            kb_index_path.write_text(json.dumps({"documents": []}, indent=2))

        # Project metadata
        project_meta = {
            "id": project_id,
            "name": name,
            "description": description,
            "created_at": now,
            "updated_at": now,
            "status": "active",
            "main_branch": "main",
        }

        meta_path = project_dir / "project.json"
        meta_path.write_text(json.dumps(project_meta, indent=2))

        # Empty tasks and sessions
        (project_dir / "tasks.json").write_text("{}")
        (project_dir / "sessions.json").write_text("{}")

        # Update registry
        self._registry.setdefault("projects", []).append(project_meta)
        self._save_registry()

        logger.info("Created project '%s' (id: %s) at %s", name, project_id, project_dir)
        return project_meta

    def list_projects(self) -> list[dict]:
        return self._registry.get("projects", [])

    def get_project(self, project_id: str) -> dict | None:
        for p in self._registry.get("projects", []):
            if p["id"] == project_id:
                return p
        return None

    def get_active_project_id(self) -> str | None:
        return self._registry.get("active_project_id")

    def get_active_project(self) -> dict | None:
        active_id = self.get_active_project_id()
        if active_id:
            return self.get_project(active_id)
        return None

    def set_active_project(self, project_id: str):
        if not self.get_project(project_id):
            raise ValueError(f"Project '{project_id}' not found")
        self._registry["active_project_id"] = project_id
        self._save_registry()
        logger.info("Set active project to '%s'", project_id)

    def get_project_dir(self, project_id: str) -> Path:
        return self.projects_dir / project_id

    def delete_project(self, project_id: str) -> bool:
        """Remove project from registry (does NOT delete files for safety)."""
        projects = self._registry.get("projects", [])
        original_len = len(projects)
        self._registry["projects"] = [p for p in projects if p["id"] != project_id]
        if len(self._registry["projects"]) == original_len:
            return False
        if self._registry.get("active_project_id") == project_id:
            self._registry["active_project_id"] = None
        self._save_registry()
        logger.info("Deleted project '%s' from registry", project_id)
        return True

    # ── Path helpers ──

    def get_tasks_path(self, project_id: str) -> Path:
        return self.get_project_dir(project_id) / "tasks.json"

    def get_sessions_path(self, project_id: str) -> Path:
        return self.get_project_dir(project_id) / "sessions.json"

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

    # ── Internals ──

    def _load_registry(self) -> dict:
        if self.registry_path.exists():
            try:
                return json.loads(self.registry_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {"active_project_id": None, "projects": []}

    def _save_registry(self):
        self.registry_path.write_text(json.dumps(self._registry, indent=2))


def _slugify(text: str) -> str:
    """Convert name to a URL-safe project ID."""
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")[:40]
