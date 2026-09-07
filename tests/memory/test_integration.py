"""端到端集成测试：模拟完整生命周期。"""

from datetime import datetime, timezone

from nanobot.memory import (
    Episode,
    EpisodeOutcome,
    Memory,
    MemoryDatabase,
    MemoryType,
    ScratchpadEntry,
    add_episode,
    add_memory,
    get_episode,
    get_memory,
    get_scratchpad,
    list_episodes_by_session,
    search_memories,
    upsert_scratchpad,
)


def _now():
    return datetime.now(timezone.utc).isoformat()


def test_full_lifecycle(tmp_path):
    db = MemoryDatabase(tmp_path)
    db.ensure_schema()

    now = _now()

    # 1. 三层记忆独立写入
    with db.connect() as conn:
        # 语义记忆：用户偏好
        add_memory(conn, Memory(
            id="pref-py", content="用户偏好 Python 而非 Ruby",
            type=MemoryType.PREFERENCE,
            subject="用户", predicate="偏好",
            tags=["编程", "Python"],
            importance_score=0.8,
            created_at=now, updated_at=now,
        ))
        # 语义记忆：错误教训
        add_memory(conn, Memory(
            id="err-deps", content="pytest 失败通常因缺少依赖",
            type=MemoryType.ERROR,
            subject="pytest", predicate="失败",
            tags=["测试"],
            importance_score=0.6,
            created_at=now, updated_at=now,
        ))
        # 情节记忆：某次任务
        add_episode(conn, Episode(
            id="ep-001", session_id="telegram:chat-1",
            summary="修复 pytest 失败", goal="让 CI 通过",
            outcome=EpisodeOutcome.COMPLETED,
            tools_used=["read_file", "python"],
            entities=["tests/test_x.py"],
            started_at=now, ended_at=now,
        ))
        # 工作记忆
        upsert_scratchpad(conn, ScratchpadEntry(
            user_id="default", workspace_id="ws1", updated_at=now,
            content="当前在做三层记忆系统实施",
            active_projects=["nanobot-memory"],
            current_focus="实现 SQLite schema",
            open_questions=["FTS5 中文分词效果?"],
            next_steps=["接入 Consolidator", "接入 Dream 导出"],
        ))

    # 2. 三层独立检索
    with db.connect() as conn:
        # 语义检索：按关键词
        mem_hits = search_memories(conn, "Python")
        mem_ids = {m.id for m in mem_hits}
        assert "pref-py" in mem_ids

        # 语义检索：按类型
        prefs = [m for m in search_memories(conn, "Python")
                 if m.type == MemoryType.PREFERENCE]
        assert any(m.id == "pref-py" for m in prefs)

        # 情节检索：按 session
        eps = list_episodes_by_session(conn, "telegram:chat-1")
        assert [e.id for e in eps] == ["ep-001"]

        # 工作记忆：直接取
        sp = get_scratchpad(conn, "default", "ws1")
        assert sp.active_projects == ["nanobot-memory"]
        assert sp.current_focus == "实现 SQLite schema"

    # 3. 关闭并重启 DB,验证持久化
    db2 = MemoryDatabase(tmp_path, db_path=db.db_path)
    db2.ensure_schema()
    with db2.connect() as conn:
        m = get_memory(conn, "pref-py")
        assert m is not None
        assert m.content == "用户偏好 Python 而非 Ruby"

    # 4. 工作记忆的 UPSERT 语义（重启后再次更新）
    with db2.connect() as conn:
        upsert_scratchpad(conn, ScratchpadEntry(
            user_id="default", workspace_id="ws1", updated_at=now,
            current_focus="完成 Task 10",
        ))
        sp = get_scratchpad(conn, "default", "ws1")
        assert sp.current_focus == "完成 Task 10"
        # active_projects 被覆盖为空（UPSERT 语义）
        assert sp.active_projects == []


def test_idempotent_init(tmp_path):
    """多次 init_schema 必须幂等。"""
    db = MemoryDatabase(tmp_path)
    for _ in range(5):
        db.init_schema()
    # 多次连接 + 写入 + 读取,验证表结构稳定
    now = _now()
    with db.connect() as conn:
        add_memory(conn, Memory(
            id="x1", content="test", created_at=now, updated_at=now,
        ))
        m = get_memory(conn, "x1")
    assert m.content == "test"


def test_episode_roundtrip(tmp_path):
    """验证 Episode 数据类型的完整往返。"""
    db = MemoryDatabase(tmp_path)
    db.ensure_schema()
    now = _now()

    ep = Episode(
        id="ep-round", session_id="session-1",
        summary="完成了测试用例编写",
        goal="覆盖 CRUD",
        outcome=EpisodeOutcome.COMPLETED,
        tools_used=["python", "pytest"],
        entities=["test_*.py"],
        action_nodes=[{"tool": "write", "file": "test_x.py"}],
        started_at=now, ended_at=now,
    )

    with db.connect() as conn:
        add_episode(conn, ep)
        retrieved = get_episode(conn, "ep-round")

    assert retrieved.id == "ep-round"
    assert retrieved.summary == "完成了测试用例编写"
    assert retrieved.tools_used == ["python", "pytest"]
    assert retrieved.action_nodes[0]["tool"] == "write"
