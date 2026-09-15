"""WU-A Task 2：``MemoryExtractor.run_idle_extraction`` 的契约测试。

覆盖 plan 描述的三条契约:
- 使用 ``state.last_count`` 作为增量起点；
- 不会触发 LLM 调用（``run_idle`` 只走纯规则扫描路径）；
- ``current_count == state.last_count`` 时为 no-op,不调底层抽取。

注: ``run_idle_extraction`` 内部走 ``extract_incremental``,而后者仅扫
``session.messages[last_extracted_index:]`` 的规则信号,不会调 LLM。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.extractor import MemoryExtractor
from nanobot.memory.repository import (
    get_extraction_state,
    upsert_extraction_state,
)
from nanobot.session.manager import Session


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
    """记录所有 chat_with_retry 调用,且保证不被调用。"""

    def __init__(self) -> None:
        self.calls: list[list[dict[str, Any]]] = []

    async def chat_with_retry(
        self, *, messages: list[dict[str, Any]], **_kwargs: Any
    ) -> _FakeResponse:
        self.calls.append(messages)
        return _FakeResponse(None)


class _FakeRuntime:
    def __init__(self, provider: _FakeProvider) -> None:
        self.provider = provider
        self.model = "fake-model"
        self.generation = _FakeGeneration()


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> MemoryDatabase:
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return database


def _make_session(messages: list[dict[str, Any]], *, key: str = "s1") -> Session:
    return Session(key=key, messages=list(messages))


def _build_messages() -> list[dict[str, Any]]:
    """5 条 user 消息,每条都含规则信号词以触发 _collect_rule_signals。"""
    rule_msgs = [
        "以后都用 uv 管理依赖",
        "每次保存前必须跑测试",
        "禁止向 main 直接 push",
        "总是用 ruff 检查代码",
        "记住数据库密码用 vault",
    ]
    out: list[dict[str, Any]] = []
    for text in rule_msgs:
        out.append({"role": "user", "content": text})
        out.append({"role": "assistant", "content": "好"})
    return out


# ---------------------------------------------------------------------------
# 契约测试
# ---------------------------------------------------------------------------


class TestRunIdleExtractionContract:
    async def test_run_idle_uses_state_start_index(self, db: MemoryDatabase):
        """``state.last_count=3`` 时,只扫 messages[3:],不调 LLM,落 3 条 RULE。

        5 条 user 消息共 10 步。messages[3:] 包含索引 4/6/8 共 3 条 user,
        所以预期落 3 条 RULE。
        """
        provider = _FakeProvider()
        runtime = _FakeRuntime(provider)
        extractor = MemoryExtractor(db, runtime, user_id="u1", workspace_id="ws")
        messages = _build_messages()

        # 预置 state.last_count=3,代表前 3 步已抽过。
        session = _make_session(messages, key="s_incremental")
        with db.connect() as conn:
            upsert_extraction_state(
                conn,
                "s_incremental",
                last_count=3,
                source="topic_change",
                extracted_at="2026-09-15T09:00:00+00:00",
            )

        result = await extractor.run_idle_extraction(session)

        # run_idle 不调 LLM(provider 必须 0 调用)。
        assert provider.calls == []
        # messages[3:] 含 3 条 user(索引 4/6/8),所以应落 3 条 RULE。
        assert len(result.memory_ids) == 3
        # state 必须推进到 current_count,且 source 改为 'idle'。
        with db.connect() as conn:
            advanced = get_extraction_state(conn, "s_incremental")
        assert advanced is not None
        assert advanced.last_count == len(messages)
        assert advanced.last_source == "idle"

    async def test_run_idle_failure_does_not_advance(self, db: MemoryDatabase):
        """``upsert_extraction_state`` 抛错时,state 必须不被推进一步。

        通过 monkeypatch 让 ``upsert_extraction_state`` 抛 RuntimeError,
        模拟 state 推进失败:必须保证调用方不抛异常,且 state 保持预置值
        (last_count=2, last_source='topic_change')。
        """
        provider = _FakeProvider()
        runtime = _FakeRuntime(provider)
        extractor = MemoryExtractor(db, runtime, user_id="u1", workspace_id="ws")
        messages = _build_messages()

        with db.connect() as conn:
            upsert_extraction_state(
                conn,
                "s_upsert_fail",
                last_count=2,
                source="topic_change",
                extracted_at="2026-09-15T09:00:00+00:00",
            )

        # Patch upsert_extraction_state 让它抛错,模拟 DB 写失败。
        from nanobot.memory import extractor as ext_module

        original_upsert = ext_module.upsert_extraction_state

        def _boom(_conn, _session_key, **_kwargs):
            raise RuntimeError("simulated upsert failure")

        ext_module.upsert_extraction_state = _boom
        try:
            session = _make_session(messages, key="s_upsert_fail")
            result = await extractor.run_idle_extraction(session)
        finally:
            ext_module.upsert_extraction_state = original_upsert

        # 调用方不抛,拿到正常 result。
        assert result is not None
        # 不调 LLM。
        assert provider.calls == []
        # state 推进失败被 inner try/except 吞掉:extract_incremental 仍正常
        # 返回其抽取结果(可能有 memory_ids),但 state 必须保持预置值不动。
        with db.connect() as conn:
            post = get_extraction_state(conn, "s_upsert_fail")
        assert post is not None
        assert post.last_count == 2  # 关键: 保持预置值,未被推进
        assert post.last_source == "topic_change"
        assert post.last_extracted_at == "2026-09-15T09:00:00+00:00"

    async def test_run_idle_no_op_when_current_equals_state(self, db: MemoryDatabase):
        """``current_count == last_count``: 不调底层抽取,state 不动,不调 LLM。"""
        provider = _FakeProvider()
        runtime = _FakeRuntime(provider)
        extractor = MemoryExtractor(db, runtime, user_id="u1", workspace_id="ws")
        messages = _build_messages()
        current_count = len(messages)

        # 预置 state.last_count = current_count,模拟已抽完。
        with db.connect() as conn:
            upsert_extraction_state(
                conn,
                "s_noop",
                last_count=current_count,
                source="idle",
                extracted_at="2026-09-15T08:00:00+00:00",
            )

        # monkeypatch _collect_rule_signals 验证它没被调(若被调会失败)。
        from nanobot.memory import extractor as ext_module

        original = ext_module._collect_rule_signals
        called: list[Any] = []

        def _spy(msgs):
            called.append(msgs)
            return original(msgs)

        ext_module._collect_rule_signals = _spy
        try:
            session = _make_session(messages, key="s_noop")
            result = await extractor.run_idle_extraction(session)
        finally:
            ext_module._collect_rule_signals = original

        # 必须 no-op: 0 调用,返回空,不调 LLM。
        assert called == []
        assert result.memory_ids == []
        assert result.episode_ids == []
        assert provider.calls == []
        # state 不变。
        with db.connect() as conn:
            post = get_extraction_state(conn, "s_noop")
        assert post is not None
        assert post.last_count == current_count
        assert post.last_extracted_at == "2026-09-15T08:00:00+00:00"

    async def test_run_idle_no_state_treats_as_zero(self, db: MemoryDatabase):
        """state 行缺失时按 ``last_count=0`` 处理(首跑 = 全量增量扫描)。"""
        provider = _FakeProvider()
        runtime = _FakeRuntime(provider)
        extractor = MemoryExtractor(db, runtime, user_id="u1", workspace_id="ws")
        session = _make_session(_build_messages(), key="s_first")

        # 不预置任何 state。
        result = await extractor.run_idle_extraction(session)
        # 至少 3 条 RULE 落库(5 条 user 都命中规则信号)。
        assert len(result.memory_ids) >= 3
        # 不调 LLM。
        assert provider.calls == []
        # state 被创建并推进。
        with db.connect() as conn:
            post = get_extraction_state(conn, "s_first")
        assert post is not None
        assert post.last_count == len(session.messages)
        assert post.last_source == "idle"

    async def test_run_idle_pure_rule_scan_no_llm_call(self, db: MemoryDatabase):
        """显式断言: ``run_idle_extraction`` 全程不应调 LLM。

        即使有 user 消息含规则信号词,provider.calls 必须始终为空。
        """
        provider = _FakeProvider()
        runtime = _FakeRuntime(provider)
        extractor = MemoryExtractor(db, runtime, user_id="u1", workspace_id="ws")

        # 多次 run_idle_extraction,验证 LLM 从不被触发。
        session = _make_session(_build_messages(), key="s_pure")
        await extractor.run_idle_extraction(session)
        await extractor.run_idle_extraction(_make_session([], key="s_pure"))
        await extractor.run_idle_extraction(
            _make_session(_build_messages()[:2], key="s_pure")
        )

        assert provider.calls == []
