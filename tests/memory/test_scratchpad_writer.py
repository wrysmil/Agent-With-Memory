"""Tests for ScratchpadWriter (plan §6 T0 路径)。"""

from pathlib import Path

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.repository import get_scratchpad
from nanobot.memory.scratchpad_writer import MAX_ACTIVE_PROJECTS, ScratchpadWriter


@pytest.fixture
def db(tmp_path: Path) -> MemoryDatabase:
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return database


# ------------------------------------------------------------------
# ScratchpadWriter.__init__
# ------------------------------------------------------------------

class TestScratchpadWriterInit:
    def test_default_user_workspace(self, db):
        writer = ScratchpadWriter(db)
        assert writer.user_id == "default"
        assert writer.workspace_id == "default"

    def test_custom_user_workspace(self, db):
        writer = ScratchpadWriter(db, user_id="alice", workspace_id="proj-x")
        assert writer.user_id == "alice"
        assert writer.workspace_id == "proj-x"


# ------------------------------------------------------------------
# update_focus
# ------------------------------------------------------------------

@pytest.mark.asyncio
class TestUpdateFocus:
    async def test_update_focus_sets_current_focus(self, db):
        """update_focus 后 get_scratchpad 读到 current_focus。"""
        writer = ScratchpadWriter(db)
        await writer.update_focus(session_key="s1", new_focus="Implement authentication")

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")

        assert entry is not None
        assert entry.current_focus == "Implement authentication"

    async def test_update_focus_truncates_long_focus(self, db):
        """new_focus 超过 200 字符时被截断。"""
        writer = ScratchpadWriter(db)
        long_focus = "x" * 500
        await writer.update_focus(session_key="s1", new_focus=long_focus)

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")

        assert entry is not None
        assert len(entry.current_focus) == 200
        assert entry.current_focus == "x" * 200

    async def test_update_focus_rolls_old_focus_to_active_projects(self, db):
        """旧 focus 入 active_projects，带日期前缀。"""
        writer = ScratchpadWriter(db)

        # 第一次更新
        await writer.update_focus(session_key="s1", new_focus="First task")
        # 第二次更新
        await writer.update_focus(session_key="s1", new_focus="Second task")

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")

        assert entry is not None
        assert entry.current_focus == "Second task"
        assert len(entry.active_projects) == 1
        # 验证带日期前缀格式：[MM-DD HH:MM]
        project = entry.active_projects[0]
        import re

        assert re.match(r"^\[\d{2}-\d{2} \d{2}:\d{2}\] First task$", project)

    async def test_update_focus_same_focus_no_duplicate(self, db):
        """两次相同 focus 不产生重复 active_projects 条目。"""
        writer = ScratchpadWriter(db)

        await writer.update_focus(session_key="s1", new_focus="Same task")
        await writer.update_focus(session_key="s1", new_focus="Same task")

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")

        assert entry is not None
        assert entry.current_focus == "Same task"
        # 相同 focus 不归档，所以 active_projects 应为空
        assert entry.active_projects == []

    async def test_update_focus_empty_to_non_empty(self, db):
        """从空 focus 更新到非空，不产生 active_projects 条目（无旧 focus 可归档）。"""
        writer = ScratchpadWriter(db)

        await writer.update_focus(session_key="s1", new_focus="First focus")

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")

        assert entry is not None
        assert entry.current_focus == "First focus"
        assert entry.active_projects == []

    async def test_active_projects_max_5_entries(self, db):
        """active_projects 最多 5 条，超出时移除最旧的。"""
        writer = ScratchpadWriter(db)

        # 更新 7 次，产生 6 个 historical 项目
        for i in range(7):
            await writer.update_focus(session_key="s1", new_focus=f"Task {i}")

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")

        assert entry is not None
        assert entry.current_focus == "Task 6"
        assert len(entry.active_projects) == MAX_ACTIVE_PROJECTS
        # 最旧的 Task 0 应被移除
        assert "Task 0" not in entry.active_projects[0]
        # 最新的 Task 5 应在列表中
        assert any("Task 5" in p for p in entry.active_projects)


# ------------------------------------------------------------------
# archive_completed
# ------------------------------------------------------------------

