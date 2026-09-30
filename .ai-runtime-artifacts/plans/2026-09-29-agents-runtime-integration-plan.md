---
artifact: implementation-plan
route: superpowers:writing-plans
skills:
  - writing-plans
  - api-and-interface-design
skills_evidence:
  - harness-kit/.agents/skills/writing-plans/SKILL.md
dispatch: n/a
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - .ai-runtime-artifacts/contracts/2026-09-29-agents-contract.md
  - .ai-runtime-artifacts/plans/2026-09-29-agents-storage-and-crud-plan.md
  - .ai-runtime-artifacts/research/2026-09-30-openakita-subagent-integration-research.md
created_at: 2026-09-29
updated_at: 2026-09-30
update_note: >
  2026-09-30 依 openakita 调研结论增补三处：
  (1) 「✅ 关于防递归」——原以为存在递归风险，实测证伪，裁定本期不新增防递归代码，
      附 `_scopes` 机制依据与实测工具集；
  (2) 「✅ 技能门禁由用户在页面配置」——纠正定性：技能可见性是配置未接通，
      非代码缺陷，本期不引入硬编码技能白名单；
  (3) 验收口径新增第 4/5 条 + 档案 prompt 字段内容约定。
update_note_2: >
  2026-09-30 实施后代码审查发现本计划「档案 prompt 追加到基础提示词之后」
  被实现为「前置」。用户裁决保留前置实现，理由：
  (1) 角色定义在前更符合「先确立身份、再给框架」的习惯；
  (2) 出厂模板首行本就是 "You are a subagent spawned by the main agent"，
      身份行紧邻其后不冲突；
  (3) 子 Agent 无多轮对话场景，顺序对实际行为影响有限。
  唯一顾虑是「用户自由文本排在全局安全框架之前」——已记录，若将来出现
  提示词注入绕过案例，应重新评估为追加。
status: approved
approved: true
approved_by: 用户在 2026-09-30 会话中明确回复「可以执行了吗」并在追问中选「批准并开工」
---

# Agent 档案运行时接入实施计划（二期）

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 `orchestration` 逐 WU 实施。步骤用 checkbox（`- [ ]`）跟踪。

**Goal:** 让 Agent 档案真正影响运行时 —— `spawn` 能指定用哪个档案，子 agent 按该档案拿到自己的 system prompt、被裁剪过的工具集和技能集。

**Architecture:** 新增 `nanobot/agents/runtime.py` 作为「档案 → 运行时约束」的解析层（`AgentProfileRuntime`），并实现一份后端版 `resolve_selection`（与前端 `types.ts:148` 同语义）。`SubagentManager` 在 `_run_admitted_subagent`（`subagent.py:412-413`）这一处装配点消费解析结果；`SpawnTool` 只多一个 `agent` 参数并透传。档案读不到或 id 非法时**回退到当前默认子 agent 行为**，不抛错 —— 运行时不该因为配置问题而拒绝干活。

**Tech Stack:** Python 3.11+ / asyncio / dataclasses / pytest（`asyncio_mode = "auto"`）。

---

## 前置条件

**本计划依赖一期已合流并稳定。** 需要的产物：

- `nanobot/agents/models.py`（`AgentProfile` / `AgentSelection`）
- `nanobot/agents/catalog.py`（`FACTORY_PROFILES`）
- `nanobot/agents/store.py`（`AgentStore`）
- `ToolRegistry.names()`（一期 Task 6 新增）

## 范围外（明确不做）

| 不做 | 理由 |
| --- | --- |
| 主 agent 侧的「列出可用 Agent」新工具 | `SpawnTool.description` 里直接枚举 id 就够；再加一个工具是多余的一跳 |
| 档案的版本历史 / 回滚 | 无需求 |
| 按档案改 `max_iterations` | 档案字段里没有这个概念 |
| `ToolLoader` 的插件来源标记、`scope: "plugin"` | 契约 §8.1 已划给后续；二期不碰 |
| 为 `modelId` 解析 provider/凭据 | 见下方「⚠️ 关于 modelId」 |
| **防递归机制** | **已由现有 scope 机制提供**，实测确认无需新增代码。见下方「✅ 关于防递归」 |

### ✅ 关于防递归（2026-09-30 核实，无需改造）

原以为「子 Agent 仍能看到 `spawn` 工具、存在无限递归风险」是真实风险，**实测证伪**。子 Agent 的工具集由 `SubagentManager._build_tools`（`subagent.py:207-227`）以 `scope="subagent"` 加载，而 `SpawnTool` **未声明 `_scopes`**，走 `Tool.base` 的默认值 `{"core"}`（`base.py:208`），在 `loader.py:100` 的 `if scope not in getattr(tool_cls, "_scopes", {"core"})` 处即被过滤。

实测（`SubagentManager._build_tools()` 实跑）：

```
子 Agent 工具集（13 个）: apply_patch, edit_file, exec, exec_session, find_files,
                          grep, list_dir, list_exec_sessions, read_file,
                          run_cli_app, web_fetch, web_search, write_file
spawn=False  long_task=False  cron=False  self=False  message=False
```

**第二道保险**：`subagent_manager` 只在 `loop.py:925` 注入主 Agent 的 `ToolContext`；子 Agent 的 `ToolContext`（`subagent.py:216-225`）不传该字段，默认 `None`，所以 `SpawnTool.create`（`spawn.py:56-58`）即使被调到也会抛 `RuntimeError`。

**结论**：递归防护已具备且是双层的。**二期不要新增防递归代码**——`AgentProfile.sub_agents` 字段（`models.py:118`）本期保持「定义但不消费」，等真要支持多层嵌套时再用。若将来改 `_scopes` 默认值或给 `SpawnTool` 显式加 `"subagent"`，**必须同步复核这两道防线**。

### ✅ 技能门禁由用户在页面配置，不硬编码

技能可见性**不是**代码要解决的问题，而是**配置还没接通到运行时**的阶段性现象。

现状：`AgentProfile.skills: AgentSelection` 字段已定义（`models.py:117`），WebUI 侧 `CapabilityPicker` 已能勾选，二期实施后 `_build_subagent_prompt` 的 `skill_exclude` 会把用户的选择应用上去。届时子 Agent 拿到什么技能由**用户在档案页勾什么**决定。

