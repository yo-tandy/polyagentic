from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


class MessageType(str, Enum):
    TASK = "task"
    RESPONSE = "response"
    REDIRECT = "redirect"
    STATUS_UPDATE = "status_update"
    CHAT = "chat"
    SYSTEM = "system"
    MERGE_REQUEST = "merge_request"
    BUILD_REQUEST = "build_request"
    REVIEW_REQUEST = "review_request"


@dataclass
class Message:
    sender: str
    recipient: str
    type: MessageType
    content: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    task_id: str | None = None
    parent_message_id: str | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def to_file(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        filepath = directory / f"{self.id}.json"
        filepath.write_text(self.to_json())
        return filepath

    @classmethod
    def from_dict(cls, data: dict) -> Message:
        data = data.copy()
        data["type"] = MessageType(data["type"])
        return cls(**data)

    @classmethod
    def from_json(cls, text: str) -> Message:
        return cls.from_dict(json.loads(text))

    @classmethod
    def from_file(cls, filepath: Path) -> Message:
        return cls.from_json(filepath.read_text())
