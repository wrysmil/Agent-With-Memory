---
artifact: implementation-plan
route: superpowers:writing-plans
skills:
  - writing-plans
skills_evidence:
  - ~/.claude/skills/writing-plans/SKILL.md
dispatch: .ai-runtime-artifacts/plans/2026-09-29-persona-injection-and-identity-responsibility-dispatch.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
created_at: 2026-09-29
status: draft
approved: false
---

# Persona 注入 + 身份文件职责校准 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `orchestration`（Harness 调度）按 `*-dispatch.md` 逐个派发 WU。每步用 checkbox（`- [ ]`）跟踪。

**Goal:** 让 `identity/personas/*.md` 真正进入 system prompt（作为 SOUL 之后、AGENT 之前的独立段落），并重写 `tech_expert` 样板文案。

**Architecture:** Python 侧在 `ContextBuilder` 内新增一个段落渲染器，从 `identity/active_persona` 读激活的 stem，读到就注入、读不到就静默跳过。激活态的读写走既有 WebUI settings 域（`identity_routes.py` 的 action dispatch），新增两个 action。前端在身份页加一个下拉。

**Tech Stack:** Python 3.11+ / asyncio / pytest（`asyncio_mode=auto`）；React + TypeScript + Vite（`webui/`，Bun）；ruff + basedpyright。

**上游 spec：** `.ai-runtime-artifacts/specs/2026-09-29-persona-injection-and-identity-responsibility-spec.md`

---

## 文件结构

| 文件 | 动作 | 职责 |
| --- | --- | --- |
| `nanobot/agent/context.py` | 改 | 新增 `_load_persona_section()` / `_render_bootstrap_file()`；`_load_bootstrap_files` 循环改为「渲染一段→若为 SOUL 则追加 persona 段」 |
| `nanobot/webui/identity_api.py` | 改 | 新增 `identity_get_active_persona()` / `identity_set_active_persona()` |
| `nanobot/webui/identity_routes.py` | 改 | `IdentitySettingsOperations` 加两个字段；`IDENTITY_ACTION_NAMES` 加两个；`dispatch()` 加两个分支 |
| `nanobot/webui/settings_routes.py` | 改 | HTTP 路径映射 +1、mutation allowlist +1 |
| `nanobot/webui/gateway_services.py:105-116` | 改 | `IdentitySettingsOperations(...)` 装配处补两个 `partial` |
| `nanobot/webui/ws_http.py:199` | 改 | mutation 名 → 路径映射加 `identity.persona.set` |
| `nanobot/identity/catalog.py` | 改 | 删 `FULL_TEXT_PERSONAS`；`build_persona_specs` 去掉徽标分支 |
| `nanobot/templates/personas/tech_expert.md` | 改 | 样板文案重写 |
| `webui/src/lib/api.ts` | 改 | 新增 `getActivePersona()` / `setActivePersona()` + 类型 |
| `webui/src/lib/types.ts:1657` | 改 | `IdentityFileEntry` 加 `tokens?: number` |
| `webui/src/components/settings/identity/IdentityView.tsx` | 改 | 人格组上方加下拉；文件行加 token 展示；删 `BADGE_DEFAULTS` 里的 `badgeFullTextInject` |
| `nanobot/channels/websocket/webui/locales/*.json` | 改 | 删 `settings.identity.badgeFullTextInject`，加 persona 下拉文案 |
| `tests/agent/test_context_builder.py` | 改 | 注入行为测试 |
| `tests/identity/test_catalog.py` | 改 | 删徽标断言 |
| `webui/src/tests/` | 改 | 下拉交互测试 |

---

## Task 1: 注入核心（WU-1）

**Files:**
- Modify: `nanobot/agent/context.py:315-364`
- Test: `tests/agent/test_context_builder.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/agent/test_context_builder.py`：

