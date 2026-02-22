from __future__ import annotations

import logging
from pathlib import Path

from core.agent import Agent
from core.message import Message, MessageType
from config import CLAUDE_ALLOWED_TOOLS_READONLY

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "project_manager.md"


class ProjectManagerAgent(Agent):
    def __init__(self, model: str, messages_dir: Path, working_dir: Path):
        prompt_template = PROMPT_PATH.read_text()
        self._prompt_template = prompt_template  # keep raw template for re-rendering
        super().__init__(
            agent_id="project_manager",
            name="Project Manager",
            role="Project Manager",
            system_prompt=prompt_template,
            model=model,
            allowed_tools=CLAUDE_ALLOWED_TOOLS_READONLY,
            messages_dir=messages_dir,
            working_dir=working_dir,
        )

    def update_team_roster(self, roster_text: str):
        """Re-render system prompt with the current team roster and memory."""
        prompt = self._prompt_template.replace("{team_roster}", roster_text)
        if self._memory_manager:
            memory = self._memory_manager.get_combined_memory(self.agent_id)
            prompt = prompt.replace("{memory}", memory or "No memory recorded yet.")
        else:
            prompt = prompt.replace("{memory}", "No memory recorded yet.")
        self.system_prompt = prompt

    async def _parse_response(self, result_text: str, original_msg: Message) -> list[Message]:
        messages = []
        actions = self._extract_actions(result_text)

        # Handle common actions (memory, KB)
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

            if action_type == "delegate":
                to = action.get("to", "")
                title = action.get("task_title", "Task")
                desc = action.get("task_description", "")
                labels = action.get("labels", [])

                if self._task_board:
                    task = self._task_board.create_task(
                        title=title, description=desc,
                        created_by=self.agent_id, assignee=to,
                        labels=labels,
                    )
                    task_id = task.id
                else:
                    task_id = None

                messages.append(Message(
                    sender=self.agent_id,
                    recipient=to,
                    type=MessageType.TASK,
                    content=desc,
                    task_id=task_id,
                    parent_message_id=original_msg.id,
                    metadata={"task_title": title},
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

            # update_memory, write_document handled by _handle_common_actions

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
