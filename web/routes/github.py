from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


class CreateRepoRequest(BaseModel):
    name: str
    description: str = ""
    private: bool = True


class MergePRRequest(BaseModel):
    method: str = "squash"


@router.post("/github/repo")
async def create_repo(body: CreateRepoRequest, request: Request):
    """Create a GitHub repo for the active project."""
    git_manager = request.app.state.git_manager
    try:
        result = await git_manager.create_github_repo(
            name=body.name,
            description=body.description,
            private=body.private,
        )
        # Store URL in project metadata
        project_store = request.app.state.project_store
        active_id = project_store.get_active_project_id()
        if active_id:
            project_store.update_project(active_id, github_url=result["url"])

        return {"status": "created", **result}
    except RuntimeError as e:
        return {"error": str(e)}


@router.get("/github/prs")
async def list_prs(request: Request):
    """List open pull requests."""
    git_manager = request.app.state.git_manager
    prs = await git_manager.list_pull_requests()
    return {"pull_requests": prs}


@router.get("/github/prs/{number}")
async def get_pr(number: int, request: Request):
    """Get PR details."""
    git_manager = request.app.state.git_manager
    pr = await git_manager.get_pull_request(number)
    if not pr:
        return {"error": f"PR #{number} not found"}
    return {"pull_request": pr}


@router.post("/github/prs/{number}/merge")
async def merge_pr(number: int, body: MergePRRequest, request: Request):
    """Merge a pull request."""
    git_manager = request.app.state.git_manager
    try:
        result = await git_manager.merge_pull_request(number, method=body.method)
        return {"status": "merged", **result}
    except RuntimeError as e:
        return {"error": str(e)}
