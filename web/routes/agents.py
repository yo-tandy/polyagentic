from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.message import Message, MessageType

logger = logging.getLogger(__name__)

router = APIRouter()


class AgentMessageRequest(BaseModel):
    message: str


@router.get("/agents")
async def get_agents(request: Request):
    registry = request.app.state.registry
    ts = getattr(request.app.state, "team_structure", None)
    fixed_ids = (
        ts.get_fixed_ids() if ts
        else {"manny", "rory", "innes", "perry", "jerry"}
    )

    template_repo = getattr(request.app.state, "template_repo", None)
    sourced_ids: set[str] = set()
    if template_repo:
        try:
            sourced_ids = await template_repo.get_source_agent_ids()
        except Exception:
            logger.debug("Failed to fetch source_agent_ids", exc_info=True)

    agents = registry.get_status_summary()
    for a in agents:
        is_fixed = a["id"] in fixed_ids
        a["is_fixed"] = is_fixed
        a["in_repository"] = is_fixed or a["id"] in sourced_ids
    return {"agents": agents}


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, request: Request):
    registry = request.app.state.registry
    agent = registry.get(agent_id)
    if agent is None:
        return JSONResponse({"error": "Agent not found"}, status_code=404)
    return agent.to_info_dict()


@router.get("/agents/{agent_id}/diagnostics")
async def get_agent_diagnostics(agent_id: str, request: Request):
    """Return diagnostic data for the agent diagnostics modal."""
    registry = request.app.state.registry
    broker = request.app.state.broker
    task_board = request.app.state.task_board
    session_store = getattr(request.app.state, "session_store", None)

    agent = registry.get(agent_id)
    if agent is None:
        return JSONResponse({"error": "Agent not found"}, status_code=404)

    # 1. Inbox tasks — read TASK-type files from inbox directory
    inbox_tasks = []
    if agent.inbox_dir.exists():
        for f in sorted(agent.inbox_dir.glob("*.json"), key=lambda p: p.stat().st_mtime):
            try:
                msg = Message.from_file(f)
                if msg.type == MessageType.TASK:
                    task_title = None
                    if msg.task_id and task_board:
                        task = task_board.get_task(msg.task_id)
                        if task:
                            task_title = task.title
                    inbox_tasks.append({
                        "message_id": msg.id,
                        "task_id": msg.task_id,
                        "task_title": task_title or msg.content[:80],
                        "sender": msg.sender,
                        "created_at": msg.timestamp,
                        "content_preview": msg.content[:120],
                    })
            except Exception:
                logger.debug("Diagnostics: failed to read inbox file %s", f, exc_info=True)

    # 2. Workingbox task — at most one file
    workingbox_task = None
    if agent.workingbox_dir.exists():
        wb_files = list(agent.workingbox_dir.glob("*.json"))
        if wb_files:
            try:
                msg = Message.from_file(wb_files[0])
                task_title = None
                if msg.task_id and task_board:
                    task = task_board.get_task(msg.task_id)
                    if task:
                        task_title = task.title
                workingbox_task = {
                    "message_id": msg.id,
                    "task_id": msg.task_id,
                    "task_title": task_title or msg.content[:80],
                    "sender": msg.sender,
                    "type": msg.type.value,
                    "created_at": msg.timestamp,
                    "content_preview": msg.content[:120],
                }
            except Exception:
                logger.debug("Diagnostics: failed to read workingbox file", exc_info=True)

    # 3. Session stats from session store
    session_stats = None
    if session_store:
        info = session_store.get_info(agent_id)
        if info:
            req_count = info.get("request_count", 0)
            total_dur = info.get("total_duration_ms", 0)
            session_stats = {
                "request_count": req_count,
                "error_count": info.get("error_count", 0),
                "consecutive_errors": info.get("consecutive_errors", 0),
                "avg_duration_ms": round(total_dur / req_count) if req_count else 0,
                "total_cost_usd": round(info.get("total_cost_usd", 0) or 0, 4),
                "total_input_tokens": info.get("total_input_tokens", 0),
                "total_output_tokens": info.get("total_output_tokens", 0),
                "last_error": info.get("last_error"),
            }

    # 4. Recent activity — filter broker's activity_log for this agent
    recent_activity = []
    if broker:
        all_activity = broker.get_activity_log(limit=500)
        agent_activity = [
            entry for entry in all_activity
            if entry.get("sender") == agent_id or entry.get("recipient") == agent_id
        ]
        recent_activity = agent_activity[-20:]

    # 5. Current task details (if any)
    current_task_detail = None
    if agent.current_task_id and task_board:
        ct = task_board.get_task(agent.current_task_id)
        if ct:
            current_task_detail = {
                "id": ct.id,
                "title": ct.title,
                "description": (ct.description or "")[:500],
                "status": ct.status.value,
                "priority": ct.priority,
                "estimate": ct.estimate,
                "labels": ct.labels,
                "category": ct.category,
                "phase_id": ct.phase_id,
                "assignee": ct.assignee,
                "role": ct.role,
                "created_by": ct.created_by,
                "scope_approved": ct.scope_approved,
                "started_at": ct.started_at,
                "progress_notes": ct.progress_notes[-10:],  # last 10 notes
            }

    # 6. Assigned tasks summary (all tasks this agent owns or can work on)
    assigned_tasks = []
    if task_board:
        for t in task_board.get_workable_tasks(agent.agent_id, agent.role):
            if t.id == agent.current_task_id:
                continue  # already shown in current_task_detail
            assigned_tasks.append({
                "id": t.id,
                "title": t.title,
                "status": t.status.value,
                "priority": t.priority,
                "category": t.category,
            })

    return {
        "agent_id": agent.agent_id,
        "agent_name": agent.name,
        "role": agent.role,
        "status": agent.status.value,
        "activity": agent.activity,
        "current_task_id": agent.current_task_id,
        "messages_processed": agent.messages_processed,
        "model": agent.model,
        "inbox_tasks": inbox_tasks,
        "workingbox_task": workingbox_task,
        "current_task": current_task_detail,
        "assigned_tasks": assigned_tasks,
        "session_stats": session_stats,
        "recent_activity": recent_activity,
    }


