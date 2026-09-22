---
artifact: implementation-dispatch
route: orchestration:dispatcher-workflow
plan: .ai-runtime-artifacts/plans/2026-09-18-identity-backend-plan.md
skills:
  - writing-plans
  - orchestration
skills_evidence:
  - .claude/skills/writing-plans/SKILL.md
source:
  - core/orchestration/dispatcher-workflow.md
  - .ai-runtime-artifacts/plans/2026-09-18-identity-backend-plan.md
created_at: 2026-09-18
---

# 身份文件后端接入 — Harness 执行图

> 实施步骤以 **plan** 为准；本文件只描述并行 GROUP / WU 与派发。多轮审阅时优先改本文件，避免扰动 plan 内 Task 细步。

## 执行图

```markdown
GROUP-1: 后端底座（identity 包）
  WU-01: 身份目录唯一真相源 + 老工作区迁移 | 标题: 身份目录与迁移底座 | 文件: nanobot/identity/__init__.py, nanobot/identity/catalog.py, nanobot/identity/bootstrap.py, nanobot/agent/context.py, tests/identity/test_catalog.py, tests/identity/test_bootstrap.py | 依赖: 无 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: <WORKTREE-INIT> | branch: feature/identity-backend | wu_skills: auto
  WU-02: IdentityStore 读写与路径穿越防护 | 标题: 身份文件读写安全层 | 文件: nanobot/identity/store.py, tests/identity/test_store.py | 依赖: WU-01 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: <同 WU-01> | branch: feature/identity-backend | wu_skills: auto

GROUP-2: WebUI API 层
  WU-03: API 实现 + 领域 handler | 标题: 身份 API 与路由 handler | 文件: nanobot/webui/identity_api.py, nanobot/webui/identity_routes.py, tests/webui/test_identity_routes.py | 依赖: WU-02 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: <同 WU-01> | branch: feature/identity-backend | wu_skills: auto
  WU-04: 路由注册 + 重载/编译占位 | 标题: 路由注册与重载链路 | 文件: nanobot/webui/settings_routes.py, nanobot/webui/ws_http.py, nanobot/webui/gateway_services.py, nanobot/webui/identity_api.py | 依赖: WU-03 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: <同 WU-01> | branch: feature/identity-backend | wu_skills: auto

GROUP-3: 前端接线（可与 GROUP-2 并行）
  WU-05: 前端 API 客户端 | 标题: 身份前端客户端 | 文件: webui/src/lib/api.ts, webui/src/tests/api-identity.test.ts | 依赖: 无（契约已在 plan 冻结） | wu_type: ui | agent_role: coder | workspace_scope: wu | worktree_path: <同 WU-01> | branch: feature/identity-backend | wu_skills: auto
  WU-06: IdentityView 去 mock + i18n | 标题: 身份页接线真实后端 | 文件: webui/src/components/settings/identity/IdentityView.tsx, webui/src/i18n/locales/zh-CN/common.json, webui/src/i18n/locales/en/common.json | 依赖: WU-05 | wu_type: ui | agent_role: coder | workspace_scope: wu | worktree_path: <同 WU-01> | branch: feature/identity-backend | wu_skills: auto

GROUP-4: 回归
  WU-07: 整体回归（后端全量 + basedpyright + ruff + 前端构建与 vitest + 手工冒烟） | 标题: 身份后端整体回归 | 文件: 无新增（只跑命令） | 依赖: WU-04, WU-06, WU-08 | wu_type: smoke | agent_role: smoke-tester | workspace_scope: none | worktree_path: <同 WU-01> | branch: feature/identity-backend | wu_skills: auto

GROUP-3b: 前端导航接线补齐（后置，因为与 WU-06 争 i18n）
  WU-08: 从主 checkout 移植身份页导航接线 | 标题: 补齐身份页可达性 | 文件: webui/src/App.tsx, webui/src/components/settings/contracts.ts, webui/src/components/settings/SettingsSidebar.tsx, webui/src/components/settings/SettingsPage.tsx, webui/src/globals.css, webui/src/i18n/locales/zh-CN/common.json, webui/src/i18n/locales/en/common.json | 依赖: WU-06 | wu_type: ui | agent_role: coder | workspace_scope: wu | worktree_path: <同 WU-01> | branch: feature/identity-backend | wu_skills: auto
```