@pytest.mark.asyncio
class TestArchiveCompleted:
    async def test_archive_completed_removes_resolved(self, db):
        """resolved_items 从 open_questions 移除。"""
        # 先用 repository 写入一个带 open_questions 的 scratchpad
        from nanobot.memory.models import ScratchpadEntry

        initial = ScratchpadEntry(
            user_id="default",
            workspace_id="default",
            updated_at="2026-09-10T00:00:00Z",
            open_questions=["Q1: Auth bug", "Q2: Perf issue", "Q3: UI glitch"],
        )
        with db.connect() as conn:
            from nanobot.memory.repository import upsert_scratchpad

            upsert_scratchpad(conn, initial)

        writer = ScratchpadWriter(db)
        await writer.archive_completed(resolved_items=["Q1: Auth bug", "Q3: UI glitch"])

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")

        assert entry is not None
        assert entry.open_questions == ["Q2: Perf issue"]

    async def test_archive_completed_empty_list_no_change(self, db):
        """resolved_items 为空列表时 open_questions 保持不变。"""
        from nanobot.memory.models import ScratchpadEntry

        initial = ScratchpadEntry(
            user_id="default",
            workspace_id="default",
            updated_at="2026-09-10T00:00:00Z",
            open_questions=["Q1"],
        )
        with db.connect() as conn:
            from nanobot.memory.repository import upsert_scratchpad

            upsert_scratchpad(conn, initial)

        writer = ScratchpadWriter(db)
        await writer.archive_completed(resolved_items=[])

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")

        assert entry is not None
        assert entry.open_questions == ["Q1"]

    async def test_archive_completed_no_matching_items(self, db):
        """resolved_items 中没有匹配项时 open_questions 完全保持。"""
        from nanobot.memory.models import ScratchpadEntry

        initial = ScratchpadEntry(
            user_id="default",
            workspace_id="default",
            updated_at="2026-09-10T00:00:00Z",
            open_questions=["Q1: Auth bug", "Q2: Perf issue"],
        )
        with db.connect() as conn:
            from nanobot.memory.repository import upsert_scratchpad

            upsert_scratchpad(conn, initial)

        writer = ScratchpadWriter(db)
        await writer.archive_completed(resolved_items=["Q9: Non-existent"])

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")

        assert entry is not None
        assert entry.open_questions == ["Q1: Auth bug", "Q2: Perf issue"]

    async def test_archive_completed_partial_match(self, db):
        """部分匹配时只移除存在的项，其余保留。"""
        from nanobot.memory.models import ScratchpadEntry

        initial = ScratchpadEntry(
            user_id="default",
            workspace_id="default",
            updated_at="2026-09-10T00:00:00Z",
            open_questions=["Q1", "Q2", "Q3"],
        )
        with db.connect() as conn:
            from nanobot.memory.repository import upsert_scratchpad

            upsert_scratchpad(conn, initial)

        writer = ScratchpadWriter(db)
        await writer.archive_completed(resolved_items=["Q1", "Q99"])  # Q99 不存在

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")

        assert entry is not None
        assert entry.open_questions == ["Q2", "Q3"]


# ------------------------------------------------------------------
# _format_project_entry
# ------------------------------------------------------------------

class TestFormatProjectEntry:
    def test_format_project_entry_with_date_prefix(self):
        """条目包含 [MM-DD HH:MM] 日期前缀。"""
        entry = ScratchpadWriter._format_project_entry("My task")
        import re

        assert re.match(r"^\[\d{2}-\d{2} \d{2}:\d{2}\] My task$", entry)

    def test_format_project_entry_truncates_long_focus(self):
        """focus 超过 200 字符时被截断。"""
        long_focus = "x" * 300
        entry = ScratchpadWriter._format_project_entry(long_focus)
        # 日期前缀 "[MM-DD HH:MM] " 占 14 字符，focus 截断至 200
        # 总长 = 14 + 200 = 214
        assert len(entry) == 214
        assert entry.endswith("x" * 200)

    def test_format_project_entry_empty_focus(self):
        """空 focus 仍返回带前缀的条目（不报错）。"""
        entry = ScratchpadWriter._format_project_entry("")
        import re

        assert re.match(r"^\[\d{2}-\d{2} \d{2}:\d{2}\] $", entry)
