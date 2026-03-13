from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from web.auth import decode_jwt, _get_jwt_secret
from web.middleware import ANONYMOUS_USER

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # Check if auth is enabled
    config = getattr(websocket.app.state, "config_provider", None)
    auth_enabled = False
    if config:
        auth_enabled = config.get("AUTH_ENABLED", False)
        if isinstance(auth_enabled, str):
            auth_enabled = auth_enabled.lower() in ("true", "1", "yes")

    if auth_enabled:
        # Authenticate via cookie (sent on WebSocket upgrade).
        # Query-param tokens are intentionally NOT supported — they leak
        # in logs, browser history, and proxy referrer headers.
        token = websocket.cookies.get("session_token")
        if not token:
            await websocket.close(code=4001, reason="Not authenticated")
            return

        jwt_secret = _get_jwt_secret(websocket)
        payload = decode_jwt(token, jwt_secret)
        if not payload:
            await websocket.close(code=4001, reason="Session expired")
            return

        websocket.state.user = {
            "id": payload["sub"],
            "org_id": payload["org_id"],
            "email": payload["email"],
            "name": payload["name"],
        }
    else:
        websocket.state.user = ANONYMOUS_USER

    await websocket.accept()
    broker = websocket.app.state.broker
    broker.register_ws(websocket)
    try:
        while True:
            # Keep connection alive; client can send pings
            await websocket.receive_text()
    except WebSocketDisconnect:
        broker.unregister_ws(websocket)
