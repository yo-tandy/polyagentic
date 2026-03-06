"""Conversation manager — DB-backed with in-memory cache.

Now persists conversations across server restarts (major improvement over
the old in-memory-only implementation).
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from db.repositories.conversation_repo import ConversationRepository

logger = logging.getLogger(__name__)


class ConversationState(str, Enum):
    INACTIVE = "inactive"
    ACTIVE = "active"
    CLOSED = "closed"


class ConversationManager:
    """Manages direct conversations between agents and the user.

    Supports multiple concurrent conversations (one per agent).
    DB-backed with in-memory cache for sync reads.
    """

    def __init__(self, repo: ConversationRepository, project_id: str):
        self._repo = repo
        self._project_id = project_id
        self._conversations: dict[str, dict] = {}  # conv_id → conversation
        self._broadcast_fn = None

    async def load(self) -> None:
        """Load active conversations from DB at startup."""
        records = await self._repo.get_active(self._project_id)
        for conv in records:
            messages = await self._repo.get_messages(conv.id)
            self._conversations[conv.id] = {
                "id": conv.id,
                "agent_id": conv.agent_id,
                "title": conv.title,
                "goals": conv.goals or [],
                "state": ConversationState.ACTIVE,
                "messages": [
                    {"sender": m.sender, "content": m.content,
                     "timestamp": m.created_at.isoformat() if m.created_at else ""}
                    for m in messages
                ],
                "started_at": conv.created_at.isoformat() if conv.created_at else "",
            }
        logger.info("Loaded %d active conversations from DB", len(self._conversations))

    def set_broadcast(self, broadcast_fn):
        """Set the async broadcast function for WS events."""
        self._broadcast_fn = broadcast_fn

    async def start(self, agent_id: str, goals: list[str], title: str) -> dict:
        """Start a new conversation. Returns existing if one is already
        active with the same agent."""
        existing = self.get_by_agent(agent_id)
        if existing:
            return existing

        conv_record = await self._repo.start(
            self._project_id, agent_id, title, goals,
        )

        conv = {
            "id": conv_record.id,
            "agent_id": agent_id,
            "title": title,
            "goals": goals,
            "state": ConversationState.ACTIVE,
            "messages": [],
            "started_at": conv_record.created_at.isoformat() if conv_record.created_at else "",
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

    # ── Sync reads (from cache) ──────────────────────────────────

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

    # ── Async writes ─────────────────────────────────────────────

    async def record_message(self, sender: str, content: str, conv_id: str | None = None):
        """Track a message in a conversation."""
        if conv_id:
            conv = self._conversations.get(conv_id)
        else:
            conv = self.get_active()
        if not conv or conv["state"] != ConversationState.ACTIVE:
            return
        # Update cache
        from datetime import datetime, timezone
        conv["messages"].append({
            "sender": sender,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        # Write to DB
        await self._repo.record_message(conv["id"], sender, content)

    async def close(self, conv_id: str | None = None) -> dict | None:
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
        result = dict(conv)
        logger.info("Conversation closed (user): %s", conv_id)

        # Write to DB
        await self._repo.close(conv_id)

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

    async def close_by_agent(self, agent_id: str) -> dict | None:
        """Agent-initiated close. Returns the conversation data."""
        conv = self.get_by_agent(agent_id)
        if not conv:
            return None
        return await self.close(conv["id"])

    # ── Summary methods (sync, from cache) ───────────────────────

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
