"""语义通道：调用底层 search_backend（向量 ∪ FTS5）并构造 RetrievalCandidate。"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Callable

from nanobot.memory.retrieval.candidate import RetrievalCandidate


def _access_freq(access_count: int) -> float:
    return min(1.0, math.log1p(max(0, access_count)) / 5.0)


def search_semantic(
    store,
    *,
    query: str,
    limit: int,
    compute_recency: Callable[[datetime], float],
) -> list[RetrievalCandidate]:
    """从 store 取语义召回结果并包装为 RetrievalCandidate。

    store 需提供 ``search_semantic_scored(query, limit) -> list[(Memory, raw_score)]``，
    该接口由 RetrievalEngine 注入（真实实现在 T-12 落地）。
    """
    scored = store.search_semantic_scored(query, limit=limit * 3)
    candidates: list[RetrievalCandidate] = []
    for mem, raw_score in scored[:limit]:
        candidates.append(
            RetrievalCandidate(
                memory_id=mem.id,
                content=mem.content,
                source_channel="semantic",
                relevance=float(raw_score),
                recency_score=compute_recency(mem.updated_at),
                importance_score=float(getattr(mem, "importance_score", 0.5) or 0.5),
                access_frequency_score=_access_freq(
                    int(getattr(mem, "access_count", 0) or 0)
                ),
                raw=mem,
            )
        )
    return candidates
