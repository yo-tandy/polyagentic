from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from web.state_helpers import apply_project_state

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_user(request: Request) -> dict:
    return getattr(request.state, "user", {})


class AgentModelConfig(BaseModel):
    provider: str = "claude-cli"
    model: str = "sonnet"


class CreateProjectRequest(BaseModel):
    name: str
    description: str = ""
    agent_models: dict[str, AgentModelConfig] = {}  # {agent_id: {provider, model}}
    git_url: str = ""  # optional: clone existing repo into workspace


@router.get("/projects")
async def list_projects(request: Request):
    project_store = request.app.state.project_store
    lifecycle = request.app.state.lifecycle_manager
    if not project_store:
        return {"projects": [], "active_project_id": None}

    projects = project_store.list_projects()
    viewed_id = lifecycle.viewed_project_id if lifecycle else None

    # Enrich with running/viewed flags
    for p in projects:
        p["is_running"] = lifecycle.is_running(p["id"]) if lifecycle else False
        p["is_viewed"] = p["id"] == viewed_id

    return {
        "projects": projects,
        "active_project_id": project_store.get_active_project_id(),
    }


@router.post("/projects")
async def create_project(body: CreateProjectRequest, request: Request):
    project_store = request.app.state.project_store
    if not project_store:
        return JSONResponse({"error": "Project store not available"}, status_code=503)
    user = _get_user(request)
    try:
        project = await project_store.create_project(
            body.name, body.description, git_url=body.git_url,
        )
        # Track the creating user
        project["created_by"] = user.get("id", "user")
        return {"status": "created", "project": project}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=500)


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
        return JSONResponse({"error": "Lifecycle manager not available"}, status_code=503)

    project = project_store.get_project(project_id)
    if not project:
        return JSONResponse({"error": f"Project '{project_id}' not found"}, status_code=404)

    try:
        new_state = await lifecycle.activate_project(project_id)
        # Swap app.state to the newly viewed project
        apply_project_state(request.app, new_state)

        logger.info("Switched to project '%s'", project_id)
        return {"status": "activated", "project": project}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        logger.exception("Failed to activate project %s", project_id)
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/projects/{project_id}/link-repo")
async def link_repo(project_id: str, request: Request):
    """Link a git repository to an existing project (stores URL only, no clone)."""
    project_store = request.app.state.project_store
    if not project_store:
        return JSONResponse({"error": "Project store not available"}, status_code=503)

    project = project_store.get_project(project_id)
    if not project:
        return JSONResponse({"error": f"Project '{project_id}' not found"}, status_code=404)

    body = await request.json()
    git_url = body.get("git_url", "").strip()
    if not git_url:
        return JSONResponse({"error": "git_url is required"}, status_code=400)

    try:
        updated = await project_store.update_project(project_id, github_url=git_url)
        if not updated:
            return JSONResponse({"error": "Failed to update project"}, status_code=500)
        return {"status": "linked", "project": updated}
    except Exception as e:
        logger.exception("Failed to link repo to project %s", project_id)
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/projects/{project_id}/stop")
async def stop_project(project_id: str, request: Request):
    """Stop a running background project."""
    lifecycle = request.app.state.lifecycle_manager
    if not lifecycle:
        return JSONResponse({"error": "Lifecycle manager not available"}, status_code=503)

    if not lifecycle.is_running(project_id):
        return JSONResponse({"error": f"Project '{project_id}' is not running"}, status_code=400)

    if lifecycle.viewed_project_id == project_id:
        return JSONResponse(
            {"error": "Cannot stop the currently viewed project. Switch to another project first."},
            status_code=400,
        )

    await lifecycle.stop_project(project_id)
    return {"status": "stopped", "project_id": project_id}


@router.get("/projects/running")
async def list_running_projects(request: Request):
    """Return list of running project IDs and which is viewed."""
    lifecycle = request.app.state.lifecycle_manager
    if not lifecycle:
        return {"running": [], "viewed_project_id": None}
    return {
        "running": lifecycle.get_running_project_ids(),
        "viewed_project_id": lifecycle.viewed_project_id,
    }


