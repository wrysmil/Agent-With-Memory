"""Phase 2 记忆提取器：从 session.messages 提取 memories / episodes / scratchpad。

设计依据：`docs/记忆系统/plan/阶段二设计_记忆提取.md` §3.1（提取流水线）、
§4（提示词）、§5.2-5.4（防污染）、§7.4（类签名）、§8.1-8.5（数据契约）。

流水线四阶段：
    阶段1 ``_system_extract``   纯结构化解析（ActionNode / 规则信号 / scratchpad 快照）
    阶段2 ``_llm_extract``      并发两路 LLM（语义 + 情节），失败隔离
    阶段3 ``_apply_filters``    防污染 + 去重（任务产物 / AI 自答 / 精确哈希 / N-Gram）
    阶段4 ``_persist``          写 SQLite（memories → episode 反向关联 → 逐条失败隔离）

本模块不修改 repository / models / filters / prompts，只通过它们的公开 API 协作。
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar
from uuid import uuid4

from loguru import logger

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.filters import (
    compute_content_hash,
    is_ai_self_talk,
    is_task_artifact,
    ngram_similarity,
)
from nanobot.memory.models import (
    Episode,
    EpisodeOutcome,
    EpisodeSource,
    Memory,
    MemoryPriority,
    MemoryType,
    ScratchpadEntry,
)
from nanobot.memory.prompts import (
    EPISODE_EXTRACTION_PROMPT,
    SEMANTIC_EXTRACTION_PROMPT,
)
from nanobot.memory.repository import (
    add_episode,
    add_memory,
    get_scratchpad,
    list_memories,
    update_memory_source_episode,
)

if TYPE_CHECKING:
    from nanobot.session.manager import Session
    from nanobot.utils.llm_runtime import LLMRuntime


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 规则信号关键词（Quick Facts 正则，plan §3.1 阶段1 / §10 Task 9）
_RULE_SIGNAL_RE = re.compile(r"必须|每次|总是|一律|务必|不要|禁止|记住|以后都|永远")

# Quick Facts 候选（规则信号）类型：统一落为 RULE
_QUICK_FACT_TYPE = MemoryType.RULE.value

# 句子切分（用于抽取命中的规则片段）
_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?\n；;]+")

# priority 兜底的时间关键词（plan §8.2）
_SHORT_TERM_KEYWORDS = ("今天", "这周", "本月", "暂时", "先")

# 合法枚举值
_VALID_TYPES = frozenset(t.value for t in MemoryType)
_VALID_PRIORITIES = frozenset(p.value for p in MemoryPriority)

# L2 去重阈值（plan §5.2 防线3）
_HIGH_SIMILARITY = 0.8

# FIX-3 Sec-M-1: ActionNode 输入/输出截断阈值（防凭据外发注入攻击）
ACTION_NODE_INPUT_MAX_CHARS = 512
ACTION_NODE_OUTPUT_MAX_CHARS = 2048

# FIX-3 Sec-M-1: 凭据/密钥/令牌脱敏模式；命中即替换为占位符，
# 然后再按上面的 OUTPUT_MAX_CHARS 截断。
_REDACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # ``key=value`` / ``key: value`` / ``"key": "value"``（JSON 风格）均匹配
    (re.compile(
        r"(?i)(?P<k>api[_-]?key|secret|password|token)\s*[\"']?\s*[=:]\s*[\"']?\s*\S+"
    ), "<redacted>"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"), "Bearer <redacted>"),
    (re.compile(
        r"(?i)(aws[_-]?access[_-]?key[_-]?id|aws[_-]?secret)\s*[\"']?\s*[=:]\s*[\"']?\s*\S+"
    ), "<redacted>"),
]

# WU-1 head+tail 双向保留（plan §3 WU-1 / 参考 OpenAkita smart_truncate）：
# OpenAkita 用 65% head，我们调成 40% head：开篇任务定义 + 结尾最新进展，
# 对 LLM 抽取语义/情节记忆的覆盖率更高。head_ratio + tail_ratio 之和 < 1.0
# （给 marker 留 ~15% 空间）。
_TRANSCRIPT_HEAD_RATIO: float = 0.4
_TRANSCRIPT_TAIL_RATIO: float = 0.45
_TRANSCRIPT_TRUNCATE_MARKER = "\n...[中段已截断,完整对话已被压缩或超长]...\n"

# FIX-4 Sec-M-2: 数值与字符串字段值域校验（防 LLM 输出 NaN/Inf / 巨型字符串）
CONTENT_MAX_CHARS = 8192
TAGS_MAX_ITEMS = 32
SUBJECT_MAX_CHARS = 256
PREDICATE_MAX_CHARS = 256
DEFAULT_IMPORTANCE = 0.7

# source 字符串 → EpisodeSource 映射（FIX-2 Sec-C-β：``deletion`` 现在有专属枚举，
# 不再折叠到 SESSION_END；保留 provenance 用于审计 / 调优）
_SOURCE_MAP: dict[str, EpisodeSource] = {
    "session_end": EpisodeSource.SESSION_END,
    "context_compress": EpisodeSource.CONTEXT_COMPRESS,
    "topic_change": EpisodeSource.TOPIC_CHANGE,
    "deletion": EpisodeSource.DELETION,
    "daily_consolidation": EpisodeSource.DAILY_CONSOLIDATION,
}


# ---------------------------------------------------------------------------
# 数据类（plan §7.4 / §8.4）
# ---------------------------------------------------------------------------


@dataclass
class ActionNode:
    """系统提取的单个工具调用节点。"""

    # FIX-3 Sec-M-1: 输入/输出大小上限（脱敏后截断）
    INPUT_MAX_CHARS: ClassVar[int] = ACTION_NODE_INPUT_MAX_CHARS
    OUTPUT_MAX_CHARS: ClassVar[int] = ACTION_NODE_OUTPUT_MAX_CHARS

    tool: str
    input: str = ""
    output: str = ""
    success: bool = True
    duration_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "input": self.input,
            "output": self.output,
            "success": self.success,
            "duration_ms": self.duration_ms,
        }


@dataclass
class SystemExtractionResult:
    """阶段1 系统提取结果（无需 LLM）。"""

    action_nodes: list[ActionNode] = field(default_factory=list)
    rule_signals: list[str] = field(default_factory=list)
    scratchpad_snapshot: ScratchpadEntry | None = None


@dataclass
class LLMMemoryItem:
    """语义 LLM 输出的单条记忆候选（memories 与 experiences 合并后同构）。"""

    content: str
    type: str
    priority: str | None = None
    importance: float = 0.7
    subject: str = ""
    predicate: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class LLMEpisodeItem:
    """情节 LLM 输出的 Episode 候选。"""

    summary: str = ""
    goal: str = ""
    outcome: str | None = None
    entities: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    importance: float = 0.7


@dataclass
class LLMExtractionResult:
    """阶段2 LLM 提取结果。

    ``action_nodes`` 承载阶段1 的系统提取结果，用于按 §7.4 的
    ``_apply_filters(llm_result, existing_memories)`` 签名把 ActionNode 传递到阶段3/4。
    """

    memories: list[LLMMemoryItem] = field(default_factory=list)
    episode: LLMEpisodeItem | None = None
    failed_tracks: list[str] = field(default_factory=list)
    action_nodes: list[ActionNode] = field(default_factory=list)


@dataclass
class FilteredExtractionResult:
    """阶段3 过滤后的结果。"""

    memories: list[LLMMemoryItem] = field(default_factory=list)
    episode: LLMEpisodeItem | None = None
    action_nodes: list[ActionNode] = field(default_factory=list)


@dataclass
class PersistenceResult:
    """阶段4 写入结果。"""

    memory_ids: list[str] = field(default_factory=list)
    episode_ids: list[str] = field(default_factory=list)


@dataclass
class ExtractionResult:
    """``extract_session`` 的对外返回。"""

    memory_ids: list[str] = field(default_factory=list)
    episode_ids: list[str] = field(default_factory=list)
    skipped: int = 0
    failed_tracks: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 纯函数工具
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _content_text(content: Any) -> str:
    """把消息 content（str / content-block list / 其他）归一化为纯文本。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(block, str):
                parts.append(block)
        return " ".join(parts)
    return str(content)


