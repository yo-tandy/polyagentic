"""Helper functions for managing app.state — avoids circular imports."""

from __future__ import annotations

from fastapi import FastAPI


def apply_project_state(app: FastAPI, state: dict) -> None:
    """Swap all project-scoped app.state references atomically.

    Used when switching the viewed project and during initial lifespan setup.
    """
    app.state.registry = state["registry"]
    app.state.broker = state["broker"]
    app.state.task_board = state["task_board"]
    app.state.git_manager = state["git_manager"]
    app.state.team_config = state["team_config"]
    app.state.session_store = state["session_store"]
    app.state.memory_manager = state.get("memory_manager")
    app.state.knowledge_base = state.get("knowledge_base")
    app.state.container_manager = state.get("container_manager")
    app.state.conversation_manager = state.get("conversation_manager")
    app.state.team_structure = state.get("team_structure")
    app.state.action_registry = state.get("action_registry")
    app.state.phase_board = state.get("phase_board")
    app.state.config_provider = state.get("config_provider")
    app.state.team_structure_repo = state.get("team_structure_repo")
    app.state.role_repo = state.get("role_repo")
    app.state.provider_history_repo = state.get("provider_history_repo")
    app.state.project_id = state.get("project_id")
    app.state.user_repo = state.get("user_repo")
    app.state.org_repo = state.get("org_repo")
    app.state.invite_repo = state.get("invite_repo")
    app.state.mcp_repo = state.get("mcp_repo")
    app.state.mcp_manager = state.get("mcp_manager")
    app.state.mcp_registry = state.get("mcp_registry")
    app.state.action_error_repo = state.get("action_error_repo")
    app.state.template_repo = state.get("template_repo")
