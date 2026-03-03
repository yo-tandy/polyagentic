from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from config import CLAUDE_CLI

logger = logging.getLogger(__name__)


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

    def _build_claude_args(
        self,
        prompt: str,
        system_prompt: str | None,
        model: str,
        allowed_tools: str | None,
        session_id: str | None,
        max_budget_usd: float | None,
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

        # Pass system prompt inline (not as a file path) because Claude Code
        # may try to read file paths with its Read tool, which causes hangs
        # when --tools "" is set.  create_subprocess_exec passes args directly
        # without shell escaping so multiline content is safe.
        if system_prompt and not session_id:
            cmd += ["--system-prompt", system_prompt]
            logger.info("System prompt: %d chars (inline)", len(system_prompt))
        else:
            logger.info("No system prompt: system_prompt=%s, session_id=%s",
                        bool(system_prompt), session_id)

        return cmd

    async def _execute(
        self, cmd: list[str], working_dir: Path | None, timeout: int
    ) -> tuple[bytes, bytes, int]:
        """Execute a subprocess and return (stdout, stderr, returncode)."""
        # Remove CLAUDECODE env var so nested Claude Code sessions don't
        # detect they're inside another session and refuse to start
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

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
    ) -> SubprocessResult:
        cmd = self._build_claude_args(
            prompt, system_prompt, model, allowed_tools, session_id, max_budget_usd,
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
        if returncode != 0:
            # Claude CLI may return rc=1 with error info in stdout JSON
            # (e.g. rate limits, context overflow). Try parsing stdout first.
            raw = stdout.decode(errors="replace").strip()
            if raw:
                try:
                    data = json.loads(raw)
                    if "result" in data or "is_error" in data:
                        logger.warning("Claude CLI rc=%d but stdout has JSON — parsing it", returncode)
                        return self._parse_output(raw, session_id)
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

        raw = stdout.decode(errors="replace").strip()
        logger.info("Claude CLI returned %d bytes (rc=0)", len(raw))
        return self._parse_output(raw, session_id)

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
        input_tokens = data.get("input_tokens")
        output_tokens = data.get("output_tokens")
        is_error = data.get("is_error", False)

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
