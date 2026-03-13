"""Authentication middleware — protects all routes except public paths.

When AUTH_ENABLED is false (default), all requests get a default anonymous
user context so the rest of the app can always rely on request.state.user.
"""

from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from core.constants import JWT_REFRESH_WINDOW_SECONDS
from web.auth import decode_jwt, create_jwt, set_auth_cookie, _get_jwt_secret

logger = logging.getLogger(__name__)

# Paths that never require auth
PUBLIC_PREFIXES = ("/auth/", "/static/")

# Anonymous user context when auth is disabled (gets admin role since auth is off)
ANONYMOUS_USER = {
    "id": "anonymous",
    "org_id": "default",
    "email": "anonymous@local",
    "name": "Anonymous",
    "role": "admin",
}


class AuthMiddleware(BaseHTTPMiddleware):
    """Inject request.state.user on every request.

    When AUTH_ENABLED=true: validates JWT cookie, returns 401/redirect on failure.
    When AUTH_ENABLED=false: sets anonymous user context (backward compatible).
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Always allow public paths through (but still try to parse user)
        is_public = any(path.startswith(p) for p in PUBLIC_PREFIXES)

        # Check if auth is enabled
        config = getattr(request.app.state, "config_provider", None)
        auth_enabled = False
        if config:
            auth_enabled = config.get("AUTH_ENABLED", False)
            if isinstance(auth_enabled, str):
                auth_enabled = auth_enabled.lower() in ("true", "1", "yes")

        if not auth_enabled:
            # Auth disabled — set anonymous user and pass through
            request.state.user = ANONYMOUS_USER
            return await call_next(request)

        # Auth enabled — public paths pass through without user context
        if is_public:
            request.state.user = None
            return await call_next(request)

        # Extract JWT from cookie
        token = request.cookies.get("session_token")
        if not token:
            return self._unauthorized(request)

        # Decode JWT
        jwt_secret = _get_jwt_secret(request)
        payload = decode_jwt(token, jwt_secret)
        if not payload:
            return self._unauthorized(request)

        # Set user context (role defaults to "member" for legacy tokens)
        request.state.user = {
            "id": payload["sub"],
            "org_id": payload["org_id"],
            "email": payload["email"],
            "name": payload["name"],
            "role": payload.get("role", "admin"),
        }

        response = await call_next(request)

        # Sliding window refresh: re-issue JWT if within refresh window
        exp = payload.get("exp", 0)
        if exp - time.time() < JWT_REFRESH_WINDOW_SECONDS:
            new_token = create_jwt(
                payload["sub"], payload["org_id"],
                payload["email"], payload["name"],
                jwt_secret,
                role=payload.get("role", "admin"),
            )
            set_auth_cookie(response, new_token, request=request)

        return response

    @staticmethod
    def _unauthorized(request: Request):
        """Return 401 for API calls, redirect for browser requests."""
        path = request.url.path
        if path.startswith("/api/") or path == "/ws":
            return JSONResponse(
                {"error": "Not authenticated"},
                status_code=401,
            )
        return RedirectResponse("/auth/login")
