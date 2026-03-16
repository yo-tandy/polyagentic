"""ConfigProvider — cached configuration reader.

Loads all config entries from the DB once at startup, provides fast
``get()`` access, and supports ``refresh()`` for hot-reload via API.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from db.repositories.config_repo import ConfigRepository

logger = logging.getLogger(__name__)

# ── Default config seeds ──────────────────────────────────────────────
# Seeded into config_entries on first run (when table is empty).

DEFAULT_CONFIG_SEEDS: list[dict] = [
    # ── System scope ──────────────────────────────────────────────
    {"scope": "system", "key": "POLL_INTERVAL_SECONDS", "value": "1.0",
     "value_type": "float", "description": "Message broker polling interval"},
    {"scope": "system", "key": "WEB_HOST", "value": "127.0.0.1",
     "value_type": "string", "description": "Web server bind address"},
    {"scope": "system", "key": "WEB_PORT", "value": "8000",
     "value_type": "int", "description": "Web server port"},
    {"scope": "system", "key": "DEFAULT_MODEL", "value": "sonnet",
     "value_type": "string", "description": "Default Claude model"},
    {"scope": "system", "key": "DEMO_PAUSE_INTERVAL", "value": "5",
     "value_type": "int", "description": "Task count trigger for demo pause"},
    {"scope": "system", "key": "NUDGE_INTERVAL_SECONDS", "value": "20",
     "value_type": "int", "description": "Seconds between idle-agent nudge checks"},
    {"scope": "system", "key": "MAX_TASK_CONTEXT_ITEMS", "value": "20",
     "value_type": "int", "description": "Default max tasks shown to agents"},
    {"scope": "system", "key": "MAX_ACTIVITY_LOG", "value": "500",
     "value_type": "int", "description": "Activity log max entries"},
    {"scope": "system", "key": "MAX_CHAT_HISTORY", "value": "200",
     "value_type": "int", "description": "Chat history max entries"},
    {"scope": "system", "key": "MAX_MEMORY_CHARS", "value": "2000",
     "value_type": "int", "description": "Max chars per agent memory"},
    {"scope": "system", "key": "MAX_INDEX_SUMMARY_DOCS", "value": "30",
     "value_type": "int", "description": "Max docs in KB index summary"},
    {"scope": "system", "key": "IMAGE_NAME", "value": "polyagentic-agent:latest",
     "value_type": "string", "description": "Docker image for agent containers"},
    {"scope": "system", "key": "CONTAINER_PREFIX", "value": "polyagentic-",
     "value_type": "string", "description": "Docker container name prefix"},
    {"scope": "system", "key": "CLAUDE_CLI", "value": "claude",
     "value_type": "string", "description": "Path to Claude CLI binary"},
    {"scope": "system", "key": "CLAUDE_ALLOWED_TOOLS_DEV",
     "value": "Bash,Edit,Write,Read,Glob,Grep",
     "value_type": "string", "description": "Tool permissions for dev agents"},
    {"scope": "system", "key": "CLAUDE_ALLOWED_TOOLS_READONLY",
     "value": "Read,Glob,Grep",
     "value_type": "string", "description": "Tool permissions for read-only agents"},
    {"scope": "system", "key": "CLAUDE_ALLOWED_TOOLS_NONE", "value": "",
     "value_type": "string", "description": "No tool permissions"},
    {"scope": "system", "key": "CONSECUTIVE_ERROR_THRESHOLD", "value": "3",
     "value_type": "int", "description": "Auto-pause after N consecutive errors"},

    # ── API keys for alternative providers ─────────────────────
    {"scope": "system", "key": "ANTHROPIC_API_KEY", "value": "",
     "value_type": "string", "description": "Anthropic API key (for Claude API provider)"},
    {"scope": "system", "key": "OPENAI_API_KEY", "value": "",
     "value_type": "string", "description": "OpenAI API key"},
    {"scope": "system", "key": "GOOGLE_API_KEY", "value": "",
     "value_type": "string", "description": "Google API key (for Gemini provider)"},

    # ── Auth / SSO ───────────────────────────────────────────────
    {"scope": "system", "key": "GOOGLE_CLIENT_ID", "value": "",
     "value_type": "string", "description": "Google OAuth 2.0 Client ID"},
    {"scope": "system", "key": "GOOGLE_CLIENT_SECRET", "value": "",
     "value_type": "string", "description": "Google OAuth 2.0 Client Secret"},
    {"scope": "system", "key": "JWT_SECRET", "value": "",
     "value_type": "string", "description": "Secret key for JWT signing (auto-generated if empty)"},
    {"scope": "system", "key": "AUTH_ENABLED", "value": "false",
     "value_type": "bool", "description": "Enable authentication (requires Google OAuth config)"},

    # ── Agent scope ───────────────────────────────────────────────
    # Manny
    {"scope": "agent", "scope_id": "manny", "key": "timeout",
     "value": "120", "value_type": "int"},
    {"scope": "agent", "scope_id": "manny", "key": "use_session",
     "value": "false", "value_type": "bool"},
    {"scope": "agent", "scope_id": "manny", "key": "max_budget_usd",
     "value": "0.25", "value_type": "float"},
    {"scope": "agent", "scope_id": "manny", "key": "max_task_context_items",
     "value": "null", "value_type": "json",
     "description": "null = unlimited (full board visibility)"},
    # Dev Manager
    {"scope": "agent", "scope_id": "dev_manager", "key": "timeout",
     "value": "120", "value_type": "int"},
    {"scope": "agent", "scope_id": "dev_manager", "key": "use_session",
     "value": "false", "value_type": "bool"},
    {"scope": "agent", "scope_id": "dev_manager", "key": "max_budget_usd",
     "value": "0.25", "value_type": "float"},
    {"scope": "agent", "scope_id": "dev_manager", "key": "max_task_context_items",
     "value": "null", "value_type": "json"},
    # Jerry
    {"scope": "agent", "scope_id": "jerry", "key": "max_task_context_items",
     "value": "null", "value_type": "json"},
]


class ConfigProvider:
    """Cached configuration reader.

    Loads once at startup. Call ``refresh()`` to reload from DB
    (e.g. after API updates a config entry).
    """

    def __init__(self, config_repo: ConfigRepository, tenant_id: str = "default"):
        self._repo = config_repo
        self._tenant_id = tenant_id
        self._system_cache: dict[str, Any] = {}
        self._agent_cache: dict[str, dict[str, Any]] = {}

    async def load(self) -> None:
        """Load all config from DB into memory cache."""
        self._system_cache = await self._repo.get_system_config(self._tenant_id)
        logger.info(
            "Loaded %d system config entries", len(self._system_cache),
        )

    async def load_agent(self, agent_id: str) -> None:
        """Load config for a specific agent."""
        self._agent_cache[agent_id] = await self._repo.get_agent_config(
            agent_id, self._tenant_id,
        )

    async def refresh(self) -> None:
        """Reload all cached config from DB."""
        await self.load()
        # Reload all previously loaded agent configs
        for agent_id in list(self._agent_cache.keys()):
            await self.load_agent(agent_id)
        logger.info("Config cache refreshed")

    def get(self, key: str, default: Any = None) -> Any:
        """Get a system config value."""
        return self._system_cache.get(key, default)

    def get_agent(self, agent_id: str, key: str, default: Any = None) -> Any:
        """Get an agent-specific config value, falling back to system default."""
        agent_config = self._agent_cache.get(agent_id, {})
        if key in agent_config:
            return agent_config[key]
        # Fall back to system scope
        return self._system_cache.get(key, default)

    async def seed_defaults(self) -> int:
        """Seed default config entries if table is empty."""
        return await self._repo.seed_defaults(DEFAULT_CONFIG_SEEDS)
