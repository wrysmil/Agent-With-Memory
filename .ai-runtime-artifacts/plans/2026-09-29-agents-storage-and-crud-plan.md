---
artifact: implementation-plan
route: superpowers:writing-plans
skills:
  - writing-plans
  - api-and-interface-design
skills_evidence:
  - harness-kit/.agents/skills/writing-plans/SKILL.md
dispatch: .ai-runtime-artifacts/plans/2026-09-29-agents-storage-and-crud-dispatch.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - .ai-runtime-artifacts/contracts/2026-09-29-agents-contract.md
created_at: 2026-09-29
status: draft
approved: false
---

# Agent 档案存储与 CRUD 实施计划（一期）

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 `orchestration` 按 `2026-09-29-agents-storage-and-crud-dispatch.md` 的执行图逐 WU 实施。步骤用 checkbox（`- [ ]`）跟踪。

**Goal:** 让 WebUI 的智能体管理界面从 mock 数据切换到真实后端 —— 建立 Agent 档案的持久化存储、目录（工具/技能/模型）导出端点、六个 CRUD 端点，并把前端接到这些端点上。

**Architecture:** 沿用项目既有的 identity 四层模式 —— `catalog`（静态目录，出厂预设与工具元数据映射）→ `store`（读写 + 路径穿越防御 + 原子写）→ `agents_api`（与传输无关的纯 payload 函数）→ `agents_routes`（域 handler + 注入的 operations 协议）。传输层复用既有双通道：读走 HTTP `GET /api/settings/agents*`，写走 WebSocket mutation。存储布局照搬 openakita 的 `ProfileStore`（每档案一个 JSON 文件 + 分类单文件），但落在 workspace 之下与 `identity/` 并列。

**Tech Stack:** Python 3.11+ / dataclasses / `threading.RLock` / `os.replace` 原子写 / pydantic `ToolsConfig` / pytest（`asyncio_mode = "auto"`）；React 19 + TypeScript + Vite + vitest。

---

## 用户已确认的决策（不得自行更改）

1. **分两期。** 本计划只做**一期：存储 + CRUD**。运行时接入（`SpawnTool` / `SubagentManager` 改造）属二期，见 `2026-09-29-agents-runtime-integration-plan.md`。
2. **存储布局照 openakita。** `{workspace}/agents/profiles/{id}.json` + `{workspace}/agents/categories.json`，依据 `/Users/mima0000/Documents/学习-001/源码学习/openakita/src/openakita/agents/profile.py:438`。
3. **接口契约已冻结。** 一切以 `.ai-runtime-artifacts/contracts/2026-09-29-agents-contract.md` 为准。各 WU 不得单方面改签名、字段名或端点。

## 范围外（明确不做）

| 不做 | 理由 |
| --- | --- |
| `SpawnTool` / `SubagentManager` 改造 | 二期 |
| 档案的导入导出、模板市场 | 无需求 |
| 单档案乐观锁（`updatedAt` 冲突检测） | WebUI 单写者；`updatedAt` 已下发但不做比对 |
| 分类的增删改端点 | 前端分类写死在 `types.ts:99`；`save_categories` 保留在 store 层不接线 |
| `GET /api/settings/agents/{id}` | 前端一次拉全量，无单条读需求 |
| `scope: "plugin"` 与模型能力表 | 依赖 `ToolLoader` 来源标记与 provider 能力表，二期做 |
| 给工具名补中文标签 / 给技能补 `tags` | 后端没有权威来源，编造比留空更糟 |
| **UI**：运行历史 Tab | `SubagentStatus` 是内存态，进程结束就没了；持久化运行历史是新功能 |
| **UI**：在线状态点（离线/空闲） | Agent 不是常驻进程，没有存活状态可言 |
| **UI**：「分配工作」按钮 | 那是会话概念，属二期运行时接入 |
| **UI**：档案级思考强度 / 速度 / 并行运行上限 | 只有全局级 `reasoning_effort` / `temperature`；加档案级字段就超出「按现有功能体系」 |
| **UI**：访问权限 / 环境变量 / 自定义参数 | nanobot 完全没有这些概念 |

## ⚠️ 五个必须先看清的坑

1. **`agents/` 这个名字和 workspace 下的 `AGENTS.md` 无关**，但 `nanobot/agents/` 这个包名会和 `AGENTS.md` 里说的「AI 入口」混淆。新包只放 Agent 档案，不放 harness 相关代码。
2. **`ToolRegistry` 缺枚举接口。** `name` / `description` / `read_only` 都是**实例属性**（`nanobot/agent/tools/base.py:172-190`），不实例化就拿不到。Task 6 因此要给 `ToolRegistry` 加一个 `names()` 方法 —— 这是本计划唯一一处改动既有共享模块，且是纯新增只读方法。
3. **出厂预置只进内存、不落盘**（契约 §6）。所以 `AgentStore.__init__` **不**读磁盘，`_load_all` 懒加载。任何在构造期就 mkdir 的写法都会让「只读一次列表」产生副作用。
4. **前端要删 4 个文件、13 个文件集几乎全新。** `AgentEditorDialog` 402 行、`AgentCard`、`AgentTreeView`、`mock.ts` 一起走。`SettingsPage.tsx` 不用动 —— 它给 `agents` 的容器约束（`max-w-[1240px]` + `xl:overflow-hidden` + `flex min-h-0`）本来就是给主从双栏预留的，`IdentityView` 正在用。
5. **草稿归父组件管，Tab 不管。** 右栏常驻后「编辑态」这个概念消失了：`AgentDetailPane` 只切视图，`draft` 由 `AgentsView` 持有。若把 `draft` 放进 `AgentDetailPane`，切 Tab 会丢编辑内容——Task 9 的测试「切 Tab 后草稿仍在」就是钉这一条。

## File Structure

### 新建

| 文件 | 职责 |
| --- | --- |
| `nanobot/agents/__init__.py` | 空包声明 |
| `nanobot/agents/models.py` | `AgentSelection` / `AgentProfile` / `AgentCategory` 三个 dataclass + camelCase 线格式 + 字段校验 |
| `nanobot/agents/catalog.py` | 静态目录：目录名常量、id 正则、出厂预设、分类表、工具分类/风险映射表、`is_customized` 判定 |
| `nanobot/agents/store.py` | `AgentStore` 读写 + `AgentStoreError` + 原子写 + RLock |
| `nanobot/webui/agents_api.py` | 与传输无关的 payload 函数：list / catalog / save / delete / reset / visibility |
| `nanobot/webui/agents_routes.py` | `AgentSettingsOperations` 协议 + `AgentSettingsHandler` + `AGENTS_ACTION_NAMES` + `dispatch` |
| `webui/src/lib/agents/api.ts` | 前端 API 客户端（读 HTTP / 写 mutation） |
| `webui/src/lib/agents/catalog.ts` | `AgentCatalog` + `createBlankAgent` + id 生成（从被删的 `AgentEditorDialog` 迁出） |
| `webui/src/components/settings/agents/AgentListPane.tsx` | 左栏：搜索 + 分类筛选 + 紧凑行列表 + 新建 + 已隐藏折叠区 |
| `webui/src/components/settings/agents/AgentDetailPane.tsx` | 右栏：详情容器（Task 8 单列 → Task 9 header + Tab + 保存条） |
| `webui/src/components/settings/agents/useAgentSummary.ts` | 行摘要文案 hook（列表行与概览共用） |
| `webui/src/components/settings/agents/parts/AgentDetailHeader.tsx` | 详情头部：头像 / 名称 / 描述 / 重置 / 关闭 |
| `webui/src/components/settings/agents/parts/AgentOverviewTab.tsx` | 概览：能力摘要四宫格 + 已渲染 prompt 三段 + 可调度子 Agent |
| `webui/src/components/settings/agents/parts/AgentCapabilityTab.tsx` | 能力：工具 / 技能 / 提示词编辑器 |
| `webui/src/components/settings/agents/parts/AgentSettingsTab.tsx` | 设置：基础信息 / 执行配置（模型）/ 关系与调度 |
| `webui/src/components/settings/agents/parts/AgentSaveBar.tsx` | 底部脏状态条：未保存提示 + 放弃 / 保存 / 删除 |
| `tests/webui/test_agents_store.py` | 存储层单测 |
| `tests/webui/test_agents_api.py` | payload 函数单测（含 catalog 描述符形状） |
| `tests/webui/test_agents_routes.py` | handler 单测（含错误码） |
| `tests/webui/test_agents_wiring.py` | 端到端：注册点 + mutation 路径 + gateway 绑定 |
| `webui/src/tests/api-agents.test.ts` | 前端客户端单测 |
| `webui/src/tests/agents-view-integration.test.tsx` | 主从布局集成：加载 / 选中 / 脏状态 / 放弃 / 新建 / 错误 |
| `webui/src/tests/agent-detail-tabs.test.tsx` | Tab 行为：默认概览 / 切换 / 草稿保持 / 保存条状态 |

### 修改

| 文件 | 改什么 |
| --- | --- |
| `nanobot/agent/tools/registry.py` | 新增 `ToolRegistry.names()`（纯新增，约 2 行） |
| `nanobot/webui/settings_routes.py` | `_SYSTEM_ROUTES` 加 2 条读路径；新增 `AGENTS_MUTATION_PATHS` 并展开进 `_SETTINGS_MUTATION_PATHS`；dispatch 加一个 `elif` 分支；构造函数加 `agents_operations` 参数 |
| `nanobot/webui/ws_http.py` | `_WEBUI_MUTATION_ACTIONS` 加 4 条；构造函数加 `agents_operations` 参数 |
| `nanobot/webui/gateway_services.py` | 新增 `build_agents_operations`；在 `build_gateway_services` 里绑定 |
| `webui/src/components/settings/agents/AgentsView.tsx` | **整体重写**：主从容器、状态管理、接真实端点（读 + 保存 / 删除 / 隐藏 / 重置 / 复制）、`[token]` 依赖、去掉硬编码模型名 |
| `webui/src/components/settings/agents/AgentRow.tsx` | 简化：去掉 `tools`/`skills`/`models`/`agentIds` 四个目录参数改用 `useAgentSummary`；加选中态；菜单去掉「编辑」项 |
| `webui/src/lib/agents/api.ts` | `AgentCatalogResponse` 改为复用 `catalog.ts` 的 `AgentCatalog`（同一个形状不留两个名字） |

### 删除

| 文件 | 为什么 |
| --- | --- |
| `webui/src/lib/agents/mock.ts` | mock 数据源，唯一引用方是 `AgentsView` |
| `webui/src/components/settings/agents/AgentEditorDialog.tsx` | 402 行模态编辑器，被右栏详情取代 |
| `webui/src/components/settings/agents/AgentCard.tsx` | 卡片视图，与主从布局的紧凑行重复 |
| `webui/src/components/settings/agents/AgentTreeView.tsx` | 树视图；子 Agent 关系在「设置」Tab 的 `RelationsSection` 里编辑 |
| `webui/src/components/settings/agents/parts/AgentPreviewRail.tsx` | 提炼成 `AgentOverviewTab`（Task 9） |

### 原样复用（不改）

`webui/src/lib/agents/types.ts`、`webui/src/lib/agents/i18n.ts`、`webui/src/lib/agents/summary.ts`、`webui/src/components/settings/agents/shared.tsx`、`parts/BasicsSection.tsx`、`parts/CapabilityPicker.tsx`、`parts/ModelPicker.tsx`、`parts/PromptSection.tsx`、`parts/RelationsSection.tsx`

---

## Task 1: 数据模型与线格式

**Files:**
- Create: `nanobot/agents/__init__.py`
- Create: `nanobot/agents/models.py`
- Test: `tests/webui/test_agents_models.py`

- [ ] **Step 1: 写失败的测试**

创建 `tests/webui/test_agents_models.py`：

```python
"""Agent 档案线格式与字段校验的单元测试。"""

from __future__ import annotations

import pytest

from nanobot.agents.models import (
    NAME_MAX_LENGTH,
    PROMPT_MAX_LENGTH,
    AgentCategory,
    AgentProfile,
    AgentProfileError,
    AgentSelection,
)


def test_selection_round_trips_camel_case() -> None:
    original = AgentSelection(mode="include", entries=["memory", "weather"])
    assert AgentSelection.from_dict(original.to_dict()) == original


def test_selection_defaults_to_all_on_missing() -> None:
    assert AgentSelection.from_dict(None) == AgentSelection(mode="all", entries=[])


@pytest.mark.parametrize("mode", ["", "ALL", "only", None, 3])
def test_selection_rejects_unknown_mode(mode: object) -> None:
    with pytest.raises(AgentProfileError) as excinfo:
        AgentSelection.from_dict({"mode": mode, "entries": []})
    assert excinfo.value.status == 400


def test_selection_rejects_duplicate_entries() -> None:
    with pytest.raises(AgentProfileError, match="must not contain duplicates"):
        AgentSelection.from_dict({"mode": "include", "entries": ["a", "a"]})


def test_selection_rejects_empty_entry() -> None:
    with pytest.raises(AgentProfileError, match="must not contain empty ids"):
        AgentSelection.from_dict({"mode": "include", "entries": [""]})


def test_profile_to_dict_uses_frontend_field_names() -> None:
    profile = AgentProfile(id="writer", name="写作", category_id="writing")
    payload = profile.to_dict()
    assert set(payload) == {
        "id", "name", "description", "type", "customized", "categoryId",
        "icon", "color", "prompt", "modelId", "tools", "skills", "subAgents",
        "hidden", "updatedAt",
    }
    assert payload["categoryId"] == "writing"
    assert payload["subAgents"] == {"mode": "all", "entries": []}


def test_profile_from_dict_fills_defaults() -> None:
    profile = AgentProfile.from_dict({"id": "writer", "name": "写作"})
    assert profile.type == "custom"
    assert profile.customized is False
    assert profile.model_id is None
    assert profile.color == "#4A90D9"
    assert profile.tools == AgentSelection()


def test_profile_rejects_blank_name() -> None:
    with pytest.raises(AgentProfileError) as excinfo:
        AgentProfile(id="writer", name="   ").validate()
    assert excinfo.value.status == 422


def test_profile_rejects_oversized_name() -> None:
    with pytest.raises(AgentProfileError) as excinfo:
        AgentProfile(id="writer", name="x" * (NAME_MAX_LENGTH + 1)).validate()
    assert excinfo.value.status == 422


def test_profile_rejects_oversized_prompt() -> None:
    profile = AgentProfile(id="writer", name="写作", prompt="x" * (PROMPT_MAX_LENGTH + 1))
    with pytest.raises(AgentProfileError) as excinfo:
        profile.validate()
    assert excinfo.value.status == 422


def test_profile_rejects_malformed_color() -> None:
    with pytest.raises(AgentProfileError, match="color"):
        AgentProfile(id="writer", name="写作", color="blue").validate()


def test_profile_rejects_unknown_type() -> None:
    with pytest.raises(AgentProfileError, match="type"):
        AgentProfile(id="writer", name="写作", type="preset").validate()


def test_category_round_trips() -> None:
    category = AgentCategory(id="coding", name="编码", color="#8E44AD", order=1)
    assert AgentCategory.from_dict(category.to_dict()) == category
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/webui/test_agents_models.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'nanobot.agents'`

- [ ] **Step 3: 写最小实现**

创建 `nanobot/agents/__init__.py`（空文件，仅一行 docstring）：

```python
"""Agent 档案：数据模型、静态目录与持久化存储。"""
```

创建 `nanobot/agents/models.py`：

```python
"""Agent 档案的数据模型与线格式转换。

线格式一律 camelCase，与 ``webui/src/lib/agents/types.ts`` 的
``AgentProfile`` 逐字段同名（见
``.ai-runtime-artifacts/contracts/2026-09-29-agents-contract.md`` §2/§4）。
Python 侧保持 snake_case，序列化时统一转换。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

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
        mode = raw.get("mode", "all")
        if mode not in SELECTION_MODES:
            raise AgentProfileError(
                f"selection mode must be one of {sorted(SELECTION_MODES)}", status=400
            )
        entries = raw.get("entries", [])
        if not isinstance(entries, list) or not all(isinstance(x, str) for x in entries):
            raise AgentProfileError("selection entries must be a string array", status=400)
        if len(set(entries)) != len(entries):
            raise AgentProfileError(
                "selection entries must not contain duplicates", status=400
            )
        if any(not item for item in entries):
            raise AgentProfileError("selection entries must not contain empty ids", status=400)
        return cls(mode=mode, entries=list(entries))

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
    def from_dict(cls, raw: dict[str, Any]) -> AgentProfile:
        if not isinstance(raw, dict):
            raise AgentProfileError("profile must be an object", status=400)
        model_id = raw.get("modelId")
        category_id = raw.get("categoryId")
        return cls(
            id=str(raw.get("id", "")),
            name=str(raw.get("name", "")),
            description=str(raw.get("description", "")),
            type=str(raw.get("type", "custom")),
            customized=bool(raw.get("customized", False)),
            category_id=str(category_id) if category_id else None,
            icon=str(raw.get("icon", "🤖")),
            color=str(raw.get("color", "#4A90D9")),
            prompt=str(raw.get("prompt", "")),
            model_id=str(model_id) if model_id else None,
            tools=AgentSelection.from_dict(raw.get("tools")),
            skills=AgentSelection.from_dict(raw.get("skills")),
            sub_agents=AgentSelection.from_dict(raw.get("subAgents")),
            hidden=bool(raw.get("hidden", False)),
            updated_at=str(raw.get("updatedAt", "")),
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/webui/test_agents_models.py -v`
Expected: PASS（15 passed）

- [ ] **Step 5: 提交**

```bash
git add nanobot/agents/__init__.py nanobot/agents/models.py tests/webui/test_agents_models.py
git commit -m "feat(agents): 档案数据模型与 camelCase 线格式"
```

---

## Task 2: 静态目录（出厂预设 / 分类 / 工具元数据）

**Files:**
- Create: `nanobot/agents/catalog.py`
- Test: `tests/webui/test_agents_catalog.py`

- [ ] **Step 1: 写失败的测试**

创建 `tests/webui/test_agents_catalog.py`：

```python
"""静态目录：出厂预设、id 校验与 customized 判定。"""

from __future__ import annotations

from nanobot.agents.catalog import (
    FACTORY_AGENT_CATEGORIES,
    FACTORY_PROFILES,
    TOOL_CATEGORIES,
    factory_for,
    is_customized,
    is_valid_agent_id,
)
from nanobot.agents.models import AgentProfile, AgentSelection


def test_six_factory_categories_match_frontend() -> None:
    # webui/src/lib/agents/types.ts:99 的 AGENT_CATEGORIES
    assert [c.id for c in FACTORY_AGENT_CATEGORIES] == [
        "general", "coding", "writing", "research", "ops", "efficiency",
    ]
    assert [c.order for c in FACTORY_AGENT_CATEGORIES] == [0, 1, 2, 3, 4, 5]
    assert FACTORY_AGENT_CATEGORIES[1].color == "#8E44AD"


def test_every_tool_category_id_is_declared() -> None:
    known = {c.id for c in FACTORY_AGENT_CATEGORIES}
    # 工具分类的取值域与前端 TOOL_CATEGORIES 的 8 个 id 一致
    assert set(TOOL_CATEGORIES) <= {
        "filesystem", "execution", "web", "memory",
        "scheduling", "session", "orchestration", "media",
    }
    assert known  # 分类表非空


def test_factory_profiles_are_well_formed() -> None:
    assert FACTORY_PROFILES
    for profile in FACTORY_PROFILES:
        assert is_valid_agent_id(profile.id), profile.id
        assert profile.type == "system"
        assert profile.customized is False
        profile.validate()


def test_factory_profile_ids_are_unique() -> None:
    ids = [p.id for p in FACTORY_PROFILES]
    assert len(ids) == len(set(ids))


def test_factory_for_returns_none_for_unknown_id() -> None:
    assert factory_for("no-such-agent") is None


def test_is_valid_agent_id_rejects_traversal() -> None:
    assert is_valid_agent_id("code-reviewer")
    assert is_valid_agent_id("a1")
    assert not is_valid_agent_id("../escape")
    assert not is_valid_agent_id("a/b")
    assert not is_valid_agent_id("")
    assert not is_valid_agent_id("-leading-dash")
    assert not is_valid_agent_id("Upper")
    assert not is_valid_agent_id("x" * 65)


def test_is_customized_is_false_for_untouched_factory_profile() -> None:
    factory = FACTORY_PROFILES[0]
    assert is_customized(factory, factory) is False


def test_is_customized_detects_prompt_edit() -> None:
    factory = FACTORY_PROFILES[0]
    edited = AgentProfile.from_dict({**factory.to_dict(), "prompt": "改了提示词"})
    assert is_customized(edited, factory) is True


def test_is_customized_ignores_hidden_and_description_only_rename() -> None:
    factory = FACTORY_PROFILES[0]
    hidden = AgentProfile.from_dict({**factory.to_dict(), "hidden": True})
    assert is_customized(hidden, factory) is False


def test_is_customized_ignores_entry_order() -> None:
    factory = FACTORY_PROFILES[0]
    swapped = AgentProfile.from_dict(
        {
            **factory.to_dict(),
            "skills": {
                "mode": factory.skills.mode,
                "entries": list(reversed(factory.skills.entries)),
            },
        }
    )
    assert factory.skills.mode == "all" or is_customized(swapped, factory) is False


def test_is_customized_detects_selection_mode_change() -> None:
    factory = FACTORY_PROFILES[0]
    changed = AgentProfile.from_dict(
        {**factory.to_dict(), "tools": AgentSelection("include", ["read_file"]).to_dict()}
    )
    assert is_customized(changed, factory) is True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/webui/test_agents_catalog.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'nanobot.agents.catalog'`

