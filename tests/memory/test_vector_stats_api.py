"""stats 扩字段与 reindex/sync 动作（V7/V9 + spec §5.4）。"""
from __future__ import annotations

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import Memory, MemoryType
from nanobot.memory.repository import add_memory
from nanobot.memory.vector.indexer import set_active_indexer
from nanobot.webui.memory_api import reindex_vector, stats_payload, sync_vector
from nanobot.webui.memory_services import MemoryServices


def teardown_function():
    # reindex/sync/stats 在未显式传参时 fallback 到进程级 indexer；
    # 必须确保全局态干净，否则 test_reindex_without_indexer_returns_unavailable
    # 会被其它测试文件残留的 active indexer 污染成假通过/假失败。
    set_active_indexer(None)



class _FakeStore:
    def __init__(self):
        self.data: dict[str, str] = {}

    def upsert(self, mid, content, metadata):
        self.data[mid] = content
        return True

    def remove(self, mid):
        return self.data.pop(mid, None) is not None

    def list_ids(self):
        return list(self.data)

    def delete_ids(self, ids):
        return sum(1 for i in ids if self.data.pop(i, None) is not None)

    def count(self):
        return len(self.data)


def _services(tmp_path) -> MemoryServices:
    db = MemoryDatabase(tmp_path)
    db.ensure_schema()
    with db.connect() as conn:
        add_memory(
            conn,
            Memory(
                id="m1", content="内容", type=MemoryType.FACT,
                created_at="2026-09-16T00:00:00+00:00",
                updated_at="2026-09-16T00:00:00+00:00",
            ),
        )
    return MemoryServices(workspace_id="default", database=db)


def test_stats_without_vector_keeps_legacy_fields(tmp_path):
    """D5/R6：未启用向量 → 老字段不变，新字段显示不可用。"""
    payload = stats_payload(_services(tmp_path))
    assert payload["total"] == 1
    assert "fact" in payload["by_type"]
    assert payload["search_backend"] == "fts5"
    assert payload["vector_available"] is False
    assert payload["vector_count"] == 0


def test_stats_reports_vector_state_when_wired(tmp_path):
    """V9：7 个新字段齐全且值正确。"""
    services = _services(tmp_path)
    store = _FakeStore()

    class _Runtime:
        search_backend = "chromadb"
        vector_state = "ready"
        model_name = "BAAI/bge-small-zh-v1.5"
        dimensions = 512
        error = None

        def count(self):
            return store.count()

    payload = stats_payload(services, vector_runtime=_Runtime())
    assert payload["search_backend"] == "chromadb"
    assert payload["vector_available"] is True
    assert payload["vector_state"] == "ready"
    assert payload["vector_model"] == "BAAI/bge-small-zh-v1.5"
    assert payload["vector_dimensions"] == 512
    assert payload["vector_error"] is None
    for key in (
        "search_backend", "vector_available", "vector_state",
        "vector_count", "vector_model", "vector_dimensions", "vector_error",
    ):
        assert key in payload


def test_reindex_rebuilds_from_sqlite(tmp_path):
    """V7：索引可重建。"""
    services = _services(tmp_path)
    store = _FakeStore()

    class _Runtime:
        search_backend = "chromadb"
        vector_state = "ready"
        model_name = "m"
        dimensions = 512
        error = None

        def count(self):
            return store.count()

    from nanobot.memory.vector.indexer import MemoryIndexer

    indexer = MemoryIndexer(store, services.database)
    result = reindex_vector(services, indexer=indexer, vector_runtime=_Runtime())
    assert result["indexed"] == 1
    assert store.count() == 1


def test_sync_drops_stale(tmp_path):
    """V8：手动加幽灵 id → sync 删除。"""
    services = _services(tmp_path)
    store = _FakeStore()
    store.data["ghost"] = "幽灵"
    from nanobot.memory.vector.indexer import MemoryIndexer

    indexer = MemoryIndexer(store, services.database)
    result = sync_vector(services, indexer=indexer)
    assert result["deleted"] == 1
    assert "ghost" not in store.data


def test_reindex_without_indexer_returns_unavailable(tmp_path):
    """未启用向量 → 端点不报 500，返回明确的不可用信号。"""
    result = reindex_vector(_services(tmp_path), indexer=None, vector_runtime=None)
    assert result["available"] is False
    assert result["indexed"] == 0