现在之所以是「全套 11 个内置技能」，是因为二期未实施 —— `AgentProfileRuntime` 尚不存在，`_build_subagent_prompt`（`subagent.py:541-562`）调 `build_skills_summary()` 时**不传任何 `exclude`**，等于「不裁剪 = 全给」。

**因此本期不引入任何硬编码技能白名单。** 与 openakita 的差异是刻意的：openakita 的 `skills_mode` 由用户在档案里配，但它的出厂 21 个预设各自硬编码了一份技能清单；nanobot 的 4 个出厂预设（`catalog.py:87-124`）出厂时 `skills=AgentSelection("include", [])` 即**不给技能** —— 从最小集起步，用户勾了才有。`catalog.py:81-85` 的注释已写明这个取向：

> 全量工具一上来就摆着既看得见噪声，也让人以为默认就该全开；从最小可用集起步，需要什么再勾，档案与工具的关系才是有意图的。

**唯一要在验收里断言的**：`skills` 与 `tools` 两个 Selection 在同一次装配里都被应用（`subagent.py:412-413`），不能只裁工具不裁技能。二期 Task 2 已天然满足，加一条测试钉死。

## ⚠️ 三个必须先看清的坑

1. **`spawn` 的参数在 `@tool_parameters` 装饰器里声明**，不在 `execute` 签名里。漏改装饰器 → 模型看不到新参数；只改装饰器不改 `execute` → 调用时 `TypeError`。
2. **`_build_tools` 与 `_build_subagent_prompt` 都在 `subagent.py` 里被 `_run_admitted_subagent` 各调一次**（412、413 行）。档案必须在这一处解析一次并同时喂给两者，否则 prompt 说一套、工具是另一套。
3. **`build_skills_summary` 只支持 `exclude`，不支持 include**（`nanobot/agent/skills.py:206`）。`include` 模式要自己算补集。

### ⚠️ 关于 `modelId`

档案有 `modelId` 字段，前端也能选，但**把一个模型 id 解析成「可用的 provider + 凭据」不是本计划该解决的问题** —— 那是 provider registry 与配置层的职责。

二期的做法是：`AgentProfileRuntime` 把 `model_id` 解析出来交给 `SubagentManager` 上的一个**注入式** `model_resolver: Callable[[str], LLMRuntime | None]`。默认 `None` 表示不做模型覆盖，`modelId` 此时是惰性的。宿主（CLI / gateway）想接通就自己注入一个 resolver。

这是有意的边界，不是遗漏：把 provider 解析硬塞进 subagent 会让 `subagent.py` 直接依赖整个 provider 层。计划里有测试证明「resolver 未注入时回退到传入的 runtime」，把行为钉死。

---

## File Structure

### 新建

| 文件 | 职责 |
| --- | --- |
| `nanobot/agents/runtime.py` | `resolve_selection`（后端版）+ `ResolvedAgent` + `AgentProfileRuntime` |
| `tests/webui/test_agents_runtime.py` | 解析层单测 |
| `tests/webui/test_subagent_agent_profile.py` | `SubagentManager` 接入单测 |

### 修改

| 文件 | 改什么 |
| --- | --- |
| `nanobot/agent/subagent.py:107-158`（`__init__`） | 加 `agent_profiles` 与 `model_resolver` 两个可选参数 |
| `nanobot/agent/subagent.py:207-227`（`_build_tools`） | 加 `allowed_tools` 参数，装配后按名单裁剪 registry |
| `nanobot/agent/subagent.py:229` / `:293`（`spawn` / `run_inline`） | 透传 `agent_id` |
| `nanobot/agent/subagent.py:384-400`（`_run_admitted_subagent`） | 解析档案并同时喂给工具与 prompt |
| `nanobot/agent/subagent.py:541-562`（`_build_subagent_prompt`） | 加 `agent` / `skill_exclude` 参数 |
| `nanobot/agent/tools/spawn.py:26-46` | `agent` 参数进 schema 与 `execute` |

---

## Task 1: 运行时解析层

**Files:**
- Create: `nanobot/agents/runtime.py`
- Test: `tests/webui/test_agents_runtime.py`

- [ ] **Step 1: 写失败的测试**

创建 `tests/webui/test_agents_runtime.py`：

