from __future__ import annotations

import asyncio
import json
import logging
import re
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from core.message import Message, MessageType
from core.subprocess_manager import SubprocessManager
from core.session_store import SessionStore
from config import DEFAULT_MODEL, CLAUDE_ALLOWED_TOOLS_DEV

from core.task import TaskStatus

if TYPE_CHECKING:
    from core.message_broker import MessageBroker
    from core.task_board import TaskBoard
    from core.memory_manager import MemoryManager
    from core.knowledge_base import KnowledgeBase

logger = logging.getLogger(__name__)

MAX_TASK_CONTEXT_ITEMS = 20


class AgentStatus(str, Enum):
    IDLE = "idle"
    WORKING = "working"
    WAITING = "waiting"
    ERROR = "error"
    OFFLINE = "offline"


class Agent:
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

        self.status = AgentStatus.OFFLINE
        self.current_task_id: str | None = None
        self.messages_processed = 0
        self.message_queue: asyncio.Queue[Message] = asyncio.Queue()

        self._subprocess = SubprocessManager()
        self._session_store: SessionStore | None = None
        self._broker: MessageBroker | None = None
        self._task_board: TaskBoard | None = None
        self._memory_manager: MemoryManager | None = None
        self._knowledge_base: KnowledgeBase | None = None
        self._loop_task: asyncio.Task | None = None
        self._running = False

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
    ):
        self._session_store = session_store
        self._broker = broker
        self._task_board = task_board
        self._memory_manager = memory_manager
        self._knowledge_base = knowledge_base

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
                continue
            except asyncio.CancelledError:
                break

            self.status = AgentStatus.WORKING
            await self._broadcast_status()

            # Auto-transition: mark task in_progress when agent starts working
            if msg.task_id and msg.type == MessageType.TASK and self._task_board:
                task = self._task_board.get_task(msg.task_id)
                if task and task.status in (TaskStatus.PENDING, TaskStatus.PAUSED):
                    self._task_board.update_task(
                        msg.task_id,
                        status=TaskStatus.IN_PROGRESS,
                        _agent_id=self.agent_id,
                        progress_note="Agent started working on this task",
                    )
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
                        self._task_board.update_task(
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

                # Memory enforcement: remind agent to update memory after task completion
                if msg.task_id and self._task_board:
                    task = self._task_board.get_task(msg.task_id)
                    if task and task.status in (TaskStatus.REVIEW, TaskStatus.DONE):
                        # Check if agent already emitted an update_memory action
                        has_memory_update = any(
                            r.metadata.get("command") == "review_feedback" for r in responses
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
            except Exception:
                logger.exception("Agent %s error processing message %s", self.agent_id, msg.id)
                self.status = AgentStatus.ERROR
                await self._broadcast_status()
                await asyncio.sleep(2)
            finally:
                self.current_task_id = None
                self.messages_processed += 1
                if self.status != AgentStatus.ERROR:
                    self.status = AgentStatus.IDLE
                    await self._broadcast_status()

    async def process_message(self, msg: Message) -> list[Message]:
        prompt = self._build_prompt(msg)

        # Only use session persistence if enabled for this agent
        session_id = None
        if self.use_session and self._session_store:
            session_id = self._session_store.get(self.agent_id)

        result = await self._subprocess.invoke(
            prompt=prompt,
            system_prompt=self._get_system_prompt_if_first_call(),
            model=self.model,
            allowed_tools=self.allowed_tools,
            session_id=session_id,
            working_dir=self.working_dir,
            timeout=self.timeout,
            max_budget_usd=self.max_budget_usd,
        )

        if self.use_session and result.session_id and self._session_store:
            self._session_store.set(self.agent_id, result.session_id)

        if result.is_error:
            logger.error("Agent %s Claude error: %s", self.agent_id, result.result_text)
            return [Message(
                sender=self.agent_id,
                recipient=msg.sender,
                type=MessageType.RESPONSE,
                content=f"Error processing request: {result.result_text}",
                task_id=msg.task_id,
                parent_message_id=msg.id,
            )]

        return await self._parse_response(result.result_text, msg)

    def _get_system_prompt_if_first_call(self) -> str | None:
        if self.use_session and self._session_store and self._session_store.get(self.agent_id):
            return None  # --resume restores system prompt
        # First call — inject memory into system prompt
        prompt = self.system_prompt
        if self._memory_manager:
            memory = self._memory_manager.get_combined_memory(self.agent_id)
            if memory:
                prompt += f"\n\n{memory}"
        return prompt

    def _build_prompt(self, msg: Message) -> str:
        parts = []

        # 1. Memory context (for resumed sessions — first-call memory is in system prompt)
        if self._memory_manager and self.use_session:
            session_id = self._session_store.get(self.agent_id) if self._session_store else None
            if session_id:  # Resumed session — memory not in system prompt
                memory = self._memory_manager.get_combined_memory(self.agent_id)
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
        if my_tasks:
            lines.append("YOUR TASKS (ordered by priority — review tasks first):")
            for t in my_tasks:
                review_marker = " [NEEDS YOUR REVIEW]" if (t.status == TaskStatus.REVIEW and t.reviewer == self.agent_id) else ""
                lines.append(f"  - [P{t.priority}] [{t.status.value}] {t.title} (id: {t.id}){review_marker}")

        if other_tasks:
            # Truncate if too many
            shown = other_tasks[:MAX_TASK_CONTEXT_ITEMS - len(my_tasks)]
            lines.append("\nOTHER TEAM TASKS:")
            for t in shown:
                assignee = t.assignee or "unassigned"
                lines.append(f"  - [P{t.priority}] [{t.status.value}] {t.title} (assignee: {assignee})")
            if len(other_tasks) > len(shown):
                lines.append(f"  ... and {len(other_tasks) - len(shown)} more")

        return "\n".join(lines)

    async def _parse_response(self, result_text: str, original_msg: Message) -> list[Message]:
        return [Message(
            sender=self.agent_id,
            recipient=original_msg.sender,
            type=MessageType.RESPONSE,
            content=result_text,
            task_id=original_msg.task_id,
            parent_message_id=original_msg.id,
        )]

    # ── Shared action extraction (used by subclasses) ──

    def _extract_actions(self, text: str) -> list[dict]:
        """Extract ```action ... ``` JSON blocks from Claude output."""
        actions = []
        pattern = r"```action\s*\n(.*?)\n```"
        for match in re.findall(pattern, text, re.DOTALL):
            try:
                actions.append(json.loads(match.strip()))
            except json.JSONDecodeError:
                logger.warning("Failed to parse action block: %s", match[:100])
        return actions

    async def _handle_common_actions(self, actions: list[dict]) -> None:
        """Process update_memory and write_document/update_document actions.

        Called from subclass _parse_response() methods. These actions
        produce no outbound messages — they modify state directly.
        """
        kb_changed = False
        for action in actions:
            action_type = action.get("action")

            if action_type == "update_memory":
                self._handle_memory_update(action)

            elif action_type == "write_document":
                self._handle_write_document(action)
                kb_changed = True

            elif action_type == "update_document":
                self._handle_update_document(action)
                kb_changed = True

            elif action_type == "update_task":
                self._handle_update_task(action)

        # Broadcast KB update so frontend auto-refreshes
        if kb_changed and self._broker:
            await self._broker.broadcast_event({
                "event_type": "knowledge_updated",
                "data": {},
            })

    def _handle_memory_update(self, action: dict):
        if not self._memory_manager:
            return
        memory_type = action.get("memory_type", "")
        content = action.get("content", "")
        if not content:
            return
        if memory_type == "personality":
            self._memory_manager.update_personality_memory(self.agent_id, content)
        elif memory_type == "project":
            self._memory_manager.update_project_memory(self.agent_id, content)
        else:
            logger.warning("Unknown memory_type '%s' from %s", memory_type, self.agent_id)

    def _handle_write_document(self, action: dict):
        if not self._knowledge_base:
            return
        title = action.get("title", "")
        category = action.get("category", "")
        content = action.get("content", "")
        if not title or not category or not content:
            return
        try:
            self._knowledge_base.add_document(
                title=title, category=category,
                content=content, created_by=self.agent_id,
            )
        except ValueError as e:
            logger.warning("KB write_document error from %s: %s", self.agent_id, e)

    def _handle_update_document(self, action: dict):
        if not self._knowledge_base:
            return
        doc_id = action.get("doc_id", "")
        content = action.get("content", "")
        if not doc_id or not content:
            return
        self._knowledge_base.update_document(
            doc_id=doc_id, content=content, updated_by=self.agent_id,
        )

    def _handle_update_task(self, action: dict):
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
        self._task_board.update_task(task_id, **updates)

    async def _broadcast_status(self):
        if self._broker:
            await self._broker.broadcast_event({
                "event_type": "agent_status",
                "data": {
                    "agent_id": self.agent_id,
                    "status": self.status.value,
                    "current_task_id": self.current_task_id,
                },
            })

    def to_info_dict(self) -> dict:
        return {
            "id": self.agent_id,
            "name": self.name,
            "role": self.role,
            "status": self.status.value,
            "current_task_id": self.current_task_id,
            "messages_processed": self.messages_processed,
            "model": self.model,
        }