```python
import pytest


def _write(path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.mark.parametrize(
    ("state", "persona_body", "expected"),
    [
        (None, "你是技术搭档", False),
        ("", "你是技术搭档", False),
        ("tech_expert", "你是技术搭档", True),
        ("missing_one", "你是技术搭档", False),
        ("tech_expert", "", False),
    ],
)
def test_persona_section_only_injects_when_active(tmp_path, state, persona_body, expected):
    ws = tmp_path / "workspace"
    _write(ws / "identity" / "SOUL.md", "# Soul\n\n本体内容。\n")
    _write(ws / "identity" / "AGENT.md", "# Agent 行为准则\n\n方法论内容。\n")
    _write(ws / "identity" / "personas" / "tech_expert.md", f"# 技术专家\n\n{persona_body}\n")
    if state is not None:
        _write(ws / "identity" / "active_persona", state + "\n")

    prompt = ContextBuilder(ws).build_system_prompt()

    assert ("## 当前人格：tech_expert" in prompt) is expected


def test_persona_strips_leading_h1_and_keeps_lower_headings(tmp_path):
    ws = tmp_path / "workspace"
    _write(ws / "identity" / "personas" / "tech_expert.md", "# 技术专家\n\n## 人格\n\n严谨。\n")
    _write(ws / "identity" / "active_persona", "tech_expert\n")

    prompt = ContextBuilder(ws).build_system_prompt()

    assert "## 当前人格：tech_expert" in prompt
    assert "技术专家\n" not in prompt.split("## 当前人格：tech_expert", 1)[1]
    assert "## 人格" in prompt


def test_persona_section_sits_between_soul_and_agent(tmp_path):
    ws = tmp_path / "workspace"
    _write(ws / "identity" / "SOUL.md", "SOUL_BODY")
    _write(ws / "identity" / "AGENT.md", "AGENT_BODY")
    _write(ws / "identity" / "personas" / "tech_expert.md", "PERSONA_BODY")
    _write(ws / "identity" / "active_persona", "tech_expert")

    prompt = ContextBuilder(ws).build_system_prompt()

    assert prompt.index("## SOUL.md") < prompt.index("## 当前人格") < prompt.index("## AGENT.md")


def test_persona_ignores_path_traversal_state(tmp_path):
    ws = tmp_path / "workspace"
    _write(ws / "identity" / "personas" / "tech_expert.md", "PERSONA_BODY")
    _write(ws / "identity" / "active_persona", "../../../etc/passwd")

    prompt = ContextBuilder(ws).build_system_prompt()

    assert "## 当前人格" not in prompt
```

再追加到 `tests/agent/test_subagent.py`（复用该文件已有的 `SubagentManager` 构造写法）：

```python
def test_subagent_prompt_never_contains_persona_section(tmp_path):
    """persona 是主 Agent 的表现层；子 agent 走独立模板，不应继承。"""
    identity = tmp_path / "identity"
    _write(identity / "personas" / "tech_expert.md", "PERSONA_BODY")
    _write(identity / "active_persona", "tech_expert")
    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=16_000,
    )

    prompt = manager._build_subagent_prompt()

    assert "## 当前人格" not in prompt
    assert "PERSONA_BODY" not in prompt
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/Scripts/python.exe -m pytest tests/agent/test_context_builder.py -k persona -v
```

Expected: FAIL（`AttributeError` / 断言不成立，因为还没有注入逻辑）

- [ ] **Step 3: 实现**

在 `nanobot/agent/context.py` 顶部 import 区补：

```python
from nanobot.identity.catalog import PERSONAS_SUBDIR, resolve_identity_dir
```

在 `ContextBuilder` 类里（或模块级）加常量与两个辅助函数：

```python
PERSONA_STATE_FILE = "active_persona"
PERSONA_HEADING = "## 当前人格"


def _strip_leading_h1(content: str) -> str:
    """剥掉正文首个一级标题——persona 文件用标题行承载展示名，注入时不需要。"""
    lines = content.splitlines()
    if lines and lines[0].lstrip().startswith("# "):
        return "\n".join(lines[1:])
    return content
```

把 `_load_bootstrap_files` 里现有的 `sources` 循环拆成「渲染一段」的辅助方法，并在 SOUL 之后插入 persona 段：

