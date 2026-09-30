---
artifact: implementation-plan
route: superpowers:writing-plans
skills:
  - writing-plans
skills_evidence:
  - harness-kit/.agents/skills/writing-plans/SKILL.md
dispatch: n/a
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - .ai-runtime-artifacts/plans/2026-09-29-agents-runtime-integration-plan.md
  - .ai-runtime-artifacts/research/2026-09-30-openakita-subagent-integration-research.md
  - 本次会话的 reviewer / security-auditor / explorer 审查报告（见 §审查结论）
created_at: 2026-09-30
status: approved
approved: true
approved_by: 用户在 2026-09-30 会话中明确回复「可以的」（计划确认）
---

# Agent 档案运行时审查修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: 逐 Task 执行，步骤用 checkbox 跟踪。

**Goal:** 修掉二期实施后代码审查 BLOCK 的 2 项偏离与 3 项 Important，并把 B-1 的决策反转补记进原计划。

**Architecture:** 三个 Task。Task 1 重写一条假测试（后续所有修复的防线），Task 2 补 plan 决策记录 + 修 `render_profile_prompt` 的二次替换，Task 3 修 WebUI 能力选择器与运行时生效集不一致。三者文件不相交，可并行。

**Tech Stack:** Python 3.11+ / pytest（`asyncio_mode = "auto"`）/ React + TypeScript / bun test。

---

## 审查结论摘要

二期（4 个 commit：`46307b5` / `e0d42de` / `c5398a4` / `4657e2f`）实施后跑了独立代码审查，结论 **BLOCK**。全量测试 6784 passed / 15 failed，15 个失败在 `a397aca` 基线上同样失败（网络与进程相关），与本次改动无关。

| 编号 | 严重级别 | 问题 | 处置 |
| --- | --- | --- | --- |
| **B-1** | 🔴 阻塞 | 档案 prompt 实现为**前置**，原计划明写**追加** | 用户已裁决：保留前置，补记 plan 理由（Task 2） |
| **B-2** | 🔴 阻塞 | WebUI 能力选择器展示集 ≠ 运行时生效集 | Task 3 |
| **I-1** | 🟠 Important | 源码字符串断言掩盖一条未测的传递跳 | Task 1 |
| **I-2** | 🟠 Important | `render_profile_prompt` 单趟 `str.replace` 会二次替换 | Task 2 |
| **I-3** | 🟠 Important | `except Exception` + 跨模块私有调用 | 另立 WU（见 §范围外） |
| S-1 | 🟡 | 前端 `{{model}}` 变量后端未实现 | 另立 WU（见 §范围外） |
| S-2 | 🟡 | `{{user_profile}}` 恒替换为空串 | 另立 WU（见 §范围外） |
| S-3 | 🟡 | `hidden` 字段代码里零生效 | 另立 WU（见 §范围外） |
| S-4 | 🟡 | `_build_tools` 先全量构造再按白名单丢弃 | 另立 WU（见 §范围外） |

### 审查确认为「无问题」的（记录以免重复排查）

- `runtime = self._apply_model_override(...)` 的参数遮蔽 —— 函数体内 464 行之前不读 `runtime`，后续两处消费点均在其后，正确
- `allowed_tools` 逐个 `unregister` —— 白名单语义，`registry.names()` 返回新 list 不会迭代中改 dict，`unregister` 显式失效 `_cached_definitions`
- `exclude=set()` ≡ `exclude=None` —— `skills.py:240` 的 `not exclude` 短路使两者严格等价
- 档案**无法提权** —— `tool_names ⊆ all_tools`，且 `all_tools` 来自带真实 `ToolsConfig` 的 `ToolLoader`。这是本设计的安全优点
- 路径穿越 —— `catalog.is_valid_agent_id` 白名单 + `store.py:77` `is_relative_to` 双闸
- skills 维度前后端一致 —— 两侧都用 `filter_unavailable=False`
- 性能 —— 每次 spawn 多付约 60 ms，相对多轮 LLM 调用可忽略

---

## 范围外（明确不做）

