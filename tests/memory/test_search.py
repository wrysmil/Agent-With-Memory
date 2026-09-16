"""Tests for FTS5 full-text search."""

from pathlib import Path

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import Memory, MemoryType
from nanobot.memory.repository import add_memory, search_memories


@pytest.fixture
def db(tmp_path: Path) -> MemoryDatabase:
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return database


def _now():
    return "2026-09-07T10:00:00"


def _seed(db):
    samples = [
        ("m1", "用户喜欢 Python 编程", "用户", "喜欢", 0.9),
        ("m2", "用户习惯用 pytest 跑测试", "用户", "习惯", 0.7),
        ("m3", "项目使用 PostgreSQL 数据库", "项目", "使用", 0.5),
        ("m4", "Rust 是系统编程语言", "Rust", "是", 0.4),
    ]
    with db.connect() as conn:
        for id_, content, subject, predicate, imp in samples:
            tags = ["编程"] if "编程" in content else (
                ["测试"] if "测试" in content else ["数据库"]
            )
            m = Memory(
                id=id_, content=content, subject=subject, predicate=predicate,
                tags=tags, created_at=_now(), updated_at=_now(),
                importance_score=imp,
            )
            add_memory(conn, m)


class TestSearchMemories:
    def test_search_by_keyword(self, db):
        _seed(db)
        with db.connect() as conn:
            results = search_memories(conn, "Python")
        ids = [r.id for r in results]
        assert "m1" in ids

    def test_search_finds_chinese(self, db):
        _seed(db)
        with db.connect() as conn:
            results = search_memories(conn, "数据库")
        assert any(r.id == "m3" for r in results)

    def test_search_with_type_filter(self, db):
        _seed(db)
        with db.connect() as conn:
            results = search_memories(conn, "编程", type=MemoryType.FACT)
        for r in results:
            assert r.type == MemoryType.FACT

    def test_search_orders_by_relevance(self, db):
        _seed(db)
        with db.connect() as conn:
            results = search_memories(conn, "用户")
        ids = {r.id for r in results}
        assert "m1" in ids
        assert "m2" in ids

    def test_search_with_limit(self, db):
        _seed(db)
        with db.connect() as conn:
            results = search_memories(conn, "用户", limit=1)
        assert len(results) == 1

    def test_search_no_match_returns_empty(self, db):
        _seed(db)
        with db.connect() as conn:
            results = search_memories(conn, "GoLanguageNoMatch")
        assert results == []


class TestChineseSubstringFallback:
    """RCA 2026-09-15 根因 5：``memories_fts`` 的 ``unicode61`` 把连续 CJK 切成
    一个整 token，因此**中文子串**查询在 FTS5 上恒 0 命中，必须由
    ``repository._search_memories_like`` 的 ``LIKE %query%`` 回退兜底。

    本类只测 ``search_memories`` 这一层（adapter 层的同一策略由
    ``tests/memory/retrieval/test_store_adapter.py`` 覆盖）。

    ⚠️ 探针设计：``memories_fts`` 同时索引 ``content / subject / predicate / tags``，
    所以「必须是 FTS token 的真子串」这一步要连 subject/predicate/tags 一起看。
    例如 seed 里 m1 的 ``predicate`` 就是「喜欢」——用「喜欢」当探针会**命中 FTS**，
    从而绕过回退路径，测不出根因 5。故探针选「欢」「用」：两者都不是任何 token
    （token 形如「用户喜欢」「项目使用」），只可能是子串。
    """

    def test_substring_of_cjk_token_falls_back_to_like(self, db):
        """「欢」是 token「用户喜欢」的子串 → FTS5 不命中，LIKE 回退找回 m1。"""
        _seed(db)
        with db.connect() as conn:
            results = search_memories(conn, "欢")
        assert [r.id for r in results] == ["m1"]

    def test_like_fallback_respects_type_filter(self, db):
        """回退路径必须继续遵守 ``type`` 过滤，不能因回退而放宽条件。"""
        _seed(db)
        with db.connect() as conn:
            add_memory(
                conn,
                Memory(
                    id="m5",
                    content="我也喜欢 Rust",
                    type=MemoryType.PREFERENCE,
                    created_at=_now(),
                    updated_at=_now(),
                    importance_score=0.5,
                ),
            )
        with db.connect() as conn:
            unfiltered = {r.id for r in search_memories(conn, "欢")}
            only_pref = {r.id for r in search_memories(
                conn, "欢", type=MemoryType.PREFERENCE
            )}
            only_fact = {r.id for r in search_memories(
                conn, "欢", type=MemoryType.FACT
            )}
        assert unfiltered == {"m1", "m5"}
        assert only_pref == {"m5"}
        assert only_fact == {"m1"}

    def test_like_fallback_respects_workspace_filter(self, db):
        """回退路径同样不得绕过 ``workspace_id`` 过滤（防跨工作区串记忆）。"""
        _seed(db)
        with db.connect() as conn:
            results = search_memories(conn, "欢", workspace_id="other-workspace")
        assert results == []

    def test_like_fallback_respects_limit(self, db):
        """回退路径的 ``limit`` 生效（不因回退而变成全量返回）。

        「用」不是任何 FTS token（token 是「用户喜欢」/「项目使用」这类整串），
        故必然走 LIKE 回退；m1/m2/m3 各含一个「用」。
        """
        _seed(db)
        with db.connect() as conn:
            all_hits = search_memories(conn, "用")
            limited = search_memories(conn, "用", limit=1)
        assert len(all_hits) == 3
        assert len(limited) == 1
        # 回退路径按 importance_score DESC，最高分那条在前
        assert limited[0].id == "m1"

    @pytest.mark.parametrize("query", ["_", "%"])
    def test_like_fallback_does_not_treat_query_as_like_pattern(self, db, query):
        """query 里的 LIKE 元字符必须被转义，否则「子串回退」会退化成「全表返回」。

        实测（未转义时）：``search_memories(conn, "_")`` 的模式 ``%_%`` 等价于
        「任意非空串」→ 返回**全部** m1-m4；``"%"`` 同理（且 FTS5 侧报
        ``syntax error near "%"``）。seed 数据里不含下划线/百分号，故转义后必须是空。
        """
        _seed(db)
        with db.connect() as conn:
            results = search_memories(conn, query)
        assert results == []
