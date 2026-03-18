from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.middleware.base import BaseHTTPMiddleware

from core.constants import (
    CORS_ALLOW_HEADERS,
    CORS_ALLOW_METHODS,
    DEFAULT_CORS_ORIGINS,
    LOCALHOST_HOSTS,
    SECURITY_HEADERS,
)
from web.routes import chat, agents, tasks, activity, git, config, ws, projects, knowledge, memory, sessions, conversations, github, uploads, phases, orgs, mcp, templates
from web.auth import auth_router
from web.middleware import AuthMiddleware
from web.state_helpers import apply_project_state


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject standard security headers into every HTTP response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        # HSTS only for non-localhost
        host = (request.headers.get("host") or "").split(":")[0]
        if host not in LOCALHOST_HOSTS:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response

STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    app_state: dict,
    project_store=None,
    lifecycle_manager=None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Apply project-scoped state
        apply_project_state(app, app_state)
        # Global state (not project-scoped)
        app.state.project_store = project_store
        app.state.lifecycle_manager = lifecycle_manager
        yield

    app = FastAPI(title="Polyagentic", lifespan=lifespan)

    # Middleware is executed in reverse registration order (last added = first to run).
    # Order: SecurityHeaders → CORS → Auth → route handler
    app.add_middleware(AuthMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_state.get("cors_origins", DEFAULT_CORS_ORIGINS),
        allow_credentials=True,
        allow_methods=CORS_ALLOW_METHODS,
        allow_headers=CORS_ALLOW_HEADERS,
    )
    app.add_middleware(SecurityHeadersMiddleware)

    app.include_router(chat.router, prefix="/api")
    app.include_router(agents.router, prefix="/api")
    app.include_router(tasks.router, prefix="/api")
    app.include_router(activity.router, prefix="/api")
    app.include_router(git.router, prefix="/api")
    app.include_router(config.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(knowledge.router, prefix="/api")
    app.include_router(memory.router, prefix="/api")
    app.include_router(sessions.router, prefix="/api")
    app.include_router(conversations.router, prefix="/api")
    app.include_router(github.router, prefix="/api")
    app.include_router(uploads.router, prefix="/api")
    app.include_router(phases.router, prefix="/api")
    app.include_router(orgs.router, prefix="/api")
    app.include_router(mcp.router, prefix="/api")
    app.include_router(templates.router, prefix="/api")
    app.include_router(ws.router)
    app.include_router(auth_router)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    async def index():
        return FileResponse(str(STATIC_DIR / "index.html"))

    @app.get("/settings")
    async def settings():
        return FileResponse(str(STATIC_DIR / "settings.html"))

    return app
