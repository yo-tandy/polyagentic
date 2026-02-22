from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


class AddDocumentRequest(BaseModel):
    title: str
    category: str
    content: str
    created_by: str = "user"


class UpdateDocumentRequest(BaseModel):
    content: str
    updated_by: str = "user"


@router.get("/knowledge")
async def list_documents(request: Request, category: str | None = None):
    kb = request.app.state.knowledge_base
    if not kb:
        return {"documents": []}
    return {"documents": kb.list_documents(category)}


@router.post("/knowledge")
async def add_document(body: AddDocumentRequest, request: Request):
    kb = request.app.state.knowledge_base
    if not kb:
        return {"error": "Knowledge base not available"}, 500
    try:
        doc = kb.add_document(
            title=body.title, category=body.category,
            content=body.content, created_by=body.created_by,
        )
        return {"status": "created", "document": doc}
    except ValueError as e:
        return {"error": str(e)}, 400


@router.get("/knowledge/{doc_id}")
async def get_document(doc_id: str, request: Request):
    kb = request.app.state.knowledge_base
    if not kb:
        return {"error": "Knowledge base not available"}, 500
    doc = kb.get_document(doc_id)
    if not doc:
        return {"error": "Document not found"}, 404
    content = kb.get_document_content(doc_id)
    return {"document": doc, "content": content}


@router.put("/knowledge/{doc_id}")
async def update_document(doc_id: str, body: UpdateDocumentRequest, request: Request):
    kb = request.app.state.knowledge_base
    if not kb:
        return {"error": "Knowledge base not available"}, 500
    doc = kb.update_document(doc_id, body.content, body.updated_by)
    if not doc:
        return {"error": "Document not found"}, 404
    return {"status": "updated", "document": doc}


@router.delete("/knowledge/{doc_id}")
async def delete_document(doc_id: str, request: Request):
    kb = request.app.state.knowledge_base
    if not kb:
        return {"error": "Knowledge base not available"}, 500
    # Remove from index only (file stays for safety)
    doc = kb.get_document(doc_id)
    if not doc:
        return {"error": "Document not found"}, 404
    index = kb._load_index()
    index["documents"] = [d for d in index["documents"] if d["id"] != doc_id]
    kb._save_index(index)
    return {"status": "deleted", "doc_id": doc_id}
