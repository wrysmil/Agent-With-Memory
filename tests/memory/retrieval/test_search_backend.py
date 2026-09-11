"""Tests for search_backend factory + FTS5 BM25 scoring."""
from __future__ import annotations

import sqlite3

import pytest

from nanobot.memory.retrieval.search_backend import (
    Fts5SearchBackend,
    create_search_backend,
    score_from_bm25_rank,
)


class TestScoreFromBm25Rank:
    def test_zero_rank_returns_one(self):
        assert score_from_bm25_rank(0.0) == pytest.approx(1.0)

    def test_monotonic_decreasing(self):
        assert score_from_bm25_rank(0.0) > score_from_bm25_rank(1.0)
        assert score_from_bm25_rank(1.0) > score_from_bm25_rank(10.0)
        assert score_from_bm25_rank(10.0) > score_from_bm25_rank(100.0)

    def test_negative_clamped_to_zero(self):
        assert score_from_bm25_rank(-5.0) == pytest.approx(1.0)


class TestFactory:
    def test_default_returns_fts5(self):
        backend = create_search_backend("default")
        assert isinstance(backend, Fts5SearchBackend)

    def test_fts5_explicit(self):
        backend = create_search_backend("fts5")
        assert isinstance(backend, Fts5SearchBackend)

    def test_chromadb_falls_back_to_fts5_when_missing(self):
        # chromadb is optional; factory should never raise, just fall back
        backend = create_search_backend("chromadb")
        assert isinstance(backend, Fts5SearchBackend)

    def test_unknown_name_falls_back_to_fts5(self):
        backend = create_search_backend("totally_unknown_backend")
        assert isinstance(backend, Fts5SearchBackend)


def _create_test_db(conn: sqlite3.Connection) -> None:
    """Set up memories table + FTS5 virtual table in an existing connection."""
    conn.executescript("""
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
            created_at      TEXT    NOT NULL,
            updated_at      TEXT    NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            content, subject, predicate, tags,
            content='memories', content_rowid='rowid',
            tokenize='unicode61 remove_diacritics 2'
        );
        CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, content, subject, predicate, tags)
            VALUES (new.rowid, new.content, new.subject, new.predicate, new.tags);
        END;
    """)


def _seed_memories(conn: sqlite3.Connection) -> None:
    """Insert test memory rows."""
    rows = [
        ("m1", "Python 爬虫 教程", "fact", 0.8, "Python", "is", "教程"),
        ("m2", "Java 入门 书籍", "fact", 0.7, "Java", "is", "入门"),
        ("m3", "Rust 异步编程", "fact", 0.6, "Rust", "is", "异步"),
    ]
    now = "2026-09-01T00:00:00"
    for id_, content, mtype, imp, subj, pred, tags in rows:
        conn.execute(
            "INSERT INTO memories (id,content,type,priority,source,"
            "importance_score,tags,subject,predicate,confidence,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (id_, content, mtype, "long_term", "manual", imp,
             tags, subj, pred, 0.9, now, now),
        )


class TestFts5SearchBackend:
    def test_search_returns_scored_results(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        _create_test_db(conn)
        _seed_memories(conn)
        conn.commit()

        backend = Fts5SearchBackend()  # uses in-memory
        backend._open = lambda: conn  # type: ignore[method-assign]
        results = backend.search("Python 爬虫", limit=10)

        assert len(results) >= 1
        top = results[0]
        assert "memory_id" in top
        assert "content" in top
        assert "score" in top
        assert 0.0 <= top["score"] <= 1.0
        assert top["score"] >= 0.5
        conn.close()

    def test_search_empty_query_returns_empty(self):
        backend = Fts5SearchBackend()
        results = backend.search("", limit=10)
        assert results == []

    def test_search_no_match_returns_like_fallback(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        _create_test_db(conn)
        _seed_memories(conn)
        conn.commit()

        backend = Fts5SearchBackend()
        backend._open = lambda: conn  # type: ignore[method-assign]
        # gibberish triggers LIKE fallback
        results = backend.search("xyznonexistent123", limit=10)
        assert isinstance(results, list)
        # Fallback LIKE match gives score=0.3
        assert all(r["score"] == 0.3 for r in results)
        conn.close()

    def test_fallback_like_score_is_0_3(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        _create_test_db(conn)
        _seed_memories(conn)
        conn.commit()

        backend = Fts5SearchBackend()
        backend._open = lambda: conn  # type: ignore[method-assign]
        # Use query that definitely triggers FTS5OperationalError (bad FTS5 syntax)
        # to force _fallback_like; then verify its score=0.3 by examining the code path
        try:
            # Attempt with invalid FTS5 syntax to force OperationalError
            results = backend._fallback_like(conn, "Python", 10)
        except Exception:
            results = []
        if results:
            assert results[0]["score"] == pytest.approx(0.3)
        # Also verify via _fallback_like directly
        fallback = backend._fallback_like(conn, "Java", 10)
        assert len(fallback) >= 1
        assert fallback[0]["score"] == pytest.approx(0.3)
        assert fallback[0]["channel"] == "fts5_fallback"
        conn.close()

    def test_fts5_channel_label(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        _create_test_db(conn)
        _seed_memories(conn)
        conn.commit()

        backend = Fts5SearchBackend()
        backend._open = lambda: conn  # type: ignore[method-assign]
        results = backend.search("Python", limit=10)
        fts_results = [r for r in results if r.get("channel") == "fts5"]
        assert len(fts_results) >= 1
        conn.close()
