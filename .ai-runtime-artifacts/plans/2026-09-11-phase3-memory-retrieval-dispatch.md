---
artifact: dispatch
route: superpowers:orchestration:dispatcher-workflow
skills:
  - orchestration
  - writing-plans
source:
  - .ai-runtime-artifacts/plans/2026-09-11-phase3-memory-retrieval-plan.md
created_at: 2026-09-11
phase: phase3-memory-retrieval
status: pending-dispatch
---

# 阶段三记忆检索 — 派发计划

> 本文件是 `2026-09-11-phase3-memory-retrieval-plan.md` 的配套 stem 文件。
> 记录派发批次、并行约束、收尾顺序，供 Leader 派发 WU 和执行尾盘时使用。

## 批次概览

| Batch | GROUP | WU 数量 | 并行模式 | 派发条件 |
|---|---|---|---|---|
| **Batch 1** | GROUP-A（基础组件） | 4 WU | 4 路并行（独立 worktree） | 无依赖，直接派 |
| **Batch 2** | GROUP-B（四路通道） | 4 WU | 4 路并行（独立 worktree） | Batch 1 完成 |
| **Batch 3** | GROUP-C（后处理） | 2 WU | 串行（Reranker → Formatter） | Batch 2 完成 |
| **Batch 4** | GROUP-D（引擎 + 集成） | 2 WU | 串行（Engine → build_system_prompt） | Batch 3 完成 |
| **Batch 5** | GROUP-E（LLM Tool） | 2 WU | 串行（Tool → identity.md） | Batch 4 完成（T-12） |
| **Batch 6** | 尾盘 | — | — | Batch 5 完成 |

---

## Batch 1 — GROUP-A（并行 4 WU）

### 派发方式

4 个独立 coder 实例并行派发，**每个 WU 独占一个 worktree**（避免文件写入冲突）。

| Task | wu_type | agent_role | worktree 隔离范围 |
|---|---|---|---|
| T-01 | feature | coder | `nanobot/memory/retrieval/candidate.py` + 测试 |
| T-02 | feature | coder | `nanobot/memory/retrieval/preprocessor.py` + 测试 |
| T-03 | feature | coder | `nanobot/memory/retrieval/decomposer.py` + 测试 |
| T-04 | feature | coder | `nanobot/memory/retrieval/search_backend.py` + 修改 `database.py` + 测试 |

### 文件冲突域

- `nanobot/memory/database.py`：仅 T-04 写入，其他 WU 只读
- `nanobot/memory/retrieval/` 目录：4 个子包各独占一个文件，无交集
- `tests/memory/retrieval/`：每个 Task 独占一个测试文件

### 收尾条件

Batch 1 完成时，Leader 验证：
```bash
pytest tests/memory/retrieval/test_candidate.py tests/memory/retrieval/test_preprocessor.py \
       tests/memory/retrieval/test_decomposer.py tests/memory/retrieval/test_search_backend.py -v
```
预期：全部 passed。

---

## Batch 2 — GROUP-B（并行 4 WU）

### 派发方式

4 个独立 coder 实例并行派发，每个 WU 独占一个 worktree。

| Task | wu_type | agent_role | worktree 隔离范围 |
|---|---|---|---|
| T-05 | feature | coder | `channels/semantic.py` + `__init__.py` + 测试 |
| T-06 | feature | coder | `channels/episodes.py` + 修改 `models.py`（加字段）+ 测试 |
| T-07 | feature | coder | `channels/recent.py` + 测试 |
| T-08 | feature | coder | `channels/attachments.py` + 测试 |

### 文件冲突域

- `nanobot/memory/models.py`：仅 T-06 写入
- `nanobot/memory/retrieval/channels/`：4 个通道各独占一个 `.py`，T-05 额外写 `__init__.py`
- `tests/memory/retrieval/channels/`：每个 Task 独占一个测试文件

### 收尾条件

```bash
pytest tests/memory/retrieval/channels/ -v
```
预期：全部 passed。

---

## Batch 3 — GROUP-C（串行 2 WU）

### 派发顺序

串行派发，不可并行：T-10（Reranker）→ T-11（Formatter）。

