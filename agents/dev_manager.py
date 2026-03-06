from __future__ import annotations

import logging
from pathlib import Path

from core.agent import Agent
from core.prompt_loader import load_prompt
from config import CLAUDE_ALLOWED_TOOLS_NONE

logger = logging.getLogger(__name__)


class DevManagerAgent(Agent):
    """Development Manager agent — stateless thin router.

    Stateless (use_session=False), no tools, budget-capped.
    Delegates to worker agents. The create_agent action is handled
    centrally via the ActionRegistry.
    """

    max_task_context_items = None  # Full board visibility

    def __init__(self, model: str, messages_dir: Path, working_dir: Path):
        prompt_template = load_prompt("dev_manager")
        self._prompt_template = prompt_template
        self._team_roster: str = ""
        self._team_roles: str = ""
        self._routing_guide: str = ""
        super().__init__(
            agent_id="dev_manager",
            name="Development Manager",
            role="Development Manager",
            system_prompt=prompt_template,
            model=model,
            allowed_tools=CLAUDE_ALLOWED_TOOLS_NONE,
            messages_dir=messages_dir,
            working_dir=working_dir,
            timeout=120,
            use_session=False,
            max_budget_usd=0.25,
        )
        # Extra deps for create_agent flow (set via configure_extras)
        self._registry = None
        self._git_manager = None
        self._extra_session_store = None
        self._workspace_path = None
        self._messages_dir = None
        self._worktrees_dir = None

    def configure_extras(self, registry, git_manager, session_store, workspace_path,
                         messages_dir=None, worktrees_dir=None):
        """Provide extra dependencies needed for dynamic agent creation."""
        self._registry = registry
        self._git_manager = git_manager
        self._extra_session_store = session_store
        self._workspace_path = workspace_path
        self._messages_dir = messages_dir
        self._worktrees_dir = worktrees_dir

    def update_team_roster(self, roster_text: str, team_roles: str = "", routing_guide: str = ""):
        """Re-render system prompt with the current team roster."""
        self._team_roster = roster_text
        self._team_roles = team_roles
        self._routing_guide = routing_guide
        self._render_system_prompt()

    def _render_system_prompt(self):
        """Re-build system prompt from template + roster + roles + routing + memory."""
        self.system_prompt = self._render_prompt_template(
            self._prompt_template,
            roster=self._team_roster or "",
            team_roles=self._team_roles,
            routing_guide=self._routing_guide,
        )

    async def _get_system_prompt_if_first_call(self) -> str | None:
        # Dev manager is stateless — always re-render and send system prompt
        self._render_system_prompt()
        return self.system_prompt
