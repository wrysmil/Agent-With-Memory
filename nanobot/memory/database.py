"""SQLite 连接管理 + schema 初始化。"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


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
