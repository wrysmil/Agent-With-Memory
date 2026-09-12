"""用户画像提取 + 引用评分 + 增量合并。

S3 Track 1: 复用 SEMANTIC_EXTRACTION_PROMPT（取 memories 字段），
拼装 CITATION_SCORING_SECTION 段（同次 LLM 调用）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from loguru import logger

from nanobot.memory.prompts import SEMANTIC_EXTRACTION_PROMPT


CITATION_SCORING_SECTION = """\

以下是被检索到的历史记忆，请逐条评判它对本次任务是否有实际帮助：
{cited_memories}

在你的 JSON 输出中增加 "citation_scores" 字段：
"citation_scores": [
  {{"memory_id": "xxx", "useful": true/false}}
]
- useful=true：记忆对本次任务执行有实际帮助（提供了信息、避免了错误等）
- useful=false：记忆与本次任务无关或无实际帮助

最终输出格式: {{"memories": [...], "citation_scores": [...]}}
如没有要提取的记忆，memories 为空数组。只输出 JSON。"""


@dataclass
class ProfileItem:
    content: str
    subject: str
    predicate: str
    type: str
    importance: float
    priority: str
    tags: list[str]
    is_update: bool = False


@dataclass
class MergeResult:
    action: str  # "update" | "create_new" | "keep_old_with_conflict"
    merged: dict | None = None
    existing: dict | None = None


class ProfileExtractor:
    """S3 Track 1：用户画像 + 引用评分。"""

    def __init__(self, runtime: Any | None = None, model: str = "") -> None:
        self._runtime = runtime
        self._model = model

    async def extract(
        self,
        transcript: list[dict],
        episode_id: str,
        cited_memories: list[dict] | None = None,
    ) -> tuple[list[ProfileItem], list[dict]]:
        if self._runtime is None:
            return [], []
        try:
            conv_text = _format_conv_lines(transcript)
            # SEMANTIC_EXTRACTION_PROMPT 为指令式，无 {conversation} 占位符
            prompt = f"{SEMANTIC_EXTRACTION_PROMPT}\n\n### 对话转录\n{conv_text}"
            has_cites = bool(cited_memories)
            if has_cites:
                cite_text = "\n".join(
                    f"- ID={m['id']} | {(m.get('content', '') or '')[:150]}"
                    for m in cited_memories
                )
                prompt += CITATION_SCORING_SECTION.format(cited_memories=cite_text)

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
                return _parse_legacy_array(text), []
            items_raw = data.get("memories") or []
            scores_raw = data.get("citation_scores") or []
            items = [
                ProfileItem(
                    content=str(i.get("content", "")),
                    subject=str(i.get("subject", "")),
                    predicate=str(i.get("predicate", "")),
                    type=str(i.get("type", "FACT")).upper(),
                    importance=float(i.get("importance", 0.5)),
                    priority=str(i.get("priority", "long_term")).lower(),
                    tags=list(i.get("tags") or []),
                    is_update=bool(i.get("is_update", False)),
                )
                for i in items_raw
                if isinstance(i, dict)
            ]
            scores = [
                {"memory_id": str(s["memory_id"]), "useful": bool(s.get("useful", False))}
                for s in scores_raw
                if isinstance(s, dict) and "memory_id" in s
            ]
            return items, scores
        except Exception as exc:
            logger.warning("ProfileExtractor.extract failed: {}", exc)
            return [], []


def merge_profile_incremental(existing: dict, incoming: dict) -> MergeResult:
    """增量合并：subject+predicate 相同才尝试更新；冲突保留旧 + 记 conflicts_with。"""
    if (
        existing.get("subject") == incoming.get("subject")
        and existing.get("predicate") == incoming.get("predicate")
    ):
        neg_a = any(k in existing.get("content", "") for k in ("不", "没", "无"))
        neg_b = any(k in incoming.get("content", "") for k in ("不", "没", "无"))
        if neg_a != neg_b and existing.get("content") != incoming.get("content"):
            new_existing = dict(existing)
            conflicts = list(new_existing.get("conflicts_with") or [])
            # incoming 缺 id 时，用 subject+predicate+content 短 hash 作冲突标识
            incoming_id = (
                incoming.get("id")
                or incoming.get("memory_id")
                or _conflict_id(incoming)
            )
            if incoming_id and incoming_id not in conflicts:
                conflicts.append(incoming_id)
            if len(conflicts) > 5:
                conflicts = conflicts[-5:]
            new_existing["conflicts_with"] = conflicts
            return MergeResult(action="keep_old_with_conflict", existing=new_existing)
        merged = dict(existing)
        merged["content"] = incoming.get("content", existing.get("content"))
        if "importance" in incoming:
            merged["importance"] = incoming["importance"]
        return MergeResult(action="update", merged=merged)
    return MergeResult(action="create_new")


def _conflict_id(incoming: dict) -> str:
    """为 incoming 生成稳定冲突标识（subject|predicate|content[:30]）。"""
    import hashlib

    key = f"{incoming.get('subject','')}|{incoming.get('predicate','')}|{(incoming.get('content','') or '')[:30]}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _format_conv_lines(transcript: list[dict]) -> str:
    lines: list[str] = []
    for t in transcript[-30:]:
        content = (t.get("content", "") or "")[:1500]
        if content:
            role = "用户" if t.get("role") == "user" else "助手"
            lines.append(f"[{role}]: {content}")
    return "\n".join(lines)


def _extract_json_obj(text: str) -> Any | None:
    """从尾部锚定提取 JSON 对象（处理 prompt 模板示例的干扰花括号）。"""
    import json as _json

    # 从尾部尝试 parse，每个 } 位置都试一次
    for end_idx in range(len(text) - 1, -1, -1):
        if text[end_idx] != "}":
            continue
        # 找最近的 { 起点
        depth = 0
        start_idx = -1
        for i in range(end_idx, -1, -1):
            if text[i] == "}":
                depth += 1
            elif text[i] == "{":
                depth -= 1
                if depth == 0:
                    start_idx = i
                    break
        if start_idx < 0:
            continue
        candidate = text[start_idx:end_idx + 1]
        try:
            return _json.loads(candidate)
        except Exception:
            continue
    return None


def _parse_legacy_array(text: str) -> list[ProfileItem]:
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return []
    return [
        ProfileItem(
            content=str(i.get("content", "")),
            subject=str(i.get("subject", "")),
            predicate=str(i.get("predicate", "")),
            type=str(i.get("type", "FACT")).upper(),
            importance=float(i.get("importance", 0.5)),
            priority=str(i.get("priority", "long_term")).lower(),
            tags=list(i.get("tags") or []),
            is_update=bool(i.get("is_update", False)),
        )
        for i in arr
        if isinstance(i, dict)
    ]