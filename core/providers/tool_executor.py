"""Tool executor — runs Bash/Read/Write/Edit/Glob/Grep for API providers.

Claude CLI executes tools internally.  For OpenAI / Gemini / Claude API
providers we need to execute tools ourselves and feed results back.
"""

from __future__ import annotations

import asyncio
import glob as glob_mod
import json
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Maximum output length returned to the model (chars)
MAX_TOOL_OUTPUT = 30_000
BASH_TIMEOUT = 120  # seconds


class ToolExecutor:
    """Executes tools on behalf of API-based providers."""

    def __init__(self, working_dir: Path | None = None):
        self.working_dir = working_dir or Path.cwd()

    async def execute(self, tool_name: str, arguments: dict) -> str:
        """Dispatch a tool call and return the string result."""
        handler = {
            "Bash": self._bash,
            "bash": self._bash,
            "Read": self._read,
            "read": self._read,
            "Write": self._write,
            "write": self._write,
            "Edit": self._edit,
            "edit": self._edit,
            "Glob": self._glob,
            "glob": self._glob,
            "Grep": self._grep,
            "grep": self._grep,
        }.get(tool_name)

        if handler is None:
            return f"[ERROR] Unknown tool: {tool_name}"

        try:
            result = await handler(arguments)
            if len(result) > MAX_TOOL_OUTPUT:
                result = result[:MAX_TOOL_OUTPUT] + "\n... [output truncated]"
            return result
        except Exception as e:
            logger.error("Tool %s failed: %s", tool_name, e)
            return f"[ERROR] {tool_name} failed: {e}"

    # ── Tool implementations ─────────────────────────────────────

    async def _bash(self, args: dict) -> str:
        """Run a shell command."""
        command = args.get("command", "")
        if not command:
            return "[ERROR] No command provided"

        timeout = min(args.get("timeout", BASH_TIMEOUT * 1000) / 1000, BASH_TIMEOUT)
        cwd = str(self.working_dir)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env={k: v for k, v in os.environ.items() if k != "CLAUDECODE"},
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            return f"[TIMEOUT] Command timed out after {timeout}s"
        except Exception as e:
            return f"[ERROR] Failed to execute: {e}"

        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()

        parts = []
        if out:
            parts.append(out)
        if err:
            parts.append(f"STDERR:\n{err}")
        if proc.returncode != 0:
            parts.append(f"Exit code: {proc.returncode}")

        return "\n".join(parts) if parts else "(no output)"

    async def _read(self, args: dict) -> str:
        """Read a file with optional offset/limit."""
        file_path = args.get("file_path", "")
        if not file_path:
            return "[ERROR] No file_path provided"

        path = Path(file_path)
        if not path.is_absolute():
            path = self.working_dir / path

        if not path.exists():
            return f"[ERROR] File not found: {path}"
        if not path.is_file():
            return f"[ERROR] Not a file: {path}"

        try:
            text = path.read_text(errors="replace")
        except Exception as e:
            return f"[ERROR] Cannot read {path}: {e}"

        lines = text.splitlines()
        offset = args.get("offset", 0)
        limit = args.get("limit", 0)

        if offset > 0:
            lines = lines[offset:]
        if limit > 0:
            lines = lines[:limit]

        # Format with line numbers (1-indexed)
        start = (offset or 0) + 1
        numbered = [f"{start + i:>6}\t{line}" for i, line in enumerate(lines)]
        return "\n".join(numbered)

    async def _write(self, args: dict) -> str:
        """Write content to a file."""
        file_path = args.get("file_path", "")
        content = args.get("content", "")
        if not file_path:
            return "[ERROR] No file_path provided"

        path = Path(file_path)
        if not path.is_absolute():
            path = self.working_dir / path

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            return f"Successfully wrote {len(content)} chars to {path}"
        except Exception as e:
            return f"[ERROR] Cannot write {path}: {e}"

    async def _edit(self, args: dict) -> str:
        """Search-and-replace in a file."""
        file_path = args.get("file_path", "")
        old_string = args.get("old_string", "")
        new_string = args.get("new_string", "")
        replace_all = args.get("replace_all", False)

        if not file_path:
            return "[ERROR] No file_path provided"
        if not old_string:
            return "[ERROR] No old_string provided"

        path = Path(file_path)
        if not path.is_absolute():
            path = self.working_dir / path

        if not path.exists():
            return f"[ERROR] File not found: {path}"

        try:
            text = path.read_text(errors="replace")
        except Exception as e:
            return f"[ERROR] Cannot read {path}: {e}"

        count = text.count(old_string)
        if count == 0:
            return f"[ERROR] old_string not found in {path}"
        if count > 1 and not replace_all:
            return f"[ERROR] old_string found {count} times in {path}. Use replace_all=true or provide more context."

        if replace_all:
            new_text = text.replace(old_string, new_string)
        else:
            new_text = text.replace(old_string, new_string, 1)

        try:
            path.write_text(new_text)
            replaced = count if replace_all else 1
            return f"Successfully replaced {replaced} occurrence(s) in {path}"
        except Exception as e:
            return f"[ERROR] Cannot write {path}: {e}"

    async def _glob(self, args: dict) -> str:
        """Find files matching a glob pattern."""
        pattern = args.get("pattern", "")
        if not pattern:
            return "[ERROR] No pattern provided"

        search_path = args.get("path", str(self.working_dir))

        full_pattern = os.path.join(search_path, pattern)
        matches = sorted(glob_mod.glob(full_pattern, recursive=True))

        if not matches:
            return f"No files matching '{pattern}' in {search_path}"

        return "\n".join(matches)

    async def _grep(self, args: dict) -> str:
        """Search file contents with ripgrep (fallback to grep)."""
        pattern = args.get("pattern", "")
        if not pattern:
            return "[ERROR] No pattern provided"

        search_path = args.get("path", str(self.working_dir))
        file_glob = args.get("glob", "")
        output_mode = args.get("output_mode", "files_with_matches")
        case_insensitive = args.get("-i", False)
        context = args.get("context") or args.get("-C", 0)

        # Try ripgrep first, fall back to grep
        cmd = self._build_rg_cmd(
            pattern, search_path, file_glob, output_mode,
            case_insensitive, context,
        )

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.working_dir),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=30
            )
            out = stdout.decode(errors="replace").strip()
            if not out and proc.returncode != 0:
                return f"No matches for pattern '{pattern}' in {search_path}"
            return out
        except asyncio.TimeoutError:
            return f"[TIMEOUT] Grep timed out for pattern '{pattern}'"
        except Exception as e:
            return f"[ERROR] Grep failed: {e}"

    @staticmethod
    def _build_rg_cmd(
        pattern: str, path: str, file_glob: str,
        output_mode: str, case_insensitive: bool, context: int,
    ) -> str:
        """Build a ripgrep command string."""
        import shlex
        parts = ["rg", "--no-heading"]

        if output_mode == "files_with_matches":
            parts.append("-l")
        elif output_mode == "count":
            parts.append("-c")
        else:
            parts.append("-n")  # line numbers for content mode

        if case_insensitive:
            parts.append("-i")
        if context and output_mode == "content":
            parts.extend(["-C", str(context)])
        if file_glob:
            parts.extend(["--glob", file_glob])

        parts.append(shlex.quote(pattern))
        parts.append(shlex.quote(path))

        return " ".join(parts)


