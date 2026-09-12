"""ProfileExtractor 单测（S3 Track 1：用户画像 + 引用评分 + 增量合并）。"""
from __future__ import annotations

import pytest

from nanobot.memory.profile_extractor import (
    ProfileExtractor,
    merge_profile_incremental,
)


class _FakeProvider:
    def __init__(self, content: str = "", raise_exc: Exception | None = None) -> None:
        self._content = content
        self._raise = raise_exc

    async def chat_with_retry(self, *, model, messages, tools, **kwargs):
        if self._raise is not None:
            raise self._raise

        class _R:
            content = self._content

        return _R()


class _FakeRuntime:
    def __init__(self, content: str = "", raise_exc: Exception | None = None) -> None:
        self.provider = _FakeProvider(content, raise_exc)
        self.model = "test-model"
        self.generation = type("G", (), {"temperature": 0.0, "max_tokens": 1000, "reasoning_effort": None})()


@pytest.mark.asyncio
async def test_extract_profile_returns_items_and_scores():
    runtime = _FakeRuntime(
        '{"memories":[{"content":"用户喜欢美咖啡","subject":"用户",'
        '"predicate":"喜欢","type":"PREFERENCE","importance":0.7,'
        '"priority":"long_term","tags":["coffee"],"is_update":false}],'
        '"citation_scores":[{"memory_id":"m1","useful":true}]}'
    )
    ext = ProfileExtractor(runtime=runtime)
    items, scores = await ext.extract(
        transcript=[{"role": "user", "content": "我喜欢美咖啡"}],
        episode_id="ep-1",
        cited_memories=[{"id": "m1", "content": "用户住在杭州"}],
    )
    assert len(items) == 1
    assert items[0].content == "用户喜欢美咖啡"
    assert items[0].subject == "用户"
    assert items[0].type == "PREFERENCE"
    assert scores[0]["memory_id"] == "m1"
    assert scores[0]["useful"] is True


@pytest.mark.asyncio
async def test_extract_profile_no_citation_returns_no_scores():
    runtime = _FakeRuntime(
        '{"memories":[{"content":"x","subject":"用户",'
        '"predicate":"喜欢","type":"PREFERENCE","importance":0.5,'
        '"priority":"long_term","tags":[],"is_update":false}]}'
    )
    ext = ProfileExtractor(runtime=runtime)
    items, scores = await ext.extract(
        transcript=[{"role": "user", "content": "x"}], episode_id="e"
    )
    assert len(items) == 1
    assert scores == []


@pytest.mark.asyncio
async def test_extract_profile_llm_failure_returns_empty():
    runtime = _FakeRuntime(raise_exc=RuntimeError("down"))
    ext = ProfileExtractor(runtime=runtime)
    items, scores = await ext.extract(
        transcript=[{"role": "user", "content": "x"}], episode_id="e"
    )
    assert items == [] and scores == []


def test_merge_incremental_same_subject_predicate_keeps_old_when_conflicting():
    existing = {
        "id": "existing-1",
        "content": "用户不喜欢咖啡",
        "subject": "用户",
        "predicate": "喜欢",
        "type": "PREFERENCE",
    }
    incoming = {
        "id": "m1",
        "content": "用户喜欢咖啡",
        "subject": "用户",
        "predicate": "喜欢",
        "type": "PREFERENCE",
        "is_update": True,
    }
    result = merge_profile_incremental(existing, incoming)
    assert result.action == "keep_old_with_conflict"
    assert "m1" in result.existing["conflicts_with"]


def test_merge_incremental_same_subject_predicate_updates_when_consistent():
    existing = {
        "id": "m1",
        "content": "用户喜欢美咖啡",
        "subject": "用户",
        "predicate": "喜欢",
        "type": "PREFERENCE",
        "importance": 0.6,
    }
    incoming = {
        "content": "用户喜欢美咖啡",
        "subject": "用户",
        "predicate": "喜欢",
        "type": "PREFERENCE",
        "is_update": True,
        "importance": 0.7,
    }
    result = merge_profile_incremental(existing, incoming)
    assert result.action == "update"
    assert result.merged["importance"] == pytest.approx(0.7)


def test_merge_incremental_different_predicate_creates_new():
    existing = {
        "id": "m1",
        "subject": "用户",
        "predicate": "喜欢",
        "content": "x",
    }
    incoming = {
        "subject": "用户",
        "predicate": "不喜欢",
        "content": "y",
        "is_update": False,
    }
    result = merge_profile_incremental(existing, incoming)
    assert result.action == "create_new"