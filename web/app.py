from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from web.routes import chat, agents, tasks, activity, git, config, ws, projects, knowledge, memory, sessions, conversations, github, uploads, phases

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
        app.state.project_store = project_store
        app.state.lifecycle_manager = lifecycle_manager
        yield

    app = FastAPI(title="Polyagentic", lifespan=lifespan)

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
    app.include_router(ws.router)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    async def index():
        return FileResponse(str(STATIC_DIR / "index.html"))

    return app
