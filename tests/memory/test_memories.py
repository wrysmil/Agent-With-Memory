"""Tests for memories table CRUD operations."""

from pathlib import Path

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import Memory, MemoryPriority, MemoryType
from nanobot.memory.repository import add_memory, delete_memory, get_memory, list_memories


@pytest.fixture
def db(tmp_path: Path) -> MemoryDatabase:
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return database


def _now():
    return "2026-09-07T10:00:00"


def _sample(id="m1", **overrides):
    fields = dict(
        id=id, content="用户喜欢 Python",
        type=MemoryType.PREFERENCE, created_at=_now(), updated_at=_now(),
    )
    fields.update(overrides)
    return Memory(**fields)


class TestAddMemory:
    def test_inserts_and_returns_id(self, db):
        m = _sample()
        with db.connect() as conn:
            add_memory(conn, m)
            row = conn.execute("SELECT id FROM memories WHERE id=?", ("m1",)).fetchone()
        assert row['id'] == 'm1'

    def test_inserts_with_complex_type_field(self, db):
        m = _sample(type=MemoryType.SKILL)
        with db.connect() as conn:
            add_memory(conn, m)
            row = conn.execute("SELECT type FROM memories WHERE id=?", ("m1",)).fetchone()
        assert row['type'] == 'skill'

    def test_inserts_with_json_tags(self, db):
        m = _sample(tags=["编程", "Python"])
        with db.connect() as conn:
            add_memory(conn, m)
            row = conn.execute("SELECT tags FROM memories WHERE id=?", ("m1",)).fetchone()
        import json
        assert json.loads(row['tags']) == ["编程", "Python"]

    def test_rejects_duplicate_id(self, db):
        m = _sample()
        with db.connect() as conn:
            add_memory(conn, m)
            with pytest.raises(Exception):
                add_memory(conn, m)


class TestGetMemory:
    def test_returns_memory(self, db):
        with db.connect() as conn:
            add_memory(conn, _sample())
            m = get_memory(conn, "m1")
        assert m is not None
        assert m.id == "m1"
        assert m.type == MemoryType.PREFERENCE
        assert m.tags == []

    def test_returns_none_when_missing(self, db):
        with db.connect() as conn:
            assert get_memory(conn, "nope") is None


class TestListMemories:
    def test_lists_all_memories(self, db):
        with db.connect() as conn:
            add_memory(conn, _sample("m1"))
            add_memory(conn, _sample("m2"))
            add_memory(conn, _sample("m3"))
            rows = list_memories(conn)
        assert {r.id for r in rows} == {"m1", "m2", "m3"}

    def test_filters_by_type(self, db):
        with db.connect() as conn:
            add_memory(conn, _sample("m1", type=MemoryType.PREFERENCE))
            add_memory(conn, _sample("m2", type=MemoryType.SKILL))
            rows = list_memories(conn, type=MemoryType.SKILL)
        assert [r.id for r in rows] == ["m2"]

    def test_filters_by_workspace_id(self, db):
        with db.connect() as conn:
            add_memory(conn, _sample("m1", workspace_id="ws-a"))
            add_memory(conn, _sample("m2", workspace_id="ws-b"))
            rows = list_memories(conn, workspace_id="ws-a")
        assert [r.id for r in rows] == ["m1"]

    def test_orders_by_importance_desc(self, db):
        with db.connect() as conn:
            add_memory(conn, _sample("m1", importance_score=0.3))
            add_memory(conn, _sample("m2", importance_score=0.9))
            rows = list_memories(conn, order_by="importance")
        assert [r.id for r in rows] == ["m2", "m1"]


class TestDeleteMemory:
    def test_removes_memory(self, db):
        with db.connect() as conn:
            add_memory(conn, _sample())
            delete_memory(conn, "m1")
            assert get_memory(conn, "m1") is None
