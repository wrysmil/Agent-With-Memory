# 身份规则编译全自动 + POLICIES.yaml 下架 实施计划

> **给执行 Agent：** 逐任务实现，每个任务以「改测试反映新契约 → 跑测试看红 → 改实现 → 跑测试看绿 → commit」推进。步骤用 `- [ ]` 勾选跟踪。

**目标：** 把身份规则编译从「用户手动点按钮」改为「保存 / 启动自动触发」，并把 POLICIES.yaml 移出可编辑白名单（UI 不显示、后端读写 403，但保留播种）。

**架构：** 编译器 `compiler.py` 不动。新增两条自动触发（`identity_write_file` 保存后、`sync_workspace_templates` 启动后，均复用现成 `compiled_status()` 新鲜度判断）；删除手动编译的整条 HTTP/WS 接口链；POLICIES.yaml 从 `catalog.CORE_FILES` 摘除即同时达成「UI 不显示 + 读写 403」（`CORE_FILES` 是 UI 清单与后端白名单的单一真相源）。

**技术栈：** Python 3.11+ / Pydantic / pytest（asyncio_mode=auto）；前端 Vite + React + TS + bun test；ruff + basedpyright。

**关联 spec：** `.ai-runtime-artifacts/specs/2026-09-23-identity-auto-compile-and-policies-unlist-spec.md`

---

## 前置事实（已实测验证，勿重复排查）

1. **编译器读 workspace 根 SOUL/USER 正常**：全新 workspace 播种后 SOUL.md 落在 workspace 根，`IdentityStore._target` 在 `identity/` 无同名文件时回退命中根目录（`store.py:108-115`），编译器 `resolve_path` 走同一逻辑，实测首次编译产出 `identity.core.md`、`compiled_status().fresh == True`。**compiler.py 无需任何改动。**
2. **POLICIES.yaml 是 `CORE_FILES` 里唯一的 `.yaml`**：移出后 `store._validate_yaml` 分支永不可达 → 删除。播种走 `bootstrap._seed` 直接写盘、不经 `IdentityStore`，不受影响。
3. **`BADGE_NEEDS_COMPILE`（AGENT.md 用）与 `BADGE_NOT_WIRED`（POLICIES.yaml 用）** 在本轮改动后均无引用 → 删常量。前端 `BADGE_DEFAULTS` map 与 10 国 i18n key 保留（无害、churn 大）。

---

## 文件结构

| 文件 | 职责 | 本计划动作 |
|---|---|---|
| `nanobot/identity/catalog.py` | 身份文件白名单单一真相源 | 删 POLICIES.yaml spec + AGENT.md badge + 两个 badge 常量 |
| `nanobot/identity/store.py` | 身份文件读写 + 安全边界 | 删 YAML 校验逻辑 |
| `nanobot/webui/identity_api.py` | 身份域 payload 转换 + 动作 | 删 `identity_compile`；`identity_write_file` 加保存即编译 |
| `nanobot/webui/gateway_services.py` | 装配 operations | 删 `compile=` |
| `nanobot/webui/identity_routes.py` | 身份域 action 路由 | 删 `compile` 字段 / action / 分支 |
| `nanobot/webui/ws_http.py` | WS mutation 白名单 | 删 `identity.compile` 映射 |
| `nanobot/utils/helpers.py` | workspace 模板同步 | 加启动即编译 |
| `webui/src/lib/api.ts` | 前端 API 客户端 | 删 `compileIdentityRules` |
| `webui/src/components/settings/identity/IdentityView.tsx` | 身份页 | 删编译按钮/handleCompile/compileNotice/POLICIES 提示块 |
| `tests/identity/test_catalog.py` | catalog 契约测试 | 更新 |
| `tests/identity/test_identity_store.py` | store 测试 | 更新 + 删 YAML 测试 + 加 403 测试 |
| `tests/webui/test_identity_routes.py` | routes 测试 | 删全部 compile 引用 + 删 YAML 测试 |
| `tests/identity/test_identity_compiler.py` | 编译器测试 | 加自动触发相关 |
| `tests/utils/`（或就近）| helpers 测试 | 加启动编译测试 |

---

## Task 1：POLICIES.yaml 后端下架（白名单 + YAML 校验）