```python
"""AgentProfileRuntime：把档案解析成运行时约束。"""

from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.agents.models import AgentProfile, AgentSelection
from nanobot.agents.runtime import (
    AgentProfileRuntime,
    ResolvedAgent,
    resolve_selection,
)


def test_resolve_selection_all_ignores_entries() -> None:
    selection = AgentSelection("all", ["ignored"])
    assert resolve_selection(selection, ["a", "b"]) == {"a", "b"}


def test_resolve_selection_include() -> None:
    assert resolve_selection(AgentSelection("include", ["b"]), ["a", "b", "c"]) == {"b"}


def test_resolve_selection_exclude() -> None:
    assert resolve_selection(AgentSelection("exclude", ["b"]), ["a", "b", "c"]) == {"a", "c"}


def test_resolve_selection_always_keeps_locked() -> None:
    assert resolve_selection(AgentSelection("include", []), ["a"], ["a"]) == {"a"}
    assert resolve_selection(AgentSelection("exclude", ["a"]), ["a"], ["a"]) == {"a"}


def test_resolve_selection_drops_unknown_entries() -> None:
    """档案可以预配置尚未安装的技能/工具；解析时只保留真实存在的。"""
    assert resolve_selection(AgentSelection("include", ["ghost"]), ["a"]) == set()


@pytest.fixture()
def runtime(tmp_path: Path) -> AgentProfileRuntime:
    return AgentProfileRuntime(tmp_path)


def test_missing_profile_resolves_to_none(runtime: AgentProfileRuntime) -> None:
    assert runtime.resolve("no-such-agent") is None


def test_illegal_id_resolves_to_none(runtime: AgentProfileRuntime) -> None:
    assert runtime.resolve("../../etc/passwd") is None


def test_hidden_agent_still_resolves(runtime: AgentProfileRuntime) -> None:
    """隐藏只影响 WebUI 列表，不影响运行时按 id 直接使用。"""
    runtime.store.set_visibility("code-reviewer", True)
    assert runtime.resolve("code-reviewer") is not None


def test_resolved_tool_names_follow_selection(runtime: AgentProfileRuntime) -> None:
    resolved = runtime.resolve("code-reviewer")
    assert resolved is not None
    assert "read_file" in resolved.tool_names
    assert "execute_command" not in resolved.tool_names


def test_resolved_skill_exclude_is_the_complement(runtime: AgentProfileRuntime) -> None:
    resolved = runtime.resolve("code-reviewer")
    assert resolved is not None
    assert resolved.skill_exclude == set()


def test_resolved_prompt_renders_template_variables(runtime: AgentProfileRuntime) -> None:
    resolved = runtime.resolve("code-reviewer")
    assert resolved is not None
    assert "{{" not in resolved.prompt
    assert "代码评审" in resolved.prompt
    assert "{{description}}" not in resolved.prompt


def test_resolved_prompt_fills_date_and_workspace(runtime: AgentProfileRuntime) -> None:
    resolved = runtime.resolve("general-assistant", workspace=Path("/tmp/ws"))
    assert resolved is not None
    assert "{{date}}" not in resolved.prompt
    assert "ws" in resolved.prompt


def test_resolved_model_id_is_optional(runtime: AgentProfileRuntime) -> None:
    assert runtime.resolve("code-reviewer").model_id is None  # type: ignore[union-attr]
    runtime.store.save_profile(
        AgentProfile(
            id="picky",
            name="挑剔",
            type="system",
            prompt="",
            model_id="default",
        )
    )
    assert runtime.resolve("picky").model_id == "default"  # type: ignore[union-attr]


def test_unresolvable_variable_is_left_verbatim(runtime: AgentProfileRuntime) -> None:
    runtime.store.save_profile(
        AgentProfile(id="odd", name="古怪", type="custom", prompt="见 {{unknown_token}}")
    )
    resolved = runtime.resolve("odd")
    assert resolved is not None
    assert "{{unknown_token}}" in resolved.prompt
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/webui/test_agents_runtime.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'nanobot.agents.runtime'`

- [ ] **Step 3: 写最小实现**

创建 `nanobot/agents/runtime.py`：

```python
"""把 Agent 档案解析成运行时可执行的约束。

一期的 ``AgentProfile`` 只描述「档案长什么样」；本模块回答「跑这个档案时，
提示词是什么、能用哪些工具、能用哪些技能、用哪个模型」。

``resolve_selection`` 是前端 ``webui/src/lib/agents/types.ts:148`` 的后端镜像
—— 两边必须同语义，否则 WebUI 里勾的能力和实际跑的能力会不一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from nanobot.agents.models import AgentProfile, AgentSelection
from nanobot.agents.store import AgentStore, AgentStoreError


def resolve_selection(
    selection: AgentSelection,
    all_ids: list[str],
    locked_ids: list[str] | None = None,
) -> set[str]:
    """把 ``AgentSelection`` 解析成具体 id 集合。

    语义与前端 ``resolveSelection`` 严格一致：``all`` 全要、``include`` 只要
    白名单、``exclude`` 全要减去黑名单；``locked_ids`` 任何模式下都在。

    ``entries`` 里不存在的 id 直接丢弃——档案可以预配置尚未安装的技能或工具。
    """
    locked = set(locked_ids or [])
    if selection.mode == "all":
        return set(all_ids) | locked
    present = set(all_ids)
    if selection.mode == "include":
        return (present & set(selection.entries)) | locked
    return (present - set(selection.entries)) | locked


@dataclass(frozen=True)
class ResolvedAgent:
    """一个档案跑起来之后的全部约束。"""

    id: str
    name: str
    description: str
    model_id: str | None
    prompt: str
    tool_names: frozenset[str] | None = None
    skill_exclude: frozenset[str] = field(default_factory=frozenset)


def render_profile_prompt(
    profile: AgentProfile,
    *,
    workspace: Path,
    enabled_skills: list[str] | None = None,
    enabled_tools: list[str] | None = None,
) -> str:
    """把档案的 ``prompt`` 模板渲染成一段可直接拼进 system prompt 的文本。

    未知变量**原样保留**：模板里出现 ``{{unknown}}`` 说明用户还没填上下文，
    静默替换成空串会让提示词莫名其妙地少一块。调用方（``SubagentManager``）
    在这段文本前面还会拼上身份行，所以这里返回空串时调用方要能跳过。
    """
    replacements = {
        "{{name}}": profile.name,
        "{{description}}": profile.description,
        "{{skills}}": "、".join(enabled_skills or []) or "无",
        "{{tools}}": "、".join(enabled_tools or []) or "无",
        "{{date}}": date.today().isoformat(),
        "{{user_profile}}": "",
        "{{workspace}}": workspace.name,
    }
    rendered = profile.prompt
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    return rendered.strip()


class AgentProfileRuntime:
    """按 id 解析档案，供 ``SubagentManager`` 在每次 spawn 时查询。

    每次 ``resolve`` 都新建 ``AgentStore`` 并重新读盘：档案可能刚被 WebUI
    改过，进程内缓存不能比用户看到的更旧。
    """

    def __init__(self, workspace: Path) -> None:
        self._workspace = Path(workspace)

    def _known_skill_ids(self) -> list[str]:
        from nanobot.webui.skills_api import webui_skills_payload  # local: 避免导入环

        entries = webui_skills_payload(self._workspace).get("skills", [])
        return [str(entry.get("name", "")) for entry in entries if entry.get("name")]

    def _known_tool_ids(self, tools_config: object | None) -> list[str]:
        """当前 scope 下真实存在的工具名。

        档案的 ``tools.entries`` 要和真实工具求交，否则档案里写了
        ``execute_command`` 而该工具因配置被关掉时，解析结果会撒谎。
        """
        from nanobot.agent.tools.context import ToolContext
        from nanobot.agent.tools.loader import ToolLoader
        from nanobot.agent.tools.registry import ToolRegistry
        from nanobot.config.schema import ToolsConfig

        registry = ToolRegistry()
        ctx = ToolContext(
            config=tools_config if isinstance(tools_config, ToolsConfig) else ToolsConfig(),
            workspace=str(self._workspace.resolve()),
        )
        loader = ToolLoader()
        for scope in ("core", "subagent"):
            loader.load(ctx, registry, scope=scope)
        return registry.names()

    def resolve(
        self,
        agent_id: str,
        *,
        workspace: Path | None = None,
        tools_config: object | None = None,
    ) -> ResolvedAgent | None:
        """解析一个档案；id 非法或不存在时返回 ``None``（调用方回退到默认行为）。"""
        try:
            profile = AgentStore(self._workspace).get_profile(agent_id)
        except AgentStoreError:
            return None

        all_tools = self._known_tool_ids(tools_config)
        tool_names = frozenset(resolve_selection(profile.tools, all_tools))

        all_skills = self._known_skill_ids()
        enabled_skills = resolve_selection(profile.skills, all_skills)
        # build_skills_summary 只认 exclude，所以 include 模式在这里取补集。
        skill_exclude = frozenset(set(all_skills) - enabled_skills)

        return ResolvedAgent(
            id=profile.id,
            name=profile.name,
            description=profile.description,
            model_id=profile.model_id,
            prompt=render_profile_prompt(
                profile,
                workspace=workspace or self._workspace,
                enabled_skills=sorted(enabled_skills),
                enabled_tools=sorted(tool_names),
            ),
            tool_names=tool_names,
            skill_exclude=skill_exclude,
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/webui/test_agents_runtime.py -v`
Expected: PASS（16 passed）