| Task | wu_type | agent_role | 关键依赖 |
|---|---|---|---|
| T-10 | feature | coder | 依赖 `RetrievalCandidate`（T-01），但 Batch 1 已完成 |
| T-11 | feature | coder | 依赖 T-10（Reranker 输出格式影响 Formatter 输入契约） |

> T-10 和 T-11 虽然逻辑上可独立，但因 `reranker.py` 输出格式（T-10 的 `composite_score` 字段）是 `Formatter.format()`（T-11）的输入契约，建议串行以避免接口协商成本。

### 收尾条件

```bash
pytest tests/memory/retrieval/test_reranker.py tests/memory/retrieval/test_formatter.py -v
```
预期：全部 passed。

---

## Batch 4 — GROUP-D（串行 2 WU）

### 派发顺序

串行派发：T-12（RetrievalEngine）→ T-13（集成 build_system_prompt）。

| Task | wu_type | agent_role | 关键依赖 |
|---|---|---|---|
| T-12 | feature | coder | 依赖 T-01..T-11 全部完成；修改 `repository.py` |
| T-13 | feature | coder | 依赖 T-12；修改 `context.py` + `loop.py` |

### 实施前核对（强制）

T-13 开始前，coder 必须执行：
```bash
grep -n "^class\|^def" nanobot/agent/context.py | head -30
```
确认：
1. 当前类名是否为 `SystemContextBuilder`（不是则按真实名更新 plan 内的测试代码）
2. `build_system_prompt` 方法是否存在（是 `_build_memory_section` 的调用方）

### 收尾条件

```bash
pytest tests/memory/retrieval/test_engine.py tests/memory/retrieval/test_integration_context.py -v
```
预期：全部 passed。

---

## Batch 5 — GROUP-E（串行 2 WU）

### 派发顺序

串行派发：T-14（MemorySearchTool）→ T-15（identity.md）。

| Task | wu_type | agent_role | 关键依赖 |
|---|---|---|---|
| T-14 | feature | coder | 依赖 T-12（`RetrievalEngine` 已可用） |
| T-15 | docs | implementer | 依赖 T-14（identity.md 追加行引用 `memory_search` 工具名） |

### 收尾条件

```bash
pytest tests/agent/tools/test_memory_search_tool.py tests/agent/test_identity_template.py -v
```
预期：全部 passed。

---

## Batch 6 — 尾盘

### 步骤

1. **集体测试**：Leader 执行 collective-test，写 `.ai-runtime-artifacts/verifications/2026-09-11-phase3-memory-retrieval-collective-test.md`
2. **集体审查**：Leader 派 `reviewer` 并行扇出，写 `.ai-runtime-artifacts/reviews/2026-09-11-phase3-memory-retrieval-code-review.md`
3. **验证汇总**：Leader 整合两路输出，填入 execution-log 关闭段
4. **execution-log 关闭**：更新 `2026-09-11-phase3-memory-retrieval-execution-log.md` 状态为 `completed`

### 集体测试命令

```bash
# 全量回归
pytest tests/memory/ tests/agent/ -q

# 覆盖率
pytest tests/memory/retrieval/ --cov=nanobot/memory/retrieval \
  --cov-report=term-missing --cov-fail-under=85

# Lint + 类型
ruff check nanobot/memory/retrieval/ nanobot/agent/context.py \
  nanobot/agent/loop.py nanobot/agent/tools/memory_search.py
uv run --no-sync basedpyright nanobot/memory/retrieval/ nanobot/agent/tools/memory_search.py
```

---

## 派发约束总结

| 约束 | 说明 |
|---|---|
| **worktree 隔离** | 每个 WU 独占 worktree，避免并发写入冲突 |
| **Batch 内并行** | Batch 1/2 内部 4 路并行；Batch 3/4/5 内部串行 |
| **Batch 间串行** | Batch 1 → 2 → 3 → 4 → 5 → 6 严格顺序 |
| **feature flag 默认关闭** | T-13 的 `active_retrieval_enabled=False` 是生产默认，无需代码回滚即可关闭 |
| **无自动 push** | 每次 WU 完成后 Leader 手动收口，不自动 push / 开 PR |
| **末 WU 不直接「完成」** | 须先执行 Batch 6（集体测试 + 集体审查）后才算交付完成 |
