"""Knowledge base — DB-backed with in-memory cache for document index.

Documents are now stored in the DB (content as TEXT column).
Repo docs (read-only from workspace/docs/) are still read from disk.
"""
from __future__ import annotations

import logging
from pathlib import Path

from db.repositories.knowledge_repo import KnowledgeRepository

logger = logging.getLogger(__name__)

CATEGORIES = ["specs", "design", "architecture", "planning", "history"]
MAX_INDEX_SUMMARY_DOCS = 30


class KnowledgeBase:
    """Manages shared project documents organised by category."""

    def __init__(
        self,
        repo: KnowledgeRepository,
        project_id: str,
        repo_docs_dir: Path | None = None,
        max_summary_docs: int = MAX_INDEX_SUMMARY_DOCS,
    ):
        self._repo = repo
        self._project_id = project_id
        self.repo_docs_dir = repo_docs_dir
        self._max_summary_docs = max_summary_docs
        # In-memory cache of doc metadata dicts
        self._docs_cache: list[dict] = []

    async def load(self) -> None:
        """Load document index from DB and repo docs from disk."""
        records = await self._repo.list_documents(self._project_id)
        self._docs_cache = [self._doc_to_dict(r) for r in records]

        # Sync repo docs/ folder (read-only, source="repo")
        if self.repo_docs_dir and self.repo_docs_dir.is_dir():
            await self._sync_repo_docs()

        logger.info("KB: loaded %d documents", len(self._docs_cache))

    async def _sync_repo_docs(self):
        """Discover .md files in workspace/docs/ and add to DB if not tracked."""
        from datetime import datetime, timezone
        known_paths = {
            d.get("source_path") for d in self._docs_cache
            if d.get("source") == "repo"
        }
        added = 0
        for md_file in self.repo_docs_dir.rglob("*.md"):
            abs_path = str(md_file.resolve())
            if abs_path in known_paths:
                continue
            rel = md_file.relative_to(self.repo_docs_dir)
            title = md_file.stem.replace("-", " ").replace("_", " ").title()
            if len(rel.parts) > 1:
                title = f"{'/'.join(rel.parts[:-1])}/{title}"
            content = md_file.read_text()
            rec = await self._repo.add_document(
                project_id=self._project_id,
                title=title,
                category="repo",
                content=content,
                created_by="repo",
            )
            doc_dict = self._doc_to_dict(rec)
            doc_dict["source"] = "repo"
            doc_dict["source_path"] = abs_path
            self._docs_cache.append(doc_dict)
            added += 1
        if added:
            logger.info("KB sync: added %d repo docs to index", added)

    # ── CRUD ──

    async def add_document(
        self, title: str, category: str, content: str, created_by: str,
    ) -> dict:
        if category not in CATEGORIES:
            raise ValueError(f"Invalid category: {category}. Must be one of {CATEGORIES}")

        rec = await self._repo.add_document(
            self._project_id, title, category, content, created_by,
        )
        doc_dict = self._doc_to_dict(rec)
        self._docs_cache.append(doc_dict)
        logger.info("KB: added document '%s' [%s] by %s", title, category, created_by)
        return doc_dict

    async def update_document(self, doc_id: str, content: str, updated_by: str) -> dict | None:
        rec = await self._repo.update_document(doc_id, content, updated_by)
        if not rec:
            logger.warning("KB: document %s not found", doc_id)
            return None
        # Update cache
        for i, d in enumerate(self._docs_cache):
            if d["id"] == doc_id:
                self._docs_cache[i] = self._doc_to_dict(rec)
                break
        logger.info("KB: updated document '%s' by %s", rec.title, updated_by)
        return self._doc_to_dict(rec)

    async def get_document(self, doc_id: str) -> dict | None:
        # Check cache first
        for d in self._docs_cache:
            if d["id"] == doc_id:
                return d
        # Fallback to DB
        rec = await self._repo.get_document(doc_id)
        return self._doc_to_dict(rec) if rec else None

    async def get_document_content(self, doc_id: str) -> str | None:
        # Check for repo doc (read from disk)
        for d in self._docs_cache:
            if d["id"] == doc_id and d.get("source") == "repo" and d.get("source_path"):
                p = Path(d["source_path"])
                return p.read_text() if p.exists() else None
        return await self._repo.get_document_content(doc_id)

    def list_documents(self, category: str | None = None) -> list[dict]:
        """Return document metadata list (sync, from cache)."""
        if category:
            return [d for d in self._docs_cache if d["category"] == category]
        return list(self._docs_cache)

    def get_index_summary(self) -> str:
        """Return a compact summary of all docs for prompt injection."""
        docs = list(self._docs_cache)
        if not docs:
            return ""

        docs.sort(key=lambda d: d.get("updated_at", ""), reverse=True)
        docs = docs[:self._max_summary_docs]

        lines = ["Available project documents:"]
        for d in docs:
            lines.append(
                f"- [{d['category']}] {d['title']} ({d['id']}) "
                f"— {d['category']}/{d.get('filename', 'unknown')}"
            )
        return "\n".join(lines)

    # ── Comments ──

    async def get_comments(self, doc_id: str) -> list[dict]:
        records = await self._repo.get_comments(doc_id)
        return [self._comment_to_dict(c) for c in records]

    async def add_comment(
        self,
        doc_id: str,
        highlighted_text: str,
        element_index: int,
        comment_text: str,
        assigned_to: str,
        created_by: str = "user",
    ) -> dict | None:
        rec = await self._repo.add_comment(
            doc_id, highlighted_text, element_index,
            comment_text, assigned_to, created_by,
        )
        if not rec:
            return None
        logger.info("KB: added comment %s on doc %s assigned to %s", rec.id, doc_id, assigned_to)
        return self._comment_to_dict(rec)

    async def update_comment(self, doc_id: str, comment_id: str, **kwargs) -> dict | None:
        rec = await self._repo.update_comment(doc_id, comment_id, **kwargs)
        return self._comment_to_dict(rec) if rec else None

    async def delete_comment(self, doc_id: str, comment_id: str) -> bool:
        result = await self._repo.delete_comment(doc_id, comment_id)
        if result:
            logger.info("KB: deleted comment %s from doc %s", comment_id, doc_id)
        return result

    async def delete_document(self, doc_id: str) -> bool:
        """Remove a document from the DB and cache."""
        result = await self._repo.delete_document(doc_id)
        if result:
            self._docs_cache = [d for d in self._docs_cache if d["id"] != doc_id]
            logger.info("KB: deleted document %s", doc_id)
        return result

    async def resolve_comments(
        self, doc_id: str, resolutions: list[dict],
        edit_verified: bool = False,
    ) -> list[dict]:
        records = await self._repo.resolve_comments(doc_id, resolutions, edit_verified)
        logger.info("KB: resolved %d comment(s) on doc %s", len(records), doc_id)
        return [self._comment_to_dict(r) for r in records]

    # ── Helpers ──

    @staticmethod
    def _doc_to_dict(rec) -> dict:
        return {
            "id": rec.id,
            "title": rec.title,
            "category": rec.category,
            "filename": rec.filename or "",
            "created_by": rec.created_by or "unknown",
            "created_at": rec.created_at.isoformat() if rec.created_at else "",
            "updated_at": rec.updated_at.isoformat() if rec.updated_at else "",
            "source": rec.source,
            "source_path": rec.source_path,
        }

    @staticmethod
    def _comment_to_dict(rec) -> dict:
        return {
            "id": rec.id,
            "doc_id": rec.doc_id,
            "highlighted_text": rec.highlighted_text,
            "element_index": rec.element_index,
            "comment_text": rec.comment_text,
            "assigned_to": rec.assigned_to,
            "created_by": rec.created_by,
            "created_at": rec.created_at.isoformat() if rec.created_at else "",
            "status": rec.status,
            "resolution": rec.resolution,
        }
