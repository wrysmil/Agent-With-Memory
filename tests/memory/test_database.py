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
