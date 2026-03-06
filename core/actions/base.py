"""Base class for all actions in the polyagentic system.

Every action inherits from :class:`BaseAction` and lives in its own file
under ``core/actions/``.  The registry auto-discovers them at startup.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from core.message import Message, MessageType

if TYPE_CHECKING:
    from core.agent import Agent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

@dataclass
class ActionField:
    """Schema for a single field in an action."""

    name: str
    type: str  # "string", "integer", "array", "boolean"
    required: bool = False
    description: str = ""
    default: Any = None
    enum: list[str] | None = None


@dataclass
class ActionContext:
    """Mutable context passed through the action execution loop.

    Allows cross-action coordination (e.g. ``resolve_comments`` verifying
    that ``update_document`` was emitted in the same response).
    """

    edited_doc_ids: set[str] = field(default_factory=set)
    kb_changed: bool = False


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def infer_doc_category(title: str) -> str:
    """Infer document category from title when not explicitly provided."""
    t = title.lower()
    if any(k in t for k in ("spec", "requirement", "product")):
        return "specs"
    if any(k in t for k in ("arch", "design", "system")):
        return "architecture"
    if any(k in t for k in ("plan", "roadmap", "milestone")):
        return "planning"
    return ""


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class BaseAction:
    """Base class for all polyagentic actions.

    Subclasses must set the class-level attributes and implement
    :meth:`execute`.

    Permissions are controlled on the **agent/role** side, not here.
    Each role defines its ``allowed_actions`` list; the
    :class:`ActionRegistry` enforces this before every execution call.
    """

    # ── Must be set by subclasses ──────────────────────────────────────

    name: str = ""
    description: str = ""
    fields: list[ActionField] = []

    # ── Optional overrides ─────────────────────────────────────────────

    produces_messages: bool = True
    example: dict | None = None

    # ── Execution ─────────────────────────────────────────────────────

    async def execute(
        self,
        agent: Agent,
        action: dict,
        original_msg: Message,
        ctx: ActionContext,
    ) -> list[Message]:
        """Execute the action.  Must be implemented by every subclass."""
        raise NotImplementedError(
            f"Action '{self.name}' has not implemented execute()"
        )

    # ── Schema / prompt generation ────────────────────────────────────

    def get_schema(self) -> dict:
        """Return the action schema as a dict."""
        schema: dict[str, Any] = {
            "action": self.name,
            "description": self.description,
            "fields": {},
        }
        for f in self.fields:
            entry: dict[str, Any] = {
                "type": f.type,
                "required": f.required,
                "description": f.description,
            }
            if f.enum:
                entry["enum"] = f.enum
            if f.default is not None:
                entry["default"] = f.default
            schema["fields"][f.name] = entry
        return schema

    def generate_prompt_doc(self) -> str:
        """Generate human-readable prompt documentation for this action."""
        lines = [f"### `{self.name}`"]
        lines.append(self.description)
        lines.append("")

        required = [f for f in self.fields if f.required]
        optional = [f for f in self.fields if not f.required]

        if required:
            for f in required:
                enum_note = f" ({', '.join(f.enum)})" if f.enum else ""
                lines.append(
                    f"- **`{f.name}`** ({f.type}): {f.description}{enum_note}"
                )

        if optional:
            for f in optional:
                dflt = (
                    f" (default: {f.default})" if f.default is not None else ""
                )
                enum_note = f" ({', '.join(f.enum)})" if f.enum else ""
                lines.append(
                    f"- `{f.name}` ({f.type}, optional): "
                    f"{f.description}{dflt}{enum_note}"
                )

        if self.example:
            lines.append("")
            lines.append("```action")
            lines.append(json.dumps(self.example, indent=2))
            lines.append("```")

        return "\n".join(lines)