**Files:**
- Modify: `nanobot/identity/catalog.py:29,35,61-64,73-79`
- Modify: `nanobot/identity/store.py:13,199-200,205-216`
- Test: `tests/identity/test_catalog.py:22-36`
- Test: `tests/identity/test_identity_store.py:176-184,313-334`
- Test: `tests/webui/test_identity_routes.py:402-407`

- [ ] **Step 1：更新 catalog 契约测试（反映新清单）**

`tests/identity/test_catalog.py` 两个 contract 测试去掉 POLICIES.yaml：

```python
def test_core_files_match_frontend_contract():
    names = [spec.name for spec in CORE_FILES]
    assert names == [
        "SOUL.md",
        "AGENT.md",
        "USER.md",
        "MEMORY.md",
        "prompts/policies.md",
    ]


def test_restricted_set_matches_frontend_contract():
    restricted = {spec.name for spec in CORE_FILES if spec.restricted}
    assert restricted == {"AGENT.md", "MEMORY.md", "prompts/policies.md"}
```

- [ ] **Step 2：更新 store list_files 测试 + 删 YAML 测试 + 加 403 测试**

`tests/identity/test_identity_store.py`：

`test_list_files_groups_core_and_personas`（L316-323）core 列表去掉 `"POLICIES.yaml"`：

```python
    assert [f["name"] for f in files if f["group"] == "core"] == [
        "SOUL.md",
        "AGENT.md",
        "USER.md",
        "MEMORY.md",
        "prompts/policies.md",
    ]
```

`test_list_files_marks_exists` 删除 L334 `assert by_name["POLICIES.yaml"]["exists"] is False`。

`test_list_files_marks_restricted_and_badges`（L337-346）追加一条固化「AGENT.md 无 badge」：

```python
    assert "badge" not in by_name["AGENT.md"]
```

删除 `test_write_rejects_invalid_yaml` 连同其上方的 `@pytest.mark.parametrize(...)` 装饰器（约 L166-179）与 `test_write_accepts_valid_yaml`（L182-184）——POLICIES.yaml 已非白名单，YAML 校验逻辑本轮移除。（注：`test_write_rejects_invalid_yaml` 移除白名单后会因 403 侥幸通过，但语义已失真，须一并删。）

新增（放同文件 list_files 段之后）：

```python
def test_policies_yaml_is_not_editable(store: IdentityStore) -> None:
    """POLICIES.yaml 移出可编辑白名单：读写均 403，且不在清单里。"""
    with pytest.raises(IdentityStoreError) as read_exc:
        store.read_file("POLICIES.yaml")
    assert read_exc.value.status == 403

    with pytest.raises(IdentityStoreError) as write_exc:
        store.write_file("POLICIES.yaml", "tool_policies:\n  shell: allow\n")
    assert write_exc.value.status == 403

    assert "POLICIES.yaml" not in {f["name"] for f in store.list_files()}
```

- [ ] **Step 3：删 routes 层的 YAML 测试**

`tests/webui/test_identity_routes.py` 删除 `test_write_invalid_yaml_is_4xx`（L402-407）。

- [ ] **Step 4：跑测试确认变红**

Run: `pytest tests/identity/test_catalog.py tests/identity/test_identity_store.py -v`
Expected: FAIL —— `test_core_files_match_frontend_contract` 等断言 POLICIES.yaml 仍在 / 403 测试因当前仍可编辑而失败。

- [ ] **Step 5：改 catalog.py**

删 L29 `BADGE_NEEDS_COMPILE = "settings.identity.badgeNeedsCompile"`、L35 `BADGE_NOT_WIRED = "settings.identity.badgeNotWired"`。

AGENT.md spec（L58-64）去掉 badge 两字段：

```python
    IdentityFileSpec(name="AGENT.md", group="core", restricted=True),
```

删除整条 POLICIES.yaml spec（L73-79）：

```python
    IdentityFileSpec(
        name="POLICIES.yaml",
        group="core",
        restricted=True,
        badge_tone="clay",
        badge_label_key=BADGE_NOT_WIRED,
    ),
```

- [ ] **Step 6：改 store.py**

删 L13 `import yaml`（YAML 校验移除后无引用）。