# ── Tool schema builders ─────────────────────────────────────────

# Tool definitions in a provider-neutral format.
# Each provider converts these to its own schema.

TOOL_DEFINITIONS = {
    "Bash": {
        "name": "Bash",
        "description": "Execute a bash command. Returns stdout, stderr, and exit code.",
        "parameters": {
            "command": {"type": "string", "description": "The bash command to execute"},
            "timeout": {"type": "integer", "description": "Timeout in milliseconds (default: 120000)"},
        },
        "required": ["command"],
    },
    "Read": {
        "name": "Read",
        "description": "Read a file's contents with line numbers.",
        "parameters": {
            "file_path": {"type": "string", "description": "Absolute path to the file"},
            "offset": {"type": "integer", "description": "Line number to start reading from (0-indexed)"},
            "limit": {"type": "integer", "description": "Number of lines to read (0 = all)"},
        },
        "required": ["file_path"],
    },
    "Write": {
        "name": "Write",
        "description": "Write content to a file (creates parent directories if needed).",
        "parameters": {
            "file_path": {"type": "string", "description": "Absolute path to the file"},
            "content": {"type": "string", "description": "The content to write"},
        },
        "required": ["file_path", "content"],
    },
    "Edit": {
        "name": "Edit",
        "description": "Search-and-replace in a file. old_string must be unique unless replace_all is true.",
        "parameters": {
            "file_path": {"type": "string", "description": "Absolute path to the file"},
            "old_string": {"type": "string", "description": "The text to replace"},
            "new_string": {"type": "string", "description": "The replacement text"},
            "replace_all": {"type": "boolean", "description": "Replace all occurrences (default: false)"},
        },
        "required": ["file_path", "old_string", "new_string"],
    },
    "Glob": {
        "name": "Glob",
        "description": "Find files matching a glob pattern (e.g. '**/*.py').",
        "parameters": {
            "pattern": {"type": "string", "description": "Glob pattern to match"},
            "path": {"type": "string", "description": "Directory to search in (default: working directory)"},
        },
        "required": ["pattern"],
    },
    "Grep": {
        "name": "Grep",
        "description": "Search file contents using regex (powered by ripgrep).",
        "parameters": {
            "pattern": {"type": "string", "description": "Regex pattern to search for"},
            "path": {"type": "string", "description": "File or directory to search"},
            "glob": {"type": "string", "description": "Glob pattern to filter files (e.g. '*.py')"},
            "output_mode": {"type": "string", "description": "Output mode: 'content', 'files_with_matches', or 'count'"},
        },
        "required": ["pattern"],
    },
}


