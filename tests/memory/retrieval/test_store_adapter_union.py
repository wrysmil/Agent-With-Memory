"""Adapter 并集：向量 ∪ FTS5 按 RRF 融合（spec §3.4 v2）。"""
from __future__ import annotations

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import Memory, MemoryType
from nanobot.memory.repository import add_memory
from nanobot.memory.retrieval.store_adapter import MemoryStoreAdapter


class _FakeVector:
    """只记 id → score；不涉及 chromadb。"""

    def __init__(self, hits: list[tuple[str, float]]):
        self._hits = hits
        self.queries: list[str] = []

    def search(self, query: str, *, limit: int = 15):
        self.queries.append(query)
        return self._hits[:limit]


def _memory(mid: str, content: str) -> Memory:
    return Memory(
        id=mid, content=content, type=MemoryType.FACT,
        created_at="2026-09-16T00:00:00+00:00",
        updated_at="2026-09-16T00:00:00+00:00",
    )


def _db(tmp_path, items):
    db = MemoryDatabase(tmp_path)
    db.ensure_schema()
    with db.connect() as conn:
        for mid, content in items:
            add_memory(conn, _memory(mid, content))
    return db


def test_without_vector_store_behavior_is_unchanged(tmp_path):
    """D5/R2：不传 vector_store → 与修复批次行为完全一致。"""
    db = _db(tmp_path, [("m1", "user loves creating art")])
    adapter = MemoryStoreAdapter(db)
    scored = adapter.search_semantic_scored("creating", limit=5)
    assert [m.id for m, _s in scored] == ["m1"]


def test_vector_only_hit_is_returned(tmp_path):
    """同义召回：FTS5 零命中，向量命中（V3/V5 的代理判据）。

    RRF 融合：单路第1名的归一化分为 0.5（两路皆第1的理论上界 RRF_MAX≈0.033，
    单路第1的 RRF≈0.016，归一化后 = 0.016/0.033 ≈ 0.5）。
    """
    db = _db(tmp_path, [("m1", "心情不好")])
    adapter = MemoryStoreAdapter(db, vector_store=_FakeVector([("m1", 0.88)]))
    scored = adapter.search_semantic_scored("情绪低落", limit=5)
    assert [m.id for m, _s in scored] == ["m1"]
    assert scored[0][1] == 0.5  # RRF 单路第1名归一化分


def test_union_takes_max_score_per_id(tmp_path):
    """同一 id 在两侧都有 → 取较高分，且只出现一次（FTS5 占优方向）。

    ⚠️ 计划原文断言 ``scored[0][1] == 0.99``，**该断言不成立**：Task 9 的页内
    min-max 归一化让 FTS5 **首名恒为 1.0**（单命中时 ``span <= 0`` → 1.0），
    因此 ``max(1.0, 0.99)`` = 1.0。实现按 spec §4.6 取 max 是对的，是断言写错了
    前提。向量占优方向见 ``test_vector_lifts_low_ranked_fts5_hit``。
    """
    db = _db(tmp_path, [("m1", "creating art")])
    adapter = MemoryStoreAdapter(db, vector_store=_FakeVector([("m1", 0.99)]))
    scored = adapter.search_semantic_scored("creating", limit=5)
    assert len(scored) == 1
    assert scored[0][1] == 1.0  # FTS5 首名归一化 1.0 > 向量 0.99


def test_vector_lifts_low_ranked_fts5_hit(tmp_path):
    """向量占优方向：向量 + FTS5 双路共识时排名抬升。

    RRF 融合：m2 在两路都是 rank 1，m1 仅 FTS5 rank 1。
    双路共识的 m2 归一化分 ≈ 0.992（接近 1.0），单路的 m1 = 0.5。
    旧 max() 融合因为向量分 0.6 < FTS5 首名 1.0 而让 m1 排第一。
    """
    db = _db(
        tmp_path,
        [("m1", "creating art"), ("m2", "creating stuff and things for a long while")],
    )
    adapter = MemoryStoreAdapter(db, vector_store=_FakeVector([("m2", 0.6)]))
    scored = adapter.search_semantic_scored("creating", limit=5)
    # m2 双路共识排第一，m1 单路排第二
    assert [(m.id, s) for m, s in scored] == [
        ("m2", pytest.approx(0.992, rel=1e-2)),
        ("m1", pytest.approx(0.500, rel=1e-2)),
    ]


