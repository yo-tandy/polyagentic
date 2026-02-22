from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


class CreateProjectRequest(BaseModel):
    name: str
    description: str = ""


@router.get("/projects")
async def list_projects(request: Request):
    project_store = request.app.state.project_store
    if not project_store:
        return {"projects": [], "active_project_id": None}
    return {
        "projects": project_store.list_projects(),
        "active_project_id": project_store.get_active_project_id(),
    }


@router.post("/projects")
async def create_project(body: CreateProjectRequest, request: Request):
    project_store = request.app.state.project_store
    if not project_store:
        return {"error": "Project store not available"}, 500
    try:
        project = project_store.create_project(body.name, body.description)
        return {"status": "created", "project": project}
    except ValueError as e:
        return {"error": str(e)}, 400


@router.get("/projects/active")
async def get_active_project(request: Request):
    project_store = request.app.state.project_store
    if not project_store:
        return {"project": None}
    project = project_store.get_active_project()
    return {"project": project}


@router.post("/projects/{project_id}/activate")
async def activate_project(project_id: str, request: Request):
    lifecycle = request.app.state.lifecycle_manager
    project_store = request.app.state.project_store
    if not lifecycle or not project_store:
        return {"error": "Lifecycle manager not available"}, 500

    project = project_store.get_project(project_id)
    if not project:
        return {"error": f"Project '{project_id}' not found"}, 404

    try:
        new_state = await lifecycle.activate_project(project_id)
        # Update app state references
        request.app.state.registry = new_state["registry"]
        request.app.state.broker = new_state["broker"]
        request.app.state.task_board = new_state["task_board"]
        request.app.state.git_manager = new_state["git_manager"]
        request.app.state.session_store = new_state["session_store"]
        request.app.state.memory_manager = new_state.get("memory_manager")
        request.app.state.knowledge_base = new_state.get("knowledge_base")

        logger.info("Switched to project '%s'", project_id)
        return {"status": "activated", "project": project}
    except Exception as e:
        logger.exception("Failed to activate project %s", project_id)
        return {"error": str(e)}, 500


@router.get("/projects/{project_id}")
async def get_project(project_id: str, request: Request):
    project_store = request.app.state.project_store
    if not project_store:
        return {"error": "Project store not available"}, 500
    project = project_store.get_project(project_id)
    if not project:
        return {"error": f"Project '{project_id}' not found"}, 404
    return project
