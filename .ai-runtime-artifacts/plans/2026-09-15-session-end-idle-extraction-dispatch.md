---
artifact: dispatch
route: superpowers:orchestration:dispatcher-workflow
plan: .ai-runtime-artifacts/plans/2026-09-15-session-end-idle-extraction-plan.md
skills:
  - writing-plans
  - orchestration
source:
  - core/orchestration/dispatcher-workflow.md
created_at: 2026-09-15
status: pending-dispatch
---

# 会话搁置触发增量抽取 + 工作记忆 UI 清理 — 派发计划

> 本文件是 `2026-09-15-session-end-idle-extraction-plan.md` 的配套 stem 文件。
> 派 2 个独立 coder（后端 + 前端）并行，写完后串行做测试收尾 + 尾盘。

## 批次概览

| Batch | GROUP | WU 数量 | 并行模式 | 派发条件 |
|---|---|---|---|---|
| **Batch 1** | GROUP-A（后端）+ GROUP-B（前端） | 2 WU | 2 路并行（独立 worktree） | 无依赖，直接派 |
| **Batch 2** | GROUP-C（测试收尾） | 1 WU | 串行 | Batch 1 全部合入 |
| **Batch 3** | 尾盘 | — | — | Batch 2 完成 |

---

## Batch 1 — GROUP-A & GROUP-B（并行 2 WU）

### 派发方式

2 个独立 coder 实例**并行**派发，每个 WU 独占一个 worktree（基线分支 `feature/memory-system`）。**无文件共享**。

| GROUP | Task | wu_type | agent_role | worktree 隔离范围 |
|---|---|---|---|---|
| **GROUP-A**（后端） | Task 1-4 | feature | coder | `nanobot/memory/database.py` + `nanobot/memory/repository.py` + `nanobot/memory/extractor.py` + `nanobot/agent/hooks/memory_extraction.py` + `nanobot/config/schema.py` + `nanobot/memory/models.py` + 3 个新测试文件 |
| **GROUP-B**（前端） | Task 5 | ui | coder | `webui/src/components/settings/memory/MemorySection.tsx` + 删除 `ScratchpadEditor.tsx` + 9 个 i18n locale JSON + `webui/src/tests/settings-memory-section.test.tsx` |

### 文件冲突域

- **0 冲突**：后端文件全在 `nanobot/`，前端文件全在 `webui/`。两个 WU 不可能写到同一文件。
- 唯一的跨边界引用是 i18n key 名（GROUP-B 删 `settings.memory.tabScratchpad` 等），后端无引用。

### Worktree 分支命名

- GROUP-A：`wt-session-idle-extraction-backend`（从 `feature/memory-system` 基线）
- GROUP-B：`wt-session-idle-extraction-frontend`（从 `feature/memory-system` 基线）

### GROUP-A WU 详细指令

**Task 1：schema + repository**
- 改 `nanobot/memory/database.py`：在 `init_schema` 加 `session_extraction_state` 表
- 改 `nanobot/memory/repository.py`：加 3 个函数（`get_extraction_state` / `upsert_extraction_state` / `reset_extraction_state`）
- 改 `nanobot/memory/models.py`：加 `ExtractionState` dataclass
- 新建 `tests/memory/test_extraction_state.py`：CRUD + 推进 + 重置 4 个测试
- **DoD**：`pytest tests/memory/test_extraction_state.py -q` 全绿；`pytest tests/memory/ -q` 不引入新失败

**Task 2：`MemoryExtractor.run_idle_extraction` 包装 state**
- 改 `nanobot/memory/extractor.py`：新增 `run_idle_extraction(session)` 方法，读 state、判边界、调 `extract_incremental`、成功后写 state
- 新建 `tests/memory/test_extractor_incremental.py`：3 个测试（用 state 表 start_index / 失败不推进 / 压缩时 reset）
- **DoD**：`pytest tests/memory/test_extractor_incremental.py -q` 全绿

**Task 3：`MemoryExtractionHook` 替换为 idle 定时器**
- 改 `nanobot/agent/hooks/memory_extraction.py`：
  - 删 `_schedule_run_extraction` / `_await_pending_extractions` / `_run_extraction` / `EXTRACTION_WAIT_TIMEOUT`
  - 新增模块级 `_PENDING_IDLE_TIMERS: dict[str, asyncio.Task] = {}`
  - 新增 `_arm_idle_timer(context)` / `_run_idle_extraction(context)` 实例方法
  - `after_run` 改调 `_arm_idle_timer`
  - `on_finally` 简化为取消本 session 的 idle 定时器
- 新建 `tests/memory/test_hook_idle_timer.py`：4 个测试（arm / cancel old / fire / session delete cancel）
- **预期失败**：`tests/memory/test_hook_memory_extraction.py` 和 `test_memory_extraction_hook.py` 里有 T5 死测试 + 旧 T1 测试会失败——**WU-C 处理**

**Task 4：`AgentDefaults.memory_idle_seconds` + 注入**
- 改 `nanobot/config/schema.py`：`AgentDefaults` 加 `memory_idle_seconds: int = 600`
- 改 `nanobot/agent/hooks/memory_extraction.py`：`MemoryExtractionHook.__init__` 加 `idle_seconds: float | None = None`，优先级：实例属性 > 类默认
- **DoD**：测试过；`pytest tests/ -q` 不引入新失败（除 T5 死测试）

### GROUP-B WU 详细指令

**Task 5：删 scratchpad tab + 删 ScratchpadEditor + 清 i18n**

1. **grep 引用方**：
   ```bash
   grep -rln "ScratchpadEditor\|tabScratchpad\|working memory\|工作记忆" webui/src/
   ```
