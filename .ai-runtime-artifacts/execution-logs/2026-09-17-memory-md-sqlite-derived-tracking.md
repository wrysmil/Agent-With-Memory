# WU Tracking — 2026-09-17 MEMORY.md SQLite 派生 + Dream 按钮改造

## 元数据

- **创建日期**：2026-09-17
- **Spec**：`.ai-runtime-artifacts/specs/2026-09-17-memory-md-sqlite-derived-and-dream-button-clarification-spec.md`
- **Plan**：`.ai-runtime-artifacts/plans/2026-09-17-memory-md-sqlite-derived-and-dream-button-plan.md`
- **Worktree**：`worktree-wt-memory-md-sqlite-derived`
- **分支**：`worktree-wt-memory-md-sqlite-derived`（from `feature/memory-system`）

## WU 状态总表

| WU ID | 范围 | agent_role | 状态 | 完成时间 | 备注 |
|---|---|---|---|---|---|
| WU-1 | `MemoryLifecycle` 类 + `content_hash_legacy` | coder | **done** | 2026-09-17 | chain A 根 |
| WU-2 | `list_memories` 加 scope/min_importance | coder | **done** | 2026-09-17 | chain A，独立 |
| WU-3 | 三处 memory_api 加钩子 | coder | **done** | 2026-09-18 | 依赖 WU-1 |
| WU-4 | `refresh-md` 路由注册 | coder | **done** | 2026-09-18 | 依赖 WU-1 |
| WU-5 | stats_payload 加 memory_md 字段 | coder | **done** | 2026-09-18 | 依赖 WU-1 |
| WU-6 | 前端 MemoryMdCard + 弹窗 | coder | **done** | 2026-09-18 | 依赖 WU-4, WU-5 |
| WU-7 | Dream 工具白名单改 editable_files | coder | **done** | 2026-09-17 | chain B 根 |
| WU-8 | dream.md 改写 | implementer | **done** | 2026-09-17 | 依赖 WU-7 |
| WU-9 | cmd_dream 成功消息文案 | implementer | **done** | 2026-09-18 | — |
| WU-10 | 集成测试 | test-engineer | pending | — | 依赖全部 |
| WU-11 | i18n 全语种 | implementer | pending | — | 依赖 WU-8, WU-9 |

## 当前批次

### Round 1（并行）

- [x] WU-1（coder）：`MemoryLifecycle` 类 + `content_hash_legacy`
- [x] WU-2（coder）：`list_memories` 加 scope/min_importance
- [x] WU-7（coder）：Dream 工具白名单
- [x] WU-8（implementer）：dream.md 改写

## 执行日志

### WU-1（2026-09-17 完成）

**范围**：`MemoryLifecycle` 核心类 + `content_hash_legacy` 函数

**变更文件**：
- `nanobot/memory/lifecycle.py` — 新建（约 320 行）
- `nanobot/memory/filters.py` — 新增 `content_hash_legacy()` 函数
- `tests/memory/test_lifecycle.py` — 新建，30 个测试用例

**关键设计决策**：

1. **`connect()` 用法**：`database.connect()` 是 context manager，必须用 `with ... as conn`。
2. **WU-2 fallback**：先用 WU-2 扩展签名（`scope` + `min_importance`），TypeError 时退化为 Python 端过滤。
3. **去重关键字**：regex 解析 heading 时需去掉 `## ` 前缀再匹配 keyword（`"## 规则".lower()` ≠ `"规则"`）。
4. **截断预算**：truncation marker 本身长度需计入预算，否则结果会超限。
5. **trigger 保留**：async `schedule_refresh_md` 预设为 `"auto"`，sync `refresh_memory_md_sync` 只在非 auto 时覆盖为 `"manual"`。

**测试覆盖**（30 个测试，全部 PASS）：

| 测试类 | 用例数 | 覆盖点 |
|---|---|---|
| `TestContentHashLegacy` | 4 | 同内容同 hash、不同内容不同 hash、空白strip、hex格式 |
| `TestRenderMemoryMd` | 6 | 空列表、单类型、6类全量、top-K、重要性排序、去重 |
| `TestRefreshMemoryMdSync` | 5 | 空库跳过、user scope、min_importance、文件写入、stats更新 |
| `TestTruncateMemoryMd` | 6 | 不超限不变、超限缩减、规则优先、规则截断+marker、普通跳过、header保留 |
| `TestSafeWriteWithBackup` | 3 | 写入、备份创建、失败回滚 |
| `TestScheduleRefreshMd` | 3 | 去抖跳过、去抖窗口后触发、trigger=auto |
| `TestForWorkspace` | 2 | 单例复用、不同workspace不同实例 |
| `TestEndToEndRender` | 1 | 端到端渲染格式验证 |

