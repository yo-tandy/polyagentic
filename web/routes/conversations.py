from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

from core.message import Message, MessageType

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_user(request: Request) -> dict:
    return getattr(request.state, "user", {})


class StartConversationRequest(BaseModel):
    agent_id: str


@router.get("/conversations/active")
async def get_active_conversations(request: Request):
    """Return all active conversations."""
    cm = request.app.state.conversation_manager
    if not cm:
        return {"conversations": []}
    return {"conversations": cm.to_summary_list()}


@router.post("/conversations/{conv_id}/close")
async def close_conversation(conv_id: str, request: Request):
    """User closes a conversation."""
    cm = request.app.state.conversation_manager
    if not cm:
        return {"error": "Conversation manager not available"}

    conv = cm.get_conversation(conv_id)
    if not conv:
        return {"error": "No active conversation with that ID"}

    agent_id = conv["agent_id"]
    user = _get_user(request)
    user_id = user.get("id", "user")

    # Send CONVERSATION_END message to the agent so it can compile summary
    broker = request.app.state.broker
    msg = Message(
        sender=user_id,
        recipient=agent_id,
        type=MessageType.CONVERSATION_END,
        content="The user has ended the conversation. Please compile a summary of the discussion.",
        metadata={"conversation_id": conv_id},
    )
    await broker.deliver(msg)

    # Close immediately (don't wait for agent summary)
    await cm.close(conv_id)

    return {"status": "closed", "conversation_id": conv_id}


@router.post("/conversations/start")
async def start_conversation(body: StartConversationRequest, request: Request):
    """User initiates a direct conversation with a specific agent."""
    cm = request.app.state.conversation_manager
    if not cm:
        return {"error": "Conversation manager not available"}

    registry = request.app.state.registry
    if not registry or not registry.get(body.agent_id):
        return {"error": f"Agent '{body.agent_id}' not found"}

    user = _get_user(request)
    user_id = user.get("id", "user")

    # Check if already chatting with this agent
    existing = cm.get_by_agent(body.agent_id)
    if existing:
        return {
            "id": existing["id"],
            "agent_id": body.agent_id,
            "title": existing["title"],
            "existing": True,
        }

    conv = await cm.start(body.agent_id, goals=[], title=f"Chat with {body.agent_id}")

    # Notify the agent that the user wants to talk
    broker = request.app.state.broker
    msg = Message(
        sender=user_id,
        recipient=body.agent_id,
        type=MessageType.CONVERSATION,
        content="The user has opened a direct chat with you. Greet them and ask how you can help.",
        metadata={"conversation_id": conv["id"]},
    )
    await broker.deliver(msg)

    return {"id": conv["id"], "agent_id": body.agent_id, "title": conv["title"]}
