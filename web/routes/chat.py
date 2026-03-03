from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from core.message import Message, MessageType

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    message_id: str
    status: str


@router.post("/chat", response_model=ChatResponse)
async def send_chat(body: ChatRequest, request: Request):
    """Send a message to the user-facing agent (main chat)."""
    broker = request.app.state.broker
    ts = getattr(request.app.state, "team_structure", None)
    ufa = ts.user_facing_agent if ts else "manny"

    msg = Message(
        sender="user",
        recipient=ufa,
        type=MessageType.CHAT,
        content=body.message,
    )

    await broker.deliver(msg)

    broker._chat_history.append({
        "message_id": msg.id,
        "sender": "user",
        "content": body.message,
        "timestamp": msg.timestamp,
        "task_id": None,
    })

    return ChatResponse(message_id=msg.id, status="delivered")


@router.post("/chat/conversation", response_model=ChatResponse)
async def send_conversation_message(body: ChatRequest, request: Request):
    """Send a message within a specific conversation."""
    cm = getattr(request.app.state, "conversation_manager", None)

    # Look up the conversation by ID, or fall back to any active
    if body.conversation_id:
        active_conv = cm.get_conversation(body.conversation_id) if cm else None
    else:
        active_conv = cm.get_active() if cm else None

    if not active_conv:
        return ChatResponse(message_id="", status="no_active_conversation")

    broker = request.app.state.broker

    msg = Message(
        sender="user",
        recipient=active_conv["agent_id"],
        type=MessageType.CONVERSATION,
        content=body.message,
        metadata={"conversation_id": active_conv["id"]},
    )

    await broker.deliver(msg)
    cm.record_message("user", body.message, conv_id=active_conv["id"])

    broker._chat_history.append({
        "message_id": msg.id,
        "sender": "user",
        "content": body.message,
        "timestamp": msg.timestamp,
        "task_id": None,
        "conversation_id": active_conv["id"],
    })

    return ChatResponse(message_id=msg.id, status="delivered")


@router.get("/chat/history")
async def get_chat_history(request: Request):
    broker = request.app.state.broker
    return {"messages": broker.get_chat_history()}
