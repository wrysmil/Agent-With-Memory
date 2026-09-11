"""Tests for the episodes recall channel (T-06)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from nanobot.memory.retrieval.channels.episodes import (
    _extract_query_entities,
    search_episodes,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("帮我打开 C:\\Users\\foo\\bar.py", ["C:\\Users\\foo\\bar.py"]),
        ("看一下 main.py 文件", ["main.py"]),
        ("那个 build.sh 还在吗", ["build.sh"]),
        ("普通聊天没有任何路径", []),
    ],
)
def test_extract_query_entities(text, expected):
    entities = _extract_query_entities(text)
    for e in expected:
        assert e in entities


def test_search_episodes_returns_060_channel():
    class _FakeStore:
        def __init__(self, mapping):
            self.mapping = mapping

        def search_episodes(self, entity, limit):
            return self.mapping.get(entity, [])

    fake_episode = SimpleNamespace(id="ep-1", summary="用户配置了 uv 环境", session_key="s-1")
    store = _FakeStore({"main.py": [fake_episode]})
    cands = search_episodes(
        store, query="main.py 还在吗", limit=5, compute_recency=lambda dt: 0.5
    )
    assert len(cands) == 1
    assert cands[0].source_channel == "episodes"
    assert cands[0].relevance == pytest.approx(0.6)
    assert cands[0].memory_id == "ep-1"


def test_search_episodes_limits_entities_to_3():
    class _CountingStore:
        def __init__(self):
            self.calls = []

        def search_episodes(self, entity, limit):
            self.calls.append(entity)
            return []

    store = _CountingStore()
    text = "a.py b.py c.py d.py e.py"
    search_episodes(store, query=text, limit=5, compute_recency=lambda dt: 0.5)
    assert len(store.calls) <= 3