| 不做 | 理由 |
| --- | --- |
| I-3（`except Exception` + 私有调用） | 根因在 `config/schema.py:790-794` 承诺了「运行时惰性补全」却没兑现。修它要动 config 层，影响面超出二期范围，且需要独立的回归验证 |
| S-1（`{{model}}`） | 前端 `PROMPT_VARIABLES` 目前**无任何消费方**（`PromptSection.tsx:22` 明说「变量直接手写」），是潜伏问题不是现网 bug |
| S-2（`{{user_profile}}` 恒空） | 要接真实来源需读 `identity/compiler.py` 的运行时上下文，超出档案运行时边界 |
| S-3（`hidden` 零生效） | 一期遗留。但本次已把 hidden 档案 id 推给 LLM 可见的工具描述，**建议在 S-3 修好前不要在 UI 里依赖 hidden** |
| S-4（先全量构造再丢弃） | 当前所有工具的 `create()` 都是纯构造，无资源持有。等有了资源持有再做 |
| 子 Agent 前端展现 | 已确认另起一份计划。本计划不含任何 WS / bus / 前端组件改动 |

---

## ⚠️ 三个必须先看清的坑

1. **`B-2` 的真实形态比审查描述的更严重**：不是「25 vs 13」，而是 `locked` 判定让**所有 14 个工具都被锁死**。`agents_api.py:143-150` 的 `scope` 字段永远算不出 `subagent`（条件要求 `core not in scopes`，但所有子 Agent 工具都同时声明了 `core`），`locked` 永远为 true（`{"core","subagent"} & {"core","memory"}` 非空）。实测：14 个工具全部 `scope="core"` + `locked=true`。**只看审查报告的「25 vs 13」会修错方向。**

2. **`_tool_descriptors` 加载 `core` scope 时 `SpawnTool.create` 会抛 `RuntimeError`**（无 `subagent_manager`），被 `loader.py:122` 吞掉。所以 `spawn` 根本不在 WebUI 工具列表里。这**符合**运行时语义（子 Agent 拿不到 spawn），但原因是崩溃而非设计——改 B-2 时不要顺手「修好」它，否则会把 spawn 暴露给用户勾选。

3. **B-1 的补记不是改代码**。用户裁决保留前置实现，改的是 `.ai-runtime-artifacts/plans/2026-09-29-agents-runtime-integration-plan.md` 的文档。**不要改实现，也不要改已通过的 `test_build_prompt_prepends_agent_identity`。**

---

## File Structure

### 新建

| 文件 | 职责 |
| --- | --- |
| `tests/webui/test_prompt_template_render.py` | 模板渲染的二次替换与转义测试 |
| `webui/src/tests/capability-picker-scope.test.ts` | 能力选择器的 locked 语义测试 |

### 修改

| 文件 | 改什么 |
| --- | --- |
| `tests/webui/test_subagent_agent_profile.py:133-143` | 删掉 `inspect.getsource` 测试，改为行为测试 |
| `nanobot/agents/runtime.py:71-84` | `render_profile_prompt` 改单趟 `re.sub` |
| `.ai-runtime-artifacts/plans/2026-09-29-agents-runtime-integration-plan.md` | 补 B-1 决策反转记录（update_note） |
| `nanobot/webui/agents_api.py:143-150` | `scope` / `locked` 判定改按子 Agent 实际可用性 |
| `webui/src/lib/agents/catalog.ts` | 适配新的 `locked` 语义（若需要） |

---

## Task 1: 换成真行为测试（I-1）

**Files:**
- Modify: `tests/webui/test_subagent_agent_profile.py`
- Test: 同文件

- [ ] **Step 1: 读现有测试确认要替换的范围**

`test_spawn_passes_agent_id_through` 用 `inspect.getsource` 断言源码含 `"agent_id=agent_id"`，只覆盖传递链第 1 跳（`spawn` / `run_inline`）与第 4 跳的存在性（`_run_admitted_subagent` 源码出现过 `_resolve_agent`）。

**第 2→3 跳（`_run_subagent` 是否把 `agent_id` 转发给 `_run_admitted_subagent`）没有任何断言。** 已核实：`tests/` 里 10 处 `await mgr._run_subagent(...)` 调用没有一个传 `agent_id`。所以把 `subagent.py` 里 `_run_subagent` 转发的 `agent_id=agent_id,` 那行删掉，43 个测试全绿而功能失效。

