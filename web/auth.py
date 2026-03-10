"""Authentication module — Google OAuth 2.0 + JWT cookie management.

Routes:
  GET  /auth/login               — serve login page
  GET  /auth/google              — redirect to Google consent screen
  GET  /auth/google/callback     — handle OAuth callback
  POST /auth/complete-signup     — new user picks org (create or join)
  POST /auth/logout              — clear session cookie
  GET  /auth/me                  — return current user info
"""

from __future__ import annotations

import json
import logging
import secrets
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from db.repositories.user_repo import UserRepository
from db.repositories.org_repo import OrgRepository
from db.repositories.invite_repo import InviteRepository

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/auth", tags=["auth"])

STATIC_DIR = Path(__file__).parent / "static"

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


# ── JWT helpers ─────────────────────────────────────────────────────

def _get_jwt_secret(request: Request) -> str:
    """Get or auto-generate JWT secret."""
    config = request.app.state.config_provider
    secret = config.get("JWT_SECRET", "")
    if not secret:
        # Auto-generate on first use
        secret = secrets.token_hex(32)
        logger.warning("JWT_SECRET not configured — generated ephemeral key. "
                       "Set JWT_SECRET in config for persistent sessions.")
    return secret


def create_jwt(
    user_id: str, org_id: str, email: str, name: str,
    secret: str, ttl_seconds: int = 3600,
) -> str:
    """Create a signed JWT with 1hr default expiry."""
    now = int(time.time())
    payload = {
        "sub": user_id,
        "org_id": org_id,
        "email": email,
        "name": name,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_jwt(token: str, secret: str) -> dict | None:
    """Decode and validate a JWT. Returns None on failure."""
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def set_auth_cookie(response, token: str) -> None:
    """Set the session JWT as an HTTP-only cookie."""
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        secure=False,  # False for localhost; True in production
        samesite="lax",
        max_age=3600,
        path="/",
    )


def clear_auth_cookie(response) -> None:
    """Remove the session cookie."""
    response.delete_cookie(key="session_token", path="/")


# ── Google OAuth config helpers ─────────────────────────────────────

def _get_google_config(request: Request) -> tuple[str, str, str]:
    """Return (client_id, client_secret, redirect_uri)."""
    config = request.app.state.config_provider
    client_id = config.get("GOOGLE_CLIENT_ID", "") or ""
    client_secret = config.get("GOOGLE_CLIENT_SECRET", "") or ""
    # Build redirect URI from request
    redirect_uri = str(request.base_url).rstrip("/") + "/auth/google/callback"
    return client_id, client_secret, redirect_uri


# ── Routes ──────────────────────────────────────────────────────────

@auth_router.get("/login")
async def login_page():
    """Serve the login page."""
    login_file = STATIC_DIR / "login.html"
    if login_file.exists():
        return FileResponse(str(login_file))
    return HTMLResponse("<h1>Login</h1><a href='/auth/google'>Sign in with Google</a>")


@auth_router.get("/google")
async def google_login(request: Request):
    """Redirect to Google OAuth consent screen."""
    client_id, _, redirect_uri = _get_google_config(request)
    if not client_id:
        return JSONResponse(
            {"error": "Google OAuth not configured. Set GOOGLE_CLIENT_ID in config."},
            status_code=500,
        )

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
    }
    return RedirectResponse(f"{GOOGLE_AUTH_URL}?{urlencode(params)}")