**验证命令**：
```bash
uv run --no-sync pytest tests/memory/test_lifecycle.py -v
# 结果：30 passed in 6.88s

uv run --no-sync pytest tests/memory/test_filters.py -v
# 结果：55 passed in 0.37s
```

### WU-2（2026-09-17 完成）

**范围**：`list_memories` 加 scope/min_importance 过滤参数

**变更文件**：
- `nanobot/memory/repository.py` — 函数签名扩展 + WHERE 子句追加
- `tests/memory/test_repository_scope_filter.py` — 新建，覆盖 11 个测试用例

**实现摘要**：
- 新增 `scope: str | None = None` 参数 → `WHERE scope = ?`
- 新增 `min_importance: float | None = None` 参数 → `WHERE importance_score >= ?`
- 默认 `None` 保证现有调用方行为不变（`memory_routes.py` 无需改动）

**测试覆盖**：
| 用例 | 状态 |
|---|---|
| scope="user" 过滤 | PASS |
| scope="global" 过滤 | PASS |
| min_importance=0.5 过滤 | PASS |
| min_importance=0.4 边界值 | PASS |
| scope + min_importance 组合（user） | PASS |
| scope + min_importance 组合（global） | PASS |
| 默认 None 回归 | PASS |
| order_by="importance" 与 min_importance 正交 | PASS |
| EXPLAIN: idx_memories_importance 命中 | PASS |
| EXPLAIN: idx_memories_owner/SCAN 计划 | PASS |
| EXPLAIN: 组合查询计划 | PASS |

**验证命令**：
```bash
uv run --no-sync pytest tests/memory/test_repository_scope_filter.py tests/memory/test_repository.py -v
# 结果：24 passed in 4.51s
```

### WU-7（2026-09-17 完成）

**范围**：Dream 工具白名单改 editable_files

**变更文件**：
- `nanobot/agent/memory.py` — `__init__` 新增 `draft_file`，`build_dream_tools()` 改白名单
- `tests/agent/test_dream.py` — 扩展，新增 3 个测试

**实现摘要**：
- `__init__` 新增 `self.draft_file = self.memory_dir / "MEMORY.md.draft"`
- `build_dream_tools()` 的 `editable_files` 改为 `[self.soul_file, self.user_file, self.draft_file]`
- `memory_file` 从白名单移除（Dream 无法直接写 MEMORY.md）

**测试覆盖**：
| 用例 | 状态 |
|---|---|
| `test_dream_tools_excludes_memory_file` 白名单验证 | PASS |
| `test_draft_file_path` 路径验证 | PASS |
| `test_dream_can_edit_canonical_files_except_memory` 写入行为验证 | PASS |
| 49 个 Dream 测试全部 | PASS |

**验证命令**：
```bash
uv run --no-sync pytest tests/agent/test_dream.py -v
# 结果：49 passed in 4.55s
```

### WU-8（2026-09-17 完成）

**范围**：dream.md 重写（约 109 行 → 130 行）

**变更文件**：
- `nanobot/templates/agent/dream.md` — 重写
- `tests/agent/test_dream.py` — 扩展，新增 1 个测试

**实现摘要**：
- File routing 表：删除 `MEMORY.md` 行，新增 `MEMORY.md.draft` 行和被划掉的 `MEMORY.md` 禁止写入行
- 新增 `## MEMORY.md.draft generation rules` 段（5 条规则）
- Verification 段末尾追加 `**禁止声称** "已写入 MEMORY.md"（你没权限也没必要）。`
- 保留 SOUL.md/USER.md/SKILL.md 的 routing 逻辑、MECE enforcement、History attribute tags 等全文

**测试覆盖**：
| 用例 | 状态 |
|---|---|
| `test_dream_prompt_excludes_memory_file_routing` | PASS |
| 49 个 Dream 测试全部 | PASS |

**验证命令**：
```bash
uv run --no-sync pytest tests/agent/test_dream.py -v
# 结果：49 passed in 5.23s
```

### WU-9（2026-09-18 完成）

**范围**：`cmd_dream` 成功消息文案改写

**变更文件**：
- `nanobot/command/builtin.py` — 第 501 行

**实现摘要**：
- 成功消息从 `f"Dream completed in {elapsed:.1f}s."` 改为多行文案，明确告知草稿路径与真值来源
- 失败分支、无变更分支、异常分支文案均未改动

