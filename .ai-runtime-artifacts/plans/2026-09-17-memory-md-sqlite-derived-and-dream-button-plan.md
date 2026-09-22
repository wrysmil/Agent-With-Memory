# MEMORY.md 改由 SQLite 派生 + Dream 按钮歧义改造 — 实施计划

> 计划日期：2026-09-17
> 上游 spec：`.ai-runtime-artifacts/specs/2026-09-17-memory-md-sqlite-derived-and-dream-button-clarification-spec.md`（464 行）
> 上游调研：`research/2026-09-16-openakita-identity-config-and-memory-md-research.md`、`research/2026-09-17-openakita-recall-parity-and-fts5-tokenizer-research.md`
> 目标：把 MEMORY.md 真值来源从"Dream LLM 直改"迁到"SQLite 派生 + Dream 降级为草稿"，消除前端「执行 Dream」按钮歧义，11 个 WU 按两条并行依赖链分批落地。

---

## 上下文（Context）

### 为什么做这件事

两件事必须同批做：

1. **MEMORY.md 由 SQLite 派生**：openakita 把 SQLite `memories` 表当作唯一真相源，MEMORY.md 是程序化派生物（`openakita/memory/lifecycle.py:1384-1458`）。nanobot 当前 MEMORY.md 由 Dream 让 LLM 直接 `edit_file`/`write_file` 改写（`nanobot/agent/memory.py:577-617`），不可复现、不进向量检索、不被 `search_memories()` 查到。

2. **Dream 按钮歧义**：WebUI 命令面板的「执行 Dream」按钮（`webui/src/i18n/locales/zh-CN/common.json:1395-1398`）调用 `/dream` slash command，让 LLM 用 `ReadFile/EditFile/WriteFile/ApplyPatch` 工具改 `MEMORY.md/SOUL.md/USER.md/SKILL.md` 四份文件。用户看到「Dream」两字以为点了"记忆系统自动整理"，实际 LLM 在改文件，**且与 SQLite 记忆系统完全无交集**（grep `MemoryDatabase` in `nanobot/agent/memory.py` 零命中）。

### 推荐方案（B 档，spec §2.1）

**Dream 降级为生成 `MEMORY.md.draft` 草稿 + SQLite 派生正式生效 + 前端加「立即刷新」按钮。**

不删 Dream（避免破坏向后兼容：周期 2h 任务、已写出的 `SKILL.md`）；但 Dream **不再写 MEMORY.md 真值**，改写 `MEMORY.md.draft`；真值由新增的 `MemoryLifecycle.refresh_memory_md()` 从 SQLite `memories` 表派生。

### 预期产出

- 用户点「执行 Dream」按钮 → LLM 改 `MEMORY.md.draft`（可见），不再覆盖真值
- 用户点 Settings → Memory 的「立即刷新 MEMORY.md」按钮 → 立即从 SQLite 重建 MEMORY.md
- SQLite `memories` 表写入后 60s 内自动去抖触发 MEMORY.md 重建
- `MEMORY.md` 注入 system prompt 的内容（`nanobot/agent/context.py:235`）由"LLM 直改产物"变为"SQLite 确定性派生镜像"

---

## 关键架构决策

### 决策 1：新增 `MemoryLifecycle` 类，独立于 `MemoryStore`

**不**往 `MemoryStore` 上加方法。新增 `nanobot/memory/lifecycle.py`：

```python
class MemoryLifecycle:
    """派生 MEMORY.md 真值（从 SQLite memories 表重建）。"""

    _instances: dict[str, "MemoryLifecycle"] = {}  # workspace_id 单例
    _refresh_tasks: dict[str, asyncio.Task] = {}
    _last_refresh_at: dict[str, float] = {}  # monotonic, 用于去抖
    _last_refresh_iso: dict[str, str] = {}  # ISO8601 UTC, 用于 stats
    _derive_lock = threading.Lock()  # 写盘互斥（不复用 MemoryStore._append_lock）
```

**理由**：
- `MemoryStore` 是纯文件 I/O 层（`nanobot/agent/memory.py:61` docstring 自述 "Pure file I/O layer"），与 SQLite 零耦合
- `MemoryLifecycle` 走 `services.database.connect()` + `list_memories(...)` 拿数据，自带 `threading.Lock` 保证派生写盘不与 Dream 抢 `_append_lock`
- 跨 workspace 单例，state 按 `workspace_id` 索引

