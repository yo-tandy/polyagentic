from __future__ import annotations

import logging
from pathlib import Path

from core.agent import Agent
from core.prompt_loader import load_prompt
from config import CLAUDE_ALLOWED_TOOLS_DEV

logger = logging.getLogger(__name__)


def create_custom_agent(
    name: str,
    role: str,
    system_prompt: str,
    model: str,
    allowed_tools: str,
    messages_dir: Path,
    working_dir: Path,
    team_roster: str = "",
    execution_mode: str = "local",
    container_name: str | None = None,
) -> CustomAgent:
    engineer_base = load_prompt("engineer")
    identity = f"# {role}\n\n{system_prompt}\n\n"
    full_prompt = identity + engineer_base
    full_prompt = full_prompt.replace("{team_roster}", team_roster)
    full_prompt = full_prompt.replace("{memory}", "No memory recorded yet.")
    return CustomAgent(
        agent_id=name,
        name=name.replace("_", " ").title(),
        role=role,
        system_prompt=full_prompt,
        model=model,
        allowed_tools=allowed_tools,
        messages_dir=messages_dir,
        working_dir=working_dir,
        execution_mode=execution_mode,
        container_name=container_name,
    )


class CustomAgent(Agent):
    """Dynamically created worker agent.

    All action handling (respond_to_user, delegate, update_task, etc.)
    is done centrally via the ActionRegistry.
    """
    pass
