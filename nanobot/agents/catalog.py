"""Agent 档案的静态目录：出厂预设、分类表、工具元数据映射。

遵循 ``nanobot/identity/catalog.py`` 的定位——**单一真相源**。凡是「哪些
Agent 出厂就有」「哪些 id 合法」「一个档案算不算被用户改过」这类问题，
只在本文件回答，别散落到 store 或 API 层。
"""

from __future__ import annotations

import re
from typing import Any

from nanobot.agents.models import AgentCategory, AgentProfile, AgentSelection

# 存储根目录名，与 nanobot/identity/catalog.py:9 的 IDENTITY_DIR_NAME 并列。
AGENTS_DIR_NAME = "agents"
PROFILES_SUBDIR = "profiles"
CATEGORIES_FILE = "categories.json"

# 档案 id 同时是文件名，所以既挡路径分隔符也挡 Windows 保留字符。
# 落在 agents/profiles/{id}.json，落到盘上就是一个可读、可手改的名字。
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# 参与「是否被用户实质编辑」判定的字段。``hidden`` 不在其中：隐藏是视图
# 偏好，不是内容改动。沿用 openakita profile.py:597 的同名集合设计。
CUSTOMIZATION_FIELDS: frozenset[str] = frozenset({
    "name", "description", "category_id", "icon", "color", "prompt", "model_id",
    "tools", "skills", "sub_agents",
})

FACTORY_AGENT_CATEGORIES: tuple[AgentCategory, ...] = (
    AgentCategory(id="general", name="通用", color="#4A90D9", order=0),
    AgentCategory(id="coding", name="编码", color="#8E44AD", order=1),
    AgentCategory(id="writing", name="写作", color="#E67E22", order=2),
    AgentCategory(id="research", name="研究", color="#16A085", order=3),
    AgentCategory(id="ops", name="运维", color="#C0392B", order=4),
    AgentCategory(id="efficiency", name="效率", color="#2C3E50", order=5),
)

# 工具分类与风险由静态表给出。分类表未命中的工具落到 "execution"——
# 宁可归到一个明确的桶，也不要落空让前端分组出现空洞。
TOOL_CATEGORIES: dict[str, str] = {
    "read_file": "filesystem",
    "write_file": "filesystem",
    "edit_file": "filesystem",
    "list_dir": "filesystem",
    "apply_patch": "filesystem",
    "exec": "execution",
    "exec_session": "execution",
    "notebook_edit": "execution",
    "web_search": "web",
    "web_fetch": "web",
    "memory_search": "memory",
    "cron": "scheduling",
    "session_messages": "session",
    "sessions": "session",
    "spawn": "orchestration",
    "long_task": "orchestration",
    "image_generation": "media",
}

# read_only 只能区分「只读」与「有副作用」，说不出「危险」。真正能改系统
# 状态的工具在这里显式提级。
TOOL_RISK_OVERRIDES: dict[str, str] = {
    "exec": "high",
    "exec_session": "high",
    "write_file": "high",
    "edit_file": "high",
    "apply_patch": "high",
    "spawn": "high",
    "long_task": "medium",
    "cron": "medium",
    "image_generation": "medium",
}

DEFAULT_TOOL_CATEGORY = "execution"

# 出厂档案的默认能力集：不给技能，工具只给「读」这一类。
# 全量工具一上来就摆着既看得见噪声，也让人以为默认就该全开；
# 从最小可用集起步，需要什么再勾，档案与工具的关系才是有意图的。
FACTORY_TOOLS: tuple[str, ...] = ("read_file", "list_dir")
FACTORY_SKILLS: tuple[str, ...] = ()

FACTORY_PROFILES: tuple[AgentProfile, ...] = (
    AgentProfile(
        id="general-assistant",
        name="通用助理",
        description="什么都能接的默认助手，能力对齐全局配置。",
        type="system",
        category_id="general",
        icon="🤖",
        color="#4A90D9",
        prompt="",
        tools=AgentSelection("include", list(FACTORY_TOOLS)),
        skills=AgentSelection("include", list(FACTORY_SKILLS)),
    ),
    AgentProfile(
        id="code-reviewer",
        name="代码评审",
        description="只读地审查改动，指出缺陷与风险，不动手改代码。",
        type="system",
        category_id="coding",
        icon="🔍",
        color="#8E44AD",
        prompt="",
        tools=AgentSelection("include", ["read_file", "list_dir", "grep", "find_files"]),
        skills=AgentSelection("include", []),
    ),
    AgentProfile(
        id="researcher",
        name="资料研究",
        description="多轮检索与交叉验证，最后给出带出处的结论。",
        type="system",
        category_id="research",
        icon="🔬",
        color="#16A085",
        prompt="",
        tools=AgentSelection("include", ["read_file", "list_dir", "web_search", "web_fetch"]),
        skills=AgentSelection("include", []),
    ),
    AgentProfile(
        id="ops-runner",
        name="运维执行",
        description="在受控环境里执行命令并回报结果，适合排障与部署检查。",
        type="system",
        category_id="ops",
        icon="🛠️",
        color="#C0392B",
        prompt="",
        tools=AgentSelection("include", ["read_file", "list_dir", "exec"]),
        skills=AgentSelection("include", []),
    ),
)


def is_valid_agent_id(agent_id: str) -> bool:
    """档案 id 是否能安全地落成 ``agents/profiles/{id}.json``。"""
    return bool(ID_PATTERN.match(agent_id))


def factory_for(agent_id: str) -> AgentProfile | None:
    """返回该 id 的出厂预设副本；不是出厂档案则返回 ``None``。"""
    for factory in FACTORY_PROFILES:
        if factory.id == agent_id:
            return AgentProfile.from_dict(factory.to_dict())
    return None


def is_customized(profile: AgentProfile, factory: AgentProfile) -> bool:
    """本次提交相对出厂默认值是否构成「实质编辑」。

    只有 :data:`CUSTOMIZATION_FIELDS` 里的字段参与判定，所以隐藏一个系统
    预设再取消，不会把它误标成「已定制」。
    """
    for field_name in CUSTOMIZATION_FIELDS:
        current = getattr(profile, field_name)
        original = getattr(factory, field_name)
        if isinstance(current, AgentSelection) and isinstance(original, AgentSelection):
            if not current.same_set(original):
                return True
        elif current != original:
            return True
    return False


def tool_descriptor_defaults() -> dict[str, Any]:
    """给 API 层用的兜底取值，避免那里散落字面量。"""
    return {"category": DEFAULT_TOOL_CATEGORY, "risks": ("low", "medium", "high")}
