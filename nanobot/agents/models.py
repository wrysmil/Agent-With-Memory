"""Agent 档案的数据模型与线格式转换。

线格式一律 camelCase，与 ``webui/src/lib/agents/types.ts`` 的
``AgentProfile`` 逐字段同名（见
``.ai-runtime-artifacts/contracts/2026-09-29-agents-contract.md`` §2/§4）。
Python 侧保持 snake_case，序列化时统一转换。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, cast

SELECTION_MODES: frozenset[str] = frozenset({"all", "include", "exclude"})
AGENT_TYPES: frozenset[str] = frozenset({"system", "custom"})

# 与 webui/src/lib/agents/types.ts:129 的 PROMPT_MAX_LENGTH 同值。
PROMPT_MAX_LENGTH = 5000
NAME_MAX_LENGTH = 120

_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


class AgentProfileError(ValueError):
    """字段级校验失败，携带可直接透传给 HTTP 层的状态码。"""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class AgentSelection:
    """能力选择：``all`` 全要、``include`` 白名单、``exclude`` 黑名单。

    语义与前端 ``resolveSelection``（``types.ts:148``）严格一致。
    """

    mode: str = "all"
    entries: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "entries": list(self.entries)}

    @classmethod
    def from_dict(cls, raw: Any) -> AgentSelection:
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise AgentProfileError("selection must be an object")
        # 收窄成 ``dict[str, object]``：JSON 的键必是字符串，值才是异构的。
        data: dict[str, object] = cast("dict[str, object]", raw)
        mode = data.get("mode", "all")
        if not isinstance(mode, str) or mode not in SELECTION_MODES:
            raise AgentProfileError(
                f"selection mode must be one of {sorted(SELECTION_MODES)}", status=400
            )
        entries_raw = data.get("entries", [])
        if not isinstance(entries_raw, list):
            raise AgentProfileError("selection entries must be a string array", status=400)
        entries: list[str] = []
        for item in cast("list[object]", entries_raw):
            if not isinstance(item, str):
                raise AgentProfileError("selection entries must be a string array", status=400)
            entries.append(item)
        if len(set(entries)) != len(entries):
            raise AgentProfileError(
                "selection entries must not contain duplicates", status=400
            )
        if any(not item for item in entries):
            raise AgentProfileError("selection entries must not contain empty ids", status=400)
        return cls(mode=mode, entries=entries)

    def same_set(self, other: AgentSelection) -> bool:
        """比较两次选择是否指向同一组 id（忽略 ``entries`` 的书写顺序）。"""
        return self.mode == other.mode and set(self.entries) == set(other.entries)


@dataclass(frozen=True)
class AgentCategory:
    id: str
    name: str
    color: str = "#4A90D9"
    order: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "color": self.color, "order": self.order}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AgentCategory:
        order = raw.get("order", 0)
        return cls(
            id=str(raw.get("id", "")),
            name=str(raw.get("name", "")),
            color=str(raw.get("color", "#4A90D9")),
            order=int(order) if isinstance(order, int) else 0,
        )


@dataclass
class AgentProfile:
    """一个 Agent 档案。字段所有权见契约 §4.1。"""

    id: str
    name: str
    description: str = ""
    type: str = "custom"
    customized: bool = False
    category_id: str | None = None
    icon: str = "🤖"
    color: str = "#4A90D9"
    prompt: str = ""
    model_id: str | None = None
    tools: AgentSelection = field(default_factory=AgentSelection)
    skills: AgentSelection = field(default_factory=AgentSelection)
    sub_agents: AgentSelection = field(default_factory=AgentSelection)
    hidden: bool = False
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "type": self.type,
            "customized": self.customized,
            "categoryId": self.category_id,
            "icon": self.icon,
            "color": self.color,
            "prompt": self.prompt,
            "modelId": self.model_id,
            "tools": self.tools.to_dict(),
            "skills": self.skills.to_dict(),
            "subAgents": self.sub_agents.to_dict(),
            "hidden": self.hidden,
            "updatedAt": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> AgentProfile:
        if not isinstance(raw, dict):
            raise AgentProfileError("profile must be an object", status=400)
        data: dict[str, object] = cast("dict[str, object]", raw)
        model_id = data.get("modelId")
        category_id = data.get("categoryId")
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            type=str(data.get("type", "custom")),
            customized=bool(data.get("customized", False)),
            category_id=str(category_id) if category_id else None,
            icon=str(data.get("icon", "🤖")),
            color=str(data.get("color", "#4A90D9")),
            prompt=str(data.get("prompt", "")),
            model_id=str(model_id) if model_id else None,
            tools=AgentSelection.from_dict(data.get("tools")),
            skills=AgentSelection.from_dict(data.get("skills")),
            sub_agents=AgentSelection.from_dict(data.get("subAgents")),
            hidden=bool(data.get("hidden", False)),
            updated_at=str(data.get("updatedAt", "")),
        )

    def validate(self) -> None:
        """字段级校验。抛出 :class:`AgentProfileError`，不返回任何东西。"""
        if not self.name.strip():
            raise AgentProfileError("Agent 名称不能为空", status=422)
        if len(self.name) > NAME_MAX_LENGTH:
            raise AgentProfileError(
                f"Agent 名称不能超过 {NAME_MAX_LENGTH} 字符", status=422
            )
        if len(self.prompt) > PROMPT_MAX_LENGTH:
            raise AgentProfileError(
                f"提示词不能超过 {PROMPT_MAX_LENGTH} 字符", status=422
            )
        if not _HEX_COLOR.match(self.color):
            raise AgentProfileError(f"color 必须是 #RRGGBB 形式：{self.color!r}", status=400)
        if self.type not in AGENT_TYPES:
            raise AgentProfileError(
                f"type 必须是 {sorted(AGENT_TYPES)} 之一：{self.type!r}", status=400
            )
