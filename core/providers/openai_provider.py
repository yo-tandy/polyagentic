"""OpenAI provider — direct OpenAI SDK integration."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from core.providers.base import BaseProvider
from core.subprocess_manager import SubprocessResult
from core.providers.tool_executor import (
    ToolExecutor,
    build_tool_schemas_openai,
)

logger = logging.getLogger(__name__)

# Model alias mapping: short names → full API model IDs
OPENAI_MODEL_MAP = {
    "gpt-4o": "gpt-4o",
    "gpt-4o-mini": "gpt-4o-mini",
    "gpt-4.1": "gpt-4.1",
    "gpt-4.1-mini": "gpt-4.1-mini",
    "gpt-4.1-nano": "gpt-4.1-nano",
    "o3": "o3",
    "o3-mini": "o3-mini",
    "o4-mini": "o4-mini",
    # Default aliases
    "sonnet": "gpt-4o",       # map generic aliases to sensible defaults
    "opus": "gpt-4o",
    "haiku": "gpt-4o-mini",
}

# Approximate cost per 1M tokens (input, output)
OPENAI_PRICING = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "o3": (2.00, 8.00),
    "o3-mini": (1.10, 4.40),
    "o4-mini": (1.10, 4.40),
}

MAX_TOOL_LOOP_ITERATIONS = 25


class OpenAIProvider(BaseProvider):
    """OpenAI API provider with agentic tool-calling loop."""

    def __init__(
        self,
        api_key: str | None = None,
        history_repo=None,
        project_id: str = "",
        agent_id: str = "",
    ):
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._history_repo = history_repo
        self._project_id = project_id
        self._agent_id = agent_id
        self._client = None  # Lazy init

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=self._api_key)
        return self._client

    async def invoke(
        self,
        prompt: str,
        system_prompt: str | None = None,
        model: str = "gpt-4o",
        allowed_tools: str | None = None,
        session_id: str | None = None,
        working_dir: Path | None = None,
        timeout: int = 300,
        max_budget_usd: float | None = None,
    ) -> SubprocessResult:
        start_ms = time.monotonic_ns() // 1_000_000
        resolved_model = OPENAI_MODEL_MAP.get(model, model)
        tool_executor = ToolExecutor(working_dir) if working_dir else ToolExecutor()

        # Build tool schemas (OpenAI format)
        tools = build_tool_schemas_openai(allowed_tools)

        # Load or start conversation history
        messages = []
        new_session = False
        if session_id and self._history_repo:
            messages = await self._history_repo.get_history(session_id)
        if not session_id and self._history_repo:
            session_id = await self._history_repo.create_session(
                self._project_id, self._agent_id,
            )
            new_session = True

        # Prepend system message if provided
        if system_prompt:
            # Only add system if not already present
            if not messages or messages[0].get("role") != "system":
                messages.insert(0, {"role": "system", "content": system_prompt})

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
                # Build API kwargs
                kwargs = {
                    "model": resolved_model,
                    "messages": messages,
                }
                if tools:
                    kwargs["tools"] = tools

                logger.info(
                    "OpenAI API call: model=%s, iteration=%d, messages=%d, tools=%d",
                    resolved_model, iteration, len(messages), len(tools),
                )

                response = await client.chat.completions.create(**kwargs)

                choice = response.choices[0]
                message = choice.message

                # Track tokens
                if response.usage:
                    total_input_tokens += response.usage.prompt_tokens or 0
                    total_output_tokens += response.usage.completion_tokens or 0

                # Check for tool calls
                tool_calls = message.tool_calls or []

                if not tool_calls or choice.finish_reason == "stop":
                    # No more tool calls — extract final text
                    result_text = message.content or ""

                    # Persist assistant message
                    if self._history_repo and session_id:
                        await self._history_repo.append(
                            session_id=session_id,
                            project_id=self._project_id,
                            agent_id=self._agent_id,
                            role="assistant",
                            content=result_text,
                        )

                    duration_ms = (time.monotonic_ns() // 1_000_000) - start_ms
                    cost = self._estimate_cost(
                        resolved_model, total_input_tokens, total_output_tokens,
                    )

                    return SubprocessResult(
                        result_text=result_text,
                        session_id=session_id,
                        cost_usd=cost,
                        duration_ms=duration_ms,
                        input_tokens=total_input_tokens,
                        output_tokens=total_output_tokens,
                        is_error=False,
                    )

                # Tool calls present — execute them
                # Append assistant message with tool_calls
                assistant_msg = {
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                }
                messages.append(assistant_msg)

                # Persist assistant message with tool calls
                if self._history_repo and session_id:
                    await self._history_repo.append(
                        session_id=session_id,
                        project_id=self._project_id,
                        agent_id=self._agent_id,
                        role="assistant",
                        content=message.content or "",
                        tool_calls=[
                            {
                                "id": tc.id,
                                "name": tc.function.name,
                                "input": json.loads(tc.function.arguments),
                            }
                            for tc in tool_calls
                        ],
                    )

                # Execute each tool and collect results
                for tc in tool_calls:
                    tool_name = tc.function.name
                    try:
                        tool_args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        tool_args = {}

                    logger.info(
                        "Executing tool %s (id=%s) for agent %s",
                        tool_name, tc.id, self._agent_id,
                    )
                    result = await tool_executor.execute(tool_name, tool_args)

                    # OpenAI expects tool results as separate messages
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })

                    # Persist tool result
                    if self._history_repo and session_id:
                        await self._history_repo.append(
                            session_id=session_id,
                            project_id=self._project_id,
                            agent_id=self._agent_id,
                            role="tool",
                            content=result,
                            tool_call_id=tc.id,
                            tool_name=tool_name,
                        )

            # Exceeded max iterations
            duration_ms = (time.monotonic_ns() // 1_000_000) - start_ms
            return SubprocessResult(
                result_text="[ERROR] Max tool loop iterations exceeded",
                session_id=session_id,
                cost_usd=self._estimate_cost(
                    resolved_model, total_input_tokens, total_output_tokens,
                ),
                duration_ms=duration_ms,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                is_error=True,
            )

        except Exception as e:
            duration_ms = (time.monotonic_ns() // 1_000_000) - start_ms
            logger.error("OpenAI API error for agent %s: %s", self._agent_id, e)
            return SubprocessResult(
                result_text=f"[ERROR] OpenAI API: {e}",
                session_id=session_id,
                cost_usd=self._estimate_cost(
                    resolved_model, total_input_tokens, total_output_tokens,
                ),
                duration_ms=duration_ms,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                is_error=True,
            )

    def supports_resume(self) -> bool:
        return False

    @staticmethod
    def _estimate_cost(
        model: str, input_tokens: int, output_tokens: int,
    ) -> float:
        """Estimate cost based on published pricing."""
        prices = OPENAI_PRICING.get(model, (2.50, 10.00))
        input_cost = (input_tokens / 1_000_000) * prices[0]
        output_cost = (output_tokens / 1_000_000) * prices[1]
        return round(input_cost + output_cost, 6)
