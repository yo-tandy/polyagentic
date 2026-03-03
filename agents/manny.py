from __future__ import annotations

import logging
import re
from pathlib import Path

from core.agent import Agent
from core.message import Message, MessageType
from core.prompt_loader import load_prompt
from config import CLAUDE_ALLOWED_TOOLS_NONE

logger = logging.getLogger(__name__)


class MannyAgent(Agent):
    """Manager agent — stateless thin router.

    Receives user requests, delegates to Rory/Innes/Perry/Jerry/workers.
    Mirrors the DevManagerAgent pattern: stateless (use_session=False),
    no tools, budget-capped, re-renders system prompt every call.
    """

    def __init__(self, model: str, messages_dir: Path, working_dir: Path):
        prompt_template = load_prompt("manny")
        self._prompt_template = prompt_template
        self._team_roster: str = ""
        super().__init__(
            agent_id="manny",
            name="Manny",
            role="Manager",
            system_prompt=prompt_template,
            model=model,
            allowed_tools=CLAUDE_ALLOWED_TOOLS_NONE,
            messages_dir=messages_dir,
            working_dir=working_dir,
            timeout=120,
            use_session=False,
            max_budget_usd=0.25,
        )
        self._registry = None

    def configure_extras(self, registry, **kwargs):
        """Provide registry for delegation checks."""
        self._registry = registry

    def update_team_roster(self, roster_text: str, team_roles: str = "", routing_guide: str = ""):
        """Re-render system prompt with updated team roster."""
        self._team_roster = roster_text
        self._team_roles = team_roles
        self._routing_guide = routing_guide
        self._render_system_prompt()

    def _render_system_prompt(self):
        """Re-build system prompt from template + roster + roles + routing + memory."""
        self.system_prompt = self._render_prompt_template(
            self._prompt_template,
            roster=self._team_roster or "",
            team_roles=getattr(self, "_team_roles", ""),
            routing_guide=getattr(self, "_routing_guide", ""),
        )

    def _get_system_prompt_if_first_call(self) -> str | None:
        # Stateless — always re-render and send full system prompt
        self._render_system_prompt()
        return self.system_prompt

    async def process_message(self, msg: Message) -> list[Message]:
        """Override to call sync _parse_response after common action handling."""
        prompt = self._build_prompt(msg)
        session_id = None
        if self.use_session and self._session_store:
            session_id = self._session_store.get(self.agent_id)

        result = await self._subprocess.invoke(
            prompt=prompt,
            system_prompt=self._get_system_prompt_if_first_call(),
            model=self.model,
            allowed_tools=self.allowed_tools,
            session_id=session_id,
            working_dir=self.working_dir,
            timeout=self.timeout,
            max_budget_usd=self.max_budget_usd,
        )

        # Retry on stale session
        if result.is_error and session_id and "No conversation found" in result.result_text:
            logger.warning(
                "Agent %s stale session %s, clearing and retrying fresh",
                self.agent_id, session_id,
            )
            if self._session_store:
                self._session_store.set(self.agent_id, "")
            result = await self._subprocess.invoke(
                prompt=prompt,
                system_prompt=self._get_system_prompt_if_first_call(),
                model=self.model,
                allowed_tools=self.allowed_tools,
                session_id=None,
                working_dir=self.working_dir,
                timeout=self.timeout,
                max_budget_usd=self.max_budget_usd,
            )

        if self.use_session and result.session_id and self._session_store:
            self._session_store.set(self.agent_id, result.session_id)

        # Record subprocess stats (stateless — no auto-pause)
        if self._session_store:
            self._session_store.record_request(
                self.agent_id,
                duration_ms=result.duration_ms or 0,
                is_error=result.is_error,
                cost_usd=result.cost_usd or 0.0,
                input_tokens=result.input_tokens or 0,
                output_tokens=result.output_tokens or 0,
            )

        if result.is_error:
            logger.error("Agent %s Claude error: %s", self.agent_id, result.result_text)
            return [Message(
                sender=self.agent_id,
                recipient="user",
                type=MessageType.CHAT,
                content=f"⚠️ {self.name} error: {result.result_text}",
                task_id=msg.task_id,
                parent_message_id=msg.id,
            )]

        result_text = result.result_text
        actions = self._extract_actions(result_text)
        await self._handle_common_actions(actions)
        return self._parse_response(result_text, msg, actions=actions)

    def _parse_response(self, result_text: str, original_msg: Message, actions: list[dict] | None = None) -> list[Message]:
        messages = []
        if actions is None:
            actions = self._extract_actions(result_text)

        if not actions:
            logger.warning(
                "Manny produced no action blocks (prompt from %s, %d chars). "
                "Forwarding sanitized text to user.",
                original_msg.sender, len(result_text),
            )
            cleaned = self._sanitize_for_user(result_text)
            if not cleaned.strip():
                cleaned = "I received your request but encountered a formatting issue. Let me try again."
            messages.append(Message(
                sender=self.agent_id,
                recipient="user",
                type=MessageType.CHAT,
                content=cleaned,
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
                priority = action.get("priority", 3)
                labels = action.get("labels", [])
                role = action.get("role", None)

                is_known_agent = self._registry and self._registry.get(to)

                if self._task_board:
                    task = self._task_board.create_task(
                        title=title,
                        description=desc,
                        created_by=self.agent_id,
                        assignee=to if is_known_agent else None,
                        role=role or (to if not is_known_agent else None),
                        priority=priority,
                        labels=labels,
                    )
                    task_id = task.id
                else:
                    task_id = None

                if is_known_agent:
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
                user_msg = action.get("message", result_text)
                suggested = action.get("suggested_answers", [])
                meta = {}
                if suggested:
                    meta["suggested_answers"] = suggested[:3]
                messages.append(Message(
                    sender=self.agent_id,
                    recipient="user",
                    type=MessageType.CHAT,
                    content=user_msg,
                    task_id=original_msg.task_id,
                    parent_message_id=original_msg.id,
                    metadata=meta if meta else None,
                ))

            elif action_type == "pause_task":
                target_agent = action.get("agent_id", "")
                target_task_id = action.get("task_id", "")
                if target_agent and target_task_id:
                    messages.append(Message(
                        sender=self.agent_id,
                        recipient=target_agent,
                        type=MessageType.SYSTEM,
                        content=(
                            f"PAUSE TASK {target_task_id}: Summarize your current progress, "
                            f"save your state using update_task with a paused_summary, and stop working."
                        ),
                        task_id=target_task_id,
                        metadata={"command": "pause_task"},
                    ))

            elif action_type == "start_task":
                target_agent = action.get("agent_id", "")
                target_task_id = action.get("task_id", "")
                task = self._task_board.get_task(target_task_id) if self._task_board else None
                content = ""
                if task:
                    content = task.description
                    if task.paused_summary:
                        content += f"\n\n--- RESUMED TASK ---\nPrevious state when paused:\n{task.paused_summary}"
                    self._task_board.update_task(
                        target_task_id, assignee=target_agent, _agent_id=self.agent_id,
                    )
                messages.append(Message(
                    sender=self.agent_id,
                    recipient=target_agent,
                    type=MessageType.TASK,
                    content=content or f"Start working on task {target_task_id}",
                    task_id=target_task_id,
                    parent_message_id=original_msg.id,
                    metadata={"task_title": task.title if task else "Task"},
                ))

            # update_task, update_memory, write_document handled by common actions

        # If no user response was generated for a user request, add a summary
        has_user_response = any(m.recipient == "user" for m in messages)
        if not has_user_response and original_msg.sender == "user":
            delegations = [a for a in actions if a.get("action") == "delegate"]
            if delegations:
                summary = f"I've delegated your request to {len(delegations)} team member(s). I'll report back when they're done."
                messages.append(Message(
                    sender=self.agent_id,
                    recipient="user",
                    type=MessageType.CHAT,
                    content=summary,
                    task_id=original_msg.task_id,
                    parent_message_id=original_msg.id,
                ))

        return messages

    @staticmethod
    def _sanitize_for_user(text: str) -> str:
        """Strip action blocks and bare JSON from text before showing to user."""
        # Remove fenced code blocks containing JSON
        cleaned = re.sub(r'```\w*\s*\{.*?\}\s*```', '', text, flags=re.DOTALL)
        # Remove bare JSON objects
        cleaned = re.sub(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', '', cleaned)
        # Remove [Saving to memory: ...] annotations
        cleaned = re.sub(r'\[.*?(?:memory|saving|delegat).*?\]', '', cleaned, flags=re.IGNORECASE)
        # Collapse excessive whitespace
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        return cleaned.strip()
