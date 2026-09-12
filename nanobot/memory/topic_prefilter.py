"""话题切换检测的廉价预筛 + 最小间隔 + topic_hash 去重（S5）。"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from loguru import logger

from nanobot.memory.filters import is_chat_only, starts_with_follow_up
from nanobot.memory.intent import IntentType


class PrefilterResult(str, Enum):
    PASS = "pass"
    SKIP = "skip"


def compute_topic_hash(message: str) -> str:
    """用首条用户消息前 50 字符做 SHA1，取前 16 hex 作为 topic 标识。"""
    return hashlib.sha1((message or "")[:50].encode("utf-8")).hexdigest()[:16]


@dataclass
class _GateConfig:
    interval_seconds: int = 60
    length_jump_threshold: float = 0.30


class TopicChangeGate:
    """组合：廉价预筛 + 最小间隔 + topic_hash 去重。"""

    def __init__(
        self,
        *,
        interval_seconds: int = 60,
        intent_classifier: Callable[[str], IntentType] | None = None,
        length_jump_threshold: float = 0.30,
    ) -> None:
        self._config = _GateConfig(
            interval_seconds=interval_seconds,
            length_jump_threshold=length_jump_threshold,
        )
        self._intent = intent_classifier
        self._last_fire: dict[tuple[str, str], float] = {}

    def prefilter(self, message: str, recent: list[str]) -> PrefilterResult:
        msg = (message or "").strip()
        if len(msg) < 5:
            return PrefilterResult.SKIP
        if is_chat_only(msg):
            return PrefilterResult.SKIP
        if starts_with_follow_up(msg):
            return PrefilterResult.SKIP
        if self._intent is not None:
            try:
                if self._intent(msg) == IntentType.CHAT:
                    return PrefilterResult.SKIP
            except Exception as exc:
                logger.debug("topic prefilter intent_classifier failed (non-fatal): {}", exc)
        if recent:
            last = recent[-1]
            if len(last) > 0 and len(msg) > 0:
                ratio = min(len(msg), len(last)) / max(len(msg), len(last))
                if ratio < self._config.length_jump_threshold:
                    return PrefilterResult.SKIP
        return PrefilterResult.PASS

    def allow_fire(self, session_key: str, topic_hash: str) -> bool:
        key = (session_key, topic_hash)
        now = time.monotonic()
        prev = self._last_fire.get(key)
        if prev is not None and (now - prev) < self._config.interval_seconds:
            return False
        self._last_fire[key] = now
        return True