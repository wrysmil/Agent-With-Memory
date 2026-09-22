# Verification Lite — 身份模板播种 + 读兜底 + 人格预设

- 日期：2026-09-22
- 路由：`「Harness：Tier 1 Leader 直做」`
- Spec：`.ai-runtime-artifacts/specs/2026-09-22-identity-template-seed-and-404-fix-spec.md`（§2.5 提示词定稿一并实现）
- 范围：三层方案 C（启动播种 / 读时兜底 / 前端预填）+ 5 个人格预设 + 10 语种 i18n

---

## 一、变更摘要

| 层 | 落点 |
|---|---|
| 模板 | `nanobot/templates/`：`SOUL.md`（= balanced 同源）、`USER.md`、**新** `AGENT.md` / `POLICIES.yaml` / `prompts/policies.md`、**新** `personas/{balanced,mentor,creative,companion,tech_expert}.md` |
| 播种 | `nanobot/identity/bootstrap.py` → `ensure_identity_templates()`（skip-if-exists）；`sync_workspace_templates` 末尾接线 |
| 读兜底 | `identity_api.identity_read_file`：store 404 → 包内模板 + `exists=false` / `fromTemplate=true`，不再红横幅 |
| Presets API | `GET /api/settings/identity/presets` → `identity-list-presets`（只读，非 mutation） |
| 前端 | `IdentityView`：缺失文件取模板预填 + 出厂模板提示条；SOUL 工具栏「人格预设」下拉预填草稿；`api.ts` 扩展 |
| i18n | 10 语种补 `settings.identity.*`（含 preset 子树）与 `settings.memory.masterToggle*` |

## 二、验证命令与输出

### 后端

```bash
uv run --no-sync pytest tests/identity/ tests/webui/test_identity_routes.py tests/webui/test_identity_wiring.py -q
```

```
149 passed, 1 skipped in 3.14s
```

覆盖新增：`tests/identity/test_identity_templates.py`（字符上限 / YAML 三键 / SOUL==balanced / 播种幂等与不覆盖）、读兜底 404→模板、presets 五项、wiring 第六路由。

### 前端测试

```bash
cd webui && npx vitest run
```

```
Test Files  1 failed | 77 passed (78)
     Tests  1 failed | 1196 passed (1197)
```

- 通过：`i18n.test`（全语种 shape 对齐）、`api-identity`、`settings-memory-section` 等。
- **失败 1 条与本任务无关**：`app-layout.test > preserves the first message when the gateway rejects a project`（`toHaveFocus()`）——stash 本任务改动后仍复现，属既有问题。

### 类型检查 + 构建

```bash
cd webui && npx tsc -p tsconfig.build.json   # 无输出 = 通过
cd webui && npx vite build                   # ✓ built in 9.06s → ../nanobot/web/dist/
```

## 三、验收对照（spec §四）

| # | 口径 | 结果 |
|---|---|---|
| 1 | dist 重建 | ✅ `vite build` 写入 `nanobot/web/dist`（需用户**重启 gateway** 才加载新 `store/api`） |
| 2 | 冷开页无 404 横幅 / 核心文件有内容 | ✅ 播种 + 读兜底双保险（待运行时复测） |
| 3 | 删文件见模板非 404 | ✅ `test_read_missing_file_returns_factory_template` |
| 4 | MEMORY.md 仍走 lifecycle | ✅ store 拒绝写路径未动；api 分流未动 |
| 5 | 穿越/白名单/YAML/1500 | ✅ 149 绿含原 `test_identity_store` |
| 6 | 命令证据 | ✅ 本文件 |
| 7 | i18n 十语种 | ✅ shape 测试 0 missing |
| 8 | presets 五项 + 下拉预填 | ✅ 后端单测 + 前端代码（UI 待运行时点验） |
| 9 | 模板字符数 / YAML dict | ✅ `test_all_templates_exist_and_within_char_limit` 等 |

## 四、遗留 / 下一步

1. **用户侧生效**：重启 gateway + 浏览器硬刷新（截图旧 UI 证明 dist 曾未更新）。
2. **三个工具按钮**（重载 / LM 优化 / 规则编译）按指示 **后续再做**；LM/规则编译仍 `not_enabled` 占位。
3. `app-layout.test` 焦点断言失败：与本任务无关，可另开 bugfix。
4. 未 commit / 未 push（遵循禁止自动 push）。
5. 临时脚本 `webui/scripts/fill-identity-i18n.cjs` 可删（一次性补齐用）。