```python
    def _render_bootstrap_file(
        self,
        filename: str,
        root: Path,
        compiled: dict[str, str] | None,
        compiled_key: str | None,
    ) -> str:
        """渲染单个 bootstrap 段；出厂未定制的内容返回空串。"""
        if compiled is not None and compiled_key is not None:
            body = compiled.get(compiled_key, "")
            if body.strip():
                return f"## {filename}\n\n{body}"
        file_path = root / filename
        if not file_path.exists():
            return ""
        content = file_path.read_text(encoding="utf-8")
        if filename == "SOUL.md" and self._is_template_content(content, "legacy/SOUL.md"):
            content = load_bundled_template("SOUL.md") or content
        if not content.strip():
            return ""
        if filename in self._SKIPPABLE_DEFAULTS and self._is_template_content(content, filename):
            return ""
        return f"## {filename}\n\n{content}"

    def _load_persona_section(self) -> str:
        """把激活的 persona 渲染成表现层段落。

        persona 是可切换的表现层，SOUL 是不可切换的本体，两者分段注入让
        「改本体」与「切表现」互不干扰。文件内容原样注入、只剥一级标题，
        不解析字段——用户改标题不会让 persona 静默失效。未激活 / 目标缺失 /
        空文件 / 仍是出厂模板时静默跳过，不报错。
        """
        identity_dir = resolve_identity_dir(self.workspace)
        try:
            stem = (identity_dir / PERSONA_STATE_FILE).read_text(encoding="utf-8").strip()
        except OSError:
            return ""
        # isidentifier 挡住 ../ 与绝对路径：状态文件是用户可写的，不能当路径用。
        if not stem or not stem.isidentifier():
            return ""
        try:
            content = (identity_dir / PERSONAS_SUBDIR / f"{stem}.md").read_text(encoding="utf-8")
        except OSError:
            return ""
        if not content.strip() or self._is_template_content(content, f"personas/{stem}.md"):
            return ""
        body = _strip_leading_h1(content).strip()
        if not body:
            return ""
        return f"{PERSONA_HEADING}：{stem}\n\n{body}"
```

然后把 `_load_bootstrap_files` 的循环替换为：

```python
        for filename, root, compiled_key in sources:
            section = self._render_bootstrap_file(filename, root, compiled, compiled_key)
            if section:
                parts.append(section)
            if filename == "SOUL.md":
                persona = self._load_persona_section()
                if persona:
                    parts.append(persona)

        return "\n\n".join(parts) if parts else ""
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/Scripts/python.exe -m pytest tests/agent/test_context_builder.py tests/agent/test_subagent.py -v
.venv/Scripts/python.exe -m pytest tests/agent/ -q
```

Expected: 全部 PASS

- [ ] **Step 5: 静态检查**

```bash
.venv/Scripts/python.exe -m ruff check nanobot/agent/context.py
.venv/Scripts/basedpyright.exe nanobot/agent/context.py
```

Expected: 无输出 / 0 errors

---

## Task 2: 激活态读写端点（WU-2）

**Files:**
- Modify: `nanobot/webui/identity_api.py`
- Modify: `nanobot/webui/identity_routes.py`
- Modify: `nanobot/webui/settings_routes.py:160-200`
- Modify: `nanobot/webui/gateway_services.py:105-116`
- Modify: `nanobot/webui/ws_http.py:199`
- Test: `tests/webui/test_identity_persona_api.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/webui/test_identity_persona_api.py`：

