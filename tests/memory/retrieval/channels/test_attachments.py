"""Tests for the attachments recall channel (T-08)."""
from __future__ import annotations

from datetime import datetime

import pytest

from nanobot.memory.retrieval.channels.attachments import search_attachments


class _FakeStore:
    """假 store：本 WU 只验闸门与候选包装，term 匹配语义归 T-12 的 repository 真实现。"""

    def __init__(self, items):
        self._items = items

    def search_attachments(self, term, *, intent, limit):
        return self._items[:limit]


def _make_item(mid, content):
    return {
        "id": mid,
        "content": content,
        "updated_at": datetime.now(),
        "importance_score": 0.5,
    }


@pytest.mark.parametrize(
    "query,intent,expected",
    [
        ("找一下之前的图片", "search_file", True),
        ("看一下 pdf 文件", "general", True),
        ("附近的视频", "general", True),
        ("聊点别的", "general", False),
    ],
)
def test_only_fires_on_media_hint_or_search_file(query, intent, expected):
    store = _FakeStore([_make_item("a-1", "img-2026.png")])
    cands = search_attachments(
        store,
        raw_query=query,
        keywords=[],
        intent=intent,
        limit=5,
        compute_recency=lambda dt: 0.9,
    )
    assert (len(cands) > 0) is expected


def test_attachments_channel_label():
    store = _FakeStore([_make_item("a-1", "video.mp4")])
    cands = search_attachments(
        store,
        raw_query="视频文件",
        keywords=[],
        intent="general",
        limit=5,
        compute_recency=lambda dt: 0.9,
    )
    assert cands and cands[0].source_channel == "attachments"


def test_dedupes_across_terms():
    store = _FakeStore([_make_item("a-1", "img-2026.png"), _make_item("a-2", "img-2025.png")])
    cands = search_attachments(
        store,
        raw_query="图片",
        keywords=["图片"],  # 与 raw_query 同批命中，去重后不应重复
        intent="general",
        limit=10,
        compute_recency=lambda dt: 0.9,
    )
    assert [c.memory_id for c in cands] == ["a-1", "a-2"]


def test_respects_limit():
    store = _FakeStore([_make_item(f"a-{i}", "img.png") for i in range(5)])
    cands = search_attachments(
        store,
        raw_query="图片",
        keywords=[],
        intent="general",
        limit=2,
        compute_recency=lambda dt: 0.9,
    )
    assert len(cands) == 2
