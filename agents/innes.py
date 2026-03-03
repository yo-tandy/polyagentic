from __future__ import annotations

import logging
from pathlib import Path

from core.agent import Agent
from core.message import Message, MessageType
from core.prompt_loader import load_prompt
from config import CLAUDE_ALLOWED_TOOLS_DEV

logger = logging.getLogger(__name__)


class InnesAgent(Agent):
    """Integrator agent — manages repos, PRs, and code quality.

    Session-based (stateful). GitHub actions (create_repo, review_pr, merge_pr)
    are stubbed in Phase 1 and will be wired to git_manager in Phase 4.
    """

    def __init__(self, model: str, messages_dir: Path, working_dir: Path):
        prompt_template = load_prompt("innes")
        self._prompt_template = prompt_template
        super().__init__(
            agent_id="innes",
            name="Innes",
            role="Integrator",
            system_prompt=prompt_template,
            model=model,
            allowed_tools=CLAUDE_ALLOWED_TOOLS_DEV,
            messages_dir=messages_dir,
            working_dir=working_dir,
            use_session=True,
        )
        self._git_manager = None
        self._project_store = None

    def configure_extras(self, git_manager, project_store=None, **kwargs):
        """Provide git_manager and project_store for GitHub operations."""
        self._git_manager = git_manager
        self._project_store = project_store

    def update_team_roster(self, roster_text: str, team_roles: str = "", routing_guide: str = ""):
        """Re-render system prompt with updated team roster."""
        self.system_prompt = self._render_prompt_template(
            self._prompt_template, roster_text, team_roles=team_roles, routing_guide=routing_guide,
        )

    async def _parse_response(self, result_text: str, original_msg: Message) -> list[Message]:
        messages = []
        actions = self._extract_actions(result_text)

        # Handle common actions (memory, KB, update_task)
        await self._handle_common_actions(actions)

        if not actions:
            messages.append(Message(
                sender=self.agent_id,
                recipient=original_msg.sender,
                type=MessageType.RESPONSE,
                content=result_text,
                task_id=original_msg.task_id,
                parent_message_id=original_msg.id,
            ))
            return messages

        for action in actions:
            action_type = action.get("action")

            if action_type == "create_repo":
                repo_name = action.get("name", "unknown")
                description = action.get("description", "")
                private = action.get("private", True)
                try:
                    result = await self._git_manager.create_github_repo(
                        name=repo_name, description=description, private=private,
                    )
                    # Store URL in project metadata
                    if self._project_store:
                        active_id = self._project_store.get_active_project_id()
                        if active_id:
                            self._project_store.update_project(active_id, github_url=result["url"])
                    messages.append(Message(
                        sender=self.agent_id,
                        recipient="user",
                        type=MessageType.CHAT,
                        content=f"Created GitHub repository: {result['url']}",
                        task_id=original_msg.task_id,
                        parent_message_id=original_msg.id,
                    ))
                except RuntimeError as e:
                    logger.error("Innes: create_repo failed: %s", e)
                    messages.append(Message(
                        sender=self.agent_id,
                        recipient=original_msg.sender,
                        type=MessageType.RESPONSE,
                        content=f"Failed to create repository: {e}",
                        task_id=original_msg.task_id,
                        parent_message_id=original_msg.id,
                    ))

            elif action_type == "review_pr":
                pr_number = action.get("pr_number")
                if pr_number and self._git_manager:
                    pr_data = await self._git_manager.get_pull_request(int(pr_number))
                    verdict = action.get("verdict", "pending")
                    review_msg = action.get("message", f"PR #{pr_number} reviewed: {verdict}")
                    messages.append(Message(
                        sender=self.agent_id,
                        recipient="user",
                        type=MessageType.CHAT,
                        content=review_msg,
                        task_id=original_msg.task_id,
                        parent_message_id=original_msg.id,
                    ))

            elif action_type == "merge_pr":
                pr_number = action.get("pr_number")
                method = action.get("method", "squash")
                if pr_number and self._git_manager:
                    try:
                        result = await self._git_manager.merge_pull_request(
                            int(pr_number), method=method,
                        )
                        messages.append(Message(
                            sender=self.agent_id,
                            recipient="user",
                            type=MessageType.CHAT,
                            content=f"Merged PR #{pr_number} via {method}.",
                            task_id=original_msg.task_id,
                            parent_message_id=original_msg.id,
                        ))
                    except RuntimeError as e:
                        logger.error("Innes: merge_pr failed: %s", e)
                        messages.append(Message(
                            sender=self.agent_id,
                            recipient=original_msg.sender,
                            type=MessageType.RESPONSE,
                            content=f"Failed to merge PR #{pr_number}: {e}",
                            task_id=original_msg.task_id,
                            parent_message_id=original_msg.id,
                        ))

            elif action_type == "request_changes":
                pr_number = action.get("pr_number", "?")
                feedback = action.get("message", "Changes requested.")
                logger.info("Innes: request_changes on PR #%s", pr_number)
                messages.append(Message(
                    sender=self.agent_id,
                    recipient=original_msg.sender,
                    type=MessageType.RESPONSE,
                    content=f"Changes requested on PR #{pr_number}: {feedback}",
                    task_id=original_msg.task_id,
                    parent_message_id=original_msg.id,
                ))

            elif action_type == "delegate":
                messages.append(Message(
                    sender=self.agent_id,
                    recipient=action.get("to", ""),
                    type=MessageType.TASK,
                    content=action.get("task_description", ""),
                    task_id=original_msg.task_id,
                    parent_message_id=original_msg.id,
                    metadata={"task_title": action.get("task_title", "")},
                ))

            elif action_type == "respond_to_user":
                suggested = action.get("suggested_answers", [])
                meta = {}
                if suggested:
                    meta["suggested_answers"] = suggested[:3]
                messages.append(Message(
                    sender=self.agent_id,
                    recipient="user",
                    type=MessageType.CHAT,
                    content=action.get("message", result_text),
                    task_id=original_msg.task_id,
                    parent_message_id=original_msg.id,
                    metadata=meta if meta else None,
                ))

            # update_task, update_memory handled by _handle_common_actions

        if not messages:
            messages.append(Message(
                sender=self.agent_id,
                recipient=original_msg.sender,
                type=MessageType.RESPONSE,
                content=result_text,
                task_id=original_msg.task_id,
                parent_message_id=original_msg.id,
            ))

        return messages