```python
from pathlib import Path

import pytest

from nanobot.webui.identity_api import (
    identity_get_active_persona,
    identity_set_active_persona,
)
from nanobot.webui.settings_contracts import WebUISettingsError


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "identity" / "personas").mkdir(parents=True)
    (ws / "identity" / "personas" / "tech_expert.md").write_text("A", encoding="utf-8")
    (ws / "identity" / "personas" / "mentor.md").write_text("B", encoding="utf-8")
    return ws


def test_get_active_persona_defaults_to_inactive(tmp_path):
    assert identity_get_active_persona(_ws(tmp_path)) == {"active": "", "options": ["mentor", "tech_expert"]}


def test_set_then_get_roundtrip(tmp_path):
    ws = _ws(tmp_path)
    assert identity_set_active_persona(ws, "mentor") == {"active": "mentor"}
    assert identity_get_active_persona(ws)["active"] == "mentor"


def test_set_empty_clears_activation(tmp_path):
    ws = _ws(tmp_path)
    identity_set_active_persona(ws, "mentor")
    assert identity_set_active_persona(ws, "")["active"] == ""


def test_set_unknown_persona_is_404(tmp_path):
    with pytest.raises(WebUISettingsError) as exc:
        identity_set_active_persona(_ws(tmp_path), "nope")
    assert exc.value.status == 404


def test_set_rejects_path_traversal(tmp_path):
    with pytest.raises(WebUISettingsError) as exc:
        identity_set_active_persona(_ws(tmp_path), "../../secrets")
    assert exc.value.status == 400


def test_get_treats_dangling_activation_as_inactive(tmp_path):
    ws = _ws(tmp_path)
    identity_set_active_persona(ws, "mentor")
    (ws / "identity" / "personas" / "mentor.md").unlink()
    assert identity_get_active_persona(ws)["active"] == ""
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/Scripts/python.exe -m pytest tests/webui/test_identity_persona_api.py -v
```

Expected: FAIL（`ImportError: cannot import name 'identity_get_active_persona'`）

- [ ] **Step 3: 实现 API 层**

`nanobot/webui/identity_api.py` 顶部 import 补 `PERSONAS_SUBDIR, resolve_identity_dir`（若尚未导入），并在 `identity_list_presets` 之后加：

```python
ACTIVE_PERSONA_FILE = "active_persona"


def _active_persona_path(workspace: Path) -> Path:
    return resolve_identity_dir(workspace) / ACTIVE_PERSONA_FILE


def _persona_stems(workspace: Path) -> list[str]:
    personas_dir = resolve_identity_dir(workspace) / PERSONAS_SUBDIR
    if not personas_dir.is_dir():
        return []
    return sorted(p.stem for p in personas_dir.glob("*.md"))


def identity_get_active_persona(workspace: Path) -> dict[str, Any]:
    """返回当前激活的 persona stem 与可选列表。

    激活态落在 ``identity/active_persona`` 而非 config：ContextBuilder 不持有
    config 对象，走 workspace 文件与既有的 policies 读取路径同构，无需新增
    参数穿透。指向已删除 persona 的陈旧激活态按未激活处理。
    """
    options = _persona_stems(workspace)
    try:
        stem = _active_persona_path(workspace).read_text(encoding="utf-8").strip()
    except OSError:
        stem = ""
    if stem not in options:
        stem = ""
    return {"active": stem, "options": options}


def identity_set_active_persona(workspace: Path, stem: str) -> dict[str, Any]:
    """写入激活态。空串表示关闭。状态文件是用户可写的，落盘前必须校验。"""
    stem = (stem or "").strip()
    if stem and not stem.isidentifier():
        raise WebUISettingsError("persona 名称非法", status=400)
    if stem and stem not in _persona_stems(workspace):
        raise WebUISettingsError(f"persona 不存在：{stem}", status=404)
    path = _active_persona_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stem, encoding="utf-8")
    return {"active": stem}
```

- [ ] **Step 4: 接进路由 dispatch**

`nanobot/webui/identity_routes.py`：`IdentitySettingsOperations` 加两个字段

```python
    get_active_persona: Callable[..., dict[str, Any]]
    set_active_persona: Callable[..., dict[str, Any]]
```

`IDENTITY_ACTION_NAMES` 加两项：

```python
    "identity-get-active-persona",
    "identity-set-active-persona",
```

`dispatch()` 在 `identity-reload` 分支之前加：

```python
    if action == "identity-get-active-persona":
        return operations.get_active_persona()

    if action == "identity-set-active-persona":
        stem = payload.get("stem", "")
        if not isinstance(stem, str):
            raise WebUISettingsError("stem must be a string")
        return operations.set_active_persona(stem=stem)
```

