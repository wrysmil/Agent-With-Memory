# 身份「规则编译」真实现 + 编译产物接管注入

- 日期：2026-09-22
- 状态：spec，方向已经用户确认（AskUserQuestion）→ 直接实现
- 范围：`nanobot/identity/`（新 compiler）+ `nanobot/agent/context.py`（注入）+ `nanobot/webui/`（api/routes/ws 白名单）+ `webui/`（去 LM 按钮、接真编译）+ i18n
- 路由：`「Harness：brainstorming」` → 用户已确认两点决策，按 Tier 1 Leader 直做实现
- 关联：`.ai-runtime-artifacts/specs/2026-09-22-identity-template-seed-and-404-fix-spec.md`（上一轮，其 §六把 PromptCompiler 列为非目标）、`.ai-runtime-artifacts/research/2026-09-16-openakita-identity-config-and-memory-md-research.md` §二 2.6 / §四 / §五

---

## 〇、背景：按钮为什么「还不行」

用户反馈（2026-09-22）身份页「规则编译」不可用。代码核对后是**两处叠加的坏**：

| # | 位置 | 现状 |
|---|---|---|
| 1 | `nanobot/webui/ws_http.py:199-200` | WS mutation 白名单**只有** `identity.file.save` / `identity.reload`，**没有 `identity.compile`**。前端 `client.requestMutation("identity.compile", …)` 在门口就 404。 |
| 2 | `nanobot/webui/identity_api.py:135` | `identity_compile(mode)` 是占位，恒返回 `{"status":"not_enabled"}`。 |

第 3 处结构缺口：`ContextBuilder.BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md"]`（`nanobot/agent/context.py:108`）——
`identity/AGENT.md` / `POLICIES.yaml` / `prompts/policies.md` **从未进入 system prompt**。`AGENT.md` 却带「需编译」徽标，属空头承诺。

## 一、用户已确认的决策

1. **编译产物接管注入**：产物写 `identity/runtime/`，注入侧优先读产物、缺失/过期回退全文。
2. **前端去掉「LM 优化」按钮**（不做 LLM 编译）。

## 二、方案

### 2.1 规则编译器（新 `nanobot/identity/compiler.py`）

纯同步、无 LLM、无网络，与 openakita `compile_all(use_llm=False)` 同构。三个编译目标：

| key | 源文件（catalog 逻辑名） | 产物 | 字符预算 |
|---|---|---|---|
| `identity_core` | `SOUL.md` | `runtime/identity.core.md` | 1200 |
| `agent_behavior` | `AGENT.md` | `runtime/agent.behavior.md` | 900 |
| `user_profile_core` | `USER.md` | `runtime/user.profile.core.md` | 600 |

单目标流水线 `_compile_one(content, target)`：

1. **章节抓取**：按 `owned_markers` 命中标题 → 取这些章节；一个都没命中 → 取全部非标题行（openakita §4.3 的降级）。
2. **行级剔除**：`excluded_markers` 命中即丢（占位符 / 空 bullet / 分隔线）。
3. **去重**、行尾空白清理、单行截断 `MAX_LINE_CHARS = 240`。
4. **预算封顶**：`max_chars` 超限则逐行累加，最后一行二分切齐（openakita §4.4）。
5. 结果为空 → **不写产物**，计入 `skipped`（不伪造静态兜底文案；注入侧自会回退全文）。

**有意与 openakita 的差异**：不做「平台职责关键词」激进剔除。nanobot 的平台提示层远小于 openakita，
激进剔除会静默丢掉用户自己写的行为规则；所以剔除只针对占位符与噪声。压缩收益主要来自去重 + 预算封顶。

### 2.2 新鲜度（`identity/runtime/`）

- `.compiler_version`：当前 `COMPILED_SCHEMA_VERSION = "1"`，不等即整体过期。
- `.compiled_at`：写完后把 mtime 抬到 `max(源文件 mtime) + 1ns`（openakita §4.5 踩坑做法，防同秒/低精度文件系统误判）。
- `read_compiled(workspace) -> dict | None`：**整体**新鲜才返回产物；任一源文件比 `.compiled_at` 新 → `None`。
- 产物写入用 `tmp + os.replace` 原子替换。

### 2.3 注入语义（安全优先：永不因编译状态丢内容）

