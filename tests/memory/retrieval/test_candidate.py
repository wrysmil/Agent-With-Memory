"""Tests for RetrievalCandidate dataclass (T-01)."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from nanobot.memory.retrieval.candidate import RetrievalCandidate


def test_candidate_required_fields():
    c = RetrievalCandidate(
        memory_id="m-1",
        content="Python 爬虫脚本",
        source_channel="semantic",
        relevance=0.9,
        recency_score=0.8,
        importance_score=0.7,
        access_frequency_score=0.5,
    )
    assert c.memory_id == "m-1"
    assert c.source_channel == "semantic"
    assert c.composite_score == 0.0  # 默认未计算
    assert c.raw is None


def test_candidate_with_raw():
    raw = SimpleNamespace(id="m-2", updated_at=datetime.now())
    c = RetrievalCandidate(
        memory_id="m-2", content="...", source_channel="episodes",
        relevance=0.6, recency_score=0.4, importance_score=0.3,
        access_frequency_score=0.2, raw=raw,
    )
    assert c.raw is raw


def test_candidate_invalid_channel():
    with pytest.raises(ValueError, match="source_channel"):
        RetrievalCandidate(
            memory_id="m-3", content="...", source_channel="bogus",
            relevance=0.0, recency_score=0.0, importance_score=0.0,
            access_frequency_score=0.0,
        )
