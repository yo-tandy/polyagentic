from __future__ import annotations

import logging
from pathlib import Path

from core.agent import Agent
from core.message import Message, MessageType
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
                    type=MessageType.REDIRECT,
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
                    recipient=original_msg.sender,
                    type=MessageType.RESPONSE,
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
