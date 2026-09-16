"""FTS5 真 bm25 分数归一化（修正 1）。"""
from __future__ import annotations

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import Memory, MemoryType
from nanobot.memory.repository import add_memory, search_semantic_scored

_SEED = [
    ("m1", "user loves creating art and writing stories"),
    ("m2", "creating art is a creative practice"),
    ("m3", "art creation and creative writing"),
    ("m4", "unrelated note about database indexing"),
]


def _seed(tmp_path) -> MemoryDatabase:
    db = MemoryDatabase(tmp_path)
    db.ensure_schema()
    with db.connect() as conn:
        for mid, content in _SEED:
            add_memory(
                conn,
                Memory(
                    id=mid, content=content, type=MemoryType.FACT,
                    created_at="2026-09-16T00:00:00+00:00",
                    updated_at="2026-09-16T00:00:00+00:00",
                ),
            )
    return db


def test_scores_are_within_unit_interval(tmp_path):
    db = _seed(tmp_path)
    with db.connect() as conn:
        scored = search_semantic_scored(conn, "creating art", limit=10)
    assert scored
    assert all(0.0 <= s <= 1.0 for _m, s in scored)


def test_scores_are_monotone_non_increasing(tmp_path):
    db = _seed(tmp_path)
    with db.connect() as conn:
        scores = [s for _m, s in search_semantic_scored(conn, "creating art", limit=10)]
    assert scores == sorted(scores, reverse=True)


def test_scores_are_not_flat_rank_ladder(tmp_path):
    """核心回归：修复前首名恒 1.0、次名恒 0.5，形成阶梯。"""
    db = _seed(tmp_path)
    with db.connect() as conn:
        scored = search_semantic_scored(conn, "creating art", limit=10)
    if len(scored) > 1:
        assert scored[0][1] != scored[-1][1]


def test_top_hit_is_highest_scoring(tmp_path):
    db = _seed(tmp_path)
    with db.connect() as conn:
        scored = search_semantic_scored(conn, "creating art", limit=10)
    assert scored[0][1] == max(s for _m, s in scored)


def test_chinese_substring_still_works(tmp_path):
    """中文子串回退路径（无 bm25 可用）必须仍然可用。"""
    db = MemoryDatabase(tmp_path)
    db.ensure_schema()
    with db.connect() as conn:
        add_memory(
            conn,
            Memory(
                id="cn1", content="用户热爱创作，希望AI主动提供创作灵感",
                type=MemoryType.FACT,
                created_at="2026-09-16T00:00:00+00:00",
                updated_at="2026-09-16T00:00:00+00:00",
            ),
        )
        scored = search_semantic_scored(conn, "创作", limit=10)
    assert [m.id for m, _s in scored] == ["cn1"]
    assert 0.0 <= scored[0][1] <= 1.0
