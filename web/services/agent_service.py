"""Agent lifecycle service — create, remove, and roster management.

Extracted from web/routes/config.py to separate business logic from
HTTP route handling.
"""

from __future__ import annotations

import logging
from pathlib import Path

from agents.custom_agent import create_custom_agent

logger = logging.getLogger(__name__)


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


async def remove_agent(
    agent_id: str,
    registry,
    project_store=None,
    team_structure=None,
) -> dict:
    """Stop and remove a custom agent.

    Returns a dict with ``"status"`` and ``"agent_id"`` on success,
    or ``"error"`` on failure.
    """
    fixed_ids = (
        team_structure.get_fixed_ids() if team_structure
        else {"manny", "rory", "innes", "perry", "jerry"}
    )

    if agent_id in fixed_ids:
        return {"error": "Cannot remove fixed agents"}

    agent = registry.get(agent_id)
    if not agent:
        return {"error": f"Agent '{agent_id}' not found"}

    await agent.stop()
    registry._agents.pop(agent_id, None)

    # Remove from project-scoped storage
    if project_store:
        active_id = project_store.get_active_project_id()
        if active_id:
            await project_store.remove_custom_agent(active_id, agent_id)

    refresh_manager_rosters(registry, team_structure=team_structure)

    logger.info("Removed agent: %s", agent_id)
    return {"status": "removed", "agent_id": agent_id}
