"""Scratchpad 写入器（T0 即时同步路径，plan §6）。

提供两种写入策略：
- T0 即时同步路径（不调 LLM）：update_focus / archive_completed
- T1/T2 LLM 深度格式化路径（extractor 主导，plan §6.2）

与 runtime_control.__scratchpad 完全独立，不在本模块范围内连接。
"""

from __future__ import annotations

from datetime import datetime, timezone

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import ScratchpadEntry
from nanobot.memory.repository import get_scratchpad, upsert_scratchpad

# ---------- 常量 ----------

# active_projects 最多保留条目数（plan §6.1）
MAX_ACTIVE_PROJECTS = 5


# ---------- ScratchpadWriter ----------

class ScratchpadWriter:
    """处理 scratchpad 写入的类（plan §6）。

    T0 即时同步路径（不调 LLM），适用于 after_run 钩子等高频低延迟场景。
    所有操作通过 :class:`MemoryDatabase` 的线程安全连接执行。

    Args:
        database: MemoryDatabase 实例。
        user_id: 用户 ID，默认为 "default"。
        workspace_id: 工作区 ID，默认为 "default"。
    """

    def __init__(
        self,
        database: MemoryDatabase,
        user_id: str = "default",
        workspace_id: str = "default",
    ) -> None:
        self.database = database
        self.user_id = user_id
        self.workspace_id = workspace_id

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _now_iso() -> str:
        """返回当前 UTC 时间 ISO 字符串。"""
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _format_project_entry(focus: str) -> str:
        """格式化 active_projects 条目：带日期前缀，如 "[09-10 15:30] xxx"。

        Args:
            focus: 项目描述文本。

        Returns:
            带本地时间戳前缀的条目，focus 截断至 200 字符。
        """
        now = datetime.now()
        date_prefix = now.strftime("%m-%d %H:%M")
        return f"[{date_prefix}] {focus[:200]}"

    # ------------------------------------------------------------------
    # T0 即时同步路径
    # ------------------------------------------------------------------

    async def update_focus(
        self,
        session_key: str,
        new_focus: str,
    ) -> None:
        """T0 即时同步：更新 current_focus，旧 focus 入 active_projects。

        触发时机：每轮 after_run。
        语义：
        - 写入 scratchpad.current_focus = new_focus[:200]
        - 若存在旧 focus 且与 new_focus 不同，将其归档至 active_projects
        - active_projects 最多保留 MAX_ACTIVE_PROJECTS 条，超出时移除最旧条目
        - 同步执行，目标 < 50ms

        注意：session_key 参数保留用于未来扩展（当前不使用）。

        Args:
            session_key: 当前会话标识（暂未使用）。
            new_focus: 新的当前焦点描述。
        """
        old_focus: str = ""
        existing_projects: list[str] = []

        # 读取现有 scratchpad（获取旧 focus 和 active_projects）
        with self.database.connect() as conn:
            entry = get_scratchpad(conn, self.user_id, self.workspace_id)
            if entry is not None:
                old_focus = entry.current_focus
                existing_projects = list(entry.active_projects)

        # 准备新的 active_projects
        new_projects = list(existing_projects)
        if old_focus and old_focus != new_focus:
            formatted = self._format_project_entry(old_focus)
            new_projects.insert(0, formatted)
            # 最多保留 MAX_ACTIVE_PROJECTS 条
            new_projects = new_projects[:MAX_ACTIVE_PROJECTS]

        # 构建新 entry 并写入
        updated_entry = ScratchpadEntry(
            user_id=self.user_id,
            workspace_id=self.workspace_id,
            updated_at=self._now_iso(),
            content="",
            active_projects=new_projects,
            current_focus=new_focus[:200],
            open_questions=[],
            next_steps=[],
        )
        with self.database.connect() as conn:
            upsert_scratchpad(conn, updated_entry)

    async def archive_completed(
        self,
        resolved_items: list[str],
    ) -> None:
        """从 open_questions 中移除已解决项。

        触发时机：LLM 深度格式化后（plan §6.2 T1/T2 路径）。

        实现：在 get_scratchpad 基础上过滤掉 resolved_items 中的条目，
        通过 upsert_scratchpad 写回。resolved_items 中不在 open_questions
        中的条目无操作影响。

        注意：本方法仅支持从 open_questions 移除项，不支持修改其他字段。
        完整的 open_questions 替换需由调用方保证一致性。

        Args:
            resolved_items: 已解决的项目列表（精确匹配移除）。
        """
        if not resolved_items:
            return

        existing_questions: list[str] = []
        current_focus: str = ""
        existing_projects: list[str] = []
        existing_next_steps: list[str] = []

        # 读取现有 scratchpad
        with self.database.connect() as conn:
            entry = get_scratchpad(conn, self.user_id, self.workspace_id)
            if entry is not None:
                current_focus = entry.current_focus
                existing_projects = list(entry.active_projects)
                existing_questions = list(entry.open_questions)
                existing_next_steps = list(entry.next_steps)

        # 过滤掉已解决项（精确匹配）
        resolved_set = set(resolved_items)
        remaining_questions = [q for q in existing_questions if q not in resolved_set]

        # 写回（保留其他字段不变）
        updated_entry = ScratchpadEntry(
            user_id=self.user_id,
            workspace_id=self.workspace_id,
            updated_at=self._now_iso(),
            content="",
            active_projects=existing_projects,
            current_focus=current_focus,
            open_questions=remaining_questions,
            next_steps=existing_next_steps,
        )
        with self.database.connect() as conn:
            upsert_scratchpad(conn, updated_entry)
