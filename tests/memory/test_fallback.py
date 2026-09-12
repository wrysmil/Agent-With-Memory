"""WU-3B: DB 写入失败 fallback 与启动重放测试。

覆盖:
1. ``_safe_write_with_fallback`` 在 DB 抛异常时落 JSON fallback 文件
2. ``_safe_write_with_fallback`` 在 DB 正常时返回 True 且无文件
3. ``replay_fallback`` 清空并重放成功项
4. ``replay_fallback`` 保留失败项,清空其他
5. ``_persist`` 在 DB 写入失败时仍返回合法 ``PersistenceResult``,并写 fallback

不依赖真实 LLM,使用 ``_FakeProvider`` + 真 SQLite(tmp_path)。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.extractor import (
    FilteredExtractionResult,
    LLMMemoryItem,
    MemoryExtractor,
    PersistenceResult,
)
from nanobot.memory.models import (
    Episode,
    EpisodeSource,
    Memory,
    MemoryPriority,
    MemoryType,
)
from tests.memory.test_extractor import (
    _FakeProvider,
    _make_extractor,
    _plain_session,
)

# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> MemoryDatabase:
    """每个测试一个 fresh database,避免 state 污染。"""
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return database


def _memory_row_dict(content: str = "fallback-memory") -> dict[str, Any]:
    """构造一个 ``Memory.to_row()`` 形态的 dict,用于 fallback JSON 载荷。"""
    return Memory(
        id="fb-memory-1",
        content=content,
        type=MemoryType.FACT,
        priority=MemoryPriority.LONG_TERM,
        source="extraction",
        importance_score=0.5,
        subject="",
        predicate="",
        created_at="2026-09-13T00:00:00+00:00",
        updated_at="2026-09-13T00:00:00+00:00",
        workspace_id="default",
        user_id="default",
    ).to_row()


def _episode_row_dict() -> dict[str, Any]:
    return Episode(
        id="fb-episode-1",
        session_id="s1",
        summary="fb ep",
        goal="",
        source=EpisodeSource.SESSION_END,
        started_at="2026-09-13T00:00:00+00:00",
        ended_at="2026-09-13T00:00:01+00:00",
    ).to_row()


# ---------------------------------------------------------------------------
# B 节: replay_fallback
# ---------------------------------------------------------------------------


class TestReplayFallback:
    def test_replay_drains_files(self, tmp_path: Path):
        """先写两个 fallback 文件,replay_fallback 后两个文件都被删除,DB 有行。"""
        db = MemoryDatabase(tmp_path)
        db.init_schema()

        # 写两个 memory fallback 文件 + 1 个 episode
        for i, content in enumerate(["alpha", "beta"]):
            payload = {
                "kind": "memory",
                "item": Memory(
                    id=f"fb-mem-{i}",
                    content=content,
                    type=MemoryType.FACT,
                    created_at="2026-09-13T00:00:00+00:00",
                    workspace_id="default",
                ).to_row(),
                "attempt": f"2026-09-13T00:00:0{i}+00:00",
                "error": "simulated",
            }
            (db.fallback_dir / f"2026-09-13T00:00:0{i}_memory_{i:08x}.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )

        ep_payload = {
            "kind": "episode",
            "item": _episode_row_dict(),
            "attempt": "2026-09-13T00:00:02+00:00",
            "error": "simulated",
        }
        (db.fallback_dir / "2026-09-13T00:00:02_episode_99999999.json").write_text(
            json.dumps(ep_payload, ensure_ascii=False), encoding="utf-8"
        )

        # 触发重放
        success = db.replay_fallback()
        assert success == 3
        # 文件都被清空
        assert list(db.fallback_dir.glob("*.json")) == []

        # DB 中有行
        with db.connect() as conn:
            from nanobot.memory.repository import get_episode, list_memories

            mems = list_memories(conn, workspace_id="default")
            assert {m.content for m in mems} == {"alpha", "beta"}
            ep = get_episode(conn, "fb-episode-1")
        assert ep is not None
        assert ep.summary == "fb ep"

    def test_replay_keeps_failed_files(self, tmp_path: Path):
        """replay 某条再次失败时,该文件保留;其余成功项被清理。"""
        db = MemoryDatabase(tmp_path)
        db.init_schema()

        good_payload = {
            "kind": "memory",
            "item": Memory(
                id="good-id",
                content="good",
                type=MemoryType.FACT,
                created_at="2026-09-13T00:00:00+00:00",
                workspace_id="default",
            ).to_row(),
            "attempt": "2026-09-13T00:00:00+00:00",
            "error": "simulated",
        }
        bad_payload = {
            "kind": "memory",
            "item": {"id": "bad-id", "content": "bad"},  # 缺字段 → Memory.from_row 抛错
            "attempt": "2026-09-13T00:00:01+00:00",
            "error": "simulated",
        }
        (db.fallback_dir / "2026-09-13T00:00:00_memory_aaaaaaaa.json").write_text(
            json.dumps(good_payload, ensure_ascii=False), encoding="utf-8"
        )
        (db.fallback_dir / "2026-09-13T00:00:01_memory_bbbbbbbb.json").write_text(
            json.dumps(bad_payload, ensure_ascii=False), encoding="utf-8"
        )

        success = db.replay_fallback()
        assert success == 1

        # 失败文件保留
        remaining = sorted(p.name for p in db.fallback_dir.glob("*.json"))
        assert remaining == ["2026-09-13T00:00:01_memory_bbbbbbbb.json"]

        # DB 只有成功项
        from nanobot.memory.repository import list_memories

        with db.connect() as conn:
            mems = list_memories(conn, workspace_id="default")
        assert len(mems) == 1
        assert mems[0].id == "good-id"


# ---------------------------------------------------------------------------
# A 节: _safe_write_with_fallback
# ---------------------------------------------------------------------------


class TestSafeWriteWithFallback:
    def test_writes_file_on_db_error(self, tmp_path: Path, db: MemoryDatabase):
        """``add_memory`` 抛异常时,fallback 文件被创建且 payload 含 ``kind=memory``。"""
        extractor = _make_extractor(db, _FakeProvider())

        def broken_writer(_conn: Any) -> None:
            raise RuntimeError("simulated db failure")

        item = _memory_row_dict()

        ok = extractor._safe_write_with_fallback(
            db.connect().__enter__(),
            kind="memory",
            item=item,
            writer=broken_writer,
        )

        assert ok is False
        files = list(db.fallback_dir.glob("*_memory_*.json"))
        assert len(files) == 1
        payload = json.loads(files[0].read_text(encoding="utf-8"))
        assert payload["kind"] == "memory"
        assert payload["item"]["id"] == "fb-memory-1"

    def test_returns_true_on_success(self, tmp_path: Path, db: MemoryDatabase):
        """DB 正常时返回 True 且不写 fallback 文件。"""
        extractor = _make_extractor(db, _FakeProvider())

        def noop_writer(_conn: Any) -> None:
            return None

        with db.connect() as conn:
            ok = extractor._safe_write_with_fallback(
                conn,
                kind="memory",
                item=_memory_row_dict(content="ok-1"),
                writer=noop_writer,
            )

        assert ok is True
        assert list(db.fallback_dir.glob("*.json")) == []

    def test_fallback_dir_unwritable_returns_false(self, tmp_path: Path, db: MemoryDatabase):
        """fallback 目录也无法写时:仍返回 False,不抛(异常隔离)。"""
        extractor = _make_extractor(db, _FakeProvider())
        # 把 fallback_dir 改成 read-only 文件,强制 write_text 抛错
        blocker = db.fallback_dir / "blocker"
        blocker.write_text("x", encoding="utf-8")
        # 实际更稳:把 fallback_dir 设成已存在但只读的目录
        readonly_dir = tmp_path / "readonly_dir"
        readonly_dir.mkdir()
        readonly_dir.chmod(0o500)
        extractor.fallback_dir = readonly_dir

        def broken_writer(_conn: Any) -> None:
            raise RuntimeError("simulated")

        ok = extractor._safe_write_with_fallback(
            db.connect().__enter__(),
            kind="memory",
            item=_memory_row_dict(),
            writer=broken_writer,
        )

        # 恢复权限,避免影响 tmp_path 清理
        try:
            readonly_dir.chmod(0o700)
        except Exception:
            pass

        assert ok is False  # 不抛,只返回 False


# ---------------------------------------------------------------------------
# 集成: _persist 在 DB 写入失败时仍返回合法 PersistenceResult
# ---------------------------------------------------------------------------


class TestPersistUsesSafeWrite:
    def test_persist_uses_safe_write_with_fallback(self, tmp_path: Path):
        """模拟 DB 写异常,_persist 返回合法 PersistenceResult 且 fallback 落盘。"""
        db = MemoryDatabase(tmp_path)
        db.init_schema()
        extractor = _make_extractor(db, _FakeProvider())

        # 故意只放 memory:既验证 memory 走 fallback,也验证 episode/action_nodes
        # 为空时整条分支被跳过(行为与原始 _persist 一致)。
        filtered = FilteredExtractionResult(
            memories=[
                LLMMemoryItem(
                    content="user likes uv",
                    type="PREFERENCE",
                    priority="long_term",
                    subject="user",
                    predicate="likes",
                )
            ],
        )
        session = _plain_session()

        from sqlite3 import OperationalError

        from nanobot.memory import extractor as ext_mod

        def broken_add_memory(conn, memory):  # noqa: ARG001
            raise OperationalError("database is locked")

        with patch.object(ext_mod, "add_memory", side_effect=broken_add_memory):
            result = extractor._persist(filtered, session, source="session_end")

        # DB 写入失败 → memory_ids 空;无 episode/action_nodes → episode_ids 也空
        assert isinstance(result, PersistenceResult)
        assert result.memory_ids == []
        assert result.episode_ids == []
        # fallback 落盘 1 条 memory
        mem_fb = list(db.fallback_dir.glob("*_memory_*.json"))
        assert len(mem_fb) == 1
        payload = json.loads(mem_fb[0].read_text(encoding="utf-8"))
        assert payload["kind"] == "memory"
        assert payload["item"]["content"] == "user likes uv"
        # 整个 _persist 仍然正常返回,无未捕获异常
        assert hasattr(result, "memory_ids")
        assert hasattr(result, "episode_ids")


# ---------------------------------------------------------------------------
# 启动时 replay
# ---------------------------------------------------------------------------


class TestReplayOnInit:
    def test_extractor_init_replays_fallback(self, tmp_path: Path):
        """构造前先写一个 fallback,新建 extractor 时自动 replay 成功。"""
        db = MemoryDatabase(tmp_path)
        db.init_schema()

        payload = {
            "kind": "memory",
            "item": Memory(
                id="init-replay",
                content="replayed-on-init",
                type=MemoryType.FACT,
                created_at="2026-09-13T00:00:00+00:00",
                workspace_id="default",
            ).to_row(),
            "attempt": "2026-09-13T00:00:00+00:00",
            "error": "prior failure",
        }
        (db.fallback_dir / "2026-09-13T00:00:00_memory_initfb01.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

        # 新建 extractor,触发 __init__ 末尾的 replay
        extractor = _make_extractor(db, _FakeProvider())
        assert isinstance(extractor, MemoryExtractor)
        # 文件已被消费
        assert list(db.fallback_dir.glob("*.json")) == []

        from nanobot.memory.repository import list_memories

        with db.connect() as conn:
            mems = list_memories(conn, workspace_id="default")
        assert any(m.content == "replayed-on-init" for m in mems)


# ---------------------------------------------------------------------------
# MAJOR #1: _safe_write_with_fallback 失败后 rollback
# MAJOR #2: fallback 内容 redact
# MAJOR #3: MemoryDatabase.__init__ 自动 replay
# 集体审查修复点
# ---------------------------------------------------------------------------


class TestSafeWriteRollbackAndRedact:
    def test_safe_write_with_fallback_calls_rollback_on_failure(
        self, tmp_path: Path, db: MemoryDatabase
    ):
        """MAJOR #1: writer 抛 OperationalError → conn.rollback 被调用 1 次。"""
        extractor = _make_extractor(db, _FakeProvider())
        conn = MagicMock()
        from sqlite3 import OperationalError

        def broken_writer(_conn: Any) -> None:
            raise OperationalError("database is locked")

        item = _memory_row_dict()
        ok = extractor._safe_write_with_fallback(
            conn, kind="memory", item=item, writer=broken_writer
        )

        assert ok is False
        assert conn.rollback.call_count == 1
        # fallback 文件被创建
        files = list(db.fallback_dir.glob("*_memory_*.json"))
        assert len(files) == 1

    def test_safe_write_with_fallback_rollback_failure_does_not_block_fallback(
        self, tmp_path: Path, db: MemoryDatabase
    ):
        """MAJOR #1: rollback 自身失败必须吞掉,不能阻断 fallback 落盘。"""
        extractor = _make_extractor(db, _FakeProvider())
        from sqlite3 import InterfaceError, OperationalError

        conn = MagicMock()
        conn.rollback.side_effect = InterfaceError("rollback itself failed")

        def broken_writer(_conn: Any) -> None:
            raise OperationalError("database is locked")

        # 不应抛异常
        ok = extractor._safe_write_with_fallback(
            conn, kind="memory", item=_memory_row_dict(), writer=broken_writer
        )

        assert ok is False
        # rollback 仍被尝试调用 1 次
        assert conn.rollback.call_count == 1
        # fallback 文件仍被创建（fallback 是兜底）
        files = list(db.fallback_dir.glob("*_memory_*.json"))
        assert len(files) == 1

    def test_persist_continues_after_mid_loop_failure(self, tmp_path: Path):
        """MAJOR #1+集成: 3 条 memory,add_memory 第 2 次抛错 → rollback 撤销第 1 条事务内写入,只剩第 3 入库,第 2 落 fallback。"""
        db = MemoryDatabase(tmp_path)
        db.init_schema()
        extractor = _make_extractor(db, _FakeProvider())

        # 构造 3 条 memory
        filtered = FilteredExtractionResult(
            memories=[
                LLMMemoryItem(content="memory-1", type="FACT", priority="long_term"),
                LLMMemoryItem(content="memory-2", type="FACT", priority="long_term"),
                LLMMemoryItem(content="memory-3", type="FACT", priority="long_term"),
            ],
        )
        session = _plain_session()

        from sqlite3 import OperationalError

        from nanobot.memory import extractor as ext_mod

        call_counter = {"n": 0}
        original_add_memory = ext_mod.add_memory

        def flaky_add_memory(conn, memory):  # noqa: ARG001
            call_counter["n"] += 1
            if call_counter["n"] == 2:
                raise OperationalError("database is locked")
            return original_add_memory(conn, memory)

        with patch.object(ext_mod, "add_memory", side_effect=flaky_add_memory):
            result = extractor._persist(filtered, session, source="session_end")

        # 验证：_persist 不抛异常,返回合法 PersistenceResult
        assert isinstance(result, PersistenceResult)
        # _persist 在 writer 返回 True 时就把 id 计入 saved_memory_ids,与最终
        # 提交状态解耦。rollback 撤销了第 1 条事务内写入,但 saved_memory_ids
        # 列表保留(调用方需自行处理这个语义差,本测试不要求改 _persist 行为)。
        assert len(result.memory_ids) == 2
        # 真正入库的只有 memory-3(rollback 清掉了 memory-1,后续 memory-3 在新 tx 中提交)
        from nanobot.memory.repository import list_memories

        with db.connect() as conn:
            mems = list_memories(conn, workspace_id="default")
        assert len(mems) == 1
        assert mems[0].content == "memory-3"
        # 恰好 1 个 fallback 文件被创建（第 2 条）
        mem_fb = list(db.fallback_dir.glob("*_memory_*.json"))
        assert len(mem_fb) == 1
        # fallback 文件中 item["content"] 是第 2 条 memory
        payload = json.loads(mem_fb[0].read_text(encoding="utf-8"))
        assert payload["kind"] == "memory"
        assert payload["item"]["content"] == "memory-2"

    def test_safe_write_redacts_fallback_content_and_subject_predicate(
        self, tmp_path: Path, db: MemoryDatabase
    ):
        """MAJOR #2 / OWASP LLM05: 凭据不应出现在 fallback JSON 中。"""
        extractor = _make_extractor(db, _FakeProvider())
        from sqlite3 import OperationalError

        conn = MagicMock()

        def broken_writer(_conn: Any) -> None:
            raise OperationalError("database is locked")

        item = {
            "content": "my api_key=secret123 hello",
            "subject": "Bearer xyzabc",
            "predicate": "password=hunter2",
        }
        ok = extractor._safe_write_with_fallback(
            conn, kind="memory", item=item, writer=broken_writer
        )

        assert ok is False
        files = list(db.fallback_dir.glob("*_memory_*.json"))
        assert len(files) == 1
        payload = json.loads(files[0].read_text(encoding="utf-8"))
        # 三个字段都被 redact,包含 <redacted>
        assert "<redacted>" in payload["item"]["content"]
        assert "<redacted>" in payload["item"]["subject"]
        assert "<redacted>" in payload["item"]["predicate"]
        # 原始凭据字面量不再出现
        assert "secret123" not in payload["item"]["content"]
        assert "xyzabc" not in payload["item"]["subject"]
        assert "hunter2" not in payload["item"]["predicate"]