`ContextBuilder._load_bootstrap_files` 对身份层逐文件取值：

```text
content_for(SOUL.md / AGENT.md / USER.md) =
    编译产物（当整体新鲜 且 该目标产物非空）
    否则 全文（沿用现有 _SKIPPABLE_DEFAULTS：等于出厂模板则跳过）
```

- 未编译 / 过期 → 回退**全文**，且**把 `AGENT.md` 一并纳入**（新增到 `_SKIPPABLE_DEFAULTS`，出厂模板不进 prompt，用户改过才进）。
- 顺序：`AGENTS.md`（项目指令，不动）→ `SOUL.md` → `AGENT.md` → `USER.md`。
- 「需编译」徽标的语义随之明确：**有改动待重新编译**（不是「不编译就不生效」），避免「改了 SOUL 看不到变化」的回归。

**非目标**：`POLICIES.yaml` / `prompts/policies.md` 本轮不编译、不注入（前者是给未来工具审批执行器读的机器策略，后者的「系统段落覆写」需要单独的注入位设计）。

### 2.4 后端接线

| 文件 | 改动 |
|---|---|
| `identity_api.py` | `identity_compile(workspace, mode="")` 真实现：调 compiler，返回 `{status, modeUsed:"rules", requestedMode, compiledFiles, skipped}`。非 `rules` 的 mode 降级并如实标注。 |
| `gateway_services.py:115` | `compile=partial(identity_api.identity_compile, workspace)`（当前未绑定 workspace） |
| `ws_http.py` | mutation 白名单加 `"identity.compile": "/api/settings/identity/compile"` |

路由/契约层无需改动：`/api/settings/identity/compile` → `identity-compile` 已注册（`settings_routes.py:171,194`）。

### 2.5 前端

- **删除**「LM 优化」按钮 + `handleCompile("llm")` 路径 + `settings.identity.compileLlm` key（10 语种）。
- 「规则编译」→ `requestMutation("identity.compile", { mode: "rules" })`；成功显示绿色提示条（新增 `compileDone`，附产物数量），失败沿用红色 `saveError`。
- 移除 `Wand2` 图标 import（不再使用）。

### 2.6 i18n（10 语种）

删 `settings.identity.compileLlm`；增 `settings.identity.compileDone`。`compileNotEnabled` 保留（真实失败时仍用）。

## 三、验收口径

1. `uv run --no-sync pytest tests/identity/ tests/webui/ tests/agent/test_context*.py -q` 全绿。
2. 新 `tests/identity/test_identity_compiler.py`：章节抓取 / 降级全文 / 占位符剔除 / 去重 / 预算封顶 / 空结果不写产物 / schema 不符即过期 / 源文件更新即过期。
3. `tests/webui/test_identity_routes.py`：`identity-compile` 动作返回 `status=ok` 且产物落盘。
4. 注入回退：编译产物新鲜 → prompt 含产物体；删产物 → 回退全文且 AGENT.md 出现；产物过期 → 回退全文。
5. 前端 `npx tsc -p tsconfig.build.json` 无错、`npx vitest run` 通过、`npx vite build` 成功。
6. 命令证据落 `.ai-runtime-artifacts/verifications/2026-09-22-identity-rule-compile-verification-lite.md`。

## 四、风险与权衡

| 风险 | 影响 | 缓解 |
|---|---|---|
| 注入路径是热路径，改错影响全部对话 | 高 | 回退语义「永不丢内容」，且回退是默认态（未编译时行为与今天几乎一致，仅多注入 AGENT.md） |
| 编译产物漏掉用户写的规则 | 中 | 不做平台关键词激进剔除；空结果不写产物 |
| `identity_compile` 签名变更 | 低 | 只此一处 wiring + 路由 dispatch 传 `mode`，一并改 |
| 去 LM 按钮后 10 语种 key 残留 | 低 | 删 key 后由 i18n shape 测试兜底 |

## 五、非目标

- LLM（LM 优化）编译：按用户决策移除入口，后端不做
- 自动编译（openakita 的「构建 prompt 时发现过期就重编」）：本轮只做手动按钮，避免写-读路径耦合
- `POLICIES.yaml` / `prompts/policies.md` 的编译与注入
- openakita 的 hash 升级账本 `_pending_upgrades`
- `AGENTS.md`（项目指令，大写复数）迁移或编译
