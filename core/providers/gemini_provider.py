"""Gemini provider — Google GenAI SDK integration."""

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
    build_tool_schemas_gemini,
)

logger = logging.getLogger(__name__)

# Model alias mapping: short names → full API model IDs
GEMINI_MODEL_MAP = {
    "gemini-2.5-pro": "gemini-2.5-pro",
    "gemini-2.5-flash": "gemini-2.5-flash",
    "gemini-2.0-flash": "gemini-2.0-flash",
    "gemini-2.0-flash-lite": "gemini-2.0-flash-lite",
    # Default aliases — map generic names to sensible Gemini models
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

MAX_TOOL_LOOP_ITERATIONS = 25


class GeminiProvider(BaseProvider):
    """Google Gemini API provider with agentic tool-calling loop."""

    def __init__(
        self,
        api_key: str | None = None,
        history_repo=None,
        project_id: str = "",
        agent_id: str = "",
    ):
        self._api_key = api_key or os.environ.get("GOOGLE_API_KEY", "")
        self._history_repo = history_repo
        self._project_id = project_id
        self._agent_id = agent_id
        self._client = None  # Lazy init

    def _get_client(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    async def invoke(
        self,
        prompt: str,
        system_prompt: str | None = None,
        model: str = "gemini-2.5-flash",
        allowed_tools: str | None = None,
        session_id: str | None = None,
        working_dir: Path | None = None,
        timeout: int = 300,
        max_budget_usd: float | None = None,
    ) -> SubprocessResult:
        start_ms = time.monotonic_ns() // 1_000_000
        resolved_model = GEMINI_MODEL_MAP.get(model, model)
        tool_executor = ToolExecutor(working_dir) if working_dir else ToolExecutor()

        # Build tool schemas (Gemini format)
        tool_declarations = build_tool_schemas_gemini(allowed_tools)

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
            from google.genai import types

            for iteration in range(MAX_TOOL_LOOP_ITERATIONS):
                # Convert messages to Gemini content format
                contents = self._build_gemini_contents(messages)

                # Build generation config
                config_kwargs: dict = {}
                if system_prompt:
                    config_kwargs["system_instruction"] = system_prompt
                if tool_declarations:
                    config_kwargs["tools"] = [
                        types.Tool(function_declarations=[
                            types.FunctionDeclaration(**decl)
                            for decl in tool_declarations
                        ])
                    ]

                config = types.GenerateContentConfig(**config_kwargs)

                logger.info(
                    "Gemini API call: model=%s, iteration=%d, messages=%d, tools=%d",
                    resolved_model, iteration, len(messages), len(tool_declarations),
                )

                response = await client.aio.models.generate_content(
                    model=resolved_model,
                    contents=contents,
                    config=config,
                )

                # Track tokens
                if response.usage_metadata:
                    total_input_tokens += response.usage_metadata.prompt_token_count or 0
                    total_output_tokens += response.usage_metadata.candidates_token_count or 0

                # Check for function calls
                function_calls = []
                text_parts = []
                if response.candidates and response.candidates[0].content:
                    for part in response.candidates[0].content.parts:
                        if part.function_call:
                            function_calls.append(part.function_call)
                        elif part.text:
                            text_parts.append(part.text)

                if not function_calls:
                    # No more tool calls — extract final text
                    result_text = "\n".join(text_parts)

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

                # Function calls present — execute them
                # Build the model's response as a message
                assistant_parts = []
                for text in text_parts:
                    assistant_parts.append({"type": "text", "text": text})
                for fc in function_calls:
                    assistant_parts.append({
                        "type": "function_call",
                        "name": fc.name,
                        "args": dict(fc.args) if fc.args else {},
                    })

                messages.append({
                    "role": "assistant",
                    "content": "\n".join(text_parts) if text_parts else "",
                    "_function_calls": [
                        {"name": fc.name, "args": dict(fc.args) if fc.args else {}}
                        for fc in function_calls
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
                            for fc in function_calls
                        ],
                    )

                # Execute each tool and collect results
                tool_results = []
                for fc in function_calls:
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
            logger.error("Gemini API error for agent %s: %s", self._agent_id, e)
            return SubprocessResult(
                result_text=f"[ERROR] Gemini API: {e}",
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

    @staticmethod
    def _estimate_cost(
        model: str, input_tokens: int, output_tokens: int,
    ) -> float:
        """Estimate cost based on published pricing."""
        prices = GEMINI_PRICING.get(model, (0.15, 0.60))
        input_cost = (input_tokens / 1_000_000) * prices[0]
        output_cost = (output_tokens / 1_000_000) * prices[1]
        return round(input_cost + output_cost, 6)
