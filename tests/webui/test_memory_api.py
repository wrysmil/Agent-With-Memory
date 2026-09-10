"""Tests for payload conversion and pure action functions in nanobot.webui.memory_api."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import (
    Episode,
    EpisodeOutcome,
    EpisodeSource,
    Memory,
    MemoryPriority,
    MemoryType,
    ScratchpadEntry,
)
from nanobot.memory.repository import (
    add_episode,
    add_memory,
)
from nanobot.webui.memory_api import (
    MemoryServices,
    WebUIMemoryError,
    create_memory,
    delete_episode,
    delete_memory,
    episode_payload,
    list_episodes_payload,
    list_memories_payload,
    memory_payload,
    save_scratchpad,
    scratchpad_payload,
    scratchpad_payload_from_entry,
    search_memories_payload,
    stats_payload,
    update_episode,
    update_memory,
)


@pytest.fixture
def services(tmp_path: Path):
    """Real MemoryDatabase on a tmp workspace."""
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return MemoryServices(workspace_id="default", database=database)


def _mem(content: str = "用户偏好 Python", **kw) -> Memory:
    base = dict(
        id=str(uuid.uuid4()),
        content=content,
        created_at="2026-09-08T10:00:00+00:00",
        updated_at="2026-09-08T10:00:00+00:00",
        type=MemoryType.PREFERENCE,
        priority=MemoryPriority.LONG_TERM,
        importance_score=0.85,
        tags=["python", "编程"],
        subject="用户",
        predicate="偏好",
        workspace_id="default",
    )
    base.update(kw)
    return Memory(**base)


def _ep(**kw) -> Episode:
    base = dict(
        id=str(uuid.uuid4()),
        session_id="telegram:chat-1",
        summary="配置 uv",
        started_at="2026-09-08T10:00:00+00:00",
        ended_at="2026-09-08T10:30:00+00:00",
        goal="让 Agent 用 uv 管理依赖",
        outcome=EpisodeOutcome.COMPLETED,
        source=EpisodeSource.SESSION_END,
        action_nodes=[{"tool": "run_command", "input": "irm", "success": True}],
        entities=["uv"],
        tools_used=["run_command", "read_file"],
        linked_memory_ids=[],
        tags=["uv", "win11"],
        importance_score=0.7,
    )
    base.update(kw)
    return Episode(**base)


# ---- payload converters ----------------------------------------------------


def test_memory_payload_round_trip():
    m = _mem()
    payload = memory_payload(m)
    assert payload["id"] == m.id
    assert payload["content"] == m.content
    assert payload["type"] == "preference"
    assert payload["priority"] == "long_term"
    assert payload["importance_score"] == 0.85
    assert payload["tags"] == ["python", "编程"]
    assert payload["subject"] == "用户"
    assert payload["predicate"] == "偏好"
    assert payload["workspace_id"] == "default"
    # JSON-serialisable
    json.dumps(payload)


def test_episode_payload_round_trip():
    ep = _ep()
    payload = episode_payload(ep)
    assert payload["id"] == ep.id
    assert payload["outcome"] == "completed"
    assert payload["action_nodes"] == ep.action_nodes
    assert payload["entities"] == ["uv"]
    assert payload["tools_used"] == ["run_command", "read_file"]
    json.dumps(payload)


def test_scratchpad_payload_round_trip():
    s = ScratchpadEntry(
        user_id="default",
        workspace_id="default",
        updated_at="2026-09-08T10:30:00+00:00",
        content="## 当前项目",
        active_projects=["uv-memory"],
        current_focus="uv 在 Windows 11",
        open_questions=["枚举对齐"],
        next_steps=["验证 pytest"],
    )
    payload = scratchpad_payload_from_entry(s)
    assert payload["active_projects"] == ["uv-memory"]
    json.dumps(payload)


# ---- list / search -----------------------------------------------------------


def test_list_memories_payload(services: MemoryServices):
    with services.database.connect() as conn:
        add_memory(conn, _mem(content="记忆 A"))
        add_memory(conn, _mem(content="记忆 B"))

    payload = list_memories_payload(services, type="preference", order="importance", limit=10)
    assert len(payload["items"]) == 2
    # Order: importance desc → A (default 0.85), B (default 0.85) — tie-broken by created DESC
    for item in payload["items"]:
        assert item["type"] == "preference"


def test_search_memories_payload(services: MemoryServices):
    with services.database.connect() as conn:
        add_memory(conn, _mem(content="Python 编程经验", tags=["经验"]))
        add_memory(conn, _mem(content="Rust 编程经验", tags=["经验"]))

    payload = search_memories_payload(services, "Python")
    assert len(payload["items"]) == 1
    assert payload["query"] == "Python"


def test_list_episodes_payload(services: MemoryServices):
    with services.database.connect() as conn:
        add_episode(conn, _ep(session_id="telegram:chat-1"))
        add_episode(conn, _ep(session_id="cli:local-2"))

    payload = list_episodes_payload(services)
    assert len(payload["items"]) == 2


def test_list_episodes_filtered_by_session(services: MemoryServices):
    with services.database.connect() as conn:
        add_episode(conn, _ep(session_id="telegram:chat-1"))
        add_episode(conn, _ep(session_id="cli:local-2"))

    payload = list_episodes_payload(services, session_id="telegram:chat-1")
    assert len(payload["items"]) == 1


def test_scratchpad_payload_returns_none_when_empty(services: MemoryServices):
    payload = scratchpad_payload(services)
    assert payload["scratchpad"] is None


def test_stats_payload_counts_by_type(services: MemoryServices):
    with services.database.connect() as conn:
        add_memory(conn, _mem(content="A", type=MemoryType.FACT))
        add_memory(conn, _mem(content="B", type=MemoryType.FACT))
        add_memory(conn, _mem(content="C", type=MemoryType.PREFERENCE))

    payload = stats_payload(services)
    assert payload["total"] == 3
    assert payload["by_type"]["fact"] == 2
    assert payload["by_type"]["preference"] == 1


# ---- mutations --------------------------------------------------------------


def test_create_memory_round_trips(services: MemoryServices):
    payload = create_memory(
        services,
        content="新记忆",
        type="rule",
        priority="long_term",
        importance_score=0.6,
        tags=["rule"],
        subject="系统",
        predicate="要求",
    )
    assert payload["memory"]["content"] == "新记忆"
    assert payload["memory"]["type"] == "rule"
    assert payload["memory"]["source"] == "manual"  # hard-coded default

    # And it is in the DB
    listed = list_memories_payload(services)
    assert any(m["content"] == "新记忆" for m in listed["items"])


def test_create_memory_rejects_empty_content(services: MemoryServices):
    with pytest.raises(WebUIMemoryError) as exc:
        create_memory(services, content="", type="fact")
    assert exc.value.status == 400


def test_update_memory_only_changes_given_fields(services: MemoryServices):
    created = create_memory(services, content="原", type="fact")
    mid = created["memory"]["id"]

    updated = update_memory(services, mid, content="改后")
    assert updated["memory"]["content"] == "改后"
    assert updated["memory"]["type"] == "fact"  # untouched


def test_update_memory_rejects_unknown_id(services: MemoryServices):
    with pytest.raises(WebUIMemoryError) as exc:
        update_memory(services, "no-such", content="x")
    assert exc.value.status == 404


def test_delete_memory(services: MemoryServices):
    created = create_memory(services, content="a", type="fact")
    mid = created["memory"]["id"]
    delete_memory(services, mid)
    listed = list_memories_payload(services)
    assert all(m["id"] != mid for m in listed["items"])


def test_delete_episode(services: MemoryServices):
    with services.database.connect() as conn:
        ep = _ep()
        add_episode(conn, ep)
    payload = delete_episode(services, ep.id)
    assert payload["ok"] is True
    listed = list_episodes_payload(services)
    assert all(e["id"] != ep.id for e in listed["items"])


def test_update_episode(services: MemoryServices):
    with services.database.connect() as conn:
        ep = _ep()
        add_episode(conn, ep)
    payload = update_episode(services, ep.id, summary="新摘要", tags=["a", "b"])
    assert payload["episode"]["summary"] == "新摘要"
    assert payload["episode"]["tags"] == ["a", "b"]


def test_save_scratchpad_round_trip(services: MemoryServices):
    payload = save_scratchpad(
        services,
        user_id="default",
        content="## 当前项目",
        active_projects=["x"],
        current_focus="y",
        open_questions=["z"],
        next_steps=["w"],
    )
    assert payload["scratchpad"]["active_projects"] == ["x"]

    # Read back
    read = scratchpad_payload(services, user_id="default")
    assert read["scratchpad"]["content"] == "## 当前项目"


def test_save_scratchpad_upserts(services: MemoryServices):
    save_scratchpad(services, user_id="default", content="first")
    save_scratchpad(services, user_id="default", content="second")
    # Only one row
    with services.database.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM scratchpad WHERE user_id='default'"
        ).fetchone()
    assert row["c"] == 1
