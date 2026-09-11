"""SQLite 连接管理 + schema 初始化。"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_SCHEMA_VERSION = "1"

_SCHEMA_STATEMENTS = [
    # ----- _schema_meta -----
    """
    CREATE TABLE IF NOT EXISTS _schema_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    # ----- memories -----
    """
    CREATE TABLE IF NOT EXISTS memories (
        id              TEXT    PRIMARY KEY,
        content         TEXT    NOT NULL,
        type            TEXT    NOT NULL DEFAULT 'fact',
        priority        TEXT    NOT NULL DEFAULT 'long_term',
        source          TEXT    NOT NULL DEFAULT 'manual',
        importance_score REAL    NOT NULL DEFAULT 0.5,
        access_count    INTEGER NOT NULL DEFAULT 0,
        tags            TEXT    NOT NULL DEFAULT '[]',
        subject         TEXT    NOT NULL DEFAULT '',
        predicate       TEXT    NOT NULL DEFAULT '',
        confidence      REAL    NOT NULL DEFAULT 0.5,
        decay_rate      REAL    NOT NULL DEFAULT 0.1,
        expires_at      TEXT,
        last_accessed_at TEXT,
        superseded_by   TEXT,
        source_episode_id TEXT,
        scope           TEXT    NOT NULL DEFAULT 'global',
        scope_owner     TEXT    NOT NULL DEFAULT '',
        agent_id        TEXT    NOT NULL DEFAULT '',
        user_id         TEXT    NOT NULL DEFAULT 'default',
        workspace_id    TEXT    NOT NULL DEFAULT 'default',
        metadata        TEXT    NOT NULL DEFAULT '{}',
        created_at      TEXT    NOT NULL,
        updated_at      TEXT    NOT NULL,
        FOREIGN KEY (superseded_by)    REFERENCES memories(id),
        FOREIGN KEY (source_episode_id) REFERENCES episodes(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_memories_owner       ON memories(workspace_id, user_id, scope, scope_owner)",
    "CREATE INDEX IF NOT EXISTS idx_memories_type        ON memories(type)",
    "CREATE INDEX IF NOT EXISTS idx_memories_importance  ON memories(importance_score DESC)",
    "CREATE INDEX IF NOT EXISTS idx_memories_created     ON memories(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_memories_episode     ON memories(source_episode_id)",
    "CREATE INDEX IF NOT EXISTS idx_memories_super       ON memories(superseded_by)",
    # ----- episodes -----
    """
    CREATE TABLE IF NOT EXISTS episodes (
        id          TEXT    PRIMARY KEY,
        session_id  TEXT    NOT NULL,
        summary     TEXT    NOT NULL,
        goal        TEXT    NOT NULL DEFAULT '',
        outcome     TEXT    NOT NULL DEFAULT 'completed',
        source      TEXT    NOT NULL DEFAULT 'session_end',
        started_at  TEXT    NOT NULL,
        ended_at    TEXT    NOT NULL,
        action_nodes      TEXT NOT NULL DEFAULT '[]',
        entities          TEXT NOT NULL DEFAULT '[]',
        tools_used        TEXT NOT NULL DEFAULT '[]',
        linked_memory_ids TEXT NOT NULL DEFAULT '[]',
        tags              TEXT NOT NULL DEFAULT '[]',
        importance_score  REAL NOT NULL DEFAULT 0.5,
        access_count      INTEGER NOT NULL DEFAULT 0,
        compaction_checkpoint_id TEXT NOT NULL DEFAULT '',
        workspace_snapshot_id    TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_episodes_session    ON episodes(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_episodes_time       ON episodes(started_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_episodes_outcome    ON episodes(outcome)",
    "CREATE INDEX IF NOT EXISTS idx_episodes_importance ON episodes(importance_score DESC)",
    # ----- scratchpad -----
    """
    CREATE TABLE IF NOT EXISTS scratchpad (
        user_id         TEXT    NOT NULL,
        workspace_id    TEXT    NOT NULL,
        content         TEXT    NOT NULL DEFAULT '',
        active_projects TEXT    NOT NULL DEFAULT '[]',
        current_focus   TEXT    NOT NULL DEFAULT '',
        open_questions  TEXT    NOT NULL DEFAULT '[]',
        next_steps      TEXT    NOT NULL DEFAULT '[]',
        updated_at      TEXT    NOT NULL,
        PRIMARY KEY (user_id, workspace_id)
    )
    """,
    # ----- memories_fts + triggers -----
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
        content,
        subject,
        predicate,
        tags,
        content='memories',
        content_rowid='rowid',
        tokenize='unicode61 remove_diacritics 2'
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
        INSERT INTO memories_fts(rowid, content, subject, predicate, tags)
        VALUES (new.rowid, new.content, new.subject, new.predicate, new.tags);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, content, subject, predicate, tags)
        VALUES ('delete', old.rowid, old.content, old.subject, old.predicate, old.tags);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, content, subject, predicate, tags)
        VALUES ('delete', old.rowid, old.content, old.subject, old.predicate, old.tags);
        INSERT INTO memories_fts(rowid, content, subject, predicate, tags)
        VALUES (new.rowid, new.content, new.subject, new.predicate, new.tags);
    END
    """,
]


class MemoryDatabase:
    """nanobot 记忆系统的 SQLite 入口。

    负责连接管理、schema 初始化、迁移钩子。
    所有读写通过 :meth:`connect` 获取的 ``sqlite3.Connection`` 进行。
    """

    def __init__(self, workspace: Path, *, db_path: Path | None = None) -> None:
        self.workspace = Path(workspace)
        self.db_path = Path(db_path) if db_path is not None else (self.workspace / "memory" / "state.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """获取一个 SQLite 连接；同一进程内由锁串行化。"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute("PRAGMA foreign_keys = ON")
                conn.row_factory = sqlite3.Row
                yield conn
                conn.commit()
            finally:
                conn.close()

    def init_schema(self) -> None:
        """幂等地创建所有表、索引与版本元数据。"""
        with self.connect() as conn:
            for stmt in _SCHEMA_STATEMENTS:
                conn.execute(stmt)
            conn.execute(
                "INSERT OR REPLACE INTO _schema_meta (key, value) VALUES (?, ?)",
                ("version", _SCHEMA_VERSION),
            )

    def ensure_schema(self) -> None:
        """若未初始化则执行 init_schema。"""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='_schema_meta'"
            ).fetchone()
        if row is None:
            self.init_schema()


def bm25_rank_to_score(rank: float) -> float:
    """FTS5 bm25() 值 -> [0,1] 相关性。rank 越小越相关。"""
    return 1.0 / (1.0 + max(0.0, rank))
