"""Provider factory + fallback wrapper.

Creates provider instances from configuration strings and wraps them
with optional automatic fallback on errors.
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.providers.base import BaseProvider
from core.subprocess_manager import SubprocessResult

logger = logging.getLogger(__name__)

VALID_PROVIDERS = {"claude-cli", "claude-api", "openai", "gemini"}


def create_provider(
    provider: str,
    api_key: str | None = None,
    history_repo=None,
    project_id: str = "",
    agent_id: str = "",
    subprocess_mgr=None,
) -> BaseProvider:
    """Create a provider instance by name.

    Args:
        provider: One of "claude-cli", "claude-api", "openai", "gemini".
        api_key: API key for the provider (falls back to env vars).
        history_repo: ProviderHistoryRepository for API providers.
        project_id: Current project ID.
        agent_id: Agent this provider is attached to.
        subprocess_mgr: SubprocessManager for claude-cli provider.

    Returns:
        A configured BaseProvider instance.

    Raises:
        ValueError: If provider name is not recognized.
    """
    if provider == "claude-cli":
        from core.providers.claude_cli_provider import ClaudeCLIProvider
        return ClaudeCLIProvider(subprocess_mgr)

    if provider == "claude-api":
        from core.providers.claude_api_provider import ClaudeAPIProvider
        return ClaudeAPIProvider(
            api_key=api_key,
            history_repo=history_repo,
            project_id=project_id,
            agent_id=agent_id,
        )

    if provider == "openai":
        from core.providers.openai_provider import OpenAIProvider
        return OpenAIProvider(
            api_key=api_key,
            history_repo=history_repo,
            project_id=project_id,
            agent_id=agent_id,
        )

    if provider == "gemini":
        from core.providers.gemini_provider import GeminiProvider
        return GeminiProvider(
            api_key=api_key,
            history_repo=history_repo,
            project_id=project_id,
            agent_id=agent_id,
        )

    raise ValueError(
        f"Unknown provider '{provider}'. Valid: {', '.join(sorted(VALID_PROVIDERS))}"
    )


class FallbackProvider(BaseProvider):
    """Wraps a primary provider with automatic fallback on errors.

    If the primary provider returns ``is_error=True``, the same request
    is retried with the fallback provider.  The fallback result includes
    a note about the switch.
    """

    def __init__(self, primary: BaseProvider, fallback: BaseProvider):
        self._primary = primary
        self._fallback = fallback

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
        result = await self._primary.invoke(
            prompt=prompt,
            system_prompt=system_prompt,
            model=model,
            allowed_tools=allowed_tools,
            session_id=session_id,
            working_dir=working_dir,
            timeout=timeout,
            max_budget_usd=max_budget_usd,
        )

        if not result.is_error:
            return result

        logger.warning(
            "Primary provider failed (%s), falling back. Error: %s",
            type(self._primary).__name__,
            result.result_text[:200],
        )

        fallback_result = await self._fallback.invoke(
            prompt=prompt,
            system_prompt=system_prompt,
            model=model,
            allowed_tools=allowed_tools,
            session_id=None,  # Don't reuse primary's session
            working_dir=working_dir,
            timeout=timeout,
            max_budget_usd=max_budget_usd,
        )

        # Combine cost from both attempts
        fallback_result.cost_usd += result.cost_usd
        fallback_result.duration_ms += result.duration_ms
        fallback_result.input_tokens += result.input_tokens
        fallback_result.output_tokens += result.output_tokens

        return fallback_result

    def supports_resume(self) -> bool:
        return self._primary.supports_resume()
