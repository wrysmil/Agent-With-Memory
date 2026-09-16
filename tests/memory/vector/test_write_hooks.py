"""写路径 best-effort 钩子：向量失败绝不影响 SQLite 写入。"""
from __future__ import annotations

from nanobot.memory.models import Memory, MemoryType
from nanobot.memory.vector.indexer import (
    index_memory_best_effort,
    remove_memory_best_effort,
    set_active_indexer,
)


class _BoomStore:
    def upsert(self, *a, **k):
        raise RuntimeError("chroma down")

    def remove(self, *a, **k):
        raise RuntimeError("chroma down")

    def list_ids(self):
        return []

    def delete_ids(self, ids):
        return 0


class _RecordingStore:
    def __init__(self):
        self.upserted: list[str] = []
        self.removed: list[str] = []

    def upsert(self, memory_id, content, metadata):
        self.upserted.append(memory_id)
        return True

    def remove(self, memory_id):
        self.removed.append(memory_id)
        return True

    def list_ids(self):
        return []

    def delete_ids(self, ids):
        return len(ids)


def _memory(mid="m1"):
    return Memory(
        id=mid, content="内容", type=MemoryType.FACT,
        created_at="2026-09-16T00:00:00+00:00",
        updated_at="2026-09-16T00:00:00+00:00",
    )


def teardown_function():
    set_active_indexer(None)


def test_no_indexer_registered_is_noop():
    set_active_indexer(None)
    index_memory_best_effort(_memory())  # 不抛即通过
    remove_memory_best_effort("m1")


def test_hook_records_into_store(tmp_path):
    from nanobot.memory.database import MemoryDatabase
    from nanobot.memory.vector.indexer import MemoryIndexer

    store = _RecordingStore()
    db = MemoryDatabase(tmp_path)
    set_active_indexer(MemoryIndexer(store, db))
    index_memory_best_effort(_memory("m9"))
    remove_memory_best_effort("m8")
    assert store.upserted == ["m9"]
    assert store.removed == ["m8"]


def test_hook_swallows_store_failure(tmp_path):
    """D4 的写路径版本：向量炸了，钩子不抛。"""
    from nanobot.memory.database import MemoryDatabase
    from nanobot.memory.vector.indexer import MemoryIndexer

    db = MemoryDatabase(tmp_path)
    set_active_indexer(MemoryIndexer(_BoomStore(), db))
    index_memory_best_effort(_memory())
    remove_memory_best_effort("m1")