def _looks_like_error(output: str) -> bool:
    low = output.lower()
    return "error" in low or "traceback" in low


def _resolve_action_success(output: str, has_result: bool) -> bool:
    """判定 ActionNode 是否执行成功。

    Args:
        output: 工具返回的 output 文本（已归一化）。
        has_result: 是否在 tool 消息中找到了对应 ``tool_call_id`` 的结果。

    Returns:
        True 表示成功,False 表示失败。

    孤儿语义：当 assistant 发出了 tool_call 但没有对应的 tool 结果消息
    时,视为执行失败（``has_result=False``）,避免把"调用了但没收到结果"
    的工具调用当作"成功调用"写入 episode。
    """
    if not has_result:
        return False
    return not _looks_like_error(output)


def _extract_tool_call(call: Any) -> tuple[str, str, str]:
    """探测 tool_call 结构，返回 ``(tool_name, arguments_str, call_id)``。"""
    if not isinstance(call, dict):
        return "", "", ""
    func = call.get("function")
    func_map: dict[str, Any] = func if isinstance(func, dict) else {}
    name = call.get("name") or func_map.get("name") or ""
    args = call.get("arguments")
    if args is None:
        args = func_map.get("arguments")
    if isinstance(args, str):
        args_str = args
    elif args is None:
        args_str = ""
    else:
        args_str = json.dumps(args, ensure_ascii=False)
    call_id = call.get("id") or ""
    return str(name), args_str, str(call_id)


def _redact(text: str) -> str:
    """FIX-3 Sec-M-1：用占位符替换常见凭据 / 密钥 / 令牌模式。"""
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _truncate(text: str, limit: int) -> str:
    """FIX-3 Sec-M-1：按 ``limit`` 截断字符串并附加 truncated 标记。"""
    if len(text) <= limit:
        return text
    extra = len(text) - limit
    return text[:limit] + f"...[truncated {extra} chars]"


def _collect_action_nodes(messages: list[dict[str, Any]]) -> list[ActionNode]:
    """扫描 assistant 消息中的 tool_calls，结合 tool 结果消息生成 ActionNode。

    FIX-3 Sec-M-1：构造前对 input/output 先脱敏，再按 INPUT_MAX_CHARS /
    OUTPUT_MAX_CHARS 截断，防止凭据随 action_nodes 持久化进入 episode。
    """
    results: dict[str, str] = {}
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        tid = msg.get("tool_call_id")
        if tid:
            results[str(tid)] = _content_text(msg.get("content"))

    nodes: list[ActionNode] = []
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        calls = msg.get("tool_calls")
        if not isinstance(calls, list):
            continue
        for call in calls:
            name, args, call_id = _extract_tool_call(call)
            if not name:
                continue
            has_result = call_id in results
            output = results.get(call_id, "")
            nodes.append(
                ActionNode(
                    tool=name,
                    input=_truncate(_redact(args), ActionNode.INPUT_MAX_CHARS),
                    output=_truncate(_redact(output), ActionNode.OUTPUT_MAX_CHARS),
                    success=_resolve_action_success(output, has_result),
                )
            )
    return nodes