`write_file` 删 L199-200：

```python
        if path.suffix == ".yaml":
            self._validate_yaml(content, name)
```

删 `_validate_yaml` 静态方法（L205-216）整个。

- [ ] **Step 7：跑测试确认变绿**

Run: `pytest tests/identity/test_catalog.py tests/identity/test_identity_store.py -v`
Expected: PASS。

Run: `ruff check nanobot/identity/`
Expected: 无错误（确认无孤儿 import）。

- [ ] **Step 8：commit**

```bash
git add nanobot/identity/catalog.py nanobot/identity/store.py tests/identity/test_catalog.py tests/identity/test_identity_store.py tests/webui/test_identity_routes.py
git commit -m "$(cat <<'EOF'
refactor(identity): POLICIES.yaml 移出可编辑白名单并移除 YAML 校验

CORE_FILES 同时驱动 UI 清单与后端读写白名单，摘除 POLICIES.yaml 即
达成「UI 不显示 + 读写 403」；播种仍在 bootstrap 保留。POLICIES.yaml
是唯一 .yaml 白名单项，连带删除不可达的 _validate_yaml。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2：删除手动编译接口全链

**Files:**
- Modify: `nanobot/webui/identity_api.py:136-149`
- Modify: `nanobot/webui/gateway_services.py:115`
- Modify: `nanobot/webui/identity_routes.py:8,43,54,118-120`
- Modify: `nanobot/webui/ws_http.py:201`
- Test: `tests/webui/test_identity_routes.py`（多处 compile 引用）

- [ ] **Step 1：先 grep 定位所有 compile 引用**

Run: `grep -rn "identity_compile\|identity-compile\|identity\.compile\|\bcompile\b" nanobot/webui/ tests/webui/test_identity_routes.py`
Expected: 列出 identity_api / gateway_services / identity_routes / ws_http / routes 测试的全部命中点，逐一对照本任务删除。

- [ ] **Step 2：更新 routes 测试反映「无 compile action」**

`tests/webui/test_identity_routes.py`：

`test_all_six_actions_are_registered`（L88-96）改为五 action、函数名与断言同步：

```python
def test_all_five_actions_are_registered():
    assert IDENTITY_ACTION_NAMES == frozenset({
        "identity-list-files",
        "identity-read-file",
        "identity-write-file",
        "identity-reload",
        "identity-list-presets",
    })
```

`test_every_known_action_dispatches`（L99-116）删除 L109 的 `identity-compile` handle 行。

删除 `# ---- compile ----` 段（L574 起）的 `test_compile_writes_runtime_products`、`test_compile_reports_requested_mode_even_when_degraded` 两条测试。

所有 `IdentitySettingsOperations(...)` 构造点（L63-70、L150-157、L273-、L316-、L540-、L560-）删除其中的 `compile=partial(identity_api.identity_compile, ...)` 行。

- [ ] **Step 3：跑 routes 测试确认变红**

Run: `pytest tests/webui/test_identity_routes.py -v`
Expected: FAIL —— `test_all_five_actions_are_registered` 因 `IDENTITY_ACTION_NAMES` 仍含 identity-compile 而失败。

- [ ] **Step 4：删 identity_api.identity_compile**

`nanobot/webui/identity_api.py` 删除 `identity_compile` 函数（L136-149）整块。

- [ ] **Step 5：删 gateway_services 装配**

`nanobot/webui/gateway_services.py` 删除 L115 `compile=partial(identity_api.identity_compile, workspace),`。

- [ ] **Step 6：删 identity_routes 接线**

`nanobot/webui/identity_routes.py`：
- 删 `IdentitySettingsOperations` 的 `compile: Callable[..., dict[str, Any]]` 字段（L43）。
- 删 `IDENTITY_ACTION_NAMES` 里的 `"identity-compile",`（L54）。
- 删 `dispatch` 里 `if action == "identity-compile":` 分支（L118-120）。
- 文件头 docstring（L8）`write/reload/compile` 改为 `write/reload`。

- [ ] **Step 7：删 ws_http 白名单映射**

`nanobot/webui/ws_http.py` 删除 L201 `"identity.compile": "/api/settings/identity/compile",`。

- [ ] **Step 8：跑测试确认变绿**

