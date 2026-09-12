"""会话结束事件数据结构（P0：编排器输入契约）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class SessionEndReason(str, Enum):
    """会话结束原因。"""

    USER_CLOSE = "user_close"
    IDLE_TIMEOUT = "idle_timeout"
    PROCESS_SHUTDOWN = "process_shutdown"
    CHANNEL_DISCONNECT = "channel_disconnect"


@dataclass
class SessionEndEvent:
    """会话结束事件载荷。"""

    session_key: str
    reason: SessionEndReason
    transcript: list[dict] = field(default_factory=list)
    emitted_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        # 防御性浅拷贝，避免外部修改影响事件快照
        self.transcript = [dict(m) for m in self.transcript]
