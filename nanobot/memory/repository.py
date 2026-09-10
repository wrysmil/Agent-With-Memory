"""SQLite CRUD 操作：memories / episodes / scratchpad。

Public API surface (all take a sqlite3.Connection as first arg):
    memories:
        add_memory, get_memory, list_memories, search_memories,
        update_memory, delete_memory
    episodes:
        add_episode, get_episode, list_episodes, list_episodes_by_session,
        update_episode, delete_episode
    scratchpad:
        upsert_scratchpad, get_scratchpad
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Literal

from nanobot.memory.models import (
    Episode,
    EpisodeOutcome,
    EpisodeSource,
    Memory,
    MemoryPriority,
    MemoryType,
    ScratchpadEntry,
)


# ---------- memories ----------

_INSERT_MEMORY_SQL = """
    INSERT INTO memories (
        id, content, type, priority, source,
        importance_score, access_count, tags, subject, predicate,
        confidence, decay_rate, expires_at, last_accessed_at,
        superseded_by, source_episode_id,
        scope, scope_owner, agent_id, user_id, workspace_id, metadata,
        created_at, updated_at
    ) VALUES (
        :id, :content, :type, :priority, :source,
        :importance_score, :access_count, :tags, :subject, :predicate,
        :confidence, :decay_rate, :expires_at, :last_accessed_at,
        :superseded_by, :source_episode_id,
        :scope, :scope_owner, :agent_id, :user_id, :workspace_id, :metadata,
        :created_at, :updated_at
    )
"""


def add_memory(conn: sqlite3.Connection, memory: Memory) -> None:
    """插入一条 memory 行。"""
    conn.execute(_INSERT_MEMORY_SQL, memory.to_row())


_SELECT_MEMORY_COLUMNS = """
    id, content, type, priority, source,
    importance_score, access_count, tags, subject, predicate,
    confidence, decay_rate, expires_at, last_accessed_at,
    superseded_by, source_episode_id,
    scope, scope_owner, agent_id, user_id, workspace_id, metadata,
    created_at, updated_at
"""


def _row_to_memory(row: sqlite3.Row) -> Memory:
    return Memory(
        id=row['id'],
        content=row['content'],
        created_at=row['created_at'],
        updated_at=row['updated_at'],
        type=MemoryType(row['type']),
        priority=MemoryPriority(row['priority']),
        source=row['source'],
        importance_score=row['importance_score'],
        access_count=row['access_count'],
        tags=json.loads(row['tags']) if row['tags'] else [],
        subject=row['subject'],
        predicate=row['predicate'],
        confidence=row['confidence'],
        decay_rate=row['decay_rate'],
        expires_at=row['expires_at'],
        last_accessed_at=row['last_accessed_at'],
        superseded_by=row['superseded_by'],
        source_episode_id=row['source_episode_id'],
        scope=row['scope'],
        scope_owner=row['scope_owner'],
        agent_id=row['agent_id'],
        user_id=row['user_id'],
        workspace_id=row['workspace_id'],
        metadata=json.loads(row['metadata']) if row['metadata'] else {},
    )


def get_memory(conn: sqlite3.Connection, memory_id: str) -> Memory | None:
    flat = " ".join(_SELECT_MEMORY_COLUMNS.split())
    row = conn.execute(
        f"SELECT {flat} FROM memories WHERE id = ?",
        (memory_id,),
    ).fetchone()
    return _row_to_memory(row) if row else None


def list_memories(
    conn: sqlite3.Connection,
    *,
    type: MemoryType | None = None,
    workspace_id: str | None = None,
    order_by: Literal["created", "importance"] = "created",
    limit: int | None = None,
) -> list[Memory]:
    clauses = []
    params: list[Any] = []
    if type is not None:
        clauses.append("type = ?")
        params.append(type.value)
    if workspace_id is not None:
        clauses.append("workspace_id = ?")
        params.append(workspace_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    order_sql = {
        "created": "created_at DESC",
        "importance": "importance_score DESC",
    }[order_by]
    limit_sql = f" LIMIT {int(limit)}" if limit is not None else ""
    flat = " ".join(_SELECT_MEMORY_COLUMNS.split())
    sql = (
        f"SELECT {flat} FROM memories "
        f"{where} ORDER BY {order_sql}{limit_sql}"
    )
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_memory(r) for r in rows]


def delete_memory(conn: sqlite3.Connection, memory_id: str) -> None:
    conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))


def update_memory_source_episode(
    conn: sqlite3.Connection,
    memory_id: str,
    episode_id: str,
) -> None:
    """反向回填：将 ``Memory.source_episode_id`` 设置为本次 episode 的 id。

    FIX-6 Rev-M-4：阶段 4a 先写 memory 再写 episode，episode 才知道 linked_memory_ids；
    反向回填在 episode 写入成功后逐条把 source_episode_id 写回对应 memory，
    让任一 memory 行都能反查来源 episode。无行被更新（如 memory_id 不存在）时
    ``rowcount == 0``，调用方负责记录 / 忽略。
    """
    cur = conn.execute(
        "UPDATE memories SET source_episode_id = ? WHERE id = ?",
        (episode_id, memory_id),
    )
    if cur.rowcount == 0:
        raise KeyError(f"memory not found: {memory_id}")


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def update_memory(
    conn: sqlite3.Connection,
    memory_id: str,
    *,
    content: str | None = None,
    type: MemoryType | None = None,
    priority: MemoryPriority | None = None,
    importance_score: float | None = None,
    tags: list[str] | None = None,
    subject: str | None = None,
    predicate: str | None = None,
    confidence: float | None = None,
    decay_rate: float | None = None,
    expires_at: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Partially update a memory row by id.

    FTS5 triggers keep ``memories_fts`` in sync. Raises ``KeyError`` if the
    row does not exist.
    """
    sets: list[str] = []
    params: list[Any] = []
    now = _now_iso()
    field_map: dict[str, Any] = {
        "content": content,
        "type": type.value if isinstance(type, MemoryType) else type,
        "priority": priority.value if isinstance(priority, MemoryPriority) else priority,
        "importance_score": importance_score,
        "tags": json.dumps(tags, ensure_ascii=False) if tags is not None else None,
        "subject": subject,
        "predicate": predicate,
        "confidence": confidence,
        "decay_rate": decay_rate,
        "expires_at": expires_at,
        "metadata": json.dumps(metadata, ensure_ascii=False) if metadata is not None else None,
    }
    for column, value in field_map.items():
        if value is None:
            continue
        sets.append(f"{column} = ?")
        params.append(value)
    sets.append("updated_at = ?")
    params.append(now)
    params.append(memory_id)
    cur = conn.execute(
        f"UPDATE memories SET {', '.join(sets)} WHERE id = ?",
        params,
    )
    if cur.rowcount == 0:
        raise KeyError(f"memory not found: {memory_id}")


