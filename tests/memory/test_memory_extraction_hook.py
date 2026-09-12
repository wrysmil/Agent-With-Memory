"""WU-2：话题切换增量抽取的 hook 测试。

覆盖：
- T5 命中话题切换后触发 ``extract_incremental`` 而非整 session 提取；
- ``_compute_incremental_start_index`` 的窗口推导行为。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from nanobot.agent.hook import AgentHookContext
from nanobot.agent.hooks.memory_extraction import (
    _BACKGROUND_TASKS,
    MemoryExtractionHook,
)


# ---------------------------------------------------------------------------
# Fakes（鸭子类型）
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeGeneration:
    temperature = 0.2
    max_tokens = 512
    reasoning_effort = None


class _FakeProvider:
    def __init__(self, result: str | None = None) -> None:
        self.result = result
        self.calls: list[list[dict[str, Any]]] = []

    async def chat_with_retry(
        self, *, messages: list[dict[str, Any]], **_kwargs: Any
    ) -> _FakeResponse:
        self.calls.append(messages)
        return _FakeResponse(self.result)


class _FakeRuntime:
    def __init__(self, provider: _FakeProvider, model: str = "fake-model") -> None:
        self.provider = provider
        self.model = model
        self.generation = _FakeGeneration()


class _FakeExtractor:
    """分别记录整 session 提取和增量提取调用。"""

    def __init__(self) -> None:
        self.session_calls: list[tuple[Any, str]] = []
        self.incremental_calls: list[tuple[Any, int]] = []

    async def extract_session(self, session: Any, *, source: str = "session_end") -> None:
        self.session_calls.append((session, source))

    async def extract_incremental(self, session: Any, last_extracted_index: int) -> None:
        self.incremental_calls.append((session, last_extracted_index))


class _FakeScratchpadWriter:
    def __init__(self) -> None:
        self.focus_calls: list[tuple[str, str]] = []

    async def update_focus(self, session_key: str, new_focus: str) -> None:
        self.focus_calls.append((session_key, new_focus))


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

_SWITCHED = ["帮我写一个排序算法", "帮我优化这个函数", "帮我加个单元测试", "今天午饭吃什么"]


@pytest.fixture(autouse=True)
async def _cleanup_background_tasks():
    yield
    for task in list(_BACKGROUND_TASKS):
        task.cancel()
    await asyncio.sleep(0)
    _BACKGROUND_TASKS.clear()


def _iter_ctx(*user_messages: str) -> AgentHookContext:
    return AgentHookContext(
        iteration=0,
        messages=[{"role": "user", "content": m} for m in user_messages],
        session_key="s1",
    )


def _make_hook(
    *,
    extractor: _FakeExtractor | None = None,
    writer: _FakeScratchpadWriter | None = None,
    runtime: _FakeRuntime | None = None,
    session_key: str = "s1",
) -> MemoryExtractionHook:
    return MemoryExtractionHook(
        extractor or _FakeExtractor(),
        session_key,
        writer or _FakeScratchpadWriter(),
        runtime=runtime,
    )


async def _drain_background() -> None:
    for _ in range(200):
        if not _BACKGROUND_TASKS:
            return
        await asyncio.sleep(0)
    raise AssertionError("background tasks did not finish")


# ---------------------------------------------------------------------------
# T5：话题切换触发增量提取
# ---------------------------------------------------------------------------


class TestTopicChangeIncrementalExtraction:
    async def test_topic_change_triggers_incremental_not_full_extraction(self):
        provider = _FakeProvider(result='{"same_topic": false}')
        extractor = _FakeExtractor()
        hook = _make_hook(extractor=extractor, runtime=_FakeRuntime(provider))

        await hook.before_iteration(_iter_ctx(*_SWITCHED))
        await _drain_background()

        assert extractor.session_calls == []
        assert len(extractor.incremental_calls) == 1
        session, last_extracted_index = extractor.incremental_calls[0]
        assert session.key == "s1"
        assert last_extracted_index == 3
        assert [m["content"] for m in session.messages] == _SWITCHED


# ---------------------------------------------------------------------------
# _compute_incremental_start_index
# ---------------------------------------------------------------------------


class TestComputeIncrementalStartIndex:
    def test_returns_after_second_last_user_message(self):
        hook = _make_hook()
        messages = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "second"},
            {"role": "tool", "content": "tool output"},
            {"role": "user", "content": "third"},
            {"role": "assistant", "content": "done"},
        ]

        assert hook._compute_incremental_start_index(messages) == 3

    def test_single_user_message_returns_zero(self):
        hook = _make_hook()
        messages = [{"role": "user", "content": "only"}]

        assert hook._compute_incremental_start_index(messages) == 0

    def test_empty_user_message_is_skipped(self):
        hook = _make_hook()
        messages = [
            {"role": "user", "content": "first"},
            {"role": "user", "content": ""},
            {"role": "user", "content": "third"},
        ]

        assert hook._compute_incremental_start_index(messages) == 1

    def test_no_user_message_returns_zero(self):
        hook = _make_hook()
        messages = [{"role": "assistant", "content": "no user here"}]

        assert hook._compute_incremental_start_index(messages) == 0
