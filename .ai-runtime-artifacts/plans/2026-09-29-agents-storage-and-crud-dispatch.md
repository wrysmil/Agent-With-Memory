---
artifact: implementation-dispatch
route: orchestration:dispatcher-workflow
plan: .ai-runtime-artifacts/plans/2026-09-29-agents-storage-and-crud-plan.md
skills:
  - writing-plans
  - orchestration
skills_evidence:
  - harness-kit/.agents/skills/writing-plans/SKILL.md
source:
  - harness-kit/core/orchestration/dispatcher-workflow.md
  - .ai-runtime-artifacts/contracts/2026-09-29-agents-contract.md
created_at: 2026-09-29
---

# Agent 档案存储与 CRUD（一期） — Harness 执行图

> 实施步骤以 **plan** 为准；本文件只描述并行 GROUP / WU 与派发。多轮审阅时优先改本文件，避免扰动 plan 内 Task 细步。
>
> 覆盖 plan 的 Task 1-9。二期（`2026-09-29-agents-runtime-integration-plan.md`）不在本执行图内。

## 执行图

```markdown
GROUP-1:
  WU-01: 数据模型与线格式 + 静态目录 | 标题: Agent 档案模型与静态目录 | 文件: nanobot/agents/__init__.py, nanobot/agents/models.py, nanobot/agents/catalog.py, tests/webui/test_agents_models.py, tests/webui/test_agents_catalog.py | 依赖: 无 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: .worktrees/agents-01-model | branch: feat/agents-model | wu_skills: auto

GROUP-2:
  WU-02: 存储层 | 标题: AgentStore 读写与路径防御 | 文件: nanobot/agents/store.py, tests/webui/test_agents_store.py | 依赖: WU-01 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: .worktrees/agents-02-store | branch: feat/agents-store | wu_skills: auto
  WU-03: API payload 函数与域 handler | 标题: agents_api 与 agents_routes | 文件: nanobot/webui/agents_api.py, nanobot/webui/agents_routes.py, tests/webui/test_agents_api.py, tests/webui/test_agents_routes.py | 依赖: WU-01, WU-02 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: .worktrees/agents-03-api | branch: feat/agents-api | wu_skills: auto
  WU-05: 前端 API 客户端 | 标题: 前端档案 API 客户端 | 文件: webui/src/lib/agents/api.ts, webui/src/tests/api-agents.test.ts | 依赖: 无 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: .worktrees/agents-05-frontend-api | branch: feat/agents-frontend-api | wu_skills: auto

GROUP-3:
  WU-04: 端点注册与 gateway 绑定 | 标题: 注册档案端点并接线 | 文件: nanobot/agent/tools/registry.py, nanobot/webui/settings_routes.py, nanobot/webui/ws_http.py, nanobot/webui/gateway_services.py, tests/webui/test_agents_wiring.py | 依赖: WU-03 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: .worktrees/agents-04-wiring | branch: feat/agents-wiring | wu_skills: auto
  WU-06: 主从布局骨架 + 端点接线 | 标题: AgentsView 改主从双栏并接真实后端 | 文件: webui/src/lib/agents/catalog.ts, webui/src/components/settings/agents/AgentListPane.tsx, webui/src/components/settings/agents/AgentDetailPane.tsx, webui/src/components/settings/agents/useAgentSummary.ts, webui/src/components/settings/agents/AgentsView.tsx, webui/src/components/settings/agents/AgentRow.tsx, webui/src/lib/agents/api.ts, 删除 AgentEditorDialog.tsx / AgentCard.tsx / AgentTreeView.tsx / mock.ts, webui/src/tests/agents-view-integration.test.tsx | 依赖: WU-05 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: .worktrees/agents-06-master-detail | branch: feat/agents-master-detail | wu_skills: auto

GROUP-4:
  WU-07: 详情区 Tab 化与概览 | 标题: 智能体详情拆成概览/能力/设置三个 Tab | 文件: webui/src/components/settings/agents/parts/AgentDetailHeader.tsx, webui/src/components/settings/agents/parts/AgentOverviewTab.tsx, webui/src/components/settings/agents/parts/AgentCapabilityTab.tsx, webui/src/components/settings/agents/parts/AgentSettingsTab.tsx, webui/src/components/settings/agents/parts/AgentSaveBar.tsx, webui/src/components/settings/agents/AgentDetailPane.tsx, 删除 webui/src/components/settings/agents/parts/AgentPreviewRail.tsx, webui/src/tests/agent-detail-tabs.test.tsx | 依赖: WU-06 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: .worktrees/agents-07-detail-tabs | branch: feat/agents-detail-tabs | wu_skills: auto
```

## 依赖图

```
WU-01 ──┬──> WU-02 ──┐
        │            ├──> WU-03 ──> WU-04
        └────────────┘                  │
                                       （前端链路，与 WU-04 无依赖）
WU-05 ──────────────────────────────> WU-06 ──> WU-07
```

