from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from core.message import Message, MessageType

router = APIRouter()


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    message_id: str
    status: str


@router.post("/chat", response_model=ChatResponse)
async def send_chat(body: ChatRequest, request: Request):
    broker = request.app.state.broker

    msg = Message(
        sender="user",
        recipient="manny",
        type=MessageType.CHAT,
        content=body.message,
    )

    await broker.deliver(msg)

    # Also store user message in chat history
    broker._chat_history.append({
        "message_id": msg.id,
        "sender": "user",
        "content": body.message,
        "timestamp": msg.timestamp,
        "task_id": None,
    })

    return ChatResponse(message_id=msg.id, status="delivered")


@router.get("/chat/history")
async def get_chat_history(request: Request):
    broker = request.app.state.broker
    return {"messages": broker.get_chat_history()}
