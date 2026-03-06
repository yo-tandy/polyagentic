from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import (
    DEFAULT_MODEL, CLAUDE_ALLOWED_TOOLS_DEV,
)
from agents.custom_agent import create_custom_agent
from db.repositories.role_repo import RoleRepository

logger = logging.getLogger(__name__)

router = APIRouter()


class AddAgentRequest(BaseModel):
    name: str
    role: str
    system_prompt: str = ""
    model: str = DEFAULT_MODEL
    allowed_tools: str = CLAUDE_ALLOWED_TOOLS_DEV


class RemoveAgentRequest(BaseModel):
    agent_id: str


async def create_and_register_agent(
    name: str,
    role: str,
    system_prompt: str,
    model: str,
    allowed_tools: str,
    registry,
    broker,
    session_store,
    task_board,
    git_manager,
    workspace_path: Path,
    messages_dir: Path,
    worktrees_dir: Path,
    memory_manager=None,
    knowledge_base=None,
    container_manager=None,
    project_store=None,
    team_structure=None,
    action_registry=None,
):
    """Create, configure, register, and start a new custom agent.

    Shared by the REST endpoint and Rory's recruit_agent action.
    When container_manager is provided, the agent runs inside a Docker container.
    Returns the created agent.
    """
    # Build team roster including the new agent
    roster_lines = []
    for a in registry.get_all():
        roster_lines.append(f"- **{a.name}** (id: `{a.agent_id}`): {a.role}")
    roster_lines.append(f"- **{name.replace('_', ' ').title()}** (id: `{name}`): {role}")
    roster = "\n".join(roster_lines)

    # Determine execution mode
    execution_mode = "local"
    container_name = None

    if container_manager:
        # Create worktree first (container mounts it)
        branch = f"dev/{name}"
        worktree_path = None
        try:
            worktree_path = await git_manager.create_worktree(
                name, branch, worktrees_dir
            )
        except RuntimeError as e:
            logger.warning("Could not create worktree for %s: %s", name, e)

        try:
            container_name = await container_manager.create_container(
                name, worktree_path
            )
            execution_mode = "container"
        except RuntimeError as e:
            logger.warning("Could not create container for %s, falling back to local: %s", name, e)

    agent = create_custom_agent(
        name=name,
        role=role,
        system_prompt=system_prompt or f"You are a {role}.",
        model=model,
        allowed_tools=allowed_tools,
        messages_dir=messages_dir,
        working_dir=workspace_path,
        team_roster=roster,
        execution_mode=execution_mode,
        container_name=container_name,
    )

    agent.configure(session_store, broker, task_board, memory_manager, knowledge_base,
                     action_registry=action_registry)
    registry.register(agent)

    if execution_mode == "local":
        # Local mode — create worktree and update working dir
        branch = f"dev/{agent.agent_id}"
        try:
            worktree_path = await git_manager.create_worktree(
                agent.agent_id, branch, worktrees_dir
            )
            agent.working_dir = worktree_path
        except RuntimeError as e:
            logger.warning("Could not create worktree for %s: %s", agent.agent_id, e)

    await agent.start()

    # Notify frontend about the new agent
    await broker.broadcast_event({
        "event_type": "agent_added",
        "data": agent.to_info_dict(),
    })

    # Persist to project-scoped storage
    if project_store:
        active_id = project_store.get_active_project_id()
        if active_id:
            await project_store.add_custom_agent(active_id, {
                "name": name,
                "role": role,
                "system_prompt": system_prompt or f"You are a {role}.",
                "model": model,
                "allowed_tools": allowed_tools,
            })

    # Refresh manager prompts
    refresh_manager_rosters(registry, team_structure=team_structure)

    return agent


def refresh_manager_rosters(registry, team_structure=None):
    """Rebuild the team roster and update all agents with update_team_roster."""
    from core.team_structure import build_fixed_team_roles, build_routing_guide

    roster_lines = []
    for a in registry.get_all():
        roster_lines.append(f"- **{a.name}** (id: `{a.agent_id}`): {a.role}")
    roster = "\n".join(roster_lines)

    team_roles = build_fixed_team_roles(team_structure) if team_structure else ""
    routing_guide = build_routing_guide(team_structure) if team_structure else ""

    for agent in registry.get_all():
        agent.update_team_roster(roster, team_roles=team_roles, routing_guide=routing_guide)
        logger.info("Refreshed team roster for %s (%d agents)", agent.agent_id, len(roster_lines))


