"""Tests for MemoryDatabase connection management."""

from pathlib import Path

import pytest

from nanobot.memory.database import MemoryDatabase


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


class TestDatabasePath:
    def test_default_db_path_is_under_workspace_memory(self, workspace: Path):
        db = MemoryDatabase(workspace)
        assert db.db_path == workspace / "memory" / "state.db"

    def test_memory_directory_is_created(self, workspace: Path):
        MemoryDatabase(workspace)
        assert (workspace / "memory").is_dir()

    def test_db_file_is_created_after_connect(self, workspace: Path):
        db = MemoryDatabase(workspace)
        with db.connect() as conn:
            pass
        assert db.db_path.is_file()

    def test_custom_db_path_overrides_default(self, workspace: Path):
        custom = workspace / "custom.db"
        db = MemoryDatabase(workspace, db_path=custom)
        assert db.db_path == custom


class TestDatabaseConnection:
    def test_connect_returns_sqlite_connection(self, workspace: Path):
        import sqlite3
        db = MemoryDatabase(workspace)
        with db.connect() as conn:
            assert isinstance(conn, sqlite3.Connection)

    def test_connect_enables_foreign_keys(self, workspace: Path):
        db = MemoryDatabase(workspace)
        with db.connect() as conn:
            cur = conn.execute("PRAGMA foreign_keys")
            assert cur.fetchone()[0] == 1

    def test_concurrent_connects_share_lock(self, workspace: Path):
        import threading
        db = MemoryDatabase(workspace)
        errors: list[Exception] = []
        def worker():
            try:
                with db.connect() as conn:
                    conn.execute("SELECT 1").fetchone()
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


class TestSchema:
    def test_init_schema_creates_three_core_tables(self, workspace: Path):
        db = MemoryDatabase(workspace)
        db.init_schema()
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('memories', 'episodes', 'scratchpad') ORDER BY name"
            ).fetchall()
        assert [r[0] for r in rows] == ['episodes', 'memories', 'scratchpad']

    def test_init_schema_creates_indexes(self, workspace: Path):
        db = MemoryDatabase(workspace)
        db.init_schema()
        with db.connect() as conn:
            idx_rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name IN ('memories', 'episodes', 'scratchpad')"
            ).fetchall()
            idx_names = {r[0] for r in idx_rows}
        assert 'idx_memories_owner' in idx_names
        assert 'idx_episodes_session' in idx_names

    def test_init_schema_records_version_1(self, workspace: Path):
        db = MemoryDatabase(workspace)
        db.init_schema()
        with db.connect() as conn:
            ver = conn.execute(
                "SELECT value FROM _schema_meta WHERE key='version'"
            ).fetchone()[0]
        assert ver == '1'

    def test_init_schema_is_idempotent(self, workspace: Path):
        db = MemoryDatabase(workspace)
        db.init_schema()
        db.init_schema()  # 第二次不能报错
        db.init_schema()  # 第三次也不能报错
        with db.connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='memories'"
            ).fetchone()[0]
        assert count == 1

    def test_init_schema_creates_on_connect_via_ensure_schema(self, workspace: Path):
        db = MemoryDatabase(workspace)
        db.ensure_schema()
        with db.connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='memories'"
            ).fetchone()[0]
        assert count == 1

    def test_memories_table_has_required_columns(self, workspace: Path):
        db = MemoryDatabase(workspace)
        db.init_schema()
        with db.connect() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
        required = {
            'id', 'content', 'type', 'priority', 'source',
            'importance_score', 'access_count', 'tags', 'subject',
            'predicate', 'confidence', 'decay_rate', 'expires_at',
            'last_accessed_at', 'superseded_by', 'source_episode_id',
            'scope', 'scope_owner', 'agent_id', 'user_id', 'workspace_id',
            'metadata', 'created_at', 'updated_at',
        }
        assert required.issubset(cols)

    def test_scratchpad_primary_key_is_composite(self, workspace: Path):
        db = MemoryDatabase(workspace)
        db.init_schema()
        with db.connect() as conn:
            pk_cols = [
                r['name'] for r in
                conn.execute("PRAGMA table_info(scratchpad)").fetchall()
                if r['pk'] > 0
            ]
        assert pk_cols == ['user_id', 'workspace_id']
