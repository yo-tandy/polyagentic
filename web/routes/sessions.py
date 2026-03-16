from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core.session_store import SessionState

router = APIRouter()


@router.get("/sessions")
async def get_sessions(request: Request):
    """List all sessions with stats for the current project.

    Iterates all registered agents so every agent appears — even those
    without session entries (e.g. stateless agents before first invocation).
    """
    session_store = request.app.state.session_store
    registry = request.app.state.registry
    all_sessions = session_store.get_all_info()

    result = []
    for agent in registry.get_all():
        info = all_sessions.get(agent.agent_id, {})
        req_count = info.get("request_count", 0)
        total_ms = info.get("total_duration_ms", 0)
        result.append({
            "agent_id": agent.agent_id,
            "agent_name": agent.name,
            "session_id": info.get("session_id", ""),
            "state": info.get("state", "active"),
            "use_session": agent.use_session,
            "model": agent.model,
            "request_count": req_count,
            "error_count": info.get("error_count", 0),
            "consecutive_errors": info.get("consecutive_errors", 0),
            "total_duration_ms": total_ms,
            "avg_duration_ms": total_ms // req_count if req_count > 0 else 0,
            "created_at": info.get("created_at"),
            "last_used_at": info.get("last_used_at"),
            "paused_at": info.get("paused_at"),
            "killed_at": info.get("killed_at"),
            "last_error": info.get("last_error") or agent.last_error,
            "provider": getattr(agent, "_provider_name", "claude-cli"),
            "fallback_provider": getattr(agent, "_fallback_provider_name", None),
        })

    return {"sessions": result}


@router.post("/sessions/{agent_id}/pause")
async def pause_session(agent_id: str, request: Request):
    """Pause a session. Agent will hold messages until resumed."""
    session_store = request.app.state.session_store
    registry = request.app.state.registry
    broker = request.app.state.broker

    if not registry.get(agent_id):
        return JSONResponse(
            {"error": f"No agent found with id '{agent_id}'"}, status_code=404
        )

    await session_store.set_state(agent_id, SessionState.PAUSED)

    await broker.broadcast_event({
        "event_type": "session_status",
        "data": {"agent_id": agent_id, "session_state": "paused"},
    })

    return {"status": "paused", "agent_id": agent_id}


@router.post("/sessions/{agent_id}/resume")
async def resume_session(agent_id: str, request: Request):
    """Resume a paused session."""
    session_store = request.app.state.session_store
    registry = request.app.state.registry
    broker = request.app.state.broker

    if not registry.get(agent_id):
        return JSONResponse(
            {"error": f"No agent found with id '{agent_id}'"}, status_code=404
        )

    info = session_store.get_info(agent_id)
    if not info:
        return JSONResponse(
            {"error": f"No session data for agent '{agent_id}'"}, status_code=404
        )

    await session_store.set_state(agent_id, SessionState.ACTIVE)
    # Reset consecutive errors on manual resume — handled by set_state

    await broker.broadcast_event({
        "event_type": "session_status",
        "data": {"agent_id": agent_id, "session_state": "active"},
    })

    return {"status": "resumed", "agent_id": agent_id}


@router.post("/sessions/{agent_id}/kill")
async def kill_session(agent_id: str, request: Request):
    """Kill a session. Next invocation will create a fresh session."""
    session_store = request.app.state.session_store
    registry = request.app.state.registry
    broker = request.app.state.broker

    if not registry.get(agent_id):
        return JSONResponse(
            {"error": f"No agent found with id '{agent_id}'"}, status_code=404
        )

    if not session_store.get_info(agent_id):
        return JSONResponse(
            {"error": f"No session data for agent '{agent_id}'"}, status_code=404
        )

    await session_store.set_state(agent_id, SessionState.KILLED)

    await broker.broadcast_event({
        "event_type": "session_status",
        "data": {"agent_id": agent_id, "session_state": "killed"},
    })

    return {"status": "killed", "agent_id": agent_id}