@router.post("/agents/{agent_id}/message")
async def send_agent_message(agent_id: str, body: AgentMessageRequest, request: Request):
    """Send a message directly to any agent."""
    registry = request.app.state.registry
    broker = request.app.state.broker

    agent = registry.get(agent_id)
    if agent is None:
        return JSONResponse({"error": f"Agent '{agent_id}' not found"}, status_code=404)

    msg = Message(
        sender="user",
        recipient=agent_id,
        type=MessageType.CHAT,
        content=body.message,
    )
    await broker.deliver(msg)
    return {"message_id": msg.id, "status": "delivered"}


@router.post("/agents/{agent_id}/status-request")
async def request_agent_status(agent_id: str, request: Request):
    """Ask an agent to produce a status report of its current work."""
    registry = request.app.state.registry
    broker = request.app.state.broker
    task_board = request.app.state.task_board

    agent = registry.get(agent_id)
    if agent is None:
        return JSONResponse({"error": f"Agent '{agent_id}' not found"}, status_code=404)

    tasks = task_board.get_tasks_by_assignee(agent_id)
    task_summary = "\n".join(
        f"- [{t.status.value}] {t.title}" for t in tasks
    ) or "No tasks assigned."

    msg = Message(
        sender="user",
        recipient=agent_id,
        type=MessageType.CHAT,
        content=(
            "Please provide a concise 2-paragraph status report of your current "
            "and recently completed work.\n\n"
            f"Your assigned tasks:\n{task_summary}"
        ),
    )
    await broker.deliver(msg)
    return {"message_id": msg.id, "status": "delivered"}