## ⚠️ WU-08 的由来（实施中发现的基线缺口）

**worktree 基线（`8ebe6d1`）不含身份页的前端导航接线。** 上一会话完成后端未动、前端却只存在主 checkout 的**未提交**工作区里，因此 worktree 从提交历史建出来时不带它。已核实 worktree 缺以下全部：

| 文件 | 主 checkout 的差异 | 缺了的后果 |
|---|---|---|
| `webui/src/App.tsx` | `+ "identity",`（`SETTINGS_SECTION_KEYS`） | 路由校验不过，点身份会弹回概述 |
| `webui/src/components/settings/contracts.ts` | `+ \| "identity"`（`SettingsSectionKey`） | 类型不通 |
| `webui/src/components/settings/SettingsSidebar.tsx` | `+ FileText` import、`+ { key: "identity", ... }` | **侧栏没有入口，页面进不去** |
| `webui/src/components/settings/SettingsPage.tsx` | `+ import IdentityView`、`+ case "identity"`、容器 class 把 identity 与 channels 并列 | 不渲染任何东西 |
| `webui/src/globals.css` | `+ textarea.no-resize { resize: none; }` | 编辑器可拖拽，破坏行对齐 |
| `webui/src/i18n/locales/{zh-CN,en}/common.json` | `settings.identity.*` 块（`badgeFullTextInject`、`editorFooterHint` 等） | 文案全部退化为 fallback |

**WU-06 已在无此基线的情况下工作**：它在 worktree 里新建了 `IdentityView.tsx`（结构标记与主 checkout 一致，mock 已移除、真实 API 已接），并往 **缺少既有 identity 键块** 的 i18n 文件里加了 `compileNotEnabled` 等新键。因此 WU-08 必须**合并**两边（主 checkout 的既有键 + WU-06 的新键），不能直接覆盖。

**WU-08 后置的原因**：与 WU-06 争 `i18n/locales/*.json`，必须等 WU-06 落地。

## 并行策略

| 波次 | WU | 说明 |
| --- | --- | --- |
| 波次 1 | WU-01 ∥ WU-05 | 两者无交集：WU-01 在 Python 侧，WU-05 在 TS 侧且接口契约已在 plan 冻结 |
| 波次 2 | WU-02 | 依赖 WU-01 的 `catalog` |
| 波次 3 | WU-03 ∥ WU-06 | WU-03 依赖 WU-02 已就绪；WU-06 只依赖 WU-05 |
| 波次 4 | WU-04 | 依赖 WU-03 |
| 波次 5 | WU-07 | 尾盘回归，须全部完成 |

## 派发注意

- **WU-01 与 WU-02 必须同 worktree 串行**：`store.py` 直接 import `catalog`，并行会撞 import 错误。
- **WU-04 触及 `ws_http.py` 与 `settings_routes.py`**，是本次唯一会改到共享入口的 WU，必须等 WU-03 落地后再动，避免与 GROUP-3 的前端改动在同一文件上冲突（两者不重叠，但 reabase 顺序要保证 WU-04 在后）。
- **WU-06 完成后必须真跑一次浏览器冒烟**（plan Task 9 Step 5）。本页此前是静态 mock，接线后可能出现"能构建但白屏"，只跑 vitest 不足以证明可用。

## 变更记录（可选）

| 轮次 | 日期 | 变更摘要 |
| --- | --- | --- |
| 1 | 2026-09-18 | 初稿 |

## Next

- 执行图确认 → 说「开始实现」或「并行执行」
- 只改 plan 任务、不改并行策略 → 仅改 `*-plan.md`
- 只改 WU 拆分 / 依赖 → 改本文件并告知 Leader 审阅
