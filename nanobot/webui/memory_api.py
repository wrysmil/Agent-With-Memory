"""Pure payload conversion + CRUD action functions for the WebUI memory domain.

This module owns the JSON wire format for ``Memory`` / ``Episode`` /
``ScratchpadEntry`` dataclasses and the pure CRUD entry points consumed by
``memory_routes.py``. It deliberately does NOT depend on HTTP/WebSocket
machinery; transport lives elsewhere.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from loguru import logger

from nanobot.memory.models import (
    Episode,
    EpisodeOutcome,
    Memory,
    MemoryPriority,
    MemoryType,
    ScratchpadEntry,
)
from nanobot.memory.repository import (
    add_memory,
    get_episode,
    get_memory,
    get_scratchpad,
    list_episodes,
    list_memories,
    search_memories,
    upsert_scratchpad,
)
from nanobot.memory.repository import (
    delete_episode as _repo_delete_episode,
)
from nanobot.memory.repository import (
    delete_memory as _repo_delete_memory,
)
from nanobot.memory.repository import (
    update_episode as _repo_update_episode,
)
from nanobot.memory.repository import (
    update_memory as _repo_update_memory,
)
from nanobot.memory.vector.indexer import (
    get_active_indexer,
    get_active_store,
    index_memory_best_effort,
    remove_memory_best_effort,
)
from nanobot.webui.memory_services import MemoryServices
from nanobot.webui.settings_contracts import WebUISettingsError

_DEFAULT_USER_ID = "default"
_VALID_MEMORY_TYPES = {t.value for t in MemoryType}
_VALID_MEMORY_PRIORITIES = {p.value for p in MemoryPriority}
_VALID_OUTCOMES = {o.value for o in EpisodeOutcome}


class WebUIMemoryError(WebUISettingsError):
    """User-facing memory validation failure with an explicit HTTP status.

    Subclass of ``WebUISettingsError`` so the settings router's single error
    path catches memory-domain failures alongside the rest of the settings API.
    """


# ---- payload converters -----------------------------------------------------


def memory_payload(m: Memory) -> dict[str, Any]:
    """Serialise a ``Memory`` to the JSON shape the WebUI expects."""
    return {
        "id": m.id,
        "content": m.content,
        "type": m.type.value,
        "priority": m.priority.value,
        "source": m.source,
        "importance_score": m.importance_score,
        "access_count": m.access_count,
        "tags": list(m.tags),
        "subject": m.subject,
        "predicate": m.predicate,
        "confidence": m.confidence,
        "decay_rate": m.decay_rate,
        "expires_at": m.expires_at,
        "last_accessed_at": m.last_accessed_at,
        "superseded_by": m.superseded_by,
        "source_episode_id": m.source_episode_id,
        "scope": m.scope,
        "workspace_id": m.workspace_id,
        "created_at": m.created_at,
        "updated_at": m.updated_at,
        "metadata": dict(m.metadata),
    }


def episode_payload(ep: Episode) -> dict[str, Any]:
    return {
        "id": ep.id,
        "session_id": ep.session_id,
        "summary": ep.summary,
        "goal": ep.goal,
        "outcome": ep.outcome.value,
        "source": ep.source.value,
        "started_at": ep.started_at,
        "ended_at": ep.ended_at,
        "action_nodes": list(ep.action_nodes),
        "entities": list(ep.entities),
        "tools_used": list(ep.tools_used),
        "linked_memory_ids": list(ep.linked_memory_ids),
        "tags": list(ep.tags),
        "importance_score": ep.importance_score,
        "access_count": ep.access_count,
    }


def scratchpad_payload_from_entry(entry: ScratchpadEntry) -> dict[str, Any]:
    return {
        "user_id": entry.user_id,
        "workspace_id": entry.workspace_id,
        "updated_at": entry.updated_at,
        "content": entry.content,
        "active_projects": list(entry.active_projects),
        "current_focus": entry.current_focus,
        "open_questions": list(entry.open_questions),
        "next_steps": list(entry.next_steps),
    }


# ---- query param parsing ----------------------------------------------------


def _parse_optional_enum(raw: str | None, choices: set[str], field: str) -> str | None:
    if raw is None:
        return None
    if raw not in choices:
        raise WebUIMemoryError(f"invalid {field}: {raw!r}")
    return raw


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# ---- read actions -----------------------------------------------------------


def list_memories_payload(
    services: MemoryServices,
    *,
    type: str | None = None,
    order: str = "created",
    limit: int | None = None,
) -> dict[str, Any]:
    type_value = _parse_optional_enum(type, _VALID_MEMORY_TYPES, "type")
    if order not in {"created", "importance"}:
        raise WebUIMemoryError("order must be 'created' or 'importance'")
    if limit is not None and limit < 1:
        raise WebUIMemoryError("limit must be >= 1")

    with services.database.connect() as conn:
        rows = list_memories(
            conn,
            type=MemoryType(type_value) if type_value else None,
            workspace_id=services.workspace_id,
            order_by=order,
            limit=limit,
        )
    return {"items": [memory_payload(r) for r in rows]}


def search_memories_payload(
    services: MemoryServices,
    query: str,
    *,
    type: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    type_value = _parse_optional_enum(type, _VALID_MEMORY_TYPES, "type")
    if not query.strip():
        raise WebUIMemoryError("query must not be empty")
    with services.database.connect() as conn:
        rows = search_memories(
            conn,
            query,
            type=MemoryType(type_value) if type_value else None,
            workspace_id=services.workspace_id,
            limit=limit,
        )
    return {"items": [memory_payload(r) for r in rows], "query": query}


def fetch_memory_payload(services: MemoryServices, memory_id: str) -> dict[str, Any]:
    with services.database.connect() as conn:
        row = get_memory(conn, memory_id)
    if row is None:
        raise WebUIMemoryError("memory not found", status=404)
    return {"memory": memory_payload(row)}


def list_episodes_payload(
    services: MemoryServices,
    *,
    session_id: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    if limit is not None and limit < 1:
        raise WebUIMemoryError("limit must be >= 1")
    with services.database.connect() as conn:
        rows = list_episodes(conn, session_id=session_id, limit=limit)
    return {"items": [episode_payload(r) for r in rows]}


def fetch_episode_payload(services: MemoryServices, episode_id: str) -> dict[str, Any]:
    with services.database.connect() as conn:
        row = get_episode(conn, episode_id)
    if row is None:
        raise WebUIMemoryError("episode not found", status=404)
    return {"episode": episode_payload(row)}


def scratchpad_payload(
    services: MemoryServices,
    *,
    user_id: str = _DEFAULT_USER_ID,
) -> dict[str, Any]:
    with services.database.connect() as conn:
        row = get_scratchpad(conn, user_id, services.workspace_id)
    if row is None:
        return {"scratchpad": None}
    return {"scratchpad": scratchpad_payload_from_entry(row)}


# ---- MEMORY.md derived-state helpers ----------------------------------------


def _get_last_refresh_at(workspace_id: str) -> str | None:
    """从 MemoryLifecycle._last_refresh_iso 读取上次刷新时间（ISO8601 UTC）。"""
    from nanobot.memory.lifecycle import MemoryLifecycle

    return MemoryLifecycle._last_refresh_iso.get(workspace_id)


def _get_last_refresh_trigger(workspace_id: str) -> str | None:
    from nanobot.memory.lifecycle import MemoryLifecycle

    return MemoryLifecycle._last_refresh_trigger.get(workspace_id)


def _draft_exists(workspace_id: str) -> bool:
    """检查 MEMORY.md.draft 是否存在。"""
    from nanobot.memory.lifecycle import MemoryLifecycle

    inst = MemoryLifecycle._instances.get(workspace_id)
    if inst is None:
        return False
    return inst.draft_file.exists()


def _draft_age_seconds(workspace_id: str) -> int | None:
    """draft 文件距今秒数，不存在返回 None。"""
    from nanobot.memory.lifecycle import MemoryLifecycle

    inst = MemoryLifecycle._instances.get(workspace_id)
    if inst is None or not inst.draft_file.exists():
        return None
    try:
        return int(time.time() - inst.draft_file.stat().st_mtime)
    except OSError:
        return None


def _current_memory_md_chars(workspace_id: str) -> int:
    """MEMORY.md 当前字符数，不存在返回 0。"""
    from nanobot.memory.lifecycle import MemoryLifecycle

    inst = MemoryLifecycle._instances.get(workspace_id)
    if inst is None or not inst.memory_file.exists():
        return 0
    try:
        return inst.memory_file.stat().st_size
    except OSError:
        return 0


def _read_memory_md_content(workspace_id: str) -> str | None:
    """读取 MEMORY.md 文件内容，不存在返回 None。"""
    from nanobot.memory.lifecycle import MemoryLifecycle

    inst = MemoryLifecycle._instances.get(workspace_id)
    if inst is None or not inst.memory_file.exists():
        return None
    try:
        return inst.memory_file.read_text(encoding="utf-8")
    except OSError:
        return None


def _read_draft_content(workspace_id: str) -> str | None:
    """读取 MEMORY.md.draft 文件内容，不存在返回 None。"""
    from nanobot.memory.lifecycle import MemoryLifecycle

    inst = MemoryLifecycle._instances.get(workspace_id)
    if inst is None or not inst.draft_file.exists():
        return None
    try:
        return inst.draft_file.read_text(encoding="utf-8")
    except OSError:
        return None


def get_memory_md_content(services: MemoryServices) -> dict[str, Any]:
    """读取 MEMORY.md 和 draft 内容，用于前端 Modal 展示。"""
    workspace_id = services.workspace_id
    memory_md = _read_memory_md_content(workspace_id)
    draft = _read_draft_content(workspace_id)
    return {
        "memory_md": memory_md,
        "draft": draft,
        "memory_md_exists": memory_md is not None,
        "draft_exists": draft is not None,
    }


def stats_payload(
    services: MemoryServices,
    *,
    vector_runtime: Any = None,
) -> dict[str, Any]:
    """Aggregate counts by memory type + 向量层状态（spec §5.4）。

    向量状态必须可见：openakita 把它完全藏在 UI 之外，降级时用户零感知
    （调研文档 §8.2 的反面教材）。``vector_runtime`` 是 ``VectorStore`` 或
    ``None``；未显式传参时 fallback 到进程级 ``get_active_store()``（与 WU-08
    写路径钩子同构，规避逐层透传）。传 ``None`` 时新字段仍全部存在，只是显示
    为不可用——**字段集恒定**，避免前端做存在性判断。
    """
    if vector_runtime is None:
        vector_runtime = get_active_store()

    counters: dict[str, int] = {t.value: 0 for t in MemoryType}
    total = 0
    with services.database.connect() as conn:
        rows = list_memories(conn, workspace_id=services.workspace_id)
    for r in rows:
        counters[r.type.value] = counters.get(r.type.value, 0) + 1
        total += 1

    if vector_runtime is None:
        return {
            "total": total,
            "by_type": counters,
            "search_backend": "fts5",
            "vector_available": False,
            "vector_state": "disabled",
            "vector_count": 0,
            "vector_model": "",
            "vector_dimensions": 0,
            "vector_error": None,
            "memory_md": {
                "last_refresh_at": _get_last_refresh_at(services.workspace_id),
                "last_refresh_trigger": _get_last_refresh_trigger(services.workspace_id),
                "draft_exists": _draft_exists(services.workspace_id),
                "draft_age_seconds": _draft_age_seconds(services.workspace_id),
                "current_chars": _current_memory_md_chars(services.workspace_id),
                "max_chars": 1500,
            },
        }

    # 兼容测试替身（提供 vector_state / search_backend）与生产 VectorStore
    # （只有 state、无 search_backend）：优先读专有字段，缺失时回退。
    state = str(
        getattr(vector_runtime, "vector_state", None)
        or getattr(vector_runtime, "state", "")
        or "unknown"
    )
    return {
        "total": total,
        "by_type": counters,
        "search_backend": str(getattr(vector_runtime, "search_backend", None) or "chromadb"),
        "vector_available": state == "ready",
        "vector_state": state,
        "vector_count": int(getattr(vector_runtime, "count", lambda: 0)()),
        "vector_model": str(getattr(vector_runtime, "model_name", "")),
        "vector_dimensions": int(getattr(vector_runtime, "dimensions", 0)),
        "vector_error": getattr(vector_runtime, "error", None),
        "memory_md": {
            "last_refresh_at": _get_last_refresh_at(services.workspace_id),
            "last_refresh_trigger": _get_last_refresh_trigger(services.workspace_id),
            "draft_exists": _draft_exists(services.workspace_id),
            "draft_age_seconds": _draft_age_seconds(services.workspace_id),
            "current_chars": _current_memory_md_chars(services.workspace_id),
            "max_chars": 1500,
        },
    }


def _vector_not_ready_reason(vector_runtime: Any) -> str:
    """向量 store 未就绪时返回原因串；就绪或测试替身（无 state 属性）返回 ``""``。"""
    state = getattr(vector_runtime, "state", None) or getattr(
        vector_runtime, "vector_state", None
    )
    if state is None or str(state) == "ready":
        return ""
    return f"vector not ready: {state}"


def reindex_vector(
    services: MemoryServices,
    *,
    indexer: Any = None,
    vector_runtime: Any = None,
) -> dict[str, Any]:
    """全量重建向量索引（spec §5.4）。

    ``sync_from_sqlite`` 本身就是双向修复（补 missing + 删 stale），因此「重建」
    与「同步」在实现上是同一操作；差别只在**语义契约**：``reindex`` 承诺
    「调用后索引与 SQLite 一致」，``sync`` 承诺「增量对账」。保留两个端点是为了
    让运维意图显式，且 openakita **没有**任何手动重建入口（调研文档 §9 末）。

    未显式传 ``indexer`` / ``vector_runtime`` 时 fallback 到进程级单例
    （``get_active_indexer`` / ``get_active_store``）。向量未启用时两者皆
    ``None``，端点返回明确的不可用信号而非 500。
    """
    if indexer is None:
        indexer = get_active_indexer()
    if vector_runtime is None:
        vector_runtime = get_active_store()
    if indexer is None:
        return {"available": False, "indexed": 0, "deleted": 0, "error": "vector disabled"}
    not_ready = _vector_not_ready_reason(vector_runtime)
    if not_ready:
        # store 未就绪时 list_ids 返 []、upsert 静默返 False → 会报「0 改动」的
        # **假成功**；用户无法区分「已一致」与「向量挂了」。显式报不可用。
        return {"available": False, "indexed": 0, "deleted": 0, "error": not_ready}
    result = indexer.sync_from_sqlite()
    return {
        "available": True,
        "indexed": int(result.get("indexed", 0)),
        "deleted": int(result.get("deleted", 0)),
        "error": result.get("error", ""),
        "vector_count": int(getattr(vector_runtime, "count", lambda: 0)()),
    }


def sync_vector(
    services: MemoryServices,
    *,
    indexer: Any = None,
    vector_runtime: Any = None,
) -> dict[str, Any]:
    """增量对账（补 missing + 删 stale），不重建。

    未显式传 ``indexer`` 时 fallback 到 ``get_active_indexer()``；未启用向量时
    返回明确的不可用信号而非 500。
    """
    if indexer is None:
        indexer = get_active_indexer()
    if vector_runtime is None:
        vector_runtime = get_active_store()
    if indexer is None:
        return {"available": False, "indexed": 0, "deleted": 0, "error": "vector disabled"}
    not_ready = _vector_not_ready_reason(vector_runtime)
    if not_ready:
        return {"available": False, "indexed": 0, "deleted": 0, "error": not_ready}
    result = indexer.sync_from_sqlite()
    return {
        "available": True,
        "indexed": int(result.get("indexed", 0)),
        "deleted": int(result.get("deleted", 0)),
        "error": result.get("error", ""),
    }


# ---- write action hooks ------------------------------------------------------


def _refresh_memory_md_after_mutation(
    workspace_id: str, services: "MemoryServices"
) -> None:
    """在 SQLite 写操作后触发 MEMORY.md 派生（同步调用）。

    派生失败只记日志，不抛给 mutation 调用方。
    60s 去抖在 MemoryLifecycle 层面处理。
    """
    try:
        from nanobot.memory.lifecycle import MemoryLifecycle

        lifecycle = MemoryLifecycle.for_workspace(workspace_id, services)
        lifecycle.refresh_memory_md_sync(workspace_id)
    except Exception:
        _logger = logging.getLogger(__name__)
        _logger.warning("[MemoryLifecycle] refresh_memory_md failed after mutation: %s")


# ---- write actions -----------------------------------------------------------


def create_memory(
    services: MemoryServices,
    *,
    content: str,
    type: str = "fact",
    priority: str = "long_term",
    importance_score: float = 0.5,
    tags: list[str] | None = None,
    subject: str = "",
    predicate: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not (isinstance(content, str) and content.strip()):
        raise WebUIMemoryError("content must be a non-empty string")
    if type not in _VALID_MEMORY_TYPES:
        raise WebUIMemoryError(f"invalid type: {type!r}")
    if priority not in _VALID_MEMORY_PRIORITIES:
        raise WebUIMemoryError(f"invalid priority: {priority!r}")
    if not (0.0 <= float(importance_score) <= 1.0):
        raise WebUIMemoryError("importance_score must be in [0, 1]")
    memory_id = str(uuid.uuid4())
    now = _now_iso()
    memory = Memory(
        id=memory_id,
        content=content.strip(),
        type=MemoryType(type),
        priority=MemoryPriority(priority),
        importance_score=float(importance_score),
        tags=list(tags or []),
        subject=subject,
        predicate=predicate,
        metadata=dict(metadata or {}),
        source="manual",
        workspace_id=services.workspace_id,
        created_at=now,
        updated_at=now,
    )
    try:
        with services.database.connect() as conn:
            add_memory(conn, memory)
    except Exception:
        logger.exception("create_memory failed: id={}", memory_id)
        raise WebUIMemoryError("failed to persist memory", status=500)
    # 向量挂钩：SQLite 落库成功（try 块之后）才 best-effort 索引
    index_memory_best_effort(memory)
    # 🆕 WU-3: 触发 MEMORY.md 派生
    _refresh_memory_md_after_mutation(services.workspace_id, services)
    return {"memory": memory_payload(memory)}


def fetch_and_index(services: MemoryServices, memory_id: str) -> Memory:
    """回读权威行并 best-effort 同步向量索引。

    更新走 repository 后必须回读，因为 ``_repo_update_memory`` 只吃 kwargs、
    不返回新行；同时保证索引里存的是**落库后**的内容。
    """
    with services.database.connect() as conn:
        row = get_memory(conn, memory_id)
    if row is None:
        raise WebUIMemoryError("memory not found", status=404)
    index_memory_best_effort(row)
    return row


def update_memory(
    services: MemoryServices,
    memory_id: str,
    *,
    content: str | None = None,
    type: str | None = None,
    priority: str | None = None,
    importance_score: float | None = None,
    tags: list[str] | None = None,
    subject: str | None = None,
    predicate: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if content is not None:
        if not content.strip():
            raise WebUIMemoryError("content must be a non-empty string")
        kwargs["content"] = content.strip()
    if type is not None:
        if type not in _VALID_MEMORY_TYPES:
            raise WebUIMemoryError(f"invalid type: {type!r}")
        kwargs["type"] = MemoryType(type)
    if priority is not None:
        if priority not in _VALID_MEMORY_PRIORITIES:
            raise WebUIMemoryError(f"invalid priority: {priority!r}")
        kwargs["priority"] = MemoryPriority(priority)
    if importance_score is not None:
        if not (0.0 <= float(importance_score) <= 1.0):
            raise WebUIMemoryError("importance_score must be in [0, 1]")
        kwargs["importance_score"] = float(importance_score)
    if tags is not None:
        kwargs["tags"] = list(tags)
    if subject is not None:
        kwargs["subject"] = subject
    if predicate is not None:
        kwargs["predicate"] = predicate
    if metadata is not None:
        kwargs["metadata"] = dict(metadata)
    try:
        with services.database.connect() as conn:
            _repo_update_memory(conn, memory_id, **kwargs)
    except KeyError:
        raise WebUIMemoryError("memory not found", status=404)
    updated = fetch_and_index(services, memory_id)
    # 🆕 WU-3: 触发 MEMORY.md 派生
    _refresh_memory_md_after_mutation(services.workspace_id, services)
    return {"memory": memory_payload(updated)}


def delete_memory(services: MemoryServices, memory_id: str) -> dict[str, Any]:
    with services.database.connect() as conn:
        cur = conn.execute("SELECT 1 FROM memories WHERE id = ?", (memory_id,))
        if cur.fetchone() is None:
            raise WebUIMemoryError("memory not found", status=404)
        _repo_delete_memory(conn, memory_id)
    # 向量挂钩：SQLite 删除成功后 best-effort 移除索引
    remove_memory_best_effort(memory_id)
    # 🆕 WU-3: 触发 MEMORY.md 派生
    _refresh_memory_md_after_mutation(services.workspace_id, services)
    return {"ok": True}


def update_episode(
    services: MemoryServices,
    episode_id: str,
    *,
    summary: str | None = None,
    goal: str | None = None,
    tags: list[str] | None = None,
    importance_score: float | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if summary is not None:
        kwargs["summary"] = summary
    if goal is not None:
        kwargs["goal"] = goal
    if tags is not None:
        kwargs["tags"] = list(tags)
    if importance_score is not None:
        kwargs["importance_score"] = float(importance_score)
    try:
        with services.database.connect() as conn:
            _repo_update_episode(conn, episode_id, **kwargs)
    except KeyError:
        raise WebUIMemoryError("episode not found", status=404)
    return fetch_episode_payload(services, episode_id)


def delete_episode(services: MemoryServices, episode_id: str) -> dict[str, Any]:
    try:
        with services.database.connect() as conn:
            _repo_delete_episode(conn, episode_id)
    except KeyError:
        raise WebUIMemoryError("episode not found", status=404)
    return {"ok": True}


def refresh_memory_md(services: "MemoryServices") -> dict[str, Any]:
    """手动触发 MEMORY.md 从 SQLite 重建。

    供 WebUI mutation 调用。
    """
    from nanobot.memory.lifecycle import MemoryLifecycle

    lifecycle = MemoryLifecycle.for_workspace(services.workspace_id, services)
    result = lifecycle.refresh_memory_md_sync(services.workspace_id)
    return result


def save_scratchpad(
    services: MemoryServices,
    *,
    user_id: str = _DEFAULT_USER_ID,
    content: str = "",
    active_projects: list[str] | None = None,
    current_focus: str = "",
    open_questions: list[str] | None = None,
    next_steps: list[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(content, str):
        raise WebUIMemoryError("content must be a string")
    entry = ScratchpadEntry(
        user_id=user_id,
        workspace_id=services.workspace_id,
        updated_at=_now_iso(),
        content=content,
        active_projects=list(active_projects or []),
        current_focus=current_focus,
        open_questions=list(open_questions or []),
        next_steps=list(next_steps or []),
    )
    with services.database.connect() as conn:
        upsert_scratchpad(conn, entry)
    return {"scratchpad": scratchpad_payload_from_entry(entry)}
