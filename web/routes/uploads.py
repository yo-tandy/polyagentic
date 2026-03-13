"""File upload routes — document/image uploads into the KB."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from core.constants import gen_id
from core.file_processor import process_file, validate_file
from core.message import Message, MessageType

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    context: str = Form("project"),
    target_agent: str = Form(""),
    description: str = Form(""),
):
    """Upload a file. Creates a KB entry and optionally sends to an agent."""
    project_store = request.app.state.project_store
    kb = request.app.state.knowledge_base
    if not project_store or not kb:
        return JSONResponse({"error": "Services not available"}, status_code=503)

    project = project_store.get_active_project()
    if not project:
        return JSONResponse({"error": "No active project"}, status_code=400)

    # Read and validate
    content_bytes = await file.read()
    try:
        validate_file(file.filename or "unknown", len(content_bytes))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    # Save to disk
    project_id = project["id"]
    uploads_dir = project_store.get_project_dir(project_id) / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename or "unknown").suffix.lower()
    unique_name = f"{gen_id()}{ext}"
    file_path = uploads_dir / unique_name
    file_path.write_bytes(content_bytes)

    # Process (extract text)
    try:
        processed = process_file(file_path)
    except Exception as e:
        file_path.unlink(missing_ok=True)
        return JSONResponse({"error": f"Failed to process file: {e}"}, status_code=400)

    # Build KB content
    kb_content = processed.extracted_text
    if description:
        kb_content = f"User description: {description}\n\n---\n\n{kb_content}"

    # Create KB entry
    title = file.filename or unique_name
    doc = await kb.add_uploaded_document(
        title=title,
        content=kb_content,
        created_by="user",
        upload_path=str(file_path),
        file_type=processed.file_type,
        file_size=processed.file_size,
    )

    # Broadcast KB update
    broker = request.app.state.broker
    if broker:
        await broker.broadcast_event({"event_type": "knowledge_updated", "data": {}})

    # If chat context, send message to agent
    if context == "chat" and broker:
        ts = getattr(request.app.state, "team_structure", None)
        ufa = ts.user_facing_agent if ts else "manny"
        recipient = target_agent or ufa

        msg_content = f"The user has uploaded a document: **{title}**\n"
        msg_content += f"KB Document ID: {doc['id']}\n"
        if description:
            msg_content += f"User's note: {description}\n"
        msg_content += f"\n---\n\n{processed.extracted_text[:4000]}"
        if len(processed.extracted_text) > 4000:
            msg_content += f"\n\n... (truncated, full content in KB doc {doc['id']})"

        msg = Message(
            sender="user",
            recipient=recipient,
            type=MessageType.CHAT,
            content=msg_content,
            metadata={"uploaded_doc_id": doc["id"], "uploaded_filename": title},
        )
        await broker.deliver(msg)

        broker._chat_history.append({
            "message_id": msg.id,
            "sender": "user",
            "content": f"[Uploaded: {title}]" + (f" — {description}" if description else ""),
            "timestamp": msg.timestamp,
            "task_id": None,
        })

    return {
        "status": "uploaded",
        "document": doc,
        "file_type": processed.file_type,
    }


@router.get("/uploads/{doc_id}/download")
async def download_file(doc_id: str, request: Request):
    """Download the original uploaded file."""
    kb = request.app.state.knowledge_base
    project_store = request.app.state.project_store
    if not kb or not project_store:
        return JSONResponse({"error": "KB not available"}, status_code=503)

    doc = await kb.get_document(doc_id)
    if not doc or not doc.get("upload_path"):
        return JSONResponse({"error": "File not found"}, status_code=404)

    file_path = Path(doc["upload_path"]).resolve()

    # Path traversal protection: ensure the file lives inside a project dir
    project = project_store.get_active_project()
    if project:
        uploads_root = project_store.get_project_dir(project["id"]).resolve()
        try:
            file_path.relative_to(uploads_root)
        except ValueError:
            logger.warning(
                "Path traversal blocked: doc %s references %s outside %s",
                doc_id, file_path, uploads_root,
            )
            return JSONResponse({"error": "Access denied"}, status_code=403)

    if not file_path.exists():
        return JSONResponse({"error": "File no longer exists on disk"}, status_code=404)

    return FileResponse(
        str(file_path),
        filename=doc.get("title", file_path.name),
        media_type="application/octet-stream",
    )
