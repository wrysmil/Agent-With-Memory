# Verification Lite — 身份「规则编译」真实现 + 编译产物接管注入

- 日期：2026-09-22
- 路由：`「Harness：brainstorming」`（用户 AskUserQuestion 确认两点决策后按 Tier 1 Leader 直做）
- Spec：`.ai-runtime-artifacts/specs/2026-09-22-identity-rule-compile-spec.md`
- 上游：`.ai-runtime-artifacts/specs/2026-09-22-identity-template-seed-and-404-fix-spec.md` §六 非目标（PromptCompiler 原列此轮）

---

## 一、缺陷根因（用户报「规则编译还不行」）

**两处叠加**，缺任一处修不好：

| # | 位置 | 问题 |
|---|---|---|
| 1 | `nanobot/webui/ws_http.py:199-200` | WS mutation 白名单只有 `identity.file.save` / `identity.reload`，**缺 `identity.compile`** → 前端 `requestMutation("identity.compile")` 在翻译层就 404 |
| 2 | `nanobot/webui/identity_api.py:135` | `identity_compile(mode)` 是占位，恒返回 `{"status":"not_enabled"}` |

第 3 处结构缺口：`ContextBuilder` 从不注入 `identity/AGENT.md`（`BOOTSTRAP_FILES` 只有 `AGENTS.md`/`SOUL.md`/`USER.md`），而 `AGENT.md` 却带「需编译」徽标。

## 二、变更摘要

| 层 | 落点 |
|---|---|
| 编译器（新） | `nanobot/identity/compiler.py`：纯规则同步编译，三目标 `SOUL.md→identity.core.md` / `AGENT.md→agent.behavior.md` / `USER.md→user.profile.core.md`；章节抓取（抓不到→降级全部非标题行）→ 剔除占位符与 Markdown 噪声 → 去重 → 单行 240 截断 → 字符预算二分封顶（1200/900/600） |
| 新鲜度 | `identity/runtime/`：`.compiler_version`（schema `"1"`）+ `.compiled_at`（mtime 抬到源文件 +1ns，防秒级精度误判）；产物原子写（tmp + `os.replace`） |
| 注入 | `context.py:_load_bootstrap_files`：产物整体新鲜 → 注入产物；未编译/过期/读不出 → 回退**全文**（含新纳入的 `AGENT.md`，出厂模板经 `_SKIPPABLE_DEFAULTS` 跳过） |
| API | `identity_compile(workspace, mode="")` 真实现；非 `rules` 的 mode 降级并如实回 `requestedMode` |
| 接线 | `ws_http.py` 补 `identity.compile` 白名单；`gateway_services.py` 改 `partial(..., workspace)` |
| 前端 | `IdentityView`：**删「LM 优化」按钮**，「规则编译」真调用 + 绿色结果条（产物计数 / 无内容） |
| i18n | 10 语种：删 `compileLlm` / `compileNotEnabled`，增 `compileDone` / `compileEmpty` / `compileFailed` |

## 三、验证命令与输出

### 后端全量

```bash
uv run --no-sync pytest tests/ -q
```

```
exit code 0（全绿）
```

改动子集（含 18 例新编译器单测 + 注入回退 5 例）：

```bash
uv run --no-sync pytest tests/identity/ tests/webui/test_identity_routes.py \
  tests/webui/test_identity_wiring.py tests/agent/test_context_builder.py \
  tests/agent/test_context_prompt_cache.py -q
```

```
239 passed, 1 skipped in 9.10s
```

### Lint

```bash
uv run --no-sync ruff check nanobot/identity/ nanobot/agent/context.py \
  nanobot/webui/ identity_api.py tests/identity/ tests/agent/test_context_builder.py ...
```

```
All checks passed!
```

（`ruff check nanobot/ tests/` 全仓另有 63 项既有告警，集中在未触碰的 `tests/webui/test_refresh_md_route.py` 等，非本轮引入。）

### 前端类型 + 测试

```bash
cd webui && npx tsc -p tsconfig.build.json   # 无输出 = 通过
cd webui && npx vitest run
```

```
Test Files  1 failed | 77 passed (78)
     Tests  1 failed | 1196 passed (1197)
```

失败 1 条为**既有问题**：`app-layout.test.tsx:855` 的 `toHaveFocus()` 断言（`preserves the first message when the gateway rejects a project`）。上一轮 verification-lite 已记录该失败在 stash 本任务改动后仍复现，与本任务无关。

## 四、验收对照（spec §三）

| # | 口径 | 结果 |
|---|---|---|
| 1 | 后端测试全绿 | ✅ `pytest tests/` exit 0 |
| 2 | 编译器单测（章节/降级/占位符/去重/预算/空产物/schema/源更新） | ✅ `tests/identity/test_identity_compiler.py` 18 例 |
| 3 | `identity-compile` 动作返回 ok 且产物落盘 | ✅ `test_compile_writes_runtime_products` |
| 4 | 注入回退（新鲜→产物 / 无产物→全文 / 过期→全文 / 出厂 AGENT 模板不进 prompt） | ✅ `TestCompiledIdentityInjection` 5 例 |
| 5 | tsc / vitest / ruff | ✅ 见上（vitest 1 例既有失败） |
| 6 | 命令证据 | ✅ 本文件 |

## 五、遗留 / 下一步

1. **`vite build` 未执行**（`nanobot/web/dist/` 被 `.gitignore:45` 忽略，不影响本次提交）：用户运行时需 `cd webui && npx vite build` + **重启 gateway** + 浏览器硬刷新，才看得到新 UI。
2. UI 未做运行时点验（未在真实浏览器点「规则编译」）——失败重灾点在 WS 白名单，已由单测锁定，但端到端仍需人工确认。
3. **非目标（spec §五）**：LLM/LM 编译（按用户决策移除入口）、构建 prompt 时自动重编译、`POLICIES.yaml` / `prompts/policies.md` 的编译与注入、openakita hash 升级账本。
4. 「需编译」徽标语义已明确为「有改动待重新编译」（非「不编译不生效」）——旧文案未改，如需更准确措辞另开小改。
5. 未提交前的工作区还含上一轮 identity 模板播种 + memory 页移除的改动，一并纳入本次提交。