**验证命令**：
```bash
grep -n "Dream 草稿已生成" nanobot/command/builtin.py
# 502:                        f"Dream 草稿已生成：memory/MEMORY.md.draft "
grep -n "MEMORY.md 真值由记忆系统" nanobot/command/builtin.py
# 503:                        f"（{elapsed:.1f}s）。MEMORY.md 真值由记忆系统从 SQLite 自动生成，"
```

### WU-5（2026-09-18 完成）

**范围**：`stats_payload` 加 `memory_md` 字段

**变更文件**：
- `nanobot/webui/memory_api.py` — 新增 5 个辅助函数 + 两处 return dict 扩展
- `tests/webui/test_memory_api.py` — 新增 2 个测试用例

**实现摘要**：
- 添加 `import time`（文件顶部）
- 新增 5 个辅助函数：`_get_last_refresh_at`、`_get_last_refresh_trigger`、`_draft_exists`、`_draft_age_seconds`、`_current_memory_md_chars`
- `stats_payload` 两个 return 分支（vector_runtime 为 None 和非 None）均加入 `memory_md` 子字段
- 字段集恒定：无 `MemoryLifecycle` 单例时各字段返回 `None`/`False`/`0`，不影响现有结构
- 测试覆盖：有单例结构字段存在性 + 无单例时降级行为

**验证命令**：
```bash
uv run --no-sync pytest tests/webui/test_memory_api.py -v
# 20 passed
```

### WU-4（2026-09-18 完成）

**范围**：`POST /api/settings/memory/refresh-md` 路由注册

**变更文件**：
- `nanobot/webui/memory_api.py` — 新增顶层函数 `refresh_memory_md()`
- `nanobot/webui/memory_routes.py` — dataclass 字段 + `MEMORY_ACTION_NAMES` + `dispatch()` 分支
- `nanobot/webui/settings_routes.py` — `_SYSTEM_ROUTES` + `_MEMORY_MUTATION_PATHS` + `_null_memory_operations()`
- `nanobot/webui/gateway_services.py` — `build_memory_operations()` 注入
- `tests/webui/test_refresh_md_route.py` — 新建，10 个测试用例
- `tests/webui/test_memory_routes.py` — operations fixture 补字段

**实现摘要**：
- `memory_api.refresh_memory_md(services)` 调用 `MemoryLifecycle.for_workspace().refresh_memory_md_sync()`
- action name `"memory-refresh-md"` 在 3 处路由表保持一致
- `_null_memory_operations()` stub 返回 503 unavailable
- `build_memory_operations()` 通过 `partial(memory_api.refresh_memory_md, services)` 注入

**测试覆盖**：
| 用例 | 状态 |
|---|---|
| action name 在 MEMORY_ACTION_NAMES | PASS |
| 函数调用 Lifecycle（mock） | PASS |
| dispatch 路径走通 | PASS |
| null_operations 返回 503 | PASS |
| _SYSTEM_ROUTES 含路由 | PASS |
| _MEMORY_MUTATION_PATHS 含路由 | PASS |
| _SETTINGS_MUTATION_PATHS 含路由 | PASS |
| dataclass 字段存在 | PASS |
| gateway_services 注入 | PASS |
| 22 个 memory_routes 测试全部 | PASS |

**验证命令**：
```bash
uv run --no-sync pytest tests/webui/test_refresh_md_route.py -v
# 10 passed

uv run --no-sync pytest tests/webui/test_memory_routes.py tests/webui/test_refresh_md_route.py -v
# 22 passed
```

### WU-3（2026-09-18 完成）

**范围**：三处 memory_api CRUD 操作后触发 MEMORY.md 派生

**变更文件**：
- `nanobot/webui/memory_api.py` — 新增 `_refresh_memory_md_after_mutation` 函数 + 三处调用
- `tests/memory/test_memory_api_hooks.py` — 新建，11 个测试用例

**实现摘要**：
- 文件顶部新增 `import logging`
- 新增辅助函数 `_refresh_memory_md_after_mutation(workspace_id, services)`：
  - 懒导入 `MemoryLifecycle`
  - 调用 `lifecycle.refresh_memory_md_sync(workspace_id)`
  - 异常时只记 WARNING 日志，不抛给调用方
- 三处调用位置：
  1. `create_memory`：在 `index_memory_best_effort(memory)` **之后**
  2. `update_memory`：在 `fetch_and_index(services, memory_id)` **之后**
  3. `delete_memory`：在 `remove_memory_best_effort(memory_id)` **之后**

