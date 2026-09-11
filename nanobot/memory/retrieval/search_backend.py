"""搜索后端工厂：FTS5（默认）/ chromadb（可选）/ api_embedding（可选）。"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path
from typing import Protocol


class SearchBackend(Protocol):
    """搜索后端协议。"""

    def search(self, query: str, *, limit: int = 10) -> list[dict]: ...


def score_from_bm25_rank(rank: float) -> float:
    """将 FTS5 bm25() 排序值转换为 [0,1] 相关性分数。"""
    from nanobot.memory.database import bm25_rank_to_score

    return bm25_rank_to_score(rank)


class Fts5SearchBackend:
    """基于 FTS5 BM25 的零依赖搜索后端。FTS5 失败时回退到 LIKE。"""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path

    def search(self, query: str, *, limit: int = 10) -> list[dict]:
        if not query or not query.strip():
            return []
        conn = self._open()
        try:
            rows = self._try_fts5(conn, query, limit)
            if not rows:
                rows = self._fallback_like(conn, query, limit)
            return rows
        finally:
            conn.close()

    def _open(self) -> sqlite3.Connection:
        if self._db_path:
            path = str(Path(self._db_path))
        else:
            path = ":memory:"
        conn = sqlite3.connect(path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _try_fts5(
        self, conn: sqlite3.Connection, query: str, limit: int
    ) -> list[dict]:
        try:
            sql = """
                SELECT m.id, m.content, m.importance_score, m.updated_at,
                       bm25(memories_fts) AS rank
                FROM memories_fts fts
                JOIN memories m ON m.rowid = fts.rowid
                WHERE memories_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """
            cur = conn.execute(sql, (query, limit))
        except sqlite3.OperationalError:
            return []
        out: list[dict] = []
        for row in cur.fetchall():
            from nanobot.memory.database import bm25_rank_to_score

            out.append(
                {
                    "memory_id": row["id"],
                    "content": row["content"],
                    "importance_score": float(row["importance_score"] or 0.5),
                    "updated_at": row["updated_at"],
                    "score": bm25_rank_to_score(float(row["rank"])),
                    "channel": "fts5",
                }
            )
        return out

    def _fallback_like(
        self, conn: sqlite3.Connection, query: str, limit: int
    ) -> list[dict]:
        like = f"%{query}%"
        cur = conn.execute(
            """
            SELECT id, content, importance_score, updated_at
            FROM memories
            WHERE content LIKE ?
            ORDER BY importance_score DESC
            LIMIT ?
            """,
            (like, limit),
        )
        return [
            {
                "memory_id": row["id"],
                "content": row["content"],
                "importance_score": float(row["importance_score"] or 0.5),
                "updated_at": row["updated_at"],
                "score": 0.3,
                "channel": "fts5_fallback",
            }
            for row in cur.fetchall()
        ]


def create_search_backend(
    name: str, *, db_path: str | None = None
) -> SearchBackend:
    """工厂：根据 name 创建对应搜索后端，缺依赖时自动回退 FTS5。"""
    name = (name or "default").lower()
    if name in ("fts5", "default"):
        return Fts5SearchBackend(db_path=db_path)
    if name == "chromadb":
        if importlib.util.find_spec("chromadb") is None:
            return Fts5SearchBackend(db_path=db_path)
        # chromadb 真实后端实现留待未来增强（不在本 plan 范围）
        return Fts5SearchBackend(db_path=db_path)
    if name in ("api_embedding", "dashscope", "openai"):
        if importlib.util.find_spec("httpx") is None:
            return Fts5SearchBackend(db_path=db_path)
        # api_embedding 真实后端实现留待未来增强（不在本 plan 范围）
        return Fts5SearchBackend(db_path=db_path)
    # 未知名称默认回退 FTS5
    return Fts5SearchBackend(db_path=db_path)
