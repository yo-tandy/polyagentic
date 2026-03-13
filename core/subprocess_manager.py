from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from config import CLAUDE_CLI
from core.constants import SENSITIVE_ENV_VARS

logger = logging.getLogger(__name__)

# Patterns that indicate an OAuth / API-key authentication failure
_AUTH_ERROR_PATTERNS = [
    "401",
    "oauth token has expired",
    "token expired",
    "authentication failed",
    "failed to authenticate",
    "unauthorized",
    "invalid api key",
    "invalid x-api-key",
]


@dataclass
class SubprocessResult:
    result_text: str
    session_id: str | None
    cost_usd: float | None
    duration_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    is_error: bool


class SubprocessManager:

    _auth_refreshed: bool = False  # class-level flag to avoid repeated refreshes

    @staticmethod
    def _is_auth_error(text: str) -> bool:
        """Return True if *text* looks like an authentication / token error."""
        lower = text.lower()
        return any(pat in lower for pat in _AUTH_ERROR_PATTERNS)

    async def _refresh_auth(self) -> bool:
        """Attempt to refresh the Claude CLI OAuth token.

        Runs ``claude auth status`` first — if the CLI reports ``loggedIn: true``
        the token is likely still valid (or was auto-refreshed).  If not, tries
        ``claude auth login`` which triggers the interactive OAuth flow.

        Returns True if the refresh looks successful.
        """
        logger.info("Attempting Claude CLI auth refresh …")
        try:
            proc = await asyncio.create_subprocess_exec(
                CLAUDE_CLI, "auth", "status", "--output", "json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            status_text = stdout.decode(errors="replace").strip()
            logger.info("Auth status after refresh attempt: %s", status_text[:200])

            try:
                status = json.loads(status_text)
                if status.get("loggedIn"):
                    logger.info("Claude CLI reports loggedIn=true — token refreshed")
                    return True
            except json.JSONDecodeError:
                pass

            # Token still invalid — try an explicit login
            logger.warning("Claude CLI not logged in, attempting `auth login` …")
            proc2 = await asyncio.create_subprocess_exec(
                CLAUDE_CLI, "auth", "login",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc2.communicate(), timeout=30)
            return proc2.returncode == 0

        except Exception as e:
            logger.error("Auth refresh failed: %s", e)
            return False

    def _build_claude_args(
        self,
        prompt: str,
        system_prompt: str | None,
        model: str,
        allowed_tools: str | None,
        session_id: str | None,
        max_budget_usd: float | None,
        mcp_config_path: Path | None = None,
    ) -> list[str]:
        """Build the Claude CLI argument list (without execution)."""
        cmd = [CLAUDE_CLI, "-p", prompt, "--output-format", "json"]

        if session_id:
            cmd += ["--resume", session_id]

        cmd += ["--model", model]

        # --tools controls which built-in tools are available to the agent.
        # Pass allowed_tools value directly (including "" to disable all tools).
        if allowed_tools is not None:
            cmd += ["--tools", allowed_tools]

        if max_budget_usd is not None:
            cmd += ["--max-budget-usd", str(max_budget_usd)]

        cmd.append("--dangerously-skip-permissions")

        # MCP server configuration
        if mcp_config_path:
            cmd += ["--mcp-config", str(mcp_config_path)]

        # Pass system prompt inline (not as a file path) because Claude Code
        # may try to read file paths with its Read tool, which causes hangs
        # when --tools "" is set.  create_subprocess_exec passes args directly
        # without shell escaping so multiline content is safe.
        if system_prompt:
            if session_id:
                # Resumed session: append to refresh Claude's understanding of
                # the protocol (especially action blocks for readonly agents).
                cmd += ["--append-system-prompt", system_prompt]
                logger.info("System prompt: %d chars (appended to resumed session)", len(system_prompt))
            else:
                cmd += ["--system-prompt", system_prompt]
                logger.info("System prompt: %d chars (inline, fresh session)", len(system_prompt))
        else:
            logger.info("No system prompt: session_id=%s", session_id)

        return cmd

    async def _execute(
        self, cmd: list[str], working_dir: Path | None, timeout: int
    ) -> tuple[bytes, bytes, int]:
        """Execute a subprocess and return (stdout, stderr, returncode)."""
        # Filter sensitive env vars (API keys, secrets) so agent subprocesses
        # cannot access them.  Also removes CLAUDECODE to prevent nested
        # Claude Code sessions from detecting the parent session.
        env = {k: v for k, v in os.environ.items() if k not in SENSITIVE_ENV_VARS}

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(working_dir) if working_dir else None,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        return stdout, stderr, proc.returncode

    async def invoke(
        self,
        prompt: str,
        system_prompt: str | None = None,
        model: str = "sonnet",
        allowed_tools: str | None = None,
        session_id: str | None = None,
        working_dir: Path | None = None,
        timeout: int = 300,
        max_budget_usd: float | None = None,
        mcp_config_path: Path | None = None,
        _auth_retry: bool = False,
    ) -> SubprocessResult:
        cmd = self._build_claude_args(
            prompt, system_prompt, model, allowed_tools, session_id, max_budget_usd,
            mcp_config_path=mcp_config_path,
        )

        logger.info(
            "Invoking Claude CLI: session=%s, model=%s, tools=%s, cwd=%s, prompt_len=%d",
            session_id or "new", model, allowed_tools or "(none)",
            working_dir or "default", len(prompt),
        )

        try:
            stdout, stderr, returncode = await self._execute(cmd, working_dir, timeout)
        except asyncio.TimeoutError:
            logger.error("Claude CLI timed out after %ds", timeout)
            return SubprocessResult(
                result_text="[TIMEOUT] Claude Code subprocess timed out",
                session_id=session_id,
                cost_usd=None,
                duration_ms=None,
                input_tokens=None,
                output_tokens=None,
                is_error=True,
            )
        except Exception as e:
            logger.error("Failed to spawn Claude CLI: %s", e)
            return SubprocessResult(
                result_text=f"[ERROR] Failed to invoke Claude CLI: {e}",
                session_id=session_id,
                cost_usd=None,
                duration_ms=None,
                input_tokens=None,
                output_tokens=None,
                is_error=True,
            )

        stderr_text = stderr.decode(errors="replace").strip()
        raw_stdout = stdout.decode(errors="replace").strip()

        # --- Auth-error detection & automatic retry -----------------------
        # Check all available output for authentication errors. If found,
        # refresh the OAuth token and retry the invocation once.
        if not _auth_retry:
            combined_output = f"{raw_stdout}\n{stderr_text}"
            if self._is_auth_error(combined_output):
                logger.warning(
                    "Auth error detected (rc=%d), attempting token refresh and retry …",
                    returncode,
                )
                await self._refresh_auth()
                return await self.invoke(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    model=model,
                    allowed_tools=allowed_tools,
                    session_id=session_id,
                    working_dir=working_dir,
                    timeout=timeout,
                    max_budget_usd=max_budget_usd,
                    mcp_config_path=mcp_config_path,
                    _auth_retry=True,  # prevent infinite loop
                )
        # ------------------------------------------------------------------

        if returncode != 0:
            # Claude CLI may return rc=1 with error info in stdout JSON
            # (e.g. rate limits, context overflow). Try parsing stdout first.
            if raw_stdout:
                try:
                    data = json.loads(raw_stdout)
                    if "result" in data or "is_error" in data:
                        logger.warning("Claude CLI rc=%d but stdout has JSON — parsing it", returncode)
                        parsed = self._parse_output(raw_stdout, session_id)
                        # Even inside JSON output, check for auth errors
                        if not _auth_retry and self._is_auth_error(parsed.result_text):
                            logger.warning("Auth error in JSON result, refreshing and retrying …")
                            await self._refresh_auth()
                            return await self.invoke(
                                prompt=prompt,
                                system_prompt=system_prompt,
                                model=model,
                                allowed_tools=allowed_tools,
                                session_id=session_id,
                                working_dir=working_dir,
                                timeout=timeout,
                                max_budget_usd=max_budget_usd,
                                mcp_config_path=mcp_config_path,
                                _auth_retry=True,
                            )
                        return parsed
                except json.JSONDecodeError:
                    pass
            # Fallback: no usable JSON on stdout
            logger.error("Claude CLI error (rc=%d): %s", returncode, stderr_text)
            return SubprocessResult(
                result_text=f"[ERROR] {stderr_text}" if stderr_text else "[ERROR] Claude CLI exited with an error",
                session_id=session_id,
                cost_usd=None,
                duration_ms=None,
                input_tokens=None,
                output_tokens=None,
                is_error=True,
            )

        if stderr_text:
            logger.debug("Claude CLI stderr: %s", stderr_text[:200])

        logger.info("Claude CLI returned %d bytes (rc=0)", len(raw_stdout))
        return self._parse_output(raw_stdout, session_id)

    def _parse_output(self, raw: str, fallback_session_id: str | None) -> SubprocessResult:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(
                "Claude CLI output not valid JSON (%d chars), treating as plain text",
                len(raw),
            )
            return SubprocessResult(
                result_text=raw,
                session_id=fallback_session_id,
                cost_usd=None,
                duration_ms=None,
                input_tokens=None,
                output_tokens=None,
                is_error=False,
            )

        result_text = data.get("result", "")
        new_session_id = data.get("session_id") or fallback_session_id
        cost = data.get("total_cost_usd") or data.get("cost_usd")
        duration = data.get("duration_ms")
        is_error = data.get("is_error", False)

        # Token counts are nested under "usage" in the Claude CLI JSON output
        usage = data.get("usage") or {}
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")

        # When budget is exceeded, there may be no result field
        if not result_text and data.get("subtype") == "error_max_budget_usd":
            result_text = "[Budget exceeded] The agent's per-call budget was reached before completing the response."
            is_error = True

        logger.info(
            "Claude result: session=%s, cost=$%s, duration=%sms, "
            "tokens=%s/%s, error=%s, len=%d",
            new_session_id, cost, duration,
            input_tokens, output_tokens, is_error, len(result_text),
        )

        return SubprocessResult(
            result_text=result_text,
            session_id=new_session_id,
            cost_usd=cost,
            duration_ms=duration,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            is_error=is_error,
        )


class DockerSubprocessManager(SubprocessManager):
    """Executes Claude CLI inside a Docker container via `docker exec`."""

    def __init__(self, container_name: str):
        self.container_name = container_name

    async def _execute(
        self, cmd: list[str], working_dir: Path | None, timeout: int
    ) -> tuple[bytes, bytes, int]:
        """Override: wrap command with docker exec."""
        docker_cmd = ["docker", "exec", "-w", "/workspace", self.container_name] + cmd

        proc = await asyncio.create_subprocess_exec(
            *docker_cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        return stdout, stderr, proc.returncode