@auth_router.get("/google/callback")
async def google_callback(request: Request, code: str = "", error: str = ""):
    """Handle Google OAuth callback."""
    if error or not code:
        return RedirectResponse(f"/auth/login?error={error or 'no_code'}")

    client_id, client_secret, redirect_uri = _get_google_config(request)

    # Exchange code for tokens
    async with httpx.AsyncClient() as client:
        token_response = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        })

    if token_response.status_code != 200:
        logger.error("Token exchange failed: %s", token_response.text)
        return RedirectResponse("/auth/login?error=token_exchange_failed")

    tokens = token_response.json()
    access_token = tokens.get("access_token")

    # Get user info from Google
    async with httpx.AsyncClient() as client:
        userinfo_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if userinfo_resp.status_code != 200:
        return RedirectResponse("/auth/login?error=userinfo_failed")

    google_user = userinfo_resp.json()
    google_sub = google_user.get("sub", "")
    email = google_user.get("email", "")
    name = google_user.get("name", email)
    picture = google_user.get("picture", "")

    # Look up existing user
    user_repo: UserRepository = request.app.state.user_repo
    user = await user_repo.get_by_google_sub(google_sub)

    if user:
        # Existing user — update last login and redirect to dashboard
        await user_repo.update_last_login(user.id)
        await user_repo.update_profile(user.id, name=name, picture_url=picture)
        jwt_secret = _get_jwt_secret(request)
        token = create_jwt(user.id, user.org_id, user.email, user.name, jwt_secret)
        response = RedirectResponse("/", status_code=302)
        set_auth_cookie(response, token)
        return response

    # New user — need to pick/create org
    # Store Google info in a short-lived signed token for the signup step
    jwt_secret = _get_jwt_secret(request)
    signup_token = jwt.encode({
        "google_sub": google_sub,
        "email": email,
        "name": name,
        "picture": picture,
        "exp": int(time.time()) + 600,  # 10 min to complete signup
    }, jwt_secret, algorithm="HS256")

    return RedirectResponse(f"/auth/login?signup=true&token={signup_token}")


class CompleteSignupRequest(BaseModel):
    signup_token: str
    action: str  # "create_org" or "join_org"
    org_name: str | None = None
    invite_code: str | None = None


@auth_router.post("/complete-signup")
async def complete_signup(body: CompleteSignupRequest, request: Request):
    """Complete new user signup — create or join an org."""
    jwt_secret = _get_jwt_secret(request)

    # Decode the signup token
    payload = decode_jwt(body.signup_token, jwt_secret)
    if not payload:
        return JSONResponse({"error": "Signup session expired. Please sign in again."}, status_code=400)

    google_sub = payload["google_sub"]
    email = payload["email"]
    name = payload["name"]
    picture = payload.get("picture", "")

    user_repo: UserRepository = request.app.state.user_repo
    org_repo: OrgRepository = request.app.state.org_repo
    invite_repo: InviteRepository = request.app.state.invite_repo

    # Check if user was already created (double-submit)
    existing = await user_repo.get_by_google_sub(google_sub)
    if existing:
        token = create_jwt(existing.id, existing.org_id, existing.email, existing.name, jwt_secret)
        return JSONResponse({"redirect": "/", "token": token})

    org_id = None

    if body.action == "create_org":
        if not body.org_name or not body.org_name.strip():
            return JSONResponse({"error": "Organization name is required."}, status_code=400)
        org_id = f"org_{uuid.uuid4().hex[:12]}"
        await org_repo.create(id=org_id, name=body.org_name.strip())
        logger.info("New org created: %s (%s)", body.org_name, org_id)

    elif body.action == "join_org":
        if not body.invite_code or not body.invite_code.strip():
            return JSONResponse({"error": "Invite code is required."}, status_code=400)
        invite = await invite_repo.get_by_token(body.invite_code.strip())
        if not invite or not invite_repo.is_valid(invite):
            return JSONResponse({"error": "Invalid or expired invite link."}, status_code=400)
        org_id = invite.org_id
        await invite_repo.increment_use_count(invite.id)

    else:
        return JSONResponse({"error": "Invalid action."}, status_code=400)

    # Create user
    user_id = f"u_{uuid.uuid4().hex[:12]}"
    user = await user_repo.create(
        id=user_id, email=email, name=name, google_sub=google_sub,
        org_id=org_id, picture_url=picture,
    )
    await user_repo.update_last_login(user.id)

    token = create_jwt(user.id, org_id, email, name, jwt_secret)
    return JSONResponse({"redirect": "/", "token": token})


@auth_router.post("/logout")
async def logout():
    """Clear the session cookie and redirect to login."""
    response = RedirectResponse("/auth/login", status_code=302)
    clear_auth_cookie(response)
    return response


@auth_router.get("/me")
async def get_current_user_info(request: Request):
    """Return the current user's info (from JWT). Used by frontend."""
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse({"user": None}, status_code=401)
    return JSONResponse({"user": user})


# ── Reusable dependency ─────────────────────────────────────────────

async def get_current_user(request: Request) -> dict:
    """FastAPI dependency returning the current user dict from middleware."""
    user = getattr(request.state, "user", None)
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user
