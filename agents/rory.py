from __future__ import annotations

import logging
from pathlib import Path

from core.agent import Agent
from core.prompt_loader import load_prompt
from config import CLAUDE_ALLOWED_TOOLS_DEV

logger = logging.getLogger(__name__)


class RoryAgent(Agent):
    """Robot Resources agent — recruits and configures worker agents.

    Session-based (stateful). The recruit_agent action is handled
    centrally via the ActionRegistry, which accesses _registry,
    _git_manager, etc. set by configure_extras().
    """

    def __init__(self, model: str, messages_dir: Path, working_dir: Path):
        prompt_template = load_prompt("rory")
        self._prompt_template = prompt_template
        super().__init__(
            agent_id="rory",
            name="Rory",
            role="Robot Resources",
            system_prompt=prompt_template,
            model=model,
            allowed_tools=CLAUDE_ALLOWED_TOOLS_DEV,
            messages_dir=messages_dir,
            working_dir=working_dir,
            use_session=True,
        )
        self._register_prompt_files("rory")
        self._registry = None
        self._git_manager = None
        self._extra_session_store = None
        self._workspace_path = None
        self._messages_dir = None
        self._worktrees_dir = None
        self._container_manager = None
        self._project_store = None

    def configure_extras(self, registry, git_manager, session_store, workspace_path,
                         messages_dir=None, worktrees_dir=None, container_manager=None,
                         project_store=None, team_structure=None):
        """Provide dependencies needed for dynamic agent creation."""
        self._registry = registry
        self._git_manager = git_manager
        self._extra_session_store = session_store
        self._workspace_path = workspace_path
        self._messages_dir = messages_dir
        self._worktrees_dir = worktrees_dir
        self._container_manager = container_manager
        self._project_store = project_store
        self._team_structure = team_structure

    def update_team_roster(self, roster_text: str, team_roles: str = "", routing_guide: str = ""):
        """Re-render system prompt with updated team roster."""
        self.system_prompt = self._render_prompt_template(
            self._prompt_template, roster_text, team_roles=team_roles, routing_guide=routing_guide,
        )
