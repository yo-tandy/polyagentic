from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.message import Message, MessageType

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_user(request: Request) -> dict:
    return getattr(request.state, "user", {})


# ── Document models ──

class AddDocumentRequest(BaseModel):
    title: str
    category: str
    content: str
    created_by: str | None = None  # Populated from auth context


class UpdateDocumentRequest(BaseModel):
    content: str
    updated_by: str | None = None  # Populated from auth context


# ── Comment models ──

class AddCommentRequest(BaseModel):
    highlighted_text: str
    element_index: int
    comment_text: str
    assigned_to: str
    created_by: str | None = None  # Populated from auth context


class UpdateCommentRequest(BaseModel):
    status: str | None = None
    resolution: str | None = None
    comment_text: str | None = None


# ── Document endpoints ──

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
        return JSONResponse({"error": "Knowledge base not available"}, status_code=503)
    user = _get_user(request)
    created_by = body.created_by or user.get("id", "user")
    try:
        doc = await kb.add_document(
            title=body.title, category=body.category,
            content=body.content, created_by=created_by,
        )
        return {"status": "created", "document": doc}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.get("/knowledge/{doc_id}")
async def get_document(doc_id: str, request: Request):
    kb = request.app.state.knowledge_base
    if not kb:
        return JSONResponse({"error": "Knowledge base not available"}, status_code=503)
    doc = await kb.get_document(doc_id)
    if not doc:
        return JSONResponse({"error": "Document not found"}, status_code=404)
    content = await kb.get_document_content(doc_id)
    comments = await kb.get_comments(doc_id)
    return {"document": doc, "content": content, "comments": comments}


@router.put("/knowledge/{doc_id}")
async def update_document(doc_id: str, body: UpdateDocumentRequest, request: Request):
    kb = request.app.state.knowledge_base
    if not kb:
        return JSONResponse({"error": "Knowledge base not available"}, status_code=503)
    user = _get_user(request)
    updated_by = body.updated_by or user.get("id", "user")
    doc = await kb.update_document(doc_id, body.content, updated_by)
    if not doc:
        return JSONResponse({"error": "Document not found"}, status_code=404)
    return {"status": "updated", "document": doc}


@router.delete("/knowledge/{doc_id}")
async def delete_document(doc_id: str, request: Request):
    kb = request.app.state.knowledge_base
    if not kb:
        return JSONResponse({"error": "Knowledge base not available"}, status_code=503)
    doc = await kb.get_document(doc_id)
    if not doc:
        return JSONResponse({"error": "Document not found"}, status_code=404)
    await kb.delete_document(doc_id)
    return {"status": "deleted", "doc_id": doc_id}


# ── Comment endpoints ──

@router.get("/knowledge/{doc_id}/comments")
async def get_comments(doc_id: str, request: Request):
    kb = request.app.state.knowledge_base
    if not kb:
        return {"comments": []}
    return {"comments": await kb.get_comments(doc_id)}


@router.post("/knowledge/{doc_id}/comments")
async def add_comment(doc_id: str, body: AddCommentRequest, request: Request):
    kb = request.app.state.knowledge_base
    if not kb:
        return JSONResponse({"error": "Knowledge base not available"}, status_code=503)
    user = _get_user(request)
    created_by = body.created_by or user.get("id", "user")
    comment = await kb.add_comment(
        doc_id=doc_id,
        highlighted_text=body.highlighted_text,
        element_index=body.element_index,
        comment_text=body.comment_text,
        assigned_to=body.assigned_to,
        created_by=created_by,
    )
    if not comment:
        return JSONResponse({"error": "Document not found"}, status_code=404)
    return {"status": "created", "comment": comment}


@router.patch("/knowledge/{doc_id}/comments/{comment_id}")
async def update_comment(
    doc_id: str, comment_id: str, body: UpdateCommentRequest, request: Request,
):
    kb = request.app.state.knowledge_base
    if not kb:
        return JSONResponse({"error": "Knowledge base not available"}, status_code=503)
    updates = {}
    if body.status is not None:
        updates["status"] = body.status
    if body.resolution is not None:
        updates["resolution"] = body.resolution
    if body.comment_text is not None:
        updates["comment_text"] = body.comment_text
    result = await kb.update_comment(doc_id, comment_id, **updates)
    if not result:
        return JSONResponse({"error": "Comment not found"}, status_code=404)
    return {"status": "updated", "comment": result}


