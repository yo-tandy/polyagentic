"""Update a project phase status or properties."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.actions.base import BaseAction, ActionField, ActionContext
from core.message import Message

if TYPE_CHECKING:
    from core.agent import Agent


class UpdatePhase(BaseAction):

    name = "update_phase"
    description = "Update a phase status or properties."
    produces_messages = False

    fields = [
        ActionField("phase_id", "string", required=True,
                     description="The phase ID to update"),
        ActionField("status", "string",
                     description="New phase status",
                     enum=["planning", "awaiting_approval", "in_progress",
                           "review", "completed"]),
        ActionField("planning_doc_id", "string",
                     description="KB doc ID for the phase planning document"),
        ActionField("review_doc_id", "string",
                     description="KB doc ID for the phase review document"),
    ]

    example = {
        "action": "update_phase",
        "phase_id": "phase-abc123",
        "status": "awaiting_approval",
        "planning_doc_id": "doc-xyz789",
    }

    async def execute(
        self, agent: Agent, action: dict, original_msg: Message,
        ctx: ActionContext,
    ) -> list[Message]:
        phase_board = getattr(agent, "_phase_board", None)
        if not phase_board:
            return []

        phase_id = action.get("phase_id")
        if not phase_id:
            return []

        updates = {}
        for key in ("status", "planning_doc_id", "review_doc_id"):
            if key in action:
                updates[key] = action[key]

        if updates:
            await phase_board.update_phase(phase_id, **updates)
        return []
