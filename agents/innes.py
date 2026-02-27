from __future__ import annotations

import logging
from pathlib import Path

from core.agent import Agent
from core.message import Message, MessageType
from config import CLAUDE_ALLOWED_TOOLS_DEV

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "innes.md"


class InnesAgent(Agent):
    """Integrator agent — manages repos, PRs, and code quality.

    Session-based (stateful). GitHub actions (create_repo, review_pr, merge_pr)
    are stubbed in Phase 1 and will be wired to git_manager in Phase 4.
    """

    def __init__(self, model: str, messages_dir: Path, working_dir: Path):
        prompt_template = PROMPT_PATH.read_text()
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

    def configure_extras(self, git_manager, **kwargs):
        """Provide git_manager for future GitHub operations."""
        self._git_manager = git_manager

    def update_team_roster(self, roster_text: str):
        """Re-render system prompt with updated team roster."""
        prompt = self._prompt_template.replace("{team_roster}", roster_text)
        self.system_prompt = prompt

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
                # Phase 1: stubbed — log and acknowledge
                repo_name = action.get("name", "unknown")
                logger.info("Innes: create_repo '%s' (stubbed — Phase 1)", repo_name)
                messages.append(Message(
                    sender=self.agent_id,
                    recipient="user",
                    type=MessageType.CHAT,
                    content=f"Repository '{repo_name}' creation noted (GitHub integration coming in Phase 4).",
                    task_id=original_msg.task_id,
                    parent_message_id=original_msg.id,
                ))

            elif action_type == "review_pr":
                pr_number = action.get("pr_number", "?")
                verdict = action.get("verdict", "pending")
                logger.info("Innes: review_pr #%s verdict=%s (stubbed — Phase 1)", pr_number, verdict)
                messages.append(Message(
                    sender=self.agent_id,
                    recipient="user",
                    type=MessageType.CHAT,
                    content=f"PR #{pr_number} review ({verdict}) noted (GitHub integration coming in Phase 4).",
                    task_id=original_msg.task_id,
                    parent_message_id=original_msg.id,
                ))

            elif action_type in ("merge_pr", "request_changes"):
                pr_number = action.get("pr_number", "?")
                logger.info("Innes: %s #%s (stubbed — Phase 1)", action_type, pr_number)

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
