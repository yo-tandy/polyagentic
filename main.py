#!/usr/bin/env python3
"""Polyagentic - Multi-Agent Development System

Usage: python main.py [--config team_config.yaml] [--port 8000]
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

import yaml
import uvicorn

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent))

from config import BASE_DIR, TEAM_CONFIG_FILE, WEB_HOST, WEB_PORT, DEFAULT_MODEL, CLAUDE_ALLOWED_TOOLS_DEV
from core.agent_registry import AgentRegistry
from core.action_registry import create_default_registry
from core.message_broker import MessageBroker
from core.session_store import SessionState, SessionStore
from core.task_board import TaskBoard
from core.phase_board import PhaseBoard
from core.git_manager import GitManager
from core.project_store import ProjectStore
from core.container_manager import ContainerManager
from core.conversation_manager import ConversationManager
from core.memory_manager import MemoryManager
from core.knowledge_base import KnowledgeBase
from core.team_structure import (
    load_team_structure,
    build_fixed_team_roles, build_routing_guide,
)
from agents.role_agent import create_role_agent
from agents.custom_agent import create_custom_agent
from core.message import Message, MessageType
from web.app import create_app

# DB imports
from db import init_db, get_session_factory
from db.config_provider import ConfigProvider
from db.repositories.config_repo import ConfigRepository
from db.repositories.project_repo import ProjectRepository
from db.repositories.session_repo import SessionRepository
from db.repositories.task_repo import TaskRepository
from db.repositories.phase_repo import PhaseRepository
from db.repositories.knowledge_repo import KnowledgeRepository
from db.repositories.memory_repo import MemoryRepository
from db.repositories.conversation_repo import ConversationRepository
from db.repositories.message_repo import MessageRepository
from db.repositories.team_structure_repo import TeamStructureRepository
from db.repositories.role_repo import RoleRepository
from db.repositories.provider_history_repo import ProviderHistoryRepository
from db.repositories.user_repo import UserRepository
from db.repositories.org_repo import OrgRepository
from db.repositories.invite_repo import InviteRepository
from db.repositories.mcp_repo import MCPRepository
from db.repositories.action_error_repo import ActionErrorRepository
from db.repositories.agent_template_repo import AgentTemplateRepository
from core.mcp_registry import MCPRegistry
from core.mcp_manager import MCPManager
from core.providers.factory import create_provider, FallbackProvider

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

    def __init__(
        self,
        project_store: ProjectStore,
        team_config: dict,
        config_provider: ConfigProvider,
        session_factory,
    ):
        self.project_store = project_store
        self.team_config = team_config
        self._config = config_provider
        self._sf = session_factory
        self.current_state: dict | None = None
        self._broker_task: asyncio.Task | None = None

    async def activate_project(self, project_id: str) -> dict:
        """Tear down current project state and set up new one."""
        if self.current_state:
            await self._teardown()

        await self.project_store.set_active_project(project_id)
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
        main_branch = project.get("main_branch", "main")

        # Ensure directories
        messages_dir.mkdir(parents=True, exist_ok=True)
        worktrees_dir.mkdir(parents=True, exist_ok=True)
        (BASE_DIR / "logs" / "agents").mkdir(parents=True, exist_ok=True)

        # ── Create repositories ──
        session_repo = SessionRepository(self._sf)
        task_repo = TaskRepository(self._sf)
        kb_repo = KnowledgeRepository(self._sf)
        memory_repo = MemoryRepository(self._sf)
        conv_repo = ConversationRepository(self._sf)
        message_repo = MessageRepository(self._sf)
        provider_history_repo = ProviderHistoryRepository(self._sf)
        user_repo = UserRepository(self._sf)
        org_repo = OrgRepository(self._sf)
        invite_repo = InviteRepository(self._sf)
        mcp_repo = MCPRepository(self._sf)
        action_error_repo = ActionErrorRepository(self._sf)
        template_repo = AgentTemplateRepository(self._sf)

        # Ensure default org exists
        await org_repo.ensure_default()

        # ── Create DB-backed stores ──
        session_store = SessionStore(session_repo, project_id)
        await session_store.load()

        # Reset paused sessions so agents can work after restart
        for agent_id, info in session_store.get_all_info().items():
            if info.get("state") == "paused":
                await session_store.set_state(agent_id, SessionState.ACTIVE)
                logger.info("Reset paused session for %s to active on startup", agent_id)

        task_board = TaskBoard(task_repo, project_id)
        await task_board.load()

        phase_repo = PhaseRepository(self._sf)
        phase_board = PhaseBoard(phase_repo, project_id)
        await phase_board.load()

        memory_manager = MemoryManager(
            memory_repo,
            project_id=project_id,
            max_chars=self._config.get("MAX_MEMORY_CHARS", 2000),
        )

        repo_docs_dir = workspace_path / "docs"
        knowledge_base = KnowledgeBase(
            kb_repo, project_id,
            repo_docs_dir=repo_docs_dir if repo_docs_dir.is_dir() else None,
            max_summary_docs=self._config.get("MAX_INDEX_SUMMARY_DOCS", 30),
        )
        await knowledge_base.load()

        conversation_manager = ConversationManager(conv_repo, project_id)
        await conversation_manager.load()

        # Load agent-specific config
        for agent_id in ["manny", "dev_manager", "jerry"]:
            await self._config.load_agent(agent_id)

        # ── Non-DB components ──
        git_manager = GitManager(workspace_path, main_branch)
        registry = AgentRegistry()
        broker = MessageBroker(
            messages_dir, registry,
            message_repo=message_repo,
            project_id=project_id,
            config=self._config,
        )
        broker.set_task_board(task_board)

        # Wire task board → WebSocket broadcast on every update
        def _task_update_broadcaster(task_id: str):
            asyncio.ensure_future(broker.broadcast_event({
                "event_type": "task_update",
                "data": {"task_id": task_id},
            }))
        task_board.set_on_update(_task_update_broadcaster)

        # Wire phase board → WebSocket broadcast on every update
        def _phase_update_broadcaster(phase_id: str):
            asyncio.ensure_future(broker.broadcast_event({
                "event_type": "phase_update",
                "data": {"phase_id": phase_id},
            }))
        phase_board.set_on_update(_phase_update_broadcaster)

        # Action registry (centralized action handling)
        action_registry = create_default_registry()
        action_registry.set_error_repo(action_error_repo, project_id)

        # Wire conversation manager
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

        # MCP server management
        mcp_config_dir = project_dir / "mcp_configs"
        mcp_registry = MCPRegistry()
        mcp_manager = MCPManager(
            mcp_repo=mcp_repo,
            container_manager=container_manager,
            project_id=project_id,
            config_dir=mcp_config_dir,
            messages_dir=messages_dir,
        )

        # ── Load team structure (global + per-project override) ──
        team_structure = load_team_structure(BASE_DIR, project_dir)

        # ── Load roles from DB ──
        role_repo = RoleRepository(self._sf)
        await role_repo.seed_defaults_if_empty()
        roles = await role_repo.get_all_as_dict()

        # Dependency map for agent deps injection
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
            "mcp_manager": mcp_manager,
            "mcp_registry": mcp_registry,
            "template_repo": template_repo,
        }

        # Apply model overrides from team_config.yaml (backward compat)
        tc_fixed = self.team_config.get("agents", {}).get("fixed", {})

        # Get default model from config
        default_model = self._config.get("DEFAULT_MODEL", DEFAULT_MODEL)
        allowed_tools_dev = self._config.get("CLAUDE_ALLOWED_TOOLS_DEV", CLAUDE_ALLOWED_TOOLS_DEV)

        # ── Create agents from team structure + roles (data-driven) ──
        for agent_id, agent_def in team_structure.get_enabled_agents().items():
            tc_model = tc_fixed.get(agent_id, {}).get("model")
            model = tc_model or agent_def.model

            role_def = roles.get(agent_def.role_id)
            if role_def:
                # Resolve provider: team_structure override > role default
                agent_provider = getattr(agent_def, "provider", None) or role_def.provider or "claude-cli"
                agent_fallback = getattr(agent_def, "fallback_provider", None) or role_def.fallback_provider

                # Role-based instantiation (new path)
                agent = create_role_agent(
                    agent_id=agent_id,
                    name=agent_def.name,
                    role_def=role_def,
                    messages_dir=messages_dir,
                    working_dir=workspace_path,
                    model=model,
                    prompt_append=agent_def.prompt_append or "",
                    allowed_actions_override=agent_def.allowed_actions,
                    description=agent_def.description,
                    provider_name=agent_provider,
                    fallback_provider_name=agent_fallback,
                )
                # Inject deps from role definition
                for dep_name in role_def.deps:
                    if dep_name in extras_map:
                        agent.deps[dep_name] = extras_map[dep_name]
            else:
                # Legacy fallback: no role_id set, try old-style class import
                logger.warning(
                    "Agent '%s' has no role_id, falling back to legacy instantiation",
                    agent_id,
                )
                import importlib
                module = importlib.import_module(agent_def.module_path)
                cls = getattr(module, agent_def.class_name)
                agent = cls(model=model, messages_dir=messages_dir, working_dir=workspace_path)
                # Legacy configure_extras
                if agent_def.configure_extras and hasattr(agent, "configure_extras"):
                    kwargs = {k: extras_map[k] for k in agent_def.configure_extras if k in extras_map}
                    agent.configure_extras(**kwargs)

            registry.register(agent)

        # Custom agents from project-scoped storage
        for agent_def_dict in await ps.get_custom_agents(project_id):
            engineer_role = roles.get("engineer")
            if engineer_role:
                agent = create_role_agent(
                    agent_id=agent_def_dict["name"],
                    name=agent_def_dict["name"].replace("_", " ").title(),
                    role_def=engineer_role,
                    messages_dir=messages_dir,
                    working_dir=workspace_path,
                    model=agent_def_dict.get("model", default_model),
                    prompt_append=agent_def_dict.get("system_prompt", ""),
                )
            else:
                agent = create_custom_agent(
                    name=agent_def_dict["name"],
                    role=agent_def_dict.get("role", agent_def_dict["name"]),
                    system_prompt=agent_def_dict.get("system_prompt", f"You are a {agent_def_dict.get('role', 'developer')}."),
                    model=agent_def_dict.get("model", default_model),
                    allowed_tools=agent_def_dict.get("allowed_tools", allowed_tools_dev),
                    messages_dir=messages_dir,
                    working_dir=workspace_path,
                )
            registry.register(agent)

        # ── Wire AI providers per agent ──
        # Resolve API keys: DB config first, then env vars
        def _get_api_key(provider_name: str) -> str | None:
            key_map = {
                "claude-api": "ANTHROPIC_API_KEY",
                "openai": "OPENAI_API_KEY",
                "gemini": "GOOGLE_API_KEY",
            }
            env_key = key_map.get(provider_name)
            if not env_key:
                return None
            # Check DB config first
            db_val = self._config.get(env_key, "")
            if db_val:
                return db_val
            return os.environ.get(env_key, "")

        for agent in registry.get_all():
            prov_name = getattr(agent, "_provider_name", "claude-cli")
            fb_name = getattr(agent, "_fallback_provider_name", None)

            if prov_name != "claude-cli":
                try:
                    primary = create_provider(
                        prov_name,
                        api_key=_get_api_key(prov_name),
                        history_repo=provider_history_repo,
                        project_id=project_id,
                        agent_id=agent.agent_id,
                    )
                    if fb_name:
                        fallback = create_provider(
                            fb_name,
                            api_key=_get_api_key(fb_name),
                            history_repo=provider_history_repo,
                            project_id=project_id,
                            agent_id=agent.agent_id,
                            subprocess_mgr=agent._subprocess if fb_name == "claude-cli" else None,
                        )
                        agent.set_provider(FallbackProvider(primary, fallback))
                    else:
                        agent.set_provider(primary)
                    logger.info(
                        "Agent '%s' using provider '%s'%s",
                        agent.agent_id, prov_name,
                        f" (fallback: {fb_name})" if fb_name else "",
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to create provider '%s' for agent '%s': %s — keeping claude-cli",
                        prov_name, agent.agent_id, e,
                    )

        # Configure all agents
        for agent in registry.get_all():
            agent.configure(session_store, broker, task_board, memory_manager, knowledge_base, conversation_manager, action_registry, phase_board=phase_board)
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
            agent.update_team_roster(roster, team_roles=team_roles, routing_guide=routing_guide)

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

        # Restore MCP configs for agents that have installed servers
        for agent in registry.get_all():
            config_path = mcp_manager.get_mcp_config_path(agent.agent_id)
            if config_path:
                agent.mcp_config_path = config_path
                logger.info("Restored MCP config for agent %s: %s", agent.agent_id, config_path)

        # Start agents
        for agent in registry.get_all():
            await agent.start()

        # Start broker
        self._broker_task = asyncio.create_task(broker.start())

        # Notify the user-facing agent about the project
        ufa = team_structure.user_facing_agent
        project_desc = project.get("description", "")
        is_reactivation = len(task_board.list_tasks()) > 0

        if is_reactivation:
            # Re-activation: lightweight message, don't re-trigger lifecycle flow
            welcome = (
                f"Project '{project.get('name', project_id)}' is back online (server restart). "
                "IMPORTANT: Do NOT create any new tickets. Do NOT delegate status rollups. "
                "Do NOT delegate any tasks. The board already has existing tasks — "
                "review them and resume coordination. "
                "Greet the user and briefly summarize current board state."
            )
        else:
            # First activation: full lifecycle kickoff
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

        # Team structure repository for API management
        team_structure_repo = TeamStructureRepository(self._sf)

        return {
            "registry": registry,
            "broker": broker,
            "task_board": task_board,
            "git_manager": git_manager,
            "session_store": session_store,
            "team_config": self.team_config,
            "team_structure": team_structure,
            "team_structure_repo": team_structure_repo,
            "memory_manager": memory_manager,
            "knowledge_base": knowledge_base,
            "container_manager": container_manager,
            "conversation_manager": conversation_manager,
            "action_registry": action_registry,
            "phase_board": phase_board,
            "project_id": project_id,
            "config_provider": self._config,
            "role_repo": role_repo,
            "provider_history_repo": provider_history_repo,
            "user_repo": user_repo,
            "org_repo": org_repo,
            "invite_repo": invite_repo,
            "mcp_repo": mcp_repo,
            "mcp_manager": mcp_manager,
            "mcp_registry": mcp_registry,
            "action_error_repo": action_error_repo,
            "template_repo": template_repo,
        }


async def run(config_path: Path, host: str, port: int):
    team_config = load_team_config(config_path)

    # ── 1. Initialize database ──
    await init_db()
    sf = get_session_factory()

    # ── 2. Seed config defaults + load config provider ──
    config_repo = ConfigRepository(sf)
    config_provider = ConfigProvider(config_repo)
    await config_provider.seed_defaults()
    await config_provider.load()

    # ── 3. Initialize project store (DB-backed) ──
    project_repo = ProjectRepository(sf)
    project_store = ProjectStore(project_repo, BASE_DIR)
    await project_store.load()

    # ── 4. Create lifecycle manager ──
    lifecycle = ProjectLifecycleManager(
        project_store, team_config, config_provider, sf,
    )

    # Activate a project
    active_project = project_store.get_active_project()
    if active_project:
        state = await lifecycle.activate_project(active_project["id"])
    else:
        # No projects exist — create a starter project
        project = await project_store.create_project("My Project", "A new polyagentic project")
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
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    # PORT env var (set by autoPort in launch.json) takes priority over --port flag
    port = int(os.environ.get("PORT", 0)) or args.port or WEB_PORT

    asyncio.run(run(args.config, args.host, port))


if __name__ == "__main__":
    main()
