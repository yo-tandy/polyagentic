"""Phase management routes — CRUD and approval gates."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.message import Message, MessageType

router = APIRouter()


class PhaseActionBody(BaseModel):
    feedback: str | None = None


@router.get("/phases")
async def get_phases(request: Request):
    phase_board = request.app.state.phase_board
    if not phase_board:
        return {"phases": []}
    return {"phases": phase_board.to_summary()}


@router.get("/phases/{phase_id}")
async def get_phase(phase_id: str, request: Request):
    phase_board = request.app.state.phase_board
    task_board = request.app.state.task_board
    if not phase_board:
        return JSONResponse({"error": "Phase board not available"}, status_code=503)

    phase = phase_board.get_phase(phase_id)
    if not phase:
        return JSONResponse({"error": "Phase not found"}, status_code=404)

    tasks = [t.to_dict() for t in task_board.get_tasks_by_phase(phase_id)]
    return {**phase, "tasks": tasks}


@router.post("/phases/{phase_id}/approve")
async def approve_phase(phase_id: str, request: Request):
    """User approves a phase plan. Moves phase to in_progress."""
    phase_board = request.app.state.phase_board
    phase = phase_board.get_phase(phase_id)
    if not phase or phase["status"] != "awaiting_approval":
        return JSONResponse(
            {"error": "Phase not in awaiting_approval state"},
            status_code=400,
        )

    result = await phase_board.update_phase(phase_id, status="in_progress")

    # Notify Jerry to assign draft tickets
    broker = request.app.state.broker
    await broker.deliver(Message(
        sender="user",
        recipient="jerry",
        type=MessageType.SYSTEM,
        content=(
            f"Phase '{phase['name']}' has been APPROVED by the user. "
            f"Assign the DRAFT tickets for this phase to the appropriate team members now. "
            f"Move each ticket from draft to pending and send the task messages."
        ),
        metadata={"command": "phase_approved", "phase_id": phase_id},
    ))

    return result


@router.post("/phases/{phase_id}/reject")
async def reject_phase(phase_id: str, body: PhaseActionBody, request: Request):
    """User rejects a phase plan. Moves back to planning."""
    phase_board = request.app.state.phase_board
    phase = phase_board.get_phase(phase_id)
    if not phase or phase["status"] != "awaiting_approval":
        return JSONResponse(
            {"error": "Phase not in awaiting_approval state"},
            status_code=400,
        )

    result = await phase_board.update_phase(phase_id, status="planning")

    broker = request.app.state.broker
    await broker.deliver(Message(
        sender="user",
        recipient="jerry",
        type=MessageType.SYSTEM,
        content=(
            f"Phase plan for '{phase['name']}' was REJECTED by the user. "
            f"Feedback: {body.feedback or 'No specific feedback provided'}. "
            f"Revise the plan and resubmit for approval."
        ),
        metadata={"command": "phase_rejected", "phase_id": phase_id},
    ))

    return result


@router.post("/phases/{phase_id}/approve-review")
async def approve_phase_review(phase_id: str, request: Request):
    """User approves phase completion. Marks completed."""
    phase_board = request.app.state.phase_board
    phase = phase_board.get_phase(phase_id)
    if not phase or phase["status"] != "review":
        return JSONResponse(
            {"error": "Phase not in review state"},
            status_code=400,
        )

    result = await phase_board.update_phase(phase_id, status="completed")

    # Notify Jerry about next phase
    broker = request.app.state.broker
    next_phase = phase_board.get_current_phase()
    if next_phase:
        await broker.deliver(Message(
            sender="user",
            recipient="jerry",
            type=MessageType.SYSTEM,
            content=(
                f"Phase '{phase['name']}' has been completed and approved. "
                f"Begin planning for the next phase: '{next_phase['name']}'. "
                f"Ask Perry to generate tickets for this phase."
            ),
            metadata={"command": "next_phase", "phase_id": next_phase["id"]},
        ))
    else:
        # All phases done
        ts = getattr(request.app.state, "team_structure", None)
        ufa = ts.user_facing_agent if ts else "manny"
        await broker.deliver(Message(
            sender="user",
            recipient=ufa,
            type=MessageType.SYSTEM,
            content=(
                f"All project phases are now COMPLETED. "
                f"Inform the user that the project is done."
            ),
            metadata={"command": "project_complete"},
        ))

    return result


@router.post("/phases/{phase_id}/reject-review")
async def reject_phase_review(
    phase_id: str, body: PhaseActionBody, request: Request,
):
    """User rejects phase review. Moves back to in_progress."""
    phase_board = request.app.state.phase_board
    phase = phase_board.get_phase(phase_id)
    if not phase or phase["status"] != "review":
        return JSONResponse(
            {"error": "Phase not in review state"},
            status_code=400,
        )

    result = await phase_board.update_phase(phase_id, status="in_progress")

    broker = request.app.state.broker
    await broker.deliver(Message(
        sender="user",
        recipient="jerry",
        type=MessageType.SYSTEM,
        content=(
            f"Phase review for '{phase['name']}' was REJECTED. "
            f"Feedback: {body.feedback or 'No specific feedback provided'}. "
            f"Address the issues and resubmit the phase for review."
        ),
        metadata={"command": "phase_review_rejected", "phase_id": phase_id},
    ))

    return result