### 决策 2：派生是同步函数，调度走 `asyncio.to_thread`

`memory_api.create_memory/update_memory/delete_memory` 全是 `def`（同步），上层路由用 `asyncio.to_thread` 包（`settings_routes.py:377-382`）。

```python
# nanobot/memory/lifecycle.py
def refresh_memory_md_sync(self, workspace_id: str) -> dict[str, Any]:
    """同步派生 MEMORY.md。线程安全，可被 asyncio.to_thread 调用。"""
    with self._derive_lock:
        memories = self._query_candidates(workspace_id)
        content = self._render_memory_md(memories)
        if len(content.strip()) < 10:
            logger.warning("refresh_memory_md: content too short, skip")
            return {"status": "skipped", "reason": "content_too_short"}
        self._safe_write_with_backup(self.memory_file, content)
        self._last_refresh_iso[workspace_id] = _iso8601_now()
        return {"status": "ok", "chars": len(content), "trigger": "manual"}

async def schedule_refresh_md(self, workspace_id: str, debounce_seconds: float = 60.0) -> None:
    """60s 去抖 + asyncio.create_task 调度。"""
    now = time.monotonic()
    last = self._last_refresh_at.get(workspace_id, 0.0)
    if now - last < debounce_seconds:
        return
    self._last_refresh_at[workspace_id] = now
    old = self._refresh_tasks.pop(workspace_id, None)
    if old and not old.done():
        old.cancel()
    self._refresh_tasks[workspace_id] = asyncio.create_task(
        asyncio.to_thread(self.refresh_memory_md_sync, workspace_id)
    )
```

### 决策 3：Dream 工具白名单硬约束 MEMORY.md

`MemoryStore.build_dream_tools()`（`memory.py:592-617`）的 `extra_write_allowed_files` 白名单：

```diff
- editable_files = [self.memory_file, self.soul_file, self.user_file]
+ editable_files = [self.soul_file, self.user_file, self.draft_file]  # 移除 memory_file
+ self.draft_file = self.memory_dir / "MEMORY.md.draft"
```

即使 Dream prompt 误说"写 MEMORY.md"，工具层物理拒绝。

### 决策 4：新增 `content_hash_legacy(content: str) -> str` 单参版本

`nanobot/memory/filters.py:140` 的 `compute_content_hash(content, subject, predicate)` 是 3 参数。openakita 用 `SHA1(content)` 单参数做去重。

```python
# nanobot/memory/filters.py
def content_hash_legacy(content: str) -> str:
    """SHA1 hex digest of stripped content. Used by lifecycle.refresh_memory_md for dedup."""
    return hashlib.sha1(content.strip().encode("utf-8")).hexdigest()
```

### 决策 5：WebUI 走 `requestMutation`，不走 `fetch`

所有 mutation 都走 `client.requestMutation(action, payload)`（`nanobot-client.ts:880-935`），无 fetch 先例。

```typescript
const res = await client.requestMutation<{status: string, chars?: number}>(
  "memory-refresh-md",
  {},
  30_000,
);
```

**三处同步维护**（无类型层强制）：
1. 前端 `.tsx` 调用字符串
2. 后端 `_SYSTEM_ROUTES` / `_MEMORY_MUTATION_PATHS` 注册（`settings_routes.py:148-178`）
3. 测试 mock（`webui/src/tests/settings-test-utils.tsx:9`）

---

## WU 拆分（11 个，按两条并行依赖链）

### 依赖图

```
                                    ┌── WU-2 (repo: scope/min_importance)
                                    │
WU-1 (lifecycle 核心 + filters) ────┼── WU-3 (钩子: create/update/delete)
                                    │
                                    ├── WU-4 (路由: refresh-md)
                                    │
                                    ├── WU-5 (stats: memory_md.*)
                                    │
                                    └───┬── WU-6 (前端 MemoryMdCard + 弹窗)
                                        │
                                        └── WU-10 (集成测试)

WU-7 (Dream 白名单) ──→ WU-8 (dream.md 改写) ──┬── WU-9 (成功消息文案)
                                                │
                                                └── WU-11 (i18n 全语种)
```

