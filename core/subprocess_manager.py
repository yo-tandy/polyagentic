from __future__ import annotations

import asyncio
import json
import logging
import os
import time
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

    def __init__(self):
        self._current_proc: asyncio.subprocess.Process | None = None

    # Class-level auth coordination — shared across all SubprocessManager instances.
    # Since the app is single-process async, asyncio.Lock is sufficient.
    _auth_lock: asyncio.Lock = asyncio.Lock()
    _auth_refreshed_at: float | None = None   # monotonic timestamp of last success
    _auth_failed_at: float | None = None      # monotonic timestamp of last failure
    _AUTH_SUCCESS_TTL: float = 1800            # 30 min: don't re-check if recently OK
    _AUTH_FAILURE_COOLDOWN: float = 300        # 5 min: don't retry if recently failed

    # Approximate cost per 1M tokens (input, output) for CLI cost estimation.
    # These are theoretical API rates — actual cost depends on subscription plan.
    _PRICING = {
        "opus": (5.00, 25.00),
        "sonnet": (3.00, 15.00),
        "haiku": (1.00, 5.00),
    }
    _DEFAULT_PRICING = (3.00, 15.00)  # Sonnet as fallback

    @staticmethod
    def _estimate_cost(
        model: str, input_tokens: int, output_tokens: int,
    ) -> float:
        """Estimate cost from tokens and published pricing."""
        prices = SubprocessManager._PRICING.get(model, SubprocessManager._DEFAULT_PRICING)
        input_cost = (input_tokens / 1_000_000) * prices[0]
        output_cost = (output_tokens / 1_000_000) * prices[1]
        return round(input_cost + output_cost, 6)

    @staticmethod
    def _is_auth_error(text: str) -> bool:
        """Return True if *text* looks like an authentication / token error."""
        lower = text.lower()
        return any(pat in lower for pat in _AUTH_ERROR_PATTERNS)

    async def _refresh_auth(self) -> bool:
        """Check Claude CLI auth status (never opens browser automatically).

        Uses a class-level lock to ensure only one check runs at a time.
        Concurrent callers wait for the lock and then check whether the
        holder already verified successfully, avoiding duplicate work.

        Returns True only if ``claude auth status`` reports loggedIn=true
        (i.e. the token auto-refreshed or is still valid).  Returns False
        if the token is expired — the caller should return [AUTH_ERROR] so
        the agent transitions to PENDING_REAUTH and the UI prompts the user.
        """
        now = time.monotonic()

        # Fast path (outside lock): if recently verified OK, skip entirely
        if (cls_at := SubprocessManager._auth_refreshed_at) is not None \
                and now - cls_at < self._AUTH_SUCCESS_TTL:
            logger.info("Auth check skipped — last success %.0fs ago", now - cls_at)
            return True

        async with SubprocessManager._auth_lock:
            # Re-check after acquiring lock — another coroutine may have
            # just completed a successful check while we were waiting.
            now = time.monotonic()
            if (cls_at := SubprocessManager._auth_refreshed_at) is not None \
                    and now - cls_at < self._AUTH_SUCCESS_TTL:
                logger.info("Auth already verified by another agent (%.0fs ago)", now - cls_at)
                return True

            # Cooldown after failure — don't keep retrying
            if (fail_at := SubprocessManager._auth_failed_at) is not None \
                    and now - fail_at < self._AUTH_FAILURE_COOLDOWN:
                remaining = self._AUTH_FAILURE_COOLDOWN - (now - fail_at)
                logger.warning(
                    "Auth check in cooldown (%.0fs remaining) — returning auth error",
                    remaining,
                )
                return False

            # === Actual auth check (only one coroutine reaches here) ===
            logger.info("Checking Claude CLI auth status …")
            try:
                proc = await asyncio.create_subprocess_exec(
                    CLAUDE_CLI, "auth", "status", "--output", "json",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
                status_text = stdout.decode(errors="replace").strip()
                logger.info("Auth status: %s", status_text[:200])

                try:
                    status = json.loads(status_text)
                    if status.get("loggedIn"):
                        logger.info("Claude CLI reports loggedIn=true — token valid")
                        SubprocessManager._auth_refreshed_at = time.monotonic()
                        return True
                except json.JSONDecodeError:
                    pass

                # Token expired / not logged in — do NOT open browser automatically.
                # Return False so the agent enters PENDING_REAUTH state.
                logger.warning(
                    "Claude CLI not logged in — auth required. "
                    "Agents will enter PENDING_REAUTH state."
                )
                SubprocessManager._auth_failed_at = time.monotonic()
                return False

            except asyncio.TimeoutError:
                logger.error("Auth status check timed out")
                SubprocessManager._auth_failed_at = time.monotonic()
                return False
            except Exception as e:
                logger.error("Auth check failed: %s", e)
                SubprocessManager._auth_failed_at = time.monotonic()
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

    def cancel(self):
        """Terminate the running subprocess, if any.

        Safe to call from any context (sync).  Handles the race where the
        process has already exited naturally.
        """
        proc = self._current_proc
        if proc is None:
            return
        try:
            proc.terminate()
            logger.info("Subprocess terminated (pid=%s)", proc.pid)
        except ProcessLookupError:
            logger.debug("Subprocess already exited (pid=%s)", proc.pid)

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
        self._current_proc = proc
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            return stdout, stderr, proc.returncode
        finally:
            self._current_proc = None

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

        # --- Auth-error detection & check ---------------------------------
        # Check output for authentication errors.  If found, verify whether
        # the token auto-refreshed (``_refresh_auth`` only checks status,
        # never opens a browser).  If still invalid → return [AUTH_ERROR]
        # so the agent enters PENDING_REAUTH and the UI prompts the user.
        if not _auth_retry:
            combined_output = f"{raw_stdout}\n{stderr_text}"
            if self._is_auth_error(combined_output):
                logger.warning(
                    "Auth error detected (rc=%d), checking auth status …",
                    returncode,
                )
                refreshed = await self._refresh_auth()
                if refreshed:
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
                # Auth truly expired — return error for PENDING_REAUTH
                logger.error("Auth expired — agent should enter PENDING_REAUTH")
                return SubprocessResult(
                    result_text=(
                        "[AUTH_ERROR] Claude CLI authentication has expired. "
                        "Please re-authenticate via the dashboard."
                    ),
                    session_id=session_id,
                    cost_usd=None,
                    duration_ms=None,
                    input_tokens=None,
                    output_tokens=None,
                    is_error=True,
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
                            logger.warning("Auth error in JSON result, checking auth status …")
                            refreshed = await self._refresh_auth()
                            if refreshed:
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
                            logger.error("Auth expired (JSON path) — agent should enter PENDING_REAUTH")
                            return SubprocessResult(
                                result_text=(
                                    "[AUTH_ERROR] Claude CLI authentication has expired. "
                                    "Please re-authenticate via the dashboard."
                                ),
                                session_id=session_id,
                                cost_usd=None,
                                duration_ms=None,
                                input_tokens=None,
                                output_tokens=None,
                                is_error=True,
                            )
                        # Override CLI-reported cost with token-based estimate
                        if parsed.input_tokens or parsed.output_tokens:
                            parsed.cost_usd = self._estimate_cost(
                                model, parsed.input_tokens or 0, parsed.output_tokens or 0,
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
        result = self._parse_output(raw_stdout, session_id)
        # Override CLI-reported cost with token-based estimate
        if result.input_tokens or result.output_tokens:
            result.cost_usd = self._estimate_cost(
                model, result.input_tokens or 0, result.output_tokens or 0,
            )
        return result

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
        super().__init__()
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
        self._current_proc = proc
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            return stdout, stderr, proc.returncode
        finally:
            self._current_proc = None
