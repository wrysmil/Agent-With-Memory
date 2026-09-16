"""store 适配层：把 ``MemoryDatabase`` 适配成四路通道期望的 store 契约。

背景（RCA 2026-09-15 根因 1）：``RetrievalEngine`` 原先直接持有
``MemoryDatabase``，而四个通道以**方法**形式调用 store
（``store.search_semantic_scored(...)`` 等）；真实实现却是
``nanobot.memory.repository`` 的**模块级函数**（首参 ``conn``）。
两者对不上 → 每次召回都抛 ``AttributeError`` → ``candidates`` 恒空 →
``retrieve_with_ids`` 恒返回 ``("", [])``。而该异常在 ``engine.py`` 里被
静默 ``continue`` 吞掉，所以故障多年不可见。

本模块是二者之间**唯一**的适配点：对外暴露通道期望的 4 个方法，对内按调用
粒度开关 ``MemoryDatabase.connect()``，复用 repository 的既有 SQL。

语义方法（``search_semantic_scored``）自 2026-09-16 起做**向量 ∪ FTS5 最高分
并集**（spec §4.6）：传入 ``vector_store`` 时两路各取 ``limit*3`` 候选按 id 融合，
不传时行为与改造前完全一致（R2 向后兼容）。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from nanobot.memory import repository
from nanobot.memory.database import MemoryDatabase


class MemoryStoreAdapter:
    """把 ``MemoryDatabase`` 适配为检索通道期望的 store 契约。

    方法契约（与 ``nanobot/memory/retrieval/channels/*.py`` 调用点对应）：

    - ``search_semantic_scored(query, *, limit) -> list[(Memory, float)]``
      向量 ∪ FTS5 最高分并集（需注入 ``vector_store``；未注入 = 纯 FTS5，行为不变）
    - ``search_episodes(*, entity, limit) -> list[_EpisodeRow]``
    - ``query_semantic(*, min_importance, since_days, limit) -> list[_MemoryRow]``
    - ``search_attachments(term, *, intent, limit) -> list[_AttachmentRow]``
      ⚠️ **元素形状与通道不符**（见该方法的 docstring）——通道按下标/字典协议
      取值（``item["id"]`` / ``item.get(...)``），而这里返回属性协议 dataclass。
      因 ``attachments`` 表尚未进 schema v1，该通道**当前不可达**，故本条只记录
      缺口、不在本 WU 内改返回类型（改契约属独立决策）。

    测试替身见 ``tests/memory/retrieval/test_engine.py::_StubStore``——
    本类与它实现同一方法集（``_StubStore`` 的 attachments 同样返回 ``list[Any]``）。
    """

    def __init__(
        self, database: MemoryDatabase, *, vector_store: Any = None
    ) -> None:
        self._database = database
        self._vector_store = vector_store

    # ----- 语义通道 -----
    def search_semantic_scored(
        self, query: str, *, limit: int = 30
    ) -> list[tuple[Any, float]]:
        """语义召回：向量 ∪ FTS5，按 id 取**最高分**（spec §4.6）。

        融合策略 v1 = 取最高分并集（与 openakita 一致，便于对照排障）。
        🔴 两侧分数都**已经**是「越大越相关」：FTS5 侧由 Task 9 的页内归一化
        保证，向量侧由 ``VectorStore._distance_to_score`` 完成符号翻转。
        本函数**不得**再引入任何 distance 语义。

        已知限制（v1，spec §8.1 R-4）：两路的分数分布不同源，直接比大小理论上
        不可校准。实测见 V4；v2 备选 RRF。
        """
        merged: dict[str, float] = {}

        fts5_hits: list[tuple[Any, float]] = []
        with self._database.connect() as conn:
            fts5_hits = repository.search_semantic_scored(conn, query, limit=limit * 3)
        for mem, score in fts5_hits:
            merged[mem.id] = max(merged.get(mem.id, 0.0), float(score))

        # 向量路：故障时静默降级为纯 FTS5（D4）。
        # VectorStore.search 自身已有 try/except；此处再包一层是因为 Adapter 是
        # 契约边界，不应假设任何实现的异常纪律。
        if self._vector_store is not None:
            try:
                vector_hits = self._vector_store.search(query, limit=limit * 3)
            except Exception as exc:  # noqa: BLE001 - 向量故障绝不上抛
                logger.warning("vector channel failed, degrading to fts5: {}", exc)
                vector_hits = []
            for mid, score in vector_hits:
                merged[str(mid)] = max(merged.get(str(mid), 0.0), float(score))

        ordered = sorted(merged.items(), key=lambda kv: kv[1], reverse=True)[:limit]

        out: list[tuple[Any, float]] = []
        fts5_by_id = {mem.id: mem for mem, _s in fts5_hits}
        with self._database.connect() as conn:
            for mid, score in ordered:
                mem = fts5_by_id.get(mid)
                if mem is None:
                    # 向量独有 id：回查 SQLite 权威行并做活性过滤（修正 3）。
                    # FTS5 侧的结果已来自 SQLite，不做重复校验，避免对既有
                    # FTS5 行为引入回归。
                    mem = repository.get_memory(conn, mid)
                    if mem is None or not _is_live(mem):
                        continue
                out.append((mem, score))
        return out

    # ----- 情节通道 -----
    def search_episodes(self, *, entity: str, limit: int = 5) -> list[Any]:
        """按实体名召回 episode。"""
        with self._database.connect() as conn:
            return repository.search_episodes(conn, entity=entity, limit=limit)

    # ----- 近期通道 -----
    def query_semantic(
        self, *, min_importance: float, since_days: int, limit: int
    ) -> list[Any]:
        """近期高重要性记忆。"""
        with self._database.connect() as conn:
            return repository.query_semantic(
                conn,
                min_importance=min_importance,
                since_days=since_days,
                limit=limit,
            )

    # ----- 附件通道 -----
    def search_attachments(
        self, term: str, *, intent: str, limit: int = 5
    ) -> list[Any]:
        """附件搜索。

        ``attachments`` 表尚未进入 schema v1（``repository.search_attachments``
        的 docstring 已声明「调用方须保证 schema 已扩展」）。表缺失时返回 ``[]``，
        而不是让 ``OperationalError`` 冒泡——该通道对普通 query 本就是条件性空
        操作（媒体词闸门，见 ``channels/attachments.py:41-45``），此处消化的只是
        「表还没建」这一结构性缺失，而非掩盖真实故障。真实故障仍会经
        ``engine.py`` 的 ``logger.warning`` 暴露。

        ⚠️ 已记录的契约缺口（2026-09-15 审查发现，**当前不可达**）：
        ``channels/attachments.py:52-66`` 用**下标/字典协议**消费元素
        （``item["id"]`` / ``item["content"]`` / ``item.get("updated_at", ...)`` /
        ``item.get("importance_score", ...)``），而 ``repository.search_attachments``
        返回的是属性协议 dataclass ``_AttachmentRow``。一旦建表让本方法不再抛
        ``OperationalError``，通道会立刻在 ``item["id"]`` 上抛
        ``TypeError: '_AttachmentRow' object is not subscriptable``——与 RCA 根因 1
        同类的 store↔channel 契约错位，只是被「表不存在」挡住了。
        修法二选一（属独立决策，不在本 WU 范围）：本方法把行转成 dict，或改通道
        用属性访问；建议连同 ``type: Protocol`` 一起收口，让静态检查能提前发现。
        """
        try:
            with self._database.connect() as conn:
                return repository.search_attachments(
                    conn, term=term, intent=intent, limit=limit
                )
        except sqlite3.OperationalError as exc:
            logger.debug("attachments channel unavailable: {}", exc)
            return []


def _is_live(memory: Any) -> bool:
    """向量独有候选的活性判定（spec §4.6）。

    Chroma 的 ``where`` 表达力不足，scope / superseded / expired 三类校验只能
    在 SQLite 侧做。
    """
    if getattr(memory, "superseded_by", None):
        return False
    expires_at = getattr(memory, "expires_at", None)
    if expires_at:
        try:
            if datetime.fromisoformat(str(expires_at)) < datetime.now(timezone.utc):
                return False
        except (ValueError, TypeError):
            # 无法解析的 expires_at 视为未过期（宽松），避免误杀
            pass
    return True