**两条独立链可并行委派**。链 A = refresh 路径；链 B = Dream 降级路径。

---

### WU-1（chain A 根）：`MemoryLifecycle` 类 + `content_hash_legacy`

| 字段 | 内容 |
|---|---|
| **范围** | 新增 `nanobot/memory/lifecycle.py`（约 250-300 行）+ 在 `nanobot/memory/filters.py` 加 `content_hash_legacy()` |
| **核心 API** | `MemoryLifecycle.refresh_memory_md_sync(workspace_id)` / `schedule_refresh_md(workspace_id)` / `_safe_write_with_backup()` / `_render_memory_md()` / `truncate_memory_md()` |
| **关键实现** | 1. `refresh_memory_md_sync` 流程：`MemoryServices` 拿 workspace → `conn = services.database.connect()` → `list_memories(scope="user", min_importance=0.5, limit=200, order_by="importance")` → Python 端按 6 类分组 + `content_hash_legacy` 去重 + 每类 top-4 + 1500 字符封顶 → 走 `_safe_write_with_backup` 写 `memory/MEMORY.md`（先 `.bak`）<br>2. 类型→中文标签映射硬编码：`fact→事实, preference→偏好, skill→技能, error→教训, rule→规则, experience→经验`<br>3. 截断逻辑参考 `openakita/memory/types.py:94-143` `truncate_memory_md`：段落优先级（`## 规则` 类优先），正则 `^## (.+)` 提取标题，`_RULE_SECTION_KEYWORDS = {"重要规则", "规则", "rules", "行为规则", "用户规则"}`<br>4. `schedule_refresh_md` 实现 60s 去抖 + `asyncio.create_task(asyncio.to_thread(...))` |
| **不变量** | 1. 内容 < 10 字符跳过（防清空）<br>2. 写失败 → 从 `.bak` 回滚<br>3. 不抛异常到调用方（异常只记日志）<br>4. `MemoryStore` 一行不动 |
| **单测** | `tests/memory/test_lifecycle.py`：空库 / 全是 rule / 全是 fact / 混合 / 超 1500 字符 / < 10 字符 / 写失败回滚 / 去抖窗口 |
| **依赖** | 无（基础模块） |
| **agent_role** | coder |

---

### WU-2（chain A）：`repository.list_memories` 扩展 `scope` / `min_importance`

| 字段 | 内容 |
|---|---|
| **范围** | `nanobot/memory/repository.py:107-135` |
| **核心改动** | `list_memories` 加两个 kw-only 参数：`scope: str \| None = None` + `min_importance: float \| None = None`；WHERE 子句各加一行 |
| **索引验证** | 加 `tests/memory/test_repository_scope_filter.py`：3 个用例 + `EXPLAIN QUERY PLAN` 验证 `idx_memories_importance` + `idx_memories_owner` 仍被命中 |
| **依赖** | 无（独立模块） |
| **agent_role** | coder |

---

### WU-3（chain A）：在 `create_memory/update_memory/delete_memory` 加钩子

| 字段 | 内容 |
|---|---|
| **范围** | `nanobot/webui/memory_api.py:375-492` 三处加钩子 |
| **核心改动** | 每个 SQLite commit 块结束后加 `refresh_memory_md_sync` 同步调用：<br>1. `create_memory`：SQLite `add_memory` 成功 + `index_memory_best_effort` 之后<br>2. `update_memory`：SQLite `_repo_update_memory` 成功 + `fetch_and_index` 之后<br>3. `delete_memory`：SQLite `_repo_delete_memory` 之后 |
| **关键实现** | 本次用**直接同步调用** `refresh_memory_md_sync`（避免跨线程 asyncio 桥接复杂度）。60s 去抖在 `MemoryLifecycle._last_refresh_at` 层面做 |
| **单测** | `tests/memory/test_memory_api_hooks.py`：mock `refresh_memory_md_sync`，验证三处函数 commit 后都被调用 |
| **依赖** | WU-1 |
| **agent_role** | coder |

---

### WU-4（chain A）：`POST /api/settings/memory/refresh-md` 路由

