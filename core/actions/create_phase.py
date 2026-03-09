"""Create a new project phase."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext
from core.message import Message

if TYPE_CHECKING:
    from core.agent import Agent


class CreatePhase(BaseAction):

    name = "create_phase"
    description = "Create a new project phase."
    produces_messages = False

    fields = [
        ActionField("name", "string", required=True,
                     description="Phase name"),
        ActionField("description", "string", required=True,
                     description="What this phase covers"),
        ActionField("ordering", "integer",
                     description="Phase sequence number (1, 2, 3...)",
                     default=0),
    ]

    example = {
        "action": "create_phase",
        "name": "Phase 1: Core Infrastructure",
        "description": "Set up project structure, database, and basic API",
        "ordering": 1,
    }

    async def execute(
        self, agent: Agent, action: dict, original_msg: Message,
        ctx: ActionContext,
    ) -> list[Message]:
        phase_board = getattr(agent, "_phase_board", None)
        if not phase_board:
            return []

        name = action.get("name", "Phase")
        desc = action.get("description", "")
        ordering = action.get("ordering", 0)

        await phase_board.create_phase(name, desc, agent.agent_id, ordering)
        return []
