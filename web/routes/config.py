from __future__ import annotations

import logging
from pathlib import Path

import yaml
from fastapi import APIRouter, Request
from pydantic import BaseModel

from config import (
    TEAM_CONFIG_FILE,
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
):
    """Create, configure, register, and start a new custom agent.

    Shared by the REST endpoint and the dev_manager's create_agent action.
    Returns the created agent.
    """
    # Build team roster including the new agent
    roster_lines = []
    for a in registry.get_all():
        roster_lines.append(f"- **{a.name}** (id: `{a.agent_id}`): {a.role}")
    roster_lines.append(f"- **{name.replace('_', ' ').title()}** (id: `{name}`): {role}")
    roster = "\n".join(roster_lines)

    agent = create_custom_agent(
        name=name,
        role=role,
        system_prompt=system_prompt or f"You are a {role}.",
        model=model,
        allowed_tools=allowed_tools,
        messages_dir=messages_dir,
        working_dir=workspace_path,
        team_roster=roster,
    )

    agent.configure(session_store, broker, task_board, memory_manager, knowledge_base)
    registry.register(agent)

    # Create worktree
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

    # Persist to YAML
    _save_agent_to_config_file(name, role, system_prompt, model, allowed_tools)

    # Refresh manager prompts
    refresh_manager_rosters(registry)

    return agent


def refresh_manager_rosters(registry):
    """Rebuild the team roster and update dev_manager + project_manager prompts."""
    roster_lines = []
    for a in registry.get_all():
        roster_lines.append(f"- **{a.name}** (id: `{a.agent_id}`): {a.role}")
    roster = "\n".join(roster_lines)

    for mgr_id in ("dev_manager", "project_manager", "product_manager"):
        mgr = registry.get(mgr_id)
        if mgr and hasattr(mgr, "update_team_roster"):
            mgr.update_team_roster(roster)
            logger.info("Refreshed team roster for %s (%d agents)", mgr_id, len(roster_lines))


@router.get("/config")
async def get_config(request: Request):
    return request.app.state.team_config


@router.get("/config/agents")
async def get_agents_config(request: Request):
    """Return all agents with their configuration."""
    registry = request.app.state.registry
    agents = []
    fixed_ids = {"dev_manager", "project_manager", "product_manager"}
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
    )

    logger.info("Added new agent: %s (%s)", body.name, body.role)
    return {"status": "created", "agent": agent.to_info_dict()}


@router.delete("/config/agents/{agent_id}")
async def remove_agent(agent_id: str, request: Request):
    """Remove a custom agent. Fixed agents cannot be removed."""
    registry = request.app.state.registry
    fixed_ids = {"dev_manager", "project_manager", "product_manager"}

    if agent_id in fixed_ids:
        return {"error": "Cannot remove fixed agents"}

    agent = registry.get(agent_id)
    if not agent:
        return {"error": f"Agent '{agent_id}' not found"}

    await agent.stop()
    registry._agents.pop(agent_id, None)

    _remove_agent_from_config(agent_id)
    refresh_manager_rosters(registry)

    logger.info("Removed agent: %s", agent_id)
    return {"status": "removed", "agent_id": agent_id}


def _save_agent_to_config_file(name, role, system_prompt, model, allowed_tools):
    """Append agent to team_config.yaml."""
    try:
        with open(TEAM_CONFIG_FILE) as f:
            config = yaml.safe_load(f) or {}

        custom = config.setdefault("agents", {}).setdefault("custom", [])
        if not any(a.get("name") == name for a in custom):
            custom.append({
                "name": name,
                "role": role,
                "system_prompt": system_prompt or f"You are a {role}.",
                "model": model,
                "allowed_tools": allowed_tools,
            })

        with open(TEAM_CONFIG_FILE, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    except Exception:
        logger.exception("Failed to save agent to config file")


def _remove_agent_from_config(agent_id: str):
    """Remove agent from team_config.yaml."""
    try:
        with open(TEAM_CONFIG_FILE) as f:
            config = yaml.safe_load(f) or {}

        custom = config.get("agents", {}).get("custom", [])
        config["agents"]["custom"] = [a for a in custom if a.get("name") != agent_id]

        with open(TEAM_CONFIG_FILE, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    except Exception:
        logger.exception("Failed to remove agent from config file")
