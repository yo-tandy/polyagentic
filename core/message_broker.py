"""Message broker — hybrid file + DB design.

File-based inbox/outbox is KEPT for Claude CLI subprocess boundary.
Activity log and chat history are now DB-backed via MessageRepository.
In-memory deques are kept as a write-behind cache for WS broadcast speed.
"""
from __future__ import annotations

import asyncio
import logging
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

    def _get_poll_interval(self) -> float:
        if self._config:
            return self._config.get("POLL_INTERVAL_SECONDS", 1.0)
        return 1.0

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
        logger.info("Message broker started (polling every %.1fs)", poll)
        while self._running:
            await self._poll_cycle()
            await asyncio.sleep(poll)

    async def stop(self):
        self._running = False
        logger.info("Message broker stopped")

    async def _poll_cycle(self):
        for agent in self.registry.get_all():
            inbox = agent.inbox_dir
            if not inbox.exists():
                continue

            msg_files = sorted(inbox.glob("*.json"), key=lambda f: f.stat().st_mtime)
            for msg_file in msg_files:
                try:
                    msg = Message.from_file(msg_file)
                    await agent.message_queue.put(msg)
                    # Move to outbox
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

    def register_ws(self, ws):
        self._ws_clients.append(ws)
        logger.info("WebSocket client connected (%d total)", len(self._ws_clients))

    def unregister_ws(self, ws):
        if ws in self._ws_clients:
            self._ws_clients.remove(ws)
            logger.info("WebSocket client disconnected (%d remaining)", len(self._ws_clients))

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
        """Send a demo request to the project manager."""
        task_summary = []
        for t in self._task_board.get_all_tasks():
            task_summary.append(f"- [{t.status.value}] {t.title} (assignee: {t.assignee or 'none'})")
        summary_text = "\n".join(task_summary) or "No tasks on board."

        msg = Message(
            sender="system",
            recipient=self._checkpoint_agent,
            type=MessageType.SYSTEM,
            content=(
                f"Demo checkpoint: {done_count} tasks completed. "
                f"Please produce a demo review document summarizing all work done so far.\n\n"
                f"Current task board:\n{summary_text}"
            ),
        )
        await self.deliver(msg)
        logger.info("Triggered demo pause for %s (%d tasks done)", self._checkpoint_agent, done_count)

    def get_activity_log(self, limit: int = 100) -> list[dict]:
        items = list(self._activity_log)
        return items[-limit:]

    def get_chat_history(self) -> list[dict]:
        return list(self._chat_history)