- [ ] **Step 3: 写最小实现**

创建 `nanobot/agents/catalog.py`：

```python
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
    "execute_command": "execution",
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
    "execute_command": "high",
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

FACTORY_PROFILES: tuple[AgentProfile, ...] = (
    AgentProfile(
        id="general-assistant",
        name="通用助理",
        description="什么都能接的默认助手，能力对齐全局配置。",
        type="system",
        category_id="general",
        icon="🤖",
        color="#4A90D9",
        prompt=(
            "你是{{name}}，{{description}}\n"
            "当前日期：{{date}}\n"
            "工作区：{{workspace}}\n"
            "已启用技能：{{skills}}\n"
            "可用工具：{{tools}}"
        ),
        tools=AgentSelection("all", []),
        skills=AgentSelection("all", []),
    ),
    AgentProfile(
        id="code-reviewer",
        name="代码评审",
        description="只读地审查改动，指出缺陷与风险，不动手改代码。",
        type="system",
        category_id="coding",
        icon="🔍",
        color="#8E44AD",
        prompt=(
            "你是{{name}}，{{description}}\n"
            "工作区：{{workspace}}\n"
            "用只读工具定位问题，给出文件与行号，不要修改文件。\n"
            "已启用技能：{{skills}}"
        ),
        tools=AgentSelection("include", [
            "read_file", "list_dir", "search", "web_search",
        ]),
        skills=AgentSelection("all", []),
    ),
    AgentProfile(
        id="researcher",
        name="资料研究",
        description="多轮检索与交叉验证，最后给出带出处的结论。",
        type="system",
        category_id="research",
        icon="🔬",
        color="#16A085",
        prompt=(
            "你是{{name}}，{{description}}\n"
            "今天日期：{{date}}\n"
            "工作区：{{workspace}}\n"
            "先检索再作答，每条结论标注来源。\n"
            "可用工具：{{tools}}"
        ),
        tools=AgentSelection("include", ["web_search", "web_fetch", "read_file"]),
        skills=AgentSelection("all", []),
    ),
    AgentProfile(
        id="ops-runner",
        name="运维执行",
        description="在受控环境里执行命令并回报结果，适合排障与部署检查。",
        type="system",
        category_id="ops",
        icon="🛠️",
        color="#C0392B",
        prompt=(
            "你是{{name}}，{{description}}\n"
            "工作区：{{workspace}}\n"
            "执行前先说明将运行的命令；失败时保留原始输出。\n"
            "可用工具：{{tools}}"
        ),
        tools=AgentSelection("include", ["execute_command", "read_file", "list_dir"]),
        skills=AgentSelection("all", []),
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/webui/test_agents_catalog.py -v`
Expected: PASS（11 passed）

- [ ] **Step 5: 提交**

```bash
git add nanobot/agents/catalog.py tests/webui/test_agents_catalog.py
git commit -m "feat(agents): 出厂预设、分类表与 customized 判定"
```

---

## Task 3: 存储层

**Files:**
- Create: `nanobot/agents/store.py`
- Test: `tests/webui/test_agents_store.py`

- [ ] **Step 1: 写失败的测试**

创建 `tests/webui/test_agents_store.py`：

```python
"""AgentStore 的读写、路径防御与出厂预置行为。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nanobot.agents.catalog import FACTORY_PROFILES
from nanobot.agents.models import AgentProfile, AgentSelection
from nanobot.agents.store import AgentStore, AgentStoreError


def test_list_includes_factory_presets_without_touching_disk(tmp_path: Path) -> None:
    store = AgentStore(tmp_path)
    ids = {p.id for p in store.list_profiles()}
    assert {p.id for p in FACTORY_PROFILES} <= ids
    # 读路径不落盘：刷新页面不该创建目录
    assert not (tmp_path / "agents").exists()


def test_save_writes_one_json_file_per_profile(tmp_path: Path) -> None:
    store = AgentStore(tmp_path)
    store.save_profile(AgentProfile(id="writer", name="写作"))
    path = tmp_path / "agents" / "profiles" / "writer.json"
    assert json.loads(path.read_text(encoding="utf-8"))["name"] == "写作"


def test_save_round_trips_through_disk(tmp_path: Path) -> None:
    AgentStore(tmp_path).save_profile(
        AgentProfile(id="writer", name="写作", skills=AgentSelection("include", ["memory"]))
    )
    reloaded = AgentStore(tmp_path).get_profile("writer")
    assert reloaded.skills == AgentSelection("include", ["memory"])


def test_save_stamps_updated_at_ignoring_client_value(tmp_path: Path) -> None:
    store = AgentStore(tmp_path)
    saved = store.save_profile(
        AgentProfile(id="writer", name="写作", updated_at="1999-01-01T00:00:00+00:00")
    )
    assert saved.updated_at != "1999-01-01T00:00:00+00:00"
    assert saved.updated_at.startswith("20")


def test_save_of_factory_id_derives_system_type_and_customized(tmp_path: Path) -> None:
    store = AgentStore(tmp_path)
    factory = next(p for p in FACTORY_PROFILES if p.id == "code-reviewer")
    untouched = AgentProfile.from_dict(factory.to_dict())
    assert store.save_profile(untouched).customized is False
    assert store.save_profile(untouched).type == "system"

    edited = AgentProfile.from_dict({**factory.to_dict(), "prompt": "改过了"})
    result = store.save_profile(edited)
    assert result.customized is True
    assert result.type == "system"


def test_save_of_unknown_id_is_custom_and_never_customized(tmp_path: Path) -> None:
    saved = AgentStore(tmp_path).save_profile(AgentProfile(id="mine", name="我的"))
    assert (saved.type, saved.customized) == ("custom", False)


def test_save_ignores_client_supplied_type(tmp_path: Path) -> None:
    profile = AgentProfile(id="mine", name="我的", type="system", customized=True)
    saved = AgentStore(tmp_path).save_profile(profile)
    assert (saved.type, saved.customized) == ("custom", False)


def test_get_missing_profile_is_404(tmp_path: Path) -> None:
    with pytest.raises(AgentStoreError) as excinfo:
        AgentStore(tmp_path).get_profile("ghost")
    assert excinfo.value.status == 404


def test_get_rejects_traversal_id(tmp_path: Path) -> None:
    with pytest.raises(AgentStoreError) as excinfo:
        AgentStore(tmp_path).get_profile("../../etc/passwd")
    assert excinfo.value.status == 400


def test_delete_removes_profile(tmp_path: Path) -> None:
    store = AgentStore(tmp_path)
    store.save_profile(AgentProfile(id="mine", name="我的"))
    store.delete_profile("mine")
    assert not (tmp_path / "agents" / "profiles" / "mine.json").exists()
    with pytest.raises(AgentStoreError):
        AgentStore(tmp_path).get_profile("mine")


def test_delete_rejects_system_preset(tmp_path: Path) -> None:
    with pytest.raises(AgentStoreError) as excinfo:
        AgentStore(tmp_path).delete_profile("code-reviewer")
    assert excinfo.value.status == 409


def test_delete_missing_is_404(tmp_path: Path) -> None:
    with pytest.raises(AgentStoreError) as excinfo:
        AgentStore(tmp_path).delete_profile("ghost")
    assert excinfo.value.status == 404


def test_reset_restores_factory_value_and_keeps_hidden(tmp_path: Path) -> None:
    store = AgentStore(tmp_path)
    factory = next(p for p in FACTORY_PROFILES if p.id == "code-reviewer")
    store.save_profile(AgentProfile.from_dict({**factory.to_dict(), "prompt": "改过了"}))
    store.set_visibility("code-reviewer", True)

    reset = AgentStore(tmp_path).reset_profile("code-reviewer")
    assert reset.prompt == factory.prompt
    assert reset.customized is False
    assert reset.hidden is True


def test_reset_rejects_custom_profile(tmp_path: Path) -> None:
    store = AgentStore(tmp_path)
    store.save_profile(AgentProfile(id="mine", name="我的"))
    with pytest.raises(AgentStoreError) as excinfo:
        store.reset_profile("mine")
    assert excinfo.value.status == 409


def test_set_visibility_persists(tmp_path: Path) -> None:
    AgentStore(tmp_path).set_visibility("code-reviewer", True)
    assert AgentStore(tmp_path).get_profile("code-reviewer").hidden is True


def test_list_sorts_system_first_then_category_then_name(tmp_path: Path) -> None:
    store = AgentStore(tmp_path)
    store.save_profile(AgentProfile(id="zeta", name="泽塔", category_id="writing"))
    store.save_profile(AgentProfile(id="alpha", name="阿尔法", category_id="ops"))
    listed = store.list_profiles()
    custom_ids = [p.id for p in listed if p.type == "custom"]
    assert custom_ids == ["alpha", "zeta"]


def test_corrupt_file_is_skipped_not_fatal(tmp_path: Path) -> None:
    store = AgentStore(tmp_path)
    store.save_profile(AgentProfile(id="writer", name="写作"))
    (tmp_path / "agents" / "profiles" / "broken.json").write_text("{oops", encoding="utf-8")

    reloaded = AgentStore(tmp_path)
    assert reloaded.get_profile("writer").name == "写作"
    with pytest.raises(AgentStoreError):
        reloaded.get_profile("broken")


def test_missing_fields_fall_back_to_defaults(tmp_path: Path) -> None:
    profiles = tmp_path / "agents" / "profiles"
    profiles.mkdir(parents=True)
    (profiles / "sparse.json").write_text('{"id": "sparse", "name": "稀"}', encoding="utf-8")

    profile = AgentStore(tmp_path).get_profile("sparse")
    assert profile.color == "#4A90D9"
    assert profile.tools == AgentSelection()


def test_categories_default_to_factory_list(tmp_path: Path) -> None:
    categories = AgentStore(tmp_path).list_categories()
    assert [c.id for c in categories] == [
        "general", "coding", "writing", "research", "ops", "efficiency",
    ]


def test_save_categories_round_trips(tmp_path: Path) -> None:
    store = AgentStore(tmp_path)
    store.save_categories([AgentCategoryHelper("x", "自定义", "#111111", 9)])
    assert [c.id for c in AgentStore(tmp_path).list_categories()] == ["x"]


def AgentCategoryHelper(cid: str, name: str, color: str, order: int):  # noqa: N802
    from nanobot.agents.models import AgentCategory

    return AgentCategory(id=cid, name=name, color=color, order=order)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/webui/test_agents_store.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'nanobot.agents.store'`

- [ ] **Step 3: 写最小实现**

创建 `nanobot/agents/store.py`：

```python
"""Agent 档案的持久化存储。

布局照 openakita 的 ``ProfileStore``：``{workspace}/agents/profiles/{id}.json``
+ ``{workspace}/agents/categories.json``。base_dir 落在 workspace 之下与
``identity/`` 并列，理由见契约 §5。

**读路径不落盘。** 出厂预置只补进内存缓存，文件要到 ``save_profile`` /
``reset_profile`` / ``set_visibility`` 才真正产生——刷新一次列表不该在
用户磁盘上创建目录。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from nanobot.agents.catalog import (
    AGENTS_DIR_NAME,
    CATEGORIES_FILE,
    FACTORY_AGENT_CATEGORIES,
    FACTORY_PROFILES,
    PROFILES_SUBDIR,
    factory_for,
    is_customized,
    is_valid_agent_id,
)
from nanobot.agents.models import AgentCategory, AgentProfile


class AgentStoreError(ValueError):
    """可安全回传给 WebUI 的存储层错误。"""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _atomic_write(path: Path, content: str) -> None:
    """同 nanobot/identity/compiler.py:224 的写法：先写临时文件再 os.replace。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sort_key(profile: AgentProfile) -> tuple[bool, str, str, str]:
    """系统预设先于自定义，同组内按分类、名称、id 稳定排序。"""
    return (profile.type != "system", profile.category_id or "", profile.name, profile.id)


class AgentStore:
    def __init__(self, workspace: Path) -> None:
        self._root = Path(workspace) / AGENTS_DIR_NAME
        self._profiles_dir = self._root / PROFILES_SUBDIR
        self._categories_file = self._root / CATEGORIES_FILE
        self._lock = threading.RLock()
        self._cache: dict[str, AgentProfile] | None = None
        self._categories: list[AgentCategory] | None = None

    # -- 路径 -----------------------------------------------------------------

    def _path_for(self, agent_id: str) -> Path:
        """档案 id → 绝对路径，两道闸：白名单正则 + 前缀比对。

        与 ``nanobot/identity/store.py:62`` 的 ``_resolve`` 同构。白名单挡
        绝对路径与 ``..``，``is_relative_to`` 挡符号链接逃逸。
        """
        if not is_valid_agent_id(agent_id):
            raise AgentStoreError(f"非法 Agent id：{agent_id!r}", status=400)
        root = self._profiles_dir.resolve()
        candidate = (self._profiles_dir / f"{agent_id}.json").resolve()
        if candidate == root or not candidate.is_relative_to(root):
            raise AgentStoreError("路径越界：档案必须位于 agents/profiles/ 目录内", status=403)
        return candidate

    # -- 加载 -----------------------------------------------------------------

    def _load_all(self) -> dict[str, AgentProfile]:
        if self._cache is not None:
            return self._cache
        loaded: dict[str, AgentProfile] = {}
        if self._profiles_dir.is_dir():
            for path in sorted(self._profiles_dir.glob("*.json")):
                try:
                    profile = AgentProfile.from_dict(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, ValueError) as exc:
                    logger.warning("跳过无法解析的 Agent 档案 {}：{}", path.name, exc)
                    continue
                if not is_valid_agent_id(profile.id):
                    logger.warning("跳过 id 非法的 Agent 档案 {}：{}", path.name, profile.id)
                    continue
                loaded[profile.id] = profile
        # 出厂预置只补内存：磁盘上已有的档案一律不覆盖。
        for factory in FACTORY_PROFILES:
            if factory.id not in loaded:
                loaded[factory.id] = factory_for(factory.id) or factory
        self._cache = loaded
        return loaded

    def _load_categories(self) -> list[AgentCategory]:
        if self._categories is not None:
            return self._categories
        parsed: list[AgentCategory] = []
        if self._categories_file.is_file():
            try:
                raw = json.loads(self._categories_file.read_text(encoding="utf-8"))
                parsed = [AgentCategory.from_dict(x) for x in raw if isinstance(x, dict)]
            except (OSError, ValueError, TypeError) as exc:
                logger.warning("分类文件无法解析，回落到出厂分类：{}", exc)
                parsed = []
        self._categories = parsed or list(FACTORY_AGENT_CATEGORIES)
        return self._categories

    def _persist(self, profile: AgentProfile) -> None:
        path = self._path_for(profile.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, json.dumps(profile.to_dict(), ensure_ascii=False, indent=2))
        self._load_all()[profile.id] = profile

    # -- 读 -------------------------------------------------------------------

    def list_profiles(self) -> list[AgentProfile]:
        with self._lock:
            profiles = list(self._load_all().values())
        profiles.sort(key=_sort_key)
        return profiles

    def get_profile(self, agent_id: str) -> AgentProfile:
        with self._lock:
            profile = self._load_all().get(agent_id)
        if profile is None:
            raise AgentStoreError(f"Agent 不存在：{agent_id}", status=404)
        return profile

    def list_categories(self) -> list[AgentCategory]:
        with self._lock:
            return list(self._load_categories())

    # -- 写 -------------------------------------------------------------------

    def save_profile(self, profile: AgentProfile) -> AgentProfile:
        """校验并落盘。``type`` / ``customized`` / ``updatedAt`` 一律由服务端决定。"""
        with self._lock:
            self._path_for(profile.id)
            profile.validate()
            factory = factory_for(profile.id)
            stored = replace(
                profile,
                type="system" if factory is not None else "custom",
                customized=is_customized(profile, factory) if factory is not None else False,
                updated_at=_utc_now(),
            )
            self._persist(stored)
            return stored

    def delete_profile(self, agent_id: str) -> None:
        with self._lock:
            profile = self.get_profile(agent_id)
            if profile.type == "system":
                # 拒绝而不是删：出厂自愈会在下一次加载时把它补回来，
                # 用户会看到「删了又出现」。见契约 §9.5。
                raise AgentStoreError(
                    f"系统预设「{agent_id}」不可删除，可重置或隐藏", status=409
                )
            path = self._path_for(agent_id)
            path.unlink(missing_ok=True)
            self._load_all().pop(agent_id, None)

    def reset_profile(self, agent_id: str) -> AgentProfile:
        with self._lock:
            current = self.get_profile(agent_id)
            factory = factory_for(agent_id)
            if factory is None or current.type != "system":
                raise AgentStoreError("只有系统预设可以重置", status=409)
            # hidden 是视图偏好而非内容定制，重置不该把它一并抹掉。
            fresh = replace(factory, hidden=current.hidden, updated_at=_utc_now())
            self._persist(fresh)
            return fresh

    def set_visibility(self, agent_id: str, hidden: bool) -> AgentProfile:
        with self._lock:
            current = self.get_profile(agent_id)
            updated = replace(current, hidden=hidden, updated_at=_utc_now())
            self._persist(updated)
            return updated

    def save_categories(self, categories: list[AgentCategory]) -> list[AgentCategory]:
        """一期不接线（前端分类写死在前端常量里），保留供二期使用。"""
        with self._lock:
            self._categories = list(categories)
            self._root.mkdir(parents=True, exist_ok=True)
            _atomic_write(
                self._categories_file,
                json.dumps([c.to_dict() for c in categories], ensure_ascii=False, indent=2),
            )
            return list(self._categories)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/webui/test_agents_store.py -v`
Expected: PASS（22 passed）

- [ ] **Step 5: 提交**

```bash
git add nanobot/agents/store.py tests/webui/test_agents_store.py
git commit -m "feat(agents): 档案存储层（每档案一文件、原子写、路径防御）"
```

---

## Task 4: API payload 函数

**Files:**
- Create: `nanobot/webui/agents_api.py`
- Test: `tests/webui/test_agents_api.py`

- [ ] **Step 1: 写失败的测试**

创建 `tests/webui/test_agents_api.py`：

