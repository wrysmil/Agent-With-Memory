---
artifact: dispatch
route: orchestration:dispatcher-workflow
plan: .ai-runtime-artifacts/plans/2026-09-15-memory-retrieval-rca-fix-plan.md
skills:
  - writing-plans
  - orchestration
source:
  - core/orchestration/dispatcher-workflow.md
  - .ai-runtime-artifacts/specs/2026-09-15-memory-retrieval-rca.md
created_at: 2026-09-15
status: pending-dispatch
---

# 记忆检索恒空修复 — 派发计划

> 配套 `2026-09-15-memory-retrieval-rca-fix-plan.md`。
> 3 个 WU 文件不相交 → 单批次 3 路并行，同 worktree。

## Worktree

| 项 | 值 |
| --- | --- |
| `worktree_id` | `wt-2026-09-15-memory-retrieval-rca-fix` |
| `worktree_path` | `/Users/mima0000/Documents/学习-001/do-project/.harness-worktrees/Agent-With-Memory/wt-2026-09-15-memory-retrieval-rca-fix` |
| `branch` | `harness/wt-2026-09-15-memory-retrieval-rca-fix` |
| `base_ref` | `feature/memory-system` @ `20e4d94` |

## 执行图

```
GROUP-1（并行 3 WU，同 worktree，文件不相交）:

  WU-01: 检索数据通路闭合（adapter + repository + engine + 装配点）
         | 文件: nanobot/memory/retrieval/store_adapter.py (新建)
                 nanobot/memory/repository.py
                 nanobot/memory/retrieval/engine.py
                 nanobot/cli/gateway_runtime.py
                 tests/memory/retrieval/test_store_adapter.py (新建)
                 tests/memory/test_search.py (追加)
         | 依赖: 无 | wu_type: feature | agent_role: coder

  WU-02: 前置门禁放宽 + 工具会话注入
         | 文件: nanobot/memory/retrieval/preprocessor.py
                 nanobot/agent/tools/memory_search.py
                 tests/memory/retrieval/test_preprocessor.py (追加)
                 tests/memory/test_memory_search_tool.py (新建)
         | 依赖: 无 | wu_type: feature | agent_role: coder

  WU-03: 提示词记忆段重写
         | 文件: nanobot/templates/agent/identity.md
         | 依赖: 无 | wu_type: docs | agent_role: implementer

GROUP-2（串行，Leader 执行）:
  尾盘 A 集体测试 → 尾盘 B 并行审查（reviewer + security-auditor）→ execution-log 关闭
```

## 文件冲突域

- **0 冲突**：三个 WU 的文件集合两两不相交，已逐文件核对。
- 跨 WU 的**隐式契约**只有一条：WU-02 的 `preprocessor` 放宽后，WU-01 的
  `store_adapter` 才能被首轮消息触发——但二者互不 import，不需要接口对齐。
- `nanobot/memory/retrieval/engine.py` **只归 WU-01**（WU-02 不改 engine；
  本批刻意不引入 `bypass_gate` 参数，见 plan §2「WU 拆分依据」）。

## WU-01 派发指令

**wu_id:** WU-01 | **wu_type:** feature | **agent_role:** coder
**cwd（worktree_path）:** `/Users/mima0000/Documents/学习-001/do-project/.harness-worktrees/Agent-With-Memory/wt-2026-09-15-memory-retrieval-rca-fix`

**目标：** 打通三条检索路径共用的数据通路：新建 `MemoryStoreAdapter` 把
`MemoryDatabase` 适配成通道期望的 store 契约；`repository.search_memories` 加
CJK 子串 LIKE 回退；`query_semantic` 时间比较归一；`engine` 通道异常记 warning；
`gateway_runtime` 装配点接上 adapter。

**允许文件（不得越界）：**
- `nanobot/memory/retrieval/store_adapter.py`（新建）
- `nanobot/memory/repository.py`
- `nanobot/memory/retrieval/engine.py`
- `nanobot/cli/gateway_runtime.py`
- `tests/memory/retrieval/test_store_adapter.py`（新建）
- `tests/memory/test_search.py`（追加）

**禁止：** 改 `preprocessor.py` / `agent/tools/memory_search.py` / `templates/**`
（属 WU-02、WU-03）；改 schema；新增第三方依赖；commit / push；动 `webui/`。

**Done Criteria（逐条给证据）：**
1. `pytest tests/memory/retrieval/test_store_adapter.py -q` 全绿（含
   `test_retrieve_with_ids_returns_chinese_memories`：写 1 条中文 memory →
   断言 `retrieve_with_ids` 返回非空块且 ids 含 `m1`）。
2. `pytest tests/memory/ -q` 全绿（既有用例零回归；若有断言旧缺陷的用例，
   修测试并在返回里说明）。
3. `pytest tests/ -k "loop_wiring or database or repository" -q` 全绿。
4. `ruff check nanobot/` → `All checks passed!`
5. `grep -rn "RetrievalEngine(" nanobot/ | grep -v test` 只有 1 处，且 `store=` 为
   `MemoryStoreAdapter(...)`。

**本 WU Skills（须逐条 Load，并在返回里写 `### Skills 使用`）：**
- `source-driven-development` → `harness-kit/.agents/skills/source-driven-development/SKILL.md`
- `test-driven-development` → `harness-kit/.agents/skills/test-driven-development/SKILL.md`
- `incremental-implementation` → `harness-kit/.agents/skills/incremental-implementation/SKILL.md`
- `verification-before-completion` → `harness-kit/.agents/skills/verification-before-completion/SKILL.md`
- `requesting-code-review` → `harness-kit/.agents/skills/requesting-code-review/SKILL.md`