2. **改 `MemorySection.tsx`**：
   - `MemoryTab = "semantic" | "episode" | "scratchpad"` → `"semantic" | "episode"`
   - `MEMORY_TABS` 数组删第三项
   - 删 `import { ScratchpadEditor }`
   - 删 `{tab === "scratchpad" && <ScratchpadEditor .../>}` 分支
3. **删 `ScratchpadEditor.tsx`**（整个文件）
4. **改 9 个 i18n JSON**（`en` / `zh-CN` / `es` / `fr` / `id` / `ja` / `ko` / `pt-BR` / `vi`）：
   - 删 `settings.memory.tabScratchpad` 键
   - 删其他 working memory / scratchpad 相关 UI label（如有）
5. **改 `webui/src/tests/settings-memory-section.test.tsx`**：
   - 断言改为只断言 `semantic` / `episode` 两个 tab
6. **DoD**：
   - `cd webui && npx --no-install tsc --noEmit` 干净
   - `cd webui && npx --no-install vitest run` 全绿
   - `cd webui && npx --no-install vite build` 构建成功

### 收尾条件

Batch 1 完成时，Leader 验证：

```bash
# 后端：2 个 worktree 的 pytest + lint
cd .worktrees/wt-session-idle-extraction-backend
uv run --no-sync pytest tests/memory/ -q
uv run --no-sync ruff check nanobot/

cd .worktrees/wt-session-idle-extraction-frontend
cd webui && npx --no-install tsc --noEmit
cd webui && npx --no-install vitest run
```

预期：
- 后端：仅 T5 死测试 + 旧 T1 测试失败（约 20+ 个），已在 WU-C 范围内
- 前端：全部通过

### 合入策略

两个 worktree 完成后 Leader 按顺序合入 `feature/memory-system`：
1. 先合 GROUP-B（前端，更小、风险更低）
2. 再合 GROUP-A（后端，可能需要解决 T5 死测试与旧 T1 测试的合并冲突——但 WU-C 会一次性删干净，所以不会冲突）

---

## Batch 2 — GROUP-C（串行 1 WU）

### 派发方式

1 个 coder 实例，必须等 Batch 1 全部合入后再派（要看到完整代码才能正确删除死测试）。

| Task | wu_type | agent_role | worktree 隔离范围 |
|---|---|---|---|
| Task 6 | test | coder | `tests/memory/test_hook_memory_extraction.py` + `tests/memory/test_memory_extraction_hook.py`（删死测试） + 新建 `tests/memory/test_extraction_integration.py`（端到端集成测试） |

### Task 6 详细指令

1. **列出要删的测试**：
   ```bash
   grep -n "EXTRACTION_WAIT_TIMEOUT\|_schedule_run_extraction\|TopicChangeDetection\|TopicChangeIncremental\|_await_pending_extractions" tests/memory/test_hook_memory_extraction.py tests/memory/test_memory_extraction_hook.py
   ```
2. **删 `TestTopicChangeDetection`** 整类（8 个测试）
3. **删 `TestTopicChangeIncrementalExtraction`** 整类（12 个测试）
4. **删依赖 `_schedule_run_extraction` / `EXTRACTION_WAIT_TIMEOUT` / `_await_pending_extractions`** 的旧 T1 测试
5. **新建 `tests/memory/test_extraction_integration.py`**：端到端
   - `test_idle_extraction_full_cycle`：开总开关 → after_run 调 3 次（模拟 3 turn） → 模拟 10 分钟到点（用 `IDLE_THRESHOLD_SECONDS = 0.05` 加速） → 验证 state.last_count 推进、memories 入库
   - `test_idle_extraction_cancelled_by_new_message`：开总开关 → after_run 调 1 次 → 立即再 after_run 调 1 次 → 验证旧 timer 被 cancel
   - `test_idle_extraction_no_op_when_state_current`：state.last_count = current_count → 调 run_idle_extraction → 0 LLM 调用

### 收尾条件

```bash
uv run --no-sync pytest tests/memory/ -q
```

预期：**0 失败**（包括之前 20+ 个 T5 死测试 + 旧 T1 测试都已清掉）

---

## Batch 3 — 尾盘

### 步骤

1. **集体测试**：Leader 在主 checkout 执行：
   ```bash
   uv run --no-sync pytest tests/ -q
   uv run --no-sync ruff check nanobot/ tests/
   cd webui && npx --no-install tsc --noEmit
   cd webui && npx --no-install vitest run
   cd webui && npx --no-install vite build
   ```
2. **集体审查**：Leader 派 `reviewer` 并行扇出，写 `.ai-runtime-artifacts/reviews/2026-09-15-session-end-idle-extraction-code-review.md`
3. **执行日志**：更新 `.ai-runtime-artifacts/execution-logs/...`（如适用）

### 集体测试硬指标

- `pytest tests/ -q` 0 失败
- `ruff check` 0 新告警
- `tsc --noEmit` 0 错
- `vitest` 全绿
- `vite build` 成功

---

## 派发约束总结

| 约束 | 说明 |
| --- | --- |
| **worktree 隔离** | 每个 WU 独占 worktree，避免并发写入冲突 |
| **Batch 内并行** | Batch 1 内部 2 路并行；Batch 2 / 3 内部串行 |
| **Batch 间串行** | Batch 1 → 2 → 3 严格顺序 |
| **默认行为不变** | 搁置阈值 600s 默认；总开关默认关；存量 state.db 用户升级无感（新建表 + 缺省 0） |
| **无自动 push** | 每次 WU 完成后 Leader 手动收口，不自动 push / 开 PR |
| **末 WU 不直接「完成」** | 须先执行 Batch 3（集体测试 + 集体审查）后才算交付完成 |