@router.get("/config")
async def get_config(request: Request):
    return request.app.state.team_config


@router.get("/config/agents")
async def get_agents_config(request: Request):
    """Return all agents with their configuration."""
    registry = request.app.state.registry
    agents = []
    ts = getattr(request.app.state, "team_structure", None)
    fixed_ids = ts.get_fixed_ids() if ts else {"manny", "rory", "innes", "perry", "jerry"}
    for agent in registry.get_all():
        agents.append({
            "id": agent.agent_id,
            "name": agent.name,
            "role": agent.role,
            "model": agent.model,
            "status": agent.status.value,
            "is_fixed": agent.agent_id in fixed_ids,
            "allowed_tools": agent.allowed_tools,
        })
    return {"agents": agents}


@router.post("/config/agents")
async def add_agent(body: AddAgentRequest, request: Request):
    """Add a new custom agent at runtime."""
    registry = request.app.state.registry

    if registry.get(body.name):
        return JSONResponse({"error": f"Agent '{body.name}' already exists"}, status_code=409)

    # Resolve project-scoped paths
    project_store = request.app.state.project_store
    active_id = project_store.get_active_project_id()
    workspace_path = project_store.get_workspace_dir(active_id)
    messages_dir = project_store.get_messages_dir(active_id)
    worktrees_dir = project_store.get_worktrees_dir(active_id)

    agent = await create_and_register_agent(
        name=body.name,
        role=body.role,
        system_prompt=body.system_prompt,
        model=body.model,
        allowed_tools=body.allowed_tools,
        registry=registry,
        broker=request.app.state.broker,
        session_store=request.app.state.session_store,
        task_board=request.app.state.task_board,
        git_manager=request.app.state.git_manager,
        workspace_path=workspace_path,
        messages_dir=messages_dir,
        worktrees_dir=worktrees_dir,
        memory_manager=getattr(request.app.state, "memory_manager", None),
        knowledge_base=getattr(request.app.state, "knowledge_base", None),
        container_manager=getattr(request.app.state, "container_manager", None),
        project_store=request.app.state.project_store,
        team_structure=getattr(request.app.state, "team_structure", None),
        action_registry=getattr(request.app.state, "action_registry", None),
    )

    logger.info("Added new agent: %s (%s)", body.name, body.role)
    return {"status": "created", "agent": agent.to_info_dict()}


@router.delete("/config/agents/{agent_id}")
async def remove_agent(agent_id: str, request: Request):
    """Remove a custom agent. Fixed agents cannot be removed."""
    registry = request.app.state.registry
    ts = getattr(request.app.state, "team_structure", None)
    fixed_ids = ts.get_fixed_ids() if ts else {"manny", "rory", "innes", "perry", "jerry"}

    if agent_id in fixed_ids:
        return {"error": "Cannot remove fixed agents"}

    agent = registry.get(agent_id)
    if not agent:
        return {"error": f"Agent '{agent_id}' not found"}

    await agent.stop()
    registry._agents.pop(agent_id, None)

    # Remove from project-scoped storage
    project_store = request.app.state.project_store
    if project_store:
        active_id = project_store.get_active_project_id()
        if active_id:
            await project_store.remove_custom_agent(active_id, agent_id)

    ts = getattr(request.app.state, "team_structure", None)
    refresh_manager_rosters(registry, team_structure=ts)

    logger.info("Removed agent: %s", agent_id)
    return {"status": "removed", "agent_id": agent_id}


# ── Config Entry CRUD ─────────────────────────────────────────────────


class ConfigEntryRequest(BaseModel):
    scope: str  # "system" or "agent"
    scope_id: str | None = None  # agent_id for agent-scope entries
    key: str
    value: str
    value_type: str = "string"
    description: str | None = None


class ConfigEntryUpdateRequest(BaseModel):
    value: str
    value_type: str | None = None
    description: str | None = None


