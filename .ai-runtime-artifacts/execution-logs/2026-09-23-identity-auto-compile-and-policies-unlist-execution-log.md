# 交接：身份规则编译全自动 + POLICIES.yaml 下架

- 日期：2026-09-24
- 分支：`feature/memory-system`（跟踪 `origin/feature/memory-system`）
- 关联：spec `.ai-runtime-artifacts/specs/2026-09-23-identity-auto-compile-and-policies-unlist-spec.md` / plan `.ai-runtime-artifacts/plans/2026-09-23-identity-auto-compile-and-policies-unlist.md`
- 状态：**Task 1 完成并提交；Task 2 进行到 grep 定位即中断；Task 3-6 未开始。工作树干净。**

## 已提交（均未推送，fast-forward 可安全 push）

| commit | 内容 |
|---|---|
| `ba6f48e` | （开工前的既有 WIP 基线）出厂模板不编译 + prompts/policies.md 注入 + 尚未生效徽标 |
| `9c64161` | **Task 1**：POLICIES.yaml 移出 CORE_FILES + 删 AGENT.md「需编译」badge + 删 store._validate_yaml/import yaml |

Task 1 验证：`pytest tests/identity/ tests/webui/test_identity_routes.py` 137 passed；`ruff check nanobot/identity/` 通过；播种测试 `test_identity_templates.py` 仍绿（POLICIES.yaml 播种未受影响）。

## Task 2 现场：已 grep 定位，尚未改任何代码

要删的 `identity-compile` 接口全链命中点（**plan 只列了 4 个文件，实际有 5 个**）：

- `nanobot/webui/identity_api.py:136-149` 函数 `identity_compile`（import `compile_identity` 在 L25，**Task 3 还要用，别删这条 import**）
- `nanobot/webui/gateway_services.py:115` `compile=partial(identity_api.identity_compile, workspace),`
- `nanobot/webui/identity_routes.py`：L8 docstring `write/reload/compile`→`write/reload`、L43 dataclass 字段 `compile: Callable[...]`、L54 `"identity-compile",`、L118-120 dispatch 分支
- `nanobot/webui/ws_http.py:201` `"identity.compile": "/api/settings/identity/compile",`
- ⚠️ **plan 遗漏：`nanobot/webui/settings_routes.py`** 三处也是本接口链，必须一并删：
  - L171 `"/api/settings/identity/compile": "identity-compile",`
  - L194 `"/api/settings/identity/compile",`（在 `_IDENTITY_MUTATION_PATHS` 里）
  - L300 `compile=_unavailable,`（在 `_null_identity_operations` 里）——**删了 dataclass 的 compile 字段后，这里不删会因传未知 kwarg 直接报错**

测试侧 `tests/webui/test_identity_routes.py`：
- L88 `test_all_six_actions_are_registered`→ 改 5 action，删 L94 `"identity-compile",`
- L109 删 `handler.handle("identity-compile", ...)`
- L68/155/280/321/537/557 六个 `IdentitySettingsOperations(...)` 构造点各删 `compile=partial(...)` 行
- L566 起 `# ---- compile ----` 段整块删（`test_compile_writes_runtime_products`、`test_compile_reports_requested_mode_even_when_degraded`）

TDD 顺序：先改测试看红（`IDENTITY_ACTION_NAMES` 仍含 identity-compile）→ 删实现 → 看绿。

## Task 3 现场：保存即编译（未开始）

锚点 `identity_api.py:86-116 identity_write_file`，非 MEMORY 分支当前是：
```python
    try:
        IdentityStore(workspace).write_file(name, content)
    except IdentityStoreError as exc:
        raise WebUISettingsError(exc.message, status=exc.status) from exc
    return {"name": name, "saved": True}
```
在 `return` 前插入（需 L25 import 追加 `COMPILE_TARGETS`）：
```python
    if name in {target.source for target in COMPILE_TARGETS}:
        try:
            compile_identity(workspace)
        except Exception:
            logger.warning("identity: 保存后自动编译失败 %s，下次启动补齐", name)
```
新增测试见 plan Task 3 Step 1（写 SOUL 触发编译 / 写 MEMORY 不触发）。**依赖 Task 2 先合入**（同一文件、同一 import 行），建议按 2→3 顺序做。

## Task 4-6（未开始，细节见 plan）

- Task 4：`nanobot/utils/helpers.py` `sync_workspace_templates` 播种后加「若过期则编译」；新建 `tests/utils/test_sync_auto_compile.py`
- Task 5：前端 `webui/src/lib/api.ts` 删 `compileIdentityRules`/`CompileIdentityResponse`；`IdentityView.tsx` 删编译按钮/handleCompile/compileNotice/结果行/POLICIES 提示块；`bun run build` + `bun run test`
- Task 6：全量 ruff/basedpyright/pytest/bun test + 真实起 gateway 冒烟（用 `.venv/Scripts/nanobot.exe gateway`，验证自动编译生效、UI 无 POLICIES.yaml/无编译按钮）+ 残留引用 grep

## 环境备忘（本轮踩过）

- 必须用**仓库** `.venv/Scripts/nanobot.exe`（0.3.0），不是全局装的 0.1.5.post3，否则 config 校验报错 + `provider 'None'`。
- websockets 若报 `No module named 'websockets.asyncio.server'`：`uv sync --all-extras --dev --reinstall-package websockets`（`uv sync` 不带 `--all-extras` 会掉 extras）。
- 全程不 push/不开 PR 由用户逐轮授权；每次 commit 尾 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。
