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


# ---------------------------------------------------------------------------
# Structured-action tool definitions
# ---------------------------------------------------------------------------
# These mirror the text-based ```action {...}``` protocol so that API
# providers (OpenAI, Gemini, Claude API) can expose them as native
# function-calling tools.  The ActionHandler still does the actual
# execution — the tool executor only wraps these calls into the text
# format that ActionHandler expects.
# ---------------------------------------------------------------------------

ACTION_TOOL_DEFINITIONS: dict[str, dict] = {
    "respond_to_user": {
        "name": "respond_to_user",
        "description": (
            "Send a message to the user. Always include suggested_answers "
            "with 2-3 short reply options when asking a question."
        ),
        "parameters": {
            "message": {
                "type": "string",
                "description": "The message content to send to the user",
            },
            "suggested_answers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-3 short quick-reply options for the user (REQUIRED when asking questions)",
            },
        },
        "required": ["message", "suggested_answers"],
    },
    "delegate": {
        "name": "delegate",
        "description": (
            "Delegate work to another team member. Creates a task on the "
            "board and sends the assignment message."
        ),
        "parameters": {
            "to": {
                "type": "string",
                "description": "Agent ID to delegate to",
            },
            "task_description": {
                "type": "string",
                "description": "Detailed description with acceptance criteria",
            },
            "task_title": {
                "type": "string",
                "description": "Short title for the task",
            },
            "priority": {
                "type": "integer",
                "description": "Priority: 1=critical, 2=high, 3=medium, 4=low, 5=backlog",
            },
            "labels": {
                "type": "string",
                "description": "Comma-separated labels (e.g. 'phase-1,backend')",
            },
            "category": {
                "type": "string",
                "description": "Task category: 'operational' or 'project'",
            },
            "phase_id": {
                "type": "string",
                "description": "Phase ID for project tasks",
            },
            "estimate": {
                "type": "integer",
                "description": "Story point estimate (Fibonacci: 1, 2, 3, 5, 8, 13)",
            },
        },
        "required": ["to", "task_description"],
    },
    "assign_ticket": {
        "name": "assign_ticket",
        "description": (
            "Create a task on the board and assign it to a team member. "
            "Sends the assignment message to the agent."
        ),
        "parameters": {
            "to": {
                "type": "string",
                "description": "Agent ID to assign the task to",
            },
            "task_title": {
                "type": "string",
                "description": "Short descriptive title",
            },
            "task_description": {
                "type": "string",
                "description": "Detailed description with acceptance criteria",
            },
            "priority": {
                "type": "integer",
                "description": "Priority: 1=critical, 2=high, 3=medium, 4=low, 5=backlog",
            },
            "labels": {
                "type": "string",
                "description": "Comma-separated labels (e.g. 'phase-1,backend')",
            },
            "category": {
                "type": "string",
                "description": "Task category: 'operational' or 'project'",
            },
            "phase_id": {
                "type": "string",
                "description": "Phase ID for project tasks",
            },
            "estimate": {
                "type": "integer",
                "description": "Story point estimate (Fibonacci: 1, 2, 3, 5, 8, 13)",
            },
        },
        "required": ["to", "task_title", "task_description"],
    },
    "update_task": {
        "name": "update_task",
        "description": "Update the status or details of a task on the board.",
        "parameters": {
            "task_id": {
                "type": "string",
                "description": "The task ID to update",
            },
            "status": {
                "type": "string",
                "description": "New status: pending, in_progress, review, done, paused, blocked, cancelled",
            },
            "completion_summary": {
                "type": "string",
                "description": "Summary of what was accomplished (for done status)",
            },
            "progress_note": {
                "type": "string",
                "description": "Progress update note",
            },
            "outcome": {
                "type": "string",
                "description": "Review outcome: approved, rejected, complete",
            },
            "estimate": {
                "type": "integer",
                "description": "Story point estimate (Fibonacci: 1, 2, 3, 5, 8, 13)",
            },
        },
        "required": ["task_id"],
    },
    "update_memory": {
        "name": "update_memory",
        "description": (
            "Save notes to your persistent memory. Re-summarize rather "
            "than appending to keep it concise."
        ),
        "parameters": {
            "memory_type": {
                "type": "string",
                "description": "Type of memory: 'project' or 'personality'",
            },
            "content": {
                "type": "string",
                "description": "Updated memory content",
            },
        },
        "required": ["memory_type", "content"],
    },
    "write_document": {
        "name": "write_document",
        "description": "Write a new document to the knowledge base.",
        "parameters": {
            "title": {
                "type": "string",
                "description": "Document title",
            },
            "content": {
                "type": "string",
                "description": "Document content in markdown",
            },
            "category": {
                "type": "string",
                "description": "Category: specs, design, architecture, planning, history",
            },
        },
        "required": ["title", "content"],
    },
    "read_document": {
        "name": "read_document",
        "description": "Read the full content of a document from the project knowledge base.",
        "parameters": {
            "doc_id": {
                "type": "string",
                "description": "Document ID from the KB index (e.g. 'doc-abc123')",
            },
        },
        "required": ["doc_id"],
    },
    "update_document": {
        "name": "update_document",
        "description": "Update an existing document in the knowledge base.",
        "parameters": {
            "doc_id": {
                "type": "string",
                "description": "Document ID to update",
            },
            "content": {
                "type": "string",
                "description": "Full updated content in markdown",
            },
        },
        "required": ["doc_id", "content"],
    },
    "resolve_comments": {
        "name": "resolve_comments",
        "description": "Mark document comments as resolved.",
        "parameters": {
            "doc_id": {
                "type": "string",
                "description": "Document ID",
            },
            "resolutions": {
                "type": "string",
                "description": "JSON array of {comment_id, resolution} objects",
            },
        },
        "required": ["doc_id", "resolutions"],
    },
    "start_conversation": {
        "name": "start_conversation",
        "description": "Start an interactive conversation with the user.",
        "parameters": {
            "title": {
                "type": "string",
                "description": "Conversation topic",
            },
            "goals": {
                "type": "string",
                "description": "JSON array of what you want to learn or decide",
            },
        },
        "required": ["title", "goals"],
    },
    "end_conversation": {
        "name": "end_conversation",
        "description": "End an active conversation with the user.",
        "parameters": {
            "summary": {
                "type": "string",
                "description": "Summary of discussion and decisions made",
            },
        },
        "required": ["summary"],
    },
    "create_phase": {
        "name": "create_phase",
        "description": "Create a new project phase.",
        "parameters": {
            "name": {
                "type": "string",
                "description": "Phase name",
            },
            "description": {
                "type": "string",
                "description": "What this phase covers",
            },
            "ordering": {
                "type": "integer",
                "description": "Phase sequence number (1, 2, 3...)",
            },
        },
        "required": ["name", "description"],
    },
    "update_phase": {
        "name": "update_phase",
        "description": "Update a phase status or properties.",
        "parameters": {
            "phase_id": {
                "type": "string",
                "description": "The phase ID to update",
            },
            "status": {
                "type": "string",
                "description": "New status: planning, awaiting_approval, in_progress, review, completed",
            },
            "planning_doc_id": {
                "type": "string",
                "description": "KB doc ID for the phase planning document",
            },
            "review_doc_id": {
                "type": "string",
                "description": "KB doc ID for the phase review document",
            },
        },
        "required": ["phase_id"],
    },
    "create_batch_tickets": {
        "name": "create_batch_tickets",
        "description": "Create multiple draft tickets for a phase at once.",
        "parameters": {
            "phase_id": {
                "type": "string",
                "description": "Phase to add tickets to",
            },
            "tickets": {
                "type": "string",
                "description": (
                    "JSON array of ticket objects with: title, description, "
                    "priority, labels, role, estimate"
                ),
            },
        },
        "required": ["phase_id", "tickets"],
    },
    "start_task": {
        "name": "start_task",
        "description": "Start or resume a paused task.",
        "parameters": {
            "agent_id": {
                "type": "string",
                "description": "Agent to assign",
            },
            "task_id": {
                "type": "string",
                "description": "Task to start/resume",
            },
        },
        "required": ["agent_id", "task_id"],
    },
    "pause_task": {
        "name": "pause_task",
        "description": "Pause an agent's in-progress task.",
        "parameters": {
            "agent_id": {
                "type": "string",
                "description": "Agent to pause",
            },
            "task_id": {
                "type": "string",
                "description": "Task to pause",
            },
        },
        "required": ["agent_id", "task_id"],
    },
    "request_capability": {
        "name": "request_capability",
        "description": (
            "Flag a missing capability so that Robot Resources can find "
            "and deploy an MCP server for you."
        ),
        "parameters": {
            "capability": {
                "type": "string",
                "description": "What capability is needed",
            },
            "context": {
                "type": "string",
                "description": "Why you need this capability",
            },
        },
        "required": ["capability"],
    },
}


