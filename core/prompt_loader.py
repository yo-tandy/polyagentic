"""Prompt inheritance loader.

Reads markdown prompt files that may declare ``extends: <parent>`` on the
first line.  Walks the chain child -> parent -> grandparent and composes
the final prompt by concatenating child-first (most specific content
first, base protocol last).

Resolution order for the parent name:
  1. agents/prompts/bases/<name>.md
  2. agents/prompts/<name>.md
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "agents" / "prompts"
BASES_DIR = PROMPTS_DIR / "bases"

_EXTENDS_PREFIX = "extends:"


def _parse_file(path: Path) -> tuple[str | None, str]:
    """Read a prompt file and return (parent_name | None, body)."""
    text = path.read_text()
    first_line = text.split("\n", 1)[0].strip()

    if first_line.lower().startswith(_EXTENDS_PREFIX):
        parent_name = first_line[len(_EXTENDS_PREFIX):].strip()
        body = text.split("\n", 1)[1].lstrip("\n") if "\n" in text else ""
        return parent_name, body

    return None, text


def _resolve_name(name: str, prompts_dir: Path, bases_dir: Path) -> Path:
    """Find a prompt file by bare name, checking bases/ first."""
    base_path = bases_dir / f"{name}.md"
    if base_path.exists():
        return base_path
    root_path = prompts_dir / f"{name}.md"
    if root_path.exists():
        return root_path
    raise FileNotFoundError(
        f"Prompt '{name}' not found in {bases_dir} or {prompts_dir}"
    )


def load_prompt(name: str, *, prompts_dir: Path | None = None) -> str:
    """Load a prompt by name, resolving the full inheritance chain.

    Args:
        name: Bare name (e.g. "perry", "engineer").
        prompts_dir: Override the prompts directory (useful for testing).

    Returns:
        Composed prompt string with child content first, base content last.
        Template variables ({team_roster}, {memory}) are preserved for
        later substitution by the agent class.
    """
    p_dir = prompts_dir or PROMPTS_DIR
    b_dir = (prompts_dir / "bases") if prompts_dir else BASES_DIR

    start_path = _resolve_name(name, p_dir, b_dir)

    chain: list[str] = []
    visited: set[str] = set()
    current_path = start_path

    while current_path is not None:
        canon = str(current_path.resolve())
        if canon in visited:
            raise ValueError(f"Circular inheritance detected at {current_path}")
        visited.add(canon)

        parent_name, body = _parse_file(current_path)
        chain.append(body)

        if parent_name:
            current_path = _resolve_name(parent_name, p_dir, b_dir)
        else:
            current_path = None

    return "\n\n".join(chain)
