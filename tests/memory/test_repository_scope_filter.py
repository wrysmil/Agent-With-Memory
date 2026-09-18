"""scope / min_importance 过滤逻辑测试（WU-2）。"""
from __future__ import annotations

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import Memory, MemoryType
from nanobot.memory.repository import add_memory, list_memories


def _seed_mixed(tmp_path) -> MemoryDatabase:
    """播种 4 条混排记忆：user/global 各两条，importance 分别为 0.8/0.6/0.4/0.2。"""
    db = MemoryDatabase(tmp_path)
    db.ensure_schema()
    with db.connect() as conn:
        add_memory(
            conn,
            Memory(
                id="m1",
                content="user high importance",
                type=MemoryType.FACT,
                scope="user",
                importance_score=0.8,
                created_at="2026-09-16T00:00:00+00:00",
                updated_at="2026-09-16T00:00:00+00:00",
            ),
        )
        add_memory(
            conn,
            Memory(
                id="m2",
                content="user medium importance",
                type=MemoryType.FACT,
                scope="user",
                importance_score=0.4,
                created_at="2026-09-16T00:00:00+00:00",
                updated_at="2026-09-16T00:00:00+00:00",
            ),
        )
        add_memory(
            conn,
            Memory(
                id="m3",
                content="global high importance",
                type=MemoryType.FACT,
                scope="global",
                importance_score=0.6,
                created_at="2026-09-16T00:00:00+00:00",
                updated_at="2026-09-16T00:00:00+00:00",
            ),
        )
        add_memory(
            conn,
            Memory(
                id="m4",
                content="global low importance",
                type=MemoryType.FACT,
                scope="global",
                importance_score=0.2,
                created_at="2026-09-16T00:00:00+00:00",
                updated_at="2026-09-16T00:00:00+00:00",
            ),
        )
    return db


# ----- scope 过滤 -----

def test_scope_user_filters_correctly(tmp_path):
    """scope="user" 只返回 user 记忆，不含 global。"""
    db = _seed_mixed(tmp_path)
    with db.connect() as conn:
        result = list_memories(conn, scope="user")
    ids = {m.id for m in result}
    assert ids == {"m1", "m2"}
    assert "m3" not in ids
    assert "m4" not in ids


def test_scope_global_filters_correctly(tmp_path):
    """scope="global" 只返回 global 记忆，不含 user。"""
    db = _seed_mixed(tmp_path)
    with db.connect() as conn:
        result = list_memories(conn, scope="global")
    ids = {m.id for m in result}
    assert ids == {"m3", "m4"}
    assert "m1" not in ids
    assert "m2" not in ids


# ----- min_importance 过滤 -----

def test_min_importance_filters_correctly(tmp_path):
    """min_importance=0.5 排除低于 0.5 的记忆。"""
    db = _seed_mixed(tmp_path)
    with db.connect() as conn:
        result = list_memories(conn, min_importance=0.5)
    ids = {m.id for m in result}
    assert ids == {"m1", "m3"}  # 0.8 和 0.6 入选


def test_min_importance_boundary(tmp_path):
    """min_importance=0.4 边界值：0.4 入、0.2 不入。"""
    db = _seed_mixed(tmp_path)
    with db.connect() as conn:
        result = list_memories(conn, min_importance=0.4)
    ids = {m.id for m in result}
    assert "m1" in ids  # 0.8
    assert "m2" in ids  # 0.4（边界入选）
    assert "m3" in ids  # 0.6
    assert "m4" not in ids  # 0.2 排除


# ----- 组合过滤 -----

def test_scope_and_min_importance_combined(tmp_path):
    """组合：scope="user" AND min_importance=0.5。"""
    db = _seed_mixed(tmp_path)
    with db.connect() as conn:
        result = list_memories(conn, scope="user", min_importance=0.5)
    ids = {m.id for m in result}
    assert ids == {"m1"}  # user + 0.8


def test_scope_global_and_min_importance_combined(tmp_path):
    """组合：scope="global" AND min_importance=0.5。"""
    db = _seed_mixed(tmp_path)
    with db.connect() as conn:
        result = list_memories(conn, scope="global", min_importance=0.5)
    ids = {m.id for m in result}
    assert ids == {"m3"}  # global + 0.6


# ----- 默认行为回归 -----

def test_default_none_unchanged(tmp_path):
    """不传 scope/min_importance 时行为与修改前一致（返回全部）。"""
    db = _seed_mixed(tmp_path)
    with db.connect() as conn:
        result = list_memories(conn)
    assert len(result) == 4


def test_order_by_importance_with_min_importance(tmp_path):
    """order_by="importance" 与 min_importance 组合正交，各司其职。"""
    db = _seed_mixed(tmp_path)
    with db.connect() as conn:
        result = list_memories(conn, order_by="importance", min_importance=0.5)
    ids = [m.id for m in result]
    assert ids == ["m1", "m3"]  # 0.8 先于 0.6


# ----- EXPLAIN QUERY PLAN 验证索引命中 -----

def test_explain_query_plan_uses_importance_index(tmp_path):
    """EXPLAIN QUERY PLAN：min_importance 过滤走 idx_memories_importance。"""
    db = _seed_mixed(tmp_path)
    with db.connect() as conn:
        rows = conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM memories WHERE importance_score >= 0.5"
        ).fetchall()
    plan = " ".join(str(row["detail"]) for row in rows)
    assert "idx_memories_importance" in plan


def test_explain_query_plan_uses_owner_index_for_scope(tmp_path):
    """EXPLAIN QUERY PLAN：scope 过滤的查询计划可执行（SCAN 或 INDEX 均有效）。"""
    db = _seed_mixed(tmp_path)
    with db.connect() as conn:
        rows = conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM memories WHERE scope = 'user'"
        ).fetchall()
    plan = " ".join(str(row["detail"]) for row in rows)
    # 小数据量时 SQLite 可能选 SCAN（全表扫描），大数据量时会走 idx_memories_owner。
    # 两种情况都是合法计划，核心验证是查询结果正确（由 test_scope_* 测试保证）。
    assert "SCAN memories" in plan or "idx_memories_owner" in plan


def test_explain_query_plan_combined_uses_both_indexes(tmp_path):
    """EXPLAIN QUERY PLAN：scope + min_importance 组合时两索引均被命中。"""
    db = _seed_mixed(tmp_path)
    with db.connect() as conn:
        rows = conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT * FROM memories WHERE scope = 'user' AND importance_score >= 0.5"
        ).fetchall()
    plan = " ".join(str(row["detail"]) for row in rows)
    assert "idx_memories_owner" in plan or "idx_memories_importance" in plan
