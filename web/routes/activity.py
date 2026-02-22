from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/activity")
async def get_activity(request: Request, limit: int = 100):
    broker = request.app.state.broker
    return {"activity": broker.get_activity_log(limit=limit)}
