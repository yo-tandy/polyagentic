"""Centralized constants for security defaults, validation patterns, and configuration."""

from __future__ import annotations

import re
import uuid

# ---------------------------------------------------------------------------
# Environment variable filtering — keys to strip from subprocess environments
# ---------------------------------------------------------------------------
SENSITIVE_ENV_VARS: frozenset[str] = frozenset({
    "CLAUDECODE",
    "JWT_SECRET",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_CLIENT_ID",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "DATABASE_URL",
    "SECRET_KEY",
})

# ---------------------------------------------------------------------------
# MCP package validation
# ---------------------------------------------------------------------------
MCP_PACKAGE_NAME_PATTERN: re.Pattern[str] = re.compile(
    r"^(@[a-zA-Z0-9._-]+/)?[a-zA-Z0-9._-]+$"
)

# ---------------------------------------------------------------------------
# File upload constraints
# ---------------------------------------------------------------------------
MAX_FILE_SIZE: int = 20 * 1024 * 1024  # 20 MB
ALLOWED_FILE_EXTENSIONS: frozenset[str] = frozenset({
    ".pdf", ".docx", ".txt", ".md", ".png", ".jpg", ".jpeg",
})

# ---------------------------------------------------------------------------
# JWT / auth
# ---------------------------------------------------------------------------
JWT_ALGORITHM: str = "HS256"
JWT_EXPIRY_SECONDS: int = 3600  # 1 hour
JWT_REFRESH_WINDOW_SECONDS: int = 900  # 15 minutes

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
DEFAULT_CORS_ORIGINS: list[str] = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]

# ---------------------------------------------------------------------------
# Security headers applied to every HTTP response
# ---------------------------------------------------------------------------
SECURITY_HEADERS: dict[str, str] = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}

# ---------------------------------------------------------------------------
# Localhost detection (shared by auth.py and SecurityHeadersMiddleware)
# ---------------------------------------------------------------------------
LOCALHOST_HOSTS: frozenset[str] = frozenset({
    "localhost", "127.0.0.1", "0.0.0.0", "",
})

# ---------------------------------------------------------------------------
# CORS allowed methods / headers
# ---------------------------------------------------------------------------
CORS_ALLOW_METHODS: list[str] = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
CORS_ALLOW_HEADERS: list[str] = ["Authorization", "Content-Type", "X-Requested-With"]

# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

def gen_id(prefix: str = "", hex_len: int = 12) -> str:
    """Generate a short unique ID with an optional prefix.

    Examples::

        gen_id("org_")     # "org_a1b2c3d4e5f6"
        gen_id("task-", 8) # "task-a1b2c3d4"
        gen_id()           # "a1b2c3d4e5f6"
    """
    return f"{prefix}{uuid.uuid4().hex[:hex_len]}"
