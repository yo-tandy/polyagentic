from __future__ import annotations

import logging
from pathlib import Path

from core.agent import Agent
from core.prompt_loader import load_prompt
from config import CLAUDE_ALLOWED_TOOLS_NONE

logger = logging.getLogger(__name__)


class MannyAgent(Agent):
    """Manager agent — stateless thin router.

    Receives user requests, delegates to Rory/Innes/Perry/Jerry/workers.
    Stateless (use_session=False), no tools, budget-capped. Re-renders
    system prompt every call via _get_system_prompt_if_first_call.

    All action handling (delegate, respond_to_user, pause_task,
    start_task, etc.) is done centrally via the ActionRegistry.
    """

    max_task_context_items = None  # Full board visibility

    def __init__(self, model: str, messages_dir: Path, working_dir: Path):
        prompt_template = load_prompt("manny")
        self._prompt_template = prompt_template
        self._team_roster: str = ""
        super().__init__(
            agent_id="manny",
            name="Manny",
            role="Manager",
            system_prompt=prompt_template,
            model=model,
            allowed_tools=CLAUDE_ALLOWED_TOOLS_NONE,
            messages_dir=messages_dir,
            working_dir=working_dir,
            timeout=120,
            use_session=False,
            max_budget_usd=0.25,
        )
        self._register_prompt_files("manny")
        self._registry = None

    def configure_extras(self, registry, **kwargs):
        """Provide registry for delegation checks."""
        self._registry = registry

    def update_team_roster(self, roster_text: str, team_roles: str = "", routing_guide: str = ""):
        """Re-render system prompt with updated team roster."""
        self._team_roster = roster_text
        self._team_roles = team_roles
        self._routing_guide = routing_guide
        self._render_system_prompt()

    def _render_system_prompt(self):
        """Re-build system prompt from template + roster + roles + routing + memory."""
        self.system_prompt = self._render_prompt_template(
            self._prompt_template,
            roster=self._team_roster or "",
            team_roles=getattr(self, "_team_roles", ""),
            routing_guide=getattr(self, "_routing_guide", ""),
        )

    async def _get_system_prompt_if_first_call(self) -> str | None:
        # Stateless — always re-render and send full system prompt
        self._check_prompt_hot_reload()
        self._render_system_prompt()
        return self.system_prompt
