"""Message broker — hybrid file + DB design.

File-based inbox/outbox is KEPT for Claude CLI subprocess boundary.
Activity log and chat history are now DB-backed via MessageRepository.
In-memory deques are kept as a write-behind cache for WS broadcast speed.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from core.message import Message, MessageType

if TYPE_CHECKING:
    from core.agent_registry import AgentRegistry
    from core.task_board import TaskBoard
    from db.repositories.message_repo import MessageRepository
    from db.config_provider import ConfigProvider

logger = logging.getLogger(__name__)

MAX_ACTIVITY_LOG = 500
MAX_CHAT_HISTORY = 200


class MessageBroker:
    def __init__(
        self,
        messages_dir: Path,
        registry: AgentRegistry,
        message_repo: MessageRepository | None = None,
        project_id: str = "",
        config: ConfigProvider | None = None,
    ):
        self.messages_dir = messages_dir
        self.registry = registry
        self._message_repo = message_repo
        self._project_id = project_id
        self._config = config
        self._running = False
        self._ws_clients: list = []
        self._activity_log: deque[dict] = deque(maxlen=MAX_ACTIVITY_LOG)
        self._chat_history: deque[dict] = deque(maxlen=MAX_CHAT_HISTORY)
        self._task_board: TaskBoard | None = None
        self._conversation_manager = None
        self._checkpoint_agent = "jerry"
        self._last_demo_count = 0
        self._last_nudge_time: float = time.monotonic()
        # Ping-pong loop detection: tracks (sender, recipient) recent message times
        self._pair_timestamps: dict[tuple[str, str], list[float]] = {}
        # Nudge escalation: tracks per-agent nudge count + task snapshot
        self._nudge_tracker: dict[str, dict] = {}  # agent_id -> {count, task_snapshot}

    def _get_poll_interval(self) -> float:
        if self._config:
            return self._config.get("POLL_INTERVAL_SECONDS", 1.0)
        return 1.0

    def _get_nudge_interval(self) -> float:
        if self._config:
            return self._config.get("NUDGE_INTERVAL_SECONDS", 20)
        return 20

    def _get_demo_pause_interval(self) -> int:
        if self._config:
            return self._config.get("DEMO_PAUSE_INTERVAL", 5)
        return 5

    def set_task_board(self, task_board: TaskBoard):
        self._task_board = task_board

    def set_conversation_manager(self, cm):
        self._conversation_manager = cm

    def set_checkpoint_agent(self, agent_id: str):
        """Set the agent that receives demo/checkpoint pings."""
        self._checkpoint_agent = agent_id

    async def start(self):
        self._running = True
        poll = self._get_poll_interval()
        nudge = self._get_nudge_interval()
        logger.info("Message broker started (polling every %.1fs, nudge every %.0fs)", poll, nudge)
        while self._running:
            try:
                await self._poll_cycle()
            except Exception:
                logger.exception("Error in poll cycle")
            # Periodically nudge idle agents and reconcile orphaned tasks
            now = time.monotonic()
            if now - self._last_nudge_time >= self._get_nudge_interval():
                try:
                    await self._nudge_idle_agents()
                except Exception:
                    logger.exception("Error in nudge cycle")
                try:
                    await self._reconcile_orphaned_tasks()
                except Exception:
                    logger.exception("Error in orphan reconciliation")
                self._last_nudge_time = now
            await asyncio.sleep(poll)

    async def stop(self):
        self._running = False
        logger.info("Message broker stopped")

    async def _poll_cycle(self):
        for agent in self.registry.get_all():
            # Poll inbox
            await self._poll_directory(agent, agent.inbox_dir, keep_task_files=True)
            # Poll workingbox (re-queue interrupted tasks not yet on queue)
            await self._poll_directory(agent, agent.workingbox_dir, keep_task_files=True)

    async def _reconcile_orphaned_tasks(self):
        """Create inbox messages for PENDING board tasks with no message file.

        This handles tasks created by scope decomposition (or delegation)
        where the corresponding message delivery failed (e.g. crash,
        broker.send() bug).  Runs periodically alongside the nudge cycle.
        """
        from core.task import TaskStatus
        if not self._task_board:
            return
        for agent in self.registry.get_all():
            # Skip agents that already have queued work — avoid duplicates
            if agent._queued_message_ids:
                continue

            # Collect task IDs already represented in inbox/workingbox
            known_task_ids: set[str] = set()
            for d in (agent.inbox_dir, agent.workingbox_dir):
                if not d.exists():
                    continue
                for f in d.glob("*.json"):
                    try:
                        m = Message.from_file(f)
                        if m.task_id:
                            known_task_ids.add(m.task_id)
                    except Exception:
                        pass

            for task in self._task_board.get_workable_tasks(agent.agent_id, agent.role):
                if task.status != TaskStatus.PENDING:
                    continue
                if task.id in known_task_ids:
                    continue
                msg = Message(
                    sender=task.created_by or "system",
                    recipient=agent.agent_id,
                    type=MessageType.TASK,
                    content=task.description,
                    task_id=task.id,
                    metadata={"task_title": task.title, "synthesized_from_board": True},
                )
                msg.to_file(agent.inbox_dir)
                logger.info(
                    "Reconciled orphaned task %s (%s) → inbox for %s",
                    task.id, task.title[:50], agent.agent_id,
                )

    async def _poll_directory(self, agent, directory: Path, *, keep_task_files: bool):
        """Read message files from a directory and queue them for the agent."""
        from core.task import TaskStatus
        if not directory.exists():
            return

        msg_files = sorted(directory.glob("*.json"), key=lambda f: f.stat().st_mtime)
        for msg_file in msg_files:
            msg_id = msg_file.stem

            # Skip if already queued
            if msg_id in agent._queued_message_ids:
                continue

            try:
                msg = Message.from_file(msg_file)

                # Skip stale TASK messages for completed/cancelled tasks
                if msg.type == MessageType.TASK and msg.task_id and self._task_board:
                    task = self._task_board.get_task(msg.task_id)
                    if task and task.status in (TaskStatus.DONE, TaskStatus.CANCELLED):
                        dest = agent.outbox_dir / msg_file.name
                        msg_file.rename(dest)
                        logger.info(
                            "Poll: cleaned stale task %s (status=%s) for agent %s",
                            msg.task_id, task.status, agent.agent_id,
                        )
                        continue

                await agent.message_queue.put(msg)
                agent._queued_message_ids.add(msg_id)

                # Non-TASK messages are transient — move to outbox immediately
                if keep_task_files and msg.type != MessageType.TASK:
                    dest = agent.outbox_dir / msg_file.name
                    msg_file.rename(dest)

                activity = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "sender": msg.sender,
                    "recipient": msg.recipient,
                    "type": msg.type.value,
                    "content_preview": msg.content[:200],
                    "message_id": msg.id,
                    "task_id": msg.task_id,
                }
                self._activity_log.append(activity)

                # Log to DB (fire-and-forget)
                if self._message_repo:
                    try:
                        await self._message_repo.log_activity(
                            project_id=self._project_id,
                            message_id=msg.id,
                            sender=msg.sender,
                            recipient=msg.recipient,
                            msg_type=msg.type.value,
                            content_preview=msg.content[:200],
                            task_id=msg.task_id,
                        )
                    except Exception:
                        logger.debug("Failed to log activity to DB", exc_info=True)

                await self.broadcast_event({
                    "event_type": "new_message",
                    "data": activity,
                })

                logger.info(
                    "Broker: %s -> %s (%s) [%s]",
                    msg.sender, msg.recipient, msg.type.value,
                    msg.content[:80].replace("\n", " "),
                )
            except Exception:
                logger.exception("Error processing message file: %s", msg_file)

    async def deliver(self, message: Message):
        recipient = self.registry.get(message.recipient)

        if recipient is None:
            if message.recipient == "user":
                logger.info(
                    "Delivering to user from %s: %s",
                    message.sender, message.content[:80].replace("\n", " "),
                )
                activity = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "sender": message.sender,
                    "recipient": "user",
                    "type": message.type.value,
                    "content_preview": message.content[:200],
                    "message_id": message.id,
                    "task_id": message.task_id,
                }
                self._activity_log.append(activity)

                # If this is a task-related status update (has task_id,
                # no suggested_answers, no active conversation), route it
                # to the task's progress notes instead of the main chat.
                meta = message.metadata or {}
                has_suggested = bool(meta.get("suggested_answers"))
                conv = None
                if self._conversation_manager:
                    meta_cid = meta.get("conversation_id")
                    if meta_cid:
                        conv = self._conversation_manager.get_conversation(meta_cid)
                    if not conv:
                        conv = self._conversation_manager.get_by_agent(message.sender)

                if message.task_id and not has_suggested and not conv and self._task_board:
                    # Route to task progress notes — not the chat window
                    await self._task_board.update_task(
                        message.task_id,
                        progress_note=message.content,
                        _agent_id=message.sender,
                    )
                    logger.info(
                        "Routed %s response to task %s progress notes",
                        message.sender, message.task_id,
                    )
                    return

                chat_event = {
                    "message_id": message.id,
                    "sender": message.sender,
                    "content": message.content,
                    "timestamp": message.timestamp,
                    "task_id": message.task_id,
                    "metadata": meta,
                }

                if conv:
                    chat_event["conversation_id"] = conv["id"]
                    await self._conversation_manager.record_message(
                        message.sender, message.content, conv_id=conv["id"],
                    )

                self._chat_history.append(chat_event)

                # Log chat to DB
                if self._message_repo:
                    try:
                        await self._message_repo.log_chat(
                            project_id=self._project_id,
                            message_id=message.id,
                            sender=message.sender,
                            recipient="user",
                            msg_type=message.type.value,
                            content=message.content,
                            task_id=message.task_id,
                            metadata_json=meta,
                            conversation_id=chat_event.get("conversation_id"),
                        )
                    except Exception:
                        logger.debug("Failed to log chat to DB", exc_info=True)

                await self.broadcast_event({
                    "event_type": "chat_response",
                    "data": chat_event,
                })
                return

            logger.warning("Unknown recipient: %s (from %s)", message.recipient, message.sender)
            return

        # Ping-pong loop detection for inter-agent messages (all types)
        if message.sender not in ("user", "system") and message.recipient not in ("user",):
            if self._is_ping_pong(message.sender, message.recipient):
                logger.warning(
                    "Ping-pong loop detected: %s <-> %s — dropping message",
                    message.sender, message.recipient,
                )
                return

        try:
            filepath = message.to_file(recipient.inbox_dir)
            logger.info(
                "Wrote message to %s inbox: %s",
                message.recipient, filepath.name,
            )
        except Exception:
            logger.exception("Failed to write message to %s inbox", message.recipient)

        # Check if we should trigger a demo pause
        await self._check_demo_pause(message)

    async def broadcast_event(self, event: dict):
        event.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        if not self._ws_clients:
            logger.debug("No WebSocket clients connected, event type: %s", event.get("event_type"))
            return
        dead = []
        for ws in self._ws_clients:
            try:
                await ws.send_json(event)
            except Exception as e:
                logger.debug("WebSocket send failed: %s", e)
                dead.append(ws)
        for ws in dead:
            self._ws_clients.remove(ws)
        if dead:
            logger.info("Removed %d dead WebSocket client(s), %d remaining", len(dead), len(self._ws_clients))

    def _is_ping_pong(self, sender: str, recipient: str) -> bool:
        """Detect rapid back-and-forth between two agents.

        If both A->B and B->A have sent 3+ messages each within 120s,
        it's a ping-pong loop.
        """
        now = time.monotonic()
        window = 120  # seconds
        threshold = 3  # messages per direction

        # Track this message
        pair = (sender, recipient)
        if pair not in self._pair_timestamps:
            self._pair_timestamps[pair] = []
        self._pair_timestamps[pair].append(now)

        # Trim old entries for both directions
        for key in [pair, (recipient, sender)]:
            if key in self._pair_timestamps:
                self._pair_timestamps[key] = [
                    t for t in self._pair_timestamps[key] if now - t < window
                ]

        fwd = len(self._pair_timestamps.get((sender, recipient), []))
        rev = len(self._pair_timestamps.get((recipient, sender), []))

        if fwd >= threshold and rev >= threshold:
            # Clear both to allow fresh conversation later
            self._pair_timestamps.pop((sender, recipient), None)
            self._pair_timestamps.pop((recipient, sender), None)
            return True
        return False

    def register_ws(self, ws):
        self._ws_clients.append(ws)
        logger.info("WebSocket client connected (%d total)", len(self._ws_clients))

    def unregister_ws(self, ws):
        if ws in self._ws_clients:
            self._ws_clients.remove(ws)
            logger.info("WebSocket client disconnected (%d remaining)", len(self._ws_clients))

    async def _nudge_idle_agents(self):
        """Ensure idle agents with pending work have TASK messages queued.

        Instead of sending STATUS_UPDATE nudges, this method ensures the
        agent's inbox/workingbox has the right content:
        1. Workingbox has a file? Re-queue it (agent lost in-memory queue item).
        2. Inbox has TASK files? Re-queue the first one.
        3. Both empty? Synthesize TASK messages from the board into inbox.

        Tracks nudge count per agent. After 3 nudges with no progress,
        escalate to the user and stop nudging.
        """
        if not self._task_board:
            return
        from core.agent import AgentStatus
        from core.task import TaskStatus

        max_nudges_before_escalation = 3
        agents = self.registry.get_all()
        logger.info("Nudge cycle: checking %d agents", len(agents))

        for agent in agents:
            # Only nudge agents that are idle
            if agent.status not in (AgentStatus.IDLE, AgentStatus.WAITING):
                logger.debug("Nudge: skipping %s (status=%s)", agent.agent_id, agent.status)
                self._nudge_tracker.pop(agent.agent_id, None)
                continue
            # Don't nudge if agent already has queued messages
            if not agent.message_queue.empty():
                logger.debug("Nudge: skipping %s (queue not empty, ~%d items)", agent.agent_id, agent.message_queue.qsize())
                continue
            # Only nudge agents that have been idle for at least 20s
            idle_seconds = time.monotonic() - agent.last_processed_at
            if idle_seconds < 20:
                logger.debug("Nudge: skipping %s (idle only %.0fs)", agent.agent_id, idle_seconds)
                continue

            active_tasks = self._task_board.get_workable_tasks(agent.agent_id, agent.role)
            if not active_tasks:
                logger.debug("Nudge: skipping %s (no active tasks)", agent.agent_id)
                self._nudge_tracker.pop(agent.agent_id, None)
                continue
            logger.info("Nudge: agent %s is idle with %d active tasks — proceeding", agent.agent_id, len(active_tasks))

            # Escalation tracking
            task_snapshot = frozenset(
                (t.id, t.status.value) for t in active_tasks
            )
            tracker = self._nudge_tracker.get(agent.agent_id)
            if tracker:
                if tracker["task_snapshot"] == task_snapshot:
                    tracker["count"] += 1
                else:
                    tracker = {"count": 1, "task_snapshot": task_snapshot}
                    self._nudge_tracker[agent.agent_id] = tracker
            else:
                tracker = {"count": 1, "task_snapshot": task_snapshot}
                self._nudge_tracker[agent.agent_id] = tracker

            if tracker.get("escalated"):
                continue

            if tracker["count"] > max_nudges_before_escalation:
                await self._escalate_unresponsive_agent(agent, active_tasks)
                tracker["escalated"] = True
                continue

            # --- Three-step self-serve nudge ---

            # Step 1: Workingbox — re-queue interrupted task
            wb_requeued = False
            if agent.workingbox_dir.exists():
                for wb_file in agent.workingbox_dir.glob("*.json"):
                    try:
                        msg = Message.from_file(wb_file)
                        agent._queued_message_ids.discard(msg.id)
                        await agent.message_queue.put(msg)
                        agent._queued_message_ids.add(msg.id)
                        wb_requeued = True
                        logger.info(
                            "Nudge: re-queued workingbox task %s for %s (nudge #%d)",
                            msg.task_id, agent.agent_id, tracker["count"],
                        )
                    except Exception:
                        logger.exception("Nudge: failed to re-queue workingbox file %s", wb_file)
            if wb_requeued:
                continue

            # Step 2: Inbox — re-queue first TASK file
            inbox_requeued = False
            if agent.inbox_dir.exists():
                for inbox_file in sorted(
                    agent.inbox_dir.glob("*.json"),
                    key=lambda f: f.stat().st_mtime,
                ):
                    try:
                        msg = Message.from_file(inbox_file)
                        if msg.type == MessageType.TASK:
                            agent._queued_message_ids.discard(msg.id)
                            await agent.message_queue.put(msg)
                            agent._queued_message_ids.add(msg.id)
                            inbox_requeued = True
                            logger.info(
                                "Nudge: re-queued inbox task %s for %s (nudge #%d)",
                                msg.task_id, agent.agent_id, tracker["count"],
                            )
                            break  # one at a time
                    except Exception:
                        logger.exception("Nudge: failed to re-queue inbox file %s", inbox_file)
            if inbox_requeued:
                continue

            # Step 3: Both empty — synthesize TASK messages from board
            for task in active_tasks:
                # Check if a message for this task already exists
                task_has_msg = self._task_has_message_file(agent, task.id)
                if task_has_msg:
                    continue

                # Build content from task data
                content = task.description or task.title
                if task.progress_notes:
                    last_note = task.progress_notes[-1]
                    content += f"\n\nLast progress: {last_note.get('note', '')}"

                msg = Message(
                    sender=task.created_by or self._checkpoint_agent,
                    recipient=agent.agent_id,
                    type=MessageType.TASK,
                    content=content,
                    task_id=task.id,
                    metadata={
                        "task_title": task.title,
                        "synthesized_from_board": True,
                    },
                )
                msg.to_file(agent.inbox_dir)
                await agent.message_queue.put(msg)
                agent._queued_message_ids.add(msg.id)
                logger.info(
                    "Nudge: synthesized TASK for %s -> %s [%s] %s (nudge #%d)",
                    task.id, agent.agent_id, task.status.value,
                    task.title, tracker["count"],
                )
                break  # one task at a time

    def _task_has_message_file(self, agent, task_id: str) -> bool:
        """Check if a TASK message file for this task_id exists in inbox or workingbox."""
        for directory in (agent.inbox_dir, agent.workingbox_dir):
            if not directory.exists():
                continue
            for f in directory.glob("*.json"):
                try:
                    msg = Message.from_file(f)
                    if msg.task_id == task_id:
                        return True
                except Exception:
                    pass
        return False

    async def _escalate_unresponsive_agent(self, agent, active_tasks):
        """Escalate to the user when an agent fails to act after repeated nudges."""
        summary = ", ".join(
            f"[{t.status.value}] {t.title}" for t in active_tasks[:5]
        )
        if len(active_tasks) > 5:
            summary += f" (+{len(active_tasks) - 5} more)"

        # Send directly to user so it appears in the chat UI
        escalation = Message(
            sender=self._checkpoint_agent,
            recipient="user",
            type=MessageType.CHAT,
            content=(
                f"**{agent.name}** ({agent.agent_id}) has been nudged "
                f"{3} times but has not made progress on {len(active_tasks)} task(s):\n"
                f"{summary}\n\n"
                f"This agent appears stuck. You may want to reset their session, "
                f"reassign these tasks, or cancel stale ones."
            ),
            metadata={"escalation": True},
        )
        await self.deliver(escalation)
        logger.warning(
            "Escalated unresponsive agent %s to user — %d stuck tasks",
            agent.agent_id, len(active_tasks),
        )

    async def _check_demo_pause(self, message: Message):
        """After a response is delivered, check if we've hit the demo pause threshold."""
        if not self._task_board or message.type != MessageType.RESPONSE:
            return
        from core.task import TaskStatus
        demo_interval = self._get_demo_pause_interval()
        done_count = len(self._task_board.get_tasks_by_status(TaskStatus.DONE))
        if done_count > 0 and done_count % demo_interval == 0 and done_count != self._last_demo_count:
            self._last_demo_count = done_count
            await self._trigger_demo_pause(done_count)

    async def _trigger_demo_pause(self, done_count: int):
        """Create a demo review task for the checkpoint agent."""
        task_summary = []
        for t in self._task_board.get_all_tasks():
            task_summary.append(f"- [{t.status.value}] {t.title} (assignee: {t.assignee or 'none'})")
        summary_text = "\n".join(task_summary) or "No tasks on board."

        description = (
            f"Demo checkpoint: {done_count} tasks completed.\n\n"
            f"You MUST produce a demo review document using the write_document action:\n"
            f"```action\n"
            f'{{"action": "write_document", "title": "Demo Review — Checkpoint {done_count}", '
            f'"category": "history", "content": "<your review in markdown>"}}\n'
            f"```\n\n"
            f"The document should summarize:\n"
            f"- What was accomplished since the last checkpoint\n"
            f"- Current project status and progress\n"
            f"- Key decisions made\n"
            f"- Blockers or risks\n"
            f"- What's next\n\n"
            f"Current task board:\n{summary_text}"
        )

        # Create a tracked task on the board
        from core.task import TaskStatus
        task = await self._task_board.create_task(
            title=f"Demo Review — {done_count} tasks completed",
            description=description,
            created_by="system",
            assignee=self._checkpoint_agent,
            priority=1,  # high priority
            category="operational",
            initial_status=TaskStatus.PENDING,
            scope_approved=True,  # skip scope analysis for review tasks
        )

        # Deliver as a TASK message so the agent processes it
        msg = Message(
            sender="system",
            recipient=self._checkpoint_agent,
            type=MessageType.TASK,
            content=description,
            task_id=task.id,
            metadata={"task_title": task.title},
        )
        await self.deliver(msg)
        logger.info(
            "Created demo review task %s for %s (%d tasks done)",
            task.id, self._checkpoint_agent, done_count,
        )

    def get_activity_log(self, limit: int = 100) -> list[dict]:
        items = list(self._activity_log)
        return items[-limit:]

    def get_chat_history(self) -> list[dict]:
        return list(self._chat_history)