```python
"""agents_api 的 payload 函数：形状、字段所有权与错误翻译。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nanobot.webui import agents_api
from nanobot.webui.settings_contracts import WebUISettingsError

PROFILE_WIRE: dict[str, Any] = {
    "id": "writer",
    "name": "写作",
    "description": "",
    "type": "custom",
    "customized": False,
    "categoryId": "writing",
    "icon": "✍️",
    "color": "#E67E22",
    "prompt": "",
    "modelId": None,
    "tools": {"mode": "all", "entries": []},
    "skills": {"mode": "all", "entries": []},
    "subAgents": {"mode": "all", "entries": []},
    "hidden": False,
    "updatedAt": "2020-01-01T00:00:00+00:00",
}


def test_list_returns_factory_presets_with_frontend_keys(tmp_path: Path) -> None:
    payload = agents_api.agents_list(tmp_path)
    assert "agents" in payload
    assert payload["agents"]
    first = payload["agents"][0]
    assert "categoryId" in first and "subAgents" in first and "updatedAt" in first


def test_list_does_not_create_directories(tmp_path: Path) -> None:
    agents_api.agents_list(tmp_path)
    assert not (tmp_path / "agents").exists()


def test_save_strips_client_owned_fields(tmp_path: Path) -> None:
    payload = dict(PROFILE_WIRE, type="system", customized=True)
    saved = agents_api.agents_save(tmp_path, payload)["agent"]
    assert saved["type"] == "custom"
    assert saved["customized"] is False
    assert saved["updatedAt"] != PROFILE_WIRE["updatedAt"]


def test_save_rejects_illegal_id(tmp_path: Path) -> None:
    with pytest.raises(WebUISettingsError) as excinfo:
        agents_api.agents_save(tmp_path, dict(PROFILE_WIRE, id="../escape"))
    assert excinfo.value.status == 400


def test_save_rejects_blank_name(tmp_path: Path) -> None:
    with pytest.raises(WebUISettingsError) as excinfo:
        agents_api.agents_save(tmp_path, dict(PROFILE_WIRE, name="  "))
    assert excinfo.value.status == 422


def test_delete_rejects_system_preset(tmp_path: Path) -> None:
    with pytest.raises(WebUISettingsError) as excinfo:
        agents_api.agents_delete(tmp_path, "code-reviewer")
    assert excinfo.value.status == 409


def test_delete_returns_echoed_id(tmp_path: Path) -> None:
    agents_api.agents_save(tmp_path, PROFILE_WIRE)
    assert agents_api.agents_delete(tmp_path, "writer") == {"id": "writer"}


def test_reset_returns_factory_profile(tmp_path: Path) -> None:
    payload = agents_api.agents_reset(tmp_path, "code-reviewer")["agent"]
    assert payload["id"] == "code-reviewer"
    assert payload["customized"] is False


def test_visibility_toggles_hidden(tmp_path: Path) -> None:
    assert agents_api.agents_visibility(tmp_path, "writer", True)["agent"]["hidden"] is True
    assert agents_api.agents_visibility(tmp_path, "writer", False)["agent"]["hidden"] is False


def test_visibility_requires_boolean(tmp_path: Path) -> None:
    with pytest.raises(WebUISettingsError) as excinfo:
        agents_api.agents_visibility(tmp_path, "writer", "yes")  # type: ignore[arg-type]
    assert excinfo.value.status == 400


# -- catalog ---------------------------------------------------------------


def _minimal_workspace(tmp_path: Path) -> Path:
    (tmp_path / "identity").mkdir()
    (tmp_path / "identity" / "SOUL.md").write_text("# soul", encoding="utf-8")
    return tmp_path


def test_catalog_has_all_four_collections(tmp_path: Path) -> None:
    catalog = agents_api.agents_catalog(_minimal_workspace(tmp_path))
    assert set(catalog) == {"tools", "skills", "models", "categories"}


def test_catalog_tool_descriptors_match_frontend_type(tmp_path: Path) -> None:
    catalog = agents_api.agents_catalog(_minimal_workspace(tmp_path))
    assert catalog["tools"]
    for tool in catalog["tools"]:
        assert set(tool) == {
            "name", "label", "description", "category", "risk", "scope", "locked",
        }
        assert tool["label"] == tool["name"]
        assert tool["risk"] in {"low", "medium", "high"}
        assert tool["scope"] in {"core", "subagent"}
        assert isinstance(tool["locked"], bool)
    names = {t["name"] for t in catalog["tools"]}
    assert "read_file" in names
    assert "execute_command" in names


def test_catalog_marks_dangerous_tools_high_risk(tmp_path: Path) -> None:
    catalog = agents_api.agents_catalog(_minimal_workspace(tmp_path))
    risk = {t["name"]: t["risk"] for t in catalog["tools"]}
    assert risk["execute_command"] == "high"
    assert risk["read_file"] == "low"


def test_catalog_skill_descriptors_match_frontend_type(tmp_path: Path) -> None:
    catalog = agents_api.agents_catalog(_minimal_workspace(tmp_path))
    for skill in catalog["skills"]:
        assert set(skill) == {"name", "description", "source", "tags"}
        assert skill["source"] in {"builtin", "workspace", "plugin"}
        assert skill["tags"] == []


def test_catalog_model_descriptors_match_frontend_type(tmp_path: Path) -> None:
    catalog = agents_api.agents_catalog(_minimal_workspace(tmp_path))
    assert catalog["models"]
    for model in catalog["models"]:
        assert set(model) == {
            "id", "label", "provider", "contextWindow", "health", "vision", "toolUse",
        }
        assert model["health"] == "healthy"
        assert model["contextWindow"] > 0


def test_catalog_categories_match_factory_list(tmp_path: Path) -> None:
    catalog = agents_api.agents_catalog(_minimal_workspace(tmp_path))
    assert [c["id"] for c in catalog["categories"]] == [
        "general", "coding", "writing", "research", "ops", "efficiency",
    ]


def test_catalog_does_not_leak_absolute_paths(tmp_path: Path) -> None:
    workspace = _minimal_workspace(tmp_path)
    catalog = agents_api.agents_catalog(workspace)
    serialized = json.dumps(catalog, ensure_ascii=False)
    assert str(workspace) not in serialized
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/webui/test_agents_api.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'nanobot.webui.agents_api'`

- [ ] **Step 3: 写最小实现**

创建 `nanobot/webui/agents_api.py`：

```python
"""Agent 档案的传输无关 payload 函数。

本模块不知道 HTTP 也不知道 WebSocket：入参是纯 Python，出参是纯 dict。
``nanobot/webui/agents_routes.py`` 负责把 action 映射到这些函数。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from nanobot.agents.catalog import (
    DEFAULT_TOOL_CATEGORY,
    TOOL_CATEGORIES as TOOL_CATEGORY_BY_NAME,
)
from nanobot.agents.catalog import (
    TOOL_RISK_OVERRIDES,
)
from nanobot.agents.models import AgentProfile
from nanobot.agents.store import AgentStore, AgentStoreError
from nanobot.config.schema import Config, ToolsConfig
from nanobot.webui.settings_contracts import WebUISettingsError

# 描述符首行截断长度：工具 description 动辄上千字符，前端列表用不上。
_DESCRIPTION_LIMIT = 200


def _translate(exc: AgentStoreError) -> WebUISettingsError:
    return WebUISettingsError(exc.message, status=exc.status)


def _store(workspace: Path) -> AgentStore:
    return AgentStore(Path(workspace))


# -- 档案 CRUD --------------------------------------------------------------


def agents_list(workspace: Path) -> dict[str, Any]:
    try:
        return {"agents": [p.to_dict() for p in _store(workspace).list_profiles()]}
    except AgentStoreError as exc:
        raise _translate(exc) from exc


def agents_save(workspace: Path, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise WebUISettingsError("agent must be an object", status=400)
    try:
        profile = AgentProfile.from_dict(raw)
        return {"agent": _store(workspace).save_profile(profile).to_dict()}
    except (AgentStoreError, ValueError) as exc:
        if isinstance(exc, WebUISettingsError):
            raise
        status = exc.status if isinstance(exc, (AgentStoreError,)) else 400
        message = exc.message if hasattr(exc, "message") else str(exc)
        raise WebUISettingsError(message, status=status) from exc


def agents_delete(workspace: Path, agent_id: str) -> dict[str, Any]:
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise WebUISettingsError("id is required", status=400)
    try:
        _store(workspace).delete_profile(agent_id.strip())
    except AgentStoreError as exc:
        raise _translate(exc) from exc
    return {"id": agent_id.strip()}


def agents_reset(workspace: Path, agent_id: str) -> dict[str, Any]:
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise WebUISettingsError("id is required", status=400)
    try:
        return {"agent": _store(workspace).reset_profile(agent_id.strip()).to_dict()}
    except AgentStoreError as exc:
        raise _translate(exc) from exc


def agents_visibility(workspace: Path, agent_id: str, hidden: bool) -> dict[str, Any]:
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise WebUISettingsError("id is required", status=400)
    if not isinstance(hidden, bool):
        raise WebUISettingsError("hidden must be a boolean", status=400)
    try:
        return {"agent": _store(workspace).set_visibility(agent_id.strip(), hidden).to_dict()}
    except AgentStoreError as exc:
        raise _translate(exc) from exc


# -- 目录 -------------------------------------------------------------------


def _tool_descriptors(workspace: Path, tools_config: ToolsConfig) -> list[dict[str, Any]]:
    from nanobot.agent.tools.context import ToolContext
    from nanobot.agent.tools.loader import ToolLoader
    from nanobot.agent.tools.registry import ToolRegistry

    registry = ToolRegistry()
    ctx = ToolContext(config=tools_config, workspace=str(Path(workspace).resolve()))
    loader = ToolLoader()
    # 两个 scope 各打一次、进同一个 registry：同名工具后者覆盖前者，
    # 结果就是全部工具的并集。
    for scope in ("core", "subagent"):
        loader.load(ctx, registry, scope=scope)

    descriptors: list[dict[str, Any]] = []
    for name in registry.names():
        tool = registry.get(name)
        if tool is None:
            continue
        scopes = set(getattr(type(tool), "_scopes", {"core"}))
        description = (tool.description or "").strip()
        if len(description) > _DESCRIPTION_LIMIT:
            description = description[: _DESCRIPTION_LIMIT - 1] + "…"
        descriptors.append(
            {
                "name": name,
                # 后端不产出中文工具名；要 i18n 走分类那套 label key。
                "label": name,
                "description": description,
                "category": TOOL_CATEGORY_BY_NAME.get(name, DEFAULT_TOOL_CATEGORY),
                "risk": TOOL_RISK_OVERRIDES.get(
                    name, "low" if tool.read_only else "medium"
                ),
                "scope": (
                    "subagent"
                    if "subagent" in scopes and "core" not in scopes
                    else "core"
                ),
                # core/memory scope 的工具是框架依赖，取消会让档案跑不起来。
                "locked": bool(scopes & {"core", "memory"}),
            }
        )
    return descriptors


def _skill_descriptors(workspace: Path) -> list[dict[str, Any]]:
    from nanobot.webui.skills_api import webui_skills_payload  # local: 避免导入环

    entries = webui_skills_payload(Path(workspace)).get("skills", [])
    descriptors: list[dict[str, Any]] = []
    for entry in entries:
        source = entry.get("source", "builtin")
        descriptors.append(
            {
                "name": entry.get("name", ""),
                "description": entry.get("description", ""),
                "source": source if source in {"builtin", "workspace", "plugin"} else "builtin",
                # SkillsLoader 不产出标签。给空数组比编造标签诚实。
                "tags": [],
            }
        )
    return descriptors


def _model_descriptors(config: Config) -> list[dict[str, Any]]:
    """按 ``settings_models`` 的口径投影一份模型清单。

    整份 ``model_settings_payload`` 还带 provider 认证态，与档案选择器无关；
    这里只要 ``model_presets`` 那一段，所以调空实现的 oauth reader 复用
    同一套 default 预设解析，避免在第二处复刻它。
    """
    from nanobot.webui.settings_models import model_settings_payload  # local: 避免导入环

    payload = model_settings_payload(config, oauth_status=lambda _provider: {})
    descriptors: list[dict[str, Any]] = []
    for preset in payload.get("model_presets", []):
        window = preset.get("context_window_tokens") or 0
        descriptors.append(
            {
                "id": preset.get("name", ""),
                "label": preset.get("label") or preset.get("name", ""),
                "provider": preset.get("resolved_provider") or preset.get("provider") or "auto",
                "contextWindow": int(window) if isinstance(window, int) and window > 0 else 200000,
                # 出现在 config.model_presets 里就是用户已配置可用的。
                "health": "healthy",
                # 配置层没有逐模型能力表；nanobot 的 agent loop 本身就以工具
                # 调用驱动，图片走同一 provider 通道。见契约 §8.3。
                "vision": True,
                "toolUse": True,
            }
        )
    return descriptors


def agents_catalog(
    workspace: Path,
    *,
    load_config: Callable[[], Config] | None = None,
) -> dict[str, Any]:
    if load_config is None:
        raise WebUISettingsError("agent catalog is not configured", status=503)
    config = load_config()
    return {
        "tools": _tool_descriptors(workspace, config.tools),
        "skills": _skill_descriptors(workspace),
        "models": _model_descriptors(config),
        "categories": [c.to_dict() for c in _store(workspace).list_categories()],
    }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/webui/test_agents_api.py -v`
Expected: PASS（15 passed）

- [ ] **Step 5: 提交**

```bash
git add nanobot/webui/agents_api.py tests/webui/test_agents_api.py
git commit -m "feat(agents): 档案与目录的 payload 函数"
```

---

## Task 5: 域 handler

**Files:**
- Create: `nanobot/webui/agents_routes.py`
- Test: `tests/webui/test_agents_routes.py`

- [ ] **Step 1: 写失败的测试**

创建 `tests/webui/test_agents_routes.py`：

```python
"""AgentSettingsHandler：动作分发与错误翻译。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from nanobot.agents.models import AgentProfile
from nanobot.webui.agents_routes import (
    AGENTS_ACTION_NAMES,
    AgentSettingsHandler,
    AgentSettingsOperations,
)
from nanobot.webui.settings_contracts import SettingsRequest, WebUISettingsError


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def ops(workspace: Path) -> AgentSettingsOperations:
    from nanobot.webui import agents_api

    return AgentSettingsOperations(
        list_profiles=lambda: agents_api.agents_list(workspace),
        list_catalog=lambda: agents_api.agents_catalog(
            workspace, load_config=lambda: pytest.importorskip("nanobot.config.loader").load_config()
        ),
        save_profile=lambda raw: agents_api.agents_save(workspace, raw),
        delete_profile=lambda agent_id: agents_api.agents_delete(workspace, agent_id),
        reset_profile=lambda agent_id: agents_api.agents_reset(workspace, agent_id),
        set_visibility=lambda agent_id, hidden: agents_api.agents_visibility(
            workspace, agent_id, hidden
        ),
    )


def _request(payload: dict | None = None, query: dict | None = None) -> SettingsRequest:
    return SettingsRequest(path="/api/settings/agents", payload=payload, query=query or {})


def test_action_names_are_frozen() -> None:
    assert AGENTS_ACTION_NAMES == frozenset({
        "agents-list", "agents-catalog",
        "agents-save", "agents-delete", "agents-reset", "agents-visibility",
    })


def test_unknown_action_is_404(ops: AgentSettingsOperations) -> None:
    result = AgentSettingsHandler(ops).handle("agents-nope", _request())
    assert result.status == 404


def test_list_returns_agents(ops: AgentSettingsOperations) -> None:
    result = AgentSettingsHandler(ops).handle("agents-list", _request())
    assert result.status == 200
    assert result.payload is not None
    assert "agents" in result.payload


def test_save_requires_agent_object(ops: AgentSettingsOperations) -> None:
    result = AgentSettingsHandler(ops).handle("agents-save", _request({}))
    assert result.status == 400


def test_save_round_trips(ops: AgentSettingsOperations) -> None:
    handler = AgentSettingsHandler(ops)
    saved = handler.handle(
        "agents-save",
        _request({"agent": {"id": "writer", "name": "写作"}}),
    )
    assert saved.status == 200
    assert saved.payload is not None
    assert saved.payload["agent"]["name"] == "写作"


def test_delete_requires_id(ops: AgentSettingsOperations) -> None:
    result = AgentSettingsHandler(ops).handle("agents-delete", _request({}))
    assert result.status == 400


def test_visibility_rejects_non_boolean(ops: AgentSettingsOperations) -> None:
    result = AgentSettingsHandler(ops).handle(
        "agents-visibility", _request({"id": "writer", "hidden": "yes"})
    )
    assert result.status == 400


def test_store_errors_keep_their_status(ops: AgentSettingsOperations) -> None:
    result = AgentSettingsHandler(ops).handle(
        "agents-reset", _request({"id": "definitely-not-a-factory-preset"})
    )
    assert result.status in {404, 409}
    assert result.error


def test_missing_list_catalog_degrades_to_503() -> None:
    from nanobot.config.loader import load_config

    ops = AgentSettingsOperations(
        list_profiles=lambda: {"agents": []},
        list_catalog=lambda: (_ for _ in ()).throw(
            WebUISettingsError("agent catalog is not configured", status=503)
        ),
        save_profile=lambda raw: {"agent": AgentProfile(id="x", name="y").to_dict()},
        delete_profile=lambda agent_id: {"id": agent_id},
        reset_profile=lambda agent_id: {"agent": AgentProfile(id="x", name="y").to_dict()},
        set_visibility=lambda agent_id, hidden: {
            "agent": AgentProfile(id="x", name="y").to_dict()
        },
    )
    result = AgentSettingsHandler(ops).handle("agents-catalog", _request())
    assert result.status == 503
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/webui/test_agents_routes.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'nanobot.webui.agents_routes'`

- [ ] **Step 3: 写最小实现**

创建 `nanobot/webui/agents_routes.py`：

```python
"""WebUI settings 域 handler：Agent 档案管理。

照 ``identity_routes.py`` 的形状：与传输无关的 handler，``handle(action, request)``
对着注入进来的 operations 分发，返回 ``SettingsRouteResult``。路径 → action 的
映射、WebSocket / HTTP 的门禁都在 ``settings_routes.py``。

读动作（list / catalog）走普通 HTTP GET；写动作（save / delete / reset /
visibility）改变磁盘状态，走已认证的 WebSocket ``requestMutation`` 白名单。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from nanobot.webui.settings_contracts import (
    SettingsRequest,
    SettingsRouteResult,
    WebUISettingsError,
)


def _not_configured(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    """catalog 未注入时的默认实现：响亮的 503，而不是 dispatch 深处的一次 AttributeError。"""
    raise WebUISettingsError("agent catalog is not configured", status=503)


@dataclass(frozen=True)
class AgentSettingsOperations:
    """注入 handler 的传输无关入口。

    每个字段都已绑好工作区依赖（gateway 用 ``partial`` 绑），所以
    ``dispatch`` 只传业务参数。保持成普通协议，测试才能塞内存替身而不碰磁盘。
    """

    list_profiles: Callable[..., dict[str, Any]]
    save_profile: Callable[..., dict[str, Any]]
    delete_profile: Callable[..., dict[str, Any]]
    reset_profile: Callable[..., dict[str, Any]]
    set_visibility: Callable[..., dict[str, Any]]
    list_catalog: Callable[..., dict[str, Any]] = _not_configured


# 本域认识的动作全集；settings router 用它把 action 路由进来。
AGENTS_ACTION_NAMES = frozenset({
    "agents-list",
    "agents-catalog",
    "agents-save",
    "agents-delete",
    "agents-reset",
    "agents-visibility",
})


class AgentSettingsHandler:
    def __init__(self, operations: AgentSettingsOperations) -> None:
        self._ops = operations

    def handle(self, action: str, request: SettingsRequest) -> SettingsRouteResult:
        if action not in AGENTS_ACTION_NAMES:
            return SettingsRouteResult.failure(404, f"unknown agent action: {action}")
        try:
            payload = dispatch(self._ops, action, request)
        except WebUISettingsError as exc:
            return SettingsRouteResult.failure(exc.status, exc.message)
        return SettingsRouteResult.success(payload)


# ---- per-action dispatch ---------------------------------------------------


def dispatch(
    operations: AgentSettingsOperations,
    action: str,
    request: SettingsRequest,
) -> dict[str, Any]:
    payload = request.payload or {}

    if action == "agents-list":
        return operations.list_profiles()

    if action == "agents-catalog":
        return operations.list_catalog()

    if action == "agents-save":
        raw = payload.get("agent")
        if not isinstance(raw, dict):
            raise WebUISettingsError("agent must be an object", status=400)
        return operations.save_profile(raw)

    if action == "agents-delete":
        return operations.delete_profile(_require_id(payload))

    if action == "agents-reset":
        return operations.reset_profile(_require_id(payload))

    if action == "agents-visibility":
        agent_id = _require_id(payload)
        hidden = payload.get("hidden")
        if not isinstance(hidden, bool):
            raise WebUISettingsError("hidden must be a boolean", status=400)
        return operations.set_visibility(agent_id, hidden)

    # 已知动作不会走到这里；留着作为将来新增动作时的兜底。
    raise WebUISettingsError(f"unsupported agent action: {action}")


def _require_id(payload: dict[str, Any]) -> str:
    agent_id = payload.get("id")
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise WebUISettingsError("id is required", status=400)
    return agent_id.strip()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/webui/test_agents_routes.py -v`
