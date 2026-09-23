# 身份规则编译改全自动 + POLICIES.yaml 移出可编辑名单

- 日期：2026-09-23
- 状态：spec，方向已由用户确认（brainstorming 多轮 + AskUserQuestion）→ 待用户 review 后进 writing-plans
- 路由：`「Harness：brainstorming」`
- 范围：`nanobot/identity/`（catalog / store / bootstrap 播种不动）+ `nanobot/webui/`（identity_api / routes / ws_http / gateway_services）+ `nanobot/utils/helpers.py`（启动编译）+ `webui/`（IdentityView / api.ts）+ 测试
- 关联：`.ai-runtime-artifacts/specs/2026-09-22-identity-rule-compile-spec.md`（本轮把它的"手动点按钮编译"改成全自动）、`.ai-runtime-artifacts/specs/2026-09-22-identity-template-seed-and-404-fix-spec.md`（POLICIES.yaml 播种来源）

---

## 〇、背景：两个用户诉求

### 诉求 1：规则编译不该让人手动点

现状（上一轮实现）：编译入口只有身份页右上角「规则编译」按钮，用户点了才产出 `identity/runtime/` 产物。用户质疑「这个规则编译真的要人来点击吗」。

代码核对结论——**手动点击没有任何技术必要**：
- 编译器（`compiler.py`）纯同步、无 LLM、无网络，毫秒级、幂等。
- 注入侧（`context.py:315-324`）注释已写明「compilation is a token optimization rather than a switch」：不编译也走全文注入，人格不丢。
- `compiled_status()`（`compiler.py:252-257`）用源文件 mtime 比对产物时间戳，改文件不重编译会自动 `source_newer` → 回退全文。

所以「手动点」只是把一个本该自动的优化，包装成了用户必须记住的操作，且与 badge「需编译」互相强化误导。

### 诉求 2：POLICIES.yaml 不该出现在 UI、用户不能编辑

用户要求 POLICIES.yaml 从身份页消失且不可编辑。它是给未来「工具审批执行器」读的机器策略文件，不是给人编辑的人格文本；现状它在 `CORE_FILES` 里带 `BADGE_NOT_WIRED`（「尚未生效」）暴露给用户，属误导。

---

## 一、用户已确认的决策

1. **全自动编译**：删除手动「规则编译」按钮，改为「保存即编译」+「启动即编译（若过期）」两条自动触发。
2. **POLICIES.yaml 保留播种、仅移出可编辑白名单**（AskUserQuestion 选定）：文件仍在 `identity/POLICIES.yaml` 生成（供未来执行器读），但从 `CORE_FILES` 摘除 → UI 不显示 + 后端读写自动 403。
3. **连带清理**（brainstorming 中逐条确认）：删 `store._validate_yaml`（POLICIES.yaml 是唯一 `.yaml`，移出后分支不可达）、删 `BADGE_NOT_WIRED` 常量、删前端 POLICIES.yaml 提示块与编译按钮；10 国 i18n 的 `policiesHint`/`badgeNotWired` key 本轮保留（churn 大、留着无害）。

### 待用户在 review 本 spec 时拍板的一个点

**AGENT.md 的「需编译」badge 如何处理？** 全自动后「需编译」三个字对用户是错的（用户不需要编译任何东西）。

- **推荐（默认按此实现）**：删除 AGENT.md 的 `badge_tone`/`badge_label_key`。它是 `restricted` 文件，UI 会显示「系统管理」图标，语义足够，无需动态状态。
- 备选：把 badge 接 `compiled_status()` 做动态状态灯（「已编译/待更新」）。**这是独立议题**（即用户最初提的「标签不随编译变化」bug），涉及 `list_files` 返回结构 + 前端 types + 新状态字段，范围显著更大，本轮不做，留后续。

---

## 二、方案

### 2.1 全自动编译的两条触发

**触发 A — 保存即编译**（`identity_api.identity_write_file`）

`identity_write_file` 内 `IdentityStore(workspace).write_file(...)` 成功返回后（即非 MEMORY 分支落盘之后），若 `name` 是编译目标源文件（`SOUL.md` / `AGENT.md` / `USER.md`），立即 `compile_identity(workspace)`。