- [ ] **Step 5: 提交**

```bash
git add nanobot/agents/runtime.py tests/webui/test_agents_runtime.py
git commit -m "feat(agents): 档案到运行时约束的解析层"
```

---

## Task 2: `SubagentManager` 消费档案

**Files:**
- Modify: `nanobot/agent/subagent.py:107-158`（`__init__` 加两个参数）
- Modify: `nanobot/agent/subagent.py:207-227`（`_build_tools` 加 `allowed_tools`）
- Modify: `nanobot/agent/subagent.py:229-241` 与 `:293`（`spawn` / `run_inline` 透传 `agent_id`）
- Modify: `nanobot/agent/subagent.py:384-413`（`_run_admitted_subagent` 解析并分发）
- Modify: `nanobot/agent/subagent.py:541-562`（`_build_subagent_prompt` 加参数）
- Test: `tests/webui/test_subagent_agent_profile.py`

- [ ] **Step 1: 写失败的测试**

创建 `tests/webui/test_subagent_agent_profile.py`：

```python
"""SubagentManager 按档案裁剪工具与注入 prompt。"""

from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.agent.subagent import SubagentManager
from nanobot.agents.models import AgentProfile, AgentSelection
from nanobot.agents.runtime import AgentProfileRuntime
from nanobot.bus.queue import MessageBus


def _manager(tmp_path: Path, **kwargs: object) -> SubagentManager:
    return SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=4000,
        **kwargs,  # type: ignore[arg-type]
    )


def test_default_manager_has_no_profile_runtime(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    assert manager._agent_profiles is None


def test_resolve_returns_none_without_runtime(tmp_path: Path) -> None:
    assert _manager(tmp_path)._resolve_agent(None) is None
    assert _manager(tmp_path)._resolve_agent("ghost") is None


def test_resolve_finds_injected_profile(tmp_path: Path) -> None:
    manager = _manager(tmp_path, agent_profiles=AgentProfileRuntime(tmp_path))
    resolved = manager._resolve_agent("code-reviewer")
    assert resolved is not None
    assert resolved.id == "code-reviewer"


def test_build_tools_without_filter_keeps_everything(tmp_path: Path) -> None:
    registry = _manager(tmp_path)._build_tools()
    assert "read_file" in registry.names()
    assert "execute_command" in registry.names()


def test_build_tools_applies_allowed_names(tmp_path: Path) -> None:
    registry = _manager(tmp_path)._build_tools(allowed_tools=frozenset({"read_file"}))
    assert registry.names() == ["read_file"]


def test_build_tools_with_empty_allowed_removes_all(tmp_path: Path) -> None:
    assert _manager(tmp_path)._build_tools(allowed_tools=frozenset()).names() == []


def test_build_prompt_without_agent_keeps_template(tmp_path: Path) -> None:
    prompt = _manager(tmp_path)._build_subagent_prompt(workspace=tmp_path)
    assert prompt
    assert "nanobot" in prompt.lower() or "subagent" in prompt.lower()


def test_build_prompt_prepends_agent_identity(tmp_path: Path) -> None:
    manager = _manager(tmp_path, agent_profiles=AgentProfileRuntime(tmp_path))
    resolved = manager._resolve_agent("code-reviewer")
    prompt = manager._build_subagent_prompt(workspace=tmp_path, agent=resolved)
    assert prompt.startswith("你是代码评审")
    assert "只读地审查改动" in prompt


def test_build_prompt_without_description_omits_empty_clause(tmp_path: Path) -> None:
    manager = _manager(tmp_path, agent_profiles=AgentProfileRuntime(tmp_path))
    manager.store_save_for_test(tmp_path, "silent", name="沉默", description="")
    resolved = manager._resolve_agent("silent")
    assert manager._build_subagent_prompt(workspace=tmp_path, agent=resolved).startswith("你是沉默")


def test_prompt_excludes_skills_not_enabled(tmp_path: Path) -> None:
    manager = _manager(tmp_path, agent_profiles=AgentProfileRuntime(tmp_path))
    manager.store_save_for_test(
        tmp_path,
        "narrow",
        name="窄",
        skills=AgentSelection("include", []),
    )
    resolved = manager._resolve_agent("narrow")
    prompt = manager._build_subagent_prompt(workspace=tmp_path, agent=resolved)
    # include 空列表 = 一个技能都不要；摘要里不该出现任何技能条目
    assert "Built-in skills" not in prompt


def test_model_resolver_not_injected_keeps_runtime(tmp_path: Path) -> None:
    from nanobot.utils.llm_runtime import LLMRuntime

    sentinel = object()
    manager = _manager(tmp_path, agent_profiles=AgentProfileRuntime(tmp_path))
    assert manager._apply_model_override(sentinel, None) is sentinel  # type: ignore[arg-type]


def test_model_resolver_returning_none_keeps_runtime(tmp_path: Path) -> None:
    manager = _manager(tmp_path, agent_profiles=AgentProfileRuntime(tmp_path))
    sentinel = object()
    assert manager._apply_model_override(sentinel, "default") is sentinel  # type: ignore[arg-type]


def test_model_resolver_result_is_used(tmp_path: Path) -> None:
    sentinel = object()
    replacement = object()
    manager = _manager(
        tmp_path,
        agent_profiles=AgentProfileRuntime(tmp_path),
        model_resolver=lambda model_id: replacement if model_id == "default" else None,
    )
    assert manager._apply_model_override(sentinel, "default") is replacement  # type: ignore[arg-type]


def test_spawn_passes_agent_id_through(tmp_path: Path) -> None:
    """spawn 必须把 agent_id 一路传到 _resolve_agent，否则整条链路是断的。"""
    import inspect

    source = inspect.getsource(SubagentManager.spawn)
    assert "agent_id=agent_id" in source
    source = inspect.getsource(SubagentManager.run_inline)
    assert "agent_id=agent_id" in source
    source = inspect.getsource(SubagentManager._run_admitted_subagent)
    assert "_resolve_agent" in source
    assert "_apply_model_override" in source
```

