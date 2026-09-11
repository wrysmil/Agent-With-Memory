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
