"""Claude API provider — direct Anthropic SDK integration."""

from __future__ import annotations

import logging
from typing import Any

from core.providers.api_provider_base import APIProviderBase
from core.providers.tool_executor import (
    ToolExecutor,
    build_tool_schemas_anthropic,
)

logger = logging.getLogger(__name__)

# Model alias mapping: short names -> full API model IDs
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


class ClaudeAPIProvider(APIProviderBase):
    """Direct Anthropic API provider with agentic tool-calling loop."""

    PROVIDER_NAME = "Claude"
    MODEL_MAP = CLAUDE_MODEL_MAP
    PRICING = CLAUDE_PRICING
    DEFAULT_PRICING = (3.00, 15.00)
    ENV_KEY = "ANTHROPIC_API_KEY"

    # -- Abstract method implementations --------------------------------

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic
            self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
        return self._client

    def _build_tool_schemas(self, allowed_tools: str | None) -> list:
        return build_tool_schemas_anthropic(allowed_tools)

    def _inject_system_prompt(
        self, messages: list[dict], system_prompt: str | None,
    ) -> None:
        # Claude passes system prompt as a separate API parameter, not in
        # the messages list.  Nothing to do here.
        pass

    async def _call_api(
        self,
        client: Any,
        model: str,
        messages: list[dict],
        system_prompt: str | None,
        tools: list,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": 16384,
            "messages": messages,
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if tools:
            kwargs["tools"] = tools

        return await client.messages.create(**kwargs)

    def _extract_tokens(self, response: Any) -> tuple[int, int]:
        if response.usage:
            return (
                response.usage.input_tokens or 0,
                response.usage.output_tokens or 0,
            )
        return (0, 0)

    def _extract_tool_calls(self, response: Any) -> list:
        tool_use_blocks = [
            b for b in response.content if b.type == "tool_use"
        ]
        if not tool_use_blocks or response.stop_reason == "end_turn":
            return []
        return tool_use_blocks

    def _extract_text(self, response: Any) -> str:
        """Extract text content from an Anthropic API response."""
        parts = []
        for block in response.content:
            if block.type == "text":
                parts.append(block.text)
        return "\n".join(parts) if parts else ""

    async def _execute_and_append_tool_results(
        self,
        messages: list[dict],
        response: Any,
        tool_executor: ToolExecutor,
        tool_calls: list,
        session_id: str | None,
    ) -> None:
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
                    for b in tool_calls
                ],
            )

        # Execute each tool and collect results
        tool_results = []
        for tool_block in tool_calls:
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
