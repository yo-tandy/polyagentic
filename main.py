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
from core.memory_manager import MemoryManager
from core.knowledge_base import KnowledgeBase
from agents.dev_manager import DevManagerAgent
from agents.project_manager import ProjectManagerAgent
from agents.product_manager import ProductManagerAgent
from agents.integrator import IntegratorAgent
from agents.cicd_engineer import CICDEngineerAgent
from agents.custom_agent import create_custom_agent
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
        """Stop all agents and broker."""
        state = self.current_state
        if not state:
            return
        logger.info("Tearing down current project...")
        for agent in state["registry"].get_all():
            await agent.stop()
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

        # Initialize git
        await git_manager.init_or_validate()

        # Create initial team: product_manager, project_manager, dev_manager
        agents_config = self.team_config.get("agents", {})
        fixed_config = agents_config.get("fixed", {})

        dev_manager = DevManagerAgent(
            model=fixed_config.get("dev_manager", {}).get("model", DEFAULT_MODEL),
            messages_dir=messages_dir,
            working_dir=workspace_path,
        )
        registry.register(dev_manager)

        project_mgr = ProjectManagerAgent(
            model=fixed_config.get("project_manager", {}).get("model", DEFAULT_MODEL),
            messages_dir=messages_dir,
            working_dir=workspace_path,
        )
        registry.register(project_mgr)

        product_mgr = ProductManagerAgent(
            model=fixed_config.get("product_manager", {}).get("model", DEFAULT_MODEL),
            messages_dir=messages_dir,
            working_dir=workspace_path,
        )
        registry.register(product_mgr)

        # Custom agents from team config (if any)
        for agent_def in agents_config.get("custom", []):
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
            agent.configure(session_store, broker, task_board, memory_manager, knowledge_base)

        # Apply per-project model overrides from session store
        for agent in registry.get_all():
            stored_model = session_store.get_model(agent.agent_id)
            if stored_model:
                logger.info("Applying stored model override: %s → %s", agent.agent_id, stored_model)
                agent.model = stored_model

        # Inject team roster (after configure so memory_manager is set)
        roster = build_team_roster(registry)
        dev_manager.update_team_roster(roster)
        project_mgr.update_team_roster(roster)
        product_mgr.update_team_roster(roster)

        # Dev manager extras
        dev_manager.configure_extras(
            registry=registry,
            git_manager=git_manager,
            session_store=session_store,
            workspace_path=workspace_path,
            messages_dir=messages_dir,
            worktrees_dir=worktrees_dir,
        )

        # Create worktrees for non-manager agents
        manager_ids = {"dev_manager", "project_manager", "product_manager"}
        for agent in registry.get_all():
            if agent.agent_id not in manager_ids:
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
            "memory_manager": memory_manager,
            "knowledge_base": knowledge_base,
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
