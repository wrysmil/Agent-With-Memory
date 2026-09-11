"""Tests for the semantic recall channel (T-05)."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from nanobot.memory.retrieval.channels.semantic import search_semantic


class _FakeStore:
    def __init__(self, rows):
        self._rows = rows

    def search_semantic_scored(self, query, limit):
        return self._rows


def test_returns_candidates_with_semantic_channel():
    raw_memory = SimpleNamespace(
        id="m-1",
        content="Python 爬虫",
        importance_score=0.8,
        updated_at=datetime.now(timezone.utc),
        access_count=3,
    )
    store = _FakeStore([(raw_memory, 0.9)])
    cands = search_semantic(
        store, query="Python 爬虫", limit=10, compute_recency=lambda dt: 1.0
    )
    assert len(cands) == 1
    c = cands[0]
    assert c.source_channel == "semantic"
    assert c.relevance == 0.9
    assert c.recency_score == 1.0
    assert c.importance_score == 0.8
    assert c.access_frequency_score == pytest.approx(0.2772589)  # log1p(3)/5


def test_empty_results():
    store = _FakeStore([])
    assert (
        search_semantic(store, query="x", limit=10, compute_recency=lambda dt: 0.5) == []
    )
