"""WU-A Task 1：``session_extraction_state`` 表的 CRUD 仓库函数测试。

覆盖:
- CRUD roundtrip: get 缺失返回 None,upsert 后 get 返回一致内容。
- 多次 upsert 推进 last_count / 更新 last_extracted_at。
- reset 真正删除行(reset 后 get 返回 None)。
- 缺失 key 的 get 返回 None(独立 case)。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nanobot.memory.database import MemoryDatabase


@pytest.fixture
def db(tmp_path: Path) -> MemoryDatabase:
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return database


class TestExtractionStateCRUD:
    def test_get_returns_none_when_missing(self, db: MemoryDatabase):
        from nanobot.memory.repository import get_extraction_state

        with db.connect() as conn:
            assert get_extraction_state(conn, "nope") is None

    def test_upsert_then_get_roundtrip(self, db: MemoryDatabase):
        from nanobot.memory.repository import (
            get_extraction_state,
            upsert_extraction_state,
        )

        with db.connect() as conn:
            upsert_extraction_state(
                conn,
                "s1",
                last_count=5,
                source="idle",
                extracted_at="2026-09-15T10:00:00+00:00",
            )
            row = get_extraction_state(conn, "s1")
        assert row is not None
        assert row.session_key == "s1"
        assert row.last_count == 5
        assert row.last_source == "idle"
        assert row.last_extracted_at == "2026-09-15T10:00:00+00:00"
        assert row.updated_at == "2026-09-15T10:00:00+00:00"

    def test_upsert_advances_cursor(self, db: MemoryDatabase):
        """第二次 upsert 必须把 last_count 推进,last_extracted_at 刷新。"""
        from nanobot.memory.repository import (
            get_extraction_state,
            upsert_extraction_state,
        )

        with db.connect() as conn:
            upsert_extraction_state(
                conn,
                "s1",
                last_count=3,
                source="idle",
                extracted_at="2026-09-15T10:00:00+00:00",
            )
            upsert_extraction_state(
                conn,
                "s1",
                last_count=10,
                source="idle",
                extracted_at="2026-09-15T10:05:00+00:00",
            )
            row = get_extraction_state(conn, "s1")
        assert row is not None
        assert row.last_count == 10
        assert row.last_extracted_at == "2026-09-15T10:05:00+00:00"

    def test_reset_removes_row(self, db: MemoryDatabase):
        from nanobot.memory.repository import (
            get_extraction_state,
            reset_extraction_state,
            upsert_extraction_state,
        )

        with db.connect() as conn:
            upsert_extraction_state(
                conn,
                "s2",
                last_count=7,
                source="idle",
                extracted_at="2026-09-15T10:00:00+00:00",
            )
            assert get_extraction_state(conn, "s2") is not None
            reset_extraction_state(conn, "s2")
            assert get_extraction_state(conn, "s2") is None

    def test_reset_missing_key_is_noop(self, db: MemoryDatabase):
        """reset 一个不存在的 key 不应抛异常,且后续 get 仍返回 None。"""
        from nanobot.memory.repository import (
            get_extraction_state,
            reset_extraction_state,
        )

        with db.connect() as conn:
            reset_extraction_state(conn, "never-existed")
            assert get_extraction_state(conn, "never-existed") is None

    def test_schema_table_exists(self, db: MemoryDatabase):
        """``session_extraction_state`` 表已建好,且 5 列齐全。"""
        with db.connect() as conn:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='session_extraction_state'"
            )
            assert cur.fetchone() is not None
            cols = [row[1] for row in conn.execute("PRAGMA table_info(session_extraction_state)").fetchall()]
            assert set(cols) == {
                "session_key",
                "last_count",
                "last_source",
                "last_extracted_at",
                "updated_at",
            }