| 字段 | 内容 |
|---|---|
| **范围** | `nanobot/webui/memory_routes.py:30-190` + `nanobot/webui/settings_routes.py:148-178, 242-265` |
| **核心改动** | 1. `MemorySettingsOperations` dataclass 加 `refresh_memory_md` 字段<br>2. `MEMORY_ACTION_NAMES` 加 `"memory-refresh-md"`<br>3. `dispatch()` 加 `if action == "memory-refresh-md"` 分支<br>4. `_SYSTEM_ROUTES` 加 `"/api/settings/memory/refresh-md"`<br>5. `_MEMORY_MUTATION_PATHS` 同步加一项<br>6. `_null_memory_operations()` stub 加 fallback |
| **单测** | `tests/webui/test_refresh_md_route.py` |
| **依赖** | WU-1 |
| **agent_role** | coder |

---

### WU-5（chain A）：`stats_payload.memory_md.*` 字段

| 字段 | 内容 |
|---|---|
| **范围** | `nanobot/webui/memory_api.py:238-292` `stats_payload` 函数 |
| **核心改动** | 返回 dict 新增 `memory_md` 子字段：`last_refresh_at / last_refresh_trigger / draft_exists / draft_age_seconds / current_chars / max_chars` |
| **单测** | 扩展 `tests/webui/test_memory_stats_api.py` |
| **依赖** | WU-1 |
| **agent_role** | coder |

---

### WU-6（chain A）：前端 `MemoryMdCard` + 弹窗

| 字段 | 内容 |
|---|---|
| **范围** | 新增 3 个组件 + 改 `MemorySection.tsx` 一行 |
| **核心改动** | 1. **🆕 `MemoryMdCard.tsx`**（约 200-300 行）：状态栏 + 2 个按钮（立即刷新 / 查看）+ 30s 轮询<br>2. **🆕 `MemoryMdViewerModal.tsx`**（约 80 行）：只读 textarea 显示 MEMORY.md<br>3. **🆕 `MemoryDraftDiffModal.tsx`**（约 120 行）：双列只读 textarea（draft vs MEMORY.md）<br>4. `MemorySection.tsx`：主开关卡后插入 `<MemoryMdCard />` |
| **依赖** | WU-4, WU-5 |
| **agent_role** | coder |

---

### WU-7（chain B 根）：Dream 工具白名单改 `editable_files`

| 字段 | 内容 |
|---|---|
| **范围** | `nanobot/agent/memory.py:577-618` |
| **核心改动** | 1. `__init__` 新增 `self.draft_file = self.memory_dir / "MEMORY.md.draft"`<br>2. `build_dream_tools()` 的 `extra_write_allowed_files`：`[self.soul_file, self.user_file, self.draft_file]`，移除 `self.memory_file` |
| **单测** | `tests/agent/test_dream.py`：断言 `memory_file` **不在**白名单 + `draft_file` **在**白名单 |
| **依赖** | 无（独立模块） |
| **agent_role** | coder |

---

### WU-8（chain B）：`templates/agent/dream.md` 改写

| 字段 | 内容 |
|---|---|
| **范围** | `nanobot/templates/agent/dream.md`（现 109 行 → 重写约 130 行） |
| **核心改动** | 1. File routing 表：去掉 MEMORY.md 行，加 MEMORY.md.draft 行，加禁止项<br>2. 新增「MEMORY.md.draft generation rules」段<br>3. Verification 段加硬约束："禁止声称已写入 MEMORY.md" |
| **依赖** | WU-7 |
| **agent_role** | implementer |

---

### WU-9（chain B）：`cmd_dream` 成功消息文案改写

| 字段 | 内容 |
|---|---|
| **范围** | `nanobot/command/builtin.py:497-518` |
| **核心改动** | 成功消息改为"Dream 草稿已生成：memory/MEMORY.md.draft...MEMORY.md 真值由记忆系统从 SQLite 自动生成" |
| **依赖** | WU-7, WU-8 |
| **agent_role** | implementer |

---

### WU-10（chain A 集成测试）：端到端集成测试

| 字段 | 内容 |
|---|---|
| **范围** | `tests/memory/test_integration.py` 扩展 + `tests/webui/test_refresh_md_route.py` 扩展 |
| **核心测试** | 1. 后端集成：create → refresh → MEMORY.md 写入<br>2. 去抖：1秒内 5 次 create → 只 1 次 refresh<br>3. 截断：30 条 fact → 总字符 ≤ 1500<br>4. WebSocket 路由：requestMutation("memory-refresh-md", {}) 走通<br>5. Dream 降级：write_file("MEMORY.md") 被拒绝，write_file("MEMORY.md.draft") 通过 |
| **依赖** | WU-1 ~ WU-9 全部 |
| **agent_role** | test-engineer |

