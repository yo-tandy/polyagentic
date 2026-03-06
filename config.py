"""Minimal path-only config — loaded before DB is available.

All runtime configuration lives in the ``config_entries`` DB table,
managed by :class:`db.config_provider.ConfigProvider`.
"""

from pathlib import Path

BASE_DIR = Path(__file__).parent
LOGS_DIR = BASE_DIR / "logs"
TEAM_CONFIG_FILE = BASE_DIR / "team_config.yaml"
PROJECTS_DIR = BASE_DIR / "projects"
MEMORY_DIR = BASE_DIR / "memory"

# ── Defaults used as fallbacks when DB config is not yet loaded ──
# These are NOT authoritative — the DB config_entries table is.
# They exist only to prevent import errors during the bootstrap window.
WEB_HOST = "127.0.0.1"
WEB_PORT = 8000
DEFAULT_MODEL = "sonnet"
CLAUDE_CLI = "claude"
CLAUDE_ALLOWED_TOOLS_DEV = "Bash,Edit,Write,Read,Glob,Grep"
CLAUDE_ALLOWED_TOOLS_READONLY = "Read,Glob,Grep"
CLAUDE_ALLOWED_TOOLS_NONE = ""