@router.get("/config/entries")
async def list_config_entries(
    request: Request,
    scope: str | None = None,
    scope_id: str | None = None,
):
    """List all config entries, optionally filtered by scope."""
    config_provider = getattr(request.app.state, "config_provider", None)
    if not config_provider:
        return JSONResponse({"error": "Config provider not available"}, status_code=503)

    entries = await config_provider._repo.list_all()

    # Apply optional filters
    if scope:
        entries = [e for e in entries if e["scope"] == scope]
    if scope_id:
        entries = [e for e in entries if e.get("scope_id") == scope_id]

    return {"entries": entries}


@router.post("/config/entries")
async def create_config_entry(body: ConfigEntryRequest, request: Request):
    """Create or upsert a config entry."""
    config_provider = getattr(request.app.state, "config_provider", None)
    if not config_provider:
        return JSONResponse({"error": "Config provider not available"}, status_code=503)

    await config_provider._repo.set(
        scope=body.scope,
        key=body.key,
        value=body.value,
        value_type=body.value_type,
        scope_id=body.scope_id,
        description=body.description,
    )

    logger.info(
        "Config entry set: scope=%s scope_id=%s key=%s",
        body.scope, body.scope_id, body.key,
    )
    return {"status": "ok", "scope": body.scope, "scope_id": body.scope_id, "key": body.key}


@router.put("/config/entries/{entry_id}")
async def update_config_entry(entry_id: int, body: ConfigEntryUpdateRequest, request: Request):
    """Update a config entry by ID."""
    config_provider = getattr(request.app.state, "config_provider", None)
    if not config_provider:
        return JSONResponse({"error": "Config provider not available"}, status_code=503)

    repo = config_provider._repo
    # Fetch current entry to get scope/key
    all_entries = await repo.list_all()
    entry = next((e for e in all_entries if e["id"] == entry_id), None)
    if not entry:
        return JSONResponse({"error": f"Config entry {entry_id} not found"}, status_code=404)

    await repo.set(
        scope=entry["scope"],
        key=entry["key"],
        value=body.value,
        value_type=body.value_type or entry["value_type"],
        scope_id=entry.get("scope_id"),
        description=body.description if body.description is not None else entry.get("description"),
    )

    logger.info("Config entry updated: id=%d key=%s", entry_id, entry["key"])
    return {"status": "updated", "id": entry_id}


@router.delete("/config/entries/{entry_id}")
async def delete_config_entry(entry_id: int, request: Request):
    """Delete a config entry by ID."""
    config_provider = getattr(request.app.state, "config_provider", None)
    if not config_provider:
        return JSONResponse({"error": "Config provider not available"}, status_code=503)

    deleted = await config_provider._repo.delete(entry_id)
    if not deleted:
        return JSONResponse({"error": f"Config entry {entry_id} not found"}, status_code=404)

    logger.info("Config entry deleted: id=%d", entry_id)
    return {"status": "deleted", "id": entry_id}


@router.post("/config/reload")
async def reload_config(request: Request):
    """Refresh the in-memory config cache from the database."""
    config_provider = getattr(request.app.state, "config_provider", None)
    if not config_provider:
        return JSONResponse({"error": "Config provider not available"}, status_code=503)

    await config_provider.refresh()
    logger.info("Config cache reloaded via API")
    return {"status": "reloaded"}


# ── Team Structure Management ──────────────────────────────────────────


class TeamAgentDefRequest(BaseModel):
    agent_id: str
    role_id: str = ""
    name: str = ""
    role: str = ""
    description: str = ""
    model: str = "sonnet"
    is_fixed: bool = False
    needs_worktree: bool = True
    prompt_append: str = ""
    allowed_actions: list[str] | None = None
    routing_rules: list[str] = []
    enabled: bool = True
    # Legacy
    class_name: str = ""
    module_path: str = ""
    configure_extras: list[str] = []


class TeamAgentDefUpdateRequest(BaseModel):
    role_id: str | None = None
    name: str | None = None
    role: str | None = None
    description: str | None = None
    model: str | None = None
    is_fixed: bool | None = None
    needs_worktree: bool | None = None
    prompt_append: str | None = None
    allowed_actions: list[str] | None = None
    routing_rules: list[str] | None = None
    enabled: bool | None = None
    # Legacy
    class_name: str | None = None
    module_path: str | None = None
    configure_extras: list[str] | None = None