- [ ] **Step 2: 写替换测试**

删除 `test_spawn_passes_agent_id_through`，替换为：

```python
@pytest.mark.asyncio
async def test_agent_id_reaches_admitted_subagent(tmp_path: Path) -> None:
    """agent_id 必须一路从 spawn 传到 _run_admitted_subagent。

    传递链有 4 跳：spawn → _run_subagent → _run_admitted_subagent → _resolve_agent。
    少任何一跳，下面的断言都会失败。
    """
    seen: list[str | None] = []

    async def _capture(task_id, task, label, origin, status, runtime,
                       origin_message_id=None, workspace_scope=None,
                       agent_id=None, *, announce=True):
        seen.append(agent_id)
        return "done"

    manager = _manager(tmp_path)
    with patch.object(manager, "_run_admitted_subagent", _capture):
        await manager.run_inline(task="t", agent_id="code-reviewer")

    assert seen == ["code-reviewer"]


@pytest.mark.asyncio
async def test_spawn_forwards_agent_id_to_background_task(tmp_path: Path) -> None:
    """后台 spawn 同样要带上 agent_id（走 _run_subagent 中转）。"""
    seen: list[str | None] = []

    async def _capture(task_id, task, label, origin, status, runtime,
                       origin_message_id=None, workspace_scope=None,
                       agent_id=None, *, announce=True):
        seen.append(agent_id)
        return "done"

    manager = _manager(tmp_path)
    with patch.object(manager, "_run_admitted_subagent", _capture):
        # run_inline 走同步路径；后台 spawn 需等 task 完成
        async with manager._run_slots:
            await manager._run_subagent(
                "tid", "t", "lbl", _origin(), _status(), _runtime(),
                agent_id="researcher",
            )

    assert seen == ["researcher"]
```

需要的 import 与辅助：

```python
from unittest.mock import MagicMock, patch

from nanobot.providers.base import GenerationSettings, LLMProvider
from nanobot.utils.llm_runtime import LLMRuntime


def _runtime() -> LLMRuntime:
    provider = MagicMock(spec=LLMProvider)
    provider.generation = GenerationSettings()
    return LLMRuntime.capture(provider, "test-model", context_window_tokens=128_000)


def _origin() -> dict[str, object]:
    return {"channel": "cli", "chat_id": "c1", "session_key": "cli:c1"}


def _status() -> "SubagentStatus":
    from nanobot.agent.subagent import SubagentStatus

    return SubagentStatus(task_id="tid", label="lbl", task_description="t", started_at=0.0)
```

- [ ] **Step 3: 验证测试能捕获回归（关键步骤）**

临时注释掉 `nanobot/agent/subagent.py` 中 `_run_subagent` 转发调用里的 `agent_id=agent_id,` 一行，跑测试。

Expected: `test_agent_id_reaches_admitted_subagent` **FAIL**。

立刻恢复该行，再跑一次 Expected: PASS。这一步证明新测试真的有防线——旧测试无论如何都拦不住这个改动。

- [ ] **Step 4: 跑全组测试**

Run: `pytest tests/webui/test_subagent_agent_profile.py -q`
Expected: 全部 PASS（数量会与原来不同，删 1 加 2）

- [ ] **Step 5: 提交**

```bash
git add tests/webui/test_subagent_agent_profile.py
git commit -m "test(agents): 用行为测试替代源码字符串断言"
```

---

## Task 2: 模板二次替换 + B-1 决策补记（I-2 + B-1）

**Files:**
- Modify: `nanobot/agents/runtime.py:71-84`
- Modify: `.ai-runtime-artifacts/plans/2026-09-29-agents-runtime-integration-plan.md`
- Test: `tests/webui/test_prompt_template_render.py`（新建）

- [ ] **Step 1: 写失败的测试**

创建 `tests/webui/test_prompt_template_render.py`：