def build_tool_schemas_openai(allowed_tools: str | None) -> list[dict]:
    """Build OpenAI function-calling tool definitions.

    ``allowed_tools`` is a comma-separated string like ``"Bash,Read,Write"``.
    Returns an empty list if tools are disabled (empty string or None).
    """
    if not allowed_tools:
        return []

    tool_names = [t.strip() for t in allowed_tools.split(",") if t.strip()]
    tools = []

    for name in tool_names:
        defn = TOOL_DEFINITIONS.get(name)
        if not defn:
            continue

        properties = {}
        for pname, pinfo in defn["parameters"].items():
            properties[pname] = {
                "type": pinfo["type"],
                "description": pinfo["description"],
            }

        tools.append({
            "type": "function",
            "function": {
                "name": defn["name"],
                "description": defn["description"],
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": defn.get("required", []),
                },
            },
        })

    return tools


def build_tool_schemas_anthropic(allowed_tools: str | None) -> list[dict]:
    """Build Anthropic API tool definitions.

    Anthropic uses a slightly different schema format than OpenAI.
    """
    if not allowed_tools:
        return []

    tool_names = [t.strip() for t in allowed_tools.split(",") if t.strip()]
    tools = []

    for name in tool_names:
        defn = TOOL_DEFINITIONS.get(name)
        if not defn:
            continue

        properties = {}
        for pname, pinfo in defn["parameters"].items():
            properties[pname] = {
                "type": pinfo["type"],
                "description": pinfo["description"],
            }

        tools.append({
            "name": defn["name"],
            "description": defn["description"],
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": defn.get("required", []),
            },
        })

    return tools


def build_tool_schemas_gemini(allowed_tools: str | None) -> list[dict]:
    """Build Gemini tool definitions.

    Gemini uses function declarations inside a tools array.
    """
    if not allowed_tools:
        return []

    tool_names = [t.strip() for t in allowed_tools.split(",") if t.strip()]
    declarations = []

    for name in tool_names:
        defn = TOOL_DEFINITIONS.get(name)
        if not defn:
            continue

        properties = {}
        for pname, pinfo in defn["parameters"].items():
            # Gemini uses uppercase type names
            gtype = pinfo["type"].upper()
            properties[pname] = {
                "type": gtype,
                "description": pinfo["description"],
            }

        declarations.append({
            "name": defn["name"],
            "description": defn["description"],
            "parameters": {
                "type": "OBJECT",
                "properties": properties,
                "required": defn.get("required", []),
            },
        })

    return declarations