Run: `pytest tests/webui/test_identity_routes.py -v`
Expected: PASS。

Run: `ruff check nanobot/webui/ && .venv/Scripts/python.exe -m basedpyright nanobot/webui/identity_routes.py nanobot/webui/gateway_services.py`
Expected: 无错误（确认 `Callable` 等 import 仍被其他字段使用、无孤儿 import）。

- [ ] **Step 9：commit**

```bash
git add nanobot/webui/identity_api.py nanobot/webui/gateway_services.py nanobot/webui/identity_routes.py nanobot/webui/ws_http.py tests/webui/test_identity_routes.py
git commit -m "$(cat <<'EOF'
refactor(identity): 删除手动规则编译 HTTP/WS 接口链

全自动编译（见后续任务）取代手动按钮后，identity.compile /
identity-compile 整条链（api / routes / ws 白名单 / 装配）摘除，
不留死接口。编译器函数 compile_identity 保留供自动触发调用。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3：保存即编译

**Files:**
- Modify: `nanobot/webui/identity_api.py:86-116`
- Test: `tests/webui/test_identity_routes.py`（新增）

- [ ] **Step 1：写失败测试**

`tests/webui/test_identity_routes.py` 新增（放 write 段之后）。复用现有 `workspace` / `handler` fixture：

```python
def test_write_soul_triggers_auto_compile(handler: IdentitySettingsHandler, workspace: Path):
    """保存编译源文件后，runtime 产物应新鲜，无需任何手动编译动作。"""
    from nanobot.identity.compiler import compiled_status

    assert compiled_status(workspace)["compiled"] is False  # 前置：从未编译

    result = handler.handle(
        "identity-write-file",
        _request(payload={"name": "SOUL.md", "content": "# soul\n\n核心原则：诚实。"}),
    )
    assert result.status == 200

    status = compiled_status(workspace)
    assert status["compiled"] is True
    assert status["fresh"] is True


def test_write_memory_does_not_compile(handler: IdentitySettingsHandler, workspace: Path):
    """MEMORY.md 走 lifecycle 分支，不是编译源，保存不该触发编译。"""
    from nanobot.identity.compiler import compiled_status

    handler.handle(
        "identity-write-file",
        _request(payload={"name": "MEMORY.md", "content": "derived"}),
    )
    assert compiled_status(workspace)["compiled"] is False
```

- [ ] **Step 2：跑测试确认变红**

Run: `pytest tests/webui/test_identity_routes.py::test_write_soul_triggers_auto_compile -v`
Expected: FAIL —— `compiled` 仍为 False（保存未触发编译）。

- [ ] **Step 3：改 identity_write_file**

`nanobot/webui/identity_api.py`：顶部 import 补 `COMPILE_TARGETS`：

```python
from nanobot.identity.compiler import COMPILE_TARGETS, compile_identity
```

（若 Task 2 后 `compile_identity` 已不在 import 中——它本就来自 compiler，`identity_compile` 删除不影响这条 import；确认 `from nanobot.identity.compiler import compile_identity` 仍在，追加 `COMPILE_TARGETS`。）

`identity_write_file` 非 MEMORY 分支（L112-116）改为写盘后自动编译：

```python
    try:
        IdentityStore(workspace).write_file(name, content)
    except IdentityStoreError as exc:
        raise WebUISettingsError(exc.message, status=exc.status) from exc

    # 保存即编译：写的是编译源文件时刷新 runtime 产物。编译是纯本地毫秒级
    # 操作；失败不得让「保存」判失败——写盘已成功，产物下次启动补齐即可。
    if name in {target.source for target in COMPILE_TARGETS}:
        try:
            compile_identity(workspace)
        except Exception:
            logger.warning("identity: 保存后自动编译失败 %s，下次启动补齐", name)

    return {"name": name, "saved": True}
```

- [ ] **Step 4：跑测试确认变绿**

Run: `pytest tests/webui/test_identity_routes.py::test_write_soul_triggers_auto_compile tests/webui/test_identity_routes.py::test_write_memory_does_not_compile -v`
Expected: 两条 PASS。

- [ ] **Step 5：commit**

```bash
git add nanobot/webui/identity_api.py tests/webui/test_identity_routes.py
git commit -m "$(cat <<'EOF'
feat(identity): 保存编译源文件后自动触发规则编译

