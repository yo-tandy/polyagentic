"""Central action registry — dispatches actions with permission checks."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.actions.base import BaseAction, ActionContext
from core.message import Message, MessageType

if TYPE_CHECKING:
    from core.agent import Agent

logger = logging.getLogger(__name__)


class ActionRegistry:
    """Central registry for all agent actions.

    Each action is a :class:`BaseAction` subclass discovered at startup.
    The registry validates **permissions** before every execution call.
    """

    def __init__(self) -> None:
        self._actions: dict[str, BaseAction] = {}

    def register(self, action: BaseAction) -> None:
        """Register an action instance."""
        self._actions[action.name] = action

    def get(self, name: str) -> BaseAction | None:
        return self._actions.get(name)

    def get_allowed_actions(self, agent_id: str) -> list[BaseAction]:
        """Return all actions the given agent is permitted to use."""
        return [
            a for a in self._actions.values()
            if a.is_allowed(agent_id)
        ]

    def get_allowed_action_names(self, agent_id: str) -> set[str]:
        return {a.name for a in self.get_allowed_actions(agent_id)}

    # ── Execution ─────────────────────────────────────────────────────

    async def execute(
        self,
        agent: Agent,
        action_dict: dict,
        original_msg: Message,
        ctx: ActionContext,
    ) -> list[Message]:
        """Execute a single action with permission check.

        Returns an empty list if the action is unknown or the agent
        lacks permission.
        """
        action_name = action_dict.get("action", "")
        action = self._actions.get(action_name)

        # ── Unknown action ────────────────────────────────────────────
        if action is None:
            logger.warning(
                "Unknown action '%s' from agent %s",
                action_name, agent.agent_id,
            )
            return []

        # ── Permission check ──────────────────────────────────────────
        if not action.is_allowed(agent.agent_id):
            logger.warning(
                "PERMISSION DENIED: agent '%s' cannot use action '%s' "
                "(allowed: %s)",
                agent.agent_id,
                action_name,
                action.allowed_agents,
            )
            return []

        # ── Execute ───────────────────────────────────────────────────
        try:
            return await action.execute(agent, action_dict, original_msg, ctx)
        except Exception:
            logger.exception(
                "Error executing action '%s' for agent %s",
                action_name, agent.agent_id,
            )
            return []

    async def execute_all(
        self,
        agent: Agent,
        actions: list[dict],
        original_msg: Message,
    ) -> list[Message]:
        """Execute all extracted actions with cross-action coordination.

        Replaces both ``_handle_common_actions`` and per-agent
        ``_parse_response`` loops.
        """
        ctx = ActionContext()

        # Pre-scan: collect doc IDs from update_document
        # (needed by resolve_comments to verify edits)
        for action_dict in actions:
            if (action_dict.get("action") == "update_document"
                    and action_dict.get("doc_id")):
                ctx.edited_doc_ids.add(action_dict["doc_id"])

        messages: list[Message] = []
        for action_dict in actions:
            result = await self.execute(agent, action_dict, original_msg, ctx)
            messages.extend(result)

        # Post-execution: broadcast KB update if any document changed
        if ctx.kb_changed and agent._broker:
            await agent._broker.broadcast_event({
                "event_type": "knowledge_updated",
                "data": {},
            })

        # Auto-summary for user requests that only produced delegations
        has_user_response = any(m.recipient == "user" for m in messages)
        if not has_user_response and original_msg.sender == "user":
            delegations = [
                a for a in actions
                if a.get("action") in ("delegate", "assign_ticket")
            ]
            if delegations:
                messages.append(Message(
                    sender=agent.agent_id,
                    recipient="user",
                    type=MessageType.CHAT,
                    content=(
                        f"I've delegated your request to "
                        f"{len(delegations)} team member(s). "
                        f"I'll report back when they're done."
                    ),
                    task_id=original_msg.task_id,
                    parent_message_id=original_msg.id,
                ))

        return messages

    # ── Prompt generation ─────────────────────────────────────────────

    def generate_prompt_docs(self, agent_id: str) -> str:
        """Generate the complete action documentation section for an agent."""
        allowed = self.get_allowed_actions(agent_id)
        if not allowed:
            return ""

        lines = [
            "## Structured Action Protocol",
            "",
            "All responses MUST use fenced action blocks:",
            "```action",
            '{"action": "action_name", ...fields}',
            "```",
            "",
            "You may include multiple action blocks in a single response.",
            "ONLY use action names from the list below — unknown names "
            "will be rejected.",
            "",
        ]

        for action in sorted(allowed, key=lambda a: a.name):
            lines.append(action.generate_prompt_doc())
            lines.append("")

        return "\n".join(lines)
