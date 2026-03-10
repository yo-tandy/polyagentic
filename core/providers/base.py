"""Base provider interface — all providers must implement this."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from core.subprocess_manager import SubprocessResult


class BaseProvider(ABC):
    """Abstract base for all AI model providers.

    The ``invoke()`` signature matches ``SubprocessManager.invoke()`` so
    that ``Agent`` can swap providers transparently.
    """

    @abstractmethod
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
        """Send a prompt to the model and return the result.

        Parameters match SubprocessManager.invoke() for drop-in compatibility.
        """
        ...

    @abstractmethod
    def supports_resume(self) -> bool:
        """Whether this provider supports session resumption via session_id.

        Claude CLI supports ``--resume``.  API providers use conversation
        history from the DB instead.
        """
        ...
