"""WU-B 引用评分闭环测试（plan §4.4 DoD）。

覆盖：
- 注入块 markdown 里能读到 ``memory_id``
- ``cited_memory_ids`` 跨 idle 送达 ``ProfileExtractor``（semantic prompt 尾部
  追加引用评分段）
- 伪 provider 返回 ``useful=true`` → 对应记忆 ``access_count`` 0→1
- 评分返回集合外的 id → 不写库（防 LLM 幻觉写错记忆）
- reranker 对 ``access_count=0`` 与 ``=1`` 的候选给出不同 ``composite_score``
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.extractor import MemoryExtractor
from nanobot.memory.models import Memory, MemoryPriority, MemoryType
from nanobot.memory.repository import (
    add_memory,
    bump_access_count,
    get_memory,
)
from nanobot.memory.retrieval.candidate import RetrievalCandidate
from nanobot.memory.retrieval.reranker import Reranker
from nanobot.session.manager import Session

# ---------------------------------------------------------------------------
# Fakes（鸭子类型，与 test_extractor_incremental.py 同款）
# ---------------------------------------------------------------------------

_SEMANTIC_MARKER = "语义记忆抽取"
_EPISODE_MARKER = "情节记忆抽取"


class _FakeResponse:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeGeneration:
    temperature = 0.2
    max_tokens = 512
    reasoning_effort = None


class _FakeProvider:
    """semantic 轨返回 memories + citation_scores；episode 轨返回简单 payload。"""

    def __init__(
        self,
        *,
        semantic: dict[str, Any],
        episode: dict[str, Any] | None = None,
    ) -> None:
        self.semantic = semantic
        self.episode = episode or {"summary": "s", "goal": "g", "outcome": "completed", "entities": [], "tools_used": []}
        self.calls: list[list[dict[str, Any]]] = []

    def prompts(self) -> list[str]:
        return [
            msg["content"]
            for call in self.calls
            for msg in call
            if msg.get("role") == "user"
        ]

    async def chat_with_retry(
        self, *, messages: list[dict[str, Any]], **_kwargs: Any
    ) -> _FakeResponse:
        self.calls.append(messages)
        body = messages[0].get("content") or ""
        if _SEMANTIC_MARKER in body:
            return _FakeResponse(json.dumps(self.semantic, ensure_ascii=False))
        if _EPISODE_MARKER in body:
            return _FakeResponse(json.dumps(self.episode, ensure_ascii=False))
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


def _add_memory(db: MemoryDatabase, *, content: str) -> str:
    memory_id = str(uuid.uuid4())
    memory = Memory(
        id=memory_id,
        content=content,
        type=MemoryType.FACT,
        priority=MemoryPriority.LONG_TERM,
        source="manual",
        importance_score=0.7,
        subject="",
        predicate="",
        workspace_id="default",
        created_at="2026-09-15T10:00:00+00:00",
        updated_at="2026-09-15T10:00:00+00:00",
    )
    with db.connect() as conn:
        add_memory(conn, memory)
    return memory_id


def _session(messages: list[dict[str, Any]] | None = None, *, key: str = "s1") -> Session:
    return Session(
        key=key,
        messages=messages
        or [
            {"role": "user", "content": "以后都用 uv 管理依赖"},
            {"role": "assistant", "content": "好的，已记住"},
        ],
    )


def _semantic_with_scores(
    mem_id_in_set: str,
    mem_id_outside: str,
    *,
    useful: bool = True,
) -> dict[str, Any]:
    """semantic 输出：1 条画像 + 引用评分（集合内 id + 集合外 id）。"""
    return {
        "memories": [
            {
                "content": "用户偏好用 uv 管理 Python 依赖",
                "subject": "用户",
                "predicate": "偏好",
                "type": "PREFERENCE",
                "importance": 0.6,
            }
        ],
        "citation_scores": [
            {"memory_id": mem_id_in_set, "useful": useful},
            {"memory_id": mem_id_outside, "useful": True},
        ],
    }


def _extractor(db: MemoryDatabase, provider: _FakeProvider) -> MemoryExtractor:
    return MemoryExtractor(db, _FakeRuntime(provider), user_id="u1", workspace_id="default")


def _access_count(db: MemoryDatabase, memory_id: str) -> int:
    with db.connect() as conn:
        memory = get_memory(conn, memory_id)
    assert memory is not None
    return int(memory.access_count)


# ---------------------------------------------------------------------------
# 闭环：id 集合内 useful=true → access_count 自增；集合外 id 不写库
# ---------------------------------------------------------------------------


class TestCitationLoop:
    async def test_useful_score_increments_access_count(self, db: MemoryDatabase):
        mem_id = _add_memory(db, content="用户住在杭州")
        provider = _FakeProvider(semantic=_semantic_with_scores(mem_id, "outside-1", useful=True))
        extractor = _extractor(db, provider)

        await extractor.run_idle_extraction(
            _session(), cited_memory_ids=[mem_id]
        )

        assert _access_count(db, mem_id) == 1

    async def test_useful_false_does_not_increment(self, db: MemoryDatabase):
        mem_id = _add_memory(db, content="用户住在杭州")
        provider = _FakeProvider(semantic=_semantic_with_scores(mem_id, "outside-1", useful=False))
        extractor = _extractor(db, provider)

        await extractor.run_idle_extraction(
            _session(), cited_memory_ids=[mem_id]
        )

        assert _access_count(db, mem_id) == 0

    async def test_score_id_outside_cited_set_is_ignored(self, db: MemoryDatabase):
        """LLM 返回的 id 不在本次注入集合内 → 丢弃（防幻觉写错记忆）。"""
        in_set = _add_memory(db, content="集合内记忆")
        outside = _add_memory(db, content="集合外记忆")
        provider = _FakeProvider(semantic=_semantic_with_scores(in_set, outside, useful=True))
        extractor = _extractor(db, provider)

        # 只注入 in_set 的 id，outside 不在集合内。
        await extractor.run_idle_extraction(
            _session(), cited_memory_ids=[in_set]
        )

        assert _access_count(db, in_set) == 1
        assert _access_count(db, outside) == 0

    async def test_no_cited_ids_is_noop(self, db: MemoryDatabase):
        """不传 cited_memory_ids → semantic prompt 不追加评分段，access_count 不动。"""
        mem_id = _add_memory(db, content="用户住在杭州")
        provider = _FakeProvider(semantic=_semantic_with_scores(mem_id, "x", useful=True))
        extractor = _extractor(db, provider)

        await extractor.run_idle_extraction(_session())

        joined = "\n".join(provider.prompts())
        assert "citation" not in joined and "useful" not in joined
        assert _access_count(db, mem_id) == 0

    async def test_cited_section_appends_to_semantic_prompt(self, db: MemoryDatabase):
        """cited_memory_ids 跨 idle 送达：semantic prompt 尾部含引用评分段。"""
        mem_id = _add_memory(db, content="用户住在杭州")
        provider = _FakeProvider(semantic=_semantic_with_scores(mem_id, "x", useful=True))
        extractor = _extractor(db, provider)

        await extractor.run_idle_extraction(
            _session(), cited_memory_ids=[mem_id]
        )

        semantic_prompt = provider.prompts()[0]
        assert _SEMANTIC_MARKER in semantic_prompt
        assert f"ID={mem_id}" in semantic_prompt
        assert "useful" in semantic_prompt


# ---------------------------------------------------------------------------
# reranker：access_count 差异 → composite_score 差异
# ---------------------------------------------------------------------------


class TestAccessCountAffectsScoring:
    def test_access_count_0_vs_1_gives_different_composite(self):
        """同一候选除 access_frequency 外全同 → access 0 与 1 得分不同。"""
        base = dict(
            memory_id="m", content="用户住在杭州", source_channel="semantic",
            relevance=0.5, recency_score=0.5, importance_score=0.5,
        )
        # access_count=0 → _access_freq=log1p(0)/5=0
        acc0 = RetrievalCandidate(**base, access_frequency_score=0.0)
        # access_count=1 → log1p(1)/5≈0.1386
        acc1 = RetrievalCandidate(**base, access_frequency_score=0.1386)
        out = Reranker().rerank([acc1, acc0], query="x", persona=None, focus_terms=[])
        assert out[0].memory_id == "m"
        # 两份去掉 focus boost（focus_terms 空）后仅差 0.2×(0.1386)≈0.0277
        assert out[0].composite_score > out[1].composite_score
        assert out[0].composite_score == pytest.approx(out[1].composite_score + 0.2 * 0.1386, abs=1e-3)


# ---------------------------------------------------------------------------
# bump_access_count（repository helper）
# ---------------------------------------------------------------------------


class TestBumpAccessCount:
    def test_bump_increments_and_is_idempotent_per_call(self, db: MemoryDatabase):
        mem_id = _add_memory(db, content="用户住在杭州")
        with db.connect() as conn:
            bump_access_count(conn, mem_id, delta=1)
        assert _access_count(db, mem_id) == 1
        with db.connect() as conn:
            bump_access_count(conn, mem_id, delta=1)
        assert _access_count(db, mem_id) == 2

    def test_bump_default_delta_is_one(self, db: MemoryDatabase):
        mem_id = _add_memory(db, content="用户住在杭州")
        with db.connect() as conn:
            bump_access_count(conn, mem_id)
        assert _access_count(db, mem_id) == 1

    def test_bump_missing_row_is_silent(self, db: MemoryDatabase):
        with db.connect() as conn:
            bump_access_count(conn, "does-not-exist")  # 不抛