- [ ] **Step 5: 加 token 计量字段（后端算，前端只显示）**

`IdentityFileEntry` 没有 `content` 字段，前端拿不到正文。让后端在列表响应里直接给估算值。

`nanobot/webui/identity_api.py` 顶部加估算函数（口径与 openakita 一致：中文 /1.5 + 英文 /4）：

```python
def _estimate_tokens(text: str) -> int:
    """粗估 token 数：CJK 按 1.5 字/token，其余按 4 字符/token。

    纯展示用途，不参与任何截断决策——见 spec §〇 对硬预算的排除理由。
    """
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    return round(cjk / 1.5 + (len(text) - cjk) / 4)
```

`identity_list_files` 的每项加 `tokens`：存在则 `read_file` 后估算，不存在（`exists=False`，WebUI 会显示出厂模板）则估算出厂模板内容。读文件失败时降级为 `0`，不让计量影响列表接口可用性。

在 `nanobot/webui/identity_routes.py` 之外无需改动——`list_files` 已经是既有 operation。

- [ ] **Step 6: 接进 settings 路由与传输层**

`nanobot/webui/settings_routes.py` 路径映射表加：

```python
    "/api/settings/identity/persona": "identity-get-active-persona",
    "/api/settings/identity/persona/set": "identity-set-active-persona",
```

mutation allowlist 列表（`settings_routes.py:191` 附近）加 `"/api/settings/identity/persona/set"`。

`nanobot/webui/gateway_services.py:105` 的 `IdentitySettingsOperations(...)` 补两项：

```python
        get_active_persona=partial(identity_api.identity_get_active_persona, workspace),
        set_active_persona=partial(identity_api.identity_set_active_persona, workspace),
```

`nanobot/webui/ws_http.py:199` 的 mutation 名映射加一行——前端 `mutation(transport, "identity.persona.set", ...)` 靠它落到 HTTP 路径：

```python
    "identity.persona.set": "/api/settings/identity/persona/set",
```

`settings_routes.py` 的 `_null_identity_operations()` 补两个抛 503 的实现，保持「服务未装配时该域仍能响应」的既有约定。

- [ ] **Step 6: 跑测试确认通过**

```bash
.venv/Scripts/python.exe -m pytest tests/webui/ -q
.venv/Scripts/python.exe -m ruff check nanobot/webui/
.venv/Scripts/basedpyright.exe nanobot/webui/
```

Expected: PASS / 无告警

同时在 `tests/webui/test_identity_persona_api.py` 追加一条：

```python
def test_list_files_reports_token_estimate(tmp_path):
    ws = _ws(tmp_path)
    (ws / "identity" / "personas" / "tech_expert.md").write_text(
        "技术搭档" * 100, encoding="utf-8"
    )
    items = {f["name"]: f for f in identity_list_files(ws)["files"]}
    assert items["tech_expert.md"]["tokens"] > 0
```

---

## Task 3: 样板文案 + 删除空转徽标（WU-4）

**Files:**
- Modify: `nanobot/templates/personas/tech_expert.md`
- Modify: `nanobot/identity/catalog.py:72-117`
- Test: `tests/identity/test_catalog.py`

- [ ] **Step 1: 写失败测试**

在 `tests/identity/test_catalog.py` 追加：

```python
def test_personas_carry_no_badge(tmp_path):
    from nanobot.identity.catalog import build_persona_specs

    personas = tmp_path / "identity" / "personas"
    personas.mkdir(parents=True)
    (personas / "tech_expert.md").write_text("x", encoding="utf-8")

    specs = build_persona_specs(tmp_path)

    assert [s.badge_label_key for s in specs] == [None]


def test_tech_expert_template_title_is_not_soul():
    from nanobot.utils.helpers import load_bundled_template

    content = load_bundled_template("personas/tech_expert.md")
    assert content.splitlines()[0] == "# 技术专家"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/Scripts/python.exe -m pytest tests/identity/test_catalog.py -k "badge or title" -v
```

