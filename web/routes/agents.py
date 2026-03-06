from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.message import Message, MessageType

logger = logging.getLogger(__name__)

router = APIRouter()


class AgentMessageRequest(BaseModel):
    message: str


@router.get("/agents")
async def get_agents(request: Request):
    registry = request.app.state.registry
    return {"agents": registry.get_status_summary()}


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, request: Request):
    registry = request.app.state.registry
    agent = registry.get(agent_id)
    if agent is None:
        return JSONResponse({"error": "Agent not found"}, status_code=404)
    return agent.to_info_dict()


@router.post("/agents/{agent_id}/message")
async def send_agent_message(agent_id: str, body: AgentMessageRequest, request: Request):
    """Send a message directly to any agent."""
    registry = request.app.state.registry
    broker = request.app.state.broker

    agent = registry.get(agent_id)
    if agent is None:
        return JSONResponse({"error": f"Agent '{agent_id}' not found"}, status_code=404)

    msg = Message(
        sender="user",
        recipient=agent_id,
        type=MessageType.CHAT,
        content=body.message,
    )
    await broker.deliver(msg)
    return {"message_id": msg.id, "status": "delivered"}


@router.post("/agents/{agent_id}/status-request")
async def request_agent_status(agent_id: str, request: Request):
    """Ask an agent to produce a status report of its current work."""
    registry = request.app.state.registry
    broker = request.app.state.broker
    task_board = request.app.state.task_board

    agent = registry.get(agent_id)
    if agent is None:
        return JSONResponse({"error": f"Agent '{agent_id}' not found"}, status_code=404)

    tasks = task_board.get_tasks_by_assignee(agent_id)
    task_summary = "\n".join(
        f"- [{t.status.value}] {t.title}" for t in tasks
    ) or "No tasks assigned."

    msg = Message(
        sender="user",
        recipient=agent_id,
        type=MessageType.CHAT,
        content=(
            "Please provide a concise 2-paragraph status report of your current "
            "and recently completed work.\n\n"
            f"Your assigned tasks:\n{task_summary}"
        ),
    )
    await broker.deliver(msg)
    return {"message_id": msg.id, "status": "delivered"}
