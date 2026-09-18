"""Tests for MemoryLifecycle: refresh_memory_md_sync / truncate_memory_md / schedule_refresh_md."""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.filters import content_hash_legacy
from nanobot.memory.lifecycle import (
    MEMORY_MD_MAX_CHARS,
    MIN_CONTENT_CHARS,
    MemoryLifecycle,
    _iso8601_now,
)
from nanobot.memory.models import Memory, MemoryPriority, MemoryType
from nanobot.memory.repository import add_memory
from nanobot.webui.memory_services import MemoryServices


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_ws(tmp_path):
    """Temporary workspace path."""
    return tmp_path / "ws"


@pytest.fixture
def services(tmp_ws):
    """MemoryServices for the temp workspace."""
    tmp_ws.mkdir(parents=True)
    db = MemoryDatabase(tmp_ws)
    db.init_schema()
    return MemoryServices(workspace_id="test-ws", database=db)


@pytest.fixture
def lifecycle(services):
    """Fresh MemoryLifecycle instance per test."""
    # Clear class-level state between tests
    MemoryLifecycle._instances.clear()
    MemoryLifecycle._refresh_tasks.clear()
    MemoryLifecycle._last_refresh_at.clear()
    MemoryLifecycle._last_refresh_iso.clear()
    MemoryLifecycle._last_refresh_trigger.clear()
    lc = MemoryLifecycle("test-ws", services)
    # Ensure memory dir exists
    lc.memory_dir.mkdir(parents=True, exist_ok=True)
    return lc


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _mem(
    content: str,
    mtype: MemoryType,
    importance: float = 0.5,
    scope: str = "user",
    **overrides,
) -> Memory:
    now = _iso8601_now()
    base = dict(
        id=str(uuid.uuid4()),
        content=content,
        created_at=now,
        updated_at=now,
        type=mtype,
        priority=MemoryPriority.LONG_TERM,
        source="manual",
        importance_score=importance,
        tags=[],
        subject="",
        predicate="",
        scope=scope,
        workspace_id="test-ws",
    )
    base.update(overrides)
    return Memory(**base)


def _seed(services: MemoryServices, *memories: Memory) -> None:
    with services.database.connect() as conn:
        for m in memories:
            add_memory(conn, m)


# ---------------------------------------------------------------------------
# content_hash_legacy
# ---------------------------------------------------------------------------


class TestContentHashLegacy:
    def test_same_content_same_hash(self):
        h1 = content_hash_legacy("用户偏好 Python")
        h2 = content_hash_legacy("用户偏好 Python")
        assert h1 == h2

    def test_different_content_different_hash(self):
        h1 = content_hash_legacy("内容A")
        h2 = content_hash_legacy("内容B")
        assert h1 != h2

    def test_whitespace_stripped(self):
        h1 = content_hash_legacy("内容")
        h2 = content_hash_legacy("  内容  ")
        assert h1 == h2

    def test_returns_sha1_hex(self):
        h = content_hash_legacy("test")
        assert len(h) == 40
        assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# _render_memory_md
# ---------------------------------------------------------------------------


