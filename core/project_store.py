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

        # Empty tasks, sessions, and agents
        (project_dir / "tasks.json").write_text("{}")
        (project_dir / "sessions.json").write_text("{}")
        (project_dir / "agents.json").write_text(json.dumps({"agents": []}, indent=2))

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

    def update_project(self, project_id: str, **kwargs) -> dict | None:
        """Update project metadata fields (e.g., github_url)."""
        project = self.get_project(project_id)
        if not project:
            return None
        project.update(kwargs)
        project["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._save_registry()

        # Also update on-disk project.json
        meta_path = self.get_project_dir(project_id) / "project.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                meta.update(kwargs)
                meta["updated_at"] = project["updated_at"]
                meta_path.write_text(json.dumps(meta, indent=2))
            except (json.JSONDecodeError, OSError):
                pass
        return project

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

    # ── Custom agents (per-project) ──

    def _agents_path(self, project_id: str) -> Path:
        return self.get_project_dir(project_id) / "agents.json"

    def get_custom_agents(self, project_id: str) -> list[dict]:
        """Return custom agent definitions for a project."""
        path = self._agents_path(project_id)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text())
            return data.get("agents", [])
        except (json.JSONDecodeError, OSError):
            return []

    def save_custom_agents(self, project_id: str, agents: list[dict]):
        """Write the full custom agents list for a project."""
        path = self._agents_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"agents": agents}, indent=2))

    def add_custom_agent(self, project_id: str, agent_def: dict):
        """Append one custom agent to a project (skip if name exists)."""
        agents = self.get_custom_agents(project_id)
        if any(a.get("name") == agent_def.get("name") for a in agents):
            return
        agents.append(agent_def)
        self.save_custom_agents(project_id, agents)
        logger.info("Saved custom agent '%s' to project '%s'", agent_def.get("name"), project_id)

    def remove_custom_agent(self, project_id: str, agent_name: str):
        """Remove one custom agent from a project."""
        agents = self.get_custom_agents(project_id)
        agents = [a for a in agents if a.get("name") != agent_name]
        self.save_custom_agents(project_id, agents)
        logger.info("Removed custom agent '%s' from project '%s'", agent_name, project_id)

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

    def get_team_structure_path(self, project_id: str) -> Path:
        """Return the path for a project-level team structure override."""
        return self.get_project_dir(project_id) / "team_structure.yaml"

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
