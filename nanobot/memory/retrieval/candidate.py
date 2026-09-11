"""检索候选项数据类。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SourceChannel = Literal["semantic", "episodes", "recent", "attachments"]
_VALID_CHANNELS = frozenset({"semantic", "episodes", "recent", "attachments"})


@dataclass(frozen=True)
class RetrievalCandidate:
    """多路召回的统一点。综合分由 Reranker 写入 composite_score。"""
    memory_id: str
    content: str
    source_channel: SourceChannel
    relevance: float              # 来自底层通道原始相关性
    recency_score: float          # 0..1，时间衰减后
    importance_score: float       # 0..1，记忆自身权重
    access_frequency_score: float # 0..1，log1p(access_count)/5
    raw: Any = None               # 原始 Memory/Episode 对象，便于后续格式化
    composite_score: float = field(default=0.0)

    def __post_init__(self) -> None:
        if self.source_channel not in _VALID_CHANNELS:
            raise ValueError(
                f"source_channel must be one of {_VALID_CHANNELS}, "
                f"got {self.source_channel!r}"
            )
