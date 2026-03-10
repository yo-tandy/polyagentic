"""Claude API provider — direct Anthropic SDK integration."""

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
    build_tool_schemas_anthropic,
)

logger = logging.getLogger(__name__)

# Model alias mapping: short names → full API model IDs
CLAUDE_MODEL_MAP = {
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
    "haiku": "claude-haiku-4-5",
    # Also accept full IDs
    "claude-sonnet-4-6": "claude-sonnet-4-6",
    "claude-opus-4-6": "claude-opus-4-6",
    "claude-haiku-4-5": "claude-haiku-4-5",
    # Legacy aliases
    "claude-sonnet-4-5": "claude-sonnet-4-5",
    "claude-opus-4-5": "claude-opus-4-5",
}

# Approximate cost per 1M tokens (input, output)
CLAUDE_PRICING = {
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-opus-4-5": (5.00, 25.00),
}

MAX_TOOL_LOOP_ITERATIONS = 25


class ClaudeAPIProvider(BaseProvider):
    """Direct Anthropic API provider with agentic tool-calling loop."""

    def __init__(
        self,
        api_key: str | None = None,
        history_repo=None,
        project_id: str = "",
        agent_id: str = "",
    ):
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._history_repo = history_repo
        self._project_id = project_id
        self._agent_id = agent_id
        self._client = None  # Lazy init

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
        return self._client

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
        start_ms = time.monotonic_ns() // 1_000_000
        resolved_model = CLAUDE_MODEL_MAP.get(model, model)
        tool_executor = ToolExecutor(working_dir) if working_dir else ToolExecutor()

        # Build tool schemas
        tools = build_tool_schemas_anthropic(allowed_tools)

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
                    "max_tokens": 16384,
                    "messages": messages,
                }
                if system_prompt:
                    kwargs["system"] = system_prompt
                if tools:
                    kwargs["tools"] = tools

                logger.info(
                    "Claude API call: model=%s, iteration=%d, messages=%d, tools=%d",
                    resolved_model, iteration, len(messages), len(tools),
                )

                response = await client.messages.create(**kwargs)

                # Track tokens
                if response.usage:
                    total_input_tokens += response.usage.input_tokens or 0
                    total_output_tokens += response.usage.output_tokens or 0

                # Check for tool use
                tool_use_blocks = [
                    b for b in response.content if b.type == "tool_use"
                ]

                if not tool_use_blocks or response.stop_reason == "end_turn":
                    # No more tool calls — extract final text
                    result_text = self._extract_text(response)

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
                # Append assistant response with tool_use blocks
                assistant_content = []
                for block in response.content:
                    if block.type == "text":
                        assistant_content.append({
                            "type": "text",
                            "text": block.text,
                        })
                    elif block.type == "tool_use":
                        assistant_content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        })

                messages.append({"role": "assistant", "content": assistant_content})

                # Persist assistant message with tool calls
                if self._history_repo and session_id:
                    await self._history_repo.append(
                        session_id=session_id,
                        project_id=self._project_id,
                        agent_id=self._agent_id,
                        role="assistant",
                        content=self._extract_text(response),
                        tool_calls=[
                            {"id": b.id, "name": b.name, "input": b.input}
                            for b in tool_use_blocks
                        ],
                    )

                # Execute each tool and collect results
                tool_results = []
                for tool_block in tool_use_blocks:
                    logger.info(
                        "Executing tool %s (id=%s) for agent %s",
                        tool_block.name, tool_block.id, self._agent_id,
                    )
                    result = await tool_executor.execute(
                        tool_block.name, tool_block.input or {},
                    )
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_block.id,
                        "content": result,
                    })

                    # Persist tool result
                    if self._history_repo and session_id:
                        await self._history_repo.append(
                            session_id=session_id,
                            project_id=self._project_id,
                            agent_id=self._agent_id,
                            role="user",
                            content=result,
                            tool_call_id=tool_block.id,
                            tool_name=tool_block.name,
                        )

                # Append tool results as user message
                messages.append({"role": "user", "content": tool_results})

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
            logger.error("Claude API error for agent %s: %s", self._agent_id, e)
            return SubprocessResult(
                result_text=f"[ERROR] Claude API: {e}",
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
    def _extract_text(response) -> str:
        """Extract text content from an Anthropic API response."""
        parts = []
        for block in response.content:
            if block.type == "text":
                parts.append(block.text)
        return "\n".join(parts) if parts else ""

    @staticmethod
    def _estimate_cost(
        model: str, input_tokens: int, output_tokens: int,
    ) -> float:
        """Estimate cost based on published pricing."""
        prices = CLAUDE_PRICING.get(model, (3.00, 15.00))
        input_cost = (input_tokens / 1_000_000) * prices[0]
        output_cost = (output_tokens / 1_000_000) * prices[1]
        return round(input_cost + output_cost, 6)