**重要约束满足**：
- SQLite 写入失败时不会触发派生（调用在 `try` 块成功路径之后）
- 派生失败只记 WARNING 日志，不破坏 mutation 返回值
- 函数签名和返回类型未改变
- `index_memory_best_effort` / `fetch_and_index` / `remove_memory_best_effort` 逻辑未改动

**测试覆盖**（11 个测试，全部 PASS）：

| 测试类 | 用例 | 状态 |
|---|---|---|
| `TestCreateMemoryTriggersRefresh` | create 成功后调用 refresh | PASS |
| `TestCreateMemoryTriggersRefresh` | 内容规范化后调用 refresh | PASS |
| `TestUpdateMemoryTriggersRefresh` | update 成功后调用 refresh | PASS |
| `TestUpdateMemoryTriggersRefresh` | 404 不触发 refresh | PASS |
| `TestDeleteMemoryTriggersRefresh` | delete 成功后调用 refresh | PASS |
| `TestDeleteMemoryTriggersRefresh` | 404 不触发 refresh | PASS |
| `TestRefreshFailureDoesNotPropagate` | create 后 refresh 失败不抛 | PASS |
| `TestRefreshFailureDoesNotPropagate` | update 后 refresh 失败不抛 | PASS |
| `TestRefreshFailureDoesNotPropagate` | delete 后 refresh 失败不抛 | PASS |
| `TestRefreshOnlyOnSuccess` | create 确认写库 | PASS |
| `TestRefreshOnlyOnSuccess` | refresh 调用时机验证 | PASS |

**验证命令**：
```bash
uv run --no-sync pytest tests/memory/test_memory_api_hooks.py -v
# 11 passed in 2.40s

uv run ruff check nanobot/webui/memory_api.py tests/memory/test_memory_api_hooks.py
# All checks passed!
```

### WU-6（2026-09-18 完成）

**范围**：前端 MemoryMdCard + Modal 组件 + 后端读取 API

**变更文件**：
- `nanobot/webui/memory_api.py` — 新增 `_read_memory_md_content()`、`_read_draft_content()`、`get_memory_md_content()`
- `nanobot/webui/memory_routes.py` — dataclass 字段 + `MEMORY_ACTION_NAMES` + `dispatch()` 分支
- `nanobot/webui/settings_routes.py` — `_SYSTEM_ROUTES` + `_null_memory_operations()` stub
- `nanobot/webui/gateway_services.py` — `build_memory_operations()` 注入
- `webui/src/components/settings/memory/MemoryMdCard.tsx` — 新增 `onViewMemoryMd`/`onViewDraftDiff` props + 两个按钮
- `webui/src/components/settings/memory/MemoryMdViewerModal.tsx` — 实现从后端读取 MEMORY.md 内容
- `webui/src/components/settings/memory/MemoryDraftDiffModal.tsx` — 实现从后端读取 draft 和 MEMORY.md 内容
- `webui/src/components/settings/memory/MemorySection.tsx` — 整合 Modal，添加 state
- `webui/src/i18n/locales/zh-CN/common.json` — 新增 mdButtonView/mdButtonDiff/mdLoading keys
- `webui/src/i18n/locales/en/common.json` — 新增对应的英文字段
- `tests/webui/test_refresh_md_route.py` — 新增 5 个 WU-6 测试用例
- `tests/webui/test_memory_routes.py` — operations fixture 补字段

**实现摘要**：
- 后端新增 `get_memory_md_content(services)` → 返回 `{memory_md, draft, memory_md_exists, draft_exists}`
- 前端三个 Modal 从 `client.requestMutation("memory-get-md-content", {})` 获取内容
- MemoryMdCard 新增「查看 MEMORY.md」和「对比草稿」两个按钮（draft 按钮仅在 `draft_exists=true` 时显示）
- Modal 通过 props 控制开关，state 提升到 MemorySection

**测试覆盖**：
| 用例 | 状态 |
|---|---|
| `memory-get-md-content` 在 MEMORY_ACTION_NAMES | PASS |
| `memory-get-md-content` dispatch 路径 | PASS |
| `/api/settings/memory/memory-md/content` 在 _SYSTEM_ROUTES | PASS |
| `get_memory_md_content` 在 dataclass 字段 | PASS |
| `build_memory_operations` 注入 | PASS |
| 148 个相关测试全部 | PASS |

**验证命令**：
```bash
uv run --no-sync pytest tests/webui/test_refresh_md_route.py -v
# 15 passed in 1.67s

uv run --no-sync pytest tests/memory/ tests/webui/ tests/agent/test_dream.py -v
# 148 passed in 18.08s
```

**（各 WU 完成时在此追加）**

