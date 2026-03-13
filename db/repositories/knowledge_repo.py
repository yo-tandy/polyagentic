"""Knowledge base repository — CRUD for documents and comments."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, delete

from core.constants import gen_id
from db.models.knowledge import Document, DocumentComment
from db.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class KnowledgeRepository(BaseRepository):

    # ── Documents ─────────────────────────────────────────────────────

    async def add_document(
        self,
        project_id: str,
        title: str,
        category: str,
        content: str,
        created_by: str = "unknown",
        tenant_id: str = "default",
        upload_path: str | None = None,
        file_type: str | None = None,
        file_size: int | None = None,
        source: str | None = None,
        source_path: str | None = None,
    ) -> Document:
        async with self._session() as session:
            slug = title.lower().replace(" ", "-")[:60]
            doc = Document(
                id=gen_id("doc-", 8),
                project_id=project_id,
                tenant_id=tenant_id,
                title=title,
                category=category,
                filename=f"{slug}.md",
                content=content,
                created_by=created_by,
                upload_path=upload_path,
                file_type=file_type,
                file_size=file_size,
                source=source,
                source_path=source_path,
            )
            session.add(doc)
            await session.commit()
            await session.refresh(doc)
            return doc

    async def update_document(
        self, doc_id: str, content: str, updated_by: str = "unknown",
    ) -> Document | None:
        async with self._session() as session:
            doc = await session.get(Document, doc_id)
            if not doc:
                return None
            doc.content = content
            await session.commit()
            await session.refresh(doc)
            return doc

    async def get_document(self, doc_id: str) -> Document | None:
        async with self._session() as session:
            return await session.get(Document, doc_id)

    async def get_document_content(self, doc_id: str) -> str | None:
        async with self._session() as session:
            doc = await session.get(Document, doc_id)
            return doc.content if doc else None

    async def list_documents(
        self, project_id: str, category: str | None = None,
    ) -> list[Document]:
        async with self._session() as session:
            stmt = select(Document).where(
                Document.project_id == project_id,
            )
            if category:
                stmt = stmt.where(Document.category == category)
            stmt = stmt.order_by(Document.updated_at.desc())
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def delete_document(self, doc_id: str) -> bool:
        async with self._session() as session:
            stmt = delete(Document).where(Document.id == doc_id)
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    # ── Comments ──────────────────────────────────────────────────────

    async def add_comment(
        self,
        doc_id: str,
        highlighted_text: str,
        element_index: int,
        comment_text: str,
        assigned_to: str,
        created_by: str = "user",
    ) -> DocumentComment | None:
        async with self._session() as session:
            doc = await session.get(Document, doc_id)
            if not doc:
                return None
            comment = DocumentComment(
                id=gen_id("cmt-", 8),
                doc_id=doc_id,
                highlighted_text=highlighted_text,
                element_index=element_index,
                comment_text=comment_text,
                assigned_to=assigned_to,
                created_by=created_by,
            )
            session.add(comment)
            await session.commit()
            await session.refresh(comment)
            return comment

    async def get_comments(
        self, doc_id: str, status: str | None = None,
    ) -> list[DocumentComment]:
        async with self._session() as session:
            stmt = select(DocumentComment).where(
                DocumentComment.doc_id == doc_id,
            )
            if status:
                stmt = stmt.where(DocumentComment.status == status)
            stmt = stmt.order_by(DocumentComment.created_at)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update_comment(
        self, doc_id: str, comment_id: str, **kwargs: Any,
    ) -> DocumentComment | None:
        async with self._session() as session:
            comment = await session.get(DocumentComment, comment_id)
            if not comment or comment.doc_id != doc_id:
                return None
            for k, v in kwargs.items():
                if hasattr(comment, k):
                    setattr(comment, k, v)
            await session.commit()
            await session.refresh(comment)
            return comment

    async def delete_comment(self, doc_id: str, comment_id: str) -> bool:
        async with self._session() as session:
            stmt = delete(DocumentComment).where(
                DocumentComment.id == comment_id,
                DocumentComment.doc_id == doc_id,
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def resolve_comments(
        self,
        doc_id: str,
        resolutions: list[dict],
        edit_verified: bool = False,
    ) -> list[DocumentComment]:
        """Resolve comments in batch. Returns resolved comments."""
        resolved: list[DocumentComment] = []
        async with self._session() as session:
            for r in resolutions:
                cid = r.get("comment_id", "")
                resolution_text = r.get("resolution", "")
                comment = await session.get(DocumentComment, cid)
                if not comment or comment.doc_id != doc_id:
                    continue
                if comment.status == "resolved":
                    continue
                comment.status = "resolved"
                comment.resolution = resolution_text
                comment.edit_verified = edit_verified
                comment.resolved_at = datetime.now(timezone.utc)
                resolved.append(comment)
            await session.commit()
        return resolved