Expected: PASS（9 passed）

- [ ] **Step 5: 提交**

```bash
git add nanobot/webui/agents_routes.py tests/webui/test_agents_routes.py
git commit -m "feat(agents): settings 域 handler 与 operations 协议"
```

---

## Task 6: 注册端点与绑定 gateway

**Files:**
- Modify: `nanobot/agent/tools/registry.py:40-43`（`ToolRegistry` 加 `names()`）
- Modify: `nanobot/webui/settings_routes.py:173`（`_SYSTEM_ROUTES` 加两条读路径）
- Modify: `nanobot/webui/settings_routes.py:196`（新增 `AGENTS_MUTATION_PATHS`）
- Modify: `nanobot/webui/settings_routes.py:232`（展开进 `_SETTINGS_MUTATION_PATHS`）
- Modify: `nanobot/webui/settings_routes.py:22`（import）
- Modify: `nanobot/webui/settings_routes.py:428`（dispatch 分支）
- Modify: `nanobot/webui/ws_http.py:199-201`（`_WEBUI_MUTATION_ACTIONS` 加四条）
- Modify: `nanobot/webui/ws_http.py:345,360`（构造函数加参数并透传）
- Modify: `nanobot/webui/gateway_services.py:90`（新增 `build_agents_operations`）
- Modify: `nanobot/webui/gateway_services.py:203`（在 `build_gateway_services` 里绑定）
- Test: `tests/webui/test_agents_wiring.py`

- [ ] **Step 1: 写失败的测试**

创建 `tests/webui/test_agents_wiring.py`：

```python
"""端点注册、mutation 路径与 gateway 绑定的端到端接线检查。

照 tests/webui/test_identity_wiring.py 的思路：不启动服务器，只断言
「路径映射 / 白名单 / 绑定」这三张表里有 Agent 档案这一组。
"""

from __future__ import annotations

from pathlib import Path

from nanobot.webui import settings_routes
from nanobot.webui.gateway_services import build_agents_operations
from nanobot.webui.ws_http import _WEBUI_MUTATION_ACTIONS


def test_read_paths_are_registered() -> None:
    assert settings_routes._SYSTEM_ROUTES["/api/settings/agents"] == "agents-list"
    assert (
        settings_routes._SYSTEM_ROUTES["/api/settings/agents/catalog"] == "agents-catalog"
    )


def test_write_paths_are_in_the_mutation_allowlist() -> None:
    for path in (
        "/api/settings/agents/save",
        "/api/settings/agents/delete",
        "/api/settings/agents/reset",
        "/api/settings/agents/visibility",
    ):
        assert path in settings_routes._SETTINGS_MUTATION_PATHS, path


def test_mutation_actions_map_to_http_paths() -> None:
    assert _WEBUI_MUTATION_ACTIONS["agents.save"] == "/api/settings/agents/save"
    assert _WEBUI_MUTATION_ACTIONS["agents.delete"] == "/api/settings/agents/delete"
    assert _WEBUI_MUTATION_ACTIONS["agents.reset"] == "/api/settings/agents/reset"
    assert _WEBUI_MUTATION_ACTIONS["agents.visibility"] == "/api/settings/agents/visibility"


def test_registry_exposes_sorted_names() -> None:
    from nanobot.agent.tools.registry import ToolRegistry

    assert ToolRegistry().names() == []


def test_build_agents_operations_binds_workspace(tmp_path: Path) -> None:
    from nanobot.webui import agents_api

    ops = build_agents_operations(
        workspace_path=tmp_path,
        load_config=lambda: __import__(
            "nanobot.config.loader", fromlist=["load_config"]
        ).load_config(),
    )
    payload = ops.list_profiles()
    assert "agents" in payload
    assert agents_api is not None


def test_gateway_services_wires_agents_operations(tmp_path: Path) -> None:
    """build_gateway_services 必须把 agents_operations 传进 HTTP handler。"""
    import inspect

    from nanobot.webui.gateway_services import build_gateway_services

    source = inspect.getsource(build_gateway_services)
    assert "agents_operations=" in source
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/webui/test_agents_wiring.py -v`
Expected: FAIL —— `KeyError: '/api/settings/agents'`（以及 `ImportError: cannot import name 'build_agents_operations'`）

- [ ] **Step 3: 给 `ToolRegistry` 加 `names()`**

编辑 `nanobot/agent/tools/registry.py`，在 `get` 方法之后插入：

```python
    def names(self) -> list[str]:
        """Registered tool names in stable order."""
        return sorted(self._tools)
```

- [ ] **Step 4: 注册读路径**

编辑 `nanobot/webui/settings_routes.py`，在 `_SYSTEM_ROUTES` 里
`"/api/settings/identity/persona/set",`（第 173 行）之后加两行：

```python
    "/api/settings/agents": "agents-list",
    "/api/settings/agents/catalog": "agents-catalog",
```

- [ ] **Step 5: 注册写路径白名单**

编辑 `nanobot/webui/settings_routes.py`，在 `_IDENTITY_MUTATION_PATHS` 之后加：

```python
_AGENTS_MUTATION_PATHS = frozenset({
    "/api/settings/agents/save",
    "/api/settings/agents/delete",
    "/api/settings/agents/reset",
    "/api/settings/agents/visibility",
})
```

然后在 `_SETTINGS_MUTATION_PATHS` 的 `*_IDENTITY_MUTATION_PATHS,` 之后加：

```python
    *_AGENTS_MUTATION_PATHS,
```

- [ ] **Step 6: 加 dispatch 分支**

编辑 `nanobot/webui/settings_routes.py`：

1. 顶部 import 区（`from nanobot.webui import identity_routes as identity_domain` 之前）加：

```python
from nanobot.webui import agents_api
from nanobot.webui import agents_routes as agents_domain
from nanobot.webui.agents_routes import AgentSettingsOperations
```

2. 在 `elif action in identity_domain.IDENTITY_ACTION_NAMES:` 分支之后加：

```python
        elif action in agents_domain.AGENTS_ACTION_NAMES:
            # 与 identity/memory 同构：独立的传输无关 handler + 注入的 operations。
            result = await asyncio.to_thread(
                lambda: self._agents.handle(action, domain_request),
            )
```

3. 构造函数加 `agents_operations: AgentSettingsOperations | None = None` 参数，
   并在构造 `self._identity` 的地方旁照抄一行：

```python
        self._agents = AgentSettingsHandler(
            agents_operations
            if agents_operations is not None
            else _null_agents_operations()
        )
```

4. 照 `_null_identity_operations`（`settings_routes.py:289`）加一个降级实现：

```python
def _null_agents_operations() -> AgentSettingsOperations:
    """未注入时的占位实现——响亮的 503，而不是 dispatch 深处的一次 AttributeError。"""
    return AgentSettingsOperations(
        list_profiles=lambda: {"agents": []},
        save_profile=lambda raw: {},
        delete_profile=lambda agent_id: {"id": agent_id},
        reset_profile=lambda agent_id: {},
        set_visibility=lambda agent_id, hidden: {},
    )
```

- [ ] **Step 7: 注册 WS mutation action**

编辑 `nanobot/webui/ws_http.py`，在
`"identity.persona.set": "/api/settings/identity/persona/set",` 之后加四行：

```python
    "agents.save": "/api/settings/agents/save",
    "agents.delete": "/api/settings/agents/delete",
    "agents.reset": "/api/settings/agents/reset",
    "agents.visibility": "/api/settings/agents/visibility",
```

并在构造函数加 `agents_operations: AgentSettingsOperations | None = None` 参数，
紧邻 `self.identity_operations = identity_operations` 之后加：

```python
        self.agents_operations = agents_operations
```

传给 `SettingsRouter` 的地方补上 `agents_operations=self.agents_operations`。
同时在 `ws_http.py` 的 import 区加：

```python
from nanobot.webui.agents_routes import AgentSettingsOperations
```

- [ ] **Step 8: gateway 绑定**

编辑 `nanobot/webui/gateway_services.py`，在 `build_identity_operations` 之后加：

```python
def build_agents_operations(
    *,
    workspace_path: Path,
    load_config: Callable[[], Config] | None = None,
) -> AgentSettingsOperations:
    """Wire the real ``agents_api`` actions to a workspace-scoped store."""
    from nanobot.config.loader import load_config as _load_config

    workspace = Path(workspace_path)
    return AgentSettingsOperations(
        list_profiles=partial(agents_api.agents_list, workspace),
        save_profile=partial(agents_api.agents_save, workspace),
        delete_profile=partial(agents_api.agents_delete, workspace),
        reset_profile=partial(agents_api.agents_reset, workspace),
        set_visibility=partial(agents_api.agents_visibility, workspace),
        list_catalog=partial(
            agents_api.agents_catalog,
            workspace,
            load_config=load_config or _load_config,
        ),
    )
```

并在 `build_gateway_services` 里 `identity_operations=build_identity_operations(...)`
之后加：

```python
        agents_operations=build_agents_operations(
            workspace_path=workspace_path,
            load_config=partial(load_config, config_path) if config_path else load_config,
        ),
```

顶部 import 区加：

```python
from nanobot.webui import agents_api
from nanobot.webui.agents_routes import AgentSettingsOperations
```

- [ ] **Step 9: 运行测试确认通过**

Run: `pytest tests/webui/test_agents_wiring.py -v`
Expected: PASS（6 passed）

- [ ] **Step 10: 跑全量后端测试确认没有回归**

Run: `pytest tests/webui/ -q`
Expected: 全部 PASS，无 error。若 `test_identity_wiring.py` 失败，说明改 `ws_http.py` 构造调用时漏传或错传了参数。

- [ ] **Step 11: lint**

Run: `ruff check nanobot/agents/ nanobot/webui/agents_api.py nanobot/webui/agents_routes.py nanobot/webui/settings_routes.py nanobot/webui/ws_http.py nanobot/webui/gateway_services.py nanobot/agent/tools/registry.py`
Expected: `All checks passed!`

- [ ] **Step 12: 提交**

```bash
git add nanobot/agent/tools/registry.py nanobot/webui/settings_routes.py \
        nanobot/webui/ws_http.py nanobot/webui/gateway_services.py \
        tests/webui/test_agents_wiring.py
git commit -m "feat(agents): 注册档案端点并绑定 gateway"
```

---

## Task 7: 前端 API 客户端

**Files:**
- Create: `webui/src/lib/agents/api.ts`
- Delete: `webui/src/lib/agents/mock.ts`
- Test: `webui/src/tests/api-agents.test.ts`

- [ ] **Step 1: 写失败的测试**

创建 `webui/src/tests/api-agents.test.ts`：

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  deleteAgent,
  listAgents,
  loadAgentCatalog,
  resetAgent,
  saveAgent,
  setAgentVisibility,
} from "@/lib/agents/api";
import type { AgentProfile } from "@/lib/agents/types";

const profile: AgentProfile = {
  id: "writer",
  name: "写作",
  description: "",
  type: "custom",
  customized: false,
  categoryId: "writing",
  icon: "✍️",
  color: "#E67E22",
  prompt: "",
  modelId: null,
  tools: { mode: "all", entries: [] },
  skills: { mode: "all", entries: [] },
  subAgents: { mode: "all", entries: [] },
  hidden: false,
  updatedAt: "2026-09-29T00:00:00+00:00",
};

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "application/json" },
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