Expected: FAIL（`tech_expert` 当前带 `FULL_TEXT_INJECT` 徽标，标题仍是 `# Soul`）

- [ ] **Step 3: 删掉空转徽标**

`nanobot/identity/catalog.py` 删除 `FULL_TEXT_PERSONAS` 常量，把 `build_persona_specs` 改为：

```python
def build_persona_specs(workspace: Path) -> list[IdentityFileSpec]:
    """Convert real persona files under personas/ into spec list."""
    specs: list[IdentityFileSpec] = []
    for path in discover_personas(workspace):
        specs.append(
            IdentityFileSpec(
                name=path.name,
                group="personas",
                logical_path=f"{PERSONAS_SUBDIR}/{path.name}",
            )
        )
    return specs
```

同时删除 `BADGE_FULL_TEXT_INJECT` 的 import/定义（若仅此处使用）与 `IdentityView.tsx` 里的 `BADGE_DEFAULTS` 条目。

- [ ] **Step 4: 重写样板**

`nanobot/templates/personas/tech_expert.md` 全量替换为：

```markdown
# 技术专家

你是 nanobot 的技术搭档，专注工程与源码工作。默认对方懂技术——不解释基础概念，不用「好的，我来帮你」开场。

## 人格

- 改代码前先读调用方和测试，不凭文件名或报错信息猜行为。
- 遇到根因不确定时，给出「假设 + 验证命令」，让对方能自己判真假，而不是给一个含糊的「大概是因为」。
- 发现更优解时直接说，附上取舍；对方明确要当前方案就照做，不反复劝。
- 对自己犯的错不粉饰：说清影响范围，然后修。

## 表达风格

- 术语不稀释：标识符、库名、报错原文保持英文原样。
- 结论带前提，「在 X 版本 / Y 条件下成立」是默认句式。
- 性能、安全、可维护性冲突时摆出 trade-off，不假装有免费午餐。
- 简洁是美德：注释解释 why，代码本身解释 what。
```

- [ ] **Step 5: 跑测试确认通过**

```bash
.venv/Scripts/python.exe -m pytest tests/identity/ -q
```

Expected: PASS

---

## Task 4: WebUI 下拉（WU-3，依赖 WU-2）

**Files:**
- Modify: `webui/src/lib/api.ts:1298-1386`
- Modify: `webui/src/components/settings/identity/IdentityView.tsx`
- Modify: `nanobot/channels/websocket/webui/locales/*.json`
- Test: `webui/src/tests/`

- [ ] **Step 1: 写失败测试**

在 `webui/src/tests/` 新建 `identity-persona-picker.test.tsx`，mock `@/lib/api` 的 `getActivePersona` / `setActivePersona`，断言：

- 加载后下拉显示「不使用」为选中项
- 选 `tech_expert` 后调用 `setActivePersona(transport, "tech_expert")`
- 后端返回的 `active` 不在 `options` 里时，下拉回落到「不使用」

- [ ] **Step 2: 跑测试确认失败**

```bash
cd webui && bun run test identity-persona-picker
```

Expected: FAIL

- [ ] **Step 3: 加 API 客户端**

`webui/src/lib/api.ts` 追加：

```ts
export interface ActivePersonaResponse {
  active: string;
  options: string[];
}

export async function getActivePersona(
  token: string,
  base: string = "",
): Promise<ActivePersonaResponse> {
  return request<ActivePersonaResponse>(
    `${base}${IDENTITY_BASE}/persona`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function setActivePersona(
  transport: WebUIMutationTransport,
  stem: string,
): Promise<{ active: string }> {
  return mutation<{ active: string }>(transport, "identity.persona.set", { stem });
}
```

- [ ] **Step 4: 加下拉 UI**

`IdentityView.tsx`：

1. 新增 state `const [activePersona, setActivePersona] = useState("")` 与 `const [personaOptions, setPersonaOptions] = useState<string[]>([])`
2. 复用已有的 `useEffect(() => listIdentityPresets...)` 模式，加一个 `getActivePersona(token)` 的 effect，失败静默
3. 在「人格模板」`GroupHeader` **上方**插入一个与 `IdentityFileRow` 同风格的行：