```python
"""档案 prompt 模板渲染：单次替换语义。"""

from __future__ import annotations

from pathlib import Path

from nanobot.agents.models import AgentProfile
from nanobot.agents.runtime import render_profile_prompt


def _render(prompt: str, *, name: str = "档案", description: str = "") -> str:
    profile = AgentProfile(id="p", name=name, description=description, prompt=prompt)
    return render_profile_prompt(profile, workspace=Path("/tmp/ws"))


def test_unknown_variable_kept_verbatim() -> None:
    assert "{{unknown}}" in _render("见 {{unknown}}")


def test_name_containing_token_is_not_substituted() -> None:
    """档案名本身含 {{...}} 时不能被二次替换。

    失败场景：用户把档案命名为「{{tools}}」，单趟 replace 会先替换 {{name}}
    得到「{{tools}}」，再替换 {{tools}} 把它展开成工具清单——档案名被静默改写。
    """
    out = _render("名字={{name}}", name="{{tools}}")
    assert out == "名字={{tools}}"


def test_description_containing_token_is_not_substituted() -> None:
    out = _render("描述={{description}}", description="见 {{date}}")
    assert out == "描述=见 {{date}}"


def test_jinja_escape_four_braces_is_consumed_as_one() -> None:
    """{{{{name}}}} 是「字面量 {{name}}」的写法。

    单趟 str.replace 会得到 '{{正常}}' 这种半截 token，用户看不出出错了。
    改成单趟正则后，四个花括号应被识别为「一个转义 token」并原样输出。
    """
    out = _render("转义 {{{{name}}}} 应保持")
    assert out == "转义 {{name}} 应保持"


def test_known_variables_still_render() -> None:
    out = _render("{{name}} / {{description}} / {{workspace}} / {{date}}",
                  name="甲", description="乙")
    assert out.startswith("甲 / 乙 / ws / ")


def test_empty_prompt_returns_empty_string() -> None:
    assert _render("") == ""
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/webui/test_prompt_template_render.py -v`
Expected: `test_name_containing_token_is_not_substituted` 与 `test_jinja_escape_four_braces_is_consumed_as_one` **FAIL**

- [ ] **Step 3: 改实现**

编辑 `nanobot/agents/runtime.py` 的 `render_profile_prompt`。把循环 `str.replace` 换成单趟正则：

```python
_TOKEN_RE = re.compile(r"\{\{(\w+)\}\}|\{\{\{\{(\w+)\}\}\}\}")
```

实现要点：
- 未在 `replacements` 里的 `\w+` → **原样保留该 token**（保住现有 docstring 承诺）
- 四花括号形式 → 输出为双花括号包裹的 `{{name}}`（标准 Jinja 转义语义）
- 替换值**不再参与后续替换**（单趟保证）

改完后同步更新 `runtime.py` 顶部 import（加 `import re`），并把 docstring 里「未知变量原样保留」那段补一句说明单趟语义。

**注意**：`{{user_profile}}` 当前恒替换为空串（S-2），本计划**不动**它——它已在 `replacements` 里，行为保持不变。`{{model}}`（S-1）同样不动。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/webui/test_prompt_template_render.py -v`
Expected: 6 passed

Run: `pytest tests/webui/ -q`
Expected: 全部 PASS

- [ ] **Step 5: 补记 B-1 决策反转**

编辑 `.ai-runtime-artifacts/plans/2026-09-29-agents-runtime-integration-plan.md`：

1. 在 FM 的 `update_note` 后**追加**一条（不要覆盖已有内容），说明 2026-09-30 的裁决：

```yaml
update_note_2: >
  2026-09-30 实施后代码审查发现本计划「档案 prompt 追加到基础提示词之后」
  被实现为「前置」。用户裁决保留前置实现，理由：
  (1) 角色定义在前更符合「先确立身份、再给框架」的习惯；
  (2) 出厂模板首行本就是 "You are a subagent spawned by the main agent"，
      身份行紧邻其后不冲突；
  (3) 子 Agent 无多轮对话场景，顺序对实际行为影响有限。
  唯一顾虑是「用户自由文本排在全局安全框架之前」——已记录，若将来出现
  提示词注入绕过案例，应重新评估为追加。
```

2. 在正文「档案 `prompt` 字段的内容约定」一节，把「**追加**到子 Agent 基础提示词之后」改为「**前置**到基础模板之前」，并保留该节末尾的 openakita 对照作为参考（注明语义差异是有意的）。

- [ ] **Step 6: 提交**

```bash
git add nanobot/agents/runtime.py tests/webui/test_prompt_template_render.py \
        .ai-runtime-artifacts/plans/2026-09-29-agents-runtime-integration-plan.md
