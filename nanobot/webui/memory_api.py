"""Pure payload conversion + CRUD action functions for the WebUI memory domain.

This module owns the JSON wire format for ``Memory`` / ``Episode`` /
``ScratchpadEntry`` dataclasses and the pure CRUD entry points consumed by
``memory_routes.py``. It deliberately does NOT depend on HTTP/WebSocket
machinery; transport lives elsewhere.
"""

from __future__ import annotations

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
from nanobot.webui.memory_services import MemoryServices

_DEFAULT_USER_ID = "default"
_VALID_MEMORY_TYPES = {t.value for t in MemoryType}
_VALID_MEMORY_PRIORITIES = {p.value for p in MemoryPriority}
_VALID_OUTCOMES = {o.value for o in EpisodeOutcome}


class WebUIMemoryError(Exception):
    """User-facing memory validation failure with explicit HTTP status."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


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


def stats_payload(services: MemoryServices) -> dict[str, Any]:
    """Aggregate counts by memory type for the current workspace."""
    counters: dict[str, int] = {t.value: 0 for t in MemoryType}
    total = 0
    with services.database.connect() as conn:
        rows = list_memories(conn, workspace_id=services.workspace_id)
    for r in rows:
        counters[r.type.value] = counters.get(r.type.value, 0) + 1
        total += 1
    return {"total": total, "by_type": counters}


# ---- write actions ----------------------------------------------------------


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
    return {"memory": memory_payload(memory)}


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
    return fetch_memory_payload(services, memory_id)


def delete_memory(services: MemoryServices, memory_id: str) -> dict[str, Any]:
    with services.database.connect() as conn:
        cur = conn.execute("SELECT 1 FROM memories WHERE id = ?", (memory_id,))
        if cur.fetchone() is None:
            raise WebUIMemoryError("memory not found", status=404)
        _repo_delete_memory(conn, memory_id)
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
