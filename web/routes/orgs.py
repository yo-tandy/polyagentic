"""Organization management routes."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()


def _get_user(request: Request) -> dict:
    return getattr(request.state, "user", {})


def _get_org_id(request: Request) -> str:
    return _get_user(request).get("org_id", "default")


# ── Org info ────────────────────────────────────────────────────────

@router.get("/orgs/current")
async def get_current_org(request: Request):
    """Get current organization info."""
    org_repo = request.app.state.org_repo
    org_id = _get_org_id(request)
    org = await org_repo.get(org_id)
    if not org:
        return JSONResponse({"error": "Organization not found"}, status_code=404)
    return {
        "id": org.id,
        "name": org.name,
        "created_at": org.created_at.isoformat() if org.created_at else None,
    }


class UpdateOrgRequest(BaseModel):
    name: str


@router.put("/orgs/current")
async def update_current_org(body: UpdateOrgRequest, request: Request):
    """Update current organization name."""
    org_repo = request.app.state.org_repo
    org_id = _get_org_id(request)
    await org_repo.update_name(org_id, body.name.strip())
    return {"status": "ok"}


# ── Members ─────────────────────────────────────────────────────────

@router.get("/orgs/members")
async def list_members(request: Request):
    """List all members of the current organization."""
    org_repo = request.app.state.org_repo
    org_id = _get_org_id(request)
    members = await org_repo.list_members(org_id)
    return {
        "members": [
            {
                "id": m.id,
                "name": m.name,
                "email": m.email,
                "picture_url": m.picture_url,
                "last_login_at": m.last_login_at.isoformat() if m.last_login_at else None,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in members
        ]
    }


# ── Invite links ────────────────────────────────────────────────────

class CreateInviteRequest(BaseModel):
    max_uses: int | None = None
    expires_in_days: int | None = 7


@router.post("/orgs/invites")
async def create_invite(body: CreateInviteRequest, request: Request):
    """Create a new invite link for the current organization."""
    invite_repo = request.app.state.invite_repo
    user = _get_user(request)
    org_id = _get_org_id(request)

    invite_id = f"inv_{uuid.uuid4().hex[:12]}"
    token = secrets.token_urlsafe(32)
    expires_at = None
    if body.expires_in_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)

    invite = await invite_repo.create(
        id=invite_id,
        org_id=org_id,
        created_by_user_id=user.get("id", "anonymous"),
        token=token,
        expires_at=expires_at,
        max_uses=body.max_uses,
    )

    # Build the invite URL
    base_url = str(request.base_url).rstrip("/")
    invite_url = f"{base_url}/auth/login?invite={token}"

    return {
        "id": invite.id,
        "token": invite.token,
        "invite_url": invite_url,
        "expires_at": invite.expires_at.isoformat() if invite.expires_at else None,
        "max_uses": invite.max_uses,
    }


@router.get("/orgs/invites")
async def list_invites(request: Request):
    """List active invite links for the current organization."""
    invite_repo = request.app.state.invite_repo
    org_id = _get_org_id(request)
    invites = await invite_repo.list_active(org_id)
    return {
        "invites": [
            {
                "id": inv.id,
                "token": inv.token,
                "expires_at": inv.expires_at.isoformat() if inv.expires_at else None,
                "max_uses": inv.max_uses,
                "use_count": inv.use_count,
                "is_active": inv.is_active,
                "created_at": inv.created_at.isoformat() if inv.created_at else None,
            }
            for inv in invites
        ]
    }


@router.delete("/orgs/invites/{invite_id}")
async def deactivate_invite(invite_id: str, request: Request):
    """Deactivate an invite link."""
    invite_repo = request.app.state.invite_repo
    await invite_repo.deactivate(invite_id)
    return {"status": "ok"}