git commit -m "fix(agents): 模板渲染改单趟正则 + 补记 prompt 前置决策"
```

---

## Task 3: 修能力选择器与运行时不一致（B-2）

**Files:**
- Modify: `nanobot/webui/agents_api.py:143-150`
- Modify/Test: `webui/src/lib/agents/catalog.ts`、`webui/src/tests/capability-picker-scope.test.ts`

- [ ] **Step 1: 先把现状固化成测试**

`nanobot/webui/agents_api.py` 已有 agents 目录的测试。跑一遍确认当前行为：

```bash
python -c "
from pathlib import Path
from nanobot.webui.agents_api import _tool_descriptors
from nanobot.config.schema import ToolsConfig
d = _tool_descriptors(Path('.'), ToolsConfig())
print('总数', len(d))
print('locked', [x['name'] for x in d if x['locked']])
print('subagent', [x['name'] for x in d if x['scope']=='subagent'])
"
```

Expected（当前，**这是待修的错误状态**）: 14 个工具，`locked` 全为 true，`subagent` 为空。

- [ ] **Step 2: 改 `scope` / `locked` 判定**

编辑 `agents_api.py:143-150`。当前：

```python
"scope": (
    "subagent"
    if "subagent" in scopes and "core" not in scopes
    else "core"
),
"locked": bool(scopes & {"core", "memory"}),
```

改成的语义：
- **`scope`**：表达「这个工具在子 Agent 档案里能不能用」，即 `"subagent" in scopes` → `"subagent"`，否则 `"core"`（主 Agent 专属，子 Agent 档案选不了）
- **`locked`**：只给**档案运行必需**的工具。`resolve_selection(selection, allIds, lockedIds)` 的 `lockedIds` 会强制保留，所以锁错会静默扩大权限。当前 13 个子 Agent 可用工具里，`read_file` / `list_dir` 是文件工具的基础，没有它们档案基本没法工作；其余（`exec` / `write_file` / `web_*` / `apply_patch` …）**必须可取消**——`code-reviewer` 预设意图就是只保留读工具。

因此：`locked` 改为一个显式的最小集合常量（放在 `agents_api.py` 顶部，附注释说明「这是档案跑起来的最小组装集，取消会让子 Agent 完全无法工作」），不要用 scope 相交推导。

- [ ] **Step 3: 确认 core-only 工具在 UI 上可见但不可选**

`scope="core"` 的工具（`cron` / `long_task` / `message` / `sessions` 等）在前端应显示但**不可勾选**，并给出 `blockedReason` 说明「主 Agent 专属」。检查 `CapabilityPicker.tsx` 是否已有 `blocked` 支持——`:20` 有 `blocked?: boolean`、`:21` 有 `blockedReason?: string`、`:233` 有 `title={item.blocked ? item.blockedReason : undefined}`，**能力已具备**，只需后端把 `scope="core"` 的工具标为 `blocked`。

- [ ] **Step 4: 后端测试**

在 `tests/webui/test_agents_api.py` 增加：

```python
def test_tool_descriptors_mark_core_only_tools_as_blocked(tmp_path):
    """core-only 工具在档案页可见但不可勾选——它们不进子 Agent 工具集。"""
    items = agents_api.agents_catalog(tmp_path)["tools"]
    by_name = {t["name"]: t for t in items}
    # cron / message 只声明 core scope
    assert by_name["cron"]["scope"] == "core"
    assert by_name["cron"]["blocked"] is True


def test_subagent_scoped_tools_are_not_locked(tmp_path):
    """子 Agent 可用的工具不应被锁死，否则档案无法收紧能力。"""
    items = agents_api.agents_catalog(tmp_path)["tools"]
    by_name = {t["name"]: t for t in items}
    # exec 可用但可取消——code-reviewer 预设正是靠 include 白名单收紧它
    assert by_name["exec"]["scope"] == "subagent"
    assert by_name["exec"]["locked"] is False