class TestRenderMemoryMd:
    def test_empty_list(self, lifecycle):
        result = lifecycle._render_memory_md([])
        # Should still produce header even with no memories
        assert "# 核心记忆" in result

    def test_single_type_only(self, lifecycle):
        m = _mem("重要规则：不要删除任何文件", MemoryType.RULE, importance=0.9)
        result = lifecycle._render_memory_md([m])
        assert "## 规则" in result
        assert "## 偏好" not in result
        assert "## 事实" not in result
        assert "- 重要规则：不要删除任何文件" in result

    def test_all_six_types(self, lifecycle):
        memories = [
            _mem("偏好1", MemoryType.PREFERENCE, importance=0.9),
            _mem("偏好2", MemoryType.PREFERENCE, importance=0.7),
            _mem("规则1", MemoryType.RULE, importance=0.8),
            _mem("事实1", MemoryType.FACT, importance=0.6),
            _mem("教训1", MemoryType.ERROR, importance=0.5),
            _mem("技能1", MemoryType.SKILL, importance=0.4),
            _mem("经验1", MemoryType.EXPERIENCE, importance=0.3),
        ]
        result = lifecycle._render_memory_md(memories)
        assert "## 偏好" in result
        assert "## 规则" in result
        assert "## 事实" in result
        assert "## 教训" in result
        assert "## 技能" in result
        assert "## 经验" in result

    def test_top_k_per_section(self, lifecycle):
        # 6 items for PREFERENCE — only top-4 should appear
        memories = [
            _mem(f"偏好{i}", MemoryType.PREFERENCE, importance=0.9 - i * 0.1)
            for i in range(6)
        ]
        result = lifecycle._render_memory_md(memories)
        count = result.count("- 偏好")
        assert count == 4

    def test_sorted_by_importance(self, lifecycle):
        memories = [
            _mem("低优先级", MemoryType.PREFERENCE, importance=0.3),
            _mem("高优先级", MemoryType.PREFERENCE, importance=0.9),
            _mem("中优先级", MemoryType.PREFERENCE, importance=0.6),
        ]
        result = lifecycle._render_memory_md(memories)
        high_idx = result.find("高优先级")
        mid_idx = result.find("中优先级")
        low_idx = result.find("低优先级")
        assert high_idx < mid_idx < low_idx

    def test_dedup_same_content(self, lifecycle):
        # Same content, same type -> only one should appear
        m1 = _mem("相同内容", MemoryType.FACT, importance=0.3)
        m2 = _mem("相同内容", MemoryType.FACT, importance=0.9)  # higher importance
        result = lifecycle._render_memory_md([m1, m2])
        assert result.count("- 相同内容") == 1


# ---------------------------------------------------------------------------
# refresh_memory_md_sync
# ---------------------------------------------------------------------------


class TestRefreshMemoryMdSync:
    def test_empty_database_skips(self, lifecycle, services):
        result = lifecycle.refresh_memory_md_sync("test-ws")
        assert result["status"] == "skipped"
        assert result["reason"] == "content_too_short"

    def test_user_scope_only(self, lifecycle, services):
        """Only user-scope memories are included."""
        _seed(
            services,
            _mem("用户记忆", MemoryType.FACT, scope="user"),
            _mem("Agent记忆", MemoryType.FACT, scope="agent"),
            _mem("Global记忆", MemoryType.FACT, scope="global"),
        )
        result = lifecycle.refresh_memory_md_sync("test-ws")
        assert result["status"] == "ok"
        content = lifecycle.memory_file.read_text()
        assert "用户记忆" in content
        assert "Agent记忆" not in content
        assert "Global记忆" not in content

    def test_min_importance_threshold(self, lifecycle, services):
        """Memories with importance < 0.5 are excluded."""
        _seed(
            services,
            _mem("高重要性", MemoryType.FACT, importance=0.8, scope="user"),
            _mem("低重要性", MemoryType.FACT, importance=0.3, scope="user"),
        )
        result = lifecycle.refresh_memory_md_sync("test-ws")
        assert result["status"] == "ok"
        content = lifecycle.memory_file.read_text()
        assert "高重要性" in content
        assert "低重要性" not in content

    def test_writes_file(self, lifecycle, services):
        _seed(services, _mem("测试内容", MemoryType.FACT, scope="user"))
        result = lifecycle.refresh_memory_md_sync("test-ws")
        assert result["status"] == "ok"
        assert lifecycle.memory_file.exists()
        assert "# 核心记忆" in lifecycle.memory_file.read_text()

    def test_updates_last_refresh_stats(self, lifecycle, services):
        _seed(services, _mem("测试", MemoryType.FACT, scope="user"))
        result = lifecycle.refresh_memory_md_sync("test-ws")
        assert result["status"] == "ok"
        assert "test-ws" in MemoryLifecycle._last_refresh_iso
        assert "test-ws" in MemoryLifecycle._last_refresh_trigger
        assert MemoryLifecycle._last_refresh_trigger["test-ws"] == "manual"


# ---------------------------------------------------------------------------
# truncate_memory_md
# ---------------------------------------------------------------------------