class TeamMetaUpdateRequest(BaseModel):
    user_facing_agent: str | None = None
    privileged_agents: list[str] | None = None
    checkpoint_agent: str | None = None


def _agent_def_to_dict(agent_def) -> dict:
    """Convert a TeamAgentDef ORM object to a plain dict."""
    return {
        "id": agent_def.id,
        "agent_id": agent_def.agent_id,
        "role_id": getattr(agent_def, "role_id", None),
        "name": agent_def.name,
        "role": agent_def.role,
        "description": agent_def.description,
        "model": agent_def.model,
        "is_fixed": agent_def.is_fixed,
        "needs_worktree": agent_def.needs_worktree,
        "prompt_append": getattr(agent_def, "prompt_append", ""),
        "allowed_actions": getattr(agent_def, "allowed_actions", None),
        "routing_rules": agent_def.routing_rules,
        "enabled": agent_def.enabled,
        # Legacy
        "class_name": getattr(agent_def, "class_name", ""),
        "module_path": getattr(agent_def, "module_path", ""),
        "configure_extras": getattr(agent_def, "configure_extras", []),
    }


def _get_team_repo(request: Request):
    """Retrieve the TeamStructureRepository from app state."""
    return getattr(request.app.state, "team_structure_repo", None)


@router.get("/config/team-structure")
async def get_team_structure(request: Request):
    """Get full team structure: meta + all agent definitions."""
    repo = _get_team_repo(request)
    if not repo:
        return JSONResponse({"error": "Team structure not available"}, status_code=503)

    meta = await repo.get_effective_meta()
    agents = await repo.get_agents()
    return {
        "meta": meta,
        "agents": [_agent_def_to_dict(a) for a in agents],
    }


@router.get("/config/team-structure/agents")
async def list_team_agents(request: Request):
    """List all team agent definitions."""
    repo = _get_team_repo(request)
    if not repo:
        return JSONResponse({"error": "Team structure not available"}, status_code=503)

    agents = await repo.get_agents()
    return {"agents": [_agent_def_to_dict(a) for a in agents]}


@router.post("/config/team-structure/agents")
async def create_team_agent(body: TeamAgentDefRequest, request: Request):
    """Create or update a team agent definition."""
    repo = _get_team_repo(request)
    if not repo:
        return JSONResponse({"error": "Team structure not available"}, status_code=503)

    agent_data = body.model_dump()
    agent_def = await repo.upsert_agent(agent_data)
    logger.info("Team agent def upserted: %s", body.agent_id)
    return {"status": "ok", "agent": _agent_def_to_dict(agent_def)}


@router.put("/config/team-structure/agents/{agent_id}")
async def update_team_agent(agent_id: str, body: TeamAgentDefUpdateRequest, request: Request):
    """Update specific fields of a team agent definition."""
    repo = _get_team_repo(request)
    if not repo:
        return JSONResponse({"error": "Team structure not available"}, status_code=503)

    # Build update dict with only non-None fields
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    updates["agent_id"] = agent_id

    agent_def = await repo.upsert_agent(updates)
    logger.info("Team agent def updated: %s", agent_id)
    return {"status": "updated", "agent": _agent_def_to_dict(agent_def)}


@router.delete("/config/team-structure/agents/{agent_id}")
async def delete_team_agent(agent_id: str, request: Request):
    """Delete a team agent definition."""
    repo = _get_team_repo(request)
    if not repo:
        return JSONResponse({"error": "Team structure not available"}, status_code=503)

    deleted = await repo.delete_agent(agent_id)
    if not deleted:
        return JSONResponse(
            {"error": f"Team agent '{agent_id}' not found"}, status_code=404,
        )

    logger.info("Team agent def deleted: %s", agent_id)
    return {"status": "deleted", "agent_id": agent_id}


@router.get("/config/team-structure/meta")
async def get_team_meta(request: Request):
    """Get team structure metadata (user_facing_agent, privileged_agents, etc.)."""
    repo = _get_team_repo(request)
    if not repo:
        return JSONResponse({"error": "Team structure not available"}, status_code=503)

    meta = await repo.get_effective_meta()
    return meta