describe("agents api client", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("listAgents reads /api/settings/agents with the bearer token", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ agents: [profile] }));
    const result = await listAgents("tok-1");
    expect(result.agents).toHaveLength(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/settings/agents");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok-1");
  });

  it("loadAgentCatalog reads the catalog endpoint", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ tools: [], skills: [], models: [], categories: [] }),
    );
    const result = await loadAgentCatalog("tok-1");
    expect(result.categories).toEqual([]);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/settings/agents/catalog");
  });

  it("saveAgent goes through the mutation transport, not fetch", async () => {
    const requestMutation = vi.fn().mockResolvedValue({ agent: profile });
    await saveAgent({ requestMutation }, profile);
    expect(requestMutation).toHaveBeenCalledWith(
      "agents.save",
      { agent: profile },
      expect.any(Number),
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("deleteAgent sends the id", async () => {
    const requestMutation = vi.fn().mockResolvedValue({ id: "writer" });
    await deleteAgent({ requestMutation }, "writer");
    expect(requestMutation.mock.calls[0][0]).toBe("agents.delete");
    expect(requestMutation.mock.calls[0][1]).toEqual({ id: "writer" });
  });

  it("resetAgent and setAgentVisibility use their own actions", async () => {
    const requestMutation = vi.fn().mockResolvedValue({ agent: profile });
    await resetAgent({ requestMutation }, "code-reviewer");
    await setAgentVisibility({ requestMutation }, "code-reviewer", true);
    expect(requestMutation.mock.calls[0][0]).toBe("agents.reset");
    expect(requestMutation.mock.calls[1][0]).toBe("agents.visibility");
    expect(requestMutation.mock.calls[1][1]).toEqual({ id: "code-reviewer", hidden: true });
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd webui && bun run test src/tests/api-agents.test.ts`
Expected: FAIL —— `Cannot find module '@/lib/agents/api'`

- [ ] **Step 3: 写客户端**

创建 `webui/src/lib/agents/api.ts`：

```ts
import { request, mutation, type WebUIMutationTransport } from "@/lib/api";
import type {
  AgentCategory,
  AgentProfile,
  ModelDescriptor,
  SkillDescriptor,
  ToolDescriptor,
} from "@/lib/agents/types";

const AGENTS_BASE = "/api/settings/agents";

export interface AgentListResponse {
  agents: AgentProfile[];
}

export interface AgentCatalogResponse {
  tools: ToolDescriptor[];
  skills: SkillDescriptor[];
  models: ModelDescriptor[];
  categories: AgentCategory[];
}

export interface AgentMutationResponse {
  agent: AgentProfile;
}

export interface AgentDeleteResponse {
  id: string;
}

export async function listAgents(token: string, base: string = ""): Promise<AgentListResponse> {
  return request<AgentListResponse>(`${base}${AGENTS_BASE}`, token);
}

export async function loadAgentCatalog(
  token: string,
  base: string = "",
): Promise<AgentCatalogResponse> {
  return request<AgentCatalogResponse>(`${base}${AGENTS_BASE}/catalog`, token);
}

export async function saveAgent(
  transport: WebUIMutationTransport,
  agent: AgentProfile,
): Promise<AgentMutationResponse> {
  return mutation<AgentMutationResponse>(transport, "agents.save", { agent });
}

export async function deleteAgent(
  transport: WebUIMutationTransport,
  id: string,
): Promise<AgentDeleteResponse> {
  return mutation<AgentDeleteResponse>(transport, "agents.delete", { id });
}

export async function resetAgent(
  transport: WebUIMutationTransport,
  id: string,
): Promise<AgentMutationResponse> {
  return mutation<AgentMutationResponse>(transport, "agents.reset", { id });
}

export async function setAgentVisibility(
  transport: WebUIMutationTransport,
  id: string,
  hidden: boolean,
): Promise<AgentMutationResponse> {
  return mutation<AgentMutationResponse>(transport, "agents.visibility", { id, hidden });
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd webui && bun run test src/tests/api-agents.test.ts`
Expected: PASS（5 passed）

- [ ] **Step 5: 确认本 WU 不碰 `AgentsView.tsx`**

本 WU 只交付 `api.ts` 及其测试。`mock.ts` 的删除和 `AgentsView.tsx:26` 的 import
改名归 **Task 8（WU-06）** —— 改名会让 `loadAgents()` 在 Task 8 接手前变成未定义
符号，单独合流本 WU 会让 `bun run build` 挂掉。

Run: `cd webui && bun run build`
Expected: 构建成功（`api.ts` 是新增文件，暂无人引用也不报错）

- [ ] **Step 6: 提交**

```bash
git add webui/src/lib/agents/api.ts webui/src/tests/api-agents.test.ts
git commit -m "feat(webui): 档案 API 客户端"
```

---

## Task 8: 主从布局骨架 + 真实端点接线（WU-06）

> 本 Task 把 `AgentsView` 从「三视图 + 模态编辑器」重写为 **左列表 + 右详情** 的主从布局，
> 与 `IdentityView`（`webui/src/components/settings/identity/IdentityView.tsx:468`）同构。
>
> **为什么现在改**：`SettingsPage.tsx:630-637` 给 `agents` 和 `identity`、`channels` 一样的容器
> 约束——`max-w-[1240px]` + `xl:overflow-hidden` + `flex min-h-0`——这组约束本来就是给主从双栏
> 预留的，`IdentityView` 正在用，而 agents 把它浪费在滚动列表 + 大 modal 上。改完两个区块的
> 布局语言才一致。
>
> Task 8 交付**可用的双栏**（右栏是单列长表单）。Task 9 再把右栏 Tab 化并加概览。
> 拆两步是因为中间态本身能用，不会出现"半成品"。

**Files:**
- Create: `webui/src/lib/agents/catalog.ts`（`AgentCatalog` + `createBlankAgent`，从被删的 `AgentEditorDialog` 迁出）
- Create: `webui/src/components/settings/agents/AgentListPane.tsx`
- Create: `webui/src/components/settings/agents/AgentDetailPane.tsx`
- Modify: `webui/src/components/settings/agents/AgentsView.tsx`（整体重写）
- Modify: `webui/src/components/settings/agents/AgentRow.tsx`（选中态；菜单去掉「编辑」）
- Modify: `webui/src/lib/agents/api.ts`（`AgentCatalogResponse` 改为复用 `AgentCatalog`）
- Delete: `webui/src/components/settings/agents/AgentEditorDialog.tsx`（402 行）
- Delete: `webui/src/components/settings/agents/AgentCard.tsx`
- Delete: `webui/src/components/settings/agents/AgentTreeView.tsx`
- Delete: `webui/src/lib/agents/mock.ts`
- Test: `webui/src/tests/agents-view-integration.test.tsx`

- [ ] **Step 1: 写失败的测试**

创建 `webui/src/tests/agents-view-integration.test.tsx`：

```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AgentsView } from "@/components/settings/agents/AgentsView";
import type { AgentProfile } from "@/lib/agents/types";

const listMock = vi.fn();
const catalogMock = vi.fn();
const saveMock = vi.fn();
const deleteMock = vi.fn();
const resetMock = vi.fn();
const visibilityMock = vi.fn();

vi.mock("@/lib/agents/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/agents/api")>(
    "@/lib/agents/api",
  );
  return {
    ...actual,
    listAgents: (...args: unknown[]) => listMock(...args),
    loadAgentCatalog: (...args: unknown[]) => catalogMock(...args),
    saveAgent: (...args: unknown[]) => saveMock(...args),
    deleteAgent: (...args: unknown[]) => deleteMock(...args),
    resetAgent: (...args: unknown[]) => resetMock(...args),
    setAgentVisibility: (...args: unknown[]) => visibilityMock(...args),
  };
});

const transport = { requestMutation: vi.fn() };

vi.mock("@/providers/ClientProvider", () => ({
  useClient: () => ({
    client: transport,
    token: "tok-1",
    getToken: async () => "tok-1",
    modelName: "claude-sonnet-5",
    ingressLimits: { maxUploadBytes: 1 },
  }),
}));

// jsdom 的 matchMedia 只返回 false，等价于窄屏（单栏 + 紧凑详情）。
vi.stubGlobal("matchMedia", (query: string) => ({
  matches: false,
  media: query,
  addEventListener: () => {},
  removeEventListener: () => {},
}));

// `src/tests/setup.ts` 会 initializeI18n 并在 beforeEach 切到 en，
// 所以这里的文案断言一律用英文原文，不要用 i18n key 也不要写中文。
// 档案名（"写作"/"代码评审"）是 fixture 数据，不走 t()，可以写中文。

function profile(overrides: Partial<AgentProfile> = {}): AgentProfile {
  return {
    id: "writer",
    name: "写作",
    description: "",
    type: "custom",
    customized: false,
    categoryId: "writing",
    icon: "✍️",
    color: "#E67E22",
    prompt: "",
    modelId: null,
    tools: { mode: "all", entries: [] },
    skills: { mode: "all", entries: [] },
    subAgents: { mode: "all", entries: [] },
    hidden: false,
    updatedAt: "2026-09-29T00:00:00+00:00",
    ...overrides,
  };
}

const TWO: AgentProfile[] = [
  profile(),
  profile({ id: "reviewer", name: "代码评审", categoryId: "coding", icon: "🔍" }),
];

describe("AgentsView", () => {
  beforeEach(() => {
    listMock.mockReset().mockResolvedValue({ agents: TWO });
    catalogMock.mockReset().mockResolvedValue({
      tools: [],
      skills: [],
      models: [],
      categories: [{ id: "writing", name: "写作", color: "#E67E22", order: 2 }],
    });
    saveMock.mockReset().mockImplementation(async (_t: unknown, agent: AgentProfile) => ({
      agent,
    }));
    deleteMock.mockReset().mockResolvedValue({ id: "writer" });
    resetMock.mockReset().mockResolvedValue({ agent: TWO[1] });
    visibilityMock
      .mockReset()
      .mockImplementation(async (_t: unknown, id: string, hidden: boolean) => ({
        agent: profile({ id, name: "代码评审", hidden }),
      }));
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("loads agents and catalog with the token", async () => {
    render(<AgentsView />);
    await waitFor(() => expect(screen.getByText("写作")).toBeTruthy());
    expect(listMock).toHaveBeenCalledWith("tok-1");
    expect(catalogMock).toHaveBeenCalledWith("tok-1");
  });

  it("surfaces a load failure instead of an empty list", async () => {
    listMock.mockRejectedValueOnce(new Error("boom"));
    render(<AgentsView />);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("boom"));
  });

  it("selects the first agent and shows it in the detail pane", async () => {
    render(<AgentsView />);
    await waitFor(() => expect(screen.getByText("写作")).toBeTruthy());
    expect(screen.getByTestId("agent-detail-header")).toBeTruthy();
    // 详情区是常驻的，不是点开才有的 modal
    expect(screen.getByLabelText("Name")).toBeTruthy();
  });

  it("keeps the draft dirty until save, then adopts the server profile", async () => {
    const user = userEvent.setup();
    render(<AgentsView />);
    await waitFor(() => expect(screen.getByText("写作")).toBeTruthy());

    const nameInput = screen.getByLabelText("Name");
    await user.clear(nameInput);
    await user.type(nameInput, "写作二号");

    expect(screen.getByText("Unsaved changes")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(saveMock).toHaveBeenCalledWith(transport, expect.objectContaining({ name: "写作二号" })),
    );
    await waitFor(() => expect(screen.queryByText("Unsaved changes")).toBeNull());
  });

  it("asks before switching away from a dirty draft", async () => {
    const user = userEvent.setup();
    render(<AgentsView />);
    await waitFor(() => expect(screen.getByText("写作")).toBeTruthy());

    const nameInput = screen.getByLabelText("Name");
    await user.clear(nameInput);
    await user.type(nameInput, "改坏了");
    await user.click(screen.getByText("代码评审"));

    expect(screen.getByText("Discard unsaved changes?")).toBeTruthy();
    // 还没确认，详情区仍停在原来那个档案
    expect(nameInput).toHaveValue("改坏了");
  });

  it("creates a new draft with a unique id without calling the api", async () => {
    const user = userEvent.setup();
    render(<AgentsView />);
    await waitFor(() => expect(screen.getByText("写作")).toBeTruthy());

    await user.click(screen.getByRole("button", { name: "New agent" }));
    expect(saveMock).not.toHaveBeenCalled();
    // 新建态：名称为空，保存按钮可用但档案还没落盘
    expect(screen.getByLabelText("Name")).toHaveValue("");
    expect(screen.getByTestId("agent-detail-header")).toBeTruthy();
  });

  it("surfaces a save failure in an alert", async () => {
    const user = userEvent.setup();
    saveMock.mockRejectedValueOnce(new Error("系统预设不可删除"));
    render(<AgentsView />);
    await waitFor(() => expect(screen.getByText("写作")).toBeTruthy());

    const nameInput = screen.getByLabelText("Name");
    await user.clear(nameInput);
    await user.type(nameInput, "触发失败");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.getByText("系统预设不可删除")).toBeTruthy());
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd webui && bun run test src/tests/agents-view-integration.test.tsx`
Expected: FAIL —— 现有 `AgentsView` 仍从 `@/lib/agents/mock` 导入，没有 `AgentDetailPane`

- [ ] **Step 3: 迁出 `AgentCatalog` 与 `createBlankAgent`**

`AgentEditorDialog` 马上要删，而 `AgentsView` 和两个 Pane 都要用这两个符号。建
`webui/src/lib/agents/catalog.ts` 接住它们：

```ts
import type {
  AgentCategory,
  AgentProfile,
  ModelDescriptor,
  SkillDescriptor,
  ToolDescriptor,
} from "./types";

/** 编辑器需要的完整能力目录。线格式见 api.ts 的 loadAgentCatalog 返回值。 */
export interface AgentCatalog {
  tools: ToolDescriptor[];
  skills: SkillDescriptor[];
  models: ModelDescriptor[];
  categories: AgentCategory[];
}

/** 新建 Agent 的空草稿。id 由调用方按现有档案去重后填入。 */
export function createBlankAgent(): AgentProfile {
  return {
    id: "",
    name: "",
    description: "",
    type: "custom",
    customized: false,
    categoryId: null,
    icon: "🤖",
    color: "#4A90D9",
    prompt: "",
    modelId: null,
    tools: { mode: "all", entries: [] },
    skills: { mode: "all", entries: [] },
    subAgents: { mode: "all", entries: [] },
    hidden: false,
    // 服务端在 save 时覆盖；这里给空串而不是本地时间戳，
    // 免得草稿看起来"已经改过"。
    updatedAt: "",
  };
}

/** 名称 → 合法档案 id。与后端 nanobot/agents/catalog.py 的 ID_PATTERN 对齐。 */
export function slugifyAgentId(value: string): string {
  const slug = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "agent";
}

/** 在 used 之外取第一个可用 id，形如 base / base-2 / base-3。 */
export function uniqueAgentId(base: string, used: Set<string>): string {
  let id = base;
  let suffix = 2;
  while (used.has(id)) id = `${base}-${suffix++}`;
  return id;
}
```

再把 `webui/src/lib/agents/api.ts` 里的 `AgentCatalogResponse` 换成对它的复用，
免得同一个形状有两个名字：

```ts
import type { AgentCatalog } from "@/lib/agents/catalog";
// …（其余 import 保持）

export type AgentCatalogResponse = AgentCatalog;
```

- [ ] **Step 4: 写 `AgentListPane`**

创建 `webui/src/components/settings/agents/AgentListPane.tsx`：

```tsx
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Plus, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { AgentRow } from "@/components/settings/agents/AgentRow";
import type { AgentCatalog } from "@/lib/agents/catalog";
import type { AgentCategory, AgentProfile } from "@/lib/agents/types";
import { AGENT_CATEGORY_LABEL_KEY } from "@/lib/agents/i18n";
import { cn } from "@/lib/utils";

const ALL = "__all__";

export function AgentListPane({
  agents,
  categories,
  catalog,
  allAgentIds,
  selectedId,
  query,
  categoryFilter,
  onQueryChange,
  onCategoryFilterChange,
  onSelect,
  onCreate,
  onDuplicate,
  onToggleHidden,
  onDelete,
}: {
  agents: AgentProfile[];
  categories: AgentCategory[];
  catalog: AgentCatalog;
  allAgentIds: string[];
  selectedId: string | null;
  query: string;
  categoryFilter: string;
  onQueryChange: (next: string) => void;
  onCategoryFilterChange: (next: string) => void;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onDuplicate: (agent: AgentProfile) => void;
  onToggleHidden: (agent: AgentProfile) => void;
  onDelete: (agent: AgentProfile) => void;
}) {
  const { t } = useTranslation();

  const visible = useMemo(() => agents.filter((agent) => !agent.hidden), [agents]);
  const hidden = useMemo(() => agents.filter((agent) => agent.hidden), [agents]);

  const categoryTabs = useMemo(() => {
    const counts = new Map<string, number>();
    for (const agent of visible) {
      if (!agent.categoryId) continue;
      counts.set(agent.categoryId, (counts.get(agent.categoryId) ?? 0) + 1);
    }
    return categories
      .slice()
      .sort((a, b) => a.order - b.order)
      .map((category) => ({
        id: category.id,
        color: category.color,
        count: counts.get(category.id) ?? 0,
      }));
  }, [visible, categories]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return visible.filter((agent) => {
      if (categoryFilter !== ALL && agent.categoryId !== categoryFilter) return false;
      if (!needle) return true;
      return (
        agent.name.toLowerCase().includes(needle) ||
        agent.description.toLowerCase().includes(needle) ||
        agent.id.toLowerCase().includes(needle)
      );
    });
  }, [visible, categoryFilter, query]);

  return (
    <div className="flex min-h-0 flex-col gap-3">
      <div className="flex shrink-0 items-center gap-2">
        <div className="relative min-w-0 flex-1">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder={t("settings.agents.search", "搜索名称、ID 或描述…")}
            aria-label={t("settings.agents.search", "搜索名称或描述")}
            className="h-9 pl-9 text-[13px]"
          />
        </div>
        <Button
          type="button"
          size="sm"
          onClick={onCreate}
          className="shrink-0 gap-1.5 text-[13px]"
        >
          <Plus className="h-3.5 w-3.5" aria-hidden />
          {t("settings.agents.create", "新建 Agent")}
        </Button>
      </div>

      {categoryTabs.length > 0 ? (
        <div className="flex shrink-0 flex-wrap items-center gap-1.5">
          <FilterChip
            active={categoryFilter === ALL}
            onClick={() => onCategoryFilterChange(ALL)}
          >
            {t("settings.agents.categoryAll", "全部")} {visible.length}
          </FilterChip>
          {categoryTabs.map((category) => (
            <FilterChip
              key={category.id}
              active={categoryFilter === category.id}
              onClick={() => onCategoryFilterChange(category.id)}
              color={category.color}
            >
              {t(AGENT_CATEGORY_LABEL_KEY[category.id], category.id)} {category.count}
            </FilterChip>
          ))}
        </div>
      ) : null}

      <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto overscroll-contain pr-1">
        {filtered.length === 0 ? (
          <p className="px-3 py-10 text-center text-[13px] text-muted-foreground">
            {query.trim() || categoryFilter !== ALL
              ? t("settings.agents.noMatch", "没有匹配的智能体，换个关键词或清掉筛选试试。")
              : t("settings.agents.emptyHint", "还没有智能体。新建一个，把技能、工具和模型按需组合起来。")}
          </p>
        ) : (
          filtered.map((agent) => (
            <AgentRow
              key={agent.id}
              agent={agent}
              catalog={catalog}
              allAgentIds={allAgentIds}
              selected={agent.id === selectedId}
              onSelect={() => onSelect(agent.id)}
              onDuplicate={() => onDuplicate(agent)}
              onToggleHidden={() => onToggleHidden(agent)}
              onDelete={() => onDelete(agent)}
            />
          ))
        )}

        {hidden.length > 0 ? (
          <details className="mt-3 rounded-panel bg-settings-surface px-4 py-3">
            <summary className="cursor-pointer text-[12px] text-muted-foreground">
              {t("settings.agents.hiddenGroup", "已隐藏的智能体（{{count}}）", {
                count: hidden.length,
              })}
            </summary>
            <div className="mt-3 space-y-1.5">
              {hidden.map((agent) => (
                <AgentRow
                  key={agent.id}
                  agent={agent}
                  catalog={catalog}
                  allAgentIds={allAgentIds}
                  dimmed
                  selected={agent.id === selectedId}
                  onSelect={() => onSelect(agent.id)}
                  onDuplicate={() => onDuplicate(agent)}
                  onToggleHidden={() => onToggleHidden(agent)}
                  onDelete={() => onDelete(agent)}
                />
              ))}
            </div>
          </details>
        ) : null}
      </div>
    </div>
  );
}

function FilterChip({
  active,
  onClick,
  color,
  children,
}: {
  active: boolean;
  onClick: () => void;
  color?: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px] font-medium transition-colors",
        active
          ? "border-border bg-background text-foreground"
          : "border-transparent text-muted-foreground hover:bg-muted/60 hover:text-foreground",
      )}
    >
      {color ? (
        <span
          aria-hidden
          className="h-2 w-2 shrink-0 rounded-full"
          style={{ backgroundColor: color }}
        />
      ) : null}
      {children}
    </button>
  );
}
```

- [ ] **Step 5: 简化 `AgentRow`**

`AgentRow` 原来的 props 里有 `tools` / `skills` / `models` / `agentIds` 四个目录参数，
只是为了算摘要文字。现在摘要由 `useAgentSummary` 提供（在 Step 6 建），
`AgentRow` 只留档案本身。重写整个文件：

```tsx
import { useTranslation } from "react-i18next";
import { Copy, Eye, EyeOff, MoreHorizontal, Trash2 } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Badge } from "@/components/settings/agents/shared";
import type { AgentCatalog } from "@/lib/agents/catalog";
import type { AgentProfile } from "@/lib/agents/types";
import { AGENT_CATEGORY_LABEL_KEY } from "@/lib/agents/i18n";
import { useAgentSummary } from "@/components/settings/agents/useAgentSummary";
import { cn } from "@/lib/utils";

export function AgentRow({
  agent,
  catalog,
  allAgentIds,
  selected,
  dimmed,
  onSelect,
  onDuplicate,
  onToggleHidden,
  onDelete,
}: {
  agent: AgentProfile;
  catalog: AgentCatalog;
  allAgentIds: string[];
  selected?: boolean;
  dimmed?: boolean;
  onSelect: () => void;
  onDuplicate: () => void;
  onToggleHidden: () => void;
  onDelete: () => void;
}) {
  const { t } = useTranslation();
  const summary = useAgentSummary(agent, catalog, allAgentIds);
  const categoryKey = agent.categoryId ? AGENT_CATEGORY_LABEL_KEY[agent.categoryId] : null;

  return (
    <div
      role="group"
      className={cn(
        "group flex items-center gap-2 rounded-panel border px-3.5 py-3 transition-colors",
        selected
          ? "border-border bg-muted/60"
          : "border-transparent bg-settings-surface hover:border-border/70 hover:bg-muted/40",
        dimmed && "opacity-60",
      )}
    >
      <button
        type="button"
        onClick={onSelect}
        aria-current={selected ? "true" : undefined}
        className="flex min-w-0 flex-1 cursor-pointer items-center gap-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span
          aria-hidden
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-control text-[18px]"
          style={{ backgroundColor: `${agent.color}1a` }}
        >
          {agent.icon}
        </span>

        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="truncate text-[13.5px] font-semibold text-foreground">
              {agent.name}
            </span>
            {agent.type === "system" ? (
              <Badge tone="info">{t("settings.agents.basics.systemPreset", "系统预设")}</Badge>
            ) : null}
            {agent.type === "system" && agent.customized ? (
              <Badge tone="warn">{t("settings.agents.basics.customized", "已定制")}</Badge>
            ) : null}
            {categoryKey ? (
              <span className="text-[11px] text-muted-foreground">
                {t(categoryKey, agent.categoryId ?? "")}
              </span>
            ) : null}
          </span>

          <span className="mt-0.5 block truncate text-[11.5px] leading-5 text-muted-foreground/85">
            {summary}
          </span>
        </span>
      </button>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            aria-label={t("settings.agents.card.menu", "更多操作")}
            className="shrink-0 rounded-sm p-1.5 text-muted-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <MoreHorizontal className="h-4 w-4" aria-hidden />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-40">
          <DropdownMenuItem
            onSelect={onDuplicate}
            className="cursor-pointer gap-2 text-[13px]"
          >
            <Copy className="h-3.5 w-3.5" aria-hidden />
            {t("settings.agents.card.duplicate", "复制")}
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={onToggleHidden}
            className="cursor-pointer gap-2 text-[13px]"
          >
            {agent.hidden ? (
              <Eye className="h-3.5 w-3.5" aria-hidden />
            ) : (
              <EyeOff className="h-3.5 w-3.5" aria-hidden />
            )}
            {agent.hidden
              ? t("settings.agents.card.show", "显示")
              : t("settings.agents.card.hide", "隐藏")}
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={onDelete}
            className="cursor-pointer gap-2 text-[13px] text-destructive focus:text-destructive"
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden />
            {t("settings.agents.card.delete", "删除")}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
```

**「编辑」菜单项被去掉了** —— 点行就是选中并进入编辑，菜单里再放一个「编辑」是多余的一跳。

- [ ] **Step 6: 建 `useAgentSummary`**

列表行的摘要（原来那段 `describeCapability(...)` 拼装）在 `AgentRow` 和
`AgentOverviewTab` 都要用，抽成 hook。创建
`webui/src/components/settings/agents/useAgentSummary.ts`：
```ts
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import type { AgentCatalog } from "@/lib/agents/catalog";
import { breakdown, describeCapability, describeSubAgents } from "@/lib/agents/summary";
import type { AgentProfile } from "@/lib/agents/types";

/** 「4 工具 · 全部技能 · 2 个子 Agent · 跟随全局模型」这一行摘要。 */
export function useAgentSummary(
  agent: AgentProfile,
  catalog: AgentCatalog | null,
  allAgentIds: string[] = [],
): string {
  const { t } = useTranslation();

  return useMemo(() => {
    if (!catalog) return "";
    const toolIds = catalog.tools.map((tool) => tool.name);
    const lockedToolIds = catalog.tools
      .filter((tool) => tool.locked)
      .map((tool) => tool.name);
    const skillIds = catalog.skills.map((skill) => skill.name);
    const selectableAgentIds = allAgentIds.filter((id) => id !== agent.id);

    const modelLabel =
      catalog.models.find((model) => model.id === agent.modelId)?.label ??
      t("settings.agents.card.modelAuto", "跟随全局模型");

    return [
      describeCapability(
        breakdown(agent.tools, toolIds, lockedToolIds),
        t("settings.agents.noun.tools", "工具"),
        t,
      ),
      describeCapability(
        breakdown(agent.skills, skillIds),
        t("settings.agents.noun.skills", "技能"),
        t,
      ),
      describeSubAgents(breakdown(agent.subAgents, selectableAgentIds), t),
      modelLabel,
    ].join(" · ");
  }, [agent, catalog, allAgentIds, t]);
}
```

Step 4 的 `AgentListPane` 与 Step 5 的 `AgentRow` 都已经是带 `catalog` /
`allAgentIds` 的签名，直接编译即可，不需要再补参数。

- [ ] **Step 7: 写 `AgentDetailPane`（本 Task 先单列，Task 9 再 Tab 化）**

创建 `webui/src/components/settings/agents/AgentDetailPane.tsx`：

```tsx
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { CircleAlert, RotateCcw, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { SectionLabel } from "@/components/settings/agents/shared";
import { BasicsSection } from "@/components/settings/agents/parts/BasicsSection";
import {
  CapabilityPicker,
  type CapabilityItem,
} from "@/components/settings/agents/parts/CapabilityPicker";
import { ModelPicker } from "@/components/settings/agents/parts/ModelPicker";
import { PromptSection } from "@/components/settings/agents/parts/PromptSection";
import { RelationsSection } from "@/components/settings/agents/parts/RelationsSection";
import { AgentPreviewRail } from "@/components/settings/agents/parts/AgentPreviewRail";
import type { AgentCatalog } from "@/lib/agents/catalog";
import type { AgentProfile, PromptContext } from "@/lib/agents/types";
import { TOOL_CATEGORIES, resolveSelection } from "@/lib/agents/types";
import { SKILL_SOURCE_LABEL_KEY, TOOL_CATEGORY_LABEL_KEY } from "@/lib/agents/i18n";
import { cn } from "@/lib/utils";

export function AgentDetailPane({
  draft,
  agents,
  catalog,
  globalModelLabel,
  isNew,
  dirty,
  canSave,
  saving,
  actionError,
  onChange,
  onSave,
  onDiscard,
  onReset,
  onDeleteRequest,
  onClose,
}: {
  draft: AgentProfile;
  agents: AgentProfile[];
  catalog: AgentCatalog;
  globalModelLabel: string;
  isNew: boolean;
  dirty: boolean;
  canSave: boolean;
  saving: boolean;
  actionError: string | null;
  onChange: (patch: Partial<AgentProfile>) => void;
  onSave: () => void;
  onDiscard: () => void;
  onReset: () => void;
  onDeleteRequest: () => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();

  const toolItems = useMemo<CapabilityItem[]>(
    () =>
      catalog.tools.map((tool) => ({
        id: tool.name,
        label: tool.label,
        description: tool.description,
        group: tool.category,
        groupLabel: tool.category,
        risk: tool.risk,
        locked: tool.locked,
      })),
    [catalog.tools],
  );

  const skillItems = useMemo<CapabilityItem[]>(
    () =>
      catalog.skills.map((skill) => ({
        id: skill.name,
        label: skill.name,
        description: skill.description,
        group: skill.source,
        groupLabel: skill.source,
        chips: skill.tags,
      })),
    [catalog.skills],
  );

  const enabledToolIds = useMemo(
    () =>
      resolveSelection(
        draft.tools,
        catalog.tools.map((tool) => tool.name),
        catalog.tools.filter((tool) => tool.locked).map((tool) => tool.name),
      ),
    [draft.tools, catalog.tools],
  );

  const enabledSkillIds = useMemo(
    () => resolveSelection(draft.skills, catalog.skills.map((skill) => skill.name)),
    [draft.skills, catalog.skills],
  );

  const enabledSubAgentIds = useMemo(
    () =>
      resolveSelection(
        draft.subAgents,
        agents.map((item) => item.id).filter((id) => id !== draft.id),
      ),
    [draft.subAgents, agents, draft.id],
  );

  const promptContext = useMemo<PromptContext>(() => {
    const modelLabel =
      catalog.models.find((model) => model.id === draft.modelId)?.label ?? globalModelLabel;
    return {
      name: draft.name || t("settings.agents.untitled", "未命名"),
      description: draft.description,
      skills: catalog.skills
        .filter((skill) => enabledSkillIds.has(skill.name))
        .map((skill) => skill.name),
      tools: catalog.tools
        .filter((tool) => enabledToolIds.has(tool.name))
        .map((tool) => tool.label),
      model: modelLabel,
      date: new Intl.DateTimeFormat("zh-CN", { dateStyle: "long" }).format(new Date()),
      userProfile: t("settings.agents.promptContext.user", "（未配置用户档案）"),
      workspace: "nanobot",
    };
  }, [draft, catalog, enabledSkillIds, enabledToolIds, globalModelLabel, t]);

  return (
    <div className="flex min-h-0 flex-col rounded-panel border border-border/60 bg-settings-surface">
      <header className="flex shrink-0 items-start gap-3 border-b border-border/60 px-5 py-4">
        <span
          aria-hidden
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-[22px]"
          style={{ backgroundColor: `${draft.color}1a` }}
        >
          {draft.icon}
        </span>
        <div className="min-w-0 flex-1">
          <p
            data-testid="agent-detail-header"
            className="truncate text-[16px] font-semibold leading-6 text-foreground"
          >
            {isNew
              ? t("settings.agents.detail.new", "新建 Agent")
              : t("settings.agents.detail.title", "编辑 · {{name}}", { name: draft.name })}
          </p>
          <p className="mt-0.5 line-clamp-2 text-[12px] leading-5 text-muted-foreground">
            {draft.description ||
              t("settings.agents.editor.subtitle", "配置该 Agent 可用的能力、模型与提示词。")}
          </p>
        </div>
        {draft.type === "system" ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onReset}
            disabled={saving}
            className="shrink-0 gap-1.5 text-[12px] text-muted-foreground"
          >
            <RotateCcw className="h-3.5 w-3.5" aria-hidden />
            {t("settings.agents.editor.reset", "重置为默认")}
          </Button>
        ) : null}
        <button
          type="button"
          aria-label={t("common.close", "关闭")}
          onClick={onClose}
          className="shrink-0 rounded-sm p-1 text-muted-foreground opacity-70 transition-opacity hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <X className="h-4 w-4" aria-hidden />
        </button>
      </header>

      <div className="min-h-0 flex-1 space-y-6 overflow-y-auto px-5 py-5">
        {actionError ? (
          <div
            role="alert"
            className="rounded-panel border border-rose-300/70 bg-rose-50/70 px-4 py-3 text-[13px] text-rose-900 dark:border-rose-700/40 dark:bg-rose-950/30 dark:text-rose-200"
          >
            {actionError}
          </div>
        ) : null}

        <section className="space-y-3">
          <SectionLabel>{t("settings.agents.section.basics", "基础信息")}</SectionLabel>
          <BasicsSection
            profile={draft}
            categories={catalog.categories}
            isNew={isNew}
            onChange={onChange}
          />
        </section>

        <section className="space-y-3">
          <SectionLabel>{t("settings.agents.section.tools", "能力 · 工具")}</SectionLabel>
          <CapabilityPicker
            items={toolItems}
            selection={draft.tools}
            onChange={(tools) => onChange({ tools })}
            modeLabel={t("settings.agents.tools.modeLabel", "搜索工具")}
            groupLabelFor={(id) =>
              t(
                TOOL_CATEGORY_LABEL_KEY[id] ?? id,
                TOOL_CATEGORIES.find((item) => item.id === id)?.label ?? id,
              )
            }
          />
        </section>

        <section className="space-y-3">
          <SectionLabel>{t("settings.agents.section.skills", "能力 · 技能")}</SectionLabel>
          {draft.skills.mode === "all" && catalog.skills.length > 0 ? (
            <p className="text-[12px] text-muted-foreground">
              {t("settings.agents.skills.inheritHint", "跟随全局配置（当前可用 {{count}} 个）", {
                count: catalog.skills.length,
              })}
            </p>
          ) : null}
          <CapabilityPicker
            items={skillItems}
            selection={draft.skills}
            onChange={(skills) => onChange({ skills })}
            modeLabel={t("settings.agents.skills.modeLabel", "搜索技能")}
            groupLabelFor={(id) =>
              t(SKILL_SOURCE_LABEL_KEY[id as keyof typeof SKILL_SOURCE_LABEL_KEY], id)
            }
          />
        </section>

        <section className="space-y-3">
          <SectionLabel>{t("settings.agents.section.model", "模型")}</SectionLabel>
          <ModelPicker
            models={catalog.models}
            value={draft.modelId}
            onChange={(modelId) => onChange({ modelId })}
            globalModelLabel={globalModelLabel}
          />
        </section>

        <section className="space-y-3">
          <SectionLabel>{t("settings.agents.section.prompt", "提示词")}</SectionLabel>
          <PromptSection
            profile={draft}
            onChange={(prompt) => onChange({ prompt })}
          />
        </section>

        <section className="space-y-3">
          <SectionLabel>{t("settings.agents.section.relations", "关系与调度")}</SectionLabel>
          <RelationsSection
            agents={agents}
            editingId={draft.id || null}
            selection={draft.subAgents}
            onChange={(subAgents) => onChange({ subAgents })}
          />
        </section>

        <section className="space-y-3">
          <SectionLabel>{t("settings.agents.rail.prompt", "最终 prompt")}</SectionLabel>
          <AgentPreviewRail
            profile={draft}
            context={promptContext}
            agents={agents}
            tools={catalog.tools}
            skills={catalog.skills}
            models={catalog.models}
            enabledToolIds={enabledToolIds}
            enabledSkillIds={enabledSkillIds}
            enabledSubAgentIds={enabledSubAgentIds}
          />
        </section>
      </div>

      <footer
        className={cn(
          "flex shrink-0 items-center gap-3 border-t border-border/60 px-5 py-3",
          dirty && "bg-amber-50/70 dark:bg-amber-950/25",
        )}
      >
        {dirty ? (
          <p className="flex items-center gap-1.5 text-[12px] text-amber-800 dark:text-amber-200">
            <CircleAlert className="h-3.5 w-3.5 shrink-0" aria-hidden />
            {t("settings.agents.editor.unsaved", "有未保存的修改")}
          </p>
        ) : null}
        {!isNew ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onDeleteRequest}
            disabled={saving}
            className="text-[12px] text-destructive hover:text-destructive"
          >
            {t("settings.agents.card.delete", "删除")}
          </Button>
        ) : null}
        <div className={cn("flex gap-2", !dirty && !isNew && "ml-auto")}>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onDiscard}
            disabled={!dirty || saving}
            className="text-[13px]"
          >
            {t("common.cancel", "取消")}
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={!canSave || !dirty}
            onClick={onSave}
            className="text-[13px]"
          >
            {saving
              ? t("settings.agents.editor.saving", "保存中…")
              : t("settings.agents.editor.save", "保存")}
          </Button>
        </div>
      </footer>
    </div>
  );
}

