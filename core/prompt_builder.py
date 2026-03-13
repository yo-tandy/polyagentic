"""Prompt construction logic extracted from Agent.

Handles template rendering, system prompt assembly, per-message prompt
building, task-board context, and session prompt-hash checks.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from core.task import TaskStatus

if TYPE_CHECKING:
    from core.memory_manager import MemoryManager
    from core.knowledge_base import KnowledgeBase
    from core.task_board import TaskBoard
    from core.session_store import SessionStore

logger = logging.getLogger(__name__)


class PromptBuilder:
    """Builds prompts for an agent — extracted from the Agent god class.

    All methods mirror their original Agent counterparts exactly so that
    behaviour is functionally identical after the refactor.
    """

    def __init__(
        self,
        *,
        agent_id: str,
        prompt_template: str,
        memory_manager: MemoryManager | None,
        knowledge_base: KnowledgeBase | None,
        task_board: TaskBoard | None,
        session_store: SessionStore | None,
        phase_board: Any | None,
        get_known_actions_fn: Callable[[], set[str]],
        max_task_context_items: int | None,
        other_agents_max_tasks: int | None,
        mcp_config_path_fn: Callable[[], Path | None],
    ) -> None:
        self._agent_id = agent_id
        self._prompt_template = prompt_template
        self._memory_manager = memory_manager
        self._knowledge_base = knowledge_base
        self._task_board = task_board
        self._session_store = session_store
        self._phase_board = phase_board
        self._get_known_actions = get_known_actions_fn
        self._max_task_context_items = max_task_context_items
        self._other_agents_max_tasks = other_agents_max_tasks
        self._mcp_config_path_fn = mcp_config_path_fn

        # Mutable state mirroring Agent attributes that the builder reads.
        # These are kept in sync by the Agent after ``update_team_roster``.
        self.system_prompt: str = prompt_template
        self._team_roster: str = ""
        self._team_roles: str = ""
        self._routing_guide: str = ""
        self._stateless: bool = False
        self._use_session: bool = True
        self._current_task_plan: str | None = None

    # ------------------------------------------------------------------
    # Template rendering
    # ------------------------------------------------------------------

    def _render_prompt_template(
        self,
        template: str,
        roster: str = "",
        team_roles: str = "",
        routing_guide: str = "",
    ) -> str:
        """Replace standard template variables in a prompt string.

        Uses cached (sync) memory so this method stays sync-safe.
        Full async memory is injected by ``_build_full_system_prompt``.
        """
        prompt = template.replace("{team_roster}", roster)
        prompt = prompt.replace("{team_roles}", team_roles)
        prompt = prompt.replace("{routing_guide}", routing_guide)
        memory = ""
        if self._memory_manager:
            memory = self._memory_manager.get_combined_memory_sync(self._agent_id)
        prompt = prompt.replace("{memory}", memory or "No memory recorded yet.")
        return prompt

    # ------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------

    async def _build_full_system_prompt(self) -> str:
        """Build the complete system prompt including memory."""
        prompt = self.system_prompt
        if self._memory_manager:
            memory = await self._memory_manager.get_combined_memory(self._agent_id)
            if memory:
                prompt += f"\n\n{memory}"
        return prompt

    def _get_session_reminder(self) -> str:
        """Build a compact reminder for resumed sessions.

        Includes the full list of valid action names so Claude doesn't
        hallucinate wrong action names in long-running sessions.
        """
        actions = ", ".join(sorted(self._get_known_actions()))
        return (
            "CRITICAL PROTOCOL REMINDER: All outputs MUST use ```action fenced blocks. "
            f"Valid actions: {actions}. "
            "Use ONLY these exact action names — unknown names will be rejected. "
            "You create documents via action blocks — the orchestrator handles "
            "file I/O on your behalf. You do NOT need Write/Edit/Bash tools for documents. "
            "Never say you lack file-writing tools."
        )

    async def _get_system_prompt_if_first_call(self) -> str | None:
        if self._stateless:
            # Stateless agent — always re-render and send full system prompt
            self.system_prompt = self._render_prompt_template(
                self._prompt_template,
                self._team_roster or "",
                team_roles=self._team_roles or "",
                routing_guide=self._routing_guide or "",
            )
            prompt = await self._build_full_system_prompt()
            return prompt

        prompt = await self._build_full_system_prompt()
        if self._use_session and self._session_store and self._session_store.get(self._agent_id):
            # Check if system prompt has changed since session was created
            current_hash = hashlib.md5(prompt.encode()).hexdigest()[:12]
            stored_hash = self._session_store.get_prompt_hash(self._agent_id)
            if stored_hash and current_hash == stored_hash:
                # Prompt unchanged — append a compact reminder to the resumed session
                # (subprocess_manager will use --append-system-prompt)
                return self._get_session_reminder()
            # Prompt changed — invalidate stale session so Claude gets the new prompt
            # (preserves accumulated stats — only the session ID is cleared)
            logger.info(
                "Agent %s prompt changed (hash %s -> %s), invalidating session",
                self._agent_id, stored_hash, current_hash,
            )
            await self._session_store.invalidate_session(self._agent_id)
        return prompt

    # ------------------------------------------------------------------
    # Per-message prompt
    # ------------------------------------------------------------------

    async def _build_prompt(self, msg) -> str:
        """Build the per-message prompt including context sections.

        ``msg`` is a :class:`core.message.Message` instance.
        """
        parts = []

        # 1. Memory context (for resumed sessions — first-call memory is in system prompt)
        if self._memory_manager and self._use_session:
            session_id = self._session_store.get(self._agent_id) if self._session_store else None
            if session_id:  # Resumed session — memory not in system prompt
                memory = await self._memory_manager.get_combined_memory(self._agent_id)
                if memory:
                    parts.append(f"[Your Memory]\n{memory}\n---")

        # 2. Knowledge base index
        if self._knowledge_base:
            kb_summary = self._knowledge_base.get_index_summary()
            if kb_summary:
                parts.append(f"[Project Knowledge Base]\n{kb_summary}\n---")

        # 3. Task board summary
        if self._task_board:
            task_ctx = self._build_task_context()
            if task_ctx:
                parts.append(f"[Current Task Board]\n{task_ctx}\n---")

        # 3b. Active plan for current task
        if self._current_task_plan and msg.task_id:
            parts.append(f"[Your Plan for This Task]\n{self._current_task_plan}\n---")

        # 3c. Connected MCP servers
        mcp_path = self._mcp_config_path_fn()
        if mcp_path and mcp_path.exists():
            try:
                import json as _json
                mcp_data = _json.loads(mcp_path.read_text())
                server_names = list(mcp_data.get("mcpServers", {}).keys())
                if server_names:
                    parts.append(f"[Connected MCP Servers: {', '.join(server_names)}]\n---")
            except Exception:
                pass

        # 4. Original message
        parts.append(f"[Message from {msg.sender}]")
        parts.append(f"Type: {msg.type.value}")
        if msg.task_id:
            parts.append(f"Task ID: {msg.task_id}")
        parts.append(f"\n{msg.content}")

        if msg.metadata:
            parts.append(f"\nMetadata: {msg.metadata}")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Task board context
    # ------------------------------------------------------------------

    def _build_task_context(self) -> str:
        """Build task board summary, emphasising this agent's tasks."""
        tasks = self._task_board.get_all_tasks()
        if not tasks:
            return ""

        my_tasks = self._task_board.get_tasks_for_agent(self._agent_id)
        my_task_ids = {t.id for t in my_tasks}
        other_tasks = [t for t in tasks if t.id not in my_task_ids]

        lines = []

        # Phase context
        if self._phase_board:
            phases = self._phase_board.get_all_phases()
            if phases:
                current = self._phase_board.get_current_phase()
                lines.append("PROJECT PHASES:")
                for p in phases:
                    marker = " [CURRENT]" if (current and p["id"] == current["id"]) else ""
                    lines.append(f"  - {p['name']} ({p['status']}){marker}")
                lines.append("")
        if my_tasks:
            lines.append("YOUR TASKS (ordered by priority — review tasks first):")
            for t in my_tasks:
                review_marker = " [NEEDS YOUR REVIEW]" if (t.status == TaskStatus.REVIEW and t.reviewer == self._agent_id) else ""
                est = f" [{t.estimate}sp]" if t.estimate else ""
                lines.append(f"  - [P{t.priority}] [{t.status.value}]{est} {t.title} (id: {t.id}){review_marker}")

        if other_tasks:
            limit = self._max_task_context_items
            if limit is None:
                shown = other_tasks
            else:
                shown = other_tasks[:max(0, limit - len(my_tasks))]
            lines.append("\nOTHER TEAM TASKS:")
            for t in shown:
                assignee = t.assignee or "unassigned"
                est = f" [{t.estimate}sp]" if t.estimate else ""
                lines.append(f"  - [P{t.priority}] [{t.status.value}]{est} {t.title} (id: {t.id}, assignee: {assignee})")
            if len(other_tasks) > len(shown):
                lines.append(f"  ... and {len(other_tasks) - len(shown)} more")

        # Velocity data for management agents
        if self._max_task_context_items is None:
            velocity = self._compute_velocity(tasks)
            if velocity:
                lines.append("\nVELOCITY DATA (completed tasks):")
                for aid, data in sorted(velocity.items()):
                    pts_per_30 = round(30 / (data["total_min"] / data["points"]), 1) if data["points"] and data["total_min"] > 0 else "N/A"
                    lines.append(
                        f"  {aid}: {data['points']}sp in {data['total_min']:.0f}min "
                        f"({data['tasks']} tasks) => ~{pts_per_30}sp/30min"
                    )

        lines.append("")
        lines.append(
            "NOTE: When asked for a status update, report ONLY on YOUR TASKS above. "
            "Other team tasks are shown for coordination context only — do not report "
            "on work done by other agents unless you directly manage them.\n\n"
            "IMPORTANT: A status update request is also a nudge to take action. "
            "After giving your update, check your task list:\n"
            "1. If you have IN-PROGRESS tasks you are not actively working on — "
            "resume working on them immediately.\n"
            "2. If you have no in-progress tasks but have PENDING tasks assigned to you — "
            "pick the highest-priority one, move it to in-progress, and start working on it.\n"
            "Do not just report status — take action on your tasks."
        )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Velocity helper
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_velocity(tasks) -> dict:
        """Compute per-agent velocity from completed tasks with timing data."""
        from datetime import datetime as _dt
        velocity: dict[str, dict] = {}
        for t in tasks:
            if t.status != TaskStatus.DONE or not t.completed_at or not t.estimate or not t.assignee or not t.started_at:
                continue
            try:
                started = _dt.fromisoformat(t.started_at)
                completed = _dt.fromisoformat(t.completed_at)
                dur_min = (completed - started).total_seconds() / 60
                if dur_min <= 0:
                    continue
            except (ValueError, TypeError):
                continue
            if t.assignee not in velocity:
                velocity[t.assignee] = {"points": 0, "total_min": 0, "tasks": 0}
            velocity[t.assignee]["points"] += t.estimate
            velocity[t.assignee]["total_min"] += dur_min
            velocity[t.assignee]["tasks"] += 1
        return velocity
