"""MCP Registry — built-in curated catalog + official MCP registry search.

Two-tier discovery:
  1. Built-in catalog of ~15 popular, well-tested MCP servers
  2. Live search against registry.modelcontextprotocol.io (official registry)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

REGISTRY_BASE = "https://registry.modelcontextprotocol.io/v0"
CACHE_TTL_SECONDS = 3600  # 1 hour


@dataclass
class MCPServerInfo:
    """A discovered MCP server entry."""
    server_id: str
    name: str
    description: str
    package: str
    install_method: str = "npx"  # npx | uvx | docker
    command: str = "npx"
    args: list[str] = field(default_factory=list)
    env_required: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source: str = "builtin"  # builtin | registry

    def to_dict(self) -> dict[str, Any]:
        return {
            "server_id": self.server_id,
            "name": self.name,
            "description": self.description,
            "package": self.package,
            "install_method": self.install_method,
            "command": self.command,
            "args": self.args,
            "env_required": self.env_required,
            "tags": self.tags,
            "source": self.source,
        }


# ── Built-in curated catalog ──────────────────────────────────────────

BUILTIN_CATALOG: list[MCPServerInfo] = [
    MCPServerInfo(
        server_id="postgres",
        name="PostgreSQL",
        description="Read-only access to PostgreSQL databases with schema inspection and query capabilities.",
        package="@modelcontextprotocol/server-postgres",
        command="npx", args=["-y", "@modelcontextprotocol/server-postgres"],
        env_required=["DATABASE_URL"],
        tags=["database", "sql", "postgres", "postgresql"],
    ),
    MCPServerInfo(
        server_id="sqlite",
        name="SQLite",
        description="Interact with SQLite databases. Run SQL queries, analyze data, and manage business intelligence.",
        package="@modelcontextprotocol/server-sqlite",
        command="npx", args=["-y", "@modelcontextprotocol/server-sqlite"],
        env_required=["SQLITE_DB_PATH"],
        tags=["database", "sql", "sqlite"],
    ),
    MCPServerInfo(
        server_id="github",
        name="GitHub",
        description="GitHub API integration for repository management, file operations, issues, pull requests, and more.",
        package="@modelcontextprotocol/server-github",
        command="npx", args=["-y", "@modelcontextprotocol/server-github"],
        env_required=["GITHUB_PERSONAL_ACCESS_TOKEN"],
        tags=["github", "git", "vcs", "code"],
    ),
    MCPServerInfo(
        server_id="filesystem",
        name="Filesystem",
        description="Secure file operations with configurable access controls.",
        package="@modelcontextprotocol/server-filesystem",
        command="npx", args=["-y", "@modelcontextprotocol/server-filesystem"],
        env_required=[],
        tags=["filesystem", "files", "io"],
    ),
    MCPServerInfo(
        server_id="puppeteer",
        name="Puppeteer",
        description="Browser automation and web scraping using Puppeteer.",
        package="@modelcontextprotocol/server-puppeteer",
        command="npx", args=["-y", "@modelcontextprotocol/server-puppeteer"],
        env_required=[],
        tags=["browser", "web", "scraping", "automation", "puppeteer"],
    ),
    MCPServerInfo(
        server_id="slack",
        name="Slack",
        description="Slack workspace integration for channels, messaging, and user management.",
        package="@modelcontextprotocol/server-slack",
        command="npx", args=["-y", "@modelcontextprotocol/server-slack"],
        env_required=["SLACK_BOT_TOKEN", "SLACK_TEAM_ID"],
        tags=["slack", "messaging", "chat", "team"],
    ),
    MCPServerInfo(
        server_id="memory",
        name="Memory",
        description="Knowledge graph-based persistent memory for maintaining context across conversations.",
        package="@modelcontextprotocol/server-memory",
        command="npx", args=["-y", "@modelcontextprotocol/server-memory"],
        env_required=[],
        tags=["memory", "knowledge-graph", "persistence"],
    ),
    MCPServerInfo(
        server_id="brave-search",
        name="Brave Search",
        description="Web and local search using the Brave Search API.",
        package="@modelcontextprotocol/server-brave-search",
        command="npx", args=["-y", "@modelcontextprotocol/server-brave-search"],
        env_required=["BRAVE_API_KEY"],
        tags=["search", "web", "brave"],
    ),
    MCPServerInfo(
        server_id="fetch",
        name="Fetch",
        description="Web content fetching and conversion to Markdown for easy consumption.",
        package="@modelcontextprotocol/server-fetch",
        command="npx", args=["-y", "@modelcontextprotocol/server-fetch"],
        env_required=[],
        tags=["web", "fetch", "http", "markdown"],
    ),
    MCPServerInfo(
        server_id="sequential-thinking",
        name="Sequential Thinking",
        description="Dynamic problem-solving through thought sequences with branching and revision.",
        package="@modelcontextprotocol/server-sequential-thinking",
        command="npx", args=["-y", "@modelcontextprotocol/server-sequential-thinking"],
        env_required=[],
        tags=["thinking", "reasoning", "problem-solving"],
    ),
    MCPServerInfo(
        server_id="docker",
        name="Docker",
        description="Docker container management, images, and compose operations.",
        package="@modelcontextprotocol/server-docker",
        install_method="uvx",
        command="uvx", args=["mcp-server-docker"],
        env_required=[],
        tags=["docker", "containers", "devops"],
    ),
    MCPServerInfo(
        server_id="google-maps",
        name="Google Maps",
        description="Google Maps Platform integration for geocoding, directions, and place search.",
        package="@modelcontextprotocol/server-google-maps",
        command="npx", args=["-y", "@modelcontextprotocol/server-google-maps"],
        env_required=["GOOGLE_MAPS_API_KEY"],
        tags=["maps", "google", "geocoding", "directions"],
    ),
    MCPServerInfo(
        server_id="sentry",
        name="Sentry",
        description="Sentry.io integration for error tracking and issue management.",
        package="@modelcontextprotocol/server-sentry",
        command="npx", args=["-y", "@modelcontextprotocol/server-sentry"],
        env_required=["SENTRY_AUTH_TOKEN"],
        tags=["sentry", "errors", "monitoring", "debugging"],
    ),
    MCPServerInfo(
        server_id="redis",
        name="Redis",
        description="Redis database integration with support for keys, strings, hashes, lists, and sets.",
        package="@gongrzhe/server-redis-mcp",
        command="npx", args=["-y", "@gongrzhe/server-redis-mcp"],
        env_required=["REDIS_URL"],
        tags=["database", "redis", "cache", "nosql"],
    ),
    MCPServerInfo(
        server_id="mongodb",
        name="MongoDB",
        description="MongoDB integration for collection management, document CRUD, and aggregation.",
        package="@gongrzhe/server-mongo-mcp",
        command="npx", args=["-y", "@gongrzhe/server-mongo-mcp"],
        env_required=["MONGODB_URI"],
        tags=["database", "mongodb", "nosql", "document"],
    ),
]

# Index by server_id for fast lookup
_BUILTIN_INDEX: dict[str, MCPServerInfo] = {s.server_id: s for s in BUILTIN_CATALOG}


class MCPRegistry:
    """Two-tier MCP server discovery: built-in catalog + official registry."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, list[MCPServerInfo]]] = {}

    def list_all(self) -> list[MCPServerInfo]:
        """Return the full built-in catalog."""
        return list(BUILTIN_CATALOG)

    def get(self, server_id: str) -> MCPServerInfo | None:
        """Look up a server by ID in the built-in catalog."""
        return _BUILTIN_INDEX.get(server_id)

    async def search(self, query: str) -> list[MCPServerInfo]:
        """Search both built-in catalog and the official MCP registry.

        Built-in results appear first, then remote results (deduplicated).
        """
        query_lower = query.lower()

        # 1. Search built-in catalog
        builtin_results = self._search_builtin(query_lower)

        # 2. Search official registry
        remote_results = await self.search_remote(query)

        # 3. Merge and deduplicate (built-in takes priority)
        seen_packages = {r.package for r in builtin_results}
        for r in remote_results:
            if r.package not in seen_packages:
                builtin_results.append(r)
                seen_packages.add(r.package)

        return builtin_results

    def _search_builtin(self, query_lower: str) -> list[MCPServerInfo]:
        """Search the built-in catalog by keyword matching."""
        results = []
        for server in BUILTIN_CATALOG:
            searchable = " ".join([
                server.server_id,
                server.name,
                server.description,
                " ".join(server.tags),
            ]).lower()
            if query_lower in searchable:
                results.append(server)
        return results

    async def search_remote(self, query: str) -> list[MCPServerInfo]:
        """Search the official MCP registry at registry.modelcontextprotocol.io."""
        # Check cache
        cache_key = f"search:{query.lower().strip()}"
        if cache_key in self._cache:
            ts, results = self._cache[cache_key]
            if time.time() - ts < CACHE_TTL_SECONDS:
                return results

        results: list[MCPServerInfo] = []
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{REGISTRY_BASE}/servers",
                    params={"search": query, "limit": 20},
                )
                resp.raise_for_status()
                data = resp.json()

            for entry in data.get("servers", []):
                info = self._parse_registry_entry(entry)
                if info:
                    results.append(info)

            self._cache[cache_key] = (time.time(), results)
            logger.info("MCP registry search for %r returned %d results", query, len(results))
        except Exception:
            logger.warning("MCP registry search failed for %r, using built-in only", query, exc_info=True)

        return results

    def _parse_registry_entry(self, entry: dict) -> MCPServerInfo | None:
        """Parse a server entry from the official registry into MCPServerInfo."""
        name = entry.get("name", "")
        if not name:
            return None

        # Determine package and install method from packages list
        packages = entry.get("packages", [])
        package_name = ""
        install_method = "npx"
        command = "npx"
        args: list[str] = []
        env_keys: list[str] = []

        for pkg in packages:
            registry_name = pkg.get("registry_name", "")
            if registry_name == "npm":
                package_name = pkg.get("name", "")
                install_method = "npx"
                command = "npx"
                args = ["-y", package_name] if package_name else []
                break
            elif registry_name == "pypi":
                package_name = pkg.get("name", "")
                install_method = "uvx"
                command = "uvx"
                args = [package_name] if package_name else []
                break

        if not package_name:
            # Fallback: try to use the name directly
            package_name = name

        # Try to extract env requirements from package arguments
        for pkg in packages:
            for arg in pkg.get("arguments", []):
                if arg.get("is_required") and arg.get("format") == "environment_variable":
                    env_keys.append(arg.get("name", ""))

        # Generate a clean server_id
        server_id = name.split("/")[-1] if "/" in name else name
        server_id = server_id.replace("server-", "").replace("mcp-", "")

        return MCPServerInfo(
            server_id=server_id,
            name=entry.get("display_name", name),
            description=entry.get("description", "")[:500],
            package=package_name,
            install_method=install_method,
            command=command,
            args=args,
            env_required=env_keys,
            tags=[],
            source="registry",
        )
