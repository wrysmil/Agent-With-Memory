"""Tests for MemoryExtractionHook（plan §10 Task 7 / Task 12）。

覆盖四个生命周期回调：
- T0  ``after_run``      意图门 + scratchpad.current_focus 写入
- T0' ``on_error``       仅紧急保存 focus（不调 LLM / extractor）
- T1  ``after_run``/``on_finally``  异步提取任务登记与 5s 超时等待
- T5  ``before_iteration``          话题切换检测（转录推导 + fire-and-forget 不阻塞）

extractor / scratchpad_writer / runtime 全部用鸭子类型 fake，不依赖真实 provider。
T5 的窗口从 ``AgentHookContext.messages``（会话转录）推导，因此测试直接喂转录，
与 ``build_agent_turn_hook`` 每轮新建实例的生产路径一致。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from nanobot.agent.hook import AgentHookContext, AgentRunHookContext, AgentTurnHookContext
from nanobot.agent.hooks.memory_extraction import (
    _BACKGROUND_TASKS,
    MemoryExtractionHook,
    create_memory_extraction_hook_factory,
)
from nanobot.memory.database import MemoryDatabase
from nanobot.memory.repository import get_scratchpad
from nanobot.memory.scratchpad_writer import ScratchpadWriter

# ---------------------------------------------------------------------------
# Fakes（鸭子类型）
# ---------------------------------------------------------------------------


class _FakeExtractionResult:
    memory_ids: list[str] = []
    episode_ids: list[str] = []
    skipped = 0
    failed_tracks: list[str] = []


class _FakeExtractor:
    """记录调用；``delay`` 模拟慢提取，``raise_exc`` 模拟失败。"""

    def __init__(self, *, delay: float = 0.0, raise_exc: BaseException | None = None) -> None:
        self.calls: list[tuple[Any, str]] = []
        self.incremental_calls: list[tuple[Any, int]] = []
        self.delay = delay
        self.raise_exc = raise_exc

    async def extract_session(self, session: Any, *, source: str = "session_end") -> Any:
        self.calls.append((session, source))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raise_exc is not None:
            raise self.raise_exc
        return _FakeExtractionResult()

    async def extract_incremental(self, session: Any, last_extracted_index: int) -> Any:
        self.incremental_calls.append((session, last_extracted_index))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raise_exc is not None:
            raise self.raise_exc
        return _FakeExtractionResult()


class _FakeScratchpadWriter:
    def __init__(self) -> None:
        self.focus_calls: list[tuple[str, str]] = []
        self.archive_calls: list[list[str]] = []

    async def update_focus(self, session_key: str, new_focus: str) -> None:
        self.focus_calls.append((session_key, new_focus))

    async def archive_completed(self, resolved_items: list[str]) -> None:
        self.archive_calls.append(list(resolved_items))


class _FakeResponse:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeGeneration:
    temperature = 0.2
    max_tokens = 512
    reasoning_effort = None


class _FakeProvider:
    def __init__(
        self,
        result: str | None = None,
        *,
        exc: BaseException | None = None,
        delay: float = 0.0,
    ) -> None:
        self.result = result
        self.exc = exc
        self.delay = delay
        self.calls: list[list[dict[str, Any]]] = []

    async def chat_with_retry(
        self, *, messages: list[dict[str, Any]], **_kwargs: Any
    ) -> _FakeResponse:
        self.calls.append(messages)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return _FakeResponse(self.result)


class _FakeRuntime:
    def __init__(self, provider: _FakeProvider, model: str = "fake-model") -> None:
        self.provider = provider
        self.model = model
        self.generation = _FakeGeneration()


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

#: 4 条同主题 user 消息（达到 T5 检测阈值）。
_SAME_TOPIC = ["帮我写一个排序算法", "帮我优化这个函数", "帮我加个单元测试", "帮我再看看性能"]

#: 前 3 条同主题 + 第 4 条跳到完全无关的话题。
_SWITCHED = ["帮我写一个排序算法", "帮我优化这个函数", "帮我加个单元测试", "今天午饭吃什么"]


@pytest.fixture(autouse=True)
async def _cleanup_background_tasks():
    """每个用例结束后回收模块级 fire-and-forget 任务，避免跨用例泄漏。"""
    yield
    for task in list(_BACKGROUND_TASKS):
        task.cancel()
    await asyncio.sleep(0)
    _BACKGROUND_TASKS.clear()


def _run_ctx(*user_messages: str) -> AgentRunHookContext:
    return AgentRunHookContext(
        messages=[{"role": "user", "content": m} for m in user_messages]
    )


def _iter_ctx(*user_messages: str) -> AgentHookContext:
    return AgentHookContext(
        iteration=0,
        messages=[{"role": "user", "content": m} for m in user_messages],
        session_key="s1",
    )


def _make_hook(
    *,
    extractor: _FakeExtractor | None = None,
    writer: Any | None = None,
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
# T0：after_run 意图门
# ---------------------------------------------------------------------------


class TestAfterRunIntentGate:
    async def test_chat_intent_skips_focus_write(self):
        writer = _FakeScratchpadWriter()
        hook = _make_hook(writer=writer)
        await hook.after_run(_run_ctx("你好"))
        assert writer.focus_calls == []
        await hook.on_finally(_run_ctx())

    async def test_task_intent_writes_focus(self):
        writer = _FakeScratchpadWriter()
        hook = _make_hook(writer=writer)
        await hook.after_run(_run_ctx("帮我实现一个爬虫"))
        assert writer.focus_calls == [("s1", "帮我实现一个爬虫")]
        await hook.on_finally(_run_ctx())

    async def test_focus_truncated_to_200_chars(self):
        writer = _FakeScratchpadWriter()
        hook = _make_hook(writer=writer)
        message = "帮我实现" + "x" * 500
        await hook.after_run(_run_ctx(message))
        assert writer.focus_calls == [("s1", message[:200])]
        assert len(writer.focus_calls[0][1]) == 200
        await hook.on_finally(_run_ctx())

    async def test_missing_user_message_is_noop(self):
        writer = _FakeScratchpadWriter()
        hook = _make_hook(writer=writer)
        await hook.after_run(AgentRunHookContext(messages=[{"role": "assistant", "content": "hi"}]))
        assert writer.focus_calls == []
        await hook.on_finally(_run_ctx())

    async def test_scratchpad_failure_does_not_raise(self):
        class _BoomWriter(_FakeScratchpadWriter):
            async def update_focus(self, session_key: str, new_focus: str) -> None:
                raise RuntimeError("db down")

        extractor = _FakeExtractor()
        hook = MemoryExtractionHook(extractor, "s1", _BoomWriter())
        await hook.after_run(_run_ctx("帮我实现一个爬虫"))  # 不抛
        await hook.on_finally(_run_ctx())
        assert len(extractor.calls) == 1


# ---------------------------------------------------------------------------
# T1：异步提取任务 + on_finally 等待
# ---------------------------------------------------------------------------


class TestRunExtraction:
    async def test_after_run_schedules_extraction(self):
        extractor = _FakeExtractor()
        hook = _make_hook(extractor=extractor)
        await hook.after_run(_run_ctx("帮我实现一个爬虫"))
        await hook.on_finally(_run_ctx())

        assert len(extractor.calls) == 1
        session, source = extractor.calls[0]
        assert source == "session_end"
        assert session.key == "s1"
        assert session.messages[0]["content"] == "帮我实现一个爬虫"

    async def test_empty_messages_skips_extraction(self):
        extractor = _FakeExtractor()
        hook = _make_hook(extractor=extractor)
        await hook.after_run(AgentRunHookContext(messages=[]))
        await hook.on_finally(_run_ctx())
        assert extractor.calls == []

    async def test_on_finally_awaits_completion(self):
        extractor = _FakeExtractor(delay=0.05)
        hook = _make_hook(extractor=extractor)
        await hook.after_run(_run_ctx("帮我实现一个爬虫"))
        assert len(extractor.calls) == 0  # task 尚未被 await
        await hook.on_finally(_run_ctx())
        assert len(extractor.calls) == 1  # on_finally 等待完成

    async def test_on_finally_timeout_does_not_raise(self):
        extractor = _FakeExtractor(delay=10.0)
        hook = _make_hook(extractor=extractor)
        hook.EXTRACTION_WAIT_TIMEOUT = 0.05
        await hook.after_run(_run_ctx("帮我实现一个爬虫"))
        assert len(hook._pending_tasks) == 1

        started = time.monotonic()
        await hook.on_finally(_run_ctx())  # 超时 → warning + cancel，不抛
        assert time.monotonic() - started < 1.0
        assert hook._pending_tasks == set()

    async def test_extraction_failure_does_not_raise(self):
        extractor = _FakeExtractor(raise_exc=RuntimeError("llm exploded"))
        hook = _make_hook(extractor=extractor)
        await hook.after_run(_run_ctx("帮我实现一个爬虫"))
        await hook.on_finally(_run_ctx())  # 不抛
        assert len(extractor.calls) == 1

    async def test_on_finally_without_pending_tasks_is_noop(self):
        hook = _make_hook()
        await hook.on_finally(_run_ctx())  # 不抛


# ---------------------------------------------------------------------------
# T0'：on_error 紧急保存
# ---------------------------------------------------------------------------


class TestOnError:
    async def test_on_error_only_writes_scratchpad(self):
        extractor = _FakeExtractor()
        writer = _FakeScratchpadWriter()
        provider = _FakeProvider(result='{"same_topic": true}')
        runtime = _FakeRuntime(provider)
        hook = _make_hook(extractor=extractor, writer=writer, runtime=runtime)

        await hook.on_error(_run_ctx("帮我实现一个爬虫"))

        assert writer.focus_calls == [("s1", "帮我实现一个爬虫")]
        assert extractor.calls == []  # 不调 extractor
        assert provider.calls == []  # 不调 LLM

    async def test_on_error_without_user_message_is_noop(self):
        writer = _FakeScratchpadWriter()
        hook = _make_hook(writer=writer)
        await hook.on_error(AgentRunHookContext(messages=[{"role": "tool", "content": "x"}]))
        assert writer.focus_calls == []

    async def test_on_error_failure_does_not_raise(self):
        class _BoomWriter(_FakeScratchpadWriter):
            async def update_focus(self, session_key: str, new_focus: str) -> None:
                raise RuntimeError("db down")

        hook = MemoryExtractionHook(_FakeExtractor(), "s1", _BoomWriter())
        await hook.on_error(_run_ctx("帮我实现一个爬虫"))  # 不抛


# ---------------------------------------------------------------------------
# T5：话题切换检测
# ---------------------------------------------------------------------------


class TestTopicChangeDetection:
    async def test_below_threshold_skips_detection(self):
        provider = _FakeProvider(result='{"same_topic": false}')
        extractor = _FakeExtractor()
        hook = _make_hook(extractor=extractor, runtime=_FakeRuntime(provider))

        # 3 条 user 消息 < 阈值 4
        await hook.before_iteration(_iter_ctx(*_SAME_TOPIC[:3]))

        assert provider.calls == []  # 未达阈值，不调检测 LLM
        assert extractor.calls == []

    async def test_same_topic_does_not_trigger(self):
        provider = _FakeProvider(result='{"same_topic": true}')
        extractor = _FakeExtractor()
        writer = _FakeScratchpadWriter()
        hook = _make_hook(extractor=extractor, writer=writer, runtime=_FakeRuntime(provider))

        await hook.before_iteration(_iter_ctx(*_SAME_TOPIC))

        assert len(provider.calls) == 1
        assert extractor.calls == []  # 未触发 semantic 提取
        assert writer.focus_calls == []  # 未归档

    async def test_topic_change_triggers_extraction_and_rotates_focus(self):
        provider = _FakeProvider(result='{"same_topic": false}')
        extractor = _FakeExtractor()
        writer = _FakeScratchpadWriter()
        hook = _make_hook(extractor=extractor, writer=writer, runtime=_FakeRuntime(provider))

        await hook.before_iteration(_iter_ctx(*_SWITCHED))
        await _drain_background()

        # ② fire-and-forget 触发增量提取（不登记到 on_finally）
        assert extractor.calls == []
        assert len(extractor.incremental_calls) == 1
        session, last_extracted_index = extractor.incremental_calls[0]
        assert last_extracted_index == 3
        assert session.key == "s1"
        assert [m["content"] for m in session.messages] == _SWITCHED

        # ① 旧 focus 滚入 active_projects（走 update_focus 归档语义）
        assert writer.focus_calls[-1] == ("s1", "今天午饭吃什么")

    async def test_topic_change_archives_previous_focus_in_real_scratchpad(self, tmp_path: Path):
        db = MemoryDatabase(tmp_path)
        db.init_schema()
        writer = ScratchpadWriter(db, user_id="default")
        # 预置上一轮 T0 写入的旧 focus
        await writer.update_focus("s1", "帮我写排序算法")

        provider = _FakeProvider(result='{"same_topic": false}')
        hook = MemoryExtractionHook(
            _FakeExtractor(), "s1", writer, runtime=_FakeRuntime(provider)
        )

        await hook.before_iteration(_iter_ctx(*_SWITCHED))
        await _drain_background()

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")

        assert entry is not None
        assert entry.current_focus == "今天午饭吃什么"
        assert any("帮我写排序算法" in project for project in entry.active_projects)

    async def test_llm_failure_treated_as_continue(self):
        provider = _FakeProvider(exc=RuntimeError("llm down"))
        extractor = _FakeExtractor()
        hook = _make_hook(extractor=extractor, runtime=_FakeRuntime(provider))

        await hook.before_iteration(_iter_ctx(*_SWITCHED))  # 不抛

        assert extractor.calls == []  # 按 CONTINUE 处理

    async def test_invalid_json_treated_as_continue(self):
        provider = _FakeProvider(result="not a json object at all")
        extractor = _FakeExtractor()
        hook = _make_hook(extractor=extractor, runtime=_FakeRuntime(provider))

        await hook.before_iteration(_iter_ctx(*_SWITCHED))  # 不抛

        assert extractor.calls == []

    async def test_no_runtime_treated_as_continue(self):
        extractor = _FakeExtractor()  # 无 runtime 属性
        writer = _FakeScratchpadWriter()
        hook = _make_hook(extractor=extractor, writer=writer)

        await hook.before_iteration(_iter_ctx(*_SWITCHED))  # 不抛

        assert extractor.calls == []
        assert writer.focus_calls == []

    async def test_same_transcript_not_detected_twice(self):
        provider = _FakeProvider(result='{"same_topic": true}')
        hook = _make_hook(runtime=_FakeRuntime(provider))
        context = _iter_ctx(*_SWITCHED)

        await hook.before_iteration(context)
        await hook.before_iteration(context)  # 同一 run 内的后续 iteration

        assert len(provider.calls) == 1

    async def test_new_topic_not_retriggered_within_same_transcript(self):
        provider = _FakeProvider(result='{"same_topic": false}')
        extractor = _FakeExtractor()
        hook = _make_hook(extractor=extractor, runtime=_FakeRuntime(provider))
        context = _iter_ctx(*_SWITCHED)

        await hook.before_iteration(context)
        await _drain_background()
        await hook.before_iteration(context)  # 不应二次触发
        await _drain_background()

        assert extractor.calls == []
        assert len(extractor.incremental_calls) == 1
        assert len(provider.calls) == 1

    async def test_cooldown_after_new_within_same_instance(self):
        """同一 hook 实例内：NEW 后需再累积 ≥4 条 user 消息才重新检测。

        生产每轮新建实例，故该冷却跨轮重置（会话 user 数 ≥4 后每轮检测一次）——
        见模块 docstring「代价」。
        """
        provider = _FakeProvider(result='{"same_topic": false}')
        extractor = _FakeExtractor()
        hook = _make_hook(extractor=extractor, runtime=_FakeRuntime(provider))

        await hook.before_iteration(_iter_ctx(*_SWITCHED))  # count=4 → NEW
        await _drain_background()
        assert len(provider.calls) == 1
        assert hook._next_check_count == 8

        # 7 条 < 冷却阈值 8 → 跳过
        await hook.before_iteration(_iter_ctx(*[f"帮我做任务 {i}" for i in range(7)]))
        assert len(provider.calls) == 1

        # 8 条 → 重新检测
        await hook.before_iteration(_iter_ctx(*[f"帮我做任务 {i}" for i in range(8)]))
        assert len(provider.calls) == 2

    async def test_fire_and_forget_does_not_block(self):
        provider = _FakeProvider(result='{"same_topic": false}')
        extractor = _FakeExtractor(delay=10.0)  # 提取很慢
        hook = _make_hook(extractor=extractor, runtime=_FakeRuntime(provider))

        started = time.monotonic()
        await asyncio.wait_for(hook.before_iteration(_iter_ctx(*_SWITCHED)), timeout=0.5)
        assert time.monotonic() - started < 0.5
        assert len(_BACKGROUND_TASKS) == 1  # 后台任务仍在跑，未被阻塞等待

    async def test_prompt_placeholders_are_substituted(self):
        provider = _FakeProvider(result='{"same_topic": false}')
        hook = _make_hook(runtime=_FakeRuntime(provider))

        await hook.before_iteration(_iter_ctx(*_SWITCHED))
        await _drain_background()

        sent_prompt = provider.calls[0][0]["content"]
        assert "{recent_messages}" not in sent_prompt
        assert "{latest_message}" not in sent_prompt
        assert _SWITCHED[-1] in sent_prompt


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------


class TestFactory:
    def test_factory_builds_hook_from_turn_context(self):
        extractor = _FakeExtractor()
        writer = _FakeScratchpadWriter()
        factory = create_memory_extraction_hook_factory(
            extractor_provider=lambda session_key: extractor,
            scratchpad_writer=writer,
        )

        hook = factory(AgentTurnHookContext(session_key="s1"))

        assert isinstance(hook, MemoryExtractionHook)
        assert hook._session_key == "s1"

    def test_factory_returns_none_without_session_key(self):
        factory = create_memory_extraction_hook_factory(
            extractor_provider=lambda session_key: _FakeExtractor(),
            scratchpad_writer=_FakeScratchpadWriter(),
        )
        assert factory(AgentTurnHookContext(session_key=None)) is None

    def test_factory_returns_none_when_provider_returns_none(self):
        factory = create_memory_extraction_hook_factory(
            extractor_provider=lambda session_key: None,
            scratchpad_writer=_FakeScratchpadWriter(),
        )
        assert factory(AgentTurnHookContext(session_key="s1")) is None

    def test_factory_survives_extractor_provider_failure(self):
        def _boom(session_key: str) -> Any:
            raise RuntimeError("no extractor")

        factory = create_memory_extraction_hook_factory(
            extractor_provider=_boom,
            scratchpad_writer=_FakeScratchpadWriter(),
        )
        assert factory(AgentTurnHookContext(session_key="s1")) is None

    def test_factory_wires_runtime_provider(self):
        extractor = _FakeExtractor()
        provider = _FakeProvider(result='{"same_topic": true}')
        runtime = _FakeRuntime(provider)
        factory = create_memory_extraction_hook_factory(
            extractor_provider=lambda session_key: extractor,
            scratchpad_writer=_FakeScratchpadWriter(),
            runtime_provider=lambda session_key: runtime,
        )

        hook = factory(AgentTurnHookContext(session_key="s1"))

        assert isinstance(hook, MemoryExtractionHook)
        assert hook._runtime is runtime

    def test_factory_survives_runtime_provider_failure(self):
        def _boom(session_key: str) -> Any:
            raise RuntimeError("no runtime")

        factory = create_memory_extraction_hook_factory(
            extractor_provider=lambda session_key: _FakeExtractor(),
            scratchpad_writer=_FakeScratchpadWriter(),
            runtime_provider=_boom,
        )

        hook = factory(AgentTurnHookContext(session_key="s1"))

        assert isinstance(hook, MemoryExtractionHook)
        assert hook._runtime is None
