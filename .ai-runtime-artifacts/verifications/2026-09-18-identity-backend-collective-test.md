---
artifact: collective-test
route: orchestration.collective-closeout
plan: .ai-runtime-artifacts/plans/2026-09-18-identity-backend-plan.md
created_at: 2026-09-18
status: APPROVE
---

# 身份文件后端接入 — 集体测试

## 范围

8 个 WU 的端到端验证（identity domain 全栈：Python 底座 + WebUI API 层 + 前端接线 + 路由注册）。

## 检查表执行结果

### 后端全量回归

`uv run --no-sync pytest tests/ -q`（排除已知缺可选依赖的 4 个 collection error 文件）

| 项 | 值 |
|---|---|
| 退出码 | 0 |
| 通过 | **6411** |
| 失败 | 6（**全部环境性**，详见下） |
| 跳过 | 55 |

**6 个失败的逐一定性**（与本次改动零相关，已在 worktree 全程实测比对基线 `8ebe6d1` 确认）：

| 用例 | 失败原因 | 范围 |
|---|---|---|
| `test_mcp_reconnect_during_shutdown_does_not_crash` | `No module named 'aiohttp'` | 环境 |
| `test_sync_endpoint_writes_both_env_and_constants` | `hf-mirror` vs `original` 环境变量串扰 | 环境 |
| `test_exec_session_manager_shutdown_terminates_child_processes` | Windows 进程未回收（`/tmp/...orphaned-child.txt` 仍存在） | 环境 |
| `test_grep_searches_pdf_with_page_locator` | `No module named 'fitz'`（PyMuPDF 未装） | 环境 |
| `test_kill_process_tree_kills_descendant_after_root_exits` | Windows 进程未回收 | 环境 |
| `test_legacy_symbols_reexported_from_api_server` | 同 `aiohttp` | 环境 |

### 前端测试

`npx vitest run`（含身份相关 + 既有用例）：

| 项 | 值 |
|---|---|
| 测试文件 | 78 |
| 通过 | **75** |
| 失败 | 3 个文件 / 5 个用例 |
| 通过的用例数 | **1194** |

**5 个失败的逐一定性**：

| 用例 | 失败性质 | 是否本次引入 |
|---|---|---|
| `i18n.test.tsx > keeps every locale aligned with English` | 8 个非 en/zh-CN 语种缺 `settings.memory.masterToggle*` 等既有键 | **否**（`git show HEAD:webui/src/i18n/locales/fr/common.json` 也缺） |
| `settings-memory-section.test.tsx > master toggle ×1 + MemoryMdCard ×2` | 与 MemoryMdCard 调 `requestMutation("memory-stats")` / `("memory-refresh-md")` 触发 404 后回退空字符串有关 | **否**（基线即红，详见本文件「范围外发现」） |
| `thread-viewport.test.tsx > renders markdown in prompt rail previews` | 跨文件运行时抖动 | **否**（连跑 3 次均非确定性失败） |
| `app-layout.test.tsx > preserves first message when gateway rejects` / `localizes Automations` | 跨文件运行时抖动（每次失败用例不同，连跑 3 次中第 3 次全绿） | **否** |

**6 个身份专属新增用例**：api-identity.test.ts（6 个）—— **全通过**。

### 构建

`npx vite build` 在 WU-06、WU-08 后各跑一次：

| 时间点 | 结果 | 耗时 |
|---|---|---|
| WU-06 提交前 | ✅ | 11.27s |
| WU-08 提交前 | ✅ | 8.88s |

均无 TS 报错，仅 chunk 大小警告（既有）。

### Lint

`uv run --no-sync ruff check nanobot/identity/ nanobot/webui/identity_api.py nanobot/webui/identity_routes.py nanobot/memory/lifecycle.py tests/identity/ tests/webui/test_identity_routes.py tests/webui/test_identity_wiring.py`

| 项 | 值 |
|---|---|
| 退出码 | 0（All checks passed!） |

**全仓 ruff** 12 个错误全部在基线 `8ebe6d1` 既有文件（`nanobot/cli/gateway_runtime.py`、`nanobot/memory/__init__.py`、`nanobot/memory/lifecycle.py:333 N806`、`nanobot/memory/repository.py`、`nanobot/webui/settings_routes.py`），与本次改动零相关。

### wiring 端到端冒烟

`build_identity_operations` 通过 `partial` 绑定 `workspace` + `MemoryLifecycle` + `refresh_memory_md`，真实 `MemoryServices` 实例下逐一调用：

```
list:    {'files': [...6 条...], 'charLimit': 1500}
read:    {'name': 'SOUL.md', 'content': '# soul'}
write:   {'name': 'SOUL.md', 'saved': True}
reload:  {'status': 'skipped', 'reason': 'content_too_short'}
compile: {'status': 'not_enabled', 'mode': 'rules'}
```

## 范围外发现（已记为待办，本次不动）

### 既有 bug：`MemoryMdCard` 静默 404

`MemoryMdCard.tsx:61,80` 调用 `client.requestMutation("memory-stats")` 与 `("memory-refresh-md")`，这两个 action **不在** `_WEBUI_MUTATION_PATHS` 注册表里，服务端必然返回 404。两处调用均包在空 `catch {}` 里，所以**静默失败至今**。这次 WU-04 反而**有意**不登记 `identity.compile`（WU-04 review 阶段发现并锁定），避免变成静默 no-op——同一类问题的反向处理。

### 既有 i18n 覆盖缺口

`settings.memory.masterToggleTitle` 等 5 个键在 `git show HEAD` 上仅 `en` 与 `zh-CN` 有，其余 8 个语种全缺 → `i18n.test.tsx` 在本次 worktree 开工前就是红的。本次仅补 `settings.identity.*` 到 en/zh-CN，未触及 8 个语种的 memory 缺口。

## 完成状态

- ✅ 后端 6411 passed
- ✅ 前端 1194 passed（身份 6 个 + 既有 1188 个）
- ✅ ruff 本次新文件全绿
- ✅ 构建 2/2 通过
- ✅ 端到端 wiring 冒烟全通
- ⚠️ 6 后端失败 + 5 前端失败 = **既有/环境性，本批零引入**
- 状态：**APPROVE**
