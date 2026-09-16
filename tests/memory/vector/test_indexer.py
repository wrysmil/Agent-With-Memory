"""MemoryIndexer：写路径与双向对账（V8）。"""
from __future__ import annotations

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import Memory, MemoryType
from nanobot.memory.repository import add_memory
from nanobot.memory.vector.indexer import MemoryIndexer


class _FakeStore:
    """内存版 VectorStore 替身：断言 indexer 只调这 4 个方法。"""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.calls: list[str] = []

    def upsert(self, memory_id, content, metadata):
        self.data[memory_id] = content
        self.calls.append("upsert")
        return True

    def remove(self, memory_id):
        existed = self.data.pop(memory_id, None) is not None
        self.calls.append("remove")
        return existed

    def list_ids(self):
        return list(self.data)

    def delete_ids(self, ids):
        n = 0
        for i in ids:
            n += 1 if self.data.pop(i, None) is not None else 0
        return n

    def count(self):
        return len(self.data)


def _db(tmp_path):
    db = MemoryDatabase(tmp_path)
    db.ensure_schema()
    return db


def _memory(mid: str, content: str) -> Memory:
    return Memory(
        id=mid, content=content, type=MemoryType.FACT,
        created_at="2026-09-16T00:00:00+00:00",
        updated_at="2026-09-16T00:00:00+00:00",
    )


def test_index_writes_content_and_scalar_metadata(tmp_path):
    store = _FakeStore()
    idx = MemoryIndexer(store, _db(tmp_path))
    assert idx.index(_memory("m1", "用户热爱创作")) is True
    assert store.data == {"m1": "用户热爱创作"}


def test_remove_deletes_from_store(tmp_path):
    store = _FakeStore()
    idx = MemoryIndexer(store, _db(tmp_path))
    idx.index(_memory("m1", "x"))
    assert idx.remove("m1") is True
    assert store.data == {}


def test_sync_backfills_missing_entries(tmp_path):
    db = _db(tmp_path)
    with db.connect() as conn:
        add_memory(conn, _memory("m1", "一"))
        add_memory(conn, _memory("m2", "二"))
    store = _FakeStore()
    idx = MemoryIndexer(store, db)
    result = idx.sync_from_sqlite()
    assert result["indexed"] == 2
    assert set(store.data) == {"m1", "m2"}


def test_sync_drops_stale_entries(tmp_path):
    db = _db(tmp_path)
    with db.connect() as conn:
        add_memory(conn, _memory("m1", "一"))
    store = _FakeStore()
    store.data["ghost"] = "幽灵"
    idx = MemoryIndexer(store, db)
    result = idx.sync_from_sqlite()
    assert result["deleted"] == 1
    assert set(store.data) == {"m1"}


def test_sync_is_idempotent(tmp_path):
    db = _db(tmp_path)
    with db.connect() as conn:
        add_memory(conn, _memory("m1", "一"))
    store = _FakeStore()
    idx = MemoryIndexer(store, db)
    first = idx.sync_from_sqlite()
    second = idx.sync_from_sqlite()
    assert (first["indexed"], first["deleted"]) == (1, 0)
    assert (second["indexed"], second["deleted"]) == (0, 0)


def test_sync_records_state_and_error(tmp_path):
    db = _db(tmp_path)
    store = _FakeStore()

    class _Boom(_FakeStore):
        def upsert(self, *a, **k):
            raise RuntimeError("chroma down")

    idx = MemoryIndexer(_Boom(), db)
    with db.connect() as conn:
        add_memory(conn, _memory("m1", "一"))
    result = idx.sync_from_sqlite()
    assert result["indexed"] == 0
    assert "chroma down" in result["error"]
