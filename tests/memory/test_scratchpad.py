"""Tests for scratchpad table UPSERT operations."""

from pathlib import Path

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import ScratchpadEntry
from nanobot.memory.repository import get_scratchpad, upsert_scratchpad


@pytest.fixture
def db(tmp_path: Path) -> MemoryDatabase:
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return database


class TestScratchpad:
    def test_get_returns_none_when_missing(self, db):
        with db.connect() as conn:
            assert get_scratchpad(conn, "u1", "w1") is None

    def test_upsert_inserts_new_entry(self, db):
        entry = ScratchpadEntry(
            user_id="u1", workspace_id="w1", updated_at="2026-09-07T10:00:00",
            content="Hello", active_projects=["p1"], current_focus="p1",
            open_questions=["q?"], next_steps=["step1"],
        )
        with db.connect() as conn:
            upsert_scratchpad(conn, entry)
            row = get_scratchpad(conn, "u1", "w1")
        assert row is not None
        assert row.content == "Hello"
        assert row.active_projects == ["p1"]
        assert row.current_focus == "p1"
        assert row.open_questions == ["q?"]
        assert row.next_steps == ["step1"]

    def test_upsert_updates_existing_entry(self, db):
        first = ScratchpadEntry(
            user_id="u1", workspace_id="w1", updated_at="2026-09-07T10:00:00",
            content="v1",
        )
        second = ScratchpadEntry(
            user_id="u1", workspace_id="w1", updated_at="2026-09-07T11:00:00",
            content="v2", current_focus="new focus",
        )
        with db.connect() as conn:
            upsert_scratchpad(conn, first)
            upsert_scratchpad(conn, second)
            row = get_scratchpad(conn, "u1", "w1")
            count = conn.execute("SELECT COUNT(*) FROM scratchpad").fetchone()[0]
        assert row.content == "v2"
        assert row.current_focus == "new focus"
        # active_projects 被 second 的空值覆盖
        assert row.active_projects == []
        assert count == 1

    def test_workspace_isolation(self, db):
        with db.connect() as conn:
            upsert_scratchpad(conn, ScratchpadEntry(
                user_id="u1", workspace_id="w1", updated_at="2026-09-07",
                content="ws1 content",
            ))
            upsert_scratchpad(conn, ScratchpadEntry(
                user_id="u1", workspace_id="w2", updated_at="2026-09-07",
                content="ws2 content",
            ))
            r1 = get_scratchpad(conn, "u1", "w1")
            r2 = get_scratchpad(conn, "u1", "w2")
        assert r1.content == "ws1 content"
        assert r2.content == "ws2 content"