def test_vector_stale_id_is_dropped(tmp_path):
    """Chroma 里多出来的僵尸 id 不得泄漏给 reranker（spec §4.6）。"""
    db = _db(tmp_path, [("m1", "内容")])
    adapter = MemoryStoreAdapter(db, vector_store=_FakeVector([("ghost", 0.99)]))
    assert adapter.search_semantic_scored("查询", limit=5) == []


def test_vector_store_exception_degrades_to_fts5(tmp_path):
    """D4：向量抛异常 → 静默降级为纯 FTS5。

    这里**刻意**让假 store 的 ``search`` 直接抛（而不是返回空），因为
    ``VectorStore.search`` 自己已有 try/except；Adapter 层不应依赖那股纪律。
    """

    class _Boom:
        def search(self, query, *, limit=15):
            raise RuntimeError("chroma exploded")

    db = _db(tmp_path, [("m1", "creating art")])
    adapter = MemoryStoreAdapter(db, vector_store=_Boom())
    assert [m.id for m, _s in adapter.search_semantic_scored("creating", limit=5)] == ["m1"]


def test_superseded_vector_only_hit_is_dropped(tmp_path):
    """向量独有 id 回查后按活性过滤（修正 3）。

    插入顺序修正：``memories.superseded_by`` 带
    ``FOREIGN KEY ... REFERENCES memories(id)``（``database.py:57``）且
    ``PRAGMA foreign_keys = ON``（``:199``），计划原文先插 m1（其
    ``superseded_by='m2'``）后插 m2 → ``IntegrityError``。改为先插被引用行。
    """
    db = MemoryDatabase(tmp_path)
    db.ensure_schema()
    with db.connect() as conn:
        add_memory(conn, _memory("m2", "新内容"))
        m = _memory("m1", "旧内容")
        m.superseded_by = "m2"
        add_memory(conn, m)
    adapter = MemoryStoreAdapter(db, vector_store=_FakeVector([("m1", 0.99)]))
    assert adapter.search_semantic_scored("查询", limit=5) == []


def test_expired_vector_only_hit_is_dropped(tmp_path):
    """过期行同样不得经向量泄漏（``_is_live`` 的 expires_at 分支）。"""
    db = _db(tmp_path, [("m1", "一次性内容")])
    with db.connect() as conn:
        conn.execute(
            "UPDATE memories SET expires_at = ? WHERE id = 'm1'",
            ("2020-01-01T00:00:00+00:00",),
        )
    adapter = MemoryStoreAdapter(db, vector_store=_FakeVector([("m1", 0.99)]))
    assert adapter.search_semantic_scored("查询", limit=5) == []


def test_results_are_sorted_by_score_desc(tmp_path):
    db = _db(tmp_path, [("m1", "alpha"), ("m2", "beta")])
    adapter = MemoryStoreAdapter(db, vector_store=_FakeVector([("m2", 0.7)]))
    scored = adapter.search_semantic_scored("查询", limit=5)
    assert [s for _m, s in scored] == sorted((s for _m, s in scored), reverse=True)


def test_limit_is_respected(tmp_path):
    db = _db(tmp_path, [("m1", "a"), ("m2", "b"), ("m3", "c")])
    adapter = MemoryStoreAdapter(db, vector_store=_FakeVector([("m1", .9), ("m2", .8), ("m3", .7)]))
    assert len(adapter.search_semantic_scored("查询", limit=2)) == 2