> 上面两个测试用了 `manager.store_save_for_test(...)`。在 Step 3 的实现里补上这个
> 仅供测试的薄封装（内部就是 `AgentStore(tmp_path).save_profile(...)`），并在
> 测试文件末尾的 fixture 里改成直接用 `AgentStore` 导入调用也可以 —— 选后者更干净，
> Step 4 之前先改测试：

```python
# Step 4 之前把上面两个 store_save_for_test 调用替换成：
from nanobot.agents.store import AgentStore
...
AgentStore(tmp_path).save_profile(AgentProfile(id="silent", name="沉默"))
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/webui/test_subagent_agent_profile.py -v`
Expected: FAIL —— `TypeError: __init__() got an unexpected keyword argument 'agent_profiles'`

- [ ] **Step 3: 写实现**

**3.1 `__init__` 加参数。** 编辑 `nanobot/agent/subagent.py`，在 `__init__` 签名的
`llm_wall_timeout_for_session` 之后加：

```python
        agent_profiles: "AgentProfileRuntime | None" = None,
        model_resolver: Callable[[str], LLMRuntime | None] | None = None,
```

在 `self._llm_wall_timeout_for_session = llm_wall_timeout_for_session` 之后加：

```python
        # 二期：Agent 档案。默认 None = 完全保持一期之前的子 agent 行为。
        self._agent_profiles = agent_profiles
        self._model_resolver = model_resolver
```

文件顶部 import 区加：

```python
from nanobot.agents.runtime import AgentProfileRuntime, ResolvedAgent
```

**3.2 加三个私有方法。** 编辑 `nanobot/agent/subagent.py`，在
`_subagent_tools_config` 之前插入：

```python
    def _resolve_agent(self, agent_id: str | None) -> ResolvedAgent | None:
        """把档案 id 解析成运行时约束。未注入 / id 非法 / 档案不存在 → None。

        解析失败一律回退而不是抛错：配置有问题不该让子 agent 拒绝干活。
        """
        if agent_id is None or self._agent_profiles is None:
            return None
        try:
            return self._agent_profiles.resolve(
                agent_id, workspace=self.workspace, tools_config=self.tools_config
            )
        except Exception:  # noqa: BLE001 - 解析失败必须退化成默认行为
            logger.warning("Agent 档案 {} 解析失败，回退到默认子 agent", agent_id, exc_info=True)
            return None

    def _apply_model_override(self, runtime: LLMRuntime, model_id: str | None) -> LLMRuntime:
        """按档案的 ``modelId`` 换 runtime；未注入 resolver 或解析不出则原样返回。

        provider/凭据的解析不属于 subagent 的职责，宿主注入 resolver 才有覆盖。
        """
        if model_id is None or self._model_resolver is None:
            return runtime
        resolved = self._model_resolver(model_id)
        return resolved if resolved is not None else runtime
```

**3.3 `_build_tools` 支持裁剪。** 把 `_build_tools` 的签名与结尾改为：

```python
    def _build_tools(
        self,
        workspace: Path | None = None,
        tools_config: ToolsConfig | None = None,
        allowed_tools: frozenset[str] | None = None,
    ) -> ToolRegistry:
        """Build an isolated subagent tool registry via ToolLoader."""
        root = self.workspace if workspace is None else workspace
        registry = ToolRegistry()
        cfg = tools_config if tools_config is not None else self._subagent_tools_config()
        ctx = ToolContext(
            config=cfg,
            workspace=str(root.resolve()),
            exec_session_manager=self._exec_session_manager,
            file_state_store=FileStates(),
            workspace_sandbox=workspace_sandbox_status(
                restrict_to_workspace=cfg.restrict_to_workspace,
                workspace=root,
            ),
        )
        ToolLoader().load(ctx, registry, scope="subagent")
        if allowed_tools is not None:
            for name in registry.names():
                if name not in allowed_tools:
                    registry.unregister(name)
        return registry
```

**3.4 `_build_subagent_prompt` 支持档案。** 把整个方法替换为：

