from __future__ import annotations

import asyncio
import hashlib
import time
import json
import logging
import re
from enum import Enum
from pathlib import Path
from typing import Any, TYPE_CHECKING

from core.message import Message, MessageType
from core.subprocess_manager import SubprocessManager, DockerSubprocessManager
from core.providers.base import BaseProvider
from core.providers.claude_cli_provider import ClaudeCLIProvider
from core.session_store import SessionStore
from core.prompt_builder import PromptBuilder
from core.action_handler import ActionHandler
from config import DEFAULT_MODEL, CLAUDE_ALLOWED_TOOLS_DEV

from core.task import TaskStatus

if TYPE_CHECKING:
    from core.message_broker import MessageBroker
    from core.task_board import TaskBoard
    from core.memory_manager import MemoryManager
    from core.knowledge_base import KnowledgeBase
    from core.actions.registry import ActionRegistry

logger = logging.getLogger(__name__)

MAX_TASK_CONTEXT_ITEMS = 20


class AgentStatus(str, Enum):
    IDLE = "idle"
    WORKING = "working"
    WAITING = "waiting"
    ERROR = "error"
    OFFLINE = "offline"
    SESSION_PAUSED = "session-paused"
    PENDING_REAUTH = "pending-reauth"


class Agent:
    # Base set of valid action names. Subclasses extend via _get_known_actions().
    KNOWN_ACTIONS = {
        "respond_to_user", "delegate", "update_task", "update_memory",
        "write_document", "update_document", "resolve_comments",
        "start_conversation", "end_conversation",
        "request_capability", "search_mcp_registry", "deploy_mcp",
    }

    # Max "other team tasks" shown in prompt context.
    # None = unlimited (for management agents that need full board visibility).
    max_task_context_items: int | None = MAX_TASK_CONTEXT_ITEMS

    # Class-level flag to deduplicate auth_required WS events.
    # Reset when reauth completes (via the /sessions/reauth endpoint).
    _auth_event_broadcast: bool = False

    def __init__(
        self,
        agent_id: str,
        name: str,
        role: str,
        system_prompt: str,
        model: str = DEFAULT_MODEL,
        allowed_tools: str = CLAUDE_ALLOWED_TOOLS_DEV,
        messages_dir: Path | None = None,
        working_dir: Path | None = None,
        timeout: int = 300,
        use_session: bool = True,
        max_budget_usd: float | None = None,
        execution_mode: str = "local",
        container_name: str | None = None,
        stateless: bool = False,
        allowed_actions: set[str] | None = None,
    ):
        self.agent_id = agent_id
        self.name = name
        self.role = role
        self.system_prompt = system_prompt
        self.model = model
        self.allowed_tools = allowed_tools
        self.messages_dir = messages_dir
        self.working_dir = working_dir
        self.timeout = timeout
        self.use_session = use_session
        self.max_budget_usd = max_budget_usd
        self.execution_mode = execution_mode
        self.container_name = container_name

        # Role-driven attributes
        self._stateless: bool = stateless
        self._allowed_actions: set[str] | None = allowed_actions  # None = all
        self.deps: dict[str, Any] = {}  # generic dependency store
        self.mcp_config_path: Path | None = None  # per-agent MCP server config

        # Prompt template + team roster (used by update_team_roster)
        self._prompt_template: str = system_prompt
        self._team_roster: str = ""
        self._team_roles: str = ""
        self._routing_guide: str = ""

        self.status = AgentStatus.OFFLINE
        self.current_task_id: str | None = None
        self.messages_processed = 0
        self.last_error: str | None = None
        self.activity: str | None = None  # sub-status: "model", "processing"
        self.last_processed_at: float = 0  # monotonic timestamp, used for nudge cooldown
        self.message_queue: asyncio.Queue[Message] = asyncio.Queue()
        self._queued_message_ids: set[str] = set()  # dedup: message IDs on queue
        self._task_cancelled: bool = False  # set by cancel_current_task()
        self._memory_updated: bool = False  # set by UpdateMemory action

        if execution_mode == "container" and container_name:
            self._subprocess = DockerSubprocessManager(container_name)
        else:
            self._subprocess = SubprocessManager()
        # Provider wraps _subprocess by default; can be swapped via set_provider()
        self._provider: BaseProvider = ClaudeCLIProvider(self._subprocess)
        self._session_store: SessionStore | None = None
        self._broker: MessageBroker | None = None
        self._task_board: TaskBoard | None = None
        self._memory_manager: MemoryManager | None = None
        self._knowledge_base: KnowledgeBase | None = None
        self._conversation_manager = None
        self._action_registry: ActionRegistry | None = None
        self._user_facing_agent: str = "manny"
        self._loop_task: asyncio.Task | None = None
        self._running = False
        self._current_task_plan: str | None = None  # plan text for current task

        # Delegate objects (created lazily in configure())
        self._prompt_builder: PromptBuilder | None = None
        self._action_handler: ActionHandler | None = None

    @property
    def inbox_dir(self) -> Path:
        return self.messages_dir / self.agent_id / "inbox"

    @property
    def outbox_dir(self) -> Path:
        return self.messages_dir / self.agent_id / "outbox"

    @property
    def workingbox_dir(self) -> Path:
        return self.messages_dir / self.agent_id / "workingbox"

    def configure(
        self,
        session_store: SessionStore,
        broker: MessageBroker,
        task_board: TaskBoard,
        memory_manager: MemoryManager | None = None,
        knowledge_base: KnowledgeBase | None = None,
        conversation_manager=None,
        action_registry: ActionRegistry | None = None,
        phase_board=None,
    ):
        self._session_store = session_store
        self._broker = broker
        self._task_board = task_board
        self._memory_manager = memory_manager
        self._knowledge_base = knowledge_base
        self._conversation_manager = conversation_manager
        self._action_registry = action_registry
        self._phase_board = phase_board

        # Build delegate objects now that dependencies are available
        self._prompt_builder = PromptBuilder(
            agent_id=self.agent_id,
            prompt_template=self._prompt_template,
            memory_manager=memory_manager,
            knowledge_base=knowledge_base,
            task_board=task_board,
            session_store=session_store,
            phase_board=phase_board,
            get_known_actions_fn=self._get_known_actions,
            max_task_context_items=self.max_task_context_items,
            other_agents_max_tasks=self.max_task_context_items,
            mcp_config_path_fn=lambda: self.mcp_config_path,
        )
        # Sync mutable state from Agent into PromptBuilder
        self._prompt_builder.system_prompt = self.system_prompt
        self._prompt_builder._team_roster = self._team_roster
        self._prompt_builder._team_roles = self._team_roles
        self._prompt_builder._routing_guide = self._routing_guide
        self._prompt_builder._stateless = self._stateless
        self._prompt_builder._use_session = self.use_session

        self._action_handler = ActionHandler(
            agent_id=self.agent_id,
            agent_name=self.name,
            action_registry=action_registry,
            memory_manager=memory_manager,
            knowledge_base=knowledge_base,
            task_board=task_board,
            conversation_manager=conversation_manager,
            broker=broker,
            session_store=session_store,
            provider=self._provider,
            user_facing_agent=self._user_facing_agent,
            allowed_actions=self._allowed_actions,
            get_known_actions_fn=self._get_known_actions,
        )
        # Back-reference so ActionHandler can pass `self` (the Agent) to
        # ActionRegistry.execute_all which expects an Agent instance.
        self._action_handler._agent_ref = self

    def set_provider(self, provider: BaseProvider) -> None:
        """Swap the AI model provider (e.g. Claude CLI -> OpenAI API)."""
        self._provider = provider
        if self._action_handler:
            self._action_handler._set_provider(provider)

    async def start(self):
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        self.workingbox_dir.mkdir(parents=True, exist_ok=True)
        self.status = AgentStatus.IDLE
        self._running = True
        await self._recover_from_restart()
        self._loop_task = asyncio.create_task(self._message_loop())
        logger.info("Agent %s (%s) started", self.name, self.agent_id)

    async def stop(self):
        self._running = False
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        self.status = AgentStatus.OFFLINE
        self.current_task_id = None
        self.activity = None
        await self._broadcast_status()
        logger.info("Agent %s stopped", self.name)

    async def cancel_current_task(self, reason: str = "task cancelled"):
        """Abort the in-flight subprocess for the current task.

        Called when the task board detects that the task an agent is working
        on has moved to DONE or CANCELLED.  Sets a flag so the message loop
        skips result processing, and kills the subprocess to save tokens/cost.
        """
        if not self.current_task_id:
            return
        logger.info(
            "Cancelling current task %s for agent %s: %s",
            self.current_task_id, self.agent_id, reason,
        )
        self._task_cancelled = True
        # Kill the running CLI subprocess (synchronous, safe from any context)
        self._subprocess.cancel()

    async def _recover_from_restart(self):
        """Re-queue messages from workingbox and inbox after a server restart."""
        # 1. Workingbox first — interrupted in-progress task (highest priority)
        for wb_file in sorted(
            self.workingbox_dir.glob("*.json"),
            key=lambda f: f.stat().st_mtime,
        ):
            try:
                msg = Message.from_file(wb_file)
                # Skip tasks that are already completed/cancelled
                if msg.task_id and self._task_board:
                    task = self._task_board.get_task(msg.task_id)
                    if task and task.status in (TaskStatus.DONE, TaskStatus.CANCELLED):
                        self._complete_workingbox_task(msg)
                        logger.info(
                            "Recovery: cleaned stale workingbox task %s (status=%s) for agent %s",
                            msg.task_id, task.status, self.agent_id,
                        )
                        continue
                await self.message_queue.put(msg)
                self._queued_message_ids.add(msg.id)
                logger.info(
                    "Recovery: re-queued workingbox task %s for agent %s",
                    msg.task_id, self.agent_id,
                )
            except Exception:
                logger.exception("Recovery: failed to read workingbox file %s", wb_file)

        # 2. Inbox — pending assigned tasks
        for inbox_file in sorted(
            self.inbox_dir.glob("*.json"),
            key=lambda f: f.stat().st_mtime,
        ):
            try:
                msg = Message.from_file(inbox_file)
                # Skip tasks that are already completed/cancelled
                if msg.task_id and self._task_board:
                    task = self._task_board.get_task(msg.task_id)
                    if task and task.status in (TaskStatus.DONE, TaskStatus.CANCELLED):
                        self._move_inbox_to_outbox(msg)
                        logger.info(
                            "Recovery: cleaned stale inbox task %s (status=%s) for agent %s",
                            msg.task_id, task.status, self.agent_id,
                        )
                        continue
                if msg.type == MessageType.TASK:
                    await self.message_queue.put(msg)
                    self._queued_message_ids.add(msg.id)
                    logger.info(
                        "Recovery: re-queued inbox task %s for agent %s",
                        msg.task_id, self.agent_id,
                    )
            except Exception:
                logger.exception("Recovery: failed to read inbox file %s", inbox_file)

        # 3. Orphaned board tasks — PENDING tasks assigned to this agent
        #    that have no corresponding message file (e.g. lost due to
        #    broker.send() bug or crash during scope decomposition).
        if self._task_board:
            queued_task_ids = set()
            # Collect task IDs already in the queue, inbox, or workingbox
            for mid in self._queued_message_ids:
                # Parse task_id from queued messages — check inbox + workingbox files
                for d in (self.inbox_dir, self.workingbox_dir):
                    f = d / f"{mid}.json"
                    if f.exists():
                        try:
                            m = Message.from_file(f)
                            if m.task_id:
                                queued_task_ids.add(m.task_id)
                        except Exception:
                            pass

            for task in self._task_board.get_workable_tasks(self.agent_id, self.role):
                if task.status == TaskStatus.PENDING and task.id not in queued_task_ids:
                    msg = Message(
                        sender=task.created_by or "system",
                        recipient=self.agent_id,
                        type=MessageType.TASK,
                        content=task.description,
                        task_id=task.id,
                        metadata={"task_title": task.title},
                    )
                    await self.message_queue.put(msg)
                    self._queued_message_ids.add(msg.id)
                    # Also persist to inbox so workingbox lifecycle works
                    msg.to_file(self.inbox_dir)
                    logger.info(
                        "Recovery: created message for orphaned board task %s (%s) for agent %s",
                        task.id, task.title, self.agent_id,
                    )

    async def _message_loop(self):
        while self._running:
            try:
                msg = await asyncio.wait_for(
                    self.message_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                # While idle, reflect session-paused or pending-reauth state
                if self.status == AgentStatus.PENDING_REAUTH:
                    continue  # stay in PENDING_REAUTH until reauth endpoint clears it
                if (self.use_session and self._session_store
                        and self._session_store.is_paused(self.agent_id)):
                    if self.status != AgentStatus.SESSION_PAUSED:
                        self.status = AgentStatus.SESSION_PAUSED
                        await self._broadcast_status()
                    continue
                # Session was resumed — restore IDLE if we were paused
                if self.status == AgentStatus.SESSION_PAUSED:
                    self.status = AgentStatus.IDLE
                    await self._broadcast_status()
                continue
            except asyncio.CancelledError:
                break

            # Session pause gate: hold message while session is paused
            while (self._running and self.use_session
                   and self._session_store
                   and self._session_store.is_paused(self.agent_id)):
                if self.status != AgentStatus.SESSION_PAUSED:
                    self.status = AgentStatus.SESSION_PAUSED
                    await self._broadcast_status()
                await asyncio.sleep(1.0)

            # Session kill: invalidate dead session so next invoke creates a fresh one
            # (preserves accumulated stats -- only the session ID is cleared)
            if (self.use_session and self._session_store
                    and self._session_store.is_killed(self.agent_id)):
                await self._session_store.invalidate_session(self.agent_id)

            # Set current_task_id BEFORE broadcasting so UI sees the task
            if msg.task_id and msg.type == MessageType.TASK:
                self.current_task_id = msg.task_id
                if self._action_handler:
                    self._action_handler.current_task_id = msg.task_id

            self.status = AgentStatus.WORKING
            self.activity = "model"
            await self._broadcast_status()

            # Auto-transition: mark task in_progress when agent starts working
            if msg.task_id and msg.type == MessageType.TASK and self._task_board:
                task = self._task_board.get_task(msg.task_id)
                # Guard: skip if task was already claimed by another agent
                if task and task.assignee and task.assignee != self.agent_id:
                    logger.info(
                        "Skipping task %s — already claimed by %s",
                        msg.task_id, task.assignee,
                    )
                    for d in (self.workingbox_dir, self.inbox_dir):
                        f = d / f"{msg.id}.json"
                        if f.exists():
                            f.unlink()
                    continue
                if task and task.status in (TaskStatus.DRAFT, TaskStatus.PENDING, TaskStatus.PAUSED):
                    # Enforce single-task invariant: reset any stale
                    # IN_PROGRESS tasks before transitioning the new one.
                    for other in self._task_board.get_tasks_by_assignee(self.agent_id):
                        if other.status == TaskStatus.IN_PROGRESS and other.id != msg.task_id:
                            await self._task_board.update_task(
                                other.id,
                                status=TaskStatus.PENDING,
                                _agent_id=self.agent_id,
                                progress_note="Reset: agent picked up a different task",
                            )
                            logger.warning(
                                "Reset stale in-progress task %s for agent %s (now working on %s)",
                                other.id, self.agent_id, msg.task_id,
                            )
                    await self._task_board.update_task(
                        msg.task_id,
                        status=TaskStatus.IN_PROGRESS,
                        assignee=self.agent_id,
                        _agent_id=self.agent_id,
                        progress_note="Agent started working on this task",
                    )

                    # Scope analysis gate: analyze before executing
                    if not task.scope_approved:
                        try:
                            approved = await self._run_scope_analysis(task, msg)
                        except Exception as exc:
                            logger.exception(
                                "Agent %s scope analysis crashed for task %s: %s",
                                self.agent_id, msg.task_id, exc,
                            )
                            approved = True  # fall through to execution
                        if self._task_cancelled:
                            logger.info("Agent %s: task cancelled during scope analysis", self.agent_id)
                            self._task_cancelled = False
                            self.current_task_id = None
                            self.status = AgentStatus.IDLE
                            await self._broadcast_status()
                            continue
                        if not approved:
                            # Task was decomposed — skip execution
                            self.current_task_id = None
                            self.status = AgentStatus.IDLE
                            await self._broadcast_status()
                            continue

                    # Planning phase: ask agent to outline its approach before execution
                    try:
                        await self._run_planning_phase(task, msg)
                    except Exception as exc:
                        logger.exception(
                            "Agent %s planning phase crashed for task %s: %s",
                            self.agent_id, msg.task_id, exc,
                        )
                    if self._task_cancelled:
                        logger.info("Agent %s: task cancelled during planning phase", self.agent_id)
                        self._task_cancelled = False
                        self.current_task_id = None
                        self.status = AgentStatus.IDLE
                        await self._broadcast_status()
                        continue

                # Move task message to workingbox (persist in-progress state)
                # Fires for all TASK messages, including already-in-progress tasks
                self._move_task_to_workingbox(msg)

            try:
                responses = await self.process_message(msg)

                # --- Task cancellation: skip result processing ----------------
                if self._task_cancelled:
                    logger.info(
                        "Agent %s: task %s cancelled mid-flight, skipping result processing",
                        self.agent_id, msg.task_id,
                    )
                    self._task_cancelled = False
                    continue  # finally block still runs → cleans up workingbox
                # --------------------------------------------------------------

                # --- AUTH_ERROR detection: enter PENDING_REAUTH ---------------
                is_auth_error = any(
                    r.content and "[AUTH_ERROR]" in r.content for r in responses
                )
                if is_auth_error:
                    logger.warning(
                        "Agent %s received AUTH_ERROR — entering PENDING_REAUTH",
                        self.agent_id,
                    )
                    self.activity = None
                    self.status = AgentStatus.PENDING_REAUTH
                    await self._broadcast_status()

                    # Broadcast auth_required event (deduplicated per class)
                    if self._broker and not Agent._auth_event_broadcast:
                        Agent._auth_event_broadcast = True
                        await self._broker.broadcast_event({
                            "event_type": "auth_required",
                            "data": {
                                "agent_id": self.agent_id,
                                "message": "Claude CLI authentication has expired.",
                            },
                        })

                    # Re-queue the original message so it retries after re-auth
                    await self.message_queue.put(msg)

                    # Hold loop: wait until status is changed by the reauth endpoint
                    while self._running and self.status == AgentStatus.PENDING_REAUTH:
                        await asyncio.sleep(1.0)

                    continue  # retry the re-queued message
                # --------------------------------------------------------------

                self.activity = "processing"
                await self._broadcast_status()

                # Auto review notification: if task moved to REVIEW, notify reviewer
                if msg.task_id and self._task_board:
                    task = self._task_board.get_task(msg.task_id)
                    if task and task.status == TaskStatus.REVIEW and task.reviewer:
                        responses.append(Message(
                            sender=self.agent_id,
                            recipient=task.reviewer,
                            type=MessageType.REVIEW_REQUEST,
                            content=f"Task '{task.title}' is ready for your review.\n\nCompletion summary: {task.completion_summary or 'No summary provided.'}",
                            task_id=msg.task_id,
                            metadata={"task_title": task.title},
                        ))

                # Auto pause enforcement: if pause was requested, ensure task is paused
                if (msg.metadata.get("command") == "pause_task"
                        and msg.task_id and self._task_board):
                    task = self._task_board.get_task(msg.task_id)
                    if task and task.status == TaskStatus.IN_PROGRESS:
                        await self._task_board.update_task(
                            msg.task_id,
                            status=TaskStatus.PAUSED,
                            _agent_id=self.agent_id,
                            progress_note="Task paused by user request",
                        )

                # Review feedback loop: when reviewer finishes, notify original assignee
                if msg.task_id and self._task_board:
                    task = self._task_board.get_task(msg.task_id)
                    if task and task.reviewer == self.agent_id and task.assignee:
                        if task.status == TaskStatus.DONE and task.review_output:
                            responses.append(Message(
                                sender=self.agent_id,
                                recipient=task.assignee,
                                type=MessageType.SYSTEM,
                                content=(
                                    f"REVIEW FEEDBACK for task '{task.title}':\n\n"
                                    f"{task.review_output}\n\n"
                                    f"Use this feedback to update your personality notes — "
                                    f"adjust your approach for future tasks based on this review."
                                ),
                                task_id=msg.task_id,
                                metadata={"command": "review_feedback"},
                            ))
                        elif task.status == TaskStatus.IN_PROGRESS and task.review_output:
                            responses.append(Message(
                                sender=self.agent_id,
                                recipient=task.assignee,
                                type=MessageType.TASK,
                                content=(
                                    f"REVISION REQUESTED for task '{task.title}':\n\n"
                                    f"{task.review_output}\n\n"
                                    f"Please address the review feedback and resubmit for review."
                                ),
                                task_id=msg.task_id,
                                metadata={"command": "revision_requested"},
                            ))

                # Auto-close enforcement: if agent processed a TASK message
                # but didn't explicitly update the task status, auto-close it.
                # Delegation is NOT a reason to skip -- delegated subtasks are
                # tracked independently; the delegating agent's own task is done.
                if (msg.type == MessageType.TASK and msg.task_id
                        and self._task_board):
                    task = self._task_board.get_task(msg.task_id)
                    # Track whether the agent actually executed any actions
                    actions_executed = (
                        self._action_handler.last_actions_count
                        if self._action_handler else 0
                    )
                    if task and task.status == TaskStatus.IN_PROGRESS and not actions_executed:
                        logger.warning(
                            "Agent %s processed task %s (%s) with 0 actions — "
                            "returning to pending instead of auto-closing",
                            self.agent_id, msg.task_id, task.title[:60],
                        )
                        await self._task_board.update_task(
                            msg.task_id,
                            status=TaskStatus.PENDING,
                            _agent_id=self.agent_id,
                            progress_note=(
                                f"⚠️ Agent {self.agent_id} processed this task but executed "
                                "0 structured actions. Task returned to pending for retry."
                            ),
                        )
                        # Send error message back to the agent's manager
                        summary = ""
                        for r in responses:
                            if r.content:
                                summary = r.content[:300]
                                break
                        responses.append(Message(
                            sender="system",
                            recipient=self.agent_id,
                            type=MessageType.SYSTEM,
                            content=(
                                f"[Zero Actions Warning] You processed task {msg.task_id} "
                                f"({task.title}) but executed 0 valid actions. "
                                "The task has been returned to pending.\n\n"
                                "Common causes:\n"
                                "- Using an action name that doesn't exist (e.g. 'assign_ticket' instead of 'delegate')\n"
                                "- Malformed action JSON block\n\n"
                                "Please re-read the available actions and try again with the correct action format."
                            ),
                            metadata={"no_reply": True},
                        ))
                        continue  # Skip the auto-close logic below

                    if task and task.status == TaskStatus.IN_PROGRESS:
                        # Check if the response is an error (timeout, auth, etc.)
                        # If so, send the task back to PENDING for retry instead
                        # of marking it DONE with an error as the "summary".
                        is_error_response = any(
                            r.content and r.content.startswith("\u26a0\ufe0f") for r in responses
                        )
                        if is_error_response:
                            error_text = next(
                                (r.content for r in responses if r.content and r.content.startswith("\u26a0\ufe0f")), ""
                            )
                            await self._task_board.update_task(
                                msg.task_id,
                                status=TaskStatus.PENDING,
                                _agent_id=self.agent_id,
                                progress_note=f"Returned to pending — {error_text}",
                            )
                            logger.warning(
                                "Task %s returned to pending after agent %s error: %s",
                                msg.task_id, self.agent_id, error_text[:200],
                            )
                        else:
                            # Only skip auto-close if agent has an active conversation
                            # (still waiting for user input to complete the task)
                            has_conversation = bool(
                                self._conversation_manager
                                and self._conversation_manager.get_by_agent(self.agent_id)
                            )
                            if not has_conversation:
                                # Use agent's response as the completion summary
                                summary = ""
                                for r in responses:
                                    if r.content:
                                        summary = r.content[:500]
                                        break
                                result = await self._task_board.update_task(
                                    msg.task_id,
                                    status=TaskStatus.DONE,
                                    _agent_id=self.agent_id,
                                    completion_summary=summary or "Task completed",
                                )
                                if result:
                                    # Re-read task — review gate may have
                                    # redirected DONE → REVIEW for project tasks.
                                    task = self._task_board.get_task(msg.task_id)
                                    if task and task.status == TaskStatus.REVIEW:
                                        logger.info(
                                            "Auto-close redirected task %s to REVIEW for agent %s",
                                            msg.task_id, self.agent_id,
                                        )
                                        if task.reviewer:
                                            responses.append(Message(
                                                sender=self.agent_id,
                                                recipient=task.reviewer,
                                                type=MessageType.REVIEW_REQUEST,
                                                content=(
                                                    f"Task '{task.title}' is ready for your review.\n\n"
                                                    f"Completion summary: {task.completion_summary or 'No summary provided.'}"
                                                ),
                                                task_id=msg.task_id,
                                                metadata={"task_title": task.title},
                                            ))
                                    else:
                                        logger.info(
                                            "Auto-closed task %s for agent %s "
                                            "(agent did not emit update_task)",
                                            msg.task_id, self.agent_id,
                                        )
                                else:
                                    logger.warning(
                                        "Auto-close REJECTED for task %s by agent %s "
                                        "(invalid transition)",
                                        msg.task_id, self.agent_id,
                                    )

                # Memory enforcement: if agent completed a task without updating
                # memory, send a nudge so it emits update_memory actions.
                # Only nudge on original TASK messages, not on the nudge itself.
                if (msg.task_id and msg.type == MessageType.TASK
                        and self._task_board):
                    task = self._task_board.get_task(msg.task_id)
                    if (task and task.status in (TaskStatus.REVIEW, TaskStatus.DONE)
                            and task.assignee == self.agent_id
                            and not self._memory_updated):
                        logger.info(
                            "Agent %s completed task %s without memory update — sending nudge",
                            self.agent_id, msg.task_id,
                        )
                        nudge = Message(
                            sender="system",
                            recipient=self.agent_id,
                            type=MessageType.SYSTEM,
                            content=(
                                f"You just completed task \"{task.title}\" but did not update your memory. "
                                "Please emit TWO update_memory actions now:\n"
                                "1. memory_type=\"project\" — what you worked on, key decisions, current state\n"
                                "2. memory_type=\"personality\" — skills improved, lessons learned, preferences\n"
                                "Re-summarize your full memory each time (don't just append)."
                            ),
                            task_id=msg.task_id,
                            metadata={"no_reply": True},
                        )
                        await self.message_queue.put(nudge)
                self._memory_updated = False  # reset for next task

                for response in responses:
                    # Suppress self-directed responses (e.g. from scope
                    # decomposition subtasks where sender == recipient == self).
                    # These create pointless loops — the agent already processed
                    # the subtask, no need to deliver a response to itself.
                    if (response.type == MessageType.RESPONSE
                            and response.recipient == self.agent_id):
                        logger.debug(
                            "Suppressed self-directed response from %s (task %s)",
                            self.agent_id, response.task_id,
                        )
                        continue

                    # Suppress reply-back for system nudges (no_reply flag)
                    # Agent should take actions (update_task etc.) but not
                    # send conversational replies that trigger loops.
                    if (msg.metadata.get("no_reply")
                            and response.type == MessageType.RESPONSE
                            and response.recipient == msg.sender):
                        logger.debug(
                            "Suppressed reply from %s to %s (no_reply nudge)",
                            self.agent_id, msg.sender,
                        )
                        continue
                    await self._broker.deliver(response)
            except Exception as exc:
                # If the task was cancelled mid-flight, exceptions are expected
                # (e.g. subprocess killed).  Skip error handling gracefully.
                if self._task_cancelled:
                    logger.info(
                        "Agent %s: exception during cancelled task %s (expected), skipping error handling",
                        self.agent_id, msg.task_id,
                    )
                    self._task_cancelled = False
                    continue  # finally block still runs

                logger.exception("Agent %s error processing message %s", self.agent_id, msg.id)
                self.last_error = f"{type(exc).__name__}: {exc}"
                self.activity = None
                self.status = AgentStatus.ERROR
                await self._broadcast_status()

                # Return task to PENDING so it can be retried
                if (msg.task_id and self._task_board):
                    task = self._task_board.get_task(msg.task_id)
                    if task and task.status == TaskStatus.IN_PROGRESS:
                        await self._task_board.update_task(
                            msg.task_id,
                            status=TaskStatus.PENDING,
                            _agent_id=self.agent_id,
                            progress_note=f"Returned to pending — {type(exc).__name__}: {exc}",
                        )
                        # Move message back to inbox for retry
                        if msg.type == MessageType.TASK:
                            self._move_workingbox_to_inbox(msg)

                await asyncio.sleep(2)
                # If session is paused, recover to SESSION_PAUSED instead of staying in ERROR
                if (self.use_session and self._session_store
                        and self._session_store.is_paused(self.agent_id)):
                    self.status = AgentStatus.SESSION_PAUSED
                    await self._broadcast_status()
            finally:
                self._task_cancelled = False  # safety reset

                # Workingbox lifecycle: move completed tasks to outbox
                if msg.type == MessageType.TASK and msg.task_id and self._task_board:
                    task = self._task_board.get_task(msg.task_id)
                    if task and task.status != TaskStatus.IN_PROGRESS:
                        self._complete_workingbox_task(msg)

                self._queued_message_ids.discard(msg.id)
                self.current_task_id = None
                self._current_task_plan = None
                self.activity = None
                if self._action_handler:
                    self._action_handler.current_task_id = None
                self.messages_processed += 1
                self.last_processed_at = time.monotonic()
                if self.status not in (AgentStatus.ERROR, AgentStatus.SESSION_PAUSED, AgentStatus.PENDING_REAUTH):
                    # Check if session was paused while we were working
                    if (self.use_session and self._session_store
                            and self._session_store.is_paused(self.agent_id)):
                        self.status = AgentStatus.SESSION_PAUSED
                    else:
                        self.status = AgentStatus.IDLE
                    await self._broadcast_status()

    async def _run_scope_analysis(self, task, msg: Message) -> bool:
        """Analyze task scope before execution.

        Returns True if the agent should proceed (execute or approved),
        False if the task was decomposed into sub-tasks.
        """
        scope_prompt = (
            "Analyze this task's scope and complexity before execution.\n\n"
            f"Task: {task.title}\n"
            f"Description: {task.description or '(no description)'}\n\n"
            f"Assignment message:\n{msg.content}\n\n"
            "Respond with EXACTLY one JSON block:\n"
            "```action\n"
            '{"action": "scope_analysis", "complexity": "simple|medium|complex", '
            '"estimated_runtime_minutes": <number>, "estimated_tokens": <number>, '
            '"recommendation": "execute|approve|decompose", '
            '"reasoning": "<1-2 sentences>", '
            '"subtasks": [{"title": "...", "description": "..."}]}\n'
            "```\n\n"
            "Guidelines:\n"
            "- If estimated runtime < 3 minutes → \"execute\" (skip scope approval, do it directly)\n"
            "- If estimated runtime 3-5 minutes → \"approve\" (mark scope approved, then execute)\n"
            "- If estimated runtime > 5 minutes → \"decompose\" (break into smaller tasks)\n"
            "- If decomposing, break into as many subtasks as needed so each is under 5 minutes\n"
            "- subtasks array is only needed when recommendation is \"decompose\""
        )
        try:
            result = await self._provider.invoke(
                prompt=scope_prompt,
                model=self.model,
                allowed_tools="",   # text-only, no tools
                timeout=60,
            )
        except Exception as e:
            logger.warning("Scope analysis failed for task %s: %s — auto-approving", task.id, e)
            return True

        if result.is_error:
            logger.warning("Scope analysis error for task %s — auto-approving", task.id)
            return True

        # Parse the scope analysis response
        import json as _json
        import re as _re
        raw = result.result_text.strip()
        match = _re.search(r"```action\s*\n(.*?)```", raw, _re.DOTALL)
        if not match:
            logger.warning("Scope analysis: no action block found for task %s — auto-approving", task.id)
            return True

        try:
            analysis = _json.loads(match.group(1).strip())
        except _json.JSONDecodeError:
            logger.warning("Scope analysis: JSON parse failed for task %s — auto-approving", task.id)
            return True

        recommendation = analysis.get("recommendation", "execute")
        complexity = analysis.get("complexity", "unknown")
        est_minutes = analysis.get("estimated_runtime_minutes", 0)
        est_tokens = analysis.get("estimated_tokens", 0)
        reasoning = analysis.get("reasoning", "")

        # Post scope analysis as progress note
        note = (
            f"\U0001f50d Scope analysis: {complexity} | ~{est_minutes} min | ~{est_tokens} tokens\n"
            f"Recommendation: {recommendation}\n"
            f"Reasoning: {reasoning}"
        )

        if recommendation == "execute":
            # Quick task (<3 min) — skip scope approval, proceed directly
            await self._task_board.update_task(
                task.id, _agent_id=self.agent_id, progress_note=note,
            )
            logger.info("Scope analysis: task %s → execute directly (est %s min)", task.id, est_minutes)
            return True

        elif recommendation == "approve":
            # Medium task (3-5 min) — mark scope approved, then proceed
            await self._task_board.update_task(
                task.id, _agent_id=self.agent_id,
                scope_approved=True, progress_note=note,
            )
            logger.info("Scope analysis: task %s → approved (est %s min)", task.id, est_minutes)
            return True

        elif recommendation == "decompose":
            # Complex task (>5 min) — break into sub-tasks
            subtasks_data = analysis.get("subtasks", [])
            if not subtasks_data:
                # No subtasks provided — fallback to approve
                logger.warning("Scope analysis: decompose but no subtasks for task %s — approving", task.id)
                await self._task_board.update_task(
                    task.id, _agent_id=self.agent_id,
                    scope_approved=True, progress_note=note + "\n(No subtasks provided — proceeding anyway)",
                )
                return True

            new_task_ids = []
            new_messages = []
            for st in subtasks_data:
                new_task = await self._task_board.create_task(
                    title=st.get("title", "Sub-task"),
                    description=st.get("description", ""),
                    created_by=self.agent_id,
                    assignee=self.agent_id,
                    priority=task.priority,
                    labels=list(task.labels),
                    category=task.category,
                    phase_id=task.phase_id,
                    parent_task_id=task.id,
                    initial_status=TaskStatus.PENDING,
                    scope_approved=True,
                )
                new_task_ids.append(new_task.id)
                new_messages.append(Message(
                    sender=self.agent_id,
                    recipient=self.agent_id,
                    type=MessageType.TASK,
                    content=st.get("description", ""),
                    task_id=new_task.id,
                    metadata={"task_title": st.get("title", "Sub-task")},
                ))

            # Reject original task
            decompose_note = (
                f"{note}\n\nDecomposed into {len(new_task_ids)} sub-tasks: "
                + ", ".join(new_task_ids)
            )
            await self._task_board.update_task(
                task.id,
                status=TaskStatus.DONE,
                outcome="rejected",
                _agent_id=self.agent_id,
                completion_summary=f"Decomposed into sub-tasks: {', '.join(new_task_ids)}",
                progress_note=decompose_note,
            )

            # Deliver sub-task messages to self
            if self._broker:
                for m in new_messages:
                    await self._broker.deliver(m)

            logger.info(
                "Scope analysis: task %s decomposed into %d sub-tasks: %s",
                task.id, len(new_task_ids), new_task_ids,
            )
            return False

        else:
            # Unknown recommendation — auto-approve
            logger.warning("Scope analysis: unknown recommendation '%s' for task %s — auto-approving", recommendation, task.id)
            await self._task_board.update_task(
                task.id, _agent_id=self.agent_id, progress_note=note,
            )
            return True

    async def _run_planning_phase(self, task, msg: Message) -> None:
        """Invoke Claude to outline an approach before executing a task.

        Posts the plan as a progress note on the ticket and stores it
        in ``_current_task_plan`` so the execution prompt can reference it.
        Failures are logged but never block task execution.
        """
        plan_prompt = (
            "You are about to work on a task. Before starting, outline your approach.\n\n"
            f"Task: {task.title}\n"
            f"Description: {task.description or '(no description)'}\n\n"
            f"Assignment message:\n{msg.content}\n\n"
            "Respond with a concise numbered plan (3-7 steps). "
            "Each step should be one clear, self-contained action. "
            "You will execute these steps ONE AT A TIME — each invocation "
            "you will tackle only the first incomplete step, then stop. "
            "Do not start working — only plan."
        )
        try:
            plan_result = await self._provider.invoke(
                prompt=plan_prompt,
                model=self.model,
                allowed_tools="",   # text-only, no tools
                timeout=60,
            )
        except Exception as e:
            logger.warning("Planning phase failed for task %s: %s", task.id, e)
            return

        if plan_result.is_error:
            logger.warning(
                "Planning phase returned error for task %s: %s",
                task.id, plan_result.result_text[:200],
            )
            return

        plan_text = plan_result.result_text.strip()
        if not plan_text:
            return

        self._current_task_plan = plan_text
        if self._prompt_builder:
            self._prompt_builder._current_task_plan = plan_text
        await self._task_board.update_task(
            task.id,
            _agent_id=self.agent_id,
            progress_note=f"\U0001f4cb Plan:\n{plan_text}",
        )
        logger.info(
            "Agent %s posted plan for task %s (%d chars)",
            self.agent_id, task.id, len(plan_text),
        )

    async def process_message(self, msg: Message) -> list[Message]:
        prompt = await self._build_prompt(msg)

        # Get system prompt first -- may clear a stale session (prompt-hash mismatch)
        system_prompt = await self._get_system_prompt_if_first_call()

        # Fetch session_id AFTER prompt check (it may have been cleared above)
        session_id = None
        if self.use_session and self._session_store:
            session_id = self._session_store.get(self.agent_id)

        result = await self._provider.invoke(
            prompt=prompt,
            system_prompt=system_prompt,
            model=self.model,
            allowed_tools=self.allowed_tools,
            session_id=session_id,
            working_dir=self.working_dir,
            timeout=self.timeout,
            max_budget_usd=self.max_budget_usd,
            mcp_config_path=self.mcp_config_path,
            allowed_actions=self._allowed_actions,
        )

        # --- Timeout retry with health check ---
        if result.is_error and "[TIMEOUT]" in result.result_text:
            if self._task_cancelled:
                return []
            logger.warning("Agent %s timed out, running health check", self.agent_id)
            if await self._check_model_health():
                if self._task_cancelled:
                    return []
                logger.info("Agent %s health check passed, retrying", self.agent_id)
                result = await self._provider.invoke(
                    prompt=prompt, system_prompt=system_prompt,
                    model=self.model, allowed_tools=self.allowed_tools,
                    session_id=session_id, working_dir=self.working_dir,
                    timeout=self.timeout, max_budget_usd=self.max_budget_usd,
                    mcp_config_path=self.mcp_config_path,
                    allowed_actions=self._allowed_actions,
                )
                if result.is_error and "[TIMEOUT]" in result.result_text:
                    if self._task_cancelled:
                        return []
                    logger.warning("Agent %s second timeout, health-checking again", self.agent_id)
                    if await self._check_model_health():
                        # Model is fine — scope too large, escalate
                        logger.warning("Agent %s: scope too large, escalating to manny", self.agent_id)
                        return await self._escalate_scope_too_large(msg)
            # Non-timeout errors or connection failures fall through to normal handling

        # Retry on stale session: clear the session and invoke fresh
        if self._task_cancelled:
            return []
        if result.is_error and session_id and "No conversation found" in result.result_text:
            logger.warning(
                "Agent %s stale session %s, clearing and retrying fresh",
                self.agent_id, session_id,
            )
            if self._session_store:
                await self._session_store.set(self.agent_id, "")
            result = await self._provider.invoke(
                prompt=prompt,
                system_prompt=await self._get_system_prompt_if_first_call(),
                model=self.model,
                allowed_tools=self.allowed_tools,
                session_id=None,
                working_dir=self.working_dir,
                timeout=self.timeout,
                max_budget_usd=self.max_budget_usd,
                mcp_config_path=self.mcp_config_path,
                allowed_actions=self._allowed_actions,
            )

        if self.use_session and result.session_id and self._session_store:
            # Only store session IDs that match the current provider type.
            # Claude CLI returns UUIDs; API providers return psess_* IDs.
            # Storing the wrong type causes --resume failures on provider switch.
            is_cli = self._provider_name in ("claude-cli", None)
            is_cli_session = "-" in result.session_id and not result.session_id.startswith("psess_")
            if is_cli == is_cli_session:
                await self._session_store.set(self.agent_id, result.session_id)
                # Store prompt hash so we can detect prompt changes later
                prompt_for_hash = await self._build_full_system_prompt()
                await self._session_store.set_prompt_hash(
                    self.agent_id,
                    hashlib.md5(prompt_for_hash.encode()).hexdigest()[:12],
                )

        # Record subprocess stats (all agents, not just session-based ones)
        if self._session_store:
            auto_paused = await self._session_store.record_request(
                self.agent_id,
                duration_ms=result.duration_ms or 0,
                is_error=result.is_error,
                error_text=self.last_error if result.is_error else None,
                cost_usd=result.cost_usd or 0.0,
                input_tokens=result.input_tokens or 0,
                output_tokens=result.output_tokens or 0,
            )
            if auto_paused and self.use_session and self._broker:
                logger.warning(
                    "Session for %s auto-paused after consecutive errors",
                    self.agent_id,
                )
                await self._broker.broadcast_event({
                    "event_type": "session_status",
                    "data": {
                        "agent_id": self.agent_id,
                        "session_state": "paused",
                        "auto": True,
                    },
                })

        if result.is_error:
            logger.error("Agent %s Claude error: %s", self.agent_id, result.result_text)
            # Send errors to the user, not back to the sender agent.
            # Sending errors back to agents creates infinite ping-pong loops.
            return [Message(
                sender=self.agent_id,
                recipient="user",
                type=MessageType.CHAT,
                content=f"\u26a0\ufe0f {self.name} error: {result.result_text}",
                task_id=msg.task_id,
                parent_message_id=msg.id,
            )]

        # Validate actions -- retry once if agent used unknown action names
        validated_text = await self._validate_result_actions(result.result_text)

        return await self._parse_response(validated_text, msg)

    async def _check_model_health(self) -> bool:
        """Quick health check — invoke model with trivial prompt, short timeout."""
        try:
            result = await self._provider.invoke(
                prompt="Reply with exactly: OK",
                system_prompt="You are a health check. Reply with OK.",
                model=self.model,
                allowed_tools="",
                session_id=None,
                working_dir=self.working_dir,
                timeout=60,
            )
            return not result.is_error
        except Exception:
            return False

    async def _escalate_scope_too_large(self, msg: Message) -> list[Message]:
        """Escalate a timed-out task to Manny for decomposition."""
        task_info = ""
        if msg.task_id and self._task_board:
            task = self._task_board.get_task(msg.task_id)
            if task:
                task_info = (
                    f"\n\nOriginal task:\n"
                    f"- ID: {task.id}\n"
                    f"- Title: {task.title}\n"
                    f"- Assignee: {task.assignee}\n"
                    f"- Phase: {task.phase_id or 'none'}\n"
                    f"- Labels: {', '.join(task.labels) if task.labels else 'none'}\n"
                    f"- Description: {task.description[:500]}"
                )
                await self._task_board.update_task(
                    task.id,
                    status=TaskStatus.DONE,
                    outcome="rejected",
                    _agent_id=self.agent_id,
                    progress_note=(
                        "Task timed out twice. Health checks confirmed model connectivity is fine. "
                        "Scope is too large for a single invocation — escalating to Manny for decomposition."
                    ),
                )

        return [Message(
            sender=self.agent_id,
            recipient="manny",
            type=MessageType.SYSTEM,
            content=(
                f"SCOPE TOO LARGE: My task timed out twice and health checks confirm "
                f"the model connection is healthy. The task scope is too large for a single "
                f"model invocation. Please break this into 2-4 smaller focused sub-tasks "
                f"and assign them all to me ({self.agent_id}). "
                f"Use initial_status 'pending' so I can start immediately. "
                f"The original task has been marked as rejected.{task_info}"
            ),
            task_id=msg.task_id,
        )]

    # ------------------------------------------------------------------
    # Delegation to PromptBuilder
    # ------------------------------------------------------------------

    def _render_prompt_template(
        self,
        template: str,
        roster: str = "",
        team_roles: str = "",
        routing_guide: str = "",
    ) -> str:
        """Replace standard template variables in a prompt string."""
        if self._prompt_builder:
            return self._prompt_builder._render_prompt_template(
                template, roster, team_roles=team_roles, routing_guide=routing_guide,
            )
        # Fallback (before configure)
        prompt = template.replace("{team_roster}", roster)
        prompt = prompt.replace("{team_roles}", team_roles)
        prompt = prompt.replace("{routing_guide}", routing_guide)
        memory = ""
        if self._memory_manager:
            memory = self._memory_manager.get_combined_memory_sync(self.agent_id)
        prompt = prompt.replace("{memory}", memory or "No memory recorded yet.")
        return prompt

    def _get_known_actions(self) -> set[str]:
        """Return the set of valid action names for this agent."""
        if self._allowed_actions is not None:
            return self._allowed_actions
        if self._action_registry:
            return self._action_registry.get_all_action_names()
        return self.KNOWN_ACTIONS

    def _get_session_reminder(self) -> str:
        """Build a compact reminder for resumed sessions."""
        if self._prompt_builder:
            return self._prompt_builder._get_session_reminder()
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
        if self._prompt_builder:
            result = await self._prompt_builder._get_system_prompt_if_first_call()
            # Sync system_prompt back in case stateless mode re-rendered it
            self.system_prompt = self._prompt_builder.system_prompt
            return result
        # Fallback (before configure -- should not normally happen)
        return await self._build_full_system_prompt()

    async def _build_full_system_prompt(self) -> str:
        """Build the complete system prompt including memory."""
        if self._prompt_builder:
            # Sync current system_prompt into builder before building
            self._prompt_builder.system_prompt = self.system_prompt
            return await self._prompt_builder._build_full_system_prompt()
        prompt = self.system_prompt
        if self._memory_manager:
            memory = await self._memory_manager.get_combined_memory(self.agent_id)
            if memory:
                prompt += f"\n\n{memory}"
        return prompt

    async def _build_prompt(self, msg: Message) -> str:
        if self._prompt_builder:
            return await self._prompt_builder._build_prompt(msg)
        # Fallback (before configure)
        parts = [f"[Message from {msg.sender}]"]
        parts.append(f"Type: {msg.type.value}")
        if msg.task_id:
            parts.append(f"Task ID: {msg.task_id}")
        parts.append(f"\n{msg.content}")
        if msg.metadata:
            parts.append(f"\nMetadata: {msg.metadata}")
        return "\n".join(parts)

    def _build_task_context(self) -> str:
        """Build task board summary, emphasising this agent's tasks."""
        if self._prompt_builder:
            return self._prompt_builder._build_task_context()
        return ""

    @staticmethod
    def _compute_velocity(tasks) -> dict:
        """Compute per-agent velocity from completed tasks with timing data."""
        return PromptBuilder._compute_velocity(tasks)

    # ------------------------------------------------------------------
    # Delegation to ActionHandler
    # ------------------------------------------------------------------

    async def _parse_response(self, result_text: str, original_msg: Message) -> list[Message]:
        """Central response parser -- dispatches all actions via the registry."""
        if self._action_handler:
            # Keep current_task_id in sync
            self._action_handler.current_task_id = self.current_task_id
            return await self._action_handler._parse_response(result_text, original_msg)
        # Fallback (before configure)
        return [Message(
            sender=self.agent_id,
            recipient=original_msg.sender,
            type=MessageType.RESPONSE,
            content=result_text,
            task_id=original_msg.task_id,
            parent_message_id=original_msg.id,
        )]

    async def _validate_result_actions(self, result_text: str) -> str:
        """Check for unknown action types; retry once with correction."""
        if self._action_handler:
            return await self._action_handler._validate_result_actions(
                result_text,
                model=self.model,
                allowed_tools=self.allowed_tools,
                working_dir=self.working_dir,
                timeout=self.timeout,
                mcp_config_path=self.mcp_config_path,
                get_session_reminder_fn=self._get_session_reminder,
            )
        return result_text

    def _extract_actions(self, text: str) -> list[dict]:
        """Extract action blocks from Claude output."""
        if self._action_handler:
            return self._action_handler._extract_actions(text)
        return []

    def _normalize_action(self, raw: dict) -> dict:
        """Normalize common wrong key/value patterns in a parsed action."""
        if self._action_handler:
            return self._action_handler._normalize_action(raw)
        return raw

    @staticmethod
    def _sanitize_for_user(text: str) -> str:
        """Strip action blocks and bare JSON from text before showing to user."""
        return ActionHandler._sanitize_for_user(text)

    async def _handle_common_actions(self, actions: list[dict]) -> None:
        """Process actions that modify state without producing messages."""
        if self._action_handler:
            self._action_handler.current_task_id = self.current_task_id
            await self._action_handler._handle_common_actions(actions)
            return
        # No-op fallback
        return

    async def _handle_memory_update(self, action: dict):
        if self._action_handler:
            await self._action_handler._handle_memory_update(action)

    async def _handle_write_document(self, action: dict):
        if self._action_handler:
            await self._action_handler._handle_write_document(action)

    @staticmethod
    def _infer_doc_category(title: str) -> str:
        """Infer document category from title/path when not explicitly provided."""
        from core.actions.base import infer_doc_category
        return infer_doc_category(title)

    async def _handle_update_document(self, action: dict):
        if self._action_handler:
            await self._action_handler._handle_update_document(action)

    async def _handle_resolve_comments(
        self, action: dict, edited_doc_ids: set[str] | None = None,
    ):
        if self._action_handler:
            self._action_handler.current_task_id = self.current_task_id
            await self._action_handler._handle_resolve_comments(action, edited_doc_ids)

    async def _handle_update_task(self, action: dict):
        if self._action_handler:
            await self._action_handler._handle_update_task(action)

    async def _handle_start_conversation(self, action: dict):
        if self._action_handler:
            await self._action_handler._handle_start_conversation(action)

    async def _handle_end_conversation(self, action: dict):
        if self._action_handler:
            await self._action_handler._handle_end_conversation(action)

    # ------------------------------------------------------------------
    # Class-level dicts kept for backward compatibility (read-only access)
    # ------------------------------------------------------------------

    _ACTION_KEY_MAP = ActionHandler._ACTION_KEY_MAP
    _ACTION_NAME_MAP = ActionHandler._ACTION_NAME_MAP
    _FIELD_MAP = ActionHandler._FIELD_MAP

    # ------------------------------------------------------------------
    # Remaining Agent methods (not extracted)
    # ------------------------------------------------------------------

    def update_team_roster(self, roster_text: str, team_roles: str = "", routing_guide: str = ""):
        """Re-render system prompt with updated team roster."""
        self._team_roster = roster_text
        self._team_roles = team_roles
        self._routing_guide = routing_guide
        self.system_prompt = self._render_prompt_template(
            self._prompt_template, roster_text,
            team_roles=team_roles, routing_guide=routing_guide,
        )
        # Sync into PromptBuilder
        if self._prompt_builder:
            self._prompt_builder.system_prompt = self.system_prompt
            self._prompt_builder._team_roster = roster_text
            self._prompt_builder._team_roles = team_roles
            self._prompt_builder._routing_guide = routing_guide
            self._prompt_builder._prompt_template = self._prompt_template

    # ------------------------------------------------------------------
    # Workingbox helpers (task message persistence)
    # ------------------------------------------------------------------

    def _move_task_to_workingbox(self, msg: Message):
        """Move a TASK message from inbox to workingbox (single-task invariant)."""
        msg_filename = f"{msg.id}.json"
        inbox_file = self.inbox_dir / msg_filename
        wb_file = self.workingbox_dir / msg_filename

        # Already in workingbox (restart recovery path)
        if wb_file.exists():
            return

        # Clear stale workingbox contents (enforce single-task invariant)
        for stale in self.workingbox_dir.glob("*.json"):
            try:
                stale.rename(self.outbox_dir / stale.name)
                logger.warning(
                    "Cleared stale workingbox file %s for agent %s",
                    stale.name, self.agent_id,
                )
            except Exception:
                logger.exception("Failed to clear stale workingbox file %s", stale.name)

        if inbox_file.exists():
            try:
                inbox_file.rename(wb_file)
                logger.info(
                    "Moved task %s inbox → workingbox for agent %s",
                    msg.task_id, self.agent_id,
                )
            except Exception:
                logger.exception("Failed to move message %s to workingbox", msg.id)
        else:
            # Not in inbox (e.g. delivered directly via queue). Write to workingbox.
            try:
                msg.to_file(self.workingbox_dir)
                logger.info(
                    "Wrote task %s to workingbox for agent %s (no inbox file)",
                    msg.task_id, self.agent_id,
                )
            except Exception:
                logger.exception("Failed to write message %s to workingbox", msg.id)

    def _complete_workingbox_task(self, msg: Message):
        """Move completed task message from workingbox to outbox."""
        wb_file = self.workingbox_dir / f"{msg.id}.json"
        if wb_file.exists():
            try:
                wb_file.rename(self.outbox_dir / wb_file.name)
                logger.info(
                    "Moved task %s workingbox → outbox for agent %s",
                    msg.task_id, self.agent_id,
                )
            except Exception:
                logger.exception("Failed to move message %s to outbox", msg.id)

    def _move_workingbox_to_inbox(self, msg: Message):
        """Move task back from workingbox to inbox (returned to pending for retry)."""
        msg_filename = f"{msg.id}.json"
        wb_file = self.workingbox_dir / msg_filename
        inbox_file = self.inbox_dir / msg_filename
        if wb_file.exists():
            try:
                wb_file.rename(inbox_file)
                logger.info(
                    "Moved task %s workingbox → inbox for agent %s (retry)",
                    msg.task_id, self.agent_id,
                )
            except Exception:
                logger.exception("Failed to move message %s back to inbox", msg.id)

    def _move_inbox_to_outbox(self, msg: Message):
        """Move stale inbox task message directly to outbox (task already completed)."""
        inbox_file = self.inbox_dir / f"{msg.id}.json"
        if inbox_file.exists():
            try:
                inbox_file.rename(self.outbox_dir / inbox_file.name)
                logger.info(
                    "Moved stale task %s inbox → outbox for agent %s",
                    msg.task_id, self.agent_id,
                )
            except Exception:
                logger.exception("Failed to move stale message %s to outbox", msg.id)

    async def _broadcast_status(self):
        if self._broker:
            data = {
                "agent_id": self.agent_id,
                "status": self.status.value,
                "current_task_id": self.current_task_id,
            }
            if self.activity:
                data["activity"] = self.activity
            if self.last_error:
                data["last_error"] = self.last_error
            await self._broker.broadcast_event({
                "event_type": "agent_status",
                "data": data,
            })

    def to_info_dict(self) -> dict:
        info = {
            "id": self.agent_id,
            "name": self.name,
            "role": self.role,
            "status": self.status.value,
            "current_task_id": self.current_task_id,
            "messages_processed": self.messages_processed,
            "model": self.model,
        }
        if self.activity:
            info["activity"] = self.activity
        if self.last_error:
            info["last_error"] = self.last_error
        return info