---

### WU-11（chain B 收尾）：i18n 全语种 + Dream 命令面板文案

| 字段 | 内容 |
|---|---|
| **范围** | `webui/src/i18n/locales/*/common.json`（11 语种） |
| **核心改动** | 1. 改 4 条 Dream 命令文案<br>2. 新增 `settings.memory.mdCard*` keys（共约 16 个） |
| **依赖** | WU-8, WU-9 |
| **agent_role** | implementer |

---

## 委派顺序

```
Round 1（并行）：
  WU-1（chain A 根） + WU-2（chain A，独立） + WU-7（chain B 根） + WU-8（依赖 WU-7）

Round 2（依赖 Round 1）：
  WU-3（依赖 WU-1）+ WU-4（依赖 WU-1）+ WU-5（依赖 WU-1）+ WU-9（依赖 WU-7, WU-8）

Round 3（依赖 Round 2）：
  WU-6（依赖 WU-4, WU-5）+ WU-11（依赖 WU-8, WU-9）

Round 4（依赖全部）：
  WU-10（集成测试）
```

### 委派表

| WU ID | wu_type | agent_role | 备注 |
|---|---|---|---|
| WU-1 | feature | coder | 核心类，单文件新模块 |
| WU-2 | feature | coder | 改签名 + 加 SQL 条件 |
| WU-3 | feature | coder | 三个钩子点 |
| WU-4 | feature | coder | 协议 + 路由表 + dispatch |
| WU-5 | feature | coder | stats 字段扩展 |
| WU-6 | ui | coder | 三个新组件 + i18n（仅 zh-CN/en） |
| WU-7 | feature | coder | 工具白名单硬改 |
| WU-8 | docs | implementer | prompt 改写 |
| WU-9 | docs | implementer | 文案改写 |
| WU-10 | test | test-engineer | 集成测试 |
| WU-11 | i18n | implementer | 11 语种 |

---

## 关键文件清单

| 文件 | 范围 | 关键位置 |
|---|---|---|
| `nanobot/memory/filters.py` | 新增 `content_hash_legacy()` | 紧贴 `compute_content_hash()`（`:140-157`） |
| `nanobot/memory/repository.py` | `list_memories` 加 `scope`/`min_importance` | `:107-135` |
| `nanobot/memory/lifecycle.py` | **🆕 新文件** | `MemoryLifecycle` 类 + `_safe_write_with_backup` + `_render_memory_md` + `truncate_memory_md` |
| `nanobot/webui/memory_api.py` | 三处加钩子 | `:375-492` |
| `nanobot/webui/memory_routes.py` | `refresh_memory_md` + `MEMORY_ACTION_NAMES` + dispatch | `:30-190` |
| `nanobot/webui/settings_routes.py` | `_SYSTEM_ROUTES` + `_MEMORY_MUTATION_PATHS` + stub | `:148-178, 242-265` |
| `nanobot/agent/memory.py` | Dream 工具白名单 + 新增 `draft_file` | `:577-618` |
| `nanobot/templates/agent/dream.md` | 改写 | 全文 109 行重写 |
| `nanobot/command/builtin.py` | `cmd_dream` 成功消息 | `:497-518` |
| `webui/src/components/settings/memory/MemoryMdCard.tsx` | **🆕** | 新组件 |
| `webui/src/components/settings/memory/MemoryMdViewerModal.tsx` | **🆕** | 新组件 |
| `webui/src/components/settings/memory/MemoryDraftDiffModal.tsx` | **🆕** | 新组件 |
| `webui/src/i18n/locales/*/common.json` | 新增 keys + Dream 命令文案 | 11 语种 |

---

## 验证（Verification）

### 单元测试

