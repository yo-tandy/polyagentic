import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
LOGS_DIR = BASE_DIR / "logs"
TEAM_CONFIG_FILE = BASE_DIR / "team_config.yaml"

CLAUDE_CLI = os.environ.get("CLAUDE_CLI", "claude")
DEFAULT_MODEL = os.environ.get("POLYAGENTIC_MODEL", "sonnet")
POLL_INTERVAL_SECONDS = 1.0
WEB_HOST = "127.0.0.1"
WEB_PORT = 8000

CLAUDE_ALLOWED_TOOLS_DEV = "Bash,Edit,Write,Read,Glob,Grep"
CLAUDE_ALLOWED_TOOLS_READONLY = "Read,Glob,Grep"
CLAUDE_ALLOWED_TOOLS_NONE = ""  # For coordinator agents that only produce text

DEMO_PAUSE_INTERVAL = 5  # Trigger PM demo pause after every N completed tasks

PROJECTS_DIR = BASE_DIR / "projects"
MEMORY_DIR = BASE_DIR / "memory"
