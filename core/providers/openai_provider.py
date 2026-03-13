"""OpenAI provider — direct OpenAI SDK integration."""

from __future__ import annotations

import json
import logging
from typing import Any

from core.providers.api_provider_base import APIProviderBase
from core.providers.tool_executor import (
    ToolExecutor,
    build_tool_schemas_openai,
)

logger = logging.getLogger(__name__)

# Model alias mapping: short names -> full API model IDs
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


class OpenAIProvider(APIProviderBase):
    """OpenAI API provider with agentic tool-calling loop."""

    PROVIDER_NAME = "OpenAI"
    MODEL_MAP = OPENAI_MODEL_MAP
    PRICING = OPENAI_PRICING
    DEFAULT_PRICING = (2.50, 10.00)
    ENV_KEY = "OPENAI_API_KEY"

    # -- Abstract method implementations --------------------------------

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=self._api_key)
        return self._client

    def _build_tool_schemas(self, allowed_tools: str | None) -> list:
        return build_tool_schemas_openai(allowed_tools)

    def _inject_system_prompt(
        self, messages: list[dict], system_prompt: str | None,
    ) -> None:
        if system_prompt:
            # Only add system if not already present
            if not messages or messages[0].get("role") != "system":
                messages.insert(0, {"role": "system", "content": system_prompt})

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
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        return await client.chat.completions.create(**kwargs)

    def _extract_tokens(self, response: Any) -> tuple[int, int]:
        if response.usage:
            return (
                response.usage.prompt_tokens or 0,
                response.usage.completion_tokens or 0,
            )
        return (0, 0)

    def _extract_tool_calls(self, response: Any) -> list:
        choice = response.choices[0]
        message = choice.message
        tool_calls = message.tool_calls or []
        if not tool_calls or choice.finish_reason == "stop":
            return []
        return tool_calls

    def _extract_text(self, response: Any) -> str:
        return response.choices[0].message.content or ""

    async def _execute_and_append_tool_results(
        self,
        messages: list[dict],
        response: Any,
        tool_executor: ToolExecutor,
        tool_calls: list,
        session_id: str | None,
    ) -> None:
        message = response.choices[0].message

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