@router.put("/config/team-structure/meta")
async def update_team_meta(body: TeamMetaUpdateRequest, request: Request):
    """Update team structure metadata."""
    repo = _get_team_repo(request)
    if not repo:
        return JSONResponse({"error": "Team structure not available"}, status_code=503)

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        return {"status": "no_changes"}

    meta = await repo.upsert_meta(**updates)
    logger.info("Team structure meta updated: %s", list(updates.keys()))
    return {
        "status": "updated",
        "meta": {
            "user_facing_agent": meta.user_facing_agent,
            "privileged_agents": meta.privileged_agents,
            "checkpoint_agent": meta.checkpoint_agent,
        },
    }


# ── Role CRUD ─────────────────────────────────────────────────────────


class RoleRequest(BaseModel):
    role_id: str
    prompt_content: str = ""
    allowed_tools: str = "dev"
    use_session: bool = True
    stateless: bool = False
    max_task_context_items: int | None = 20
    timeout: int = 300
    max_budget_usd: float | None = None
    deps: list[str] = []
    allowed_actions: list[str] = []


class RoleUpdateRequest(BaseModel):
    prompt_content: str | None = None
    allowed_tools: str | None = None
    use_session: bool | None = None
    stateless: bool | None = None
    max_task_context_items: int | None = None
    timeout: int | None = None
    max_budget_usd: float | None = None
    deps: list[str] | None = None
    allowed_actions: list[str] | None = None


def _get_role_repo(request: Request) -> RoleRepository | None:
    return getattr(request.app.state, "role_repo", None)


def _role_to_dict(role) -> dict:
    return {
        "role_id": role.role_id,
        "prompt_content": role.prompt_content,
        "allowed_tools": role.allowed_tools,
        "use_session": role.use_session,
        "stateless": role.stateless,
        "max_task_context_items": role.max_task_context_items,
        "timeout": role.timeout,
        "max_budget_usd": role.max_budget_usd,
        "deps": role.deps,
        "allowed_actions": role.allowed_actions,
    }


@router.get("/config/roles")
async def list_roles(request: Request):
    """List all agent role definitions."""
    repo = _get_role_repo(request)
    if not repo:
        return JSONResponse({"error": "Role repository not available"}, status_code=503)

    roles = await repo.get_all()
    return {"roles": [_role_to_dict(r) for r in roles]}


@router.get("/config/roles/{role_id}")
async def get_role(role_id: str, request: Request):
    """Get a single role definition."""
    repo = _get_role_repo(request)
    if not repo:
        return JSONResponse({"error": "Role repository not available"}, status_code=503)

    role = await repo.get(role_id)
    if not role:
        return JSONResponse({"error": f"Role '{role_id}' not found"}, status_code=404)

    return _role_to_dict(role)


@router.post("/config/roles")
async def create_role(body: RoleRequest, request: Request):
    """Create or update a role definition."""
    repo = _get_role_repo(request)
    if not repo:
        return JSONResponse({"error": "Role repository not available"}, status_code=503)

    data = body.model_dump()
    role_id = data.pop("role_id")
    await repo.upsert(role_id, data)
    logger.info("Role upserted: %s", role_id)
    return {"status": "ok", "role_id": role_id}


@router.put("/config/roles/{role_id}")
async def update_role(role_id: str, body: RoleUpdateRequest, request: Request):
    """Update specific fields of a role definition."""
    repo = _get_role_repo(request)
    if not repo:
        return JSONResponse({"error": "Role repository not available"}, status_code=503)

    existing = await repo.get(role_id)
    if not existing:
        return JSONResponse({"error": f"Role '{role_id}' not found"}, status_code=404)

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        return {"status": "no_changes"}

    await repo.upsert(role_id, updates)
    logger.info("Role updated: %s (%s)", role_id, list(updates.keys()))
    return {"status": "updated", "role_id": role_id}


@router.delete("/config/roles/{role_id}")
async def delete_role(role_id: str, request: Request):
    """Delete a role definition."""
    repo = _get_role_repo(request)
    if not repo:
        return JSONResponse({"error": "Role repository not available"}, status_code=503)

    deleted = await repo.delete(role_id)
    if not deleted:
        return JSONResponse({"error": f"Role '{role_id}' not found"}, status_code=404)

    logger.info("Role deleted: %s", role_id)
    return {"status": "deleted", "role_id": role_id}
