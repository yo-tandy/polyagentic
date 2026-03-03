from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ConversationState(str, Enum):
    INACTIVE = "inactive"
    ACTIVE = "active"
    CLOSED = "closed"


class ConversationManager:
    """Manages direct conversations between agents and the user.

    Supports multiple concurrent conversations (one per agent).
    """

    def __init__(self):
        self._conversations: dict[str, dict] = {}  # conv_id → conversation
        self._broadcast_fn = None

    def set_broadcast(self, broadcast_fn):
        """Set the async broadcast function for WS events."""
        self._broadcast_fn = broadcast_fn

    def start(self, agent_id: str, goals: list[str], title: str) -> dict:
        """Start a new conversation. Returns existing if one is already
        active with the same agent."""
        # Check for existing conversation with this agent
        existing = self.get_by_agent(agent_id)
        if existing:
            return existing

        conv = {
            "id": str(uuid.uuid4()),
            "agent_id": agent_id,
            "title": title,
            "goals": goals,
            "state": ConversationState.ACTIVE,
            "messages": [],
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        self._conversations[conv["id"]] = conv
        logger.info(
            "Conversation started: %s with %s — %s",
            conv["id"], agent_id, title,
        )

        if self._broadcast_fn:
            import asyncio
            asyncio.ensure_future(self._broadcast_fn({
                "event_type": "conversation_started",
                "data": {
                    "id": conv["id"],
                    "agent_id": agent_id,
                    "title": title,
                    "goals": goals,
                },
            }))

        return conv

    def get_active(self) -> dict | None:
        """Return any active conversation (first found). Backward compat."""
        for conv in self._conversations.values():
            if conv["state"] == ConversationState.ACTIVE:
                return conv
        return None

    def get_conversation(self, conv_id: str) -> dict | None:
        """Return a specific conversation by ID."""
        conv = self._conversations.get(conv_id)
        if conv and conv["state"] == ConversationState.ACTIVE:
            return conv
        return None

    def get_all_active(self) -> list[dict]:
        """Return all active conversations."""
        return [
            c for c in self._conversations.values()
            if c["state"] == ConversationState.ACTIVE
        ]

    def get_by_agent(self, agent_id: str) -> dict | None:
        """Return the active conversation for a specific agent."""
        for conv in self._conversations.values():
            if conv["agent_id"] == agent_id and conv["state"] == ConversationState.ACTIVE:
                return conv
        return None

    def record_message(self, sender: str, content: str, conv_id: str | None = None):
        """Track a message in a conversation."""
        if conv_id:
            conv = self._conversations.get(conv_id)
        else:
            conv = self.get_active()  # backward compat
        if not conv or conv["state"] != ConversationState.ACTIVE:
            return
        conv["messages"].append({
            "sender": sender,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def close(self, conv_id: str | None = None) -> dict | None:
        """User-initiated close. Returns the conversation data."""
        if not conv_id:
            active = self.get_active()
            if active:
                conv_id = active["id"]
            else:
                return None

        conv = self._conversations.get(conv_id)
        if not conv:
            return None

        conv["state"] = ConversationState.CLOSED
        conv["closed_at"] = datetime.now(timezone.utc).isoformat()
        result = dict(conv)  # copy before deleting
        logger.info("Conversation closed (user): %s", conv_id)

        if self._broadcast_fn:
            import asyncio
            asyncio.ensure_future(self._broadcast_fn({
                "event_type": "conversation_ended",
                "data": {
                    "id": result["id"],
                    "agent_id": result["agent_id"],
                    "title": result["title"],
                },
            }))

        del self._conversations[conv_id]
        return result

    def close_by_agent(self, agent_id: str) -> dict | None:
        """Agent-initiated close. Returns the conversation data."""
        conv = self.get_by_agent(agent_id)
        if not conv:
            return None
        return self.close(conv["id"])

    def to_summary(self) -> dict[str, Any] | None:
        """Return a summary of the first active conversation. Backward compat."""
        active = self.get_all_active()
        if not active:
            return None
        conv = active[0]
        return {
            "id": conv["id"],
            "agent_id": conv["agent_id"],
            "title": conv["title"],
            "goals": conv["goals"],
            "message_count": len(conv["messages"]),
            "started_at": conv["started_at"],
        }

    def to_summary_list(self) -> list[dict]:
        """Return summaries of all active conversations."""
        return [
            {
                "id": c["id"],
                "agent_id": c["agent_id"],
                "title": c["title"],
                "goals": c["goals"],
                "message_count": len(c["messages"]),
                "started_at": c["started_at"],
            }
            for c in self.get_all_active()
        ]