- MEMORY.md 走 lifecycle 分支（`LIFECYCLE_OWNED_FILES`），**不触发编译**（它不是编译源）。
- POLICIES.yaml 已移出白名单，`write_file` 对它抛 403，**根本走不到编译分支**。
- 编译异常不得让保存失败：`compile_identity` 用 try/except 包住，写盘成功即算保存成功，编译失败仅 `logger.warning`。下次启动编译会补上。

**触发 B — 启动即编译（若过期）**（`utils/helpers.sync_workspace_templates`）

在 `sync_workspace_templates` 播种段之后追加：

```python
try:
    from nanobot.identity.compiler import compile_identity, compiled_status
    if not compiled_status(workspace)["fresh"]:
        compile_identity(workspace)
except Exception:
    logger.exception("Failed to auto-compile identity for {}", workspace)
```

- 覆盖：首次播种后、外部编辑器改文件后、老用户升级后、所有 CLI 入口（gateway / agent / commands / webui 都调 sync）。
- `compiled_status` 已新鲜时不触发，无稳态写盘开销。
- 局部 import 避免 import cycle（与同函数内 `ensure_identity_templates` 的局部 import 风格一致）。

**首次安全性**（已在 brainstorming 中实测推演，无需额外代码）：出厂模板态下，AGENT/USER 带 `skip_factory_template=True` 被源头跳过、无产物；SOUL `skip_factory_template=False` 产出清洗版默认人格。注入结果与「不编译」一致，不会把占位符模板灌进 prompt。

### 2.2 POLICIES.yaml 移出可编辑白名单

`catalog.CORE_FILES` 删除 POLICIES.yaml 那条 `IdentityFileSpec`。因 `CORE_FILES` 同时驱动 UI 清单（`store.list_files`）与后端白名单（`store._resolve`），一处删除即达成「UI 不显示 + 读写 403」，无需新增隐藏标志。

`bootstrap._IDENTITY_CORE_SEEDS` **保持不变**——POLICIES.yaml 仍被播种到 `identity/POLICIES.yaml`，供未来执行器读取。播种走 `bootstrap._seed` 直接写盘，不经 `IdentityStore`，不受白名单变化影响。

### 2.3 删除编译 HTTP/WS 接口

手动按钮没了，`identity.compile` 整条链摘除，不留死接口：

| 位置 | 删除内容 |
|---|---|
| `webui/identity_api.py` | `identity_compile()` 函数 |
| `webui/gateway_services.py` | `compile=partial(identity_api.identity_compile, workspace)` |
| `webui/identity_routes.py` | `IdentitySettingsOperations.compile` 字段、`"identity-compile"` action 常量、`if action == "identity-compile"` 分支 |
| `webui/ws_http.py` | `"identity.compile": "/api/settings/identity/compile"` 白名单映射 |
| `webui/src/lib/api.ts` | `compileIdentityRules()`、`CompileIdentityResponse` |
| `webui/.../IdentityView.tsx` | `handleCompile()`、`compileNotice` state、编译按钮、编译结果行、`compileIdentityRules` import |

`compiler.compile_identity()` 函数本体**保留**（触发 A/B 要用）。

### 2.4 前端 POLICIES.yaml 提示块删除

`IdentityView.tsx` 删除 `selected.name === "POLICIES.yaml"` 的权限边界提示块（L612-627）。POLICIES.yaml 已从清单消失，该分支永不可达。

---

## 三、改动清单（文件级）

### 后端
1. `nanobot/identity/catalog.py` — 删 POLICIES.yaml spec（L73-79）；删 `BADGE_NOT_WIRED` 常量（L35）；按 §一决策删 AGENT.md 的 `badge_tone`/`badge_label_key`。
2. `nanobot/identity/store.py` — 删 `_validate_yaml`（L205-216）与 `write_file` 内 `if path.suffix == ".yaml"` 分支（L199-200）。
3. `nanobot/webui/identity_api.py` — `identity_write_file` 写编译源后自动 `compile_identity`（try/except 容错）；删 `identity_compile`。
4. `nanobot/webui/gateway_services.py` — 删 `compile=` 装配。
5. `nanobot/webui/identity_routes.py` — 删 `compile` 字段 / action 常量 / 路由分支。
6. `nanobot/webui/ws_http.py` — 删 `"identity.compile"` 映射。
7. `nanobot/utils/helpers.py` — `sync_workspace_templates` 末尾加启动自动编译（§2.1 触发 B）。
8. `nanobot/identity/bootstrap.py` — **不改**（POLICIES.yaml 播种保留）。