```tsx
<div className="mb-1 flex items-center gap-2 rounded-control border border-border/60 bg-settings-surface px-3 py-2">
  <UserRound className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
  <span className="shrink-0 text-[12px] text-foreground/80">
    {t("settings.identity.activePersona", "使用人格")}
  </span>
  <select
    className="ml-auto rounded-control border border-border/60 bg-background px-2 py-1 text-[12px] text-foreground"
    value={activePersona}
    onChange={(e) => handlePersonaChange(e.target.value)}
  >
    <option value="">{t("settings.identity.noPersona", "不使用")}</option>
    {personaOptions.map((stem) => (
      <option key={stem} value={stem}>
        {t(`settings.identity.persona.${stem}`, stem)}
      </option>
    ))}
  </select>
</div>
```

4. `handlePersonaChange`：乐观更新本地 state → 调 `setActivePersona` → 失败回滚并置 `saveError`

- [ ] **Step 5: 加 token 展示**

`IdentityFileEntry` 新增 `tokens?: number`（由 WU-2 的后端填充），在 `IdentityFileRow` 的徽标行右侧渲染 `≈{file.tokens ?? 0} tokens`。

**只展示，不执行**：不设预算总额、不做百分比分配、不截断。理由见 spec §〇。

- [ ] **Step 6: 补 i18n**

`nanobot/channels/websocket/webui/locales/` 下 11 个语言文件补 `settings.identity.activePersona` / `settings.identity.noPersona` / `settings.identity.persona.{stem}`；所有语言删除 `settings.identity.badgeFullTextInject`。

- [ ] **Step 7: 跑测试与构建**

```bash
cd webui && bun run test && bun run build
```

Expected: 全部通过，构建产物输出到 `../nanobot/web/dist`

---

## Task 5: 集成验证

- [ ] **Step 1: 后端全量检查**

```bash
.venv/Scripts/python.exe -m pytest tests/ -q
.venv/Scripts/python.exe -m ruff check nanobot/
.venv/Scripts/basedpyright.exe
```

- [ ] **Step 2: 端到端手测**

1. 重启 gateway
2. `curl -H "X-Nanobot-Auth: <tokenIssueSecret>" http://127.0.0.1:8765/webui/bootstrap` 拿 token 后调 `GET /api/settings/identity/persona`，确认返回 `{"active":"","options":[...]}`
3. WebUI 身份页下拉选 `技术专家`
4. 确认 `~/.nanobot/workspace/identity/active_persona` 内容为 `tech_expert`
5. 新开会话，dump system prompt 确认含 `## 当前人格：tech_expert`，且位于 `## SOUL.md` 与 `## AGENT.md` 之间
6. 切回「不使用」，确认该段消失

- [ ] **Step 3: 落盘验证证据**

写 `.ai-runtime-artifacts/verifications/2026-09-29-persona-injection-verification.md`，记录上述命令的实际输出。

---

## 依赖关系

```
WU-1（context.py 注入）  ─┐
                          ├─→ WU-3（WebUI 下拉，依赖 WU-2 的端点）
WU-2（后端端点）──────────┤
                          │
WU-4（文案 + 删徽标）    ─┘
```

WU-1 / WU-2 / WU-4 文件互不重叠，可并行；WU-3 必须等 WU-2。

---

## 验收标准

- `pytest tests/` 全绿；`ruff check nanobot/` 与 `basedpyright` 无新增告警
- `webui` 侧 `bun run test` 与 `bun run build` 通过
- 端到端：选中 persona 后 system prompt 出现 `## 当前人格：{stem}`，位置在 SOUL 与 AGENT 之间；切回后消失
- 启动日志无新增 warning

---

## Next

**（写入后须暂停 — 即使用户句末含「然后执行」）**

- 计划确认 → 说「开始实现」或「执行」
- 需要调整 → 直接说修改意见
- 想拆分并行 → 审 `*-dispatch.md` 后说「开始实现」或「并行执行」
