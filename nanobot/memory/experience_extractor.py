"""任务经验提取（S3 Track 2：feature flag 控制默认关闭）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from nanobot.memory.profile_extractor import _extract_json_obj, _format_conv_lines
from nanobot.memory.prompts import SEMANTIC_EXTRACTION_PROMPT


@dataclass
class ExperienceItem:
    content: str
    subject: str
    predicate: str
    type: str
    importance: float
    priority: str
    tags: list[str]


class ExperienceExtractor:
    """S3 Track 2：从对话转录提取可复用的任务经验。"""

    MIN_ASSISTANT_TURNS: int = 2

    def __init__(self, runtime: Any | None = None, model: str = "") -> None:
        self._runtime = runtime
        self._model = model

    async def extract(
        self,
        transcript: list[dict],
        episode_id: str,
    ) -> list[ExperienceItem]:
        if self._runtime is None:
            return []
        assistant_turns = [
            t for t in transcript
            if t.get("role") == "assistant" and t.get("content")
        ]
        if len(assistant_turns) < self.MIN_ASSISTANT_TURNS:
            return []
        try:
            conv_text = _format_conv_lines(transcript)
            # 复用 SEMANTIC_EXTRACTION_PROMPT，取 experiences 字段
            prompt = f"{SEMANTIC_EXTRACTION_PROMPT}\n\n### 对话转录\n{conv_text}"
            resp = await self._runtime.provider.chat_with_retry(
                model=self._model or getattr(self._runtime, "model", ""),
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                temperature=getattr(self._runtime.generation, "temperature", 0.0),
                max_tokens=getattr(self._runtime.generation, "max_tokens", 1000),
                reasoning_effort=getattr(self._runtime.generation, "reasoning_effort", None),
            )
            text = (getattr(resp, "content", "") or "").strip()
            data = _extract_json_obj(text)
            if not isinstance(data, dict):
                return []
            arr = data.get("experiences") or []
            return [
                ExperienceItem(
                    content=str(i.get("content", "")),
                    subject=str(i.get("subject", "")),
                    predicate=str(i.get("predicate", "")),
                    type=str(i.get("type", "EXPERIENCE")).upper(),
                    importance=float(i.get("importance", 0.5)),
                    priority=str(i.get("priority", "long_term")).lower(),
                    tags=list(i.get("tags") or []),
                )
                for i in arr
                if isinstance(i, dict)
            ]
        except Exception as exc:
            logger.warning("ExperienceExtractor.extract failed: {}", exc)
            return []
