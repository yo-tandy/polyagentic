"""Create multiple draft tickets for a phase at once."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext
from core.message import Message, MessageType
from core.task import TaskStatus

if TYPE_CHECKING:
    from core.agent import Agent

logger = logging.getLogger(__name__)


def _error_msg(agent: Agent, text: str) -> Message:
    """Build a SYSTEM error message back to the agent."""
    return Message(
        sender="system",
        recipient=agent.agent_id,
        type=MessageType.SYSTEM,
        content=f"[create_batch_tickets ERROR] {text}",
        metadata={"no_reply": True},
    )


class CreateBatchTickets(BaseAction):

    name = "create_batch_tickets"
    description = "Create multiple draft tickets for a phase at once."
    produces_messages = True  # can return error messages

    fields = [
        ActionField("phase_id", "string", required=True,
                     description="Phase to add tickets to"),
        ActionField("tickets", "array", required=True,
                     description="Array of ticket objects: {title, description, priority, labels, role, estimate}"),
    ]

    example = {
        "action": "create_batch_tickets",
        "phase_id": "phase-abc123",
        "tickets": [
            {"title": "Set up database schema", "description": "Create SQLAlchemy models...", "priority": 2, "labels": ["backend"], "role": "backend_developer"},
            {"title": "Build REST API", "description": "Implement CRUD endpoints...", "priority": 3, "labels": ["backend", "api"]},
        ],
    }

    async def execute(
        self, agent: Agent, action: dict, original_msg: Message,
        ctx: ActionContext,
    ) -> list[Message]:
        if not agent._task_board:
            logger.warning("create_batch_tickets: no task board for agent %s", agent.agent_id)
            return [_error_msg(agent, "No task board available. Agent may not be fully configured.")]

        phase_id = action.get("phase_id")
        tickets = action.get("tickets", [])

        if not phase_id:
            logger.warning("create_batch_tickets: missing phase_id from agent %s", agent.agent_id)
            return [_error_msg(
                agent,
                "Missing required field 'phase_id'. Please re-emit the action with a valid phase_id.",
            )]

        if not tickets:
            logger.warning(
                "create_batch_tickets: empty tickets array from agent %s (phase %s)",
                agent.agent_id, phase_id,
            )
            return [_error_msg(
                agent,
                "The 'tickets' array is empty — no tickets were created. "
                "Make sure to include ticket objects in the array, e.g.:\n"
                '```action\n'
                '{"action": "create_batch_tickets", "phase_id": "' + str(phase_id) + '", '
                '"tickets": [{"title": "...", "description": "...", "role": "..."}]}\n'
                '```',
            )]

        created = 0
        errors = []
        for i, ticket in enumerate(tickets):
            if not isinstance(ticket, dict):
                errors.append(f"Ticket #{i+1} is not a dict (got {type(ticket).__name__})")
                continue
            if not ticket.get("title"):
                errors.append(f"Ticket #{i+1} is missing a 'title' field")
                continue
            try:
                await agent._task_board.create_task(
                    title=ticket.get("title", "Task"),
                    description=ticket.get("description", ""),
                    created_by=agent.agent_id,
                    priority=ticket.get("priority", 3),
                    labels=ticket.get("labels", []),
                    role=ticket.get("role"),
                    category="project",
                    phase_id=phase_id,
                    initial_status=TaskStatus.DRAFT,
                    estimate=ticket.get("estimate"),
                )
                created += 1
            except Exception as exc:
                errors.append(f"Ticket #{i+1} '{ticket.get('title', '?')}' failed: {exc}")
                logger.exception(
                    "create_batch_tickets: failed to create ticket %d for agent %s",
                    i + 1, agent.agent_id,
                )

        logger.info(
            "Agent %s created %d/%d draft tickets for phase %s",
            agent.agent_id, created, len(tickets), phase_id,
        )

        messages: list[Message] = []
        if errors:
            messages.append(_error_msg(
                agent,
                f"Created {created}/{len(tickets)} tickets. Errors:\n"
                + "\n".join(f"- {e}" for e in errors),
            ))
        return messages
