#!/usr/bin/env python3
"""Polyagentic - Multi-Agent Development System

Usage: python main.py [--config team_config.yaml] [--port 8000]
"""

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

import yaml
import uvicorn

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    BASE_DIR, TEAM_CONFIG_FILE,
    WEB_HOST, WEB_PORT, DEFAULT_MODEL,
    CLAUDE_ALLOWED_TOOLS_DEV, PROJECTS_DIR, MEMORY_DIR,
)
from core.agent_registry import AgentRegistry
from core.message_broker import MessageBroker
from core.session_store import SessionStore
from core.task_board import TaskBoard
from core.git_manager import GitManager
from core.project_store import ProjectStore
from core.container_manager import ContainerManager
from core.conversation_manager import ConversationManager
from core.memory_manager import MemoryManager
from core.knowledge_base import KnowledgeBase
from core.team_structure import (
    load_team_structure, instantiate_agent,
    build_fixed_team_roles, build_routing_guide,
)
from agents.custom_agent import create_custom_agent
from core.message import Message, MessageType
from web.app import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(BASE_DIR / "logs" / "server.log"),
    ],
)
logger = logging.getLogger("polyagentic")


def load_team_config(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def build_team_roster(registry: AgentRegistry) -> str:
    lines = []
    for agent in registry.get_all():
        lines.append(f"- **{agent.name}** (id: `{agent.agent_id}`): {agent.role}")
    return "\n".join(lines)


class ProjectLifecycleManager:
    """Manages project setup/teardown lifecycle."""

    def __init__(self, project_store: ProjectStore, team_config: dict):
        self.project_store = project_store
        self.team_config = team_config
        self.current_state: dict | None = None
        self._broker_task: asyncio.Task | None = None

    async def activate_project(self, project_id: str) -> dict:
        """Tear down current project state and set up new one."""
        if self.current_state:
            await self._teardown()

        self.project_store.set_active_project(project_id)
        state = await self._setup(project_id)
        self.current_state = state
        return state

    async def _teardown(self):
        """Stop all agents, containers, and broker."""
        state = self.current_state
        if not state:
            return
        logger.info("Tearing down current project...")
        for agent in state["registry"].get_all():
            await agent.stop()
        # Stop Docker containers for worker agents
        cm = state.get("container_manager")
        if cm:
            await cm.stop_all()
        await state["broker"].stop()
        if self._broker_task:
            self._broker_task.cancel()
            try:
                await self._broker_task
            except asyncio.CancelledError:
                pass
            self._broker_task = None
        self.current_state = None

    async def _setup(self, project_id: str) -> dict:
        """Initialize all components for the given project."""
        ps = self.project_store
        project = ps.get_project(project_id)
        if not project:
            raise ValueError(f"Project '{project_id}' not found")

        project_dir = ps.get_project_dir(project_id)
        messages_dir = ps.get_messages_dir(project_id)
        workspace_path = ps.get_workspace_dir(project_id)
        worktrees_dir = ps.get_worktrees_dir(project_id)
        tasks_path = ps.get_tasks_path(project_id)
        sessions_path = ps.get_sessions_path(project_id)
        docs_dir = ps.get_docs_dir(project_id)
        project_memory_dir = ps.get_project_memory_dir(project_id)
        main_branch = project.get("main_branch", "main")

        # Ensure directories
        messages_dir.mkdir(parents=True, exist_ok=True)
        worktrees_dir.mkdir(parents=True, exist_ok=True)
        (BASE_DIR / "logs" / "agents").mkdir(parents=True, exist_ok=True)
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)

        # Core components
        session_store = SessionStore(sessions_path)
        task_board = TaskBoard(tasks_path)
        git_manager = GitManager(workspace_path, main_branch)
        registry = AgentRegistry()
        broker = MessageBroker(messages_dir, registry)
        broker.set_task_board(task_board)

        # Wire task board → WebSocket broadcast on every update
        def _task_update_broadcaster(task_id: str):
            asyncio.ensure_future(broker.broadcast_event({
                "event_type": "task_update",
                "data": {"task_id": task_id},
            }))
        task_board.set_on_update(_task_update_broadcaster)
        memory_manager = MemoryManager(MEMORY_DIR, project_memory_dir)
        knowledge_base = KnowledgeBase(docs_dir)

        # Conversation manager
        conversation_manager = ConversationManager()
        conversation_manager.set_broadcast(broker.broadcast_event)
        broker.set_conversation_manager(conversation_manager)

        # Initialize git
        await git_manager.init_or_validate()

        # Container manager for worker agents
        container_manager = ContainerManager(workspace_path, worktrees_dir, messages_dir)
        try:
            await container_manager.ensure_image()
        except Exception:
            logger.warning("Docker image build failed — containerized agents unavailable")

        # ── Load team structure (global + per-project override) ──
        team_structure = load_team_structure(BASE_DIR, project_dir)

        # Dependency map for configure_extras
        extras_map = {
            "registry": registry,
            "git_manager": git_manager,
            "session_store": session_store,
            "workspace_path": workspace_path,
            "messages_dir": messages_dir,
            "worktrees_dir": worktrees_dir,
            "container_manager": container_manager,
            "project_store": ps,
            "team_structure": team_structure,
        }

        # Apply model overrides from team_config.yaml (backward compat)
        tc_fixed = self.team_config.get("agents", {}).get("fixed", {})

        # ── Create agents from team structure (data-driven) ──
        for agent_id, agent_def in team_structure.get_enabled_agents().items():
            # Allow team_config.yaml to override model (backward compat)
            tc_model = tc_fixed.get(agent_id, {}).get("model")
            if tc_model:
                agent_def.model = tc_model

            agent = instantiate_agent(agent_def, messages_dir, workspace_path)
            registry.register(agent)

        # Custom agents from project-scoped storage
        for agent_def in ps.get_custom_agents(project_id):
            agent = create_custom_agent(
                name=agent_def["name"],
                role=agent_def.get("role", agent_def["name"]),
                system_prompt=agent_def.get("system_prompt", f"You are a {agent_def.get('role', 'developer')}."),
                model=agent_def.get("model", DEFAULT_MODEL),
                allowed_tools=agent_def.get("allowed_tools", CLAUDE_ALLOWED_TOOLS_DEV),
                messages_dir=messages_dir,
                working_dir=workspace_path,
            )
            registry.register(agent)

        # Configure all agents (before roster injection so memory_manager is available)
        for agent in registry.get_all():
            agent.configure(session_store, broker, task_board, memory_manager, knowledge_base, conversation_manager)
            agent._user_facing_agent = team_structure.user_facing_agent

        # Set privileged agents on task board
        task_board.set_privileged_agents(
            {"user"} | set(team_structure.privileged_agents)
        )

        # Set checkpoint agent on broker
        broker.set_checkpoint_agent(team_structure.checkpoint_agent)

        # Apply per-project model overrides from session store
        for agent in registry.get_all():
            stored_model = session_store.get_model(agent.agent_id)
            if stored_model:
                logger.info("Applying stored model override: %s → %s", agent.agent_id, stored_model)
                agent.model = stored_model

        # ── Inject team roster + team roles + routing guide ──
        roster = build_team_roster(registry)
        team_roles = build_fixed_team_roles(team_structure)
        routing_guide = build_routing_guide(team_structure)
        for agent in registry.get_all():
            if hasattr(agent, "update_team_roster"):
                agent.update_team_roster(roster, team_roles=team_roles, routing_guide=routing_guide)

        # ── configure_extras (data-driven from team structure) ──
        for agent_id, agent_def in team_structure.get_enabled_agents().items():
            if not agent_def.configure_extras:
                continue
            agent = registry.get(agent_id)
            if agent and hasattr(agent, "configure_extras"):
                kwargs = {k: extras_map[k] for k in agent_def.configure_extras if k in extras_map}
                agent.configure_extras(**kwargs)

        # ── Create worktrees for agents that need them ──
        no_worktree_ids = team_structure.get_worktree_excluded_ids()
        for agent in registry.get_all():
            if agent.agent_id not in no_worktree_ids:
                branch = f"dev/{agent.agent_id}"
                try:
                    worktree_path = await git_manager.create_worktree(
                        agent.agent_id, branch, worktrees_dir
                    )
                    agent.working_dir = worktree_path
                except RuntimeError as e:
                    logger.warning("Could not create worktree for %s: %s", agent.agent_id, e)

        # Start agents
        for agent in registry.get_all():
            await agent.start()

        # Start broker
        self._broker_task = asyncio.create_task(broker.start())

        # Notify the user-facing agent about the project
        ufa = team_structure.user_facing_agent
        project_desc = project.get("description", "")
        welcome = f"Project '{project.get('name', project_id)}' has been activated."
        if project_desc:
            welcome += f"\n\nProject description:\n{project_desc}"
            welcome += (
                "\n\nAnalyze this project description. Share your initial understanding "
                "with the user — what you think this project is about, key areas of work, "
                "and any immediate questions. Then kick off the Project Lifecycle Flow: "
                "delegate to Perry to start building a product spec by interviewing the user "
                "for deeper requirements. Save your initial understanding to project memory."
            )
        else:
            welcome += (
                "\n\nThis project has no description yet. Greet the user and ask them "
                "to describe what they'd like to build. Use suggested_answers to offer "
                "common project types as starting points."
            )
        welcome_msg = Message(
            sender="system",
            recipient=ufa,
            type=MessageType.SYSTEM,
            content=welcome,
        )
        await broker.deliver(welcome_msg)

        logger.info(
            "Project '%s' activated with %d agents",
            project_id, len(registry.get_all()),
        )

        return {
            "registry": registry,
            "broker": broker,
            "task_board": task_board,
            "git_manager": git_manager,
            "session_store": session_store,
            "team_config": self.team_config,
            "team_structure": team_structure,
            "memory_manager": memory_manager,
            "knowledge_base": knowledge_base,
            "container_manager": container_manager,
            "conversation_manager": conversation_manager,
            "project_id": project_id,
        }


