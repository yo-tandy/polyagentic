from __future__ import annotations

import logging
from pathlib import Path

from core.agent import Agent
from core.prompt_loader import load_prompt
from config import CLAUDE_ALLOWED_TOOLS_READONLY

logger = logging.getLogger(__name__)


class PerryAgent(Agent):
    """Product Manager agent — builds specs through user conversations.

    Session-based (stateful). All action handling (respond_to_user,
    delegate, write_document, start_conversation, etc.) is done
    centrally via the ActionRegistry.
    """

    def __init__(self, model: str, messages_dir: Path, working_dir: Path):
        prompt_template = load_prompt("perry")
        self._prompt_template = prompt_template
        super().__init__(
            agent_id="perry",
            name="Perry",
            role="Product Manager",
            system_prompt=prompt_template,
            model=model,
            allowed_tools=CLAUDE_ALLOWED_TOOLS_READONLY,
            messages_dir=messages_dir,
            working_dir=working_dir,
            use_session=True,
        )
        self._register_prompt_files("perry")

    def update_team_roster(self, roster_text: str, team_roles: str = "", routing_guide: str = ""):
        """Re-render system prompt with updated team roster."""
        self.system_prompt = self._render_prompt_template(
            self._prompt_template, roster_text, team_roles=team_roles, routing_guide=routing_guide,
        )
