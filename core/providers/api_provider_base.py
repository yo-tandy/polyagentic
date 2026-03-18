"""Base class for API-based providers (Claude API, OpenAI, Gemini).

Extracts the common tool-loop, session-management, cost-estimation and
result-building logic so that each concrete provider only implements the
thin provider-specific adapter methods.
"""

from __future__ import annotations

import logging
import os
import time
from abc import abstractmethod
from pathlib import Path
from typing import Any

from core.providers.base import BaseProvider
from core.providers.tool_executor import ToolExecutor, is_action_tool
from core.subprocess_manager import SubprocessResult

logger = logging.getLogger(__name__)

MAX_TOOL_LOOP_ITERATIONS = 25


class APIProviderBase(BaseProvider):
    """Shared implementation for all direct-API providers.

    Subclasses set class attributes and implement the abstract adapter
    methods listed below.

    Class attributes each subclass must define:
        PROVIDER_NAME  -- human label used in log lines ("Claude", ...)
        MODEL_MAP      -- alias -> full model-id dict
        PRICING        -- model-id -> (input_per_1M, output_per_1M)
        DEFAULT_PRICING -- fallback (input_per_1M, output_per_1M) tuple
        ENV_KEY        -- environment variable for the API key
    """

    # -- Subclass must set these ----------------------------------------
    PROVIDER_NAME: str
    MODEL_MAP: dict[str, str]
    PRICING: dict[str, tuple[float, float]]
    DEFAULT_PRICING: tuple[float, float]
    ENV_KEY: str

    # -------------------------------------------------------------------

    def __init__(
        self,
        api_key: str | None = None,
        history_repo: Any = None,
        project_id: str = "",
        agent_id: str = "",
    ):
        self._api_key = api_key or os.environ.get(self.ENV_KEY, "")
        self._history_repo = history_repo
        self._project_id = project_id
        self._agent_id = agent_id
        self._client: Any = None  # Lazy init

    # -- Public interface -----------------------------------------------

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
        allowed_actions: set[str] | None = None,
    ) -> SubprocessResult:
        start_ms = time.monotonic_ns() // 1_000_000
        resolved_model = self.MODEL_MAP.get(model, model)
        tool_executor = ToolExecutor(working_dir) if working_dir else ToolExecutor()

        # Build tool schemas in the provider's native format
        tools = self._build_tool_schemas(allowed_tools, allowed_actions)

        # Track action tool calls — converted to text-based ```action```
        # blocks in the result so ActionHandler can parse them.
        self._action_blocks: list[str] = []

        # -- Session management (common) --------------------------------
        messages: list[dict] = []

        # Only reuse session IDs that belong to this provider (psess_* prefix).
        # Ignore stale Claude CLI UUIDs that may linger after a provider switch.
        if session_id and not session_id.startswith("psess_"):
            logger.debug(
                "Ignoring non-provider session ID %s for agent %s",
                session_id, self._agent_id,
            )
            session_id = None

        if session_id and self._history_repo:
            messages = await self._history_repo.get_history(session_id)
            messages = self._convert_history(messages)
        if not session_id and self._history_repo:
            session_id = await self._history_repo.create_session(
                self._project_id, self._agent_id,
            )

        # Provider-specific system-prompt injection
        self._inject_system_prompt(messages, system_prompt)

        # Append user message
        messages.append({"role": "user", "content": prompt})

        # Persist user message
        if self._history_repo and session_id:
            await self._history_repo.append(
                session_id=session_id,
                project_id=self._project_id,
                agent_id=self._agent_id,
                role="user",
                content=prompt,
            )

        total_input_tokens = 0
        total_output_tokens = 0

        try:
            client = self._get_client()

            for iteration in range(MAX_TOOL_LOOP_ITERATIONS):
                logger.info(
                    "%s API call: model=%s, iteration=%d, messages=%d, tools=%d",
                    self.PROVIDER_NAME, resolved_model, iteration,
                    len(messages), len(tools),
                )

                response = await self._call_api(
                    client, resolved_model, messages, system_prompt, tools,
                )

                inp, out = self._extract_tokens(response)
                total_input_tokens += inp
                total_output_tokens += out

                tool_calls = self._extract_tool_calls(response)

                if not tool_calls:
                    # No (more) tool calls -- extract final text
                    result_text = self._extract_text(response)

                    # Append any accumulated action blocks so ActionHandler
                    # can parse them from the result text.
                    if self._action_blocks:
                        action_text = "\n".join(self._action_blocks)
                        result_text = f"{result_text}\n\n{action_text}" if result_text else action_text

                    # Persist final assistant message
                    if self._history_repo and session_id:
                        await self._history_repo.append(
                            session_id=session_id,
                            project_id=self._project_id,
                            agent_id=self._agent_id,
                            role="assistant",
                            content=result_text,
                        )

                    return self._build_result(
                        result_text, session_id, resolved_model,
                        total_input_tokens, total_output_tokens, start_ms,
                        is_error=False,
                    )

                # Tool calls present -- execute them and append results
                await self._execute_and_append_tool_results(
                    messages, response, tool_executor, tool_calls, session_id,
                )

            # Exceeded max iterations
            return self._build_result(
                "[ERROR] Max tool loop iterations exceeded",
                session_id, resolved_model,
                total_input_tokens, total_output_tokens, start_ms,
                is_error=True,
            )

        except Exception as e:
            logger.error(
                "%s API error for agent %s: %s",
                self.PROVIDER_NAME, self._agent_id, e,
            )
            return self._build_result(
                f"[ERROR] {self.PROVIDER_NAME} API: {e}",
                session_id, resolved_model,
                total_input_tokens, total_output_tokens, start_ms,
                is_error=True,
            )

    def supports_resume(self) -> bool:
        return False

    # -- Helpers (shared) -----------------------------------------------

    def _build_result(
        self,
        result_text: str,
        session_id: str | None,
        model: str,
        input_tokens: int,
        output_tokens: int,
        start_ms: int,
        *,
        is_error: bool,
    ) -> SubprocessResult:
        duration_ms = (time.monotonic_ns() // 1_000_000) - start_ms
        cost = self._estimate_cost(model, input_tokens, output_tokens)
        return SubprocessResult(
            result_text=result_text,
            session_id=session_id,
            cost_usd=cost,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            is_error=is_error,
        )

    def _estimate_cost(
        self, model: str, input_tokens: int, output_tokens: int,
    ) -> float:
        """Estimate cost based on published pricing."""
        prices = self.PRICING.get(model, self.DEFAULT_PRICING)
        input_cost = (input_tokens / 1_000_000) * prices[0]
        output_cost = (output_tokens / 1_000_000) * prices[1]
        return round(input_cost + output_cost, 6)

    def _convert_history(self, messages: list[dict]) -> list[dict]:
        """Convert provider-neutral history into this provider's format.

        The DB stores tool_calls as ``[{"id", "name", "input"}, ...]``
        and tool results as ``{"tool_call_id", "tool_name", "content"}``.
        Each provider must convert these into its native API format.

        Default: pass-through (subclasses override).
        """
        return messages

    # -- Abstract methods subclasses must implement ----------------------

    @abstractmethod
    def _get_client(self) -> Any:
        """Lazily initialise and return the SDK client."""
        ...

    @abstractmethod
    def _build_tool_schemas(
        self, allowed_tools: str | None,
        allowed_actions: set[str] | None = None,
    ) -> list:
        """Build tool schemas in the provider's native format.

        Includes both file tools (from ``allowed_tools``) and structured
        action tools (from ``allowed_actions``).
        """
        ...

    @abstractmethod
    def _inject_system_prompt(
        self, messages: list[dict], system_prompt: str | None,
    ) -> None:
        """Mutate *messages* to inject the system prompt if needed.

        Claude and Gemini pass the system prompt via a separate API
        parameter so this is a no-op for them.  OpenAI prepends a
        ``{"role": "system", ...}`` message.
        """
        ...

    @abstractmethod
    async def _call_api(
        self,
        client: Any,
        model: str,
        messages: list[dict],
        system_prompt: str | None,
        tools: list,
    ) -> Any:
        """Make a single API call and return the raw response object."""
        ...

    @abstractmethod
    def _extract_tokens(self, response: Any) -> tuple[int, int]:
        """Return ``(input_tokens, output_tokens)`` from the response."""
        ...

    @abstractmethod
    def _extract_tool_calls(self, response: Any) -> list:
        """Return a list of tool-call objects (provider-specific).

        An empty list means the model finished without requesting tools.
        """
        ...

    @abstractmethod
    def _extract_text(self, response: Any) -> str:
        """Extract the final text from the response."""
        ...

    @abstractmethod
    async def _execute_and_append_tool_results(
        self,
        messages: list[dict],
        response: Any,
        tool_executor: ToolExecutor,
        tool_calls: list,
        session_id: str | None,
    ) -> None:
        """Execute each tool call and append results to *messages*.

        This is provider-specific because the message format differs:
        - Claude: single ``user`` message with a list of ``tool_result``
        - OpenAI: separate ``tool`` role messages per tool call
        - Gemini: single ``tool`` message with ``_function_responses``

        Must also persist assistant + tool messages via ``_history_repo``.
        """
        ...