@router.post("/sessions/{agent_id}/reset")
async def reset_session(agent_id: str, request: Request):
    """Reset session — clear stats and assign a fresh session on next invocation."""
    session_store = request.app.state.session_store
    registry = request.app.state.registry
    broker = request.app.state.broker

    if not registry.get(agent_id):
        return JSONResponse(
            {"error": f"No agent found with id '{agent_id}'"}, status_code=404
        )

    await session_store.clear_session(agent_id)

    await broker.broadcast_event({
        "event_type": "session_status",
        "data": {"agent_id": agent_id, "session_state": "active"},
    })

    return {"status": "reset", "agent_id": agent_id}


@router.post("/sessions/pause-all")
async def pause_all_sessions(request: Request):
    """Pause all session-based agents."""
    session_store = request.app.state.session_store
    registry = request.app.state.registry
    broker = request.app.state.broker

    paused = []
    for agent in registry.get_all():
        if not agent.use_session:
            continue
        await session_store.set_state(agent.agent_id, SessionState.PAUSED)
        paused.append(agent.agent_id)
        # Broadcast per-agent event so the agent panel updates immediately
        await broker.broadcast_event({
            "event_type": "session_status",
            "data": {"agent_id": agent.agent_id, "session_state": "paused"},
        })

    return {"status": "paused_all", "agents": paused}


@router.post("/sessions/resume-all")
async def resume_all_sessions(request: Request):
    """Resume all paused session-based agents."""
    session_store = request.app.state.session_store
    registry = request.app.state.registry
    broker = request.app.state.broker

    resumed = []
    for agent in registry.get_all():
        if not agent.use_session:
            continue
        info = session_store.get_info(agent.agent_id)
        if info and info.get("state") == SessionState.PAUSED.value:
            await session_store.set_state(agent.agent_id, SessionState.ACTIVE)
            resumed.append(agent.agent_id)
            # Broadcast per-agent event so the agent panel updates immediately
            await broker.broadcast_event({
                "event_type": "session_status",
                "data": {"agent_id": agent.agent_id, "session_state": "active"},
            })

    return {"status": "resumed_all", "agents": resumed}


