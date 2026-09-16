"""索引器：把 SQLite 的 ``Memory`` 同步进 ``VectorStore``，并做双向对账。

边界（spec §4.2）：本模块**不知道** Chroma API——只调 ``VectorStore`` 的 4 个
方法（``upsert`` / ``remove`` / ``list_ids`` / ``delete_ids``），因此可用纯内存
替身单测。它是**唯一**把 ``Memory`` 领域模型转成 ``(id, content, metadata)``
三元组的地方。

为什么必须有对账（spec §3.2）：向量写入是异步 / 可失败的，新写入的记忆会被
静默漏掉；nanobot 的抽取走 idle timer 异步路径，同样会遇到。对账做双向修复——
删 stale（Chroma 有、SQLite 无）+ 补 missing（SQLite 有、Chroma 无）。
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from nanobot.memory import repository
from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import Memory

# 进程级 best-effort 钩子。写路径（extractor / webui / fallback replay）只需
# 调 :func:`index_memory_best_effort`，不必持有 indexer 引用，避免把向量依赖
# 注入到 5+ 个写入点。
_ACTIVE_INDEXER: "MemoryIndexer | None" = None


def set_active_indexer(indexer: "MemoryIndexer | None") -> None:
    """注册进程级 indexer（gateway 启动时调用一次）。"""
    global _ACTIVE_INDEXER
    _ACTIVE_INDEXER = indexer


def get_active_indexer() -> "MemoryIndexer | None":
    return _ACTIVE_INDEXER


def get_active_store():
    """进程级 indexer 背后的 store（VectorStore）；无 indexer 时返回 None。供 stats 读取向量状态。"""
    indexer = _ACTIVE_INDEXER
    return getattr(indexer, "_store", None) if indexer is not None else None


def index_memory_best_effort(memory: Memory) -> None:
    """写路径钩子：**绝不抛出**。

    向量失败绝不影响主流程（openakita 纪律，spec §4.5 不变式）。indexer 未注册
    （未启用向量 / 非 gateway 进程）时是 no-op。
    """
    indexer = _ACTIVE_INDEXER
    if indexer is None:
        return
    try:
        indexer.index(memory)
    except Exception as exc:  # noqa: BLE001 - 向量故障绝不传播
        logger.warning("vector index hook failed for {}: {}", memory.id, exc)


def remove_memory_best_effort(memory_id: str) -> None:
    """删除路径钩子：**绝不抛出**。"""
    indexer = _ACTIVE_INDEXER
    if indexer is None:
        return
    try:
        indexer.remove(memory_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("vector remove hook failed for {}: {}", memory_id, exc)


def _metadata_of(memory: Memory) -> dict[str, Any]:
    """``Memory`` → ``VectorStore.upsert`` 的 metadata 字典。

    仅传标量字段；``tags`` / ``metadata`` dict 等由 ``VectorStore._clean_metadata``
    丢弃（Chroma ``where`` 不支持多标签过滤）。
    """
    return {
        "type": memory.type.value if hasattr(memory.type, "value") else str(memory.type),
        "priority": memory.priority.value if hasattr(memory.priority, "value") else str(memory.priority),
        "importance": float(memory.importance_score or 0.0),
        "workspace_id": memory.workspace_id or "",
        "user_id": memory.user_id or "",
    }


def _latest_updated_at(conn) -> str:
    """在当前连接上取 ``memories`` 的最大 ``updated_at`` 作为同步游标。

    必须在调用方**已持有**的 ``connect()`` 连接上执行——``connect()`` 持的是
    非可重入 ``threading.Lock``，嵌套 ``connect()`` 会自死锁（见 ``_record_state``）。
    """
    try:
        row = conn.execute("SELECT MAX(updated_at) AS m FROM memories").fetchone()
        val = row["m"] if row is not None else None
        return str(val) if val is not None else ""
    except Exception:  # noqa: BLE001 - 游标取不到不影响对账结果落库
        return ""


class MemoryIndexer:
    """``Memory`` ↔ ``VectorStore`` 的同步器。"""

    def __init__(self, store: Any, database: MemoryDatabase) -> None:
        self._store = store
        self._database = database

    def index(self, memory: Memory) -> bool:
        """写入单条记忆。幂等（``upsert`` 语义）。"""
        return bool(self._store.upsert(memory.id, memory.content, _metadata_of(memory)))

    def remove(self, memory_id: str) -> bool:
        return bool(self._store.remove(memory_id))

    def sync_from_sqlite(self) -> dict[str, Any]:
        """双向对账：删 stale + 补 missing，并把结果写进 ``vector_sync_state``。

        返回 ``{"indexed", "deleted", "error"}``。**绝不抛出**——单条失败计入
        ``error`` 并继续，其余条目照常对账。
        """
        errors: list[str] = []
        try:
            with self._database.connect() as conn:
                memories = repository.list_memories(conn)
        except Exception as exc:  # noqa: BLE001
            logger.warning("vector sync: cannot read memories: {}", exc)
            return {"indexed": 0, "deleted": 0, "error": f"sqlite: {exc}"}

        try:
            indexed_ids = set(self._store.list_ids())
        except Exception as exc:  # noqa: BLE001
            logger.warning("vector sync: list_ids failed: {}", exc)
            return {"indexed": 0, "deleted": 0, "error": f"list_ids: {exc}"}

        sqlite_by_id = {m.id: m for m in memories}
        sqlite_ids = set(sqlite_by_id)

        stale = sorted(indexed_ids - sqlite_ids)
        missing = sorted(sqlite_ids - indexed_ids)

        deleted = 0
        if stale:
            try:
                deleted = int(self._store.delete_ids(stale))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"delete: {exc}")

        indexed = 0
        for mid in missing:
            try:
                if self.index(sqlite_by_id[mid]):
                    indexed += 1
            except Exception as exc:  # noqa: BLE001 - 单条失败不中断
                errors.append(f"{mid}: {exc}")

        error_text = "; ".join(errors)
        self._record_state(indexed=indexed, deleted=deleted, error=error_text)
        if error_text:
            logger.warning("vector sync completed with errors: {}", error_text)
        return {"indexed": indexed, "deleted": deleted, "error": error_text}

    def _record_state(self, *, indexed: int, deleted: int, error: str) -> None:
        try:
            with self._database.connect() as conn:
                repository.upsert_vector_sync_state(
                    conn,
                    cursor=_latest_updated_at(conn),
                    indexed=indexed,
                    deleted=deleted,
                    last_error=error,
                )
        except Exception as exc:
            logger.debug("vector sync state record failed: {}", exc)
