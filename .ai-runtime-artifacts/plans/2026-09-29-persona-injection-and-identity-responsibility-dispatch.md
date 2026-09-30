---
artifact: implementation-dispatch
route: orchestration:dispatcher-workflow
plan: .ai-runtime-artifacts/plans/2026-09-29-persona-injection-and-identity-responsibility-plan.md
skills:
  - writing-plans
  - orchestration
skills_evidence:
  - ~/.claude/skills/writing-plans/SKILL.md
source:
  - harness-kit/core/orchestration/dispatcher-workflow.md
created_at: 2026-09-29
---

# Persona 注入 + 身份文件职责校准 — Harness 执行图

> 实施步骤以 **plan** 为准；本文件只描述并行 GROUP / WU 与派发。

## 执行图

```markdown
GROUP-1:
  WU-1: Task 1 | 标题: persona 注入段落（ContextBuilder） | 文件: nanobot/agent/context.py, tests/agent/test_context_builder.py | 依赖: 无 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: <worktree> | branch: feature/persona-injection | wu_skills: auto
  WU-2: Task 2 | 标题: 激活态读写端点 + token 计量字段（identity settings 域） | 文件: nanobot/webui/identity_api.py, identity_routes.py, settings_routes.py, gateway_services.py, ws_http.py, tests/webui/test_identity_persona_api.py | 依赖: 无 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: <worktree> | branch: feature/persona-injection | wu_skills: auto
  WU-4: Task 3 | 标题: tech_expert 样板重写 + 删除空转徽标 | 文件: nanobot/templates/personas/tech_expert.md, nanobot/identity/catalog.py, tests/identity/test_catalog.py | 依赖: 无 | wu_type: chore | agent_role: implementer | workspace_scope: wu | worktree_path: <worktree> | branch: feature/persona-injection | wu_skills: auto

GROUP-2 (依赖 GROUP-1 全部返回):
  WU-3: Task 4 | 标题: WebUI persona 下拉 + i18n | 文件: webui/src/lib/api.ts, webui/src/components/settings/identity/IdentityView.tsx, nanobot/channels/websocket/webui/locales/*.json, webui/src/tests/identity-persona-picker.test.tsx | 依赖: WU-2 | wu_type: ui | agent_role: coder | workspace_scope: wu | worktree_path: <worktree> | branch: feature/persona-injection | wu_skills: auto
```

## 文件冲突检查

三个 GROUP-1 的 WU 文件互不重叠：

| WU | 写文件 |
| --- | --- |
| WU-1 | `nanobot/agent/context.py`、`tests/agent/test_context_builder.py` |
| WU-2 | `nanobot/webui/identity_api.py`、`identity_routes.py`、`settings_routes.py`、`tests/webui/test_identity_persona_api.py` |
| WU-4 | `nanobot/templates/personas/tech_expert.md`、`nanobot/identity/catalog.py`、`tests/identity/test_catalog.py` |

WU-3 依赖 WU-2 的端点契约（`GET /api/settings/identity/persona`、`identity.persona.set` mutation），故单独成 GROUP-2。

**已知跨 WU 耦合（需在 prompt 中点名）：** WU-4 删除 `BADGE_FULL_TEXT_INJECT` 徽标时，`webui/src/components/settings/identity/IdentityView.tsx:60` 的 `BADGE_DEFAULTS` 里也有该 key。WU-4 只改 Python 侧与模板，**不要碰 `IdentityView.tsx`**（属 WU-3 的文件），由 WU-3 一并清理，避免同文件并发写。

## 尾盘

- GROUP-2 返回后：集体测试（`pytest tests/` + `ruff check` + `basedpyright` + `bun run test` + `bun run build`）→ Leader 落盘 `verifications/*-collective-test.md`
- 集体审查 → Leader 落盘 `reviews/*-code-review.md`
- 端到端手测（plan Task 5 Step 2）→ 落盘 `verifications/*-verification.md`

## 变更记录

| 轮次 | 日期 | 变更摘要 |
| --- | --- | --- |
| 1 | 2026-09-29 | 初稿 |

## Next

- 执行图确认 → 说「开始实现」或「并行执行」
- 只改 plan 任务、不改并行策略 → 仅改 `*-plan.md`
- 只改 WU 拆分 / 依赖 → 改本文件并告知 Leader 审阅
