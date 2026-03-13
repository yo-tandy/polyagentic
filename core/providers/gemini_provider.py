"""Gemini provider — Google GenAI SDK integration."""

from __future__ import annotations

import logging
from typing import Any

from core.providers.api_provider_base import APIProviderBase
from core.providers.tool_executor import (
    ToolExecutor,
    build_tool_schemas_gemini,
)

logger = logging.getLogger(__name__)

# Model alias mapping: short names -> full API model IDs
GEMINI_MODEL_MAP = {
    "gemini-2.5-pro": "gemini-2.5-pro",
    "gemini-2.5-flash": "gemini-2.5-flash",
    "gemini-2.0-flash": "gemini-2.0-flash",
    "gemini-2.0-flash-lite": "gemini-2.0-flash-lite",
    # Default aliases -- map generic names to sensible Gemini models
    "sonnet": "gemini-2.5-pro",
    "opus": "gemini-2.5-pro",
    "haiku": "gemini-2.0-flash-lite",
}

# Approximate cost per 1M tokens (input, output)
GEMINI_PRICING = {
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.15, 0.60),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-2.0-flash-lite": (0.075, 0.30),
}


class GeminiProvider(APIProviderBase):
    """Google Gemini API provider with agentic tool-calling loop."""

    PROVIDER_NAME = "Gemini"
    MODEL_MAP = GEMINI_MODEL_MAP
    PRICING = GEMINI_PRICING
    DEFAULT_PRICING = (0.15, 0.60)
    ENV_KEY = "GOOGLE_API_KEY"

    # -- Abstract method implementations --------------------------------

    def _get_client(self) -> Any:
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def _build_tool_schemas(self, allowed_tools: str | None) -> list:
        return build_tool_schemas_gemini(allowed_tools)

    def _inject_system_prompt(
        self, messages: list[dict], system_prompt: str | None,
    ) -> None:
        # Gemini passes system_instruction via the config, not in messages.
        pass

    async def _call_api(
        self,
        client: Any,
        model: str,
        messages: list[dict],
        system_prompt: str | None,
        tools: list,
    ) -> Any:
        from google.genai import types

        # Convert messages to Gemini content format
        contents = self._build_gemini_contents(messages)

        # Build generation config
        config_kwargs: dict[str, Any] = {}
        if system_prompt:
            config_kwargs["system_instruction"] = system_prompt
        if tools:
            config_kwargs["tools"] = [
                types.Tool(function_declarations=[
                    types.FunctionDeclaration(**decl)
                    for decl in tools
                ])
            ]

        config = types.GenerateContentConfig(**config_kwargs)

        return await client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )

    def _extract_tokens(self, response: Any) -> tuple[int, int]:
        if response.usage_metadata:
            return (
                response.usage_metadata.prompt_token_count or 0,
                response.usage_metadata.candidates_token_count or 0,
            )
        return (0, 0)

    def _extract_tool_calls(self, response: Any) -> list:
        function_calls = []
        if response.candidates and response.candidates[0].content:
            for part in response.candidates[0].content.parts:
                if part.function_call:
                    function_calls.append(part.function_call)
        return function_calls

    def _extract_text(self, response: Any) -> str:
        text_parts = []
        if response.candidates and response.candidates[0].content:
            for part in response.candidates[0].content.parts:
                if part.text:
                    text_parts.append(part.text)
        return "\n".join(text_parts)

    async def _execute_and_append_tool_results(
        self,
        messages: list[dict],
        response: Any,
        tool_executor: ToolExecutor,
        tool_calls: list,
        session_id: str | None,
    ) -> None:
        # Extract text parts from the response for the assistant message
        text_parts = []
        if response.candidates and response.candidates[0].content:
            for part in response.candidates[0].content.parts:
                if part.text:
                    text_parts.append(part.text)

        # Build the model's response as a message
        messages.append({
            "role": "assistant",
            "content": "\n".join(text_parts) if text_parts else "",
            "_function_calls": [
                {"name": fc.name, "args": dict(fc.args) if fc.args else {}}
                for fc in tool_calls
            ],
        })

        # Persist assistant message with tool calls
        if self._history_repo and session_id:
            await self._history_repo.append(
                session_id=session_id,
                project_id=self._project_id,
                agent_id=self._agent_id,
                role="assistant",
                content="\n".join(text_parts) if text_parts else "",
                tool_calls=[
                    {"name": fc.name, "input": dict(fc.args) if fc.args else {}}
                    for fc in tool_calls
                ],
            )

        # Execute each tool and collect results
        tool_results = []
        for fc in tool_calls:
            tool_name = fc.name
            tool_args = dict(fc.args) if fc.args else {}

            logger.info(
                "Executing tool %s for agent %s",
                tool_name, self._agent_id,
            )
            result = await tool_executor.execute(tool_name, tool_args)
            tool_results.append({
                "name": tool_name,
                "result": result,
            })

            # Persist tool result
            if self._history_repo and session_id:
                await self._history_repo.append(
                    session_id=session_id,
                    project_id=self._project_id,
                    agent_id=self._agent_id,
                    role="user",
                    content=result,
                    tool_name=tool_name,
                )

        # Append function responses
        messages.append({
            "role": "tool",
            "content": "",
            "_function_responses": tool_results,
        })

    # -- Gemini-specific helpers ----------------------------------------

    @staticmethod
    def _build_gemini_contents(messages: list[dict]) -> list:
        """Convert internal message format to Gemini Content objects."""
        from google.genai import types

        contents = []
        for msg in messages:
            role = msg.get("role", "user")
            # Gemini uses "user" and "model" roles
            gemini_role = "model" if role == "assistant" else "user"

            # Handle function calls from the model
            if role == "assistant" and "_function_calls" in msg:
                parts = []
                if msg.get("content"):
                    parts.append(types.Part.from_text(text=msg["content"]))
                for fc in msg["_function_calls"]:
                    parts.append(types.Part.from_function_call(
                        name=fc["name"],
                        args=fc["args"],
                    ))
                contents.append(types.Content(role="model", parts=parts))
                continue

            # Handle function responses
            if role == "tool" and "_function_responses" in msg:
                parts = []
                for fr in msg["_function_responses"]:
                    parts.append(types.Part.from_function_response(
                        name=fr["name"],
                        response={"result": fr["result"]},
                    ))
                contents.append(types.Content(role="user", parts=parts))
                continue

            # Regular text message
            content_text = msg.get("content", "")
            if content_text:
                contents.append(types.Content(
                    role=gemini_role,
                    parts=[types.Part.from_text(text=content_text)],
                ))

        return contents
