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
