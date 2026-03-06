"""Message log repository — replaces in-memory activity_log and chat_history."""

from __future__ import annotations

from sqlalchemy import select

from db.models.message import MessageLog
from db.repositories.base import BaseRepository


class MessageRepository(BaseRepository):

    async def log_activity(
        self,
        project_id: str,
        message_id: str,
        sender: str,
        recipient: str,
        msg_type: str,
        content_preview: str = "",
        task_id: str | None = None,
        metadata_json: dict | None = None,
        tenant_id: str = "default",
    ) -> None:
        """Log a message delivery to the activity log."""
        async with self._session() as session:
            entry = MessageLog(
                project_id=project_id,
                tenant_id=tenant_id,
                message_id=message_id,
                sender=sender,
                recipient=recipient,
                type=msg_type,
                content_preview=content_preview[:500],
                log_type="activity",
                task_id=task_id,
                metadata_json=metadata_json,
            )
            session.add(entry)
            await session.commit()

    async def log_chat(
        self,
        project_id: str,
        message_id: str,
        sender: str,
        recipient: str,
        msg_type: str,
        content: str,
        task_id: str | None = None,
        metadata_json: dict | None = None,
        conversation_id: str | None = None,
        tenant_id: str = "default",
    ) -> None:
        """Log a chat message (user-facing)."""
        async with self._session() as session:
            entry = MessageLog(
                project_id=project_id,
                tenant_id=tenant_id,
                message_id=message_id,
                sender=sender,
                recipient=recipient,
                type=msg_type,
                content_preview=content[:500],
                content=content,
                log_type="chat",
                task_id=task_id,
                metadata_json=metadata_json,
                conversation_id=conversation_id,
            )
            session.add(entry)
            await session.commit()

    async def get_activity_log(
        self,
        project_id: str,
        limit: int = 100,
        tenant_id: str = "default",
    ) -> list[dict]:
        """Return recent activity log entries."""
        async with self._session() as session:
            stmt = (
                select(MessageLog)
                .where(
                    MessageLog.project_id == project_id,
                    MessageLog.tenant_id == tenant_id,
                    MessageLog.log_type == "activity",
                )
                .order_by(MessageLog.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            entries = result.scalars().all()
            return [
                {
                    "timestamp": e.created_at.isoformat() if e.created_at else "",
                    "sender": e.sender,
                    "recipient": e.recipient,
                    "type": e.type,
                    "preview": e.content_preview,
                    "task_id": e.task_id,
                }
                for e in reversed(entries)  # chronological order
            ]

    async def get_chat_history(
        self,
        project_id: str,
        limit: int = 200,
        tenant_id: str = "default",
    ) -> list[dict]:
        """Return recent chat history entries."""
        async with self._session() as session:
            stmt = (
                select(MessageLog)
                .where(
                    MessageLog.project_id == project_id,
                    MessageLog.tenant_id == tenant_id,
                    MessageLog.log_type == "chat",
                )
                .order_by(MessageLog.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            entries = result.scalars().all()
            return [
                {
                    "id": e.message_id,
                    "timestamp": e.created_at.isoformat() if e.created_at else "",
                    "sender": e.sender,
                    "recipient": e.recipient,
                    "type": e.type,
                    "content": e.content,
                    "task_id": e.task_id,
                    "metadata": e.metadata_json,
                    "conversation_id": e.conversation_id,
                }
                for e in reversed(entries)  # chronological order
            ]
