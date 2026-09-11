"""Tests for the recent recall channel (T-07)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from nanobot.memory.retrieval.channels.recent import search_recent


class _FakeStore:
    def __init__(self, memories):
        self._memories = memories

    def query_semantic(self, *, min_importance, since_days, limit):
        cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
        out = [
            m
            for m in self._memories
            if m.importance_score >= min_importance and m.updated_at >= cutoff
        ]
        return out[:limit]


def _make_memory(mid, content, importance, days_ago, *, keyword_hit=False):
    return SimpleNamespace(
        id=mid,
        content=content,
        importance_score=importance,
        updated_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        access_count=0,
    )


def test_skips_low_recency():
    m_recent = _make_memory("m-recent", "old", 0.9, days_ago=1)
    store = _FakeStore([m_recent])
    cands = search_recent(
        store,
        query="anything",
        keywords=[],
        limit=5,
        compute_recency=lambda dt: 0.05,  # 极低 recency
    )
    assert cands == []


def test_keyword_hit_relevance_higher_than_miss():
    m_hit = _make_memory("m-hit", "Python 爬虫", 0.7, days_ago=1, keyword_hit=True)
    m_miss = _make_memory("m-miss", "Java 入门", 0.7, days_ago=1)
    store = _FakeStore([m_hit, m_miss])
    cands = search_recent(
        store,
        query="Python",
        keywords=["Python"],
        limit=5,
        compute_recency=lambda dt: 0.9,
    )
    rels = sorted([c.relevance for c in cands], reverse=True)
    assert rels[0] == pytest.approx(0.7)  # 关键词命中
    assert rels[-1] == pytest.approx(0.2)  # 未命中


def test_returns_recent_channel():
    m = _make_memory("m-1", "anything", 0.8, days_ago=1)
    store = _FakeStore([m])
    cands = search_recent(
        store, query="x", keywords=[], limit=5, compute_recency=lambda dt: 0.95
    )
    assert cands and cands[0].source_channel == "recent"