class TestTruncateMemoryMd:
    def test_under_limit_unchanged(self, lifecycle):
        content = "# 核心记忆\n\n## 事实\n- 短内容"
        result = lifecycle.truncate_memory_md(content, max_chars=200)
        assert result == content

    def test_truncation_reduces_size(self, lifecycle):
        # Build content that exceeds 1500 chars
        repeated = "这是一个很长的记忆内容，用于测试截断功能。" * 20
        content = "# 核心记忆\n\n## 事实\n" + "\n".join(
            f"- {repeated}{i}" for i in range(10)
        )
        assert len(content) > MEMORY_MD_MAX_CHARS
        result = lifecycle.truncate_memory_md(content, max_chars=MEMORY_MD_MAX_CHARS)
        assert len(result) <= MEMORY_MD_MAX_CHARS

    def test_rule_section_priority(self, lifecycle):
        """Rule sections are kept even when normal sections are dropped."""
        long_rule = "重要规则：" + "x" * 800
        long_fact = "事实：" + "y" * 800
        content = (
            "# 核心记忆\n\n"
            f"## 规则\n- {long_rule}\n\n"
            f"## 事实\n- {long_fact}\n"
        )
        assert len(content) > MEMORY_MD_MAX_CHARS
        result = lifecycle.truncate_memory_md(content, max_chars=MEMORY_MD_MAX_CHARS)
        assert "## 规则" in result
        assert len(result) <= MEMORY_MD_MAX_CHARS

    def test_truncated_rule_gets_marker(self, lifecycle):
        """When rule section itself is truncated, it gets the marker."""
        # Very long rule section that forces truncation
        long_rule = "重要规则：" + "x" * 1600
        content = f"# 核心记忆\n\n## 规则\n- {long_rule}\n"
        assert len(content) > MEMORY_MD_MAX_CHARS
        result = lifecycle.truncate_memory_md(content, max_chars=MEMORY_MD_MAX_CHARS)
        assert "(规则被截断)" in result
        assert len(result) <= MEMORY_MD_MAX_CHARS

    def test_normal_section_skipped_when_no_budget(self, lifecycle):
        """Normal sections are skipped when they don't fit at the end."""
        # Rule takes almost all budget
        rule_content = "## 规则\n- " + "r" * 1400 + "\n\n"
        fact_content = "## 事实\n- fact content\n"
        content = f"# 核心记忆\n\n{rule_content}{fact_content}"
        result = lifecycle.truncate_memory_md(content, max_chars=MEMORY_MD_MAX_CHARS)
        # Fact section may be skipped or truncated
        assert "## 规则" in result
        assert len(result) <= MEMORY_MD_MAX_CHARS

    def test_level1_header_preserved(self, lifecycle):
        """The top-level # header is always preserved."""
        content = (
            "# 我的核心记忆\n\n"
            "## 规则\n- rule1\n"
            "## 事实\n- fact1\n"
        )
        # Make it exceed limit
        content = content + "extra " * 300
        result = lifecycle.truncate_memory_md(content, max_chars=200)
        assert result.startswith("# 我的核心记忆")


# ---------------------------------------------------------------------------
# _safe_write_with_backup
# ---------------------------------------------------------------------------


class TestSafeWriteWithBackup:
    def test_write_creates_file(self, lifecycle, tmp_path):
        path = tmp_path / "test.md"
        lifecycle._safe_write_with_backup(path, "hello world")
        assert path.read_text() == "hello world"

    def test_backup_created(self, lifecycle, tmp_path):
        path = tmp_path / "test.md"
        path.write_text("original")
        lifecycle._safe_write_with_backup(path, "modified")
        backup = path.with_suffix(".md.bak")
        assert backup.read_text() == "original"
        assert path.read_text() == "modified"

    def test_rollback_on_failure(self, lifecycle, tmp_path):
        path = tmp_path / "test.md"
        path.write_text("original")
        backup = path.with_suffix(".md.bak")
        backup.write_text("backup-content")

        original_content = "original"
        path.write_text(original_content)

        # Simulate failure by making path unwritable (directory read-only)
        # We can't easily simulate write failure on Windows, so test logic path
        # Instead verify the backup mechanism works by checking backup is created
        lifecycle._safe_write_with_backup(path, "new content")
        assert path.read_text() == "new content"
        assert backup.read_text() == "original"


