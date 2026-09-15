"""检索引擎编排器。

契约：
- 输入：用户消息（``query``）+ 最近消息（``recent_messages``）+ 可选 ``max_tokens`` / ``precomputed_keywords``
- 输出：可注入 system prompt 的 markdown 字符串（无候选时为 ``""``）

处理链：
    1. ``MemoryQueryPreprocessor.prepare`` — Gate（空/控制词/超短）+ 反注入清洗
    2. ``QueryDecomposer.decompose`` — LLM 拆解关键词/意图，规则降级
    3. 四路并行召回（``asyncio.to_thread`` 包装同步通道）：
       semantic / episodes / recent / attachments；任一异常被隔离
    4. 按 ``memory_id`` 去重，保留最高 relevance
    5. ``Reranker.rerank`` — 综合分 + 焦点增强 + 阈值过滤
    6. ``RetrievalFormatter.format`` — 按 ``composite_score`` 截断
    7. 拼接 ``## 相关记忆（自动检索）`` markdown 注入块
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Callable

from loguru import logger

from nanobot.memory.retrieval.candidate import RetrievalCandidate
from nanobot.memory.retrieval.channels.attachments import search_attachments
from nanobot.memory.retrieval.channels.episodes import search_episodes
from nanobot.memory.retrieval.channels.recent import search_recent
from nanobot.memory.retrieval.channels.semantic import search_semantic
from nanobot.memory.retrieval.decomposer import QueryDecomposer
from nanobot.memory.retrieval.formatter import RetrievalFormatter
from nanobot.memory.retrieval.preprocessor import MemoryQueryPreprocessor
from nanobot.memory.retrieval.reranker import Reranker, _compute_recency

# 四路通道各自的召回上限（与 plan T-12 §GROUP-B 一致）
_SEMANTIC_LIMIT = 15
_EPISODES_LIMIT = 5
_RECENT_LIMIT = 5
_ATTACHMENTS_LIMIT = 5

# max_tokens → formatter limit 的经验映射：每条候选约占 ~70 token
_TOKENS_PER_CANDIDATE = 70


class RetrievalEngine:
    """Layer 4 Active Retrieval 编排器。"""

    def __init__(
        self,
        *,
        store: Any,
        brain: Any = None,
        persona: Any = None,
        max_tokens: int = 700,
    ) -> None:
        self.store = store
        self._decomposer = QueryDecomposer(brain=brain)
        self._reranker = Reranker()
        self._persona = persona
        self._default_max_tokens = max(1, max_tokens)

    async def retrieve(
        self,
        *,
        query: str,
        recent_messages: list,
        active_persona: Any = None,
        max_tokens: int | None = None,
        precomputed_keywords: list[str] | None = None,
    ) -> str:
        """执行检索；gate skip / 无候选时返回 ``""``。

        兼容薄壳：只返回 markdown 注入块，丢弃 memory_id 集合——需要 ID 的
        调用方（WU-B 引用评分闭环）应改用 :meth:`retrieve_with_ids`。
        """
        block, _ids = await self.retrieve_with_ids(
            query=query,
            recent_messages=recent_messages,
            active_persona=active_persona,
            max_tokens=max_tokens,
            precomputed_keywords=precomputed_keywords,
        )
        return block

    async def retrieve_with_ids(
        self,
        *,
        query: str,
        recent_messages: list,
        active_persona: Any = None,
        max_tokens: int | None = None,
        precomputed_keywords: list[str] | None = None,
    ) -> tuple[str, list[str]]:
        """执行检索并同时返回 ``(注入块, 本次注入的 memory_id 列表)``。

        WU-B 入口：markdown 里渲染了每条的 ``memory_id``，调用方拿到 ids 供
        idle 提取的引用评分（``cited_memories``）使用。gate skip / 无候选时
        返回 ``("", [])``。
        """
        prepared = MemoryQueryPreprocessor.prepare(query, recent_messages)
        if prepared.skip:
            return "", []

        tokens = max_tokens if max_tokens is not None else self._default_max_tokens

        # 1) 拆解（关键词 + 意图）
        if precomputed_keywords is not None:
            keywords = precomputed_keywords
            # 通用查询，走一般检索
            # "search_file"	 ：检索文件/附件类资源
            intent = "general"
        else:
            decomp = await self._decomposer.decompose(
                prepared.cleaned_query, recent_messages
            )
            keywords = decomp.keywords
            intent = decomp.intent

      # _fn 是一个闭包（内部函数）
    # 即一个接收一个参数、返回一个结果的函数式接口。
        recency = _build_recency_fn()

        # 2) 四路并行（同步通道 → asyncio.to_thread）
        sem_task = asyncio.create_task(
            asyncio.to_thread(
                search_semantic,
                self.store,
                query=prepared.cleaned_query,
                limit=_SEMANTIC_LIMIT,
                compute_recency=recency,
            )
        )
        eps_task = asyncio.create_task(
            asyncio.to_thread(
                search_episodes,
                self.store,
                query=prepared.cleaned_query,
                limit=_EPISODES_LIMIT,
                compute_recency=recency,
            )
        )
        rec_task = asyncio.create_task(
            asyncio.to_thread(
                search_recent,
                self.store,
                query=prepared.cleaned_query,
                keywords=keywords,
                limit=_RECENT_LIMIT,
                compute_recency=recency,
            )
        )
        att_task = asyncio.create_task(
            asyncio.to_thread(
                search_attachments,
                self.store,
                raw_query=prepared.cleaned_query,
                keywords=keywords,
                intent=intent,
                limit=_ATTACHMENTS_LIMIT,
                compute_recency=recency,
            )
        )
        sem, eps, rec, att = await asyncio.gather(
            sem_task, eps_task, rec_task, att_task,
            return_exceptions=True,
        )

        candidates: list[RetrievalCandidate] = []
        for channel_name, chunk in zip(
            ("semantic", "episodes", "recent", "attachments"),
            (sem, eps, rec, att),
            strict=True,
        ):
            if isinstance(chunk, Exception):
                # RCA 2026-09-15 根因 1：这里原本是无条件 ``continue``，把
                # ``AttributeError: 'MemoryDatabase' object has no attribute
                # 'search_semantic_scored'`` 这类装配级故障完全吞掉，导致
                # 「四路召回全废」表现为「记忆里是空的」。定位信息必须留下。
                logger.warning(
                    "retrieval channel {} failed: {}: {}",
                    channel_name,
                    type(chunk).__name__,
                    chunk,
                )
                continue
            candidates.extend(chunk)

        # 3) 去重：按 memory_id 保留最高 relevance
        unique = _dedupe_by_memory_id(candidates)

        # 4) rerank + format
        ranked = self._reranker.rerank(
            unique,
            query=prepared.cleaned_query,
            persona=self._persona,
            focus_terms=keywords,
        )
        limit = max(1, tokens // _TOKENS_PER_CANDIDATE)
        items = RetrievalFormatter(limit=limit).format(ranked)
        return _render_injection_block(items), [item["memory_id"] for item in items]


def _dedupe_by_memory_id(
    candidates: list[RetrievalCandidate],
) -> list[RetrievalCandidate]:
    """按 ``memory_id`` 去重，同 id 保留最高 ``relevance``。"""
    bucket: dict[str, RetrievalCandidate] = {}
    for c in candidates:
        prev = bucket.get(c.memory_id)
        # 桶空 或 当前候选 relevance 更高 → 替换
        if prev is None or c.relevance > prev.relevance:
            bucket[c.memory_id] = c
    return list(bucket.values())


def _build_recency_fn() -> Callable[[Any], float]:
    """构造通道 ``compute_recency`` 闭包：兼容 ``datetime`` 与 ISO 字符串。"""

    def _fn(value: Any) -> float:
        coerced = _coerce_dt(value)
        return _compute_recency(coerced)

    return _fn


def _coerce_dt(value: Any) -> datetime:
    """``datetime`` 原样返回；ISO 字符串 → ``datetime.fromisoformat``；失败兜底。"""
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return datetime.now()


def _render_injection_block(items: list[dict]) -> str:
    """``RetrievalFormatter.format`` 输出 → markdown 注入块。

    契约修正（2026-09-11 实测）：实际 ``RetrievalFormatter.format`` 返回
    ``list[dict]``，不提供静态 ``render_injection_block``。Engine 自渲染 markdown。

    WU-B（2026-09-15）：每条 bullet 尾部追加 ``(ID: <memory_id>)``，让 LLM 在
    需要时能引用具体记忆，同时 idle 提取方可用 ``cited_memories=[{id, content}]``
    把同一批注入记忆送进引用评分 prompt。
    """
    if not items:
        return ""
    lines = ["## 相关记忆（自动检索）"]
    for item in items:
        content = item.get("content", "")
        memory_id = item.get("memory_id", "")
        if content:
            id_suffix = f" (ID: {memory_id})" if memory_id else ""
            lines.append(f"- {content}{id_suffix}")
    return "\n".join(lines)
