"""Create multiple draft tickets for a phase at once."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext
from core.message import Message
from core.task import TaskStatus

if TYPE_CHECKING:
    from core.agent import Agent

logger = logging.getLogger(__name__)


class CreateBatchTickets(BaseAction):

    name = "create_batch_tickets"
    description = "Create multiple draft tickets for a phase at once."
    produces_messages = False

    fields = [
        ActionField("phase_id", "string", required=True,
                     description="Phase to add tickets to"),
        ActionField("tickets", "array", required=True,
                     description="Array of ticket objects: {title, description, priority, labels, role}"),
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
            return []

        phase_id = action.get("phase_id")
        tickets = action.get("tickets", [])

        if not phase_id or not tickets:
            return []

        created = 0
        for ticket in tickets:
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
            )
            created += 1

        logger.info(
            "Agent %s created %d draft tickets for phase %s",
            agent.agent_id, created, phase_id,
        )
        return []
