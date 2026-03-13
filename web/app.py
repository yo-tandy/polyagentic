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
from web.routes import chat, agents, tasks, activity, git, config, ws, projects, knowledge, memory, sessions, conversations, github, uploads, phases, orgs, mcp
from web.auth import auth_router
from web.middleware import AuthMiddleware


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
        app.state.registry = app_state["registry"]
        app.state.broker = app_state["broker"]
        app.state.task_board = app_state["task_board"]
        app.state.git_manager = app_state["git_manager"]
        app.state.team_config = app_state["team_config"]
        app.state.session_store = app_state["session_store"]
        app.state.memory_manager = app_state.get("memory_manager")
        app.state.knowledge_base = app_state.get("knowledge_base")
        app.state.container_manager = app_state.get("container_manager")
        app.state.conversation_manager = app_state.get("conversation_manager")
        app.state.team_structure = app_state.get("team_structure")
        app.state.action_registry = app_state.get("action_registry")
        app.state.phase_board = app_state.get("phase_board")
        app.state.config_provider = app_state.get("config_provider")
        app.state.team_structure_repo = app_state.get("team_structure_repo")
        app.state.role_repo = app_state.get("role_repo")
        app.state.provider_history_repo = app_state.get("provider_history_repo")
        app.state.project_id = app_state.get("project_id")
        app.state.user_repo = app_state.get("user_repo")
        app.state.org_repo = app_state.get("org_repo")
        app.state.invite_repo = app_state.get("invite_repo")
        app.state.mcp_repo = app_state.get("mcp_repo")
        app.state.mcp_manager = app_state.get("mcp_manager")
        app.state.mcp_registry = app_state.get("mcp_registry")
        app.state.action_error_repo = app_state.get("action_error_repo")
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
