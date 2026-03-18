"""Claude CLI provider — wraps the existing SubprocessManager."""

from __future__ import annotations

from pathlib import Path

from core.providers.base import BaseProvider
from core.subprocess_manager import SubprocessManager, SubprocessResult


class ClaudeCLIProvider(BaseProvider):
    """Thin wrapper around SubprocessManager for the provider interface.

    This is the default provider — all existing behavior is preserved
    exactly as before.
    """

    def __init__(self, subprocess_manager: SubprocessManager | None = None):
        self._subprocess = subprocess_manager or SubprocessManager()

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
        # CLI uses text-based ```action``` blocks; allowed_actions is ignored
        return await self._subprocess.invoke(
            prompt=prompt,
            system_prompt=system_prompt,
            model=model,
            allowed_tools=allowed_tools,
            session_id=session_id,
            working_dir=working_dir,
            timeout=timeout,
            max_budget_usd=max_budget_usd,
            mcp_config_path=mcp_config_path,
        )

    def supports_resume(self) -> bool:
        return True
