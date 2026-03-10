"""Generic role-based agent factory.

Replaces all per-agent Python files (manny.py, jerry.py, etc.) with a
single data-driven factory function.  Agent instances are created from
:class:`RoleDefinition` dataclasses loaded from the ``agent_roles`` DB
table.
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.agent import Agent
from config import (
    CLAUDE_ALLOWED_TOOLS_DEV,
    CLAUDE_ALLOWED_TOOLS_READONLY,
    CLAUDE_ALLOWED_TOOLS_NONE,
)

logger = logging.getLogger(__name__)

# Map symbolic tool names to config constants
_TOOLS_MAP = {
    "none": CLAUDE_ALLOWED_TOOLS_NONE,
    "readonly": CLAUDE_ALLOWED_TOOLS_READONLY,
    "dev": CLAUDE_ALLOWED_TOOLS_DEV,
}


def create_role_agent(
    agent_id: str,
    name: str,
    role_def,  # RoleDefinition (from db.repositories.role_repo)
    messages_dir: Path,
    working_dir: Path,
    model: str = "sonnet",
    prompt_append: str = "",
    allowed_actions_override: list[str] | None = None,
    description: str = "",
    provider_name: str | None = None,
    fallback_provider_name: str | None = None,
) -> Agent:
    """Create an Agent instance from a role definition.

    Args:
        agent_id: Unique ID for this agent instance (e.g. "manny").
        name: Display name (e.g. "Manny").
        role_def: RoleDefinition loaded from agent_roles DB table.
        messages_dir: Root directory for inbox/outbox files.
        working_dir: Working directory for Claude subprocess.
        model: Claude model override (default from team_structure).
        prompt_append: Project-specific text appended to role prompt.
        allowed_actions_override: Override role's default action list.
        description: Human-readable description of this instance.
        provider_name: AI provider name (e.g. "claude-cli", "openai").
        fallback_provider_name: Fallback provider if primary fails.

    Returns:
        A fully configured Agent ready for ``configure()`` and ``start()``.
    """
    # Build prompt from role + optional project-specific append
    prompt_template = role_def.prompt_content
    if prompt_append:
        prompt_template = prompt_template + "\n\n" + prompt_append

    # Resolve tools
    allowed_tools = _TOOLS_MAP.get(role_def.allowed_tools, CLAUDE_ALLOWED_TOOLS_DEV)

    # Resolve actions — override wins over role default
    raw_actions = allowed_actions_override if allowed_actions_override is not None else role_def.allowed_actions
    actions = set(raw_actions) if raw_actions else None

    agent = Agent(
        agent_id=agent_id,
        name=name,
        role=role_def.role_id,
        system_prompt=prompt_template,
        model=model,
        allowed_tools=allowed_tools,
        messages_dir=messages_dir,
        working_dir=working_dir,
        timeout=role_def.timeout,
        use_session=role_def.use_session,
        max_budget_usd=role_def.max_budget_usd,
        stateless=role_def.stateless,
        allowed_actions=actions,
    )

    # Store template for re-rendering
    agent._prompt_template = prompt_template
    agent.max_task_context_items = role_def.max_task_context_items

    # Store provider configuration for wiring in main.py
    agent._provider_name = provider_name or getattr(role_def, "provider", "claude-cli") or "claude-cli"
    agent._fallback_provider_name = fallback_provider_name or getattr(role_def, "fallback_provider", None)

    logger.debug(
        "Created agent '%s' (%s) from role '%s' — tools=%s, session=%s, stateless=%s, actions=%d",
        agent_id, name, role_def.role_id,
        role_def.allowed_tools, role_def.use_session, role_def.stateless,
        len(actions) if actions else 0,
    )

    return agent