@router.delete("/knowledge/{doc_id}/comments/{comment_id}")
async def delete_comment(doc_id: str, comment_id: str, request: Request):
    kb = request.app.state.knowledge_base
    if not kb:
        return JSONResponse({"error": "Knowledge base not available"}, status_code=503)
    if await kb.delete_comment(doc_id, comment_id):
        return {"status": "deleted"}
    return JSONResponse({"error": "Comment not found"}, status_code=404)


@router.post("/knowledge/{doc_id}/comments/dispatch")
async def dispatch_comments(doc_id: str, request: Request):
    """Group open comments by assigned agent and create one review task per agent."""
    kb = request.app.state.knowledge_base
    task_board = request.app.state.task_board
    broker = request.app.state.broker
    if not kb or not task_board or not broker:
        return JSONResponse({"error": "Required services not available"}, status_code=503)

    doc = await kb.get_document(doc_id)
    if not doc:
        return JSONResponse({"error": "Document not found"}, status_code=404)

    doc_content = await kb.get_document_content(doc_id) or "(could not load content)"

    comments = await kb.get_comments(doc_id)
    open_comments = [c for c in comments if c["status"] == "open"]
    if not open_comments:
        return {"status": "no_comments", "tasks_created": []}

    # Group by assigned_to
    by_agent: dict[str, list[dict]] = {}
    for c in open_comments:
        by_agent.setdefault(c["assigned_to"], []).append(c)

    user = _get_user(request)
    user_id = user.get("id", "user")

    tasks_created = []
    for agent_id, agent_comments in by_agent.items():
        comment_list = "\n".join(
            f"- **Comment {c['id']}** on: \"{c['highlighted_text'][:80]}\"\n"
            f"  Comment: {c['comment_text']}"
            for c in agent_comments
        )
        description = (
            f"Review the following comments on document \"{doc['title']}\" "
            f"(doc_id: {doc_id}).\n\n"
            f"## Required workflow\n"
            f"1. Review the current document content (included below)\n"
            f"2. For each comment, evaluate whether the document needs editing\n"
            f"3. If edits are needed: emit an `update_document` action with the "
            f"FULL updated document content BEFORE resolving\n"
            f"4. Then emit `resolve_comments` to mark them addressed\n"
            f"5. Finally emit `update_task` to mark this task as `done`\n\n"
            f"**IMPORTANT**: The system tracks whether you actually edited the "
            f"document. If you resolve comments without emitting `update_document`, "
            f"the resolution will be flagged as UNVERIFIED and shown to the user "
            f"as such. Only skip the edit if the document genuinely needs no changes.\n\n"
            f"Comments to address:\n{comment_list}\n\n"
            f"## Current document content\n"
            f"```markdown\n{doc_content}\n```\n\n"
            f"## Actions to emit\n"
            f"First, edit the document (include the FULL updated content):\n"
            f"```action\n"
            f'{{"action": "update_document", "doc_id": "{doc_id}", '
            f'"content": "Full updated markdown content..."}}\n```\n\n'
            f"Then resolve the comments:\n"
            f"```action\n"
            f'{{"action": "resolve_comments", "doc_id": "{doc_id}", '
            f'"resolutions": ['
            f'{{"comment_id": "cmt-xxx", "resolution": "Description of what was changed"}}'
            f"]}}\n```\n\n"
            f"Finally, close the task:\n"
            f"```action\n"
            f'{{"action": "update_task", "task_id": "<your_task_id>", '
            f'"status": "done", "completion_summary": "Reviewed and resolved comments"}}'
            f"\n```"
        )

        task = await task_board.create_task(
            title=f"Review comments on: {doc['title']}",
            description=description,
            created_by=user_id,
            assignee=agent_id,
            priority=2,
            labels=["comments", "review"],
        )
        tasks_created.append(task.id)

        msg = Message(
            sender=user_id,
            recipient=agent_id,
            type=MessageType.TASK,
            content=description,
            task_id=task.id,
            metadata={"task_title": task.title, "comment_review": True},
        )
        await broker.deliver(msg)

    logger.info(
        "Dispatched comment review tasks for doc '%s': %s",
        doc["title"], tasks_created,
    )
    return {"status": "dispatched", "tasks_created": tasks_created}