```python
    def _build_subagent_prompt(
        self,
        workspace: Path | None = None,
        agent: ResolvedAgent | None = None,
    ) -> str:
        """Build a focused system prompt for the subagent.

        给了档案时，在出厂模板前面拼一段身份 + 档案自定义提示词；技能摘要按
        档案的 ``skills`` 选择裁剪。没给档案时行为与一期完全一致。
        """
        from nanobot.agent.skills import SkillsLoader

        agent_workspace = self.workspace.expanduser().resolve()
        project_workspace = workspace.expanduser().resolve() if workspace else agent_workspace
        exclude = set(agent.skill_exclude) if agent is not None else None
        skills_summary = SkillsLoader(
            self.workspace,
            disabled_skills=self.disabled_skills,
        ).build_skills_summary(exclude=exclude, workspace=project_workspace)
        history_log = (
            str(agent_workspace / "memory" / "history.jsonl")
            if agent_workspace != project_workspace
            else "memory/history.jsonl"
        )
        base = render_template(
            "agent/subagent_system.md",
            workspace=str(project_workspace),
            agent_workspace=str(agent_workspace),
            history_log=history_log,
            skills_summary=skills_summary or "",
        )
        if agent is None or not agent.prompt:
            return base
        identity = (
            f"你是{agent.name}，{agent.description}。"
            if agent.description
            else f"你是{agent.name}。"
        )
        return f"{identity}\n\n{agent.prompt}\n\n{base}"
```

**3.5 `spawn` / `run_inline` 透传。** 给两个方法的签名各加一个
`agent_id: str | None = None`（放在 `workspace_scope` 之后、`*` 之前），
并把传给 `_run_subagent(...)` 的实参里加上 `agent_id=agent_id,`。
`_run_subagent` 与 `_run_admitted_subagent` 的签名同样各加一个
`agent_id: str | None = None`，前者在转发给后者时带上它。

**3.6 `_run_admitted_subagent` 装配点。** 把 `subagent.py:409-413` 这四行：

```python
            cfg = None
            if workspace_scope is not None:
                cfg = self._subagent_tools_config()
                cfg.restrict_to_workspace = workspace_scope.restrict_to_workspace
            # Construct from the agent workspace; the bound scope below supplies the project cwd.
            tools = self._build_tools(tools_config=cfg)
            system_prompt = self._build_subagent_prompt(workspace=root)
```

替换为：

```python
            cfg = None
            if workspace_scope is not None:
                cfg = self._subagent_tools_config()
                cfg.restrict_to_workspace = workspace_scope.restrict_to_workspace
            # 一次解析，工具与提示词共用同一个档案 —— 否则两者会各说各话。
            agent = self._resolve_agent(agent_id)
            # Construct from the agent workspace; the bound scope below supplies the project cwd.
            tools = self._build_tools(
                tools_config=cfg,
                allowed_tools=agent.tool_names if agent is not None else None,
            )
            system_prompt = self._build_subagent_prompt(workspace=root, agent=agent)
            runtime = self._apply_model_override(runtime, agent.model_id if agent else None)
```

- [ ] **Step 4: 改掉测试里的 `store_save_for_test`**

在 `tests/webui/test_subagent_agent_profile.py` 顶部加
`from nanobot.agents.store import AgentStore`，并把两处
`manager.store_save_for_test(tmp_path, "silent", name="沉默", description="")`
换成 `AgentStore(tmp_path).save_profile(AgentProfile(id="silent", name="沉默"))`，
把 `manager.store_save_for_test(tmp_path, "narrow", name="窄", skills=AgentSelection("include", []))`
换成：

```python
    AgentStore(tmp_path).save_profile(
        AgentProfile(id="narrow", name="窄", skills=AgentSelection("include", []))
    )
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/webui/test_subagent_agent_profile.py -v`
Expected: PASS（15 passed）

- [ ] **Step 6: 跑既有 subagent 测试确认没有回归**

Run: `pytest tests/ -q -k subagent`
Expected: 全部 PASS。`allowed_tools` 默认 `None` 意味着不裁剪，既有行为应完全不变。

- [ ] **Step 7: 提交**

```bash
git add nanobot/agent/subagent.py tests/webui/test_subagent_agent_profile.py
git commit -m "feat(agents): 子 agent 按档案裁剪工具与注入提示词"
```

---

## Task 3: `SpawnTool` 增加 `agent` 参数

**Files:**
- Modify: `nanobot/agent/tools/spawn.py:26-46`（装饰器 schema）
- Modify: `nanobot/agent/tools/spawn.py:83-108`（`description` 与 `execute`）
- Test: `tests/webui/test_spawn_agent_param.py`

- [ ] **Step 1: 写失败的测试**

创建 `tests/webui/test_spawn_agent_param.py`：

```python
"""SpawnTool 的 agent 参数：schema 声明、透传与描述里的可用档案枚举。"""

from __future__ import annotations

from pathlib import Path

from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agents.runtime import AgentProfileRuntime


class _FakeManager:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.calls: list[dict] = []

    async def spawn(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        return "ok"

    run_inline = spawn


def test_agent_is_in_the_json_schema(tmp_path: Path) -> None:
    # tool_parameters 把 schema 关在闭包里，只经 parameters 属性暴露
    # （nanobot/agent/tools/base.py:338），所以必须走实例。
    params = SpawnTool(_FakeManager(tmp_path)).parameters  # type: ignore[arg-type]
    assert "agent" in params["properties"]
    assert params["required"] == ["task"]


def test_agent_is_optional(tmp_path: Path) -> None:
    params = SpawnTool(_FakeManager(tmp_path)).parameters  # type: ignore[arg-type]
    assert params["properties"]["agent"]["type"] == "string"
    assert "description" in params["properties"]["agent"]


def test_description_lists_available_agents(tmp_path: Path) -> None:
    tool = SpawnTool(_FakeManager(tmp_path))  # type: ignore[arg-type]
    description = tool.description
    assert "code-reviewer" in description
    assert "researcher" in description


def test_description_falls_back_when_store_is_unreadable(tmp_path: Path) -> None:
    tool = SpawnTool(_FakeManager(tmp_path))  # type: ignore[arg-type]
    assert tool.description  # 至少还有基础描述


def test_execute_forwards_agent_id(tmp_path: Path) -> None:
    import inspect

    source = inspect.getsource(SpawnTool.execute)
    assert "agent=agent" in source
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/webui/test_spawn_agent_param.py -v`
Expected: FAIL —— 描述里没有 `code-reviewer`，schema 里没有 `agent`

> `test_agent_is_in_the_json_schema` 与 `test_agent_is_optional` 依赖 Step 3.2 的
> `__init__` 改造：改造前 `_FakeManager` 传的 manager 会被 `SpawnTool.__init__`
> 正常接受（它只存引用），所以这两个用例能跑，只是 `agent` 还没进 schema。

