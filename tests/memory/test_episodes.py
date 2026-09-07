"""Tests for episodes table CRUD operations."""

from pathlib import Path

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import Episode, EpisodeOutcome
from nanobot.memory.repository import add_episode, get_episode, list_episodes_by_session


@pytest.fixture
def db(tmp_path: Path) -> MemoryDatabase:
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return database


def _episode(id="e1", **kw):
    base = dict(
        id=id, session_id="s1", summary="修复测试失败",
        goal="让 CI 通过", outcome=EpisodeOutcome.COMPLETED,
        started_at="2026-09-07T10:00:00", ended_at="2026-09-07T10:30:00",
    )
    base.update(kw)
    return Episode(**base)


class TestAddEpisode:
    def test_inserts(self, db):
        with db.connect() as conn:
            add_episode(conn, _episode())
            row = conn.execute("SELECT id FROM episodes WHERE id=?", ("e1",)).fetchone()
        assert row['id'] == 'e1'

    def test_inserts_with_json_fields(self, db):
        ep = _episode(
            action_nodes=[{"tool": "read_file", "input": "x.py"}],
            tools_used=["read_file"],
            entities=["x.py"],
        )
        with db.connect() as conn:
            add_episode(conn, ep)
            row = conn.execute(
                "SELECT action_nodes, tools_used, entities FROM episodes WHERE id=?",
                ("e1",),
            ).fetchone()
        import json
        assert json.loads(row['action_nodes'])[0]['tool'] == 'read_file'
        assert json.loads(row['tools_used']) == ['read_file']


class TestGetEpisode:
    def test_returns_episode(self, db):
        with db.connect() as conn:
            add_episode(conn, _episode())
            ep = get_episode(conn, "e1")
        assert ep is not None
        assert ep.session_id == "s1"
        assert ep.outcome == EpisodeOutcome.COMPLETED
        assert ep.goal == "让 CI 通过"

    def test_returns_none_when_missing(self, db):
        with db.connect() as conn:
            assert get_episode(conn, "nope") is None


class TestListEpisodesBySession:
    def test_lists_only_target_session(self, db):
        with db.connect() as conn:
            add_episode(conn, _episode("e1", session_id="s1"))
            add_episode(conn, _episode("e2", session_id="s2"))
            rows = list_episodes_by_session(conn, "s1")
        assert [r.id for r in rows] == ["e1"]

    def test_orders_by_started_at_desc(self, db):
        with db.connect() as conn:
            add_episode(conn, _episode("e1", started_at="2026-09-07T08:00:00", ended_at="2026-09-07T08:30:00"))
            add_episode(conn, _episode("e2", started_at="2026-09-07T10:00:00", ended_at="2026-09-07T10:30:00"))
            rows = list_episodes_by_session(conn, "s1")
        assert [r.id for r in rows] == ["e2", "e1"]
