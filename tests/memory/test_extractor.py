"""Tests for MemoryExtractor（plan §10 Task 3 / 4 / 5）。

覆盖四个阶段：系统提取、LLM 双路提取、防污染过滤、持久化。
使用可编程 FakeLLM（鸭子类型）注入 runtime，避免依赖真实 provider。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from loguru import logger

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.extractor import (
    LLMEpisodeItem,
    LLMExtractionResult,
    LLMMemoryItem,
    MemoryExtractor,
    SystemExtractionResult,
    _collect_action_nodes,
    _coerce_memory_item,
)
from nanobot.memory.models import (
    EpisodeSource,
    Memory,
    MemoryPriority,
    MemoryType,
    ScratchpadEntry,
)
from nanobot.memory.prompts import (
    EPISODE_EXTRACTION_PROMPT,
    SEMANTIC_EXTRACTION_PROMPT,
)
from nanobot.memory.repository import (
    add_memory,
    get_episode,
    get_scratchpad,
    list_memories,
    search_memories,
    upsert_scratchpad,
)
from nanobot.session.manager import Session

# ---------------------------------------------------------------------------
# Fake LLM（鸭子类型，不依赖真实 provider）
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeGeneration:
    temperature = 0.2
    max_tokens = 2048
    reasoning_effort = None


class _FakeProvider:
    """可编程 provider：按 prompt 内容分流返回语义/情节结果。

    ``semantic`` / ``episode`` 可为 str（返回 content）、BaseException（抛出）、
    或 None（无内容）。``*_delay`` 用于制造并发与超时场景。
    """

    def __init__(
        self,
        *,
        semantic: Any = None,
        episode: Any = None,
        semantic_delay: float = 0.0,
        episode_delay: float = 0.0,
    ) -> None:
        self.semantic = semantic
        self.episode = episode
        self.semantic_delay = semantic_delay
        self.episode_delay = episode_delay
        self.calls: list[list[dict[str, Any]]] = []
        self.tracks: list[str] = []
        self.active = 0
        self.max_active = 0

    async def chat_with_retry(self, *, messages: list[dict[str, Any]], **_kwargs: Any) -> _FakeResponse:
        self.calls.append(messages)
        text = " ".join(
            m.get("content", "") for m in messages if isinstance(m.get("content"), str)
        )
        is_semantic = "语义记忆抽取" in text
        self.tracks.append("semantic" if is_semantic else "episode")
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            delay = self.semantic_delay if is_semantic else self.episode_delay
            if delay:
                await asyncio.sleep(delay)
            result = self.semantic if is_semantic else self.episode
            if isinstance(result, BaseException):
                raise result
            return _FakeResponse(result)
        finally:
            self.active -= 1


class _FakeRuntime:
    def __init__(self, provider: _FakeProvider, model: str = "fake-model") -> None:
        self.provider = provider
        self.model = model
        self.generation = _FakeGeneration()
        self.context_window_tokens = 8192


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

EPISODE_JSON = json.dumps(
    {
        "summary": "用户通过 nanobot 配置了 uv 依赖管理",
        "goal": "配置 uv 管理 Python 依赖",
        "outcome": "completed",
        "entities": ["uv"],
        "tools_used": ["bash"],
    },
    ensure_ascii=False,
)


def _semantic_json(
    memories: list[dict[str, Any]] | None = None,
    experiences: list[dict[str, Any]] | None = None,
) -> str:
    return json.dumps(
        {"memories": memories or [], "experiences": experiences or []},
        ensure_ascii=False,
    )


def _tool_call(name: str, arguments: str, call_id: str) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _session(messages: list[dict[str, Any]] | None = None, key: str = "s1") -> Session:
    return Session(key=key, messages=messages or [])


def _plain_session() -> Session:
    return _session([{"role": "user", "content": "配置 uv。"}])


def _make_extractor(
    db: MemoryDatabase,
    provider: _FakeProvider,
    **kwargs: Any,
) -> MemoryExtractor:
    return MemoryExtractor(db, _FakeRuntime(provider), **kwargs)


@pytest.fixture
def db(tmp_path: Path) -> MemoryDatabase:
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return database


def _capture_warnings(records: list[str]) -> int:
    return logger.add(records.append, level="WARNING", format="{message}")


def _memory_rows(db: MemoryDatabase) -> list[Memory]:
    with db.connect() as conn:
        return list_memories(conn, workspace_id="default")


# ---------------------------------------------------------------------------
# 阶段1：系统提取（Task 3）
# ---------------------------------------------------------------------------


class TestSystemExtract:
    def test_captures_action_nodes_from_tool_calls(self, db):
        session = _session(
            [
                {"role": "user", "content": "跑一下测试"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        _tool_call("read_file", '{"path": "a.py"}', "c1"),
                        _tool_call("bash", '{"cmd": "pytest"}', "c2"),
                        _tool_call("write_file", '{"path": "b.py"}', "c3"),
                    ],
                },
            ]
        )
        extractor = _make_extractor(db, _FakeProvider())
        result = extractor._system_extract(session)

        assert len(result.action_nodes) >= 3
        assert {n.tool for n in result.action_nodes} == {"read_file", "bash", "write_file"}
        assert result.action_nodes[0].input == '{"path": "a.py"}'

    def test_action_node_matches_tool_result_and_marks_failure(self, db):
        session = _session(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [_tool_call("bash", '{"cmd": "pytest"}', "c1")],
                },
                {
                    "role": "tool",
                    "tool_call_id": "c1",
                    "name": "bash",
                    "content": "Traceback (most recent call last):\n  ...Error",
                },
            ]
        )
        extractor = _make_extractor(db, _FakeProvider())
        nodes = extractor._system_extract(session).action_nodes

        assert len(nodes) == 1
        assert nodes[0].success is False
        assert "Traceback" in nodes[0].output

    def test_rule_signal_hit(self, db):
        session = _session([{"role": "user", "content": "必须每次保存日志。其他随意。"}])
        extractor = _make_extractor(db, _FakeProvider())
        signals = extractor._system_extract(session).rule_signals

        assert signals
        assert any("必须" in s for s in signals)

    def test_empty_messages_returns_empty_result(self, db):
        extractor = _make_extractor(db, _FakeProvider())
        result = extractor._system_extract(_session([]))

        assert result.action_nodes == []
        assert result.rule_signals == []
        assert result.scratchpad_snapshot is None

    def test_reads_scratchpad_snapshot(self, db):
        with db.connect() as conn:
            upsert_scratchpad(
                conn,
                ScratchpadEntry(
                    user_id="default",
                    workspace_id="default",
                    updated_at="2026-09-10T00:00:00+00:00",
                    current_focus="配置 uv",
                ),
            )
        extractor = _make_extractor(db, _FakeProvider())
        snapshot = extractor._system_extract(_plain_session()).scratchpad_snapshot

        assert snapshot is not None
        assert snapshot.current_focus == "配置 uv"


# ---------------------------------------------------------------------------
# 阶段2：LLM 提取（Task 4）
# ---------------------------------------------------------------------------


class TestLLMExtract:
    async def test_uses_semantic_and_episode_prompts(self, db):
        provider = _FakeProvider(semantic=_semantic_json(), episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        await extractor._llm_extract(_plain_session(), SystemExtractionResult())

        combined = "".join(
            m["content"] for call in provider.calls for m in call if isinstance(m.get("content"), str)
        )
        assert SEMANTIC_EXTRACTION_PROMPT in combined
        assert EPISODE_EXTRACTION_PROMPT in combined
        assert len(provider.calls) == 2

    async def test_two_tracks_run_concurrently(self, db):
        provider = _FakeProvider(
            semantic=_semantic_json(),
            episode=EPISODE_JSON,
            semantic_delay=0.05,
            episode_delay=0.05,
        )
        extractor = _make_extractor(db, provider)

        await extractor._llm_extract(_plain_session(), SystemExtractionResult())

        assert provider.max_active >= 2

    async def test_parses_memories_and_experiences(self, db):
        semantic = _semantic_json(
            memories=[
                {
                    "content": "用户偏好 uv",
                    "type": "PREFERENCE",
                    "priority": "long_term",
                    "importance": 0.8,
                    "tags": ["uv"],
                    "subject": "用户",
                    "predicate": "偏好",
                }
            ],
            experiences=[
                {
                    "content": "用 uv sync 安装依赖可保持锁文件同步",
                    "type": "EXPERIENCE",
                    "priority": "short_term",
                    "importance": 0.6,
                    "tags": ["uv"],
                }
            ],
        )
        provider = _FakeProvider(semantic=semantic, episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        result = await extractor._llm_extract(_plain_session(), SystemExtractionResult())

        assert len(result.memories) == 2
        assert {m.content for m in result.memories} == {
            "用户偏好 uv",
            "用 uv sync 安装依赖可保持锁文件同步",
        }
        assert result.memories[0].type == "PREFERENCE"
        assert result.memories[1].type == "EXPERIENCE"

    async def test_parses_episode(self, db):
        provider = _FakeProvider(semantic=_semantic_json(), episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        result = await extractor._llm_extract(_plain_session(), SystemExtractionResult())

        assert result.episode is not None
        assert result.episode.outcome == "completed"
        assert result.episode.entities == ["uv"]
        assert result.episode.tools_used == ["bash"]

    async def test_tolerates_json_code_fence(self, db):
        fenced = f"```json\n{EPISODE_JSON}\n```"
        provider = _FakeProvider(semantic=_semantic_json(), episode=fenced)
        extractor = _make_extractor(db, provider)

        result = await extractor._llm_extract(_plain_session(), SystemExtractionResult())

        assert result.episode is not None
        assert result.failed_tracks == []

    async def test_semantic_parse_failure_is_isolated(self, db):
        provider = _FakeProvider(semantic="这不是 JSON", episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)
        system = SystemExtractionResult(action_nodes=[])

        result = await extractor._llm_extract(_plain_session(), system)

        assert result.memories == []
        assert result.episode is not None
        assert result.failed_tracks == ["semantic"]

    async def test_both_tracks_failed_returns_empty(self, db):
        provider = _FakeProvider(semantic="坏内容", episode="也是坏内容")
        extractor = _make_extractor(db, provider)

        result = await extractor._llm_extract(_plain_session(), SystemExtractionResult())

        assert result.memories == []
        assert result.episode is None
        assert set(result.failed_tracks) == {"semantic", "episode"}

    async def test_both_tracks_return_none_returns_empty(self, db):
        provider = _FakeProvider(semantic="NONE", episode="NONE")
        extractor = _make_extractor(db, provider)

        result = await extractor._llm_extract(_plain_session(), SystemExtractionResult())

        assert result.memories == []
        assert result.episode is None
        assert result.failed_tracks == []

    async def test_timeout_on_one_track_persists_the_other(self, db):
        semantic = _semantic_json(
            memories=[
                {
                    "content": "用户偏好 uv",
                    "type": "PREFERENCE",
                    "priority": "long_term",
                    "subject": "用户",
                    "predicate": "偏好",
                }
            ]
        )
        provider = _FakeProvider(
            semantic=semantic,
            episode=EPISODE_JSON,
            semantic_delay=0.5,
        )
        extractor = _make_extractor(db, provider)
        extractor.LLM_TIMEOUT = 0.05

        result = await extractor.extract_session(_plain_session())

        assert result.failed_tracks == ["semantic"]
        assert result.memory_ids == []
        assert len(result.episode_ids) == 1
        with db.connect() as conn:
            episode = get_episode(conn, result.episode_ids[0])
        assert episode is not None
        assert episode.summary == "用户通过 nanobot 配置了 uv 依赖管理"

    async def test_scratchpad_snapshot_included_in_prompt(self, db):
        with db.connect() as conn:
            upsert_scratchpad(
                conn,
                ScratchpadEntry(
                    user_id="default",
                    workspace_id="default",
                    updated_at="2026-09-10T00:00:00+00:00",
                    current_focus="snapshot-focus-xyz",
                ),
            )
        provider = _FakeProvider(semantic=_semantic_json(), episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)
        system = extractor._system_extract(_plain_session())

        await extractor._llm_extract(_plain_session(), system)

        combined = "".join(
            m["content"]
            for call in provider.calls
            for m in call
            if isinstance(m.get("content"), str)
        )
        assert "snapshot-focus-xyz" in combined


# ---------------------------------------------------------------------------
# 阶段3 + 阶段4：过滤与持久化（Task 5）
# ---------------------------------------------------------------------------


class TestFiltersAndPersistence:
    async def test_task_artifact_is_blocked(self, db):
        semantic = _semantic_json(
            memories=[
                {
                    "content": "帮我生成苹果照片",
                    "type": "PREFERENCE",
                    "priority": "long_term",
                }
            ]
        )
        provider = _FakeProvider(semantic=semantic, episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        result = await extractor.extract_session(_plain_session())

        assert result.memory_ids == []
        assert result.skipped == 1
        assert _memory_rows(db) == []

    async def test_ai_self_talk_is_blocked(self, db):
        semantic = _semantic_json(
            memories=[
                {"content": "我建议使用 uv", "type": "PREFERENCE", "priority": "long_term"}
            ]
        )
        provider = _FakeProvider(semantic=semantic, episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        result = await extractor.extract_session(_plain_session())

        assert result.memory_ids == []
        assert result.skipped == 1

    async def test_ngram_blocks_near_duplicate(self, db):
        with db.connect() as conn:
            add_memory(
                conn,
                Memory(
                    id="m1",
                    content="用户喜欢 Python 编程语言",
                    type=MemoryType.PREFERENCE,
                    subject="用户",
                    predicate="喜欢",
                    created_at="2026-09-09T00:00:00+00:00",
                    workspace_id="default",
                ),
            )
        semantic = _semantic_json(
            memories=[
                {
                    "content": "用户喜欢 Python 编程语言",
                    "type": "PREFERENCE",
                    "priority": "long_term",
                    "subject": "使用者",
                    "predicate": "喜欢",
                }
            ]
        )
        provider = _FakeProvider(semantic=semantic, episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        result = await extractor.extract_session(_plain_session())

        assert result.memory_ids == []
        assert result.skipped == 1

    async def test_exact_duplicate_is_blocked(self, db):
        with db.connect() as conn:
            add_memory(
                conn,
                Memory(
                    id="m1",
                    content="用户偏好 uv",
                    type=MemoryType.PREFERENCE,
                    subject="用户",
                    predicate="偏好",
                    created_at="2026-09-09T00:00:00+00:00",
                    workspace_id="default",
                ),
            )
        semantic = _semantic_json(
            memories=[
                {
                    "content": "用户偏好 uv",
                    "type": "PREFERENCE",
                    "priority": "long_term",
                    "subject": "用户",
                    "predicate": "偏好",
                }
            ]
        )
        provider = _FakeProvider(semantic=semantic, episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        result = await extractor.extract_session(_plain_session())

        assert result.memory_ids == []
        assert result.skipped == 1

    async def test_short_term_priority_persisted(self, db):
        semantic = _semantic_json(
            memories=[
                {
                    "content": "用户暂时使用 uv 管理依赖",
                    "type": "PREFERENCE",
                    "priority": "short_term",
                    "subject": "用户",
                    "predicate": "使用",
                }
            ]
        )
        provider = _FakeProvider(semantic=semantic, episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        result = await extractor.extract_session(_plain_session())

        assert len(result.memory_ids) == 1
        rows = _memory_rows(db)
        assert rows[0].priority is MemoryPriority.SHORT_TERM
        assert rows[0].source == "extraction"

    async def test_missing_priority_uses_fallback(self, db):
        semantic = _semantic_json(
            memories=[
                {"content": "用户今天在调试依赖", "type": "FACT", "subject": "用户"},
                {"content": "用户常用 pytest", "type": "FACT", "subject": "用户"},
            ]
        )
        provider = _FakeProvider(semantic=semantic, episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        result = await extractor.extract_session(_plain_session())

        priorities = {m.content: m.priority for m in _memory_rows(db)}
        assert len(result.memory_ids) == 2
        assert priorities["用户今天在调试依赖"] is MemoryPriority.SHORT_TERM
        assert priorities["用户常用 pytest"] is MemoryPriority.LONG_TERM

    async def test_invalid_priority_is_skipped_with_warning(self, db):
        semantic = _semantic_json(
            memories=[
                {
                    "content": "用户偏好 Go 语言",
                    "type": "PREFERENCE",
                    "priority": "invalid",
                }
            ]
        )
        provider = _FakeProvider(semantic=semantic, episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        records: list[str] = []
        sink_id = _capture_warnings(records)
        try:
            result = await extractor.extract_session(_plain_session())
        finally:
            logger.remove(sink_id)

        assert result.memory_ids == []
        assert result.skipped == 1
        assert any("invalid priority" in r for r in records)

    async def test_invalid_type_is_skipped(self, db):
        semantic = _semantic_json(
            memories=[{"content": "用户偏好 Rust", "type": "BOGUS", "priority": "long_term"}]
        )
        provider = _FakeProvider(semantic=semantic, episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        result = await extractor.extract_session(_plain_session())

        assert result.memory_ids == []
        assert result.skipped == 1

    async def test_valid_memory_is_searchable(self, db):
        semantic = _semantic_json(
            memories=[
                {
                    "content": "用户偏好使用 uv 管理 Python 依赖",
                    "type": "PREFERENCE",
                    "priority": "long_term",
                    "subject": "用户",
                    "predicate": "偏好",
                }
            ]
        )
        provider = _FakeProvider(semantic=semantic, episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        result = await extractor.extract_session(_plain_session())

        assert len(result.memory_ids) == 1
        with db.connect() as conn:
            hits = search_memories(conn, "uv")
        assert any(h.id == result.memory_ids[0] for h in hits)

    async def test_linked_memory_ids_reverse_link(self, db):
        semantic = _semantic_json(
            memories=[
                {
                    "content": "uv 用于管理 Python 依赖",
                    "type": "SKILL",
                    "priority": "long_term",
                    "subject": "uv",
                    "predicate": "用于",
                }
            ]
        )
        provider = _FakeProvider(semantic=semantic, episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        result = await extractor.extract_session(_plain_session())

        with db.connect() as conn:
            episode = get_episode(conn, result.episode_ids[0])
        assert episode is not None
        assert episode.linked_memory_ids == [result.memory_ids[0]]

    async def test_source_mapping(self, db):
        provider = _FakeProvider(semantic=_semantic_json(), episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        default_result = await extractor.extract_session(_plain_session())
        compress_result = await extractor.extract_session(
            _plain_session(), source="context_compress"
        )
        deletion_result = await extractor.extract_session(_plain_session(), source="deletion")

        with db.connect() as conn:
            default_ep = get_episode(conn, default_result.episode_ids[0])
            compress_ep = get_episode(conn, compress_result.episode_ids[0])
            deletion_ep = get_episode(conn, deletion_result.episode_ids[0])
        assert default_ep is not None and default_ep.source is EpisodeSource.SESSION_END
        assert compress_ep is not None and compress_ep.source is EpisodeSource.CONTEXT_COMPRESS
        assert deletion_ep is not None and deletion_ep.source is EpisodeSource.DELETION

    async def test_deletion_source_persists_to_db_column(self, db):
        """FIX-2 Sec-C-β：``source="deletion"`` 持久化后 DB 行 ``source`` 列 = ``"deletion"``。"""
        provider = _FakeProvider(semantic=_semantic_json(), episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        result = await extractor.extract_session(_plain_session(), source="deletion")

        with db.connect() as conn:
            row = conn.execute(
                "SELECT source FROM episodes WHERE id = ?",
                (result.episode_ids[0],),
            ).fetchone()
        assert row is not None
        assert row["source"] == "deletion"

    @pytest.mark.parametrize(
        ("raw_outcome", "expected"),
        [
            ("success", "completed"),
            ("ongoing", "ongoing"),
            ("invalid", "completed"),
            ("partial", "partial"),
            (None, "completed"),
        ],
    )
    async def test_outcome_normalization(self, db, raw_outcome, expected):
        episode = json.dumps(
            {"summary": "一次任务", "goal": "目标", "outcome": raw_outcome, "entities": []},
            ensure_ascii=False,
        )
        provider = _FakeProvider(semantic=_semantic_json(), episode=episode)
        extractor = _make_extractor(db, provider)

        result = await extractor.extract_session(_plain_session())

        with db.connect() as conn:
            episode_row = get_episode(conn, result.episode_ids[0])
        assert episode_row is not None
        assert episode_row.outcome.value == expected

    async def test_episode_persisted_with_action_nodes_when_llm_fails(self, db):
        provider = _FakeProvider(semantic="坏", episode="坏")
        extractor = _make_extractor(db, provider)
        session = _session(
            [
                {"role": "user", "content": "跑测试"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [_tool_call("bash", '{"cmd": "pytest"}', "c1")],
                },
            ]
        )

        result = await extractor.extract_session(session)

        assert result.episode_ids != []
        assert "semantic" in result.failed_tracks
        with db.connect() as conn:
            episode = get_episode(conn, result.episode_ids[0])
        assert episode is not None
        assert episode.summary == ""
        assert len(episode.action_nodes) == 1
        assert episode.action_nodes[0]["tool"] == "bash"

    async def test_empty_session_writes_nothing(self, db):
        provider = _FakeProvider(semantic="NONE", episode="NONE")
        extractor = _make_extractor(db, provider)

        result = await extractor.extract_session(_session([]))

        assert result.memory_ids == []
        assert result.episode_ids == []
        assert result.skipped == 0

    async def test_scratchpad_upsert_is_idempotent(self, db):
        first = ScratchpadEntry(
            user_id="default",
            workspace_id="default",
            updated_at="2026-09-10T00:00:00+00:00",
            current_focus="第一次",
        )
        second = ScratchpadEntry(
            user_id="default",
            workspace_id="default",
            updated_at="2026-09-10T01:00:00+00:00",
            current_focus="第二次",
        )
        with db.connect() as conn:
            upsert_scratchpad(conn, first)
            upsert_scratchpad(conn, second)
        with db.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM scratchpad").fetchone()[0]
            entry = get_scratchpad(conn, "default", "default")

        assert count == 1
        assert entry is not None
        assert entry.current_focus == "第二次"


# ---------------------------------------------------------------------------
# 契约级单测（dataclass 默认值 / 独立方法）
# ---------------------------------------------------------------------------


class TestContracts:
    def test_llm_extraction_result_defaults(self):
        result = LLMExtractionResult()
        assert result.memories == []
        assert result.episode is None
        assert result.failed_tracks == []
        assert result.action_nodes == []

    def test_apply_filters_passes_through_action_nodes(self, db):
        extractor = _make_extractor(db, _FakeProvider())
        system = SystemExtractionResult()
        llm_result = LLMExtractionResult(action_nodes=list(system.action_nodes))
        filtered = extractor._apply_filters(llm_result, [])

        assert filtered.memories == []
        assert filtered.episode is None
        assert filtered.action_nodes == []

    def test_llm_memory_item_and_episode_item_defaults(self):
        item = LLMMemoryItem(content="c", type="FACT")
        assert item.priority is None
        assert item.importance == 0.7
        assert item.subject == ""
        assert item.tags == []

        episode = LLMEpisodeItem()
        assert episode.summary == ""
        assert episode.outcome is None
        assert episode.entities == []
        assert episode.importance == 0.7


# ---------------------------------------------------------------------------
# FIX-3 Sec-M-1: ActionNode redact + truncate
# ---------------------------------------------------------------------------


class TestCollectActionNodes:
    """FIX-3 Sec-M-1: ActionNode 的 input/output 必须先 redact 再截断。"""

    def test_redacts_api_key_in_output(self):
        """含 ``api_key=xxx`` 的输出应被 ``<redacted>`` 替换。"""
        session = _session(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [_tool_call("bash", '{"cmd": "env"}', "c1")],
                },
                {
                    "role": "tool",
                    "tool_call_id": "c1",
                    "name": "bash",
                    "content": "PATH=/usr/bin api_key=sk-live-abc123def",
                },
            ]
        )

        nodes = _collect_action_nodes(session.messages)

        assert len(nodes) == 1
        assert "<redacted>" in nodes[0].output
        assert "sk-live-abc123def" not in nodes[0].output

    def test_redacts_bearer_token(self):
        """``Bearer xxx`` 应被 ``Bearer <redacted>`` 替换。"""
        session = _session(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [_tool_call("bash", '{"cmd": "cat"}', "c1")],
                },
                {
                    "role": "tool",
                    "tool_call_id": "c1",
                    "name": "bash",
                    "content": "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9",
                },
            ]
        )

        nodes = _collect_action_nodes(session.messages)

        assert len(nodes) == 1
        assert "Bearer <redacted>" in nodes[0].output
        assert "eyJhbGciOiJIUzI1NiJ9" not in nodes[0].output

    def test_output_truncated_to_max_chars(self):
        """超长 output 被截断到 OUTPUT_MAX_CHARS 并附 truncated 标记。"""
        session = _session(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [_tool_call("bash", '{"cmd": "yes"}', "c1")],
                },
                {
                    "role": "tool",
                    "tool_call_id": "c1",
                    "name": "bash",
                    "content": "x" * 5000,
                },
            ]
        )

        nodes = _collect_action_nodes(session.messages)

        from nanobot.memory.extractor import ActionNode

        assert len(nodes) == 1
        # 截断后实际内容 ≤ OUTPUT_MAX_CHARS；末尾追加 truncated 标记
        assert nodes[0].output.startswith("x" * ActionNode.OUTPUT_MAX_CHARS)
        assert "truncated" in nodes[0].output
        # 原始 5000 字符不应完整出现
        assert len(nodes[0].output) < 5000

    def test_input_redacts_password(self):
        """含 ``password=xxx`` 的入参应被 ``<redacted>`` 替换。"""
        session = _session(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        _tool_call("bash", '{"cmd": "login", "password": "p4ssw0rd!"}', "c1")
                    ],
                },
                {"role": "tool", "tool_call_id": "c1", "name": "bash", "content": "ok"},
            ]
        )

        nodes = _collect_action_nodes(session.messages)

        assert len(nodes) == 1
        assert "<redacted>" in nodes[0].input
        assert "p4ssw0rd!" not in nodes[0].input


# ---------------------------------------------------------------------------
# FIX-4 Sec-M-2: 数值与字符串字段值域校验
# ---------------------------------------------------------------------------


class TestCoerceMemoryItem:
    """FIX-4 Sec-M-2: importance/content/tags/subject/predicate 域校验。"""

    def test_importance_nan_falls_back_to_default(self):
        """``importance="nan"`` 回落默认值 0.7。"""
        item = _coerce_memory_item(
            {"content": "x", "type": "FACT", "importance": "nan"}
        )
        assert item is not None
        assert item.importance == 0.7

    def test_importance_inf_falls_back_to_default(self):
        """``importance=1e400``（Inf）回落默认值 0.7。"""
        item = _coerce_memory_item(
            {"content": "x", "type": "FACT", "importance": "1e400"}
        )
        assert item is not None
        assert item.importance == 0.7

    def test_importance_negative_falls_back_to_default(self):
        """``importance=-1`` 越界回落默认值 0.7（FIX-4 Sec-M-2：失败或越界 → 0.7）。"""
        item = _coerce_memory_item(
            {"content": "x", "type": "FACT", "importance": -1}
        )
        assert item is not None
        assert item.importance == 0.7

    def test_importance_above_one_falls_back_to_default(self):
        """``importance=2.5`` 越界回落默认值 0.7。"""
        item = _coerce_memory_item(
            {"content": "x", "type": "FACT", "importance": 2.5}
        )
        assert item is not None
        assert item.importance == 0.7

    def test_content_truncated_when_over_max_chars(self):
        """超长 content 截断 + truncated 标记。"""
        from nanobot.memory.extractor import CONTENT_MAX_CHARS

        long_content = "a" * (CONTENT_MAX_CHARS + 1000)
        item = _coerce_memory_item({"content": long_content, "type": "FACT"})
        assert item is not None
        assert len(item.content) <= CONTENT_MAX_CHARS + len("...[truncated]")
        assert item.content.endswith("...[truncated]")

    def test_tags_truncated_when_over_max_items(self):
        """超量 tags 截断到 TAGS_MAX_ITEMS。"""
        from nanobot.memory.extractor import TAGS_MAX_ITEMS

        many_tags = [f"tag-{i}" for i in range(TAGS_MAX_ITEMS + 100)]
        item = _coerce_memory_item(
            {"content": "x", "type": "FACT", "tags": many_tags}
        )
        assert item is not None
        assert len(item.tags) == TAGS_MAX_ITEMS
        assert item.tags[0] == "tag-0"
        assert item.tags[-1] == f"tag-{TAGS_MAX_ITEMS - 1}"

    def test_subject_truncated_when_over_max_chars(self):
        """超长 subject 截断到 SUBJECT_MAX_CHARS。"""
        from nanobot.memory.extractor import SUBJECT_MAX_CHARS

        long_subject = "s" * (SUBJECT_MAX_CHARS + 500)
        item = _coerce_memory_item(
            {"content": "x", "type": "FACT", "subject": long_subject}
        )
        assert item is not None
        assert len(item.subject) == SUBJECT_MAX_CHARS

    def test_importance_valid_value_passes_through(self):
        """合法值（0.5）原样保留。"""
        item = _coerce_memory_item(
            {"content": "x", "type": "FACT", "importance": 0.5}
        )
        assert item is not None
        assert item.importance == 0.5