```

若函数名不是 `agents_catalog`，按 `agents_api.py` 现有导出调整。

Run: `pytest tests/webui/test_agents_api.py -q`
Expected: 全部 PASS

- [ ] **Step 5: 前端测试**

`webui/src/lib/agents/catalog.ts` 的 `resolveSelection(selection, allIds, lockedIds)` 已有 `lockedIds` 语义（`:151-155`）。加一个测试验证「locked 集合变小后，include 白名单能真正收紧」：

```ts
import { describe, expect, it } from "vitest";
import { resolveSelection } from "@/lib/agents/catalog";

describe("resolveSelection with minimal locked set", () => {
  it("keeps locked ids even when include list omits them", () => {
    const all = ["read_file", "list_dir", "exec", "write_file"];
    const locked = ["read_file", "list_dir"];
    const got = resolveSelection({ mode: "include", entries: ["read_file"] }, all, locked);
    expect([...got].sort()).toEqual(["list_dir", "read_file"]);
  });
});
```

Run: `cd webui && bun run test`
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```bash
git add nanobot/webui/agents_api.py tests/webui/test_agents_api.py \
        webui/src/lib/agents/catalog.ts webui/src/tests/capability-picker-scope.test.ts
git commit -m "fix(webui): 能力选择器区分 core-only 与子 Agent 可用工具"
```

---

## 验收口径

完成的判定标准（全部满足才算完成）：

1. `pytest tests/ -q` 无**新增**失败（既有 15 个网络/进程相关失败在 `a397aca` 基线上同样失败，不在本次范围）
2. `ruff check nanobot/` 对本次改动文件干净（仓库既有 14 个 lint 错误在 `nanobot/memory/`、`nanobot/cli/`、`tests/webui/test_refresh_md_route.py`，不在范围内）
3. `cd webui && bun run test` 全绿
4. **Task 1 的 Step 3 已实际执行**——即注释掉转发行后新测试确实 FAIL、恢复后 PASS。没做这步就等于没验证防线
5. **B-2 修完后**，`_tool_descriptors` 输出中 `locked=true` 的工具只剩最小集合（Step 1 的复现命令应显示绝大多数工具 `locked=false`）
6. 原计划 `2026-09-29-agents-runtime-integration-plan.md` 的 `update_note_2` 已写入，且正文「追加」措辞已改为「前置」

## 后续（另立计划，不在本计划内）

- **子 Agent 前端展现**（「结果可用」范围：失败可见 / 耗时可见 / 工具列表可见 / 可取消）。已调研完毕，关键事实：
  - `SubagentStatus`（`nanobot/agent/subagent.py:52-65`）**已经采到** phase（7 态）/ iteration / tool_events / usage / error / started_at，**只差出口**
  - `bus/outbound_events.py` 有 13 个事件类，**零个子 Agent 事件**；`webui/src/lib/types.ts` 的 `ServerEvent` 联合同样零变体
  - `bus/events.py:13` 有个 `OUTBOUND_META_AGENT_UI` 扩展点，**协议两端都已铺好但都空着**（后端零生产者、前端测试显式忽略）——这是现成的富客户端口子
  - `started_at` 用的是 `time.monotonic()`（`:58`），**不可跨进程传输**，要推 wire 必须先换 `time.time()`
  - `get_running_count()` / `cancel_by_session()` 已实现但传输层零调用
  - 「过程可见」还需给子 Agent 挂 `AgentProgressHook`（目前只挂 `_SubagentHook`，工具调用既不进 WS 也不进 transcript）
  - 调研细节见 `.ai-runtime-artifacts/research/2026-09-30-openakita-subagent-integration-research.md`（§五回执结构、§九状态管理）与本计划的来源清单
- I-3：在 `config/schema.py` 兑现「运行时惰性补全」承诺
- S-1 / S-2：前端 `PROMPT_VARIABLES` 与后端 `replacements` 清单对齐
- S-3：`hidden` 字段接线（`agents_api.py:60` 与 `spawn.py:105` 都不过滤）
- S-4：给 `ToolLoader.load` 加 allowlist 参数

---

## Next

**（写入后须暂停）**

- 计划确认 → 说「开始实现」或「执行」
- 需要调整 → 直接说修改意见
- 三个 Task 文件不相交，若要并行可另写同 stem `*-dispatch.md`