def build_action_schemas_openai(allowed_actions: set[str] | None) -> list[dict]:
    """Build OpenAI function-calling tool definitions for structured actions.

    Only includes actions that are in the agent's ``allowed_actions`` set.
    """
    if not allowed_actions:
        return []

    tools = []
    for action_name in allowed_actions:
        defn = ACTION_TOOL_DEFINITIONS.get(action_name)
        if not defn:
            continue

        properties = {}
        for pname, pinfo in defn["parameters"].items():
            prop: dict = {
                "type": pinfo["type"],
                "description": pinfo["description"],
            }
            # Array types need an items schema
            if pinfo["type"] == "array" and "items" in pinfo:
                prop["items"] = pinfo["items"]
            # Enum support
            if "enum" in pinfo:
                prop["enum"] = pinfo["enum"]
            properties[pname] = prop

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


def build_action_schemas_anthropic(allowed_actions: set[str] | None) -> list[dict]:
    """Build Anthropic API tool definitions for structured actions."""
    if not allowed_actions:
        return []

    tools = []
    for action_name in allowed_actions:
        defn = ACTION_TOOL_DEFINITIONS.get(action_name)
        if not defn:
            continue

        properties = {}
        for pname, pinfo in defn["parameters"].items():
            prop: dict = {
                "type": pinfo["type"],
                "description": pinfo["description"],
            }
            if pinfo["type"] == "array" and "items" in pinfo:
                prop["items"] = pinfo["items"]
            if "enum" in pinfo:
                prop["enum"] = pinfo["enum"]
            properties[pname] = prop

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


def build_action_schemas_gemini(allowed_actions: set[str] | None) -> list[dict]:
    """Build Gemini tool definitions for structured actions."""
    if not allowed_actions:
        return []

    declarations = []
    for action_name in allowed_actions:
        defn = ACTION_TOOL_DEFINITIONS.get(action_name)
        if not defn:
            continue

        properties = {}
        for pname, pinfo in defn["parameters"].items():
            gtype = pinfo["type"].upper()
            prop: dict = {
                "type": gtype,
                "description": pinfo["description"],
            }
            if pinfo["type"] == "array" and "items" in pinfo:
                prop["items"] = {
                    "type": pinfo["items"]["type"].upper(),
                }
            if "enum" in pinfo:
                prop["enum"] = pinfo["enum"]
            properties[pname] = prop

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


def is_action_tool(tool_name: str) -> bool:
    """Check whether a tool name is a structured action (vs a file tool)."""
    return tool_name in ACTION_TOOL_DEFINITIONS