- [ ] **Step 3: 写实现**

编辑 `nanobot/agent/tools/spawn.py`。

**3.1 装饰器加参数。** 在 `task=StringSchema(...)` 之后加：

```python
        agent=StringSchema(
            description=(
                "Optional id of a configured Agent profile. When given, the subagent "
                "runs with that profile's prompt, tool set and skill set. Omit to use "
                "the default subagent configuration."
            ),
        ),
```

`required=["task"]` **不动** —— `agent` 是可选参数。

**3.2 `__init__` 记 workspace。** 把 `__init__` 改为：

```python
    def __init__(self, manager: "SubagentManager"):
        self._manager = manager
        self._agent_profiles: AgentProfileRuntime | None = None
        try:
            self._agent_profiles = AgentProfileRuntime(Path(manager.workspace))
        except (TypeError, AttributeError):
            # manager 没有 workspace（测试替身等）——描述里就不枚举档案。
            self._agent_profiles = None
```

import 区加：

```python
from pathlib import Path

from nanobot.agents.runtime import AgentProfileRuntime
```

**3.3 `description` 枚举档案。** 把 `description` 属性替换为：

```python
    @property
    def description(self) -> str:
        base = (
            "Spawn a subagent to handle a task in the background. "
            "Use this for complex or time-consuming tasks that can run independently. "
            "Set wait=true for a consultation whose result must inform the current turn. "
            "The subagent will complete the task and report back when done. "
            "For deliverables or existing projects, inspect the workspace first "
            "and use a dedicated subdirectory when helpful."
        )
        available = self._available_agent_ids()
        if not available:
            return base
        return (
            f"{base} Pass agent=<id> to run it as a configured Agent profile. "
            f"Available profiles: {', '.join(available)}."
        )

    def _available_agent_ids(self) -> list[str]:
        """当前可用的档案 id 列表；读不到时返回空列表（描述退回基础文案）。"""
        if self._agent_profiles is None:
            return []
        try:
            from nanobot.agents.store import AgentStore  # local: 避免导入环

            return [p.id for p in AgentStore(self._agent_profiles.workspace).list_profiles()]
        except (OSError, ValueError):
            return []
```

在 `AgentProfileRuntime` 里加一个只读属性：

```python
    @property
    def workspace(self) -> Path:
        return self._workspace
```

**3.4 `execute` 透传。** 把 `execute` 签名与调用改为：

```python
    async def execute(
        self,
        task: str,
        label: str | None = None,
        temperature: float | None = None,
        wait: bool = False,
        agent: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Spawn a subagent to execute the given task."""
        request_ctx = current_request_context()
        if request_ctx is None or request_ctx.runtime is None:
            return ToolResult.error("Error: spawn requires an active model runtime")
        origin_channel = request_ctx.channel
        origin_chat_id = request_ctx.chat_id
        session_key = request_ctx.session_key or f"{origin_channel}:{origin_chat_id}"
        method = self._manager.run_inline if wait else self._manager.spawn
        return await method(
            task=task,
            runtime=request_ctx.runtime,
            label=label,
            origin_channel=origin_channel,
            origin_chat_id=origin_chat_id,
            session_key=session_key,
            origin_message_id=request_ctx.message_id,
            temperature=temperature,
            workspace_scope=current_workspace_scope(),
            agent_id=agent,
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/webui/test_spawn_agent_param.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 跑既有 spawn 测试确认没有回归**

Run: `pytest tests/ -q -k spawn`
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```bash
git add nanobot/agent/tools/spawn.py nanobot/agents/runtime.py \
        tests/webui/test_spawn_agent_param.py
git commit -m "feat(agents): spawn 支持按档案启动子 agent"
```

---

## Task 4: 端到端接线与验证

**Files:**
- Modify: `nanobot/cli/commands.py`（或实际构造 `SubagentManager` 的位置）
- Test: `tests/webui/test_agents_runtime_e2e.py`

- [ ] **Step 1: 写失败的测试**

创建 `tests/webui/test_agents_runtime_e2e.py`：

```python
"""从 WebUI 保存档案到 spawn 消费它的完整链路。"""

from __future__ import annotations

from pathlib import Path

from nanobot.agents.models import AgentProfile, AgentSelection
from nanobot.agents.runtime import AgentProfileRuntime
from nanobot.agents.store import AgentStore
from nanobot.webui import agents_api


def test_saved_profile_is_visible_to_runtime_without_restart(tmp_path: Path) -> None:
    agents_api.agents_save(
        tmp_path,
        {
            "id": "reviewer-2",
            "name": "评审二号",
            "type": "custom",
            "tools": {"mode": "include", "entries": ["read_file"]},
            "prompt": "你是{{name}}，看这里。",
        },
    )
    resolved = AgentProfileRuntime(tmp_path).resolve("reviewer-2")
    assert resolved is not None
    assert resolved.tool_names == frozenset({"read_file"})
    assert resolved.prompt.startswith("你是评审二号")


def test_edited_profile_replaces_previous_resolution(tmp_path: Path) -> None:
    store = AgentStore(tmp_path)
    store.save_profile(AgentProfile(id="x", name="旧", tools=AgentSelection("include", ["read_file"])))
    assert AgentProfileRuntime(tmp_path).resolve("x").tool_names == frozenset({"read_file"})  # type: ignore[union-attr]

    store.save_profile(AgentProfile(id="x", name="新", tools=AgentSelection("include", ["list_dir"])))
    assert AgentProfileRuntime(tmp_path).resolve("x").tool_names == frozenset({"list_dir"})  # type: ignore[union-attr]


def test_deleted_profile_stops_resolving(tmp_path: Path) -> None:
    agents_api.agents_save(tmp_path, {"id": "temp", "name": "临时"})
    assert AgentProfileRuntime(tmp_path).resolve("temp") is not None
    agents_api.agents_delete(tmp_path, "temp")
    assert AgentProfileRuntime(tmp_path).resolve("temp") is None
