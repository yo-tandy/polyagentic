from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

from config import (
    DEFAULT_MODEL, CLAUDE_ALLOWED_TOOLS_DEV,
)
from agents.custom_agent import create_custom_agent

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

    agent.configure(session_store, broker, task_board, memory_manager, knowledge_base)
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
            project_store.add_custom_agent(active_id, {
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
        if hasattr(agent, "update_team_roster"):
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
        return {"error": f"Agent '{body.name}' already exists"}, 409

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
            project_store.remove_custom_agent(active_id, agent_id)

    ts = getattr(request.app.state, "team_structure", None)
    refresh_manager_rosters(registry, team_structure=ts)

    logger.info("Removed agent: %s", agent_id)
    return {"status": "removed", "agent_id": agent_id}