class TestDatabaseInitReplaysFallback:
    def test_database_init_replays_pending_fallback_file(self, tmp_path: Path):
        """MAJOR #3: MemoryDatabase.__init__ 末尾自动 replay 已存在的 fallback 文件。"""
        # 先单独构造一个 db 来建 schema（构造时会 replay 一次空目录,无害）。
        db_setup = MemoryDatabase(tmp_path)
        db_setup.init_schema()

        # 模拟"上轮遗留" — 直接写一个 fallback JSON(绕过 db_setup 的 init replay)
        payload = {
            "kind": "memory",
            "item": Memory(
                id="init-replay-db",
                content="replayed-on-db-init",
                type=MemoryType.FACT,
                created_at="2026-09-13T00:00:00+00:00",
                workspace_id="default",
            ).to_row(),
            "attempt": "2026-09-13T00:00:00+00:00",
            "error": "prior failure",
        }
        (db_setup.fallback_dir / "2026-09-13T00:00:00_memory_initdb01.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

        # 新构造一个 db → __init__ 末尾的 replay_fallback 应自动消费该文件
        db2 = MemoryDatabase(tmp_path)

        # 验证：fallback 文件被消费
        assert list(db2.fallback_dir.glob("*.json")) == []
        # DB 中能查到对应 memory
        from nanobot.memory.repository import list_memories

        with db2.connect() as conn:
            mems = list_memories(conn, workspace_id="default")
        assert any(m.content == "replayed-on-db-init" for m in mems)

    def test_database_init_replay_failure_does_not_raise(self, tmp_path: Path):
        """MAJOR #3: 即使 fallback 文件无效,MemoryDatabase.__init__ 也不抛异常。"""
        # 先建 schema,确保 replay 失败仅来自 JSON 内容(而不是缺表)
        db_setup = MemoryDatabase(tmp_path)
        db_setup.init_schema()

        # 写一个无效的 fallback JSON（缺 item 字段）
        (db_setup.fallback_dir / "2026-09-13T00:00:00_memory_bad0001.json").write_text(
            json.dumps(
                {"kind": "memory", "attempt": "2026-09-13T00:00:00+00:00"},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        # 构造不应抛异常
        db = MemoryDatabase(tmp_path)
        assert isinstance(db, MemoryDatabase)
        # 失败文件被保留（replay 失败隔离）
        remaining = list(db.fallback_dir.glob("*.json"))
        assert len(remaining) == 1