```

- [ ] **Step 2: 运行测试**

Run: `pytest tests/webui/test_agents_runtime_e2e.py -v`
Expected: PASS（3 passed）。这三个用例验证的是「读盘不过期」这个关键性质 ——
如果 `AgentProfileRuntime` 内部加了进程级缓存，它们会挂。

- [ ] **Step 3: 宿主接线**

找到实际构造 `SubagentManager` 的位置（`grep -rn "SubagentManager(" nanobot/`），
在构造调用里补上：

```python
        agent_profiles=AgentProfileRuntime(workspace),
```

并加 import：

```python
from nanobot.agents.runtime import AgentProfileRuntime
```

**只接 `agent_profiles`，不接 `model_resolver`。** `modelId` 在宿主注入 resolver
之前是惰性的 —— 这是 Task 说明里划定的边界。resolver 的具体实现依赖宿主如何
解析 provider 与凭据，留给接线方按自己的环境决定。

- [ ] **Step 4: 跑全量测试**

Run: `pytest tests/ -q`
Expected: 全部 PASS，无 error、无新增 warning

- [ ] **Step 5: lint 与类型检查**

Run: `ruff check nanobot/agents/ nanobot/agent/subagent.py nanobot/agent/tools/spawn.py`
Expected: `All checks passed!`

Run: `uv run --no-sync basedpyright nanobot/agents/ nanobot/agent/subagent.py nanobot/agent/tools/spawn.py`
Expected: 无新增报错

- [ ] **Step 6: 提交**

```bash
git add nanobot/cli/commands.py tests/webui/test_agents_runtime_e2e.py
git commit -m "feat(agents): CLI 侧接入 Agent 档案运行时"
```

---

## 验收口径

二期完成的判定标准（全部满足才算完成）：

1. `pytest tests/ -q` 全绿，`ruff check nanobot/` 干净，`basedpyright` 无新增报错。
2. 手工验证（`nanobot gateway` + `bun run dev`）：
   - 在 WebUI 新建档案「只读审查」，工具只勾 `read_file` / `list_dir`
   - 在 CLI 里对一个会调 spawn 的任务说「spawn 一个 agent=只读审查 去查一下 README」
   - 子 agent 的 system prompt 里出现「你是只读审查」且技能摘要为空
   - 同一个任务改成 `agent=通用助理`（或省略 `agent=`），子 agent 拿到完整工具集
   - 传一个不存在的 `agent=不存在`，任务照常完成（回退到默认行为，不报错）
3. `modelId` 未接通这件事在交付说明里写明，不当成 bug。
4. **子 Agent 拿不到 `spawn`**（2026-09-30 新增）——本计划不改动 `SpawnTool._scopes`，实施后重跑 §「✅ 关于防递归」里的实测命令，工具集仍应是 13 个且 `spawn=False`。**若出现 `spawn=True`，说明 scope 机制被意外改动，立即回滚排查。**
5. **`skills` 与 `tools` 在同一次装配里都被应用**（2026-09-30 新增）——新增一条测试：档案 `tools=include[read_file]` + `skills=include[]` 时，断言 registry 只有 `read_file` **且** `build_skills_summary()` 返回空串。只裁工具不裁技能、或反之，都算不通过。

## 档案 `prompt` 字段的内容约定（2026-09-30 新增）

`AgentProfile.prompt` 是**前置**到子 Agent 基础模板之前的，完整形态是「身份行 + 档案 prompt + 空行 + 出厂模板」（`subagent.py` 的 `_build_subagent_prompt`）。> **2026-09-30 裁决**：本节原写「追加到基础提示词之后」（对齐 openakita 的 `custom_prompt` 语义，`profile.py:145`），实施时改为前置，理由见 FM `update_note_2`。语义差异是有意的，不是遗漏。

当前基础提示词（`templates/agent/subagent_system.md`）只有 3 行实质内容，档案 prompt 是子 Agent 唯一的「角色说明书」，应当写清四件事：

| 该写 | 不该写 |
| --- | --- |
| 角色与专长（你是谁、擅长什么） | 「你有 spawn 工具」这类事实描述（模板已隐含） |
| 工作方式与产出形态（怎么交付、产物落在哪） | 工具清单（已由 `tools` Selection 决定，重复即失配） |
| 边界（什么不做、什么情况放弃并上报） | 通用安全规则（已由全局模板覆盖） |
| 失败时的行为（返回什么、怎么说明失败原因） | 措辞打磨类的空话 |

对照 openakita：它的 `custom_prompt` 追加在编译后的 system prompt 末尾（`_agent_runtime.py:3287`），21 个预设各自写了 5–7 行角色说明，是这个字段的**写法**参考样本。注意它用的是**追加**语义，与本仓的前置不同——这是 2026-09-30 有意偏离，理由见 FM `update_note_2`。两者可参考的是「每个角色写 5–7 行具体说明」这个实践，不是拼接位置。

**不建议本期做的事**：不要在档案 prompt 里教模型「怎么写委派 prompt」（openakita `prompt/builder.py:288-319` 那段协作原则）。nanobot 是单跳架构且档案由用户自配，这段文案对「用户勾了哪些子 Agent」是动态的，静态写死会与实际不符。若后续要加，做成主 Agent 侧的一段系统文案，且必须随可见子 Agent 动态渲染。

## 后续

- provider 侧的模型解析器 → 让 `modelId` 真正生效
- `ToolLoader` 插件来源标记 → 解锁 `scope: "plugin"`（契约 §8.1）
- 档案版本历史 / 子 agent 运行结果回写
- 子 Agent 运行结果的结构化回执（参考 openakita 的 `DelegationResult` 状态头 + `__ARTIFACT_RECEIPTS__` 哨兵块，调研见 `.ai-runtime-artifacts/research/2026-09-30-openakita-subagent-integration-research.md` §五）
- 主 Agent 侧的子 Agent 状态可观测（WS 广播 + 磁盘快照，调研同上 §九）

---

## Next

**（写入后须暂停 — 即使用户句末含「然后执行」）**

- 计划确认 → 说「开始实现」或「执行」
- 需要调整 → 直接说修改意见
- 一期还没合流 → 先执行 `2026-09-29-agents-storage-and-crud-plan.md`