```bash
uv run --no-sync pytest tests/memory/test_lifecycle.py -v
uv run --no-sync pytest tests/memory/test_repository_scope_filter.py -v
uv run --no-sync pytest tests/memory/test_filters.py -v
uv run --no-sync pytest tests/agent/test_dream.py -v
uv run --no-sync pytest tests/webui/test_refresh_md_route.py -v
uv run --no-sync pytest tests/memory/test_integration.py -v
cd webui && bun run test
```

### 集成验证

1. **派生正确性**：`refresh_memory_md_sync` → MEMORY.md 存在且 ≤1500 字符
2. **去抖验证**：1 秒内 5 次调用 → 只 1 次实际 refresh
3. **Dream 白名单验证**：`memory_file` 不在白名单，`draft_file` 在白名单

### 兼容性回归

```bash
uv run --no-sync pytest tests/memory/test_integration.py tests/memory/test_repository_bm25.py -v
cd webui && bun run test settings-memory-section
uv run --no-sync basedpyright
```

---

## 不变量（强制保护）

1. `MemoryStore` 一行不动（保持纯文件 I/O 边界）
2. `MemoryType` enum 6 值不变
3. `nanobot/agent/context.py:235` MEMORY.md 注入点不变
4. GitStore 仍跟踪 4 文件
5. SOUL.md / USER.md 仍由 Dream 改（不纳入派生）

---

## 非目标（明确不做）

- ❌ IdentityView 整页
- ❌ 编译管线（`PromptCompiler`）
- ❌ Layer 0（记忆系统自描述 prompt 注入）
- ❌ draft 自动合并 UI
- ❌ draft 冲突自动检测
- ❌ USER.md / SOUL.md 由 SQLite 派生
- ❌ MEMORY.md 注入格式变更
- ❌ schema 迁移工具

---

## 关键源码索引

| 主题 | 位置 |
|---|---|
| `MemoryStore` 类 + `memory_file` 属性 | `nanobot/agent/memory.py:60-92` |
| `MemoryStore.build_dream_tools()` 白名单 | `nanobot/agent/memory.py:577-618` |
| `MemoryStore.build_dream_prompt()` | `nanobot/agent/memory.py:545-565` |
| `_append_lock = threading.Lock()` | `nanobot/agent/memory.py:89` |
| `cmd_dream` 主流程 | `nanobot/command/builtin.py:451-528` |
| `cmd_dream` 成功消息 | `nanobot/command/builtin.py:497-518` |
| `compute_content_hash` 3 参 | `nanobot/memory/filters.py:140-157` |
| `MemoryType` enum | `nanobot/memory/models.py:11-18` |
| `list_memories` 当前签名 | `nanobot/memory/repository.py:107-135` |
| `memory_api.create_memory` | `nanobot/webui/memory_api.py:375-420` |
| `memory_api.update_memory` | `nanobot/webui/memory_api.py:437-481` |
| `memory_api.delete_memory` | `nanobot/webui/memory_api.py:484-492` |
| `memory_api.stats_payload` | `nanobot/webui/memory_api.py:238-292` |
| `MemorySettingsOperations` 协议 | `nanobot/webui/memory_routes.py:30-54` |
| `MEMORY_ACTION_NAMES` | `nanobot/webui/memory_routes.py:59-65` |
| `dispatch()` | `nanobot/webui/memory_routes.py:91-190` |
| `_SYSTEM_ROUTES` memory 域 | `nanobot/webui/settings_routes.py:148-162` |
| `_MEMORY_MUTATION_PATHS` | `nanobot/webui/settings_routes.py:169-178` |
| `_null_memory_operations()` stub | `nanobot/webui/settings_routes.py:242-265` |
| `client.requestMutation` 实现 | `webui/src/lib/nanobot-client.ts:880-935` |
| WebUI Settings → Memory 容器 | `webui/src/components/settings/memory/MemorySection.tsx:1-123` |
| WebUI Dream 命令文案 | `webui/src/i18n/locales/zh-CN/common.json:1395-1410` |
| pytest asyncio_mode | `pyproject.toml:183-186` |
| openakita `refresh_memory_md`（参照） | openakita `memory/lifecycle.py:1384-1458` |
| openakita `_safe_write_with_backup` | openakita `memory/lifecycle.py:102-125` |
| openakita `truncate_memory_md` | openakita `memory/types.py:94-143` |
| openakita `MEMORY_MD_MAX_CHARS = 1500` | openakita `memory/types.py:82-89` |
