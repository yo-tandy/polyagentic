from __future__ import annotations

import asyncio
import hashlib
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


class Agent:
    # Base set of valid action names. Subclasses extend via _get_known_actions().
    KNOWN_ACTIONS = {
        "respond_to_user", "delegate", "update_task", "update_memory",
        "write_document", "update_document", "resolve_comments",
        "start_conversation", "end_conversation",
    }

    # Max "other team tasks" shown in prompt context.
    # None = unlimited (for management agents that need full board visibility).
    max_task_context_items: int | None = MAX_TASK_CONTEXT_ITEMS

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

        # Prompt template + team roster (used by update_team_roster)
        self._prompt_template: str = system_prompt
        self._team_roster: str = ""
        self._team_roles: str = ""
        self._routing_guide: str = ""

        self.status = AgentStatus.OFFLINE
        self.current_task_id: str | None = None
        self.messages_processed = 0
        self.last_error: str | None = None
        self.message_queue: asyncio.Queue[Message] = asyncio.Queue()

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

    @property
    def inbox_dir(self) -> Path:
        return self.messages_dir / self.agent_id / "inbox"

    @property
    def outbox_dir(self) -> Path:
        return self.messages_dir / self.agent_id / "outbox"

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

    def set_provider(self, provider: BaseProvider) -> None:
        """Swap the AI model provider (e.g. Claude CLI → OpenAI API)."""
        self._provider = provider

    async def start(self):
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        self.status = AgentStatus.IDLE
        self._running = True
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
        logger.info("Agent %s stopped", self.name)

    async def _message_loop(self):
        while self._running:
            try:
                msg = await asyncio.wait_for(
                    self.message_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                # While idle, reflect session-paused state in status
                if (self.use_session and self._session_store
                        and self._session_store.is_paused(self.agent_id)):
                    if self.status != AgentStatus.SESSION_PAUSED:
                        self.status = AgentStatus.SESSION_PAUSED
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
            # (preserves accumulated stats — only the session ID is cleared)
            if (self.use_session and self._session_store
                    and self._session_store.is_killed(self.agent_id)):
                await self._session_store.invalidate_session(self.agent_id)

            self.status = AgentStatus.WORKING
            await self._broadcast_status()

            # Auto-transition: mark task in_progress when agent starts working
            if msg.task_id and msg.type == MessageType.TASK and self._task_board:
                task = self._task_board.get_task(msg.task_id)
                if task and task.status in (TaskStatus.DRAFT, TaskStatus.PENDING, TaskStatus.PAUSED):
                    await self._task_board.update_task(
                        msg.task_id,
                        status=TaskStatus.IN_PROGRESS,
                        _agent_id=self.agent_id,
                        progress_note="Agent started working on this task",
                    )

                    # Planning phase: ask agent to outline its approach before execution
                    await self._run_planning_phase(task, msg)

                self.current_task_id = msg.task_id

            try:
                responses = await self.process_message(msg)

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
                # Delegation is NOT a reason to skip — delegated subtasks are
                # tracked independently; the delegating agent's own task is done.
                if (msg.type == MessageType.TASK and msg.task_id
                        and self._task_board):
                    task = self._task_board.get_task(msg.task_id)
                    if task and task.status == TaskStatus.IN_PROGRESS:
                        # Check if the response is an error (timeout, auth, etc.)
                        # If so, send the task back to PENDING for retry instead
                        # of marking it DONE with an error as the "summary".
                        is_error_response = any(
                            r.content and r.content.startswith("⚠️") for r in responses
                        )
                        if is_error_response:
                            error_text = next(
                                (r.content for r in responses if r.content and r.content.startswith("⚠️")), ""
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

                # Memory enforcement: remind agent to update memory after task completion
                if msg.task_id and self._task_board:
                    task = self._task_board.get_task(msg.task_id)
                    if task and task.status in (TaskStatus.REVIEW, TaskStatus.DONE):
                        # Check if agent already emitted an update_memory action
                        has_memory_update = any(
                            (r.metadata or {}).get("command") == "review_feedback" for r in responses
                        ) is False  # not a reviewer-feedback flow
                        # Simple heuristic: check if any response text mentions update_memory
                        raw_text = " ".join(r.content for r in responses)
                        if "update_memory" not in raw_text and task.assignee == self.agent_id:
                            logger.info(
                                "Agent %s completed task %s without memory update — sending reminder",
                                self.agent_id, msg.task_id,
                            )

                for response in responses:
                    await self._broker.deliver(response)
            except Exception as exc:
                logger.exception("Agent %s error processing message %s", self.agent_id, msg.id)
                self.last_error = f"{type(exc).__name__}: {exc}"
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

                await asyncio.sleep(2)
                # If session is paused, recover to SESSION_PAUSED instead of staying in ERROR
                if (self.use_session and self._session_store
                        and self._session_store.is_paused(self.agent_id)):
                    self.status = AgentStatus.SESSION_PAUSED
                    await self._broadcast_status()
            finally:
                self.current_task_id = None
                self._current_task_plan = None
                self.messages_processed += 1
                if self.status not in (AgentStatus.ERROR, AgentStatus.SESSION_PAUSED):
                    self.status = AgentStatus.IDLE
                    await self._broadcast_status()

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
            "Each step should be one clear action. Do not start working — only plan."
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

        # Get system prompt first — may clear a stale session (prompt-hash mismatch)
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
        )

        # Retry on stale session: clear the session and invoke fresh
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
            )

        if self.use_session and result.session_id and self._session_store:
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
                content=f"⚠️ {self.name} error: {result.result_text}",
                task_id=msg.task_id,
                parent_message_id=msg.id,
            )]

        # Validate actions — retry once if agent used unknown action names
        validated_text = await self._validate_result_actions(result.result_text)

        return await self._parse_response(validated_text, msg)

    async def _validate_result_actions(self, result_text: str) -> str:
        """Check for unknown action types in the result; retry once with correction.

        If the agent emitted action blocks with unrecognized names (after
        normalization), send a correction prompt asking it to re-emit with
        valid action names.  Returns corrected text, or original if no
        unknown actions or retry fails.
        """
        actions = self._extract_actions(result_text)
        if not actions:
            return result_text

        known = self._get_known_actions()
        unknown = [a.get("action") for a in actions if a.get("action") not in known]
        if not unknown:
            return result_text

        logger.warning(
            "Agent %s used unknown action(s): %s — requesting correction",
            self.agent_id, unknown,
        )

        valid_list = ", ".join(sorted(known))
        correction = (
            f"Your previous response contained unrecognized action(s): {', '.join(unknown)}. "
            f"These are NOT valid actions and will be ignored.\n"
            f"Valid actions are: {valid_list}.\n"
            f"Please re-emit your response using only valid action names from the list above."
        )

        session_id = self._session_store.get(self.agent_id) if self._session_store else None
        retry = await self._provider.invoke(
            prompt=correction,
            system_prompt=self._get_session_reminder(),
            model=self.model,
            allowed_tools=self.allowed_tools,
            session_id=session_id,
            working_dir=self.working_dir,
            timeout=self.timeout,
        )

        # Record retry stats
        if self._session_store:
            await self._session_store.record_request(
                self.agent_id,
                duration_ms=retry.duration_ms or 0,
                is_error=retry.is_error,
                cost_usd=retry.cost_usd or 0.0,
                input_tokens=retry.input_tokens or 0,
                output_tokens=retry.output_tokens or 0,
            )

        if retry.is_error:
            logger.warning(
                "Agent %s action validation retry failed: %s",
                self.agent_id, retry.result_text[:200],
            )
            return result_text  # Keep original if retry fails

        logger.info("Agent %s action validation retry succeeded", self.agent_id)
        return retry.result_text

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
            memory = self._memory_manager.get_combined_memory_sync(self.agent_id)
        prompt = prompt.replace("{memory}", memory or "No memory recorded yet.")
        return prompt

    def _get_known_actions(self) -> set[str]:
        """Return the set of valid action names for this agent.

        Uses agent-side permissions (``_allowed_actions``) when set;
        falls back to the action registry or class-level ``KNOWN_ACTIONS``.
        """
        if self._allowed_actions is not None:
            return self._allowed_actions
        if self._action_registry:
            return self._action_registry.get_all_action_names()
        return self.KNOWN_ACTIONS

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
        if self.use_session and self._session_store and self._session_store.get(self.agent_id):
            # Check if system prompt has changed since session was created
            current_hash = hashlib.md5(prompt.encode()).hexdigest()[:12]
            stored_hash = self._session_store.get_prompt_hash(self.agent_id)
            if stored_hash and current_hash == stored_hash:
                # Prompt unchanged — append a compact reminder to the resumed session
                # (subprocess_manager will use --append-system-prompt)
                return self._get_session_reminder()
            # Prompt changed — invalidate stale session so Claude gets the new prompt
            # (preserves accumulated stats — only the session ID is cleared)
            logger.info(
                "Agent %s prompt changed (hash %s → %s), invalidating session",
                self.agent_id, stored_hash, current_hash,
            )
            await self._session_store.invalidate_session(self.agent_id)
        return prompt

    def update_team_roster(self, roster_text: str, team_roles: str = "", routing_guide: str = ""):
        """Re-render system prompt with updated team roster.

        This is the canonical implementation — subclasses no longer need
        their own version.
        """
        self._team_roster = roster_text
        self._team_roles = team_roles
        self._routing_guide = routing_guide
        self.system_prompt = self._render_prompt_template(
            self._prompt_template, roster_text,
            team_roles=team_roles, routing_guide=routing_guide,
        )

    async def _build_full_system_prompt(self) -> str:
        """Build the complete system prompt including memory."""
        prompt = self.system_prompt
        if self._memory_manager:
            memory = await self._memory_manager.get_combined_memory(self.agent_id)
            if memory:
                prompt += f"\n\n{memory}"
        return prompt

    async def _build_prompt(self, msg: Message) -> str:
        parts = []

        # 1. Memory context (for resumed sessions — first-call memory is in system prompt)
        if self._memory_manager and self.use_session:
            session_id = self._session_store.get(self.agent_id) if self._session_store else None
            if session_id:  # Resumed session — memory not in system prompt
                memory = await self._memory_manager.get_combined_memory(self.agent_id)
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

        # 4. Original message
        parts.append(f"[Message from {msg.sender}]")
        parts.append(f"Type: {msg.type.value}")
        if msg.task_id:
            parts.append(f"Task ID: {msg.task_id}")
        parts.append(f"\n{msg.content}")

        if msg.metadata:
            parts.append(f"\nMetadata: {msg.metadata}")

        return "\n".join(parts)

    def _build_task_context(self) -> str:
        """Build task board summary, emphasising this agent's tasks."""
        tasks = self._task_board.get_all_tasks()
        if not tasks:
            return ""

        my_tasks = self._task_board.get_tasks_for_agent(self.agent_id)
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
                review_marker = " [NEEDS YOUR REVIEW]" if (t.status == TaskStatus.REVIEW and t.reviewer == self.agent_id) else ""
                lines.append(f"  - [P{t.priority}] [{t.status.value}] {t.title} (id: {t.id}){review_marker}")

        if other_tasks:
            limit = self.max_task_context_items
            if limit is None:
                shown = other_tasks
            else:
                shown = other_tasks[:max(0, limit - len(my_tasks))]
            lines.append("\nOTHER TEAM TASKS:")
            for t in shown:
                assignee = t.assignee or "unassigned"
                lines.append(f"  - [P{t.priority}] [{t.status.value}] {t.title} (assignee: {assignee})")
            if len(other_tasks) > len(shown):
                lines.append(f"  ... and {len(other_tasks) - len(shown)} more")

        return "\n".join(lines)

    async def _parse_response(self, result_text: str, original_msg: Message) -> list[Message]:
        """Central response parser — dispatches all actions via the registry.

        Replaces per-agent ``_parse_response`` overrides and the old
        ``_handle_common_actions`` method.  Every action (messaging,
        documents, memory, git, etc.) is handled through the registry.
        """
        actions = self._extract_actions(result_text)

        if not actions:
            # No action blocks found — sanitize and forward raw text
            cleaned = self._sanitize_for_user(result_text)
            if not cleaned.strip():
                cleaned = result_text  # keep raw if sanitization removes everything
            if cleaned.strip():
                return [Message(
                    sender=self.agent_id,
                    recipient=original_msg.sender,
                    type=MessageType.RESPONSE,
                    content=cleaned,
                    task_id=original_msg.task_id,
                    parent_message_id=original_msg.id,
                )]
            return []

        # Dispatch through the action registry
        if self._action_registry:
            messages = await self._action_registry.execute_all(
                self, actions, original_msg,
            )
        else:
            # Fallback: no registry — just return sanitized text
            logger.warning(
                "Agent %s has no action registry — cannot process actions",
                self.agent_id,
            )
            messages = []

        # Fallback: if actions were processed but no messages generated,
        # send sanitized text (if any human-readable content remains)
        if not messages:
            cleaned = self._sanitize_for_user(result_text)
            if cleaned.strip():
                messages.append(Message(
                    sender=self.agent_id,
                    recipient=original_msg.sender,
                    type=MessageType.RESPONSE,
                    content=cleaned,
                    task_id=original_msg.task_id,
                    parent_message_id=original_msg.id,
                ))

        return messages

    # ── Shared action extraction (used by subclasses) ──

    # Maps wrong top-level keys to correct ones
    _ACTION_KEY_MAP = {"tool": "action"}

    # Maps wrong action names to correct ones
    _ACTION_NAME_MAP = {
        "save_to_memory": "update_memory",
        "save_memory": "update_memory",
        "memory": "update_memory",
        "respond": "respond_to_user",
        "reply": "respond_to_user",
        "send_message": "respond_to_user",
        "assign": "delegate",
        "assign_task": "delegate",
        "resolve_comment": "resolve_comments",
        "conversation_summary": "end_conversation",
        "close_conversation": "end_conversation",
        "finish_conversation": "end_conversation",
        "summarize_conversation": "end_conversation",
    }

    # Maps wrong field names to correct ones, per action type
    _FIELD_MAP = {
        "delegate": {
            "message": "task_description",
            "description": "task_description",
            "content": "task_description",
            "target": "to",
            "agent": "to",
            "title": "task_title",
        },
        "respond_to_user": {
            "content": "message",
            "text": "message",
            "response": "message",
        },
        "update_memory": {
            "value": "content",
            "text": "content",
            "key": "memory_type",
        },
        "write_document": {
            "path": "title",
            "name": "title",
            "filename": "title",
            "type": "category",
        },
        "resolve_comments": {
            "comments": "resolutions",
            "results": "resolutions",
        },
    }

    def _normalize_action(self, raw: dict) -> dict:
        """Normalize common wrong key/value patterns in a parsed action."""
        result = dict(raw)

        # Remap top-level keys (e.g. "tool" → "action")
        for wrong, right in self._ACTION_KEY_MAP.items():
            if wrong in result and right not in result:
                result[right] = result.pop(wrong)

        # Remap action names (e.g. "save_to_memory" → "update_memory")
        action_name = result.get("action", "")
        if action_name in self._ACTION_NAME_MAP:
            result["action"] = self._ACTION_NAME_MAP[action_name]

        # Remap field names per action type
        action_type = result.get("action", "")
        field_map = self._FIELD_MAP.get(action_type, {})
        for wrong, right in field_map.items():
            if wrong in result and right not in result:
                result[right] = result.pop(wrong)

        # Normalize agent references: lowercase "to" field
        if "to" in result and isinstance(result["to"], str):
            result["to"] = result["to"].lower().replace(" ", "_")

        return result

    def _extract_actions(self, text: str) -> list[dict]:
        """Extract action blocks from Claude output.

        Primary: looks for ```action ... ``` fenced blocks.
        Fallback: recovers bare JSON objects containing "action" or "tool" keys.
        All parsed actions are normalized via _normalize_action().
        """
        actions = []

        # Primary: fenced action blocks
        pattern = r"```action\s*(.*?)\s*```"
        for match in re.findall(pattern, text, re.DOTALL):
            try:
                parsed = json.loads(match.strip())
                actions.append(self._normalize_action(parsed))
            except json.JSONDecodeError:
                logger.warning("Failed to parse action block: %s", match[:100])

        if actions:
            return actions

        # Fallback: bare JSON objects with "action" or "tool" key
        bare_pattern = r'(?<!`)\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
        for match in re.findall(bare_pattern, text):
            try:
                parsed = json.loads(match.strip())
                if isinstance(parsed, dict) and ("action" in parsed or "tool" in parsed):
                    actions.append(self._normalize_action(parsed))
            except (json.JSONDecodeError, ValueError):
                continue

        if actions:
            logger.warning(
                "Agent %s: recovered %d bare JSON action(s) (no fenced blocks found)",
                self.agent_id, len(actions),
            )

        return actions

    @staticmethod
    def _sanitize_for_user(text: str) -> str:
        """Strip action blocks and bare JSON from text before showing to user."""
        # Remove fenced code blocks containing JSON
        cleaned = re.sub(r'```\w*\s*\{.*?\}\s*```', '', text, flags=re.DOTALL)
        # Remove bare JSON objects
        cleaned = re.sub(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', '', cleaned)
        # Remove [Saving to memory: ...] annotations
        cleaned = re.sub(r'\[.*?(?:memory|saving|delegat).*?\]', '', cleaned, flags=re.IGNORECASE)
        # Collapse excessive whitespace
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        return cleaned.strip()

    async def _handle_common_actions(self, actions: list[dict]) -> None:
        """Process actions that modify state without producing messages.

        .. deprecated::
            Kept for backward compatibility with any code that still
            calls it directly.  All action handling now goes through
            the :class:`ActionRegistry` in ``_parse_response``.
        """
        if self._action_registry:
            from core.actions.base import ActionContext
            ctx = ActionContext()
            for action in actions:
                if action.get("action") == "update_document" and action.get("doc_id"):
                    ctx.edited_doc_ids.add(action["doc_id"])
            for action in actions:
                await self._action_registry.execute(
                    self, action, Message(sender="system", recipient=self.agent_id,
                                         type=MessageType.SYSTEM, content=""), ctx,
                )
            if ctx.kb_changed and self._broker:
                await self._broker.broadcast_event({
                    "event_type": "knowledge_updated", "data": {},
                })
            return

        # Legacy fallback (no registry)
        kb_changed = False
        edited_doc_ids: set[str] = set()
        for action in actions:
            if action.get("action") == "update_document" and action.get("doc_id"):
                edited_doc_ids.add(action["doc_id"])

        for action in actions:
            action_type = action.get("action")

            if action_type == "update_memory":
                await self._handle_memory_update(action)

            elif action_type == "write_document":
                await self._handle_write_document(action)
                kb_changed = True

            elif action_type == "update_document":
                await self._handle_update_document(action)
                kb_changed = True

            elif action_type == "resolve_comments":
                await self._handle_resolve_comments(action, edited_doc_ids)

            elif action_type == "update_task":
                await self._handle_update_task(action)

            elif action_type == "start_conversation":
                await self._handle_start_conversation(action)

            elif action_type == "end_conversation":
                await self._handle_end_conversation(action)

        # Broadcast KB update so frontend auto-refreshes
        if kb_changed and self._broker:
            await self._broker.broadcast_event({
                "event_type": "knowledge_updated",
                "data": {},
            })

    async def _handle_memory_update(self, action: dict):
        if not self._memory_manager:
            return
        memory_type = action.get("memory_type", "")
        content = action.get("content", "")
        if not content:
            return
        if memory_type == "personality":
            await self._memory_manager.update_personality_memory(self.agent_id, content)
        elif memory_type == "project":
            await self._memory_manager.update_project_memory(self.agent_id, content)
        else:
            logger.warning("Unknown memory_type '%s' from %s", memory_type, self.agent_id)

    async def _handle_write_document(self, action: dict):
        if not self._knowledge_base:
            return
        title = action.get("title", "")
        category = action.get("category", "") or self._infer_doc_category(title)
        content = action.get("content", "")
        if not title or not content:
            logger.warning(
                "Agent %s write_document missing fields: title=%s, category=%s, content_len=%d",
                self.agent_id, bool(title), bool(category), len(content),
            )
            return
        if not category:
            category = "specs"  # default for documents without explicit category
        try:
            await self._knowledge_base.add_document(
                title=title, category=category,
                content=content, created_by=self.agent_id,
            )
        except ValueError as e:
            logger.warning("KB write_document error from %s: %s", self.agent_id, e)

    @staticmethod
    def _infer_doc_category(title: str) -> str:
        """Infer document category from title/path when not explicitly provided."""
        t = title.lower()
        if any(k in t for k in ("spec", "requirement", "product")):
            return "specs"
        if any(k in t for k in ("arch", "design", "system")):
            return "architecture"
        if any(k in t for k in ("plan", "roadmap", "milestone")):
            return "planning"
        return ""

    async def _handle_update_document(self, action: dict):
        if not self._knowledge_base:
            return
        doc_id = action.get("doc_id", "")
        content = action.get("content", "")
        if not doc_id or not content:
            return
        await self._knowledge_base.update_document(
            doc_id=doc_id, content=content, updated_by=self.agent_id,
        )

    async def _handle_resolve_comments(
        self, action: dict, edited_doc_ids: set[str] | None = None,
    ):
        """Agent resolves one or more comments on a document."""
        if not self._knowledge_base:
            return
        doc_id = action.get("doc_id", "")
        resolutions = action.get("resolutions", [])
        if not doc_id or not resolutions:
            logger.warning("Agent %s resolve_comments missing fields", self.agent_id)
            return

        edit_verified = bool(edited_doc_ids and doc_id in edited_doc_ids)
        if not edit_verified:
            logger.warning(
                "Agent %s resolved comments on %s WITHOUT editing the document",
                self.agent_id, doc_id,
            )

        resolved = await self._knowledge_base.resolve_comments(
            doc_id, resolutions, edit_verified=edit_verified,
        )
        if resolved and self._broker:
            logger.info(
                "Agent %s resolved %d comment(s) on %s (edit_verified=%s)",
                self.agent_id, len(resolved), doc_id, edit_verified,
            )
            await self._broker.broadcast_event({
                "event_type": "comments_updated",
                "data": {"doc_id": doc_id},
            })

        # Auto-complete the current task if all assigned comments are resolved
        if resolved and self.current_task_id and self._task_board:
            all_comments = await self._knowledge_base.get_comments(doc_id)
            remaining = [
                c for c in all_comments
                if c["status"] == "open" and c.get("assigned_to") == self.agent_id
            ]
            if not remaining:
                verified_str = "with verified edits" if edit_verified else "WITHOUT document edits (unverified)"
                await self._task_board.update_task(
                    self.current_task_id,
                    status="done",
                    _agent_id=self.agent_id,
                    completion_summary=(
                        f"Resolved {len(resolved)} comment(s) on \"{doc_id}\" {verified_str}."
                    ),
                )
                logger.info(
                    "Auto-completed task %s after resolving all comments",
                    self.current_task_id,
                )

    async def _handle_update_task(self, action: dict):
        if not self._task_board:
            return
        task_id = action.get("task_id")
        if not task_id:
            return
        updates = {"_agent_id": self.agent_id}
        for key in ("status", "assignee", "role", "priority", "reviewer",
                     "progress_note", "completion_summary", "review_output",
                     "paused_summary", "labels", "outcome"):
            if key in action:
                updates[key] = action[key]
        await self._task_board.update_task(task_id, **updates)

    async def _handle_start_conversation(self, action: dict):
        """Agent requests a direct conversation with the user."""
        if not self._conversation_manager:
            logger.warning("No conversation_manager for %s", self.agent_id)
            return
        goals = action.get("goals", [])
        title = action.get("title", "Conversation")
        conv = await self._conversation_manager.start(self.agent_id, goals, title)

        # Send CONVERSATION message to self with the conversation context
        if self._broker:
            msg = Message(
                sender="system",
                recipient=self.agent_id,
                type=MessageType.CONVERSATION,
                content=f"Conversation started: {title}. Goals: {', '.join(goals)}",
                metadata={"conversation_id": conv["id"]},
            )
            await self._broker.deliver(msg)

    async def _handle_end_conversation(self, action: dict):
        """Agent ends a direct conversation with the user."""
        if not self._conversation_manager:
            return
        summary = action.get("summary", "")
        conv = await self._conversation_manager.close_by_agent(self.agent_id)
        if not conv:
            return

        # Save summary to knowledge base
        if summary and self._knowledge_base:
            try:
                await self._knowledge_base.add_document(
                    title=conv.get("title", "Conversation Summary"),
                    category="specs",
                    content=summary,
                    created_by=self.agent_id,
                )
                if self._broker:
                    await self._broker.broadcast_event({
                        "event_type": "knowledge_updated",
                        "data": {},
                    })
            except Exception:
                logger.exception("Failed to save conversation summary to KB")

        # Send summary to the user-facing agent so they know what was discussed
        if summary and self._broker:
            summary_msg = Message(
                sender=self.agent_id,
                recipient=self._user_facing_agent,
                type=MessageType.RESPONSE,
                content=(
                    f"Conversation completed: '{conv.get('title', 'Conversation')}'\n\n"
                    f"Summary:\n{summary}"
                ),
                metadata={"conversation_summary": True},
            )
            await self._broker.deliver(summary_msg)

    async def _broadcast_status(self):
        if self._broker:
            data = {
                "agent_id": self.agent_id,
                "status": self.status.value,
                "current_task_id": self.current_task_id,
            }
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
        if self.last_error:
            info["last_error"] = self.last_error
        return info