identity_write_file 写完 SOUL/AGENT/USER 后直接 compile_identity，
用户不再需要手动点编译。MEMORY.md 走 lifecycle 分支不触发。编译异常
容错——写盘成功即算保存成功，产物下次启动补齐。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4：启动即编译（若过期）

**Files:**
- Modify: `nanobot/utils/helpers.py:929-935`
- Test: `tests/`（就近 helpers/sync 测试文件；若无则新建 `tests/utils/test_sync_auto_compile.py`）

- [ ] **Step 1：写失败测试**

新建 `tests/utils/test_sync_auto_compile.py`（若已有 sync_workspace_templates 测试文件则并入）：

```python
from pathlib import Path

from nanobot.identity.compiler import compiled_status, runtime_dir
from nanobot.utils.helpers import sync_workspace_templates


def test_sync_seeds_and_compiles_on_fresh_workspace(tmp_path: Path):
    """全新 workspace 首次 sync：播种后自动编译，产物存在且新鲜。"""
    sync_workspace_templates(tmp_path, silent=True)

    status = compiled_status(tmp_path)
    assert status["compiled"] is True
    assert status["fresh"] is True
    # 出厂模板态：仅 SOUL 产出清洗版，AGENT/USER 被 skip_factory_template 跳过
    assert (runtime_dir(tmp_path) / "identity.core.md").is_file()


def test_sync_does_not_recompile_when_fresh(tmp_path: Path):
    """产物已新鲜时，重复 sync 不重写时间戳（无稳态开销）。"""
    sync_workspace_templates(tmp_path, silent=True)
    stamp = runtime_dir(tmp_path) / ".compiled_at"
    first_mtime = stamp.stat().st_mtime_ns

    sync_workspace_templates(tmp_path, silent=True)
    assert stamp.stat().st_mtime_ns == first_mtime
```

- [ ] **Step 2：跑测试确认变红**

Run: `pytest tests/utils/test_sync_auto_compile.py -v`
Expected: FAIL —— `test_sync_seeds_and_compiles_on_fresh_workspace` 因 sync 目前不编译、`compiled` 为 False。

- [ ] **Step 3：改 sync_workspace_templates**

`nanobot/utils/helpers.py`，在 identity 播种 try 块（L930-935）之后、`if added and not silent:`（L937）之前，插入启动自动编译：

```python
    # 启动即编译：产物缺失 / 过期（首次播种、外部改文件、升级）时补齐。
    # compiled_status 已新鲜时不触发，无稳态写盘开销。局部 import 避免循环依赖。
    try:
        from nanobot.identity.compiler import compile_identity, compiled_status

        if not compiled_status(workspace)["fresh"]:
            compile_identity(workspace)
    except Exception:
        logger.exception("Failed to auto-compile identity for {}", workspace)
```

- [ ] **Step 4：跑测试确认变绿**

Run: `pytest tests/utils/test_sync_auto_compile.py -v`
Expected: 两条 PASS。

- [ ] **Step 5：跑既有 sync / gateway 启动相关测试防回归**

Run: `pytest tests/ -k "sync or workspace_template or gateway_startup" -v`
Expected: PASS（编译是附加副作用，不改变 sync 的返回值/播种行为）。

- [ ] **Step 6：commit**