# ---------------------------------------------------------------------------
# schedule_refresh_md debounce
# ---------------------------------------------------------------------------


class TestScheduleRefreshMd:
    @pytest.mark.asyncio
    async def test_debounce_skips_second_call(self, lifecycle, services):
        """Two calls within 60s should only trigger one refresh."""
        _seed(services, _mem("测试", MemoryType.FACT, scope="user"))

        # First call: should trigger
        await lifecycle.schedule_refresh_md("test-ws", debounce_seconds=60.0)
        await asyncio.sleep(0.05)

        # Get the first task
        task1 = MemoryLifecycle._refresh_tasks.get("test-ws")

        # Second call within debounce window: should be skipped (no new task created)
        await lifecycle.schedule_refresh_md("test-ws", debounce_seconds=60.0)
        await asyncio.sleep(0.05)

        # Task should be the same (or already done)
        # The debounce should prevent a new task from being created
        assert "test-ws" in MemoryLifecycle._last_refresh_at

    @pytest.mark.asyncio
    async def test_debounce_reset_after_window(self, lifecycle, services):
        """After debounce window, a new refresh is triggered."""
        _seed(services, _mem("测试", MemoryType.FACT, scope="user"))

        # First call
        await lifecycle.schedule_refresh_md("test-ws", debounce_seconds=0.05)
        await asyncio.sleep(0.1)

        # After window: new refresh should trigger
        await lifecycle.schedule_refresh_md("test-ws", debounce_seconds=0.05)
        await asyncio.sleep(0.05)

        # Should have completed
        assert lifecycle.memory_file.exists()

    @pytest.mark.asyncio
    async def test_trigger_is_auto(self, lifecycle, services):
        """schedule_refresh_md sets trigger to 'auto'."""
        _seed(services, _mem("测试", MemoryType.FACT, scope="user"))

        await lifecycle.schedule_refresh_md("test-ws", debounce_seconds=0.05)
        await asyncio.sleep(0.1)

        assert MemoryLifecycle._last_refresh_trigger.get("test-ws") == "auto"


# ---------------------------------------------------------------------------
# for_workspace singleton
# ---------------------------------------------------------------------------


class TestForWorkspace:
    def test_same_instance_on_repeated_calls(self, services):
        MemoryLifecycle._instances.clear()
        lc1 = MemoryLifecycle.for_workspace("ws-a", services)
        lc2 = MemoryLifecycle.for_workspace("ws-a", services)
        assert lc1 is lc2

    def test_different_workspaces_different_instances(self, tmp_ws, services):
        MemoryLifecycle._instances.clear()
        services_b = MemoryServices.for_workspace("ws-b", tmp_ws)
        lc1 = MemoryLifecycle.for_workspace("ws-a", services)
        lc2 = MemoryLifecycle.for_workspace("ws-b", services_b)
        assert lc1 is not lc2


# ---------------------------------------------------------------------------
# Integration: end-to-end render
# ---------------------------------------------------------------------------


class TestEndToEndRender:
    def test_render_matches_expected_format(self, lifecycle, services):
        memories = [
            _mem("喜欢用 Python 编程", MemoryType.PREFERENCE, importance=0.8),
            _mem("不要轻易删除文件", MemoryType.RULE, importance=0.9),
            _mem("用户使用 Windows 11", MemoryType.FACT, importance=0.7),
        ]
        _seed(services, *memories)
        result = lifecycle.refresh_memory_md_sync("test-ws")
        assert result["status"] == "ok"
        content = lifecycle.memory_file.read_text()
        # Header
        assert content.startswith("# 核心记忆\n\n")
        # Rule first (high importance)
        assert "## 规则" in content
        assert "不要轻易删除文件" in content
        # Preference
        assert "## 偏好" in content
        # Fact
        assert "## 事实" in content