### 前端
9. `webui/src/lib/api.ts` — 删 `compileIdentityRules` / `CompileIdentityResponse`。
10. `webui/src/components/settings/identity/IdentityView.tsx` — 删编译按钮/handleCompile/compileNotice/结果行/import；删 POLICIES.yaml 提示块；（若采纳 §一决策）badge 逻辑无需改，AGENT.md badge 由后端不再下发即自然消失。

### i18n
11. 10 个 locale 的 `policiesHint` / `badgeNotWired` key — **本轮保留**（可选后续清理）。`compileRules`/`compileDone`/`compileEmpty`/`compileFailed` 前端已无引用，key 保留无害。

---

## 四、测试改动清单

### 需修改
- `tests/identity/test_catalog.py`
  - `test_core_files_match_frontend_contract`：期望列表去掉 `"POLICIES.yaml"`。
  - `test_restricted_set_matches_frontend_contract`：期望集合去掉 `"POLICIES.yaml"`。
- `tests/identity/test_identity_store.py`
  - `test_list_files`（L321 / L334 断言 `by_name["POLICIES.yaml"]`）：POLICIES.yaml 不在清单，改为断言其**不出现**在返回中。
  - `test_write_rejects_invalid_yaml` / `test_write_accepts_valid_yaml`（L176-184）：POLICIES.yaml 已非白名单，`write_file` 抛 403 而非 YAML 错误 → **删除这两条**（YAML 校验逻辑已移除）。

### 需删除
- `tests/webui/test_identity_routes.py`
  - `test_write_invalid_yaml_is_4xx`（L402-407）：依赖 POLICIES.yaml 可写 + YAML 校验，两者皆移除 → 删。
  - 任何 `identity-compile` action 相关测试（grep 确认）。

### 需保留（不动）
- `tests/identity/test_identity_templates.py`（L21/L37/L64 断言 POLICIES.yaml **播种**）：播种保留，测试有效，不动。

### 需新增
- 保存 SOUL/AGENT/USER 后经 `identity_write_file` 触发编译 → `identity/runtime/` 产物存在且 `compiled_status()["fresh"]` 为真。
- 保存 MEMORY.md **不**触发编译（lifecycle 分支）。
- `sync_workspace_templates`：产物过期时启动编译补齐；已新鲜时不重复写盘（可用 mtime 或调用计数断言）。
- POLICIES.yaml 移出白名单后：`store.read_file("POLICIES.yaml")` / `write_file` 抛 403（`IdentityStoreError`）；`list_files` 不含它。
- 前端：`compileIdentityRules` 移除后 IdentityView 无编译按钮（若有对应组件测试）。

---

## 五、非目标

- AGENT.md badge 的**动态状态灯**（接 `compiled_status` 显示「已编译/待更新」）——本轮按 §一决策静态删除，动态化留后续独立议题。
- POLICIES.yaml / `prompts/policies.md` 的编译与注入（沿用上一轮非目标）。
- 工具审批执行器读取 POLICIES.yaml 的策略执行（播种文件先占位，执行器未接）。
- i18n 死 key 的清理。

---

## 六、验收口径

1. 身份页文件列表**不再出现 POLICIES.yaml**；直接构造 `identity-read-file`/`identity-write-file` 请求访问 POLICIES.yaml 返回 403。
2. 身份页**无「规则编译」按钮**；`identity.compile` / `identity-compile` 接口已摘除（WS 白名单、路由、api.ts 均无）。
3. 在 WebUI 编辑并保存 SOUL.md（或 AGENT/USER）后，**不点任何按钮**，`identity/runtime/` 产物即为新鲜（`compiled_status().fresh == True`），下一轮对话注入用产物。
4. 全新 workspace 首次启动（仅播种、用户未编辑）：gateway 正常起，注入结果与「未编译」一致（默认 SOUL 人格进 prompt，AGENT/USER 模板被跳过），无占位符泄漏。
5. 老用户升级：已有 `identity/` 文件但产物缺失/过期，启动 `sync_workspace_templates` 自动补齐编译。
6. 保存 MEMORY.md 不触发编译；编译失败不阻断保存（写盘成功即返回 `saved`）。
7. `ruff check nanobot/` 通过；`basedpyright` 无新增错误；`pytest tests/identity tests/webui/test_identity_routes.py` 全绿；`cd webui && bun run test` 全绿。