# ---------- episodes ----------

_INSERT_EPISODE_SQL = """
    INSERT INTO episodes (
        id, session_id, summary, goal, outcome, source,
        started_at, ended_at,
        action_nodes, entities, tools_used, linked_memory_ids, tags,
        importance_score, access_count,
        compaction_checkpoint_id, workspace_snapshot_id
    ) VALUES (
        :id, :session_id, :summary, :goal, :outcome, :source,
        :started_at, :ended_at,
        :action_nodes, :entities, :tools_used, :linked_memory_ids, :tags,
        :importance_score, :access_count,
        :compaction_checkpoint_id, :workspace_snapshot_id
    )
"""


def add_episode(conn: sqlite3.Connection, episode: Episode) -> None:
    conn.execute(_INSERT_EPISODE_SQL, episode.to_row())


_SELECT_EPISODE_COLUMNS = """
    id, session_id, summary, goal, outcome, source,
    started_at, ended_at,
    action_nodes, entities, tools_used, linked_memory_ids, tags,
    importance_score, access_count,
    compaction_checkpoint_id, workspace_snapshot_id
"""


def _row_to_episode(row: sqlite3.Row) -> Episode:
    return Episode(
        id=row['id'],
        session_id=row['session_id'],
        summary=row['summary'],
        started_at=row['started_at'],
        ended_at=row['ended_at'],
        goal=row['goal'],
        outcome=EpisodeOutcome(row['outcome']),
        source=EpisodeSource(row['source']),
        action_nodes=json.loads(row['action_nodes']) if row['action_nodes'] else [],
        entities=json.loads(row['entities']) if row['entities'] else [],
        tools_used=json.loads(row['tools_used']) if row['tools_used'] else [],
        linked_memory_ids=json.loads(row['linked_memory_ids']) if row['linked_memory_ids'] else [],
        tags=json.loads(row['tags']) if row['tags'] else [],
        importance_score=row['importance_score'],
        access_count=row['access_count'],
        compaction_checkpoint_id=row['compaction_checkpoint_id'],
        workspace_snapshot_id=row['workspace_snapshot_id'],
    )


def get_episode(conn: sqlite3.Connection, episode_id: str) -> Episode | None:
    flat = " ".join(_SELECT_EPISODE_COLUMNS.split())
    row = conn.execute(
        f"SELECT {flat} FROM episodes WHERE id = ?",
        (episode_id,),
    ).fetchone()
    return _row_to_episode(row) if row else None


def list_episodes_by_session(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    limit: int | None = None,
) -> list[Episode]:
    flat = " ".join(_SELECT_EPISODE_COLUMNS.split())
    limit_sql = f" LIMIT {int(limit)}" if limit is not None else ""
    sql = (
        f"SELECT {flat} FROM episodes "
        f"WHERE session_id = ? "
        f"ORDER BY started_at DESC{limit_sql}"
    )
    rows = conn.execute(sql, (session_id,)).fetchall()
    return [_row_to_episode(r) for r in rows]


