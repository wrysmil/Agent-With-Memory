"""SQLite 连接管理 + schema 初始化。"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from loguru import logger

from nanobot.memory.repository import (
    add_episode,
    add_memory,
    update_memory_source_episode,
)

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
        # WU-3B: 写入失败时的 fallback 目录（同级 _memory_fallback/）。
        # 构造时即 mkdir，确保 _safe_write_with_fallback / replay_fallback
        # 在数据库不可写时也能落地 JSON 文件。
        self.fallback_dir = self.db_path.parent / "_memory_fallback"
        self.fallback_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # MAJOR #3: 构造时自动 replay 一次,与 MemoryExtractor.__init__ 端的
        # 调用形成双调幂等(第一次清空,第二次 glob 空 list 直接 no-op)。
        # replay_fallback 内部已对单文件 try/except 隔离,此处再加 try/except
        # 是双保险:启动路径绝不被 fallback 队列污染。
        try:
            self.replay_fallback()
        except Exception as exc:  # noqa: BLE001 - 启动路径绝不被污染
            logger.warning("memory fallback replay at init failed: {}", exc)

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

    def replay_fallback(self) -> int:
        """把 ``fallback_dir`` 中的所有 JSON 载荷重新执行一次,成功后删除文件。

        设计依据:``_safe_write_with_fallback`` 在 DB 写入失败时把整条 payload 落盘
        到 ``fallback_dir``。下次启动 / 下次 _persist 调用前调用本方法把队列重放,
        避免 LLM 已经成功抽取但写入失败导致数据丢失。

        - 单文件失败仅 ``logger.warning``,**绝不**抛出,以保证启动路径不被污染。
        - 文件名按字典序排序以保证顺序;失败文件保留以便下次再试。
        - 返回成功重放的数量。
        """
        if not self.fallback_dir.exists():
            return 0
        succeeded = 0
        # 按文件名排序(ISO ts 前缀保证顺序);glob 一次拿全避免迭代中新增干扰。
        paths = sorted(self.fallback_dir.glob("*.json"))
        for path in paths:
            try:
                payload_raw = path.read_text(encoding="utf-8")
                payload = json.loads(payload_raw)
                kind = payload.get("kind")
                item = payload.get("item")
                if not isinstance(item, dict):
                    raise ValueError(f"fallback payload missing/invalid 'item': {path.name}")
                with self.connect() as conn:
                    if kind == "memory":
                        from nanobot.memory.models import Memory

                        memory = Memory.from_row(item)
                        add_memory(conn, memory)
                    elif kind == "episode":
                        from nanobot.memory.models import Episode

                        episode = Episode.from_row(item)
                        add_episode(conn, episode)
                    elif kind == "backfill":
                        memory_id = str(item.get("memory_id") or "")
                        episode_id = str(item.get("episode_id") or "")
                        if not memory_id or not episode_id:
                            raise ValueError(
                                f"backfill payload missing ids: {path.name}"
                            )
                        update_memory_source_episode(conn, memory_id, episode_id)
                    else:
                        raise ValueError(f"unknown fallback kind: {kind!r}")
                path.unlink()
                succeeded += 1
            except Exception as exc:  # noqa: BLE001 - 单文件失败隔离
                logger.warning(
                    "memory fallback replay skipped for {}: {}",
                    path.name,
                    exc,
                )
        return succeeded


def bm25_rank_to_score(rank: float) -> float:
    """FTS5 bm25() 值 -> [0,1] 相关性。rank 越小越相关。"""
    return 1.0 / (1.0 + max(0.0, rank))
