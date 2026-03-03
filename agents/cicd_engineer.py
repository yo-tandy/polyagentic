from __future__ import annotations

import logging
from pathlib import Path

from core.agent import Agent
from core.message import Message, MessageType
from core.prompt_loader import load_prompt
from config import CLAUDE_ALLOWED_TOOLS_DEV

logger = logging.getLogger(__name__)


class CICDEngineerAgent(Agent):
    def __init__(self, model: str, messages_dir: Path, working_dir: Path):
        prompt_template = load_prompt("cicd_engineer")
        super().__init__(
            agent_id="cicd_engineer",
            name="CI/CD Engineer",
            role="CI/CD Pipeline Engineer",
            system_prompt=prompt_template,
            model=model,
            allowed_tools=CLAUDE_ALLOWED_TOOLS_DEV,
            messages_dir=messages_dir,
            working_dir=working_dir,
        )

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