**必读上下文：** plan `.ai-runtime-artifacts/plans/2026-09-15-memory-retrieval-rca-fix-plan.md`
的 §1、§2、Task 1-5；`nanobot/memory/retrieval/channels/*.py`（契约来源）；
`tests/memory/retrieval/test_engine.py::_StubStore`（同契约的测试替身）。

**返回格式：**
```
wu_status: done | blocked | partial
### Skills 使用
- <slug>: <要点一句话>
### 变更文件
### 验证证据（命令 + 原始输出）
### 自检 code_review / self_check
### 遗留风险
```

## WU-02 派发指令

**wu_id:** WU-02 | **wu_type:** feature | **agent_role:** coder
**cwd（worktree_path）:** 同上

**目标：** 放宽 `MemoryQueryPreprocessor` 的前置门禁——首轮无历史时，含记忆意图
关键词的短消息不再被 `short_without_context` / `too_short` 误杀；把
`MemorySearchTool` 的会话来源从「恒 False 的 `hasattr(self, "_sessions")`」改为经
`ToolContext.sessions` 注入。

**允许文件（不得越界）：**
- `nanobot/memory/retrieval/preprocessor.py`
- `nanobot/agent/tools/memory_search.py`
- `tests/memory/retrieval/test_preprocessor.py`（追加）
- `tests/memory/test_memory_search_tool.py`（新建）

**禁止：** 改 `repository.py` / `engine.py` / `gateway_runtime.py` / `templates/**`
（属 WU-01、WU-03）；删除 `_CONTROL_ONLY` 既有条目；让门禁整体失效
（`test_should_still_skip_plain_short_without_context` 必须仍然通过）；commit / push。

**Done Criteria（逐条给证据）：**
1. `pytest tests/memory/retrieval/test_preprocessor.py tests/memory/test_memory_search_tool.py -q` 全绿。
2. `pytest tests/memory/ -q` 全绿。
3. `ruff check nanobot/` → `All checks passed!`
4. 手工复核：`MemoryQueryPreprocessor.should_skip_retrieval("查一下我的记忆", [])`
   → `(False, "")`；`should_skip_retrieval("明天天气怎么样", [])` → `(True, "short_without_context")`。

**本 WU Skills（须逐条 Load，并在返回里写 `### Skills 使用`）：**
- `source-driven-development` → `harness-kit/.agents/skills/source-driven-development/SKILL.md`
- `test-driven-development` → `harness-kit/.agents/skills/test-driven-development/SKILL.md`
- `incremental-implementation` → `harness-kit/.agents/skills/incremental-implementation/SKILL.md`
- `verification-before-completion` → `harness-kit/.agents/skills/verification-before-completion/SKILL.md`
- `requesting-code-review` → `harness-kit/.agents/skills/requesting-code-review/SKILL.md`

**必读上下文：** plan 的 §1.1 根因 2/3、Task 6；
`nanobot/agent/tools/long_task.py:55-72`（`_GoalToolsMixin` 会话注入参考范例）；
`nanobot/agent/tools/context.py:78-93`（`ToolContext.sessions` 定义）。

**返回格式：** 同 WU-01。

## WU-03 派发指令

**wu_id:** WU-03 | **wu_type:** docs | **agent_role:** implementer
**cwd（worktree_path）:** 同上

**目标：** 重写 `nanobot/templates/agent/identity.md` 的记忆段，让 Agent 知道
**三层 SQLite 记忆（memories / episodes / scratchpad @ memory/state.db）才是主存储**，
`memory/MEMORY.md` 是 Dream 的次要散文文件、常为空壳，**空 MEMORY.md ≠ 记忆为空**。

**允许文件（不得越界）：**
- `nanobot/templates/agent/identity.md`

**禁止：** 改任何 Python；改 `_snippets/`；改文首 `## Runtime` / `## Workspace` 结构；
commit / push。

**Done Criteria：**
1. 两个 Jinja 分支（`agent_workspace_path != workspace_path` 与 `else`）**都**已更新，
   且都点明 `state.db`、三个表名、`memory_search` 是主检索入口、
   `MEMORY.md` 空≠记忆为空。
2. 模板渲染通过，无 Jinja 语法错误。
3. `pytest tests/ -k "identity or prompt" -q` 全绿（若存在）。

**本 WU Skills：** 无（implementer 纯体力活）。
**必读上下文：** plan Task 7；RCA §根因 4。

**返回格式：** 同 WU-01（可省 `code_review`）。

## 尾盘（GROUP-2，Leader 执行）

| 门禁 | 委派 | 产物 |
| --- | --- | --- |
| A 集体测试 | Leader 执行（Load `verification-before-completion`） | `.ai-runtime-artifacts/verifications/2026-09-15-memory-retrieval-rca-fix-collective-test.md` |
| B1 代码审查 | `reviewer`（只读，独立实例） | `.ai-runtime-artifacts/reviews/2026-09-15-memory-retrieval-rca-fix-code-review.md` |
| B2 安全审查 | `security-auditor`（只读） | `.ai-runtime-artifacts/reviews/2026-09-15-memory-retrieval-rca-fix-security-review.md` |
| C 关闭 | Leader | `.ai-runtime-artifacts/execution-logs/2026-09-15-memory-retrieval-rca-fix-execution-log.md` |

**门禁：** A FAIL → STOP；B 任一 BLOCK → 对应 WU 修复后重审。
