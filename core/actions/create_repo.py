"""Create a GitHub repository."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext
from core.message import Message, MessageType

if TYPE_CHECKING:
    from core.agent import Agent

logger = logging.getLogger(__name__)


class CreateRepo(BaseAction):

    name = "create_repo"
    description = "Create a GitHub repository."

    fields = [
        ActionField("name", "string", required=True,
                     description="Repository name"),
        ActionField("description", "string",
                     description="Repo description"),
        ActionField("private", "boolean",
                     description="Private repo?", default=True),
    ]

    async def execute(
        self, agent: Agent, action: dict, original_msg: Message,
        ctx: ActionContext,
    ) -> list[Message]:
        git_manager = agent.deps.get("git_manager")
        if not git_manager:
            return [Message(
                sender=agent.agent_id,
                recipient=original_msg.sender,
                type=MessageType.RESPONSE,
                content="Cannot create repository: "
                        "git manager not available.",
                task_id=original_msg.task_id,
                parent_message_id=original_msg.id,
            )]

        repo_name = action.get("name", "unknown")
        description = action.get("description", "")
        private = action.get("private", True)

        try:
            result = await git_manager.create_github_repo(
                name=repo_name, description=description, private=private,
            )
            project_store = agent.deps.get("project_store")
            if project_store:
                active_id = project_store.get_active_project_id()
                if active_id:
                    await project_store.update_project(
                        active_id, github_url=result["url"],
                    )
            return [Message(
                sender=agent.agent_id,
                recipient="user",
                type=MessageType.CHAT,
                content=f"Created GitHub repository: {result['url']}",
                task_id=original_msg.task_id,
                parent_message_id=original_msg.id,
            )]
        except RuntimeError as e:
            logger.error("create_repo failed: %s", e)
            return [Message(
                sender=agent.agent_id,
                recipient=original_msg.sender,
                type=MessageType.RESPONSE,
                content=f"Failed to create repository: {e}",
                task_id=original_msg.task_id,
                parent_message_id=original_msg.id,
            )]