def list_episodes(
    conn: sqlite3.Connection,
    *,
    session_id: str | None = None,
    limit: int | None = None,
) -> list[Episode]:
    """List episodes, most recent first. Optionally filter by session_id."""
    flat = " ".join(_SELECT_EPISODE_COLUMNS.split())
    clauses: list[str] = []
    params: list[Any] = []
    if session_id is not None:
        clauses.append("session_id = ?")
        params.append(session_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    limit_sql = f" LIMIT {int(limit)}" if limit is not None else ""
    sql = (
        f"SELECT {flat} FROM episodes {where} "
        f"ORDER BY started_at DESC{limit_sql}"
    )
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_episode(r) for r in rows]


def update_episode(
    conn: sqlite3.Connection,
    episode_id: str,
    *,
    summary: str | None = None,
    goal: str | None = None,
    outcome: EpisodeOutcome | None = None,
    tags: list[str] | None = None,
    importance_score: float | None = None,
) -> None:
    """Partially update an episode row by id. Raises KeyError if missing."""
    sets: list[str] = []
    params: list[Any] = []
    field_map: dict[str, Any] = {
        "summary": summary,
        "goal": goal,
        "outcome": outcome.value if isinstance(outcome, EpisodeOutcome) else outcome,
        "tags": json.dumps(tags, ensure_ascii=False) if tags is not None else None,
        "importance_score": importance_score,
    }
    for column, value in field_map.items():
        if value is None:
            continue
        sets.append(f"{column} = ?")
        params.append(value)
    if not sets:
        return
    params.append(episode_id)
    cur = conn.execute(
        f"UPDATE episodes SET {', '.join(sets)} WHERE id = ?",
        params,
    )
    if cur.rowcount == 0:
        raise KeyError(f"episode not found: {episode_id}")


def delete_episode(conn: sqlite3.Connection, episode_id: str) -> None:
    cur = conn.execute("DELETE FROM episodes WHERE id = ?", (episode_id,))
    if cur.rowcount == 0:
        raise KeyError(f"episode not found: {episode_id}")


# ---------- scratchpad ----------

_UPSERT_SCRATCHPAD_SQL = """
    INSERT INTO scratchpad (
        user_id, workspace_id, content,
        active_projects, current_focus, open_questions, next_steps,
        updated_at
    ) VALUES (
        :user_id, :workspace_id, :content,
        :active_projects, :current_focus, :open_questions, :next_steps,
        :updated_at
    )
    ON CONFLICT (user_id, workspace_id) DO UPDATE SET
        content = excluded.content,
        active_projects = excluded.active_projects,
        current_focus = excluded.current_focus,
        open_questions = excluded.open_questions,
        next_steps = excluded.next_steps,
        updated_at = excluded.updated_at
"""


def upsert_scratchpad(conn: sqlite3.Connection, entry: ScratchpadEntry) -> None:
    conn.execute(_UPSERT_SCRATCHPAD_SQL, entry.to_row())


def get_scratchpad(
    conn: sqlite3.Connection,
    user_id: str,
    workspace_id: str,
) -> ScratchpadEntry | None:
    row = conn.execute(
        "SELECT * FROM scratchpad WHERE user_id = ? AND workspace_id = ?",
        (user_id, workspace_id),
    ).fetchone()
    if row is None:
        return None
    return ScratchpadEntry(
        user_id=row['user_id'],
        workspace_id=row['workspace_id'],
        updated_at=row['updated_at'],
        content=row['content'],
        active_projects=json.loads(row['active_projects']) if row['active_projects'] else [],
        current_focus=row['current_focus'],
        open_questions=json.loads(row['open_questions']) if row['open_questions'] else [],
        next_steps=json.loads(row['next_steps']) if row['next_steps'] else [],
    )


# ---------- search ----------

def search_memories(
    conn: sqlite3.Connection,
    query: str,
    *,
    type: MemoryType | None = None,
    workspace_id: str | None = None,
    limit: int | None = None,
) -> list[Memory]:
    """FTS5 全文检索 memories。

    检索字段: content / subject / predicate / tags。
    排序: BM25 rank（FTS5 内置）；相同 rank 时按 importance_score 降序。
    """
    clauses = ["memories_fts MATCH ?"]
    params: list[Any] = [query]
    if type is not None:
        clauses.append("m.type = ?")
        params.append(type.value)
    if workspace_id is not None:
        clauses.append("m.workspace_id = ?")
        params.append(workspace_id)
    where = " AND ".join(clauses)
    limit_sql = f" LIMIT {int(limit)}" if limit is not None else ""
    # Prefix all memories columns with m. to avoid ambiguity with memories_fts.content
    _SELECT_MEMORY_COLUMNS_PREFIXED = ", ".join(
        f"m.{col.strip()}" for col in _SELECT_MEMORY_COLUMNS.split(",")
    )
    sql = (
        f"SELECT {_SELECT_MEMORY_COLUMNS_PREFIXED} "
        f"FROM memories m JOIN memories_fts f ON f.rowid = m.rowid "
        f"WHERE {where} "
        f"ORDER BY rank, m.importance_score DESC{limit_sql}"
    )
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_memory(r) for r in rows]
