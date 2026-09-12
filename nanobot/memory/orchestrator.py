"""会话结束 4 步编排器（S1）。"""
from __future__ import annotations

import time
from typing import Any

from loguru import logger

from nanobot.memory.session_end_event import SessionEndEvent


class SessionEndOrchestrator:
    """会话结束编排器：4 步依赖链 + 失败隔离 + feature flag + 幂等保护。"""

    IDEMPOTENCY_WINDOW_SECONDS: float = 30.0

    def __init__(
        self,
        *,
        extractor: Any,
        scratchpad_writer: Any | None = None,
        enable_track2: bool = False,
        enable_scratchpad_reformat: bool = False,
    ) -> None:
        self._extractor = extractor
        self._scratchpad_writer = scratchpad_writer
        self._enable_track2 = enable_track2
        self._enable_scratchpad_reformat = enable_scratchpad_reformat
        self._last_run: dict[tuple[str, str], float] = {}

    async def run(self, event: SessionEndEvent) -> None:
        # 幂等保护
        key = (event.session_key, event.reason.value)
        now = time.monotonic()
        prev = self._last_run.get(key)
        if prev is not None and (now - prev) < self.IDEMPOTENCY_WINDOW_SECONDS:
            logger.info("[S1:SessionEnd] skipped (idempotent within 30s) key={}", key)
            return
        self._last_run[key] = now

        # Step 1: Episode
        episode = None
        try:
            episode = await self._extractor.generate_episode(
                event.transcript, event.session_key
            )
        except Exception as exc:
            logger.warning("[S1:SessionEnd] episode step failed: {}", exc)
        ep_id = getattr(episode, "id", None) if episode is not None else None
        ep_summary = getattr(episode, "summary", "") or "" if episode is not None else ""

        # Step 2a: 用户画像（默认 ON）
        try:
            await self._extractor.extract_user_profile(
                event.transcript, ep_id, cited=None
            )
        except Exception as exc:
            logger.warning("[S3:Track1] failed: {}", exc)

        # Step 2b: 任务经验（feature flag）
        if self._enable_track2:
            try:
                await self._extractor.extract_experience(event.transcript, ep_id)
            except Exception as exc:
                logger.warning("[S3:Track2] failed: {}", exc)

        # Step 3: Scratchpad 重构（feature flag，依赖 episode）
        if (
            self._enable_scratchpad_reformat
            and self._scratchpad_writer is not None
            and ep_summary
        ):
            try:
                await self._scratchpad_writer.format_with_llm(
                    None,
                    ep_summary,
                )
            except Exception as exc:
                logger.warning("[S4:Scratchpad] failed: {}", exc)
