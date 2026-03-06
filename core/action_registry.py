"""Backward-compatibility shim — re-exports from ``core.actions``.

All action definitions now live as individual classes under
``core/actions/``.  This module re-exports the public API so that
existing ``from core.action_registry import ...`` statements
continue to work.
"""

from core.actions import (          # noqa: F401
    ActionRegistry,
    create_default_registry,
)
from core.actions.base import (     # noqa: F401
    BaseAction,
    ActionField,
    ActionContext,
)
