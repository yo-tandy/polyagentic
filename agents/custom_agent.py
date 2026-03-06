"""Factory for dynamically created worker agents.

Delegates to :func:`agents.role_agent.create_role_agent` using the
``engineer`` role from the DB when available, falling back to a
plain Agent with the given system prompt.
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.agent import Agent
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
) -> Agent:
    """Create a dynamically recruited worker agent.

    This is the fallback path used when the engineer role is not
    available in the DB (e.g. before seeding).  Normally the caller
    should prefer :func:`agents.role_agent.create_role_agent` with the
    engineer ``RoleDefinition`` directly.
    """
    identity = f"# {role}\n\n{system_prompt}\n\n"
    full_prompt = identity
    full_prompt = full_prompt.replace("{team_roster}", team_roster)
    full_prompt = full_prompt.replace("{memory}", "No memory recorded yet.")

    agent = Agent(
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
    agent._prompt_template = full_prompt
    return agent
