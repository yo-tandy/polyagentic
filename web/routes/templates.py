"""Agent template routes — CRUD for the agent repository."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from web.auth import require_admin

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_tenant(request: Request) -> str:
    user = getattr(request.state, "user", {})
    return user.get("org_id", "default")


class CreateTemplateRequest(BaseModel):
    name: str
    title: str
    personality: str = ""
    model: str = "sonnet"
    allowed_tools: str = ""
    scope: str = "org"
    tags: list[str] = []
    source_agent_id: str | None = None


class UpdateTemplateRequest(BaseModel):
    name: str | None = None
    title: str | None = None
    personality: str | None = None
    model: str | None = None
    allowed_tools: str | None = None
    scope: str | None = None
    tags: list[str] | None = None


def _to_dict(t) -> dict:
    return {
        "id": t.id,
        "scope": t.scope,
        "name": t.name,
        "title": t.title,
        "personality": t.personality,
        "model": t.model,
        "allowed_tools": t.allowed_tools,
        "tags": t.tags or [],
        "source_agent_id": t.source_agent_id,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


@router.get("/templates")
async def list_templates(request: Request, scope: str | None = None, q: str | None = None):
    """List agent templates. Optional filters: ?scope=global|org, ?q=search."""
    repo = request.app.state.template_repo
    if not repo:
        return {"templates": []}
    tenant_id = _get_tenant(request)
    if q:
        templates = await repo.search(q, tenant_id=tenant_id)
    else:
        templates = await repo.get_all(tenant_id=tenant_id, scope=scope)
    return {"templates": [_to_dict(t) for t in templates]}


@router.get("/templates/{template_id}")
async def get_template(template_id: str, request: Request):
    repo = request.app.state.template_repo
    if not repo:
        return JSONResponse({"error": "Template repository not available"}, status_code=503)
    tmpl = await repo.get(template_id)
    if not tmpl:
        return JSONResponse({"error": "Template not found"}, status_code=404)
    return _to_dict(tmpl)


@router.post("/templates")
async def create_template(
    body: CreateTemplateRequest,
    request: Request,
    _admin: dict = Depends(require_admin),
):
    repo = request.app.state.template_repo
    if not repo:
        return JSONResponse({"error": "Template repository not available"}, status_code=503)
    tenant_id = _get_tenant(request)
    tmpl = await repo.create(
        name=body.name,
        title=body.title,
        personality=body.personality,
        model=body.model,
        allowed_tools=body.allowed_tools,
        scope=body.scope,
        tags=body.tags,
        source_agent_id=body.source_agent_id,
        tenant_id=tenant_id,
    )
    return _to_dict(tmpl)


@router.put("/templates/{template_id}")
async def update_template(
    template_id: str,
    body: UpdateTemplateRequest,
    request: Request,
    _admin: dict = Depends(require_admin),
):
    repo = request.app.state.template_repo
    if not repo:
        return JSONResponse({"error": "Template repository not available"}, status_code=503)
    kwargs = {k: v for k, v in body.model_dump().items() if v is not None}
    if not kwargs:
        return JSONResponse({"error": "No fields to update"}, status_code=400)
    tmpl = await repo.update(template_id, **kwargs)
    if not tmpl:
        return JSONResponse({"error": "Template not found"}, status_code=404)
    return _to_dict(tmpl)


@router.delete("/templates/{template_id}")
async def delete_template(
    template_id: str,
    request: Request,
    _admin: dict = Depends(require_admin),
):
    repo = request.app.state.template_repo
    if not repo:
        return JSONResponse({"error": "Template repository not available"}, status_code=503)
    ok = await repo.delete(template_id)
    if not ok:
        return JSONResponse({"error": "Template not found"}, status_code=404)
    return {"deleted": True}