async def run(config_path: Path, host: str, port: int):
    team_config = load_team_config(config_path)

    # Initialize project store
    project_store = ProjectStore(BASE_DIR)

    # Create lifecycle manager
    lifecycle = ProjectLifecycleManager(project_store, team_config)

    # Activate a project
    active_project = project_store.get_active_project()
    if active_project:
        state = await lifecycle.activate_project(active_project["id"])
    else:
        # No projects exist — create a starter project
        project = project_store.create_project("My Project", "A new polyagentic project")
        state = await lifecycle.activate_project(project["id"])

    # Create FastAPI app
    app = create_app(state, project_store=project_store, lifecycle_manager=lifecycle)

    # Run uvicorn
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)

    # Handle shutdown
    def handle_shutdown(sig, frame):
        logger.info("Received shutdown signal (%s)", sig)

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    logger.info("=" * 60)
    logger.info("  POLYAGENTIC - Multi-Agent Development System")
    logger.info("  Dashboard: http://%s:%d", host, port)
    logger.info("  Active project: %s", project_store.get_active_project_id())
    logger.info("  Agents: %d", len(state["registry"].get_all()))
    logger.info("=" * 60)

    try:
        await server.serve()
    finally:
        logger.info("Shutting down...")
        await lifecycle._teardown()
        logger.info("Shutdown complete.")


def main():
    parser = argparse.ArgumentParser(description="Polyagentic Development System")
    parser.add_argument("--config", type=Path, default=TEAM_CONFIG_FILE,
                        help="Path to team config YAML")
    parser.add_argument("--host", type=str, default=WEB_HOST)
    parser.add_argument("--port", type=int, default=WEB_PORT)
    args = parser.parse_args()

    asyncio.run(run(args.config, args.host, args.port))


if __name__ == "__main__":
    main()
