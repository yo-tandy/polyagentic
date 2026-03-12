"""MCP server management routes — dashboard visibility into MCP deployments."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/mcp/servers")
async def list_mcp_servers(request: Request, status: str | None = None):
    """List all MCP server records, optionally filtered by status."""
    mcp_repo = getattr(request.app.state, "mcp_repo", None)
    if not mcp_repo:
        return JSONResponse({"error": "MCP not configured"}, status_code=503)

    project_id = getattr(request.app.state, "project_id", None)
    servers = await mcp_repo.get_all(project_id=project_id, status=status)
    return {
        "servers": [
            {
                "id": s.id,
                "server_id": s.server_id,
                "name": s.name,
                "description": s.description[:200] if s.description else "",
                "package_name": s.package_name,
                "install_method": s.install_method,
                "status": s.status,
                "requested_by": s.requested_by,
                "agent_ids": s.agent_ids or [],
                "container_installed": s.container_installed,
                "error_message": s.error_message,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in servers
        ]
    }


@router.get("/mcp/servers/{server_id}")
async def get_mcp_server(server_id: int, request: Request):
    """Get a single MCP server record by DB id."""
    mcp_repo = getattr(request.app.state, "mcp_repo", None)
    if not mcp_repo:
        return JSONResponse({"error": "MCP not configured"}, status_code=503)

    server = await mcp_repo.get(server_id)
    if not server:
        return JSONResponse({"error": "Not found"}, status_code=404)

    return {
        "id": server.id,
        "server_id": server.server_id,
        "name": server.name,
        "description": server.description,
        "package_name": server.package_name,
        "command": server.command,
        "args": server.args,
        "env_template": server.env_template,
        "install_method": server.install_method,
        "status": server.status,
        "requested_by": server.requested_by,
        "managed_by": server.managed_by,
        "requested_reason": server.requested_reason,
        "error_message": server.error_message,
        "agent_ids": server.agent_ids or [],
        "container_installed": server.container_installed,
        "created_at": server.created_at.isoformat() if server.created_at else None,
        "updated_at": server.updated_at.isoformat() if server.updated_at else None,
    }


@router.delete("/mcp/servers/{server_id}")
async def remove_mcp_server(server_id: int, request: Request):
    """Remove an MCP server record."""
    mcp_manager = getattr(request.app.state, "mcp_manager", None)
    if not mcp_manager:
        return JSONResponse({"error": "MCP not configured"}, status_code=503)

    removed = await mcp_manager.remove_server(server_id)
    if not removed:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return {"status": "ok"}


@router.get("/mcp/registry")
async def search_registry(request: Request, q: str = ""):
    """Search the MCP registry (built-in + official registry)."""
    mcp_registry = getattr(request.app.state, "mcp_registry", None)
    if not mcp_registry:
        return JSONResponse({"error": "MCP registry not configured"}, status_code=503)

    if q:
        results = await mcp_registry.search(q)
    else:
        results = mcp_registry.list_all()

    return {
        "results": [r.to_dict() for r in results]
    }


@router.get("/mcp/agents/{agent_id}/servers")
async def list_agent_mcp_servers(agent_id: str, request: Request):
    """List MCP servers installed for a specific agent."""
    mcp_manager = getattr(request.app.state, "mcp_manager", None)
    if not mcp_manager:
        return JSONResponse({"error": "MCP not configured"}, status_code=503)

    servers = await mcp_manager.get_agent_servers(agent_id)
    return {"servers": servers}
