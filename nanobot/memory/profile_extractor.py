"""用户画像提取 + 引用评分 + 增量合并。

S3 Track 1: 复用 SEMANTIC_EXTRACTION_PROMPT（取 memories 字段），
拼装 CITATION_SCORING_SECTION 段（同次 LLM 调用）。
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
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
    #: LLM 未给出 priority 时保持 ``None``，让下游 ``_resolve_priority`` 的兜底
    #: 推断（含时间关键词 → short_term）仍能生效。切勿默认成 "long_term"。
    priority: str | None
    tags: list[str]
    is_update: bool = False


@dataclass
class MergeResult:
    action: str  # "update" | "create_new" | "keep_old_with_conflict"
    merged: dict | None = None
    existing: dict | None = None


@dataclass
class ProfileExtractionResult:
    """Track 1 单次调用的产出。

    ``experiences`` 与 ``items`` 同源：``SEMANTIC_EXTRACTION_PROMPT`` 是双轨输出，
    一次调用同时返回 ``memories`` 与 ``experiences``，拆开解析可免第二次 LLM 往返。

    ``error`` 非空表示这一路失败（调用异常 / JSON 不可解析），供调用方标记
    ``failed_tracks``。无内容（空响应或 "NONE"）不算失败，返回全空且 ``error=None``。
    """

    items: list[ProfileItem] = field(default_factory=list)
    experiences: list[ProfileItem] = field(default_factory=list)
    scores: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


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
        *,
        prompt_messages: list[dict[str, Any]] | None = None,
        timeout: float | None = None,
    ) -> ProfileExtractionResult:
        """抽取画像（+ 经验 + 引用评分）。

        Args:
            transcript: 对话转录；仅当 ``prompt_messages`` 缺省时用于自行拼 prompt。
            episode_id: 关联的情节 id（当前仅透传，不参与 prompt）。
            cited_memories: 本次检索注入的历史记忆 ``[{id, content}]``；非空时追加
                引用评分段。
            prompt_messages: 调用方已拼好的 prompt。传入时**原样使用**，不再自行拼接
                —— 让调用方复用带阶段1 系统提取结果的 prompt，避免两套拼装逻辑分叉。
            timeout: 单次 LLM 调用超时（秒）。``None`` 表示不额外设限。调用方应传入
                与 ``_call_track`` 一致的 ``LLM_TIMEOUT``，避免悬挂调用拖死整条流水线。

        Returns:
            :class:`ProfileExtractionResult`。失败隔离：任何异常仅 warning，
            ``error`` 记录原因，绝不上抛。
        """
        if self._runtime is None:
            return ProfileExtractionResult()
        try:
            if prompt_messages is not None:
                messages = _append_citation_section(prompt_messages, cited_memories)
            else:
                conv_text = _format_conv_lines(transcript)
                # SEMANTIC_EXTRACTION_PROMPT 为指令式，无 {conversation} 占位符
                prompt = f"{SEMANTIC_EXTRACTION_PROMPT}\n\n### 对话转录\n{conv_text}"
                if cited_memories:
                    prompt += CITATION_SCORING_SECTION.format(
                        cited_memories=_cite_text(cited_memories)
                    )
                messages = [{"role": "user", "content": prompt}]

            call = self._runtime.provider.chat_with_retry(
                model=self._model or getattr(self._runtime, "model", ""),
                messages=messages,
                tools=[],
                temperature=getattr(self._runtime.generation, "temperature", 0.0),
                max_tokens=getattr(self._runtime.generation, "max_tokens", 1000),
                reasoning_effort=getattr(self._runtime.generation, "reasoning_effort", None),
            )
            resp = await asyncio.wait_for(call, timeout=timeout) if timeout else await call
            text = (getattr(resp, "content", "") or "").strip()
            if not text or text.upper() == "NONE":
                return ProfileExtractionResult()
            data = _extract_json_obj(text)
            if not isinstance(data, dict):
                # 兼容旧式「纯 JSON 数组」输出；两者都解析不出才算解析失败，
                # 需与 ``_call_track`` 的 ``invalid_json`` 语义保持一致。
                legacy = _parse_legacy_array(text)
                if legacy:
                    return ProfileExtractionResult(items=legacy)
                return ProfileExtractionResult(error="invalid_json")
            return ProfileExtractionResult(
                items=_parse_items(data.get("memories")),
                experiences=_parse_items(data.get("experiences")),
                scores=_parse_scores(data.get("citation_scores")),
            )
        except Exception as exc:
            logger.warning("ProfileExtractor.extract failed: {}", exc)
            return ProfileExtractionResult(error=f"call_failed: {exc}")


def _cite_text(cited_memories: list[dict]) -> str:
    return "\n".join(
        f"- ID={m['id']} | {(m.get('content', '') or '')[:150]}" for m in cited_memories
    )


def _append_citation_section(
    prompt_messages: list[dict[str, Any]],
    cited_memories: list[dict] | None,
) -> list[dict[str, Any]]:
    """把引用评分段追加到最后一条 user 消息尾部；无 cited 时原样返回副本。"""
    if not cited_memories:
        return prompt_messages
    section = CITATION_SCORING_SECTION.format(cited_memories=_cite_text(cited_memories))
    out = [dict(m) for m in prompt_messages]
    for msg in reversed(out):
        if msg.get("role") == "user" and isinstance(msg.get("content"), str):
            msg["content"] = msg["content"] + section
            break
    return out


def _parse_items(raw_items: Any) -> list[ProfileItem]:
    if not isinstance(raw_items, list):
        return []
    return [_item_from_dict(i) for i in raw_items if isinstance(i, dict)]


def _item_from_dict(raw: dict[str, Any]) -> ProfileItem:
    raw_priority = raw.get("priority")
    return ProfileItem(
        content=str(raw.get("content", "")),
        subject=str(raw.get("subject", "")),
        predicate=str(raw.get("predicate", "")),
        type=str(raw.get("type", "FACT")).upper(),
        importance=float(raw.get("importance", 0.5)),
        priority=str(raw_priority).lower() if raw_priority else None,
        tags=list(raw.get("tags") or []),
        is_update=bool(raw.get("is_update", False)),
    )


def _parse_scores(raw_scores: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_scores, list):
        return []
    return [
        {"memory_id": str(s["memory_id"]), "useful": bool(s.get("useful", False))}
        for s in raw_scores
        if isinstance(s, dict) and "memory_id" in s
    ]

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
    return [_item_from_dict(i) for i in arr if isinstance(i, dict)]
