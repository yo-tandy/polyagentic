from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

CATEGORIES = ["specs", "design", "architecture", "planning", "history"]
MAX_INDEX_SUMMARY_DOCS = 30


class KnowledgeBase:
    """Manages shared project documents organised by category."""

    def __init__(self, docs_dir: Path):
        self.docs_dir = docs_dir
        self.index_path = docs_dir / "_index.json"
        self._ensure_structure()

    def _ensure_structure(self):
        for cat in CATEGORIES:
            (self.docs_dir / cat).mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self._save_index({"documents": []})
        self._sync_index()

    def _sync_index(self):
        """Discover .md files on disk not tracked in the index and add them."""
        index = self._load_index()
        known_files = {(d["category"], d["filename"]) for d in index["documents"]}
        added = 0
        for cat in CATEGORIES:
            cat_dir = self.docs_dir / cat
            for md_file in cat_dir.glob("*.md"):
                if (cat, md_file.name) not in known_files:
                    mtime = datetime.fromtimestamp(
                        md_file.stat().st_mtime, tz=timezone.utc
                    ).isoformat()
                    doc_meta = {
                        "id": f"doc-{uuid.uuid4().hex[:8]}",
                        "title": md_file.stem.replace("-", " ").title(),
                        "category": cat,
                        "filename": md_file.name,
                        "created_by": "unknown",
                        "created_at": mtime,
                        "updated_at": mtime,
                    }
                    index["documents"].append(doc_meta)
                    added += 1
        if added:
            self._save_index(index)
            logger.info("KB sync: added %d untracked documents to index", added)

    # ── CRUD ──

    def add_document(
        self, title: str, category: str, content: str, created_by: str
    ) -> dict:
        if category not in CATEGORIES:
            raise ValueError(f"Invalid category: {category}. Must be one of {CATEGORIES}")

        doc_id = f"doc-{uuid.uuid4().hex[:8]}"
        filename = _slugify(title) + ".md"
        now = datetime.now(timezone.utc).isoformat()

        doc_meta = {
            "id": doc_id,
            "title": title,
            "category": category,
            "filename": filename,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }

        # Write content file
        filepath = self.docs_dir / category / filename
        filepath.write_text(content)

        # Update index
        index = self._load_index()
        index["documents"].append(doc_meta)
        self._save_index(index)

        logger.info("KB: added document '%s' [%s] by %s", title, category, created_by)
        return doc_meta

    def update_document(self, doc_id: str, content: str, updated_by: str) -> dict | None:
        index = self._load_index()
        doc = self._find_doc(index, doc_id)
        if not doc:
            logger.warning("KB: document %s not found", doc_id)
            return None

        filepath = self.docs_dir / doc["category"] / doc["filename"]
        filepath.write_text(content)
        doc["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._save_index(index)

        logger.info("KB: updated document '%s' by %s", doc["title"], updated_by)
        return doc

    def get_document(self, doc_id: str) -> dict | None:
        index = self._load_index()
        return self._find_doc(index, doc_id)

    def get_document_content(self, doc_id: str) -> str | None:
        doc = self.get_document(doc_id)
        if not doc:
            return None
        filepath = self.docs_dir / doc["category"] / doc["filename"]
        if filepath.exists():
            return filepath.read_text()
        return None

    def list_documents(self, category: str | None = None) -> list[dict]:
        index = self._load_index()
        docs = index.get("documents", [])
        if category:
            docs = [d for d in docs if d["category"] == category]
        return docs

    def get_index_summary(self) -> str:
        """Return a compact summary of all docs for prompt injection."""
        docs = self.list_documents()
        if not docs:
            return ""

        # Sort by updated_at descending, cap at MAX_INDEX_SUMMARY_DOCS
        docs.sort(key=lambda d: d.get("updated_at", ""), reverse=True)
        docs = docs[:MAX_INDEX_SUMMARY_DOCS]

        lines = ["Available project documents:"]
        for d in docs:
            lines.append(
                f"- [{d['category']}] {d['title']} ({d['id']}) "
                f"— {d['category']}/{d['filename']}"
            )
        lines.append(f"\nDocument root: {self.docs_dir}")
        return "\n".join(lines)

    # ── Internals ──

    def _load_index(self) -> dict:
        try:
            return json.loads(self.index_path.read_text())
        except (json.JSONDecodeError, OSError):
            return {"documents": []}

    def _save_index(self, index: dict):
        self.index_path.write_text(json.dumps(index, indent=2))

    @staticmethod
    def _find_doc(index: dict, doc_id: str) -> dict | None:
        for d in index.get("documents", []):
            if d["id"] == doc_id:
                return d
        return None


def _slugify(text: str) -> str:
    """Convert title to a safe filename slug."""
    slug = text.lower().strip()
    slug = "".join(c if c.isalnum() or c in (" ", "-") else "" for c in slug)
    slug = slug.replace(" ", "-")
    # Collapse multiple dashes
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:60] or "untitled"