**关键路径**：WU-01 → WU-02 → WU-03 → WU-04（后端 4 跳）。
**可并行**：WU-05 从头到尾不碰 Python，只依赖契约 §10，可在 GROUP-2 就开工。
**前端与后端在 WU-04 / WU-06 处会合**，是唯一需要端到端联调的点。
**WU-06 与 WU-07 严格串行**：WU-06 的中间态（右栏单列长表单）本身可用，
WU-07 在其上拆 Tab 并加概览。拆开的理由是每个 WU 单独合流都跑得起来，不产生"半成品"。

## 合并顺序

1. WU-01（其余全部依赖它）
2. WU-02
3. WU-03 与 WU-05 可同时合并
4. WU-04 与 WU-06 可同时合并
5. WU-07（必须在 WU-06 之后）
6. **尾盘**：集体测试（`pytest tests/webui/ -q` + `cd webui && bun run build && bun run test`）→ 集体审查 → Leader 落盘 collective-test 与 code-review

## 各 WU 边界（互不越界的约定）

| 约定 | 说明 |
| --- | --- |
| 线格式只有 camelCase | Python 侧 snake_case，`to_dict()`/`from_dict()` 是唯一的转换点。谁要在别处手拼 key 就是 bug |
| `updatedAt` / `customized` / `type` 服务端说了算 | 客户端传入一律忽略。`AgentStore.save_profile` 是唯一决定者（契约 §4.1） |
| 读路径不落盘 | `AgentStore.__init__` 不读磁盘，`_load_all` 懒加载，出厂预置只进内存 |
| 写只有 4 个 mutation action | `agents.save` / `agents.delete` / `agents.reset` / `agents.visibility`，路径与 action 名的对应见契约 §9.2 |
| 前端不新增类型 | `webui/src/lib/agents/types.ts` 与 `i18n.ts` 一个字都不改；后端描述符形状必须迁就既有 TS 接口 |
| 草稿归 `AgentsView` | `AgentDetailPane` 只切视图、不持有 `draft`。放进 Pane 会导致切 Tab 丢编辑内容（plan §坑 5） |
| `mock.ts` 由 WU-06 删 | WU-05 只新增 `api.ts` 与其测试，**不碰** `AgentsView.tsx` —— 改了 import 就会让 `loadAgents()` 在 WU-06 接手前变成未定义符号，`bun run build` 挂掉 |
| WU-07 不碰后端 | 纯前端重组，`nanobot/` 一个字不动 |

## 已知冲突点（并行时需 Leader 裁决）

**`AgentsView.tsx` 只有 WU-06 触碰。** WU-05 曾被安排顺手改 import 并删 `mock.ts`，
已改掉：那样单独合流 WU-05 会编译失败。

WU-06 与 WU-07 都改 `AgentDetailPane.tsx`，但两者**串行**（WU-07 依赖 WU-06），
不构成并行冲突。后端 WU 不碰 `webui/`，前端 WU 不碰 `nanobot/`，
因此除尾盘联调外无交叉。

## UI 范围外（一期不抄参考设计的哪些部分）

用户在参考截图后确认「按项目现有的功能体系来就行」。以下**明确不做**，
写在这里是为了让 WU-06/07 的 coder 不要再"顺手"加进去：

| 不抄 | 理由 |
| --- | --- |
| 运行历史 Tab | `SubagentStatus` 是内存态，进程结束就没了；持久化是新功能 |
| 在线状态点（离线/空闲） | Agent 不是常驻进程，没有存活状态 |
| 「分配工作」按钮 | 会话概念，属二期运行时接入 |
| 档案级思考强度 / 速度 / 并行运行上限 | 只有全局级；加档案级字段超出「现有功能体系」 |
| 访问权限 / 环境变量 / 自定义参数 | nanobot 没有这些概念 |

## 契约变更通道

任何 WU 发现契约（`.ai-runtime-artifacts/contracts/2026-09-29-agents-contract.md`）
与实现对不上 → **停下来报给 Leader**，由 Leader 改契约并广播，不得单方面改代码。
最可能触发这条的几个点：`ToolDescriptor.locked` 的判定、`ModelDescriptor`
的 `vision`/`toolUse` 恒为 `true`、`scope` 恒不返回 `plugin`。

## 变更记录（可选）

| 轮次 | 日期 | 变更摘要 |
| --- | --- | --- |
| 1 | 2026-09-29 | 初稿 |
| 2 | 2026-09-29 | 拆出 WU-07（详情 Tab 化 + 概览），WU-06 扩为主从布局骨架 + 端点接线；补 UI 范围外清单 |

## Next

- 执行图确认 → 说「开始实现」或「并行执行」
- 只改 plan 任务、不改并行策略 → 仅改 `*-plan.md`
- 只改 WU 拆分 / 依赖 → 改本文件并告知 Leader 审阅