```bash
git add nanobot/utils/helpers.py tests/utils/test_sync_auto_compile.py
git commit -m "$(cat <<'EOF'
feat(identity): 启动时若编译产物过期则自动补齐

sync_workspace_templates 播种后追加自动编译，覆盖首次播种、外部编辑、
老用户升级与所有 CLI 入口。已新鲜时不触发，无稳态开销。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5：前端下架编译按钮与 POLICIES 提示块

**Files:**
- Modify: `webui/src/lib/api.ts:1388-1407`
- Modify: `webui/src/components/settings/identity/IdentityView.tsx`（多处）

- [ ] **Step 1：删 api.ts 编译客户端**

`webui/src/lib/api.ts` 删除 `CompileIdentityResponse` 接口（L1388-1394）与 `compileIdentityRules` 函数（L1396-1407）。

- [ ] **Step 2：删 IdentityView 编译相关**

`webui/src/components/settings/identity/IdentityView.tsx`：
- import 去掉 `compileIdentityRules`（L27）与 `Sparkles`（L13，若无其他用处）。
- 删 `compileNotice` state（L189）。
- 删 `handleCompile` 函数（L364-379）。
- 删编译按钮 `<Button ... title="规则编译" ...>`（L561-573）。
- 删编译结果行 `{compileNotice ? (...)}`（L665-674）。
- 删 POLICIES.yaml 提示块 `{selected.name === "POLICIES.yaml" && (...)}`（L612-627）。

- [ ] **Step 3：typecheck 前端**

Run: `cd webui && bun run build`（或 `bunx tsc --noEmit`）
Expected: 无类型错误（确认无残留 `compileNotice` / `Sparkles` 引用）。

- [ ] **Step 4：跑前端测试**

Run: `cd webui && bun run test`
Expected: PASS（若有组件测试断言编译按钮存在，一并更新删除该断言）。

- [ ] **Step 5：commit**

```bash
git add webui/src/lib/api.ts webui/src/components/settings/identity/IdentityView.tsx
git commit -m "$(cat <<'EOF'
refactor(webui): 身份页移除规则编译按钮与 POLICIES.yaml 提示块

编译改全自动，手动按钮与 compileIdentityRules 客户端下线；POLICIES.yaml
已从后端清单消失，前端提示块永不可达一并删除。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6：全量验证 + 真实启动冒烟

- [ ] **Step 1：后端 lint + 类型**

Run: `ruff check nanobot/`
Expected: 无错误。

Run: `uv run --no-sync python -m scripts.install_channel_dependencies --all-channels && uv run --no-sync basedpyright`
Expected: 无新增错误。

- [ ] **Step 2：后端全测试**

Run: `pytest tests/identity tests/webui/test_identity_routes.py tests/utils -v`
Expected: 全绿。

Run: `pytest tests/ -q`
Expected: 全绿（无因 compile 接口删除而残留的引用失败）。

- [ ] **Step 3：前端全测试**

Run: `cd webui && bun run test`
Expected: 全绿。

- [ ] **Step 4：真实启动冒烟（关键验收）**

用仓库 `.venv` 起 gateway，验证上一轮 config 报错链已消失、且自动编译生效：

Run: `.venv/Scripts/nanobot.exe gateway`
Expected: 启动日志**不再出现** `Failed to load config` / `No API key configured`；无 `identity.compile` 相关 404；若 workspace 有 identity 文件，日志出现 `identity: 规则编译完成`。

打开 WebUI 身份页：
- 文件列表**无 POLICIES.yaml**；
- **无「规则编译」按钮**；
- 编辑 SOUL.md 保存后，右下角出现「已保存」，**无需点编译**，重启后注入走产物（可查 `identity/runtime/` 时间戳更新）。

- [ ] **Step 5：确认无残留引用**

Run: `grep -rn "identity_compile\|identity-compile\|identity\.compile\|compileIdentityRules\|BADGE_NOT_WIRED\|BADGE_NEEDS_COMPILE" nanobot/ webui/src/ tests/`
Expected: 无命中（i18n locale 里的 `badgeNotWired`/`badgeNeedsCompile`/`compileRules` 等 key 按 spec 保留，不算残留）。

- [ ] **Step 6：若冒烟发现问题，回到对应 Task 修复后重跑本任务**

---

## 验收口径（对照 spec §六）

1. 身份页文件列表不再出现 POLICIES.yaml；构造 read/write 请求访问它返回 403。
2. 身份页无「规则编译」按钮；`identity.compile` / `identity-compile` 接口全链摘除。
3. WebUI 保存 SOUL/AGENT/USER 后不点任何按钮，`identity/runtime/` 产物即新鲜。
4. 全新 workspace 首次启动：gateway 正常起，自动编译产出 SOUL 清洗版，AGENT/USER 模板被跳过，无占位符泄漏进 prompt。
5. 老用户升级：产物缺失/过期时启动自动补齐；已新鲜时不重复写盘。
6. 保存 MEMORY.md 不触发编译；编译失败不阻断保存。
7. ruff / basedpyright / pytest / bun test 全绿。