@router.post("/sessions/reauth")
async def reauth(request: Request):
    """Trigger Claude CLI OAuth login and resume PENDING_REAUTH agents on success."""
    import asyncio
    import time

    from config import CLAUDE_CLI
    from core.agent import Agent, AgentStatus
    from core.subprocess_manager import SubprocessManager

    registry = request.app.state.registry
    broker = request.app.state.broker

    try:
        proc = await asyncio.create_subprocess_exec(
            CLAUDE_CLI, "auth", "login",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    except asyncio.TimeoutError:
        return JSONResponse(
            {"status": "failed", "error": "OAuth login timed out (120s)"},
            status_code=504,
        )
    except Exception as e:
        return JSONResponse(
            {"status": "failed", "error": str(e)},
            status_code=500,
        )

    if proc.returncode != 0:
        err = stderr.decode(errors="replace").strip()
        return JSONResponse(
            {"status": "failed", "error": err or "OAuth login failed"},
            status_code=500,
        )

    # Success — update class-level auth state
    SubprocessManager._auth_refreshed_at = time.monotonic()
    SubprocessManager._auth_failed_at = None

    # Reset the dedup flag so future auth errors can broadcast again
    Agent._auth_event_broadcast = False

    # Resume all PENDING_REAUTH agents → IDLE
    resumed = []
    for agent in registry.get_all():
        if agent.status == AgentStatus.PENDING_REAUTH:
            agent.status = AgentStatus.IDLE
            resumed.append(agent.agent_id)
            await broker.broadcast_event({
                "event_type": "agent_status",
                "data": {"agent_id": agent.agent_id, "status": "idle"},
            })

    # Broadcast auth_restored event so the frontend can hide the modal
    await broker.broadcast_event({
        "event_type": "auth_restored",
        "data": {"resumed_agents": resumed},
    })

    return {"status": "ok", "resumed_agents": resumed}


@router.post("/sessions/reauth/cancel")
async def reauth_cancel(request: Request):
    """Cancel reauth — agents stay in PENDING_REAUTH state."""
    return {"status": "cancelled"}


ALLOWED_MODELS = {"sonnet", "opus", "haiku"}


@router.post("/sessions/{agent_id}/model")
async def set_model(agent_id: str, request: Request):
    """Change the model used by an agent. Takes effect on next invocation."""
    registry = request.app.state.registry
    broker = request.app.state.broker

    agent = registry.get(agent_id)
    if not agent:
        return JSONResponse(
            {"error": f"No agent found with id '{agent_id}'"}, status_code=404
        )

    body = await request.json()
    model = body.get("model", "").strip().lower()
    if model not in ALLOWED_MODELS:
        return JSONResponse(
            {"error": f"Invalid model '{model}'. Allowed: {', '.join(sorted(ALLOWED_MODELS))}"},
            status_code=400,
        )

    old_model = agent.model
    agent.model = model

    # Persist model override to session store (survives restart)
    session_store = request.app.state.session_store
    await session_store.set_model(agent_id, model)

    await broker.broadcast_event({
        "event_type": "session_status",
        "data": {"agent_id": agent_id, "model": model},
    })

    return {"status": "ok", "agent_id": agent_id, "old_model": old_model, "model": model}


ALLOWED_PROVIDERS = {"claude-cli", "claude-api", "openai", "gemini"}


@router.post("/sessions/{agent_id}/provider")
async def set_provider(agent_id: str, request: Request):
    """Change the AI provider used by an agent. Takes effect on next invocation.

    Body: {"provider": "openai", "fallback_provider": "claude-cli"}
    """
    from core.providers.factory import create_provider, FallbackProvider, VALID_PROVIDERS
    import os

    registry = request.app.state.registry
    broker = request.app.state.broker

    agent = registry.get(agent_id)
    if not agent:
        return JSONResponse(
            {"error": f"No agent found with id '{agent_id}'"}, status_code=404
        )

    body = await request.json()
    provider_name = body.get("provider", "").strip().lower()
    fallback_name = body.get("fallback_provider", "").strip().lower() or None

    if provider_name not in ALLOWED_PROVIDERS:
        return JSONResponse(
            {"error": f"Invalid provider '{provider_name}'. Allowed: {', '.join(sorted(ALLOWED_PROVIDERS))}"},
            status_code=400,
        )

    if fallback_name and fallback_name not in ALLOWED_PROVIDERS:
        return JSONResponse(
            {"error": f"Invalid fallback provider '{fallback_name}'. Allowed: {', '.join(sorted(ALLOWED_PROVIDERS))}"},
            status_code=400,
        )

    config_provider = request.app.state.config_provider
    project_id = request.app.state.project_id

    # Resolve API keys
    key_map = {
        "claude-api": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "gemini": "GOOGLE_API_KEY",
    }

    def _get_key(prov):
        env_key = key_map.get(prov)
        if not env_key:
            return None
        db_val = config_provider.get(env_key, "")
        return db_val or os.environ.get(env_key, "")

    try:
        # Get or create provider_history_repo
        provider_history_repo = getattr(request.app.state, "provider_history_repo", None)

        primary = create_provider(
            provider_name,
            api_key=_get_key(provider_name),
            history_repo=provider_history_repo,
            project_id=project_id,
            agent_id=agent_id,
            subprocess_mgr=getattr(agent, "_subprocess", None) if provider_name == "claude-cli" else None,
        )

        if fallback_name:
            fallback = create_provider(
                fallback_name,
                api_key=_get_key(fallback_name),
                history_repo=provider_history_repo,
                project_id=project_id,
                agent_id=agent_id,
                subprocess_mgr=getattr(agent, "_subprocess", None) if fallback_name == "claude-cli" else None,
            )
            agent.set_provider(FallbackProvider(primary, fallback))
        else:
            agent.set_provider(primary)

        # Update agent metadata
        agent._provider_name = provider_name
        agent._fallback_provider_name = fallback_name

    except Exception as e:
        return JSONResponse(
            {"error": f"Failed to create provider: {e}"},
            status_code=500,
        )

    await broker.broadcast_event({
        "event_type": "session_status",
        "data": {
            "agent_id": agent_id,
            "provider": provider_name,
            "fallback_provider": fallback_name,
        },
    })

    return {
        "status": "ok",
        "agent_id": agent_id,
        "provider": provider_name,
        "fallback_provider": fallback_name,
    }
