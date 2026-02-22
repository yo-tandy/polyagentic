from __future__ import annotations

import logging

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/git/branches")
async def get_branches(request: Request):
    try:
        git_manager = request.app.state.git_manager
        branches = await git_manager.get_branches()
        return {"branches": branches}
    except Exception as e:
        logger.exception("Error getting git branches")
        return {"branches": [], "error": str(e)}


@router.get("/git/log")
async def get_log(request: Request, branch: str | None = None, limit: int = 20):
    try:
        git_manager = request.app.state.git_manager
        log = await git_manager.get_log(branch=branch, limit=limit)
        return {"log": log}
    except Exception as e:
        logger.exception("Error getting git log")
        return {"log": [], "error": str(e)}


@router.get("/git/status")
async def get_status(request: Request):
    try:
        git_manager = request.app.state.git_manager
        status = await git_manager.get_status()
        return status
    except Exception as e:
        logger.exception("Error getting git status")
        return {"current_branch": "unknown", "changes": [], "error": str(e)}
