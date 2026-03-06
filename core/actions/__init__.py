"""Centralized action system for the polyagentic framework.

Actions are auto-discovered from this package.  Every ``.py`` file
(except ``base`` and ``registry``) is scanned for :class:`BaseAction`
subclasses and automatically registered.

Usage::

    from core.actions import create_default_registry

    registry = create_default_registry()
    agent.configure(..., action_registry=registry)
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from pathlib import Path

from core.actions.base import BaseAction, ActionField, ActionContext
from core.actions.registry import ActionRegistry

logger = logging.getLogger(__name__)

# Re-export for convenience
__all__ = [
    "BaseAction",
    "ActionField",
    "ActionContext",
    "ActionRegistry",
    "create_default_registry",
    "discover_actions",
]

_SKIP_MODULES = {"base", "registry"}


def discover_actions() -> list[BaseAction]:
    """Scan this package for BaseAction subclasses and instantiate them.

    Every ``.py`` file in ``core/actions/`` (except ``base.py`` and
    ``registry.py``) is imported.  All :class:`BaseAction` subclasses
    found inside are instantiated and returned.
    """
    actions: list[BaseAction] = []
    package_dir = str(Path(__file__).parent)

    for _, module_name, _ in pkgutil.iter_modules([package_dir]):
        if module_name in _SKIP_MODULES:
            continue
        try:
            module = importlib.import_module(f"core.actions.{module_name}")
        except Exception:
            logger.exception(
                "Failed to import action module: core.actions.%s",
                module_name,
            )
            continue

        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, BaseAction)
                and attr is not BaseAction
                and getattr(attr, "name", "")  # must have a name
                and attr.__module__ == module.__name__  # defined here, not imported
            ):
                actions.append(attr())

    return actions


def create_default_registry() -> ActionRegistry:
    """Create and populate the action registry by auto-discovering actions.

    Scans ``core/actions/*.py`` for :class:`BaseAction` subclasses,
    instantiates each one, and registers them in a new
    :class:`ActionRegistry`.
    """
    registry = ActionRegistry()
    actions = discover_actions()

    for action in actions:
        registry.register(action)
        logger.debug("Registered action '%s'", action.name)

    logger.info(
        "Action registry loaded: %d actions discovered", len(actions),
    )
    return registry
