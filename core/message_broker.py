from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from core.message import Message, MessageType
from config import POLL_INTERVAL_SECONDS, DEMO_PAUSE_INTERVAL

if TYPE_CHECKING:
    from core.agent_registry import AgentRegistry
    from core.task_board import TaskBoard

logger = logging.getLogger(__name__)

MAX_ACTIVITY_LOG = 500
MAX_CHAT_HISTORY = 200


class MessageBroker:
    def __init__(self, messages_dir: Path, registry: AgentRegistry):
        self.messages_dir = messages_dir
        self.registry = registry
        self._running = False
        self._ws_clients: list = []
        self._activity_log: deque[dict] = deque(maxlen=MAX_ACTIVITY_LOG)
        self._chat_history: deque[dict] = deque(maxlen=MAX_CHAT_HISTORY)
        self._task_board: TaskBoard | None = None
        self._last_demo_count = 0

    def set_task_board(self, task_board: TaskBoard):
        self._task_board = task_board

    async def start(self):
        self._running = True
        logger.info("Message broker started (polling every %.1fs)", POLL_INTERVAL_SECONDS)
        while self._running:
            await self._poll_cycle()
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

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

                chat_event = {
                    "message_id": message.id,
                    "sender": message.sender,
                    "content": message.content,
                    "timestamp": message.timestamp,
                    "task_id": message.task_id,
                    "metadata": message.metadata or {},
                }
                self._chat_history.append(chat_event)

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
        done_count = len(self._task_board.get_tasks_by_status(TaskStatus.DONE))
        if done_count > 0 and done_count % DEMO_PAUSE_INTERVAL == 0 and done_count != self._last_demo_count:
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
            recipient="project_manager",
            type=MessageType.SYSTEM,
            content=(
                f"Demo checkpoint: {done_count} tasks completed. "
                f"Please produce a demo review document summarizing all work done so far.\n\n"
                f"Current task board:\n{summary_text}"
            ),
        )
        await self.deliver(msg)
        logger.info("Triggered demo pause for project_manager (%d tasks done)", done_count)

    def get_activity_log(self, limit: int = 100) -> list[dict]:
        items = list(self._activity_log)
        return items[-limit:]

    def get_chat_history(self) -> list[dict]:
        return list(self._chat_history)