@router.get("/projects/dashboard")
async def projects_dashboard(request: Request):
    """Return all projects with aggregated stats for the dashboard."""
    project_store = request.app.state.project_store
    lifecycle = request.app.state.lifecycle_manager
    if not project_store:
        return {"projects": []}

    from db import get_session_factory
    from db.repositories.request_history_repo import RequestHistoryRepository

    sf = get_session_factory()
    history_repo = RequestHistoryRepository(sf)

    projects = project_store.list_projects()
    project_ids = [p["id"] for p in projects]
    viewed_id = lifecycle.viewed_project_id if lifecycle else None

    # Get time-windowed stats for all projects
    all_stats = await history_repo.get_all_projects_stats(project_ids)

    result = []
    for p in projects:
        pid = p["id"]
        is_running = lifecycle.is_running(pid) if lifecycle else False

        # Get agent count from the running project's registry
        agent_count = 0
        if lifecycle and is_running:
            state = lifecycle.get_project_state(pid)
            if state:
                agent_count = len(state["registry"].get_all())

        stats = all_stats.get(pid, {"hour": {}, "day": {}, "overall": {}})

        result.append({
            "id": pid,
            "name": p.get("name", pid),
            "description": p.get("description", ""),
            "created_at": p.get("created_at", ""),
            "is_running": is_running,
            "is_viewed": pid == viewed_id,
            "agent_count": agent_count,
            "stats": stats,
        })

    return {"projects": result}


@router.get("/projects/active/info")
async def get_active_project_info(request: Request):
    """Return aggregated project info: metadata, team, stats, per-model breakdown."""
    project_store = request.app.state.project_store
    registry = request.app.state.registry
    session_store = getattr(request.app.state, "session_store", None)

    project = project_store.get_active_project() if project_store else None
    if not project:
        return {"error": "No active project"}

    # Build per-agent stats
    team = []
    by_model: dict[str, dict] = {}
    totals = {
        "agents": 0,
        "total_requests": 0,
        "total_duration_ms": 0,
        "total_cost_usd": 0.0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
    }

    agents = registry.get_all() if registry else []
    all_sessions = session_store.get_all_info() if session_store else {}

    for agent in agents:
        info = agent.to_info_dict()
        session = all_sessions.get(agent.agent_id, {})

        req_count = session.get("request_count", 0)
        dur_ms = session.get("total_duration_ms", 0)
        cost = session.get("total_cost_usd", 0.0)
        in_tok = session.get("total_input_tokens", 0)
        out_tok = session.get("total_output_tokens", 0)
        err_count = session.get("error_count", 0)
        model = info.get("model", "sonnet")

        agent_entry = {
            "id": info["id"],
            "name": info["name"],
            "role": info["role"],
            "provider": getattr(agent, "_provider_name", "claude-cli"),
            "model": model,
            "status": info["status"],
            "request_count": req_count,
            "error_count": err_count,
            "total_duration_ms": dur_ms,
            "total_cost_usd": round(cost, 4),
            "total_input_tokens": in_tok,
            "total_output_tokens": out_tok,
        }
        team.append(agent_entry)

        # Aggregate totals
        totals["agents"] += 1
        totals["total_requests"] += req_count
        totals["total_duration_ms"] += dur_ms
        totals["total_cost_usd"] += cost
        totals["total_input_tokens"] += in_tok
        totals["total_output_tokens"] += out_tok

        # Aggregate by model
        if model not in by_model:
            by_model[model] = {
                "agents": 0, "requests": 0, "duration_ms": 0,
                "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0,
            }
        by_model[model]["agents"] += 1
        by_model[model]["requests"] += req_count
        by_model[model]["duration_ms"] += dur_ms
        by_model[model]["cost_usd"] += cost
        by_model[model]["input_tokens"] += in_tok
        by_model[model]["output_tokens"] += out_tok

    # Round cost totals
    totals["total_cost_usd"] = round(totals["total_cost_usd"], 4)
    for m in by_model.values():
        m["cost_usd"] = round(m["cost_usd"], 4)

    return {
        "project": project,
        "team": team,
        "totals": totals,
        "by_model": by_model,
    }


@router.get("/projects/{project_id}")
async def get_project(project_id: str, request: Request):
    project_store = request.app.state.project_store
    if not project_store:
        return JSONResponse({"error": "Project store not available"}, status_code=503)
    project = project_store.get_project(project_id)
    if not project:
        return JSONResponse({"error": f"Project '{project_id}' not found"}, status_code=404)
    return project
