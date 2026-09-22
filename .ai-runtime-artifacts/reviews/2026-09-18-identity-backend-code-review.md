---
artifact: code-review
route: requesting-code-review
plan: .ai-runtime-artifacts/plans/2026-09-18-identity-backend-plan.md
created_at: 2026-09-18
status: APPROVE
---

# 身份后端接入 — Code Review

## 结论

**APPROVE** — 0 Critical / 3 Important / 3 Suggestion / 3 Nit。Important 项均为可延后改进，不阻塞合并。

## 范围

9 个 commit（`e3c609b`..`3823405`），覆盖 Python 底座 + WebUI API + 前端接线 + 路由注册。

## 五轴摘要

| 轴 | 状态 |
|---|---|
| 正确性 | ✅ MEMORY.md 分流严密；handleCompile 故意 404 由测试锁 |
| 可读性 | ✅ 模块边界清晰；identity_routes 镜像 memory_routes |
| 架构 | ✅ operations 注入模式与既有约定一致；常量收敛到 catalog |
| 安全 | ✅ `_resolve` 双闸覆盖字符串层、符号链接、Windows 分隔符、URL 编码 |
| 性能 | ✅ 无 N+1、无热路径同步阻塞 |

## Important（3）

1. [nanobot/webui/identity_api.py:65] lifecycle=None 时返回 500，建议降为 503（与 `_null_identity_operations` 兜底语义对齐）
2. [webui/src/components/settings/identity/IdentityView.tsx:285] `handleCompile` 当前 404 是设计意图，但**未来编译功能落地时**前端必须显式处理 `not_enabled`/`ok` 分支——否则变成静默成功
3. [nanobot/identity/store.py:74-80] `_resolve` 双闸逻辑严密，无漏洞（review 确认）

## Suggestion（3，可延后）

1. [identity_api.py:100-108] `identity_compile` docstring 显式标注 "DO NOT wire as WS action until a real implementation exists"
2. [identity/store.py:130-137] `isinstance(content, str)` 校验移到 `_resolve` 之前，更线性
3. [SettingsPage.tsx:612-626] 4 处 `channels || identity` 条件重复，建议提取 `widePaneSections` 常量

## Nit（3，可延后）

- `_resolve` 条件表达式可改写更显式
- `build_identity_operations` 与 `build_memory_operations` 各自 `MemoryServices.for_workspace(...)` —— 同进程同 workspace 是 singleton，无害
- `IdentityFileRow` 的 badge fallback 嵌套三元式可提取 `BADGE_FALLBACKS` 常量

## 证据

- 已逐文件读：catalog/store/api/routes/lifecycle/context/api.ts/types.ts/IdentityView 等
- 已核对 WS action 名 `identity.file.save` / `identity.reload` 与 `_WEBUI_MUTATION_PATHS` 逐字一致
- 已核对 en/zh-CN 两端 `settings.identity` 各 31 键、顺序一致
- 已核对 `LIFECYCLE_OWNED_FILES` 在 catalog.py 唯一定义
- 已核对 `identity.compile` 不登记由 `test_compile_is_deliberately_not_a_ws_action` 锁死
- pytest tests/identity/ + tests/webui/test_identity_wiring.py + tests/webui/test_identity_routes.py → 129 passed / 1 skipped
- ruff check 本次新文件 → 0 errors（lifecycle.py:333 N806 在基线）

## 决定

本次 Important 项**不修**：
1. lifecycle=None 路径实际不可达（gateway 必绑），降为 503 是「未来防御」而非当前 bug
2. handleCompile 的未来风险不是本次范围
3. `_resolve` 已被确认严密

Suggestion 与 Nit 留作 follow-up PR，本批不阻塞。

## Next

- 用户确认后开 MR（`git-xywh`）
- follow-up：把 Important #1 与 3 个 Suggestion 收口到一个独立 commit
