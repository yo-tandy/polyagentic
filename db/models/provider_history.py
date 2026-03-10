"""Provider conversation history — stores messages for API-based providers.

Claude CLI maintains conversation history internally via ``--resume``.
API providers (Claude API, OpenAI, Gemini) are stateless — each call
needs the full conversation history replayed.  This model stores that
history so sessions can persist across server restarts.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base


class ProviderMessage(Base):
    __tablename__ = "provider_messages"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True,
        default=lambda: f"pmsg_{uuid.uuid4().hex[:12]}",
    )
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # system, user, assistant, tool
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON-encoded tool calls from the model response
    tool_calls_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # For tool result messages: which tool call this responds to
    tool_call_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
