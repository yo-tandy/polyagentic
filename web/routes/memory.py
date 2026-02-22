from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/memory/{agent_id}")
async def get_agent_memory(agent_id: str, request: Request):
    mm = request.app.state.memory_manager
    if not mm:
        return {"personality": "", "project": ""}
    return {
        "personality": mm.get_personality_memory(agent_id),
        "project": mm.get_project_memory(agent_id),
    }


@router.get("/memory/{agent_id}/personality")
async def get_personality_memory(agent_id: str, request: Request):
    mm = request.app.state.memory_manager
    if not mm:
        return {"content": ""}
    return {"content": mm.get_personality_memory(agent_id)}


@router.get("/memory/{agent_id}/project")
async def get_project_memory(agent_id: str, request: Request):
    mm = request.app.state.memory_manager
    if not mm:
        return {"content": ""}
    return {"content": mm.get_project_memory(agent_id)}