def _collect_rule_signals(messages: list[dict[str, Any]]) -> list[str]:
    """扫描 user 消息，收集命中规则关键词的句子片段。"""
    signals: list[str] = []
    for msg in messages:
        if msg.get("role") != "user":
            continue
        text = _content_text(msg.get("content")).strip()
        if not text:
            continue
        for sentence in _SENTENCE_SPLIT_RE.split(text):
            fragment = sentence.strip()
            if fragment and _RULE_SIGNAL_RE.search(fragment):
                signals.append(fragment)
    return signals


def _parse_json_object(text: str) -> dict[str, Any] | None:
    """容忍 ```json 围栏地从 LLM 输出中解析 JSON 对象；失败返回 None。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline != -1:
            cleaned = cleaned[first_newline + 1:]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    cleaned = cleaned.strip()
    for candidate in _json_candidates(cleaned):
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _json_candidates(text: str) -> list[str]:
    candidates = [text]
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        inner = text[start:end + 1]
        if inner != text:
            candidates.append(inner)
    return candidates


def _coerce_memory_item(raw: Any) -> LLMMemoryItem | None:
    if not isinstance(raw, dict):
        return None
    # FIX-4 Sec-M-2：content 长度上限 + 截断标记（防 LLM 输出巨型字符串）
    raw_content = str(raw.get("content") or "").strip()
    if not raw_content:
        return None
    if len(raw_content) > CONTENT_MAX_CHARS:
        content = raw_content[:CONTENT_MAX_CHARS] + "...[truncated]"
    else:
        content = raw_content

    priority_raw = raw.get("priority")
    priority = str(priority_raw).strip() if priority_raw is not None else None

    # FIX-4 Sec-M-2：importance 必须为有限数并位于 [0,1]；否则回落默认值
    importance_raw = raw.get("importance", DEFAULT_IMPORTANCE)
    try:
        importance_value = float(importance_raw)
    except (TypeError, ValueError):
        importance_value = DEFAULT_IMPORTANCE
    if not math.isfinite(importance_value) or not (0.0 <= importance_value <= 1.0):
        importance_value = DEFAULT_IMPORTANCE
    importance = min(max(importance_value, 0.0), 1.0)

    tags_raw = raw.get("tags")
    if isinstance(tags_raw, list):
        tags_all = [str(t) for t in tags_raw]
    else:
        tags_all = []
    # FIX-4 Sec-M-2：tags 条数上限（防 LLM 输出巨型标签列表）
    tags = tags_all[:TAGS_MAX_ITEMS]

    # FIX-4 Sec-M-2：subject / predicate 长度上限
    subject_raw = str(raw.get("subject") or "").strip()
    predicate_raw = str(raw.get("predicate") or "").strip()
    subject = subject_raw[:SUBJECT_MAX_CHARS]
    predicate = predicate_raw[:PREDICATE_MAX_CHARS]

    return LLMMemoryItem(
        content=content,
        type=str(raw.get("type") or "").strip(),
        priority=priority or None,
        importance=importance,
        subject=subject,
        predicate=predicate,
        tags=tags,
    )


def _coerce_episode_item(raw: dict[str, Any]) -> LLMEpisodeItem:
    outcome_raw = raw.get("outcome")
    entities_raw = raw.get("entities")
    tools_raw = raw.get("tools_used")
    try:
        importance = float(raw.get("importance", 0.7))
    except (TypeError, ValueError):
        importance = 0.7
    return LLMEpisodeItem(
        summary=str(raw.get("summary") or "").strip(),
        goal=str(raw.get("goal") or "").strip(),
        outcome=str(outcome_raw).strip() if outcome_raw is not None else None,
        entities=[str(e) for e in entities_raw] if isinstance(entities_raw, list) else [],
        tools_used=[str(t) for t in tools_raw] if isinstance(tools_raw, list) else [],
        importance=importance,
    )


def _coerce_memory_items(raw_items: Any) -> list[LLMMemoryItem]:
    if not isinstance(raw_items, list):
        return []
    items: list[LLMMemoryItem] = []
    for raw in raw_items:
        item = _coerce_memory_item(raw)
        if item is not None:
            items.append(item)
    return items


def _normalize_outcome(raw: str | None) -> EpisodeOutcome:
    """归一化 episode outcome（plan §8.4.1）。非法值降级 completed 并 warning。"""
    if raw is None:
        return EpisodeOutcome.COMPLETED
    mapping = {"success": "completed"}
    value = mapping.get(raw, raw)
    try:
        return EpisodeOutcome(value)
    except ValueError:
        logger.warning("invalid episode outcome {!r}, fallback to completed", raw)
        return EpisodeOutcome.COMPLETED


def _resolve_priority(item: LLMMemoryItem) -> MemoryPriority:
    """按 §8.2 解析 priority；缺失或（兜底）非法时按规则推断。

    注意：非法值在阶段3 ``_apply_filters`` 已被跳过，此处仅做最终兜底。
    """
    raw = (item.priority or "").strip().lower()
    if raw in _VALID_PRIORITIES:
        return MemoryPriority(raw)
    if raw:
        logger.warning("invalid memory priority {!r}, applying fallback rules", item.priority)
    if item.type.strip().lower() == MemoryType.ERROR.value:
        return MemoryPriority.LONG_TERM
    if any(keyword in item.content for keyword in _SHORT_TERM_KEYWORDS):
        return MemoryPriority.SHORT_TERM
    return MemoryPriority.LONG_TERM


def _render_scratchpad(snapshot: ScratchpadEntry | None) -> str:
    """把 scratchpad 快照渲染为 JSON 文本，供 LLM 调用输入（§3.1）。"""
    if snapshot is None:
        return "{}"
    return json.dumps(
        {
            "current_focus": snapshot.current_focus,
            "active_projects": snapshot.active_projects,
            "open_questions": snapshot.open_questions,
            "next_steps": snapshot.next_steps,
        },
        ensure_ascii=False,
    )


def _render_transcript(
    messages: list[dict[str, Any]],
    max_chars: int = 8000,
    *,
    head_ratio: float = _TRANSCRIPT_HEAD_RATIO,
    tail_ratio: float = _TRANSCRIPT_TAIL_RATIO,
) -> str:
    """渲染 user/assistant 文本转录，超长时 head + tail 双向保留。

    短对话（``<= max_chars``）原样返回，不做任何截断。
    长对话保留头部 ``head_ratio`` + 尾部 ``tail_ratio``，中间插入
    ``_TRANSCRIPT_TRUNCATE_MARKER``，让 LLM 既能看到开篇任务定义，
    又能看到结尾最新进展，覆盖率显著高于纯 tail 截断。

    参考 OpenAkita ``smart_truncate``（truncate.py:60-82），但更简单、无 sidecar
    文件机制（本路径无 LLM 工具调用回读能力）。

    :param head_ratio: 头部比例，默认 0.4。会被裁剪到 ``[0.0, 1.0]``。
    :param tail_ratio: 尾部比例，默认 0.45。会被裁剪到 ``[0.0, 1.0]``。
        若 ``head_ratio + tail_ratio > 1.0``，自动按比例缩放，确保不溢出。
    """
    head_ratio = max(0.0, min(1.0, float(head_ratio)))
    tail_ratio = max(0.0, min(1.0, float(tail_ratio)))
    if head_ratio + tail_ratio > 1.0:
        scale = 1.0 / (head_ratio + tail_ratio)
        head_ratio *= scale
        tail_ratio *= scale

    lines: list[str] = []
    for msg in messages:
        role = str(msg.get("role") or "")
        if role not in ("user", "assistant"):
            continue
        text = _content_text(msg.get("content")).strip()
        if not text:
            continue
        lines.append(f"[{role}] {text}")
    joined = "\n".join(lines)
    if len(joined) <= max_chars:
        return joined
    head_chars = int(max_chars * head_ratio)
    tail_chars = int(max_chars * tail_ratio)
    return joined[:head_chars] + _TRANSCRIPT_TRUNCATE_MARKER + joined[-tail_chars:]


# ---------------------------------------------------------------------------
# MemoryExtractor
# ---------------------------------------------------------------------------


class MemoryExtractor:
    """从 ``session.messages`` 提取三层记忆的核心类（plan §7.4）。"""

    #: 单路 LLM 调用的超时（秒）；测试可覆盖实例属性以缩短超时。
    LLM_TIMEOUT: float = 30.0

    #: 阶段3 预载同类型历史记忆的窗口大小（plan §5.2 比对集来源）。
    EXISTING_MEMORY_LIMIT: int = 500

    def __init__(
        self,
        database: MemoryDatabase,
        runtime: LLMRuntime,
        workspace_id: str = "default",
        user_id: str = "default",
    ) -> None:
        self.database = database
        self.runtime = runtime
        self.workspace_id = workspace_id
        self.user_id = user_id
        # WU-3B: fallback 目录。若 database 暴露了 fallback_dir(B 节),复用;
        # 否则回退到 db_path.parent/_memory_fallback,确保 _safe_write_with_fallback
        # 始终有可写目录。database 可能为 None(generate_episode 路径下),此时
        # 不创建 fallback_dir,运行时再兜底。
        self.fallback_dir: Path | None = None
        if database is not None:
            db_fb = getattr(database, "fallback_dir", None)
            if isinstance(db_fb, Path):
                self.fallback_dir = db_fb
            else:
                db_path = getattr(database, "db_path", None)
                if db_path is not None:
                    self.fallback_dir = Path(db_path).parent / "_memory_fallback"
            if self.fallback_dir is not None:
                try:
                    self.fallback_dir.mkdir(parents=True, exist_ok=True)
                except Exception as exc:  # noqa: BLE001 - fallback 目录建不出来不能阻断 extractor
                    logger.warning("memory fallback dir unavailable ({}): {}", self.fallback_dir, exc)
            # WU-3B: 启动时重放上轮未写入的 fallback 队列。任何异常仅 warning,
            # 不影响 extractor 主流程。
            replay_fn = getattr(database, "replay_fallback", None)
            if callable(replay_fn):
                try:
                    replay_fn()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("memory fallback replay skipped on init: {}", exc)

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    async def extract_session(
        self,
        session: Session,
        *,
        source: str = "session_end",
    ) -> ExtractionResult:
        """执行完整 4 阶段提取，返回写入统计。"""
        # 系统性读取
        system = self._system_extract(session)

        # LLM 抽取
        llm_result = await self._llm_extract(session, system)

        existing_memories = self._load_existing_memories(llm_result.memories)

        # 去重
        filtered = self._apply_filters(llm_result, existing_memories)
        persisted = self._persist(filtered, session, source)
        skipped = len(llm_result.memories) - len(filtered.memories)
        return ExtractionResult(
            memory_ids=persisted.memory_ids,
            episode_ids=persisted.episode_ids,
            skipped=skipped,
            failed_tracks=list(llm_result.failed_tracks),
        )

    # ------------------------------------------------------------------
    # Quick Facts：压缩后规则信号提取（无需 LLM；plan §10 Task 9）
    # ------------------------------------------------------------------

    def extract_quick_facts(self, session: Session) -> int:
        """从 ``session.messages`` 用正则抽取「规则信号」类记忆（不调 LLM）。

        只扫描 user 消息，命中的句子落为 ``RULE``，priority 走 §8.2 兜底
        （``_resolve_priority``：含时间关键词 → short_term，否则 long_term）。
        去重与落库复用阶段3/4 的 ``_apply_filters`` + ``_persist``，不另写 INSERT。

        Returns:
            实际写入的 memory 条数；无命中或任何异常时返回 0（异常仅 warning）。
        """
        try:
            fragments = _collect_rule_signals(session.messages) if session.messages else []
            if not fragments:
                return 0
            candidates = [
                LLMMemoryItem(content=fragment, type=_QUICK_FACT_TYPE)
                for fragment in fragments
            ]
            existing = self._load_existing_memories(candidates)
            filtered = self._apply_filters(
                LLMExtractionResult(memories=candidates),
                existing,
            )
            if not filtered.memories:
                return 0
            # 只填 memories（无 episode / action_nodes），故 _persist 仅写 memories，
            # source 仅用于（可能存在的）episode 映射，此处取压缩触发语义。
            persisted = self._persist(filtered, session, source="context_compress")
            return len(persisted.memory_ids)
        except Exception as exc:  # noqa: BLE001 - 快速事实失败绝不能上抛
            logger.opt(exception=exc).warning("quick facts extraction failed: {}", exc)
            return 0

    async def extract_incremental(
        self,
        session: Session,
        last_extracted_index: int,
    ) -> ExtractionResult:
        """话题切换触发的轻量增量抽取（只扫新消息，不调 LLM）。

        ``session.messages[last_extracted_index:]`` 视为新增消息，仅走阶段1 的规则
        信号抽取，去重与落库复用 ``_load_existing_memories`` + ``_apply_filters``
        + ``_persist``。source 固定为 ``topic_change``，用于 episode 映射审计。
        失败隔离：任何异常仅 warning，并返回空 :class:`ExtractionResult`。
        """
        try:
            if not session.messages:
                return ExtractionResult()
            start = max(0, last_extracted_index)
            new_messages = session.messages[start:]
            if not new_messages:
                return ExtractionResult()

            fragments = _collect_rule_signals(new_messages)
            if not fragments:
                return ExtractionResult()
            candidates = [
                LLMMemoryItem(content=fragment, type=_QUICK_FACT_TYPE)
                for fragment in fragments
            ]
            existing = self._load_existing_memories(candidates)
            filtered = self._apply_filters(
                LLMExtractionResult(memories=candidates),
                existing,
            )
            persisted = self._persist(filtered, session, source="topic_change")
            return ExtractionResult(
                memory_ids=persisted.memory_ids,
                episode_ids=persisted.episode_ids,
                skipped=len(candidates) - len(filtered.memories),
            )
        except Exception as exc:  # noqa: BLE001 - 增量抽取失败绝不能上抛
            logger.opt(exception=exc).warning("incremental extraction failed: {}", exc)
            return ExtractionResult()

    # ------------------------------------------------------------------
    # 阶段1：系统提取（无 LLM）
    # ------------------------------------------------------------------

    def _system_extract(self, session: Session) -> SystemExtractionResult:
        if not session.messages:
            return SystemExtractionResult()
        # 工具
        action_nodes = _collect_action_nodes(session.messages)
        # 对话内容
        rule_signals = _collect_rule_signals(session.messages)
        with self.database.connect() as conn:
            # 快照
            snapshot = get_scratchpad(conn, self.user_id, self.workspace_id)
        return SystemExtractionResult(
            action_nodes=action_nodes,
            rule_signals=rule_signals,
            scratchpad_snapshot=snapshot,
        )

    # ------------------------------------------------------------------
    # 阶段2：LLM 提取（并发 + 失败隔离）
    # ------------------------------------------------------------------

    async def _llm_extract(
        self,
        session: Session,
        system: SystemExtractionResult,
    ) -> LLMExtractionResult:
        semantic_messages = self._build_prompt_messages(
            SEMANTIC_EXTRACTION_PROMPT, session, system
        )
        episode_messages = self._build_prompt_messages(
            EPISODE_EXTRACTION_PROMPT, session, system
        )

        raw = await asyncio.gather(
            self._call_track("semantic", semantic_messages),
            self._call_track("episode", episode_messages),
            return_exceptions=True,
        )

        failed_tracks: list[str] = []
        semantic_payload: dict[str, Any] | None = None
        episode_payload: dict[str, Any] | None = None

        for track, result in zip(("semantic", "episode"), raw):
            if isinstance(result, BaseException):
                failed_tracks.append(track)
                logger.warning("memory extraction track {} raised: {}", track, result)
                continue
            payload, error = result
            if error is not None:
                failed_tracks.append(track)
            if payload is None:
                continue
            if track == "semantic":
                semantic_payload = payload
            else:
                episode_payload = payload

        memories: list[LLMMemoryItem] = []
        if semantic_payload is not None:
            memories.extend(_coerce_memory_items(semantic_payload.get("memories")))
            memories.extend(_coerce_memory_items(semantic_payload.get("experiences")))

        episode = _coerce_episode_item(episode_payload) if episode_payload is not None else None

        return LLMExtractionResult(
            memories=memories,
            episode=episode,
            failed_tracks=failed_tracks,
            action_nodes=list(system.action_nodes),
        )

    async def _call_track(
        self,
        track: str,
        messages: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, str | None]:
        """调用单路 LLM 并解析 JSON。

        Returns:
            ``(payload, error)``：成功为 (dict, None)，无内容为 (None, None)，
            失败为 (None, <原因>)。
        """
        try:
            response = await asyncio.wait_for(
                self.runtime.provider.chat_with_retry(
                    model=self.runtime.model,
                    messages=messages,
                    tools=[],
                    temperature=self.runtime.generation.temperature,
                    max_tokens=self.runtime.generation.max_tokens,
                    reasoning_effort=self.runtime.generation.reasoning_effort,
                ),
                timeout=self.LLM_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001 - 失败隔离：任何异常都不上抛
            logger.warning("memory extraction LLM call failed ({}): {}", track, exc)
            return None, f"call_failed: {exc}"

        content = getattr(response, "content", None)
        if not content or not content.strip():
            return None, None
        if content.strip().upper() == "NONE":
            return None, None
        payload = _parse_json_object(content)
        if payload is None:
            logger.warning("memory extraction LLM returned unparseable JSON ({})", track)
            return None, "invalid_json"
        return payload, None

    def _build_prompt_messages(
        self,
        prompt: str,
        session: Session,
        system: SystemExtractionResult,
        *,
        transcript_max_chars: int = 8000,
        transcript_head_ratio: float = _TRANSCRIPT_HEAD_RATIO,
    ) -> list[dict[str, Any]]:
        transcript = _render_transcript(
            session.messages,
            max_chars=transcript_max_chars,
            head_ratio=transcript_head_ratio,
        )
        action_summary = json.dumps(
            [node.to_dict() for node in system.action_nodes], ensure_ascii=False
        )
        rules = json.dumps(system.rule_signals, ensure_ascii=False)
        scratchpad_text = _render_scratchpad(system.scratchpad_snapshot)
        body = (
            f"{prompt}\n\n"
            f"## 对话内容\n\n{transcript}\n\n"
            f"## 阶段1 系统提取结果（参考用，可忽略）\n\n"
            f"- 工具调用摘要: {action_summary}\n"
            f"- 规则信号命中: {rules}\n"
            f"- 当前 scratchpad 快照: {scratchpad_text}"
        )
        return [{"role": "user", "content": body}]

    # ------------------------------------------------------------------
    # 阶段3：防污染过滤
    # ------------------------------------------------------------------

    def _load_existing_memories(self, candidates: list[LLMMemoryItem]) -> list[Memory]:
        types: set[str] = set()
        for item in candidates:
            type_key = item.type.strip().lower()
            if type_key in _VALID_TYPES:
                types.add(type_key)

        existing: list[Memory] = []
        if not types:
            return existing
        with self.database.connect() as conn:
            for type_key in types:
                existing.extend(
                    list_memories(
                        conn,
                        type=MemoryType(type_key),
                        workspace_id=self.workspace_id,
                        limit=self.EXISTING_MEMORY_LIMIT,
                    )
                )
        return existing

    def _apply_filters(
        self,
        llm_result: LLMExtractionResult,
        existing_memories: list[Memory],
    ) -> FilteredExtractionResult:
        existing_hashes = {
            compute_content_hash(m.content, m.subject, m.predicate) for m in existing_memories
        }
        existing_by_type: dict[str, list[Memory]] = {}
        for memory in existing_memories:
            existing_by_type.setdefault(memory.type.value, []).append(memory)

        kept: list[LLMMemoryItem] = []
        seen_hashes: set[str] = set()
        for item in llm_result.memories:
            content = item.content
            if is_task_artifact(content) or is_ai_self_talk(content):
                continue

            type_key = item.type.strip().lower()
            if type_key not in _VALID_TYPES:
                logger.warning("skipping memory with invalid type {!r}", item.type)
                continue

            priority_raw = (item.priority or "").strip().lower()
            if priority_raw and priority_raw not in _VALID_PRIORITIES:
                logger.warning("skipping memory with invalid priority {!r}", item.priority)
                continue

            content_hash = compute_content_hash(content, item.subject, item.predicate)
            if content_hash in existing_hashes or content_hash in seen_hashes:
                continue

            if self._has_high_similarity(content, type_key, existing_by_type):
                continue

            seen_hashes.add(content_hash)
            kept.append(item)

        return FilteredExtractionResult(
            memories=kept,
            episode=llm_result.episode,
            action_nodes=list(llm_result.action_nodes),
        )

    @staticmethod
    def _has_high_similarity(
        content: str,
        type_key: str,
        existing_by_type: dict[str, list[Memory]],
    ) -> bool:
        for memory in existing_by_type.get(type_key, []):
            if ngram_similarity(content, memory.content) > _HIGH_SIMILARITY:
                return True
        return False

    # ------------------------------------------------------------------
    # 阶段4：持久化
    # ------------------------------------------------------------------

    def _safe_write_with_fallback(
        self,
        conn: Any,
        *,
        kind: str,
        item: dict[str, Any],
        writer: Any,
    ) -> bool:
        """执行单条 DB 写入;失败时把 payload 落 ``fallback_dir`` 等下次重放。

        Args:
            conn: 当前事务内的 ``sqlite3.Connection``(由 ``_persist`` 的
                ``with self.database.connect() as conn:`` 提供)。
            kind: ``"memory"`` / ``"episode"`` / ``"backfill"``。
            item: 载荷 dict(对应 ``Memory.to_row()`` / ``Episode.to_row()`` 或
                backfill 的 ``{memory_id, episode_id}``)。
            writer: 真正写入 DB 的可调用对象,签名 ``writer(conn) -> None``,
                抛任意异常即视为写入失败。

        Returns:
            True 表示实际写入 DB 成功,False 表示已落 fallback 文件(或 fallback
            也失败,仅 warning)。调用方据此决定是否把 id 计入
            ``saved_memory_ids`` / ``saved_episode_ids``。
        """
        try:
            writer(conn)
            return True
        except Exception as exc:  # noqa: BLE001 - 失败隔离
            # MAJOR #1: 失败后 rollback 撤销未提交写入,避免事务残留污染后续操作。
            # rollback 自身失败必须吞掉,fallback 是兜底,绝不能被 rollback 阻断。
            try:
                conn.rollback()
            except Exception:
                logger.debug("rollback after failed write also failed (ignored)")
            fallback_dir = self.fallback_dir
            if fallback_dir is None:
                logger.warning(
                    "memory write failed and no fallback_dir configured: {} (kind={})",
                    exc,
                    kind,
                )
                return False
            try:
                ts = _now_iso()
                uid = uuid.uuid4().hex[:8]
                safe_kind = str(kind).replace("/", "_").replace("\\", "_")
                path = fallback_dir / f"{ts}_{safe_kind}_{uid}.json"
                fallback_dir.mkdir(parents=True, exist_ok=True)
                # MAJOR #2 / OWASP LLM05: 落盘前对文本字段 redact,防止凭据
                # 残留到 fallback JSON 中。只处理 content / subject / predicate
                # 三个文本字段(tags 数组不在本改动范围);用 isinstance 守卫保证
                # 缺字段/非字符串时原样保留。
                payload_item = dict(item)
                for text_key in ("content", "subject", "predicate"):
                    value = payload_item.get(text_key)
                    if isinstance(value, str):
                        payload_item[text_key] = _redact(value)
                payload = {
                    "kind": safe_kind,
                    "item": payload_item,
                    "attempt": ts,
                    "error": str(exc),
                }
                path.write_text(
                    json.dumps(payload, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
                logger.warning(
                    "memory write failed; saved to fallback {} ({})",
                    path.name,
                    exc,
                )
            except Exception as file_exc:  # noqa: BLE001 - fallback 自身失败也吞掉
                logger.warning(
                    "memory fallback write also failed for kind={} ({}); original={}",
                    kind,
                    file_exc,
                    exc,
                )
            return False

    def _persist(
        self,
        filtered: FilteredExtractionResult,
        session: Session,
        source: str,
    ) -> PersistenceResult:
        now = _now_iso()
        saved_memory_ids: list[str] = []
        saved_episode_ids: list[str] = []

        # WU-3B: 每条真实写入通过 _safe_write_with_fallback 路由。
        # 成功 → 返回 True → id 进入 saved_*_ids;失败 → 整条 payload 落 fallback
        # 文件 → 返回 False → id 不进 saved_*_ids(对外统计保持「真实成功」语义)。
        with self.database.connect() as conn:
            # 阶段 4a：写入 memories（先写，拿到 ID 供 episode 反向引用）
            memory_by_subject: dict[str, str] = {}
            for item in filtered.memories:
                memory_id = str(uuid4())
                memory = Memory(
                    id=memory_id,
                    content=item.content,
                    type=MemoryType(item.type.strip().lower()),
                    priority=_resolve_priority(item),
                    source="extraction",
                    importance_score=item.importance,
                    tags=list(item.tags),
                    subject=item.subject,
                    predicate=item.predicate,
                    workspace_id=self.workspace_id,
                    user_id=self.user_id,
                    created_at=now,
                    updated_at=now,
                )
                ok = self._safe_write_with_fallback(
                    conn,
                    kind="memory",
                    item=memory.to_row(),
                    writer=lambda c, m=memory: add_memory(c, m),
                )
                if not ok:
                    continue
                saved_memory_ids.append(memory_id)
                if item.subject:
                    memory_by_subject.setdefault(item.subject, memory_id)

            # 阶段 4b：反向关联（subject == entity 匹配）
            episode = filtered.episode
            linked_ids: list[str] = []
            if episode is not None:
                for entity in episode.entities:
                    memory_id = memory_by_subject.get(entity)
                    if memory_id is not None and memory_id not in linked_ids:
                        linked_ids.append(memory_id)

            # 阶段 4c：写入 episode（即使 LLM 失败也写，仅 action_nodes）
            if episode is not None or filtered.action_nodes:
                episode_id = str(uuid4())
                record = Episode(
                    id=episode_id,
                    session_id=session.key,
                    summary=episode.summary if episode is not None else "",
                    goal=episode.goal if episode is not None else "",
                    outcome=_normalize_outcome(episode.outcome if episode is not None else None),
                    source=self._map_source(source),
                    action_nodes=[node.to_dict() for node in filtered.action_nodes],
                    entities=list(episode.entities) if episode is not None else [],
                    tools_used=list(episode.tools_used) if episode is not None else [],
                    linked_memory_ids=linked_ids,
                    importance_score=episode.importance if episode is not None else 0.7,
                    started_at=_iso(session.created_at),
                    ended_at=now,
                )
                episode_ok = self._safe_write_with_fallback(
                    conn,
                    kind="episode",
                    item=record.to_row(),
                    writer=lambda c, r=record: add_episode(c, r),
                )
                if episode_ok:
                    saved_episode_ids.append(episode_id)
                    # FIX-6 Rev-M-4：反向回填 source_episode_id（同一事务）。
                    # 单条失败仅 warning，不影响 episode 持久化或后续回填。
                    for memory_id in linked_ids:
                        backfill_ok = self._safe_write_with_fallback(
                            conn,
                            kind="backfill",
                            item={"memory_id": memory_id, "episode_id": episode_id},
                            writer=lambda c, mid=memory_id, eid=episode_id: update_memory_source_episode(
                                c, mid, eid
                            ),
                        )
                        if not backfill_ok:
                            logger.warning(
                                "memory source_episode_id backfill failed for {} (saved to fallback)",
                                memory_id,
                            )
                else:
                    logger.error("episode insert failed (saved to fallback)")

        return PersistenceResult(
            memory_ids=saved_memory_ids,
            episode_ids=saved_episode_ids,
        )

    @staticmethod
    def _map_source(source: str) -> EpisodeSource:
        mapped = _SOURCE_MAP.get(source)
        if mapped is None:
            logger.warning("unknown extraction source {!r}, fallback to session_end", source)
            return EpisodeSource.SESSION_END
        return mapped

    # ------------------------------------------------------------------
    # S2: Episode 抽取（plan 2026-09-12 nanobot memory extraction hardening）
    # ------------------------------------------------------------------

    async def generate_episode(
        self,
        transcript: list[dict],
        session_key: str,
        source: str = "session_end",
    ) -> Episode | None:
        """从整段对话转录生成一个情节记忆（不入库，由调用方决定）。"""
        from datetime import datetime, timezone

        from .models import Episode, EpisodeOutcome

        if not transcript:
            return None

        action_nodes = self._extract_action_nodes(transcript)
        now_iso = datetime.now(timezone.utc).isoformat()
        episode = Episode(
            id=str(uuid4()),
            session_id=session_key,
            summary="",
            started_at=now_iso,
            ended_at=now_iso,
            source=self._map_source(source),
            outcome=EpisodeOutcome.COMPLETED,
            action_nodes=action_nodes,
        )

        if self.runtime is not None:
            try:
                conv_text = self._format_episode_lines(transcript)
                # EPISODE_EXTRACTION_PROMPT 为指令式，无 {conversation} 占位符
                prompt = f"{EPISODE_EXTRACTION_PROMPT}\n\n### 对话转录\n{conv_text}"
                resp = await self.runtime.provider.chat_with_retry(
                    model=self.runtime.model,
                    messages=[{"role": "user", "content": prompt}],
                    tools=[],
                    temperature=self.runtime.generation.temperature,
                    max_tokens=self.runtime.generation.max_tokens,
                    reasoning_effort=self.runtime.generation.reasoning_effort,
                )
                text = getattr(resp, "content", "") or ""
                data = _parse_json_object(text)
                if isinstance(data, dict):
                    summary = str(data.get("summary", "")).strip()
                    if summary:
                        episode.summary = summary
                    episode.goal = str(data.get("goal", "")).strip()
                    raw_outcome = str(data.get("outcome", "")).strip().lower()
                    if raw_outcome in ("completed", "partial", "failed", "ongoing"):
                        try:
                            episode.outcome = EpisodeOutcome(raw_outcome)
                        except ValueError:
                            pass
                    entities = data.get("entities")
                    if isinstance(entities, list):
                        episode.entities = [str(e) for e in entities if e]
                    tools = data.get("tools_used")
                    if isinstance(tools, list):
                        existing = set(episode.tools_used)
                        for t in tools:
                            ts = str(t)
                            if ts and ts not in existing:
                                episode.tools_used.append(ts)
                                existing.add(ts)
            except Exception as exc:
                logger.warning("generate_episode LLM failed: {}", exc)

        if not episode.summary:
            episode.summary = self._generate_fallback_summary(transcript)
            first_content = transcript[0].get("content", "") if transcript else ""
            episode.goal = str(first_content)[:100]
        if not episode.entities:
            episode.entities = self._extract_entities_heuristic(transcript)

        return episode

    def _extract_action_nodes(self, transcript: list[dict]) -> list[dict]:
        """从 transcript 中抽取结构化工具调用节点（参考 OpenAkita _extract_action_nodes）。"""
        nodes: list[dict] = []
        for turn in transcript:
            tool_calls = turn.get("tool_calls") or []
            if not tool_calls:
                continue
            for tc in tool_calls:
                inp = tc.get("input", tc.get("arguments", {})) or {}
                params: dict[str, str] = {}
                for key in ("command", "path", "query", "url", "filename"):
                    if key in inp:
                        params[key] = str(inp[key])[:200]
                success = True
                err: str | None = None
                summary = ""
                tc_id = tc.get("id", "")
                for tr in turn.get("tool_results") or []:
                    if tr.get("tool_use_id") == tc_id or not tc_id:
                        content = tr.get("content", "")
                        if isinstance(content, str):
                            summary = content[:200]
                        else:
                            summary = str(content)[:200]
                        if tr.get("is_error"):
                            success = False
                            err = summary
                        break
                nodes.append({
                    "tool_name": tc.get("name", ""),
                    "key_params": params,
                    "result_summary": summary,
                    "success": success,
                    "error_message": err,
                })
        return nodes

    @staticmethod
    def _format_episode_lines(transcript: list[dict]) -> str:
        lines: list[str] = []
        for t in transcript[-20:]:
            content = (t.get("content", "") or "")[:600]
            suffix = ""
            if t.get("tool_calls"):
                suffix = f" [调用了 {len(t['tool_calls'])} 个工具]"
            lines.append(f"[{t.get('role', '?')}]: {content}{suffix}")
        return "\n".join(lines)

    @staticmethod
    def _generate_fallback_summary(transcript: list[dict]) -> str:
        user_msgs = [
            (t.get("content", "") or "")[:100]
            for t in transcript
            if t.get("role") == "user" and t.get("content")
        ]
        if user_msgs:
            return f"对话涉及: {'; '.join(user_msgs[:3])}"
        return f"共 {len(transcript)} 轮对话"

    @staticmethod
    def _extract_entities_heuristic(transcript: list[dict]) -> list[str]:
        path_re = re.compile(r"[A-Za-z]:[\\/][^\s\"']+")
        file_re = re.compile(r"[\w-]+\.(?:py|js|ts|md|json|yaml|toml|sh)\b")
        out: set[str] = set()
        for t in transcript:
            text = t.get("content", "") or ""
            for m in path_re.finditer(text):
                out.add(m.group(0))
            for m in file_re.finditer(text):
                out.add(m.group(0))
        return list(out)[:20]


def _parse_json_object(text: str) -> dict | None:
    """宽松提取首个 JSON 对象。"""
    import json
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None
