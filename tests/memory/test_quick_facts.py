"""Quick Facts 提取测试（plan §10 Task 9）。

覆盖两部分：

1. ``MemoryExtractor.extract_quick_facts`` —— 纯正则规则信号，不调 LLM；
2. ``AutoCompact`` 的 ``quick_facts_hook`` 注入点（默认无副作用、失败隔离）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.autocompact import AutoCompact
from nanobot.memory.database import MemoryDatabase
from nanobot.memory.extractor import MemoryExtractor
from nanobot.memory.models import Memory, MemoryPriority, MemoryType
from nanobot.memory.repository import list_memories, search_memories
from nanobot.session.manager import Session, SessionManager


class _DummyRuntime:
    """Quick Facts 路径不触碰 runtime；占位以满足构造签名。"""


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> MemoryDatabase:
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return database


def _extractor(db: MemoryDatabase, **kwargs: Any) -> MemoryExtractor:
    return MemoryExtractor(db, _DummyRuntime(), **kwargs)


def _session(messages: list[dict[str, Any]] | None = None, key: str = "s1") -> Session:
    return Session(key=key, messages=messages or [])


def _rows(db: MemoryDatabase) -> list[Memory]:
    with db.connect() as conn:
        return list_memories(conn, workspace_id="default")


def _autocompact(
    *,
    sessions: SessionManager | None = None,
    consolidator: Any = None,
    quick_facts_hook: Any = None,
    ttl: int = 15,
) -> AutoCompact:
    if sessions is None:
        sessions = MagicMock(spec=SessionManager)
    if consolidator is None:
        consolidator = MagicMock()
        consolidator.compact_idle_session = AsyncMock(return_value="Summary.")
    return AutoCompact(
        sessions=sessions,
        consolidator=consolidator,
        session_ttl_minutes=ttl,
        quick_facts_hook=quick_facts_hook,
    )


# ---------------------------------------------------------------------------
# extract_quick_facts
# ---------------------------------------------------------------------------


class TestExtractQuickFacts:
    def test_rule_signal_writes_rule_memory(self, db):
        extractor = _extractor(db)
        session = _session([{"role": "user", "content": "以后都用 uv 管理依赖"}])

        written = extractor.extract_quick_facts(session)

        assert written == 1
        rows = _rows(db)
        assert len(rows) == 1
        assert rows[0].type is MemoryType.RULE
        assert rows[0].source == "extraction"
        assert "uv" in rows[0].content

    def test_repeated_call_is_idempotent(self, db):
        extractor = _extractor(db)
        session = _session([{"role": "user", "content": "以后都用 uv 管理依赖"}])

        first = extractor.extract_quick_facts(session)
        second = extractor.extract_quick_facts(session)

        assert first == 1
        assert second == 0
        assert len(_rows(db)) == 1

    def test_chat_message_produces_nothing(self, db):
        extractor = _extractor(db)
        session = _session([{"role": "user", "content": "你好"}])

        assert extractor.extract_quick_facts(session) == 0
        assert _rows(db) == []

    def test_empty_messages_returns_zero(self, db):
        extractor = _extractor(db)

        assert extractor.extract_quick_facts(_session([])) == 0
        assert _rows(db) == []

    def test_short_term_priority_when_time_keyword_present(self, db):
        extractor = _extractor(db)
        session = _session([{"role": "user", "content": "以后都先试试 X，今天先这样"}])

        assert extractor.extract_quick_facts(session) == 1
        rows = _rows(db)
        assert len(rows) == 1
        assert rows[0].priority is MemoryPriority.SHORT_TERM

    def test_long_term_priority_without_time_keyword(self, db):
        extractor = _extractor(db)
        session = _session([{"role": "user", "content": "以后都用 uv 管理依赖"}])

        assert extractor.extract_quick_facts(session) == 1
        assert _rows(db)[0].priority is MemoryPriority.LONG_TERM

    def test_memory_is_searchable(self, db):
        extractor = _extractor(db)
        session = _session([{"role": "user", "content": "以后都用 uv 管理依赖"}])

        assert extractor.extract_quick_facts(session) == 1
        with db.connect() as conn:
            hits = search_memories(conn, "uv")
        assert len(hits) == 1
        assert hits[0].type is MemoryType.RULE

    def test_task_artifact_fragment_is_filtered(self, db):
        extractor = _extractor(db)
        session = _session([{"role": "user", "content": "帮我生成报告，必须每次都要"}])

        assert extractor.extract_quick_facts(session) == 0
        assert _rows(db) == []

    def test_ai_self_talk_fragment_is_filtered(self, db):
        extractor = _extractor(db)
        session = _session([{"role": "user", "content": "我建议以后都用 uv"}])

        assert extractor.extract_quick_facts(session) == 0
        assert _rows(db) == []

    def test_assistant_messages_are_ignored(self, db):
        extractor = _extractor(db)
        session = _session([{"role": "assistant", "content": "以后都用 uv"}])

        assert extractor.extract_quick_facts(session) == 0
        assert _rows(db) == []

    def test_internal_failure_is_swallowed(self, db, monkeypatch):
        extractor = _extractor(db)
        session = _session([{"role": "user", "content": "以后都用 uv"}])
        monkeypatch.setattr(extractor, "_persist", MagicMock(side_effect=RuntimeError("boom")))

        assert extractor.extract_quick_facts(session) == 0


# ---------------------------------------------------------------------------
# AutoCompact.quick_facts_hook
# ---------------------------------------------------------------------------


class TestAutoCompactQuickFactsHook:
    @pytest.mark.asyncio
    async def test_hook_invoked_after_successful_compaction(self):
        session = _session(key="cli:test")
        sessions = MagicMock(spec=SessionManager)
        sessions.get_or_create.return_value = session
        hook = MagicMock(return_value=2)
        ac = _autocompact(sessions=sessions, quick_facts_hook=hook)

        result = await ac._archive("cli:test", runtime=MagicMock())

        assert result is None
        hook.assert_called_once_with(session)
        ac.consolidator.compact_idle_session.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_hook_invoked_on_empty_summary(self):
        session = _session(key="cli:test")
        sessions = MagicMock(spec=SessionManager)
        sessions.get_or_create.return_value = session
        consolidator = MagicMock()
        consolidator.compact_idle_session = AsyncMock(return_value="(nothing)")
        hook = MagicMock(return_value=0)
        ac = _autocompact(sessions=sessions, consolidator=consolidator, quick_facts_hook=hook)

        await ac._archive("cli:test", runtime=MagicMock())

        hook.assert_called_once_with(session)

    @pytest.mark.asyncio
    async def test_hook_exception_does_not_break_archive(self):
        session = _session(key="cli:test")
        sessions = MagicMock(spec=SessionManager)
        sessions.get_or_create.return_value = session
        hook = MagicMock(side_effect=RuntimeError("boom"))
        ac = _autocompact(sessions=sessions, quick_facts_hook=hook)

        result = await ac._archive("cli:test", runtime=MagicMock())

        assert result is None
        hook.assert_called_once()
        ac.consolidator.compact_idle_session.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_none_hook_has_no_side_effects(self):
        sessions = MagicMock(spec=SessionManager)
        consolidator = MagicMock()
        consolidator.compact_idle_session = AsyncMock(return_value="(nothing)")
        ac = _autocompact(sessions=sessions, consolidator=consolidator)
        assert ac._quick_facts_hook is None

        result = await ac._archive("cli:test", runtime=MagicMock())

        assert result is None
        sessions.get_or_create.assert_not_called()
        ac.consolidator.compact_idle_session.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_hook_not_invoked_when_compaction_fails(self):
        sessions = MagicMock(spec=SessionManager)
        consolidator = MagicMock()
        consolidator.compact_idle_session = AsyncMock(side_effect=RuntimeError("fail"))
        hook = MagicMock(return_value=1)
        ac = _autocompact(sessions=sessions, consolidator=consolidator, quick_facts_hook=hook)

        result = await ac._archive("cli:test", runtime=MagicMock())

        assert result is None
        hook.assert_not_called()
        sessions.get_or_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_hook_skipped_for_internal_session(self):
        sessions = MagicMock(spec=SessionManager)
        hook = MagicMock(return_value=1)
        ac = _autocompact(sessions=sessions, quick_facts_hook=hook)

        result = await ac._archive("dream:20260602-155256", runtime=MagicMock())

        assert result is None
        hook.assert_not_called()
        ac.consolidator.compact_idle_session.assert_not_awaited()