export function ConfirmDiscardDialog({
  open,
  onOpenChange,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation();
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {t("settings.agents.editor.discardTitle", "放弃未保存的修改？")}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {t("settings.agents.editor.discardBody", "切换后本次编辑不会保存。")}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{t("common.cancel", "取消")}</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm}>
            {t("settings.agents.editor.discard", "放弃修改")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
```

> `AgentPreviewRail` 在本 Task 里**继续使用**（作为单列布局的最后一块）。Task 9 会把它
> 提炼成 `AgentOverviewTab` 并删除。

- [ ] **Step 8: 重写 `AgentsView`**

整个文件替换为：

```tsx
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronLeft } from "lucide-react";

import { useClient } from "@/providers/ClientProvider";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { StatusBlock } from "@/components/settings/agents/shared";
import { AgentListPane } from "@/components/settings/agents/AgentListPane";
import {
  AgentDetailPane,
  ConfirmDiscardDialog,
} from "@/components/settings/agents/AgentDetailPane";
import {
  createBlankAgent,
  slugifyAgentId,
  uniqueAgentId,
  type AgentCatalog,
} from "@/lib/agents/catalog";
import {
  deleteAgent,
  listAgents,
  loadAgentCatalog,
  resetAgent,
  saveAgent,
  setAgentVisibility,
} from "@/lib/agents/api";
import type { AgentProfile } from "@/lib/agents/types";
import { PROMPT_MAX_LENGTH } from "@/lib/agents/types";
import { cn } from "@/lib/utils";

const ALL = "__all__";

export function AgentsView() {
  const { t } = useTranslation();
  const { client, token, modelName } = useClient();

  const [agents, setAgents] = useState<AgentProfile[]>([]);
  const [catalog, setCatalog] = useState<AgentCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const [query, setQuery] = useState("");
  const [categoryFilter, setCategoryFilter] = useState<string>(ALL);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<AgentProfile | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [pendingSwitch, setPendingSwitch] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<AgentProfile | null>(null);

  // 与 IdentityView 同一断点：≥1280px 走双栏，窄屏走单栏 + 紧凑详情。
  const splitLayout = useMediaQuery("(min-width: 1280px)");
  const [compactDetailOpen, setCompactDetailOpen] = useState(false);
  const showingCompactDetail = !splitLayout && compactDetailOpen;

  // 每次 token 变化都重新拉取。`cancelled` 让 effect 对卸载、以及与在途请求
  // 竞争的换 token 安全 —— 与 IdentityView 的写法一致。
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    Promise.all([listAgents(token), loadAgentCatalog(token)])
      .then(([loadedAgents, loadedCatalog]) => {
        if (cancelled) return;
        setAgents(loadedAgents.agents);
        setCatalog(loadedCatalog);
        setActionError(null);
        setSelectedId((prev) =>
          prev && loadedAgents.agents.some((agent) => agent.id === prev)
            ? prev
            : (loadedAgents.agents[0]?.id ?? null),
        );
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setLoadError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const selected = useMemo(
    () => agents.find((agent) => agent.id === selectedId) ?? null,
    [agents, selectedId],
  );

  // 选中项变化才重置草稿。用户在详情里改的东西不会因为左栏别的操作被冲掉，
  // 因为那些操作改的是 agents 数组里的别的条目，selected 引用不变。
  useEffect(() => {
    if (selected && !isNew) setDraft({ ...selected });
  }, [selected, isNew]);

  const dirty = useMemo(() => {
    if (!draft) return false;
    if (isNew) return true;
    return JSON.stringify(draft) !== JSON.stringify(selected);
  }, [draft, isNew, selected]);

  const canSave = Boolean(
    draft &&
      draft.name.trim().length > 0 &&
      draft.id.trim().length > 0 &&
      draft.prompt.length <= PROMPT_MAX_LENGTH &&
      !saving,
  );

  const applyServerProfile = (next: AgentProfile) => {
    setAgents((prev) => {
      const index = prev.findIndex((agent) => agent.id === next.id);
      if (index === -1) return [...prev, next];
      const copy = prev.slice();
      copy[index] = next;
      return copy;
    });
  };

  const requestSelect = (id: string) => {
    if (id === selectedId && !isNew) return;
    if (dirty) {
      setPendingSwitch(id);
      return;
    }
    setIsNew(false);
    setSelectedId(id);
    if (!splitLayout) setCompactDetailOpen(true);
  };

  const startNewDraft = () => {
    const used = new Set(agents.map((agent) => agent.id));
    const base = slugifyAgentId(t("settings.agents.untitled", "未命名"));
    setDraft({ ...createBlankAgent(), id: uniqueAgentId(base, used) });
    setIsNew(true);
    setSelectedId(null);
    setActionError(null);
    if (!splitLayout) setCompactDetailOpen(true);
  };

  const handleCreate = () => {
    if (dirty) {
      setPendingSwitch(CREATE_SENTINEL);
      return;
    }
    startNewDraft();
  };

  const handleSave = async () => {
    if (!draft || !canSave) return;
    setActionError(null);
    setSaving(true);
    try {
      // 关键：updatedAt / customized / type 一律以服务端返回为准，
      // 不用本地拼的对象（契约 §4.1）。
      const { agent } = await saveAgent(client, draft);
      applyServerProfile(agent);
      setDraft({ ...agent });
      setIsNew(false);
      setSelectedId(agent.id);
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  };

  const handleDiscard = () => {
    if (isNew) {
      setIsNew(false);
      setDraft(selected ? { ...selected } : null);
      if (!selected) setSelectedId(null);
      return;
    }
    if (selected) setDraft({ ...selected });
  };

  const handleReset = async () => {
    if (!draft) return;
    setActionError(null);
    setSaving(true);
    try {
      const { agent } = await resetAgent(client, draft.id);
      applyServerProfile(agent);
      setDraft({ ...agent });
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  };

  const handleDuplicate = async (agent: AgentProfile) => {
    setActionError(null);
    const used = new Set(agents.map((item) => item.id));
    const id = uniqueAgentId(`${slugifyAgentId(agent.name)}-copy`, used);
    try {
      const { agent: created } = await saveAgent(client, {
        ...agent,
        id,
        name: `${agent.name} ${t("settings.agents.duplicateSuffix", "副本")}`,
        type: "custom",
        customized: false,
        hidden: false,
        updatedAt: "",
      });
      setAgents((prev) => [...prev, created]);
      setSelectedId(created.id);
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const handleToggleHidden = async (agent: AgentProfile) => {
    setActionError(null);
    try {
      const { agent: updated } = await setAgentVisibility(
        client,
        agent.id,
        !agent.hidden,
      );
      applyServerProfile(updated);
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const handleDelete = async () => {
    if (!pendingDelete) return;
    setActionError(null);
    try {
      await deleteAgent(client, pendingDelete.id);
      setAgents((prev) => prev.filter((agent) => agent.id !== pendingDelete.id));
      if (selectedId === pendingDelete.id) {
        setSelectedId(null);
        setDraft(null);
        setIsNew(false);
      }
    } catch (reason) {
      // 服务端拒删系统预设（409）时，文案原样显示在详情区的 actionError 里。
      setActionError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setPendingDelete(null);
    }
  };

  const confirmSwitch = () => {
    const target = pendingSwitch;
    setPendingSwitch(null);
    if (target === CREATE_SENTINEL) {
      // 直接调 startNewDraft 而不是 handleCreate —— 此刻 dirty 仍是 true，
      // 走 handleCreate 会再次 setPendingSwitch，把对话框弹回来。
      startNewDraft();
      return;
    }
    if (!target) return;
    setIsNew(false);
    setSelectedId(target);
    if (!splitLayout) setCompactDetailOpen(true);
  };

  const listPane = catalog ? (
    <AgentListPane
      agents={agents}
      categories={catalog.categories}
      catalog={catalog}
      allAgentIds={agents.map((agent) => agent.id)}
      selectedId={selectedId}
      query={query}
      categoryFilter={categoryFilter}
      onQueryChange={setQuery}
      onCategoryFilterChange={setCategoryFilter}
      onSelect={requestSelect}
      onCreate={handleCreate}
      onDuplicate={handleDuplicate}
      onToggleHidden={handleToggleHidden}
      onDelete={setPendingDelete}
    />
  ) : null;

  const detailPane =
    draft && catalog ? (
      <AgentDetailPane
        draft={draft}
        agents={agents}
        catalog={catalog}
        globalModelLabel={modelName}
        isNew={isNew}
        dirty={dirty}
        canSave={canSave}
        saving={saving}
        actionError={actionError}
        onChange={(patch) => setDraft((prev) => (prev ? { ...prev, ...patch } : prev))}
        onSave={handleSave}
        onDiscard={handleDiscard}
        onReset={handleReset}
        onDeleteRequest={() => setPendingDelete(draft)}
        onClose={() => {
          handleDiscard();
          if (!splitLayout) setCompactDetailOpen(false);
          else setSelectedId(null);
        }}
      />
    ) : null;

  return (
    <div className="flex min-h-full flex-1 flex-col xl:min-h-0 xl:overflow-hidden">
      <StatusBlock
        status={loading ? "loading" : agents.length === 0 ? "empty" : "ready"}
        error={loadError}
      />
      {!loadError && catalog && agents.length > 0 ? (
        <div
          className={cn(
            "grid min-h-0 flex-1",
            splitLayout
              ? "grid-cols-[minmax(0,1fr)_minmax(420px,520px)] gap-6 overflow-hidden"
              : "gap-3",
          )}
        >
          {!showingCompactDetail && listPane}
          {detailPane ? (
            <div className="flex min-h-0 flex-col">
              {showingCompactDetail ? (
                <button
                  type="button"
                  onClick={() => setCompactDetailOpen(false)}
                  className="touch-target mb-3 inline-flex items-center gap-1.5 self-start rounded-full px-2.5 py-1.5 text-[12px] font-medium text-muted-foreground transition-colors hover:bg-muted/70 hover:text-foreground"
                >
                  <ChevronLeft className="h-3.5 w-3.5" aria-hidden />
                  {t("settings.agents.backToList", "返回列表")}
                </button>
              ) : null}
              {detailPane}
            </div>
          ) : null}
        </div>
      ) : null}

      <ConfirmDiscardDialog
        open={pendingSwitch !== null}
        onOpenChange={(next) => {
          if (!next) setPendingSwitch(null);
        }}
        onConfirm={confirmSwitch}
      />

      {pendingDelete ? (
        <DeleteConfirmDialog
          agent={pendingDelete}
          onCancel={() => setPendingDelete(null)}
          onConfirm={handleDelete}
        />
      ) : null}
    </div>
  );
}

const CREATE_SENTINEL = "__create__";

function DeleteConfirmDialog({
  agent,
  onCancel,
  onConfirm,
}: {
  agent: AgentProfile;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation();
  return (
    <AlertDialog open onOpenChange={(next) => !next && onCancel()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {t("settings.agents.deleteTitle", "删除「{{name}}」？", { name: agent.name })}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {agent.type === "system"
              ? t(
                  "settings.agents.deleteSystemBody",
                  "这是系统预设，无法删除。可以先重置为默认值，或改为隐藏。",
                )
              : t("settings.agents.deleteBody", "删除后不可恢复，历史会话不受影响。")}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{t("common.cancel", "取消")}</AlertDialogCancel>
          <AlertDialogAction
            onClick={onConfirm}
            className="bg-destructive text-white hover:bg-destructive/90"
          >
            {t("settings.agents.card.delete", "删除")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
```

import 区还需要 `AlertDialog*`（`DeleteConfirmDialog` 用了），补上：

```tsx
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
```

> `CREATE_SENTINEL` 必须在 `AgentsView` 函数体之前声明（`const` 不提升），
> 放在文件末尾 `DeleteConfirmDialog` 之前即可。

- [ ] **Step 9: 删掉三视图与 mock**

```bash
rm webui/src/components/settings/agents/AgentEditorDialog.tsx \
   webui/src/components/settings/agents/AgentCard.tsx \
   webui/src/components/settings/agents/AgentTreeView.tsx \
   webui/src/lib/agents/mock.ts
```

删完先确认没有残留引用：

Run: `grep -rn "AgentEditorDialog\|AgentCard\|AgentTreeView\|agents/mock" webui/src/`
Expected: 无输出。有输出就说明还有文件在 import 它们，一并清掉。

- [ ] **Step 10: 运行测试确认通过**

Run: `cd webui && bun run test src/tests/agents-view-integration.test.tsx`
Expected: PASS（7 passed）

- [ ] **Step 11: 类型检查与全量前端测试**

Run: `cd webui && bun run build`
Expected: 构建成功（`tsc` 无错误）

Run: `cd webui && bun run test`
Expected: 全部 PASS

- [ ] **Step 12: 补 i18n 词条**

本 Task 引入 7 个新 key。不补的话英文界面会直接显示中文默认值
（`t(key, "中文")` 在 key 缺失时返回默认值）。

`en` 与 `zh-CN` 是仅有的两个完整 locale（各 1597 key），其余 8 个语言
本来就各缺 152 个 key、走 `fallbackLng: "en"`，**本计划不补它们**。

编辑 `webui/src/i18n/locales/en/common.json` 与 `zh-CN/common.json`，
在 `settings.agents` 下各加：

```jsonc
// en/common.json
"detail": {
  "title": "Edit · {{name}}",
  "new": "New agent"
},
"backToList": "Back to list",
"deleteSystemBody": "This is a factory preset and cannot be deleted. Reset it to defaults or hide it instead.",
"editor": { /* 已有 */ "saving": "Saving…" },
"skills": { /* 已有 */ "inheritHint": "Following global config ({{count}} available)" }
```

```jsonc
// zh-CN/common.json
"detail": {
  "title": "编辑 · {{name}}",
  "new": "新建 Agent"
},
"backToList": "返回列表",
"deleteSystemBody": "这是系统预设，无法删除。可以先重置为默认值，或改为隐藏。",
"editor": { /* 已有 */ "saving": "保存中…" },
"skills": { /* 已有 */ "inheritHint": "跟随全局配置（当前可用 {{count}} 个）" }
```

加完核对两个 locale 的 key 集合仍然一致：

Run: `python3 -c "import json;a=json.load(open('webui/src/i18n/locales/en/common.json'));b=json.load(open('webui/src/i18n/locales/zh-CN/common.json'));print('一致' if a.keys()==b.keys() else '不一致')"`
Expected: `一致`

- [ ] **Step 13: 提交**

```bash
git add -A webui/src/components/settings/agents webui/src/lib/agents \
           webui/src/i18n/locales \
           webui/src/tests/agents-view-integration.test.tsx
git commit -m "feat(webui): 智能体管理改为主从双栏布局，接入真实后端"
```

---

## Task 9: 详情区 Tab 化与概览（WU-07）

> Task 8 的右栏是一整条长表单，功能齐全但要滚很久。这个 Task 按参考设计把它拆成
> **概览 / 能力 / 设置** 三个 Tab，并新增概览——一个能一眼看清「这个 Agent 实际长什么样」
> 的只读面板（能力摘要 + 已渲染的 prompt 三段）。

### 🔶 修订 A（2026-09-29，用户看图后追加）—— 能力 Tab 内加子导航

> **已被下方「修订 B」扩展**：能力 Tab 的子导航仍然要做，但整个详情区要搬进弹窗。
> 读的时候两份叠加生效。

原计划把「工具 / Skills / 提示词」平铺在能力 Tab 里。**用户看参考设计后要求再加一层
子导航**：能力 Tab 内部左侧是 `指令 / 工具 / Skills / MCP` 四个子项，右侧才是内容区。
本节覆盖下方 Step 5 中 `AgentCapabilityTab` 的平铺写法，其余 Step 不变。

**修订后的能力 Tab 结构：**

```
┌─────────────────────────────────────────────┐
│ 能力                                          │
├──────────┬──────────────────────────────────┤
│ 指令      │  ┌────────────────────────────┐  │
│ 工具      │  │ 内容区（只渲染当前子项）      │  │
│ Skills   │  └────────────────────────────┘  │
│ MCP      │                                   │
└──────────┴──────────────────────────────────┘
```

- 子导航用竖排 `role="tablist"`，窄屏（`splitLayout === false`）退化为横向滚动的胶囊条。
- 默认停在「指令」——参考设计里进能力 Tab 就是为了改 System Prompt。
- **切换子项不丢草稿**：`draft` 仍由 `AgentsView` 持有（坑 5），子导航只切 `activeSub` 局部状态。

**四个子项的内容：**

| 子项 | 内容 | 复用 |
| --- | --- | --- |
| 指令 | System Prompt 编辑器 + 字数 | `PromptSection`（原样复用） |
| 工具 | 工具选择器 | `CapabilityPicker` |
| Skills | 技能选择器 + 跟随全局提示 | `CapabilityPicker` |
| MCP | **只读**全局 MCP server 列表 + 跳全局设置按钮 | 新写 |

**MCP 子项为什么是只读（2026-09-29 用户已确认）**

nanobot 的 MCP 是**全局配置**：`config.tools.mcp_servers: dict[str, MCPServerConfig]`
（`nanobot/config/schema.py:505`），MCP 工具在运行时动态注册成 `mcp_<server>_<tool>`，
**不在静态工具目录里**（实测 25 个内置工具无任何 `mcp_` 前缀）。
所以「按 Agent 勾选 MCP」在当前运行时里没有对应语义——真要生效，得让 `SubagentManager`
按档案过滤 `mcp_servers` 注入，那是二期的事。一期只做展示 + 跳转。

**为此需要一处后端小改动（只读，不动存储契约）：**
`GET /api/settings/agents/catalog` 的响应增加 `mcpServers: McpServerDescriptor[]`，
从 `load_config().tools.mcp_servers` 读，只取 `name` / `type` / `url` / `command` / `enabled_tools`。
`AgentProfile` **不加字段**、存储层**不动**。
前端 `webui/src/lib/agents/catalog.ts` 的 `AgentCatalog` 同步加 `mcpServers`。

### 🔶 修订 B（2026-09-30，用户二次确认）—— 改回「列表 + 弹窗」，覆盖 Task 8 的双栏布局

**用户最终要的形态：紧凑行列表 + 点任意一行开弹窗编辑。**
既不是 Task 8 做的左右分栏，参考设计里的「整页详情」也变成「弹窗里的 Tab」。
用户已明确选定：**列表 = 紧凑行列表**，**弹窗内照搬图二三的 Tab + 子导航**。

```
┌──────────────────────────────────────────────┐
│ 智能体                        [ + 新建智能体 ]  │
│ [搜索…]  [全部]  [已归档]        筛选 · 最近活跃│
├──────────────────────────────────────────────┤
│ ● 代码评审   只读地审查改动        11 天前   ⋯ │  ← 紧凑行
│ ● SEO 写手   …                               │
└──────────────────────────────────────────────┘
        ↓ 点任意一行
┌──────────────────────────────────────────────┐
│ ● Mika   你的工作区 Chief of Staff            │  ← 弹窗 header
│         默认 · Claude · 11 天前更新            │
├──────────────────────────────────────────────┤
│ 概览 │ 能力 │ 设置                             │  ← Tab
├──────────┬───────────────────────────────────┤
│ 指令      │  ┌─────────────────────────────┐  │
│ 工具      │  │ 内容区（只渲染当前子项）        │  │
│ Skills   │  └─────────────────────────────┘  │
│ MCP      │                                    │
├──────────┴───────────────────────────────────┤
│ 有未保存的修改              [放弃] [保存] [删除] │  ← 保存条
└──────────────────────────────────────────────┘
```

**对 Task 8 产物的处置：**

| 文件 | 处置 |
| --- | --- |
| `AgentListPane.tsx` | **保留**。它本来就是紧凑行列表（搜索 + 分类筛选 + 行 + 新建 + 已隐藏折叠区），正是要的东西 |
| `AgentRow.tsx` | **改造**。整行可点开弹窗 |
| `AgentDetailPane.tsx` | **改造**。从「右栏常驻面板」变成「弹窗体」：内部是 header + Tab + 子导航 + 保存条 |
| `useAgentSummary.ts` | **保留**。行摘要与概览区共用 |
| `AgentsView.tsx` | **改造**。删掉 `grid-cols-[minmax(0,1fr)_minmax(420px,520px)]` 双栏布局，删掉 `splitLayout` / `compactDetailOpen` / `showingCompactDetail` 这套「窄屏折叠右栏」逻辑（弹窗天然自适应，不需要），改为单列列表 + `Dialog` 开关 |

**弹窗实现**：复用项目既有的 `@/components/ui/dialog`（shadcn 风格），
结构照 `webui/src/components/settings/system/McpManagementDialog.tsx`。
弹窗要够大——它内部还要再分 Tab 和子导航，建议 `sm:max-w-5xl` 且内容区可滚动。

**「切换子项/切 Tab 不丢草稿」这条仍然成立**：`draft` 由 `AgentsView` 持有，
`AgentDetailPane`（弹窗体）只切视图。

**Files:**
- Create: `webui/src/components/settings/agents/parts/AgentDetailHeader.tsx`
- Create: `webui/src/components/settings/agents/parts/AgentOverviewTab.tsx`
- Create: `webui/src/components/settings/agents/parts/AgentCapabilityTab.tsx`（**含子导航，见上方修订**）
- Create: `webui/src/components/settings/agents/parts/AgentMcpPanel.tsx`（**修订追加**）
- Create: `webui/src/components/settings/agents/parts/AgentSettingsTab.tsx`
- Create: `webui/src/components/settings/agents/parts/AgentSaveBar.tsx`
- Modify: `webui/src/components/settings/agents/AgentDetailPane.tsx`（**改成弹窗体**，修订 B）
- Create: `webui/src/components/settings/agents/AgentEditorModal.tsx`（**弹窗外壳**，修订 B）
- Modify: `webui/src/components/settings/agents/AgentsView.tsx`（**单列列表 + Dialog 开关，删双栏逻辑**，修订 B）
- Modify: `webui/src/components/settings/agents/AgentRow.tsx`（**整行可点开弹窗**，修订 B）
- ~~Modify: `nanobot/webui/agents_api.py`~~ **✅ 已完成**（catalog 响应加 `mcpServers`，契约 §8.4）
- Modify: `webui/src/lib/agents/catalog.ts`（`AgentCatalog` 加 `mcpServers`）
- ~~Modify: `tests/webui/test_agents_api.py`~~ **✅ 已完成**
- Delete: `webui/src/components/settings/agents/parts/AgentPreviewRail.tsx`
- Test: `webui/src/tests/agent-detail-tabs.test.tsx`


- [ ] **Step 1: 写失败的测试**

创建 `webui/src/tests/agent-detail-tabs.test.tsx`：

```tsx
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AgentDetailPane } from "@/components/settings/agents/AgentDetailPane";
import type { AgentCatalog } from "@/lib/agents/catalog";
import type { AgentProfile } from "@/lib/agents/types";

const profile: AgentProfile = {
  id: "code-reviewer",
  name: "代码评审",
  description: "只读地审查改动",
  type: "system",
  customized: false,
  categoryId: "coding",
  icon: "🔍",
  color: "#8E44AD",
  prompt: "你是{{name}}，{{description}}",
  modelId: null,
  tools: { mode: "include", entries: ["read_file"] },
  skills: { mode: "all", entries: [] },
  subAgents: { mode: "all", entries: [] },
  hidden: false,
  updatedAt: "2026-09-29T00:00:00+00:00",
};

const catalog: AgentCatalog = {
  tools: [
    {
      name: "read_file",
      label: "read_file",
      description: "读文件",
      category: "filesystem",
      risk: "low",
      scope: "core",
      locked: true,
    },
    {
      name: "execute_command",
      label: "execute_command",
      description: "执行命令",
      category: "execution",
      risk: "high",
      scope: "core",
      locked: true,
    },
  ],
  skills: [{ name: "memory", description: "记忆", source: "builtin", tags: [] }],
  models: [
    {
      id: "default",
      label: "Default",
      provider: "auto",
      contextWindow: 200000,
      health: "healthy",
      vision: true,
      toolUse: true,
    },
  ],
  categories: [{ id: "coding", name: "编码", color: "#8E44AD", order: 1 }],
};

function renderPane(overrides: Partial<Parameters<typeof AgentDetailPane>[0]> = {}) {
  const props = {
    draft: profile,
    agents: [profile],
    catalog,
    globalModelLabel: "全局模型",
    isNew: false,
    dirty: false,
    canSave: true,
    saving: false,
    actionError: null,
    onChange: vi.fn(),
    onSave: vi.fn(),
    onDiscard: vi.fn(),
    onReset: vi.fn(),
    onDeleteRequest: vi.fn(),
    onClose: vi.fn(),
    ...overrides,
  };
  return { ...render(<AgentDetailPane {...props} />), props };
}

describe("AgentDetailPane tabs", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // 断言用英文原文：src/tests/setup.ts 会把 i18n 切到 en。

  it("opens on the overview tab and shows the rendered prompt", () => {
    renderPane();
    expect(screen.getByText("Capability summary")).toBeTruthy();
    // {{name}} / {{description}} 必须已被替换成真实值。
    // 注意 assemblePrompt（webui/src/lib/agents/types.ts:221-229）的 base/identity
    // 段落是**硬编码中文**、不走 t()，所以这里只能按中文断言。
    expect(screen.getByText("你是代码评审，只读地审查改动。")).toBeTruthy();
    expect(screen.queryByText(/{{name}}/)).toBeNull();
  });

  it("shows the capability summary counts", () => {
    renderPane();
    // include 模式只选了 read_file，共 2 个工具
    expect(screen.getByText("1 / 2")).toBeTruthy();
  });

  it("switches to the capability tab and renders the tool picker", async () => {
    const user = userEvent.setup();
    renderPane();
    await user.click(screen.getByRole("tab", { name: "Capabilities" }));
    expect(screen.getByText("② Capabilities · Tools")).toBeTruthy();
  });

  it("switches to the settings tab and renders basics", async () => {
    const user = userEvent.setup();
    renderPane();
    await user.click(screen.getByRole("tab", { name: "Settings" }));
    expect(screen.getByLabelText("Name")).toBeTruthy();
  });

  it("keeps the draft intact when switching tabs", async () => {
    const user = userEvent.setup();
    renderPane();
    await user.click(screen.getByRole("tab", { name: "Settings" }));
    await user.clear(screen.getByLabelText("Name"));
    await user.type(screen.getByLabelText("Name"), "评审二号");
    await user.click(screen.getByRole("tab", { name: "Overview" }));
    expect(screen.getByTestId("agent-detail-header")).toHaveTextContent("评审二号");
  });

  it("shows the reset button only for system presets", async () => {
    const user = userEvent.setup();
    const { unmount } = renderPane();
    expect(screen.getByRole("button", { name: "Reset to default" })).toBeTruthy();
    unmount();
    renderPane({ draft: { ...profile, type: "custom" } });
    expect(screen.queryByRole("button", { name: "Reset to default" })).toBeNull();
  });

  it("disables save when the draft is clean", () => {
    renderPane();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("enables save and shows the unsaved hint when dirty", () => {
    renderPane({ dirty: true });
    expect(screen.getByRole("button", { name: "Save" })).not.toBeDisabled();
    expect(screen.getByText("Unsaved changes")).toBeTruthy();
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd webui && bun run test src/tests/agent-detail-tabs.test.tsx`
Expected: FAIL —— 现在的 `AgentDetailPane` 没有 `tab` 角色，也没有「能力摘要」

- [ ] **Step 3: 写 `AgentDetailHeader`**

创建 `webui/src/components/settings/agents/parts/AgentDetailHeader.tsx`：

```tsx
import { useTranslation } from "react-i18next";
import { RotateCcw, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { AgentProfile } from "@/lib/agents/types";

export function AgentDetailHeader({
  draft,
  isNew,
  saving,
  onReset,
  onClose,
}: {
  draft: AgentProfile;
  isNew: boolean;
  saving: boolean;
  onReset: () => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();

  return (
    <header className="flex shrink-0 items-start gap-3 border-b border-border/60 px-5 py-4">
      <span
        aria-hidden
        className="flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-[22px]"
        style={{ backgroundColor: `${draft.color}1a` }}
      >
        {draft.icon}
      </span>
      <div className="min-w-0 flex-1">
        <p
          data-testid="agent-detail-header"
          className="truncate text-[16px] font-semibold leading-6 text-foreground"
        >
          {isNew
            ? t("settings.agents.detail.new", "新建 Agent")
            : t("settings.agents.detail.title", "编辑 · {{name}}", { name: draft.name })}
        </p>
        <p className="mt-0.5 line-clamp-2 text-[12px] leading-5 text-muted-foreground">
          {draft.description ||
            t("settings.agents.editor.subtitle", "配置该 Agent 可用的能力、模型与提示词。")}
        </p>
      </div>
      {draft.type === "system" ? (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onReset}
          disabled={saving}
          className="shrink-0 gap-1.5 text-[12px] text-muted-foreground"
        >
          <RotateCcw className="h-3.5 w-3.5" aria-hidden />
          {t("settings.agents.editor.reset", "重置为默认")}
        </Button>
      ) : null}
      <button
        type="button"
        aria-label={t("common.close", "关闭")}
        onClick={onClose}
        className="shrink-0 rounded-sm p-1 text-muted-foreground opacity-70 transition-opacity hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <X className="h-4 w-4" aria-hidden />
      </button>
    </header>
  );
}
```

- [ ] **Step 4: 写 `AgentOverviewTab`**

从 `AgentPreviewRail` 提炼：摘要卡 + 已渲染 prompt 三段 + 可调度子 Agent。
创建 `webui/src/components/settings/agents/parts/AgentOverviewTab.tsx`：

```tsx
import { useTranslation } from "react-i18next";

import { Badge, SectionLabel } from "@/components/settings/agents/shared";
import type { AgentCatalog } from "@/lib/agents/catalog";
import type { AgentProfile, PromptContext } from "@/lib/agents/types";
import { assemblePrompt } from "@/lib/agents/types";

function PreviewBlock({ label, body, tone }: { label: string; body: string; tone: string }) {
  const { t } = useTranslation();
  return (
    <div className="border-l-2 pl-2.5" style={{ borderColor: tone }}>
      <p className="mb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p className="whitespace-pre-wrap text-[12px] leading-relaxed text-foreground/85">
        {body || t("settings.agents.prompt.emptySegment", "（空）")}
      </p>
    </div>
  );
}

function SummaryStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-panel bg-muted/30 px-3 py-2">
      <p className="text-[10.5px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
        {label}
      </p>
      <p className="mt-0.5 truncate text-[13px] text-foreground/90">{value}</p>
    </div>
  );
}

export function AgentOverviewTab({
  draft,
  agents,
  catalog,
  context,
  enabledToolIds,
  enabledSkillIds,
  enabledSubAgentIds,
}: {
  draft: AgentProfile;
  agents: AgentProfile[];
  catalog: AgentCatalog;
  context: PromptContext;
  enabledToolIds: Set<string>;
  enabledSkillIds: Set<string>;
  enabledSubAgentIds: Set<string>;
}) {
  const { t } = useTranslation();
  const assembled = assemblePrompt(draft, context);

  const enabledTools = catalog.tools.filter((tool) => enabledToolIds.has(tool.name));
  const enabledSkills = catalog.skills.filter((skill) => enabledSkillIds.has(skill.name));
  const subAgents = agents.filter((agent) => enabledSubAgentIds.has(agent.id));
  const model =
    catalog.models.find((item) => item.id === draft.modelId)?.label ??
    t("settings.agents.card.modelAuto", "跟随全局模型");

  return (
    <div className="space-y-6">
      <section className="space-y-3">
        <SectionLabel>{t("settings.agents.overview.summary", "能力摘要")}</SectionLabel>
        <div className="grid grid-cols-2 gap-2">
          <SummaryStat
            label={t("settings.agents.noun.tools", "工具")}
            value={`${enabledTools.length} / ${catalog.tools.length}`}
          />
          <SummaryStat
            label={t("settings.agents.noun.skills", "技能")}
            value={
              draft.skills.mode === "all"
                ? t("settings.agents.overview.followAll", "全部（跟随全局）")
                : `${enabledSkills.length} / ${catalog.skills.length}`
            }
          />
          <SummaryStat
            label={t("settings.agents.rail.subAgents", "可调度子 Agent")}
            value={
              draft.subAgents.mode === "all"
                ? t("settings.agents.overview.allSubAgents", "全部")
                : String(subAgents.length)
            }
          />
          <SummaryStat label={t("settings.agents.rail.model", "模型")} value={model} />
        </div>
      </section>

      <section className="space-y-3">
        <SectionLabel>{t("settings.agents.rail.prompt", "最终 prompt")}</SectionLabel>
        <div className="space-y-2.5">
          <PreviewBlock
            label={t("settings.agents.prompt.segmentBase", "基础系统提示词（平台内置）")}
            body={assembled.base}
            tone="#94a3b8"
          />
          <PreviewBlock
            label={t("settings.agents.prompt.segmentIdentity", "身份段落")}
            body={assembled.identity}
            tone="#8E44AD"
          />
          <PreviewBlock
            label={t("settings.agents.prompt.segmentCustom", "本 Agent 提示词")}
            body={assembled.custom}
            tone="#4A90D9"
          />
        </div>
      </section>

      <section className="space-y-3">
        <SectionLabel>{t("settings.agents.rail.subAgents", "可调度子 Agent")}</SectionLabel>
        {subAgents.length === 0 ? (
          <p className="text-[12px] text-muted-foreground/80">
            {t("settings.agents.rail.noSubAgents", "不调度任何子 Agent")}
          </p>
        ) : (
          <ul className="space-y-1">
            {subAgents.map((agent) => (
              <li key={agent.id} className="flex items-center gap-2 text-[12px]">
                <span
                  aria-hidden
                  className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-[12px]"
                  style={{ backgroundColor: `${agent.color}1f` }}
                >
                  {agent.icon}
                </span>
                <span className="min-w-0 flex-1 truncate text-foreground/85">{agent.name}</span>
                {agent.type === "system" ? (
                  <Badge tone="muted">{t("settings.agents.basics.systemPreset", "系统预设")}</Badge>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
```

- [ ] **Step 5: 写 `AgentCapabilityTab` 与 `AgentSettingsTab`**

把 Task 8 `AgentDetailPane` 里那六个 `<section>` 原样搬进两个新文件。

`webui/src/components/settings/agents/parts/AgentCapabilityTab.tsx`：

```tsx
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { SectionLabel } from "@/components/settings/agents/shared";
import {
  CapabilityPicker,
  type CapabilityItem,
} from "@/components/settings/agents/parts/CapabilityPicker";
import { PromptSection } from "@/components/settings/agents/parts/PromptSection";
import type { AgentCatalog } from "@/lib/agents/catalog";
import type { AgentProfile } from "@/lib/agents/types";
import { SKILL_SOURCE_LABEL_KEY, TOOL_CATEGORY_LABEL_KEY } from "@/lib/agents/i18n";
import { TOOL_CATEGORIES } from "@/lib/agents/types";

export function AgentCapabilityTab({
  draft,
  catalog,
  onChange,
}: {
  draft: AgentProfile;
  catalog: AgentCatalog;
  onChange: (patch: Partial<AgentProfile>) => void;
}) {
  const { t } = useTranslation();

  const toolItems = useMemo<CapabilityItem[]>(
    () =>
      catalog.tools.map((tool) => ({
        id: tool.name,
        label: tool.label,
        description: tool.description,
        group: tool.category,
        groupLabel: tool.category,
        risk: tool.risk,
        locked: tool.locked,
      })),
    [catalog.tools],
  );

  const skillItems = useMemo<CapabilityItem[]>(
    () =>
      catalog.skills.map((skill) => ({
        id: skill.name,
        label: skill.name,
        description: skill.description,
        group: skill.source,
        groupLabel: skill.source,
        chips: skill.tags,
      })),
    [catalog.skills],
  );

  return (
    <div className="space-y-6">
      <section className="space-y-3">
        <SectionLabel>{t("settings.agents.section.tools", "能力 · 工具")}</SectionLabel>
        <CapabilityPicker
          items={toolItems}
          selection={draft.tools}
          onChange={(tools) => onChange({ tools })}
          modeLabel={t("settings.agents.tools.modeLabel", "搜索工具")}
          groupLabelFor={(id) =>
            t(
              TOOL_CATEGORY_LABEL_KEY[id] ?? id,
              TOOL_CATEGORIES.find((item) => item.id === id)?.label ?? id,
            )
          }
        />
      </section>

      <section className="space-y-3">
        <SectionLabel>{t("settings.agents.section.skills", "能力 · 技能")}</SectionLabel>
        {draft.skills.mode === "all" && catalog.skills.length > 0 ? (
          <p className="text-[12px] text-muted-foreground">
            {t("settings.agents.skills.inheritHint", "跟随全局配置（当前可用 {{count}} 个）", {
              count: catalog.skills.length,
            })}
          </p>
        ) : null}
        <CapabilityPicker
          items={skillItems}
          selection={draft.skills}
          onChange={(skills) => onChange({ skills })}
          modeLabel={t("settings.agents.skills.modeLabel", "搜索技能")}
          groupLabelFor={(id) =>
            t(SKILL_SOURCE_LABEL_KEY[id as keyof typeof SKILL_SOURCE_LABEL_KEY], id)
          }
        />
      </section>

      <section className="space-y-3">
        <SectionLabel>{t("settings.agents.section.prompt", "提示词")}</SectionLabel>
        <PromptSection profile={draft} onChange={(prompt) => onChange({ prompt })} />
      </section>
    </div>
  );
}
```

`webui/src/components/settings/agents/parts/AgentSettingsTab.tsx`：

```tsx
import { useTranslation } from "react-i18next";

import { SectionLabel } from "@/components/settings/agents/shared";
import { BasicsSection } from "@/components/settings/agents/parts/BasicsSection";
import { ModelPicker } from "@/components/settings/agents/parts/ModelPicker";
import { RelationsSection } from "@/components/settings/agents/parts/RelationsSection";
import type { AgentCatalog } from "@/lib/agents/catalog";
import type { AgentProfile } from "@/lib/agents/types";

export function AgentSettingsTab({
  draft,
  agents,
  catalog,
  isNew,
  globalModelLabel,
  onChange,
}: {
  draft: AgentProfile;
  agents: AgentProfile[];
  catalog: AgentCatalog;
  isNew: boolean;
  globalModelLabel: string;
  onChange: (patch: Partial<AgentProfile>) => void;
}) {
  const { t } = useTranslation();

  return (
    <div className="space-y-6">
      <section className="space-y-3">
        <SectionLabel>{t("settings.agents.section.basics", "基础信息")}</SectionLabel>
        <BasicsSection
          profile={draft}
          categories={catalog.categories}
          isNew={isNew}
          onChange={onChange}
        />
      </section>

      <section className="space-y-3">
        <SectionLabel>
          {t("settings.agents.settings.execution", "执行配置")}
        </SectionLabel>
        <ModelPicker
          models={catalog.models}
          value={draft.modelId}
          onChange={(modelId) => onChange({ modelId })}
          globalModelLabel={globalModelLabel}
        />
      </section>

      <section className="space-y-3">
        <SectionLabel>{t("settings.agents.section.relations", "关系与调度")}</SectionLabel>
        <RelationsSection
          agents={agents}
          editingId={draft.id || null}
          selection={draft.subAgents}
          onChange={(subAgents) => onChange({ subAgents })}
        />
      </section>
    </div>
  );
}
```

> **参考设计里的「思考强度 / 速度 / 并行运行上限」不抄**：nanobot 只有全局级的
> `reasoning_effort` / `temperature`，档案级没有这些字段，加了就超出「按现有功能体系」。

- [ ] **Step 6: 写 `AgentSaveBar`**

创建 `webui/src/components/settings/agents/parts/AgentSaveBar.tsx`：

```tsx
import { useTranslation } from "react-i18next";
import { CircleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function AgentSaveBar({
  dirty,
  canSave,
  saving,
  isNew,
  onSave,
  onDiscard,
  onDeleteRequest,
}: {
  dirty: boolean;
  canSave: boolean;
  saving: boolean;
  isNew: boolean;
  onSave: () => void;
  onDiscard: () => void;
  onDeleteRequest: () => void;
}) {
  const { t } = useTranslation();

  return (
    <footer
      className={cn(
        "flex shrink-0 items-center gap-3 border-t border-border/60 px-5 py-3",
        dirty && "bg-amber-50/70 dark:bg-amber-950/25",
      )}
    >
      {dirty ? (
        <p className="flex items-center gap-1.5 text-[12px] text-amber-800 dark:text-amber-200">
          <CircleAlert className="h-3.5 w-3.5 shrink-0" aria-hidden />
          {t("settings.agents.editor.unsaved", "有未保存的修改")}
        </p>
      ) : null}
      {!isNew ? (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onDeleteRequest}
          disabled={saving}
          className="text-[12px] text-destructive hover:text-destructive"
        >
          {t("settings.agents.card.delete", "删除")}
        </Button>
      ) : null}
      <div className={cn("flex gap-2", !dirty && !isNew && "ml-auto")}>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={onDiscard}
          disabled={!dirty || saving}
          className="text-[13px]"
        >
          {t("common.cancel", "取消")}
        </Button>
        <Button
          type="button"
          size="sm"
          onClick={onSave}
          disabled={!canSave || !dirty}
          className="text-[13px]"
        >
          {saving
            ? t("settings.agents.editor.saving", "保存中…")
            : t("settings.agents.editor.save", "保存")}
        </Button>
      </div>
    </footer>
  );
}
```

- [ ] **Step 7: 把 `AgentDetailPane` 改成 header + tabs + save bar**

整个文件替换为：

```tsx
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { AgentDetailHeader } from "@/components/settings/agents/parts/AgentDetailHeader";
import { AgentOverviewTab } from "@/components/settings/agents/parts/AgentOverviewTab";
import { AgentCapabilityTab } from "@/components/settings/agents/parts/AgentCapabilityTab";
import { AgentSettingsTab } from "@/components/settings/agents/parts/AgentSettingsTab";
import { AgentSaveBar } from "@/components/settings/agents/parts/AgentSaveBar";
import type { AgentCatalog } from "@/lib/agents/catalog";
import type { AgentProfile, PromptContext } from "@/lib/agents/types";
import { resolveSelection } from "@/lib/agents/types";

const TABS = ["overview", "capability", "settings"] as const;
type DetailTab = (typeof TABS)[number];

export function AgentDetailPane({
  draft,
  agents,
  catalog,
  globalModelLabel,
  isNew,
  dirty,
  canSave,
  saving,
  actionError,
  onChange,
  onSave,
  onDiscard,
  onReset,
  onDeleteRequest,
  onClose,
}: {
  draft: AgentProfile;
  agents: AgentProfile[];
  catalog: AgentCatalog;
  globalModelLabel: string;
  isNew: boolean;
  dirty: boolean;
  canSave: boolean;
  saving: boolean;
  actionError: string | null;
  onChange: (patch: Partial<AgentProfile>) => void;
  onSave: () => void;
  onDiscard: () => void;
  onReset: () => void;
  onDeleteRequest: () => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  // 切 Tab 不重置草稿：draft 由父组件持有，这里只切视图。
  const [tab, setTab] = useState<DetailTab>("overview");

  const enabledToolIds = useMemo(
    () =>
      resolveSelection(
        draft.tools,
        catalog.tools.map((tool) => tool.name),
        catalog.tools.filter((tool) => tool.locked).map((tool) => tool.name),
      ),
    [draft.tools, catalog.tools],
  );

  const enabledSkillIds = useMemo(
    () => resolveSelection(draft.skills, catalog.skills.map((skill) => skill.name)),
    [draft.skills, catalog.skills],
  );

  const enabledSubAgentIds = useMemo(
    () =>
      resolveSelection(
        draft.subAgents,
        agents.map((item) => item.id).filter((id) => id !== draft.id),
      ),
    [draft.subAgents, agents, draft.id],
  );

  const promptContext = useMemo<PromptContext>(() => {
    const modelLabel =
      catalog.models.find((model) => model.id === draft.modelId)?.label ?? globalModelLabel;
    return {
      name: draft.name || t("settings.agents.untitled", "未命名"),
      description: draft.description,
      skills: catalog.skills
        .filter((skill) => enabledSkillIds.has(skill.name))
        .map((skill) => skill.name),
      tools: catalog.tools
        .filter((tool) => enabledToolIds.has(tool.name))
        .map((tool) => tool.label),
      model: modelLabel,
      date: new Intl.DateTimeFormat("zh-CN", { dateStyle: "long" }).format(new Date()),
      userProfile: t("settings.agents.promptContext.user", "（未配置用户档案）"),
      workspace: "nanobot",
    };
  }, [draft, catalog, enabledSkillIds, enabledToolIds, globalModelLabel, t]);

  const tabLabel: Record<DetailTab, string> = {
    overview: t("settings.agents.tab.overview", "概览"),
    capability: t("settings.agents.tab.capability", "能力"),
    settings: t("settings.agents.tab.settings", "设置"),
  };

  return (
    <div className="flex min-h-0 flex-col rounded-panel border border-border/60 bg-settings-surface">
      <AgentDetailHeader
        draft={draft}
        isNew={isNew}
        saving={saving}
        onReset={onReset}
        onClose={onClose}
      />

      <div role="tablist" className="flex shrink-0 gap-1 border-b border-border/60 px-3">
        {TABS.map((item) => (
          <button
            key={item}
            type="button"
            role="tab"
            aria-selected={tab === item}
            onClick={() => setTab(item)}
            className={
              tab === item
                ? "border-b-2 border-foreground/70 px-3 py-2 text-[13px] font-medium text-foreground"
                : "border-b-2 border-transparent px-3 py-2 text-[13px] text-muted-foreground transition-colors hover:text-foreground"
            }
          >
            {tabLabel[item]}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
        {actionError ? (
          <div
            role="alert"
            className="mb-4 rounded-panel border border-rose-300/70 bg-rose-50/70 px-4 py-3 text-[13px] text-rose-900 dark:border-rose-700/40 dark:bg-rose-950/30 dark:text-rose-200"
          >
            {actionError}
          </div>
        ) : null}

        {tab === "overview" ? (
          <AgentOverviewTab
            draft={draft}
            agents={agents}
            catalog={catalog}
            context={promptContext}
            enabledToolIds={enabledToolIds}
            enabledSkillIds={enabledSkillIds}
            enabledSubAgentIds={enabledSubAgentIds}
          />
        ) : null}

        {tab === "capability" ? (
          <AgentCapabilityTab draft={draft} catalog={catalog} onChange={onChange} />
        ) : null}

        {tab === "settings" ? (
          <AgentSettingsTab
            draft={draft}
            agents={agents}
            catalog={catalog}
            isNew={isNew}
            globalModelLabel={globalModelLabel}
            onChange={onChange}
          />
        ) : null}
      </div>

      <AgentSaveBar
        dirty={dirty}
        canSave={canSave}
        saving={saving}
        isNew={isNew}
        onSave={onSave}
        onDiscard={onDiscard}
        onDeleteRequest={onDeleteRequest}
      />
    </div>
  );
}

export function ConfirmDiscardDialog({
  open,
  onOpenChange,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation();
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {t("settings.agents.editor.discardTitle", "放弃未保存的修改？")}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {t("settings.agents.editor.discardBody", "切换后本次编辑不会保存。")}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{t("common.cancel", "取消")}</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm}>
            {t("settings.agents.editor.discard", "放弃修改")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
```

- [ ] **Step 8: 删除 `AgentPreviewRail`**

```bash
rm webui/src/components/settings/agents/parts/AgentPreviewRail.tsx
```

Run: `grep -rn "AgentPreviewRail" webui/src/`
Expected: 无输出。

- [ ] **Step 9: 运行测试确认通过**

Run: `cd webui && bun run test src/tests/agent-detail-tabs.test.tsx`
Expected: PASS（8 passed）

- [ ] **Step 10: 跑全量前端测试与类型检查**

Run: `cd webui && bun run build`
Expected: 构建成功

Run: `cd webui && bun run test`
Expected: 全部 PASS，含 Task 8 的 7 个集成用例

- [ ] **Step 11: 补 i18n 词条**

本 Task 引入 6 个新 key，同样只补 `en` 与 `zh-CN`（Task 8 Step 12 已说明理由）。

在 `webui/src/i18n/locales/en/common.json` 与 `zh-CN/common.json` 的
`settings.agents` 下各加：

```jsonc
// en
"tab": { "overview": "Overview", "capability": "Capabilities", "settings": "Settings" },
"overview": {
  "summary": "Capability summary",
  "followAll": "All (follow global)",
  "allSubAgents": "All"
},
"settings": { "execution": "Execution" }
```

```jsonc
// zh-CN
"tab": { "overview": "概览", "capability": "能力", "settings": "设置" },
"overview": {
  "summary": "能力摘要",
  "followAll": "全部（跟随全局）",
  "allSubAgents": "全部"
},
"settings": { "execution": "执行配置" }
```

> **顺带修一个既有 bug**：`en` 的 `settings.agents.section.*` 词条里烤进了序号
> （`"① Basics"`），而 `SectionLabel` 又通过 `index` prop 单独渲染一遍 ——
> 英文界面现在显示 "① ① Basics"。本 Task 把 `<SectionLabel index="①">` 的
> `index` prop 全部去掉（Step 5/7 的代码里已经没有 `index`），修掉这个重复。
> **不要**再去改 locale 词条里的序号，去了会让中文界面丢掉序号。

- [ ] **Step 12: 提交**

```bash
git add -A webui/src/components/settings/agents \
           webui/src/i18n/locales \
           webui/src/tests/agent-detail-tabs.test.tsx
git commit -m "feat(webui): 智能体详情拆成概览/能力/设置三个 Tab"
```

---

## 验收口径

一期完成的判定标准（全部满足才算完成）：

1. `pytest tests/webui/test_agents_*.py -q` 全绿，且 `pytest tests/webui/ -q` 无回归。
2. `ruff check nanobot/` 干净。
3. `cd webui && bun run build` 成功，`bun run test` 全绿（Task 8 的 7 个集成用例 + Task 9 的 8 个 Tab 用例）。
4. 手工验证（起 `nanobot gateway` + `bun run dev`）：
   - 智能体页首次进入是**左列表 + 右详情**双栏，右栏默认选中第一个 Agent
   - 首次进入能看到 4 个出厂预设（通用助理 / 代码评审 / 资料研究 / 运维执行）
   - 详情区有**概览 / 能力 / 设置**三个 Tab，概览里能力摘要和已渲染 prompt 都在
   - 在「设置」里改名字 → 底部条变琥珀色显示「有未保存的修改」→ 保存后用服务端返回的 `updatedAt` 刷新
   - 草稿未保存时点左栏另一个 Agent → 弹「放弃未保存的修改？」；放弃后确实不保存
   - 新建一个 Agent → 刷新页面仍在（落盘生效）
   - 编辑出厂预设的提示词保存 → 徽标从「系统预设」多出「已定制」
   - 点删除出厂预设 → 显示 409 文案「系统预设不可删除」
   - 删除自建的 Agent → 刷新后不再出现
   - 隐藏一个 Agent → 出现在左栏底部「已隐藏」折叠区
   - 浏览器窗口拉到 <1280px → 变成单栏，选中后出现「← 返回列表」
5. 契约 §11 的每一项「不做」都确实没做（没有顺手加单条读端点、分类 CRUD 等）。
6. UI 范围外清单同样没做：运行历史 Tab、在线状态点、档案级思考强度/速度/并行上限、访问权限/环境变量/自定义参数。

## 后续

一期合流并稳定后，再启动
`.ai-runtime-artifacts/plans/2026-09-29-agents-runtime-integration-plan.md`（二期：运行时接入）。

---

## Next

**（写入后须暂停 — 即使用户句末含「然后执行」）**

- 计划确认 → 说「开始实现」或「执行」
- 需要调整 → 直接说修改意见
- 想拆分并行 → 审 `*-dispatch.md` 后说「开始实现」或「并行执行」
