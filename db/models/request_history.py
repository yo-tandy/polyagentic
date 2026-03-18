"""Request history — one row per agent API call for time-windowed stats."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base, TenantMixin


class RequestHistory(Base, TenantMixin):
    __tablename__ = "request_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(100), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    is_error: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        {"sqlite_autoincrement": True},
    )
