"""Recruit (create) a new team member agent."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext
from core.message import Message

if TYPE_CHECKING:
    from core.agent import Agent

logger = logging.getLogger(__name__)


class RecruitAgent(BaseAction):

    name = "recruit_agent"
    description = "Recruit (create) a new team member agent."
    allowed_agents = {"rory"}

    fields = [
        ActionField("name", "string", required=True,
                     description="Agent ID (snake_case)"),
        ActionField("role", "string", required=True,
                     description="Agent role"),
        ActionField("system_prompt", "string",
                     description="Custom system prompt"),
        ActionField("model", "string",
                     description="Model to use",
                     enum=["opus", "sonnet", "haiku"]),
        ActionField("allowed_tools", "string",
                     description="Tool permissions"),
    ]

    async def execute(
        self, agent: Agent, action: dict, original_msg: Message,
        ctx: ActionContext,
    ) -> list[Message]:
        registry = getattr(agent, "_registry", None)
        if not registry:
            logger.warning(
                "Agent %s tried recruit/create agent but has no registry",
                agent.agent_id,
            )
            return []

        from web.routes.config import create_and_register_agent
        from config import DEFAULT_MODEL, CLAUDE_ALLOWED_TOOLS_DEV

        name = action.get("name", "")
        role = action.get("role", "")
        sys_prompt = action.get("system_prompt", f"You are a {role}.")

        if not name:
            return []
        if registry.get(name):
            logger.info(
                "Agent %s already exists, skipping recruitment", name,
            )
            return []

        try:
            await create_and_register_agent(
                name=name,
                role=role,
                system_prompt=sys_prompt,
                model=action.get("model", DEFAULT_MODEL),
                allowed_tools=action.get("allowed_tools",
                                         CLAUDE_ALLOWED_TOOLS_DEV),
                registry=registry,
                broker=agent._broker,
                session_store=getattr(agent, "_extra_session_store", None),
                task_board=agent._task_board,
                git_manager=getattr(agent, "_git_manager", None),
                workspace_path=getattr(agent, "_workspace_path", None),
                messages_dir=getattr(agent, "_messages_dir", None),
                worktrees_dir=getattr(agent, "_worktrees_dir", None),
                memory_manager=agent._memory_manager,
                knowledge_base=agent._knowledge_base,
                container_manager=getattr(agent, "_container_manager", None),
                project_store=getattr(agent, "_project_store", None),
                team_structure=getattr(agent, "_team_structure", None),
                action_registry=agent._action_registry,
            )
            logger.info(
                "Agent %s recruited new agent: %s (%s)",
                agent.agent_id, name, role,
            )
        except Exception:
            logger.exception("Failed to recruit agent %s", name)

        return []
