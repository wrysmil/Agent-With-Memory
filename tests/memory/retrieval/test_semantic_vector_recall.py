"""端到端：同义召回（V3/V5）与字面召回不退化（V6）。

⚠️ 修正 2：``RetrievalCandidate.source_channel`` 恒为 "semantic"（向量与 FTS5
不可区分），故这里**不**断言 channel 字段，改用更强的判据：
- V3/V5：构造 FTS5 必然零命中的同义查询 → 候选只能来自向量；
- V6：专有名词查询仍命中（FTS5 并集兜底）。
"""
from __future__ import annotations

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import Memory, MemoryType
from nanobot.memory.repository import add_memory
from nanobot.memory.retrieval.engine import RetrievalEngine
from nanobot.memory.retrieval.store_adapter import MemoryStoreAdapter


class _KeywordVector:
    """无 chromadb 的确定性替身：按关键词表命中。

    真实 bge 模型在 CI 上不可得，但「FTS5 零命中 + 向量命中 → 候选非空」
    这条因果链与具体 embedding 无关，用替身即可锁住。
    """

    def __init__(self, table: dict[str, list[tuple[str, float]]]):
        self._table = table

    def search(self, query: str, *, limit: int = 15):
        for key, hits in self._table.items():
            if key in query:
                return hits[:limit]
        return []


def _db(tmp_path, items):
    db = MemoryDatabase(tmp_path)
    db.ensure_schema()
    with db.connect() as conn:
        for mid, content in items:
            add_memory(
                conn,
                Memory(
                    id=mid, content=content, type=MemoryType.FACT,
                    importance_score=0.9, access_count=3,
                    created_at="2026-09-16T00:00:00+00:00",
                    updated_at="2026-09-16T00:00:00+00:00",
                ),
            )
    return db


def test_synonym_recall_via_vector_only(tmp_path):
    """V3/V5：FTS5 搜不到「情绪低落」，向量把它接到「心情不好」。"""
    db = _db(tmp_path, [("m1", "用户最近心情不好，需要多鼓励")])
    vector = _KeywordVector({"情绪低落": [("m1", 0.86)]})
    adapter = MemoryStoreAdapter(db, vector_store=vector)

    # 前置断言：纯 FTS5 确实搜不到（否则本用例证明不了向量起了作用）
    plain = MemoryStoreAdapter(db)
    assert plain.search_semantic_scored("情绪低落", limit=5) == []

    scored = adapter.search_semantic_scored("情绪低落", limit=5)
    assert [m.id for m, _s in scored] == ["m1"]


def test_literal_recall_not_regressed(tmp_path):
    """V6：专有名词仍能命中（FTS5 并集兜底）。"""
    db = _db(tmp_path, [("m1", "ERR_CONN_REFUSED happens on port 8080")])
    adapter = MemoryStoreAdapter(db, vector_store=_KeywordVector({}))
    scored = adapter.search_semantic_scored("ERR_CONN_REFUSED", limit=5)
    assert "m1" in [m.id for m, _s in scored]


@pytest.mark.asyncio
async def test_retrieve_with_ids_returns_nonempty_block(tmp_path):
    """V1：Gate 0 已解除 —— 含记忆的库必须产出非空注入块。"""
    db = _db(tmp_path, [("m1", "user loves creating art and writing stories")])
    vector = _KeywordVector({"art": [("m1", 0.9)]})
    engine = RetrievalEngine(store=MemoryStoreAdapter(db, vector_store=vector))
    block, ids = await engine.retrieve_with_ids(
        query="tell me about art", recent_messages=[]
    )
    assert block != ""
    assert "m1" in ids
