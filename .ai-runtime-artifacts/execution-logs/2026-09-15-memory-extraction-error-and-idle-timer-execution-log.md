---
artifact: execution-log
route: Harness:orchestration:dispatcher-workflow（实际降级为 Tier 1 Leader 直做，见 §1）
source:
  - .ai-runtime-artifacts/specs/2026-09-15-memory-extraction-error-and-idle-timer-spec.md
  - .ai-runtime-artifacts/plans/2026-09-15-memory-extraction-error-and-idle-timer-plan.md
  - .ai-runtime-artifacts/verifications/2026-09-15-memory-extraction-error-and-idle-timer-collective-test.md
  - .ai-runtime-artifacts/reviews/2026-09-15-memory-extraction-error-and-idle-timer-code-review.md
created_at: 2026-09-15
target_branch: feature/memory-system
status: completed
---

# 记忆抽取三项修复 — 执行日志

## 1. 执行方式（偏离声明）

plan 定义为两个串行 WU（WU-A `nanobot/memory/**` + 新增 helper；WU-B hook/loop），由 `coder` 子 Agent 承接。

**实际执行为 Leader 直做，未派发 WU、未建 worktree。** 依据：用户明确指示「对的开始做吧，你直接改吧，**不用子Agent**」。

后果（记录在案，供后续判断）：
- 无独立 coder 自测环，测试由 Leader 同步编写；
- 无独立 reviewer——审查降级为 Leader 自审，见 review 产物 §0 的独立性声明。

## 2. WU 状态

| WU | 负责文件 | 状态 | 回归证据 |
|---|---|---|---|
| WU-A | 新增 `nanobot/memory/llm_error.py`、`nanobot/session/labels.py`；改 `nanobot/memory/{extractor,profile_extractor,experience_extractor,scratchpad_writer}.py` | **done** | `pytest tests/memory/ tests/session/ -q` → 807 passed |
| WU-B | 改 `nanobot/agent/hooks/memory_extraction.py`、`nanobot/agent/loop.py` | **done** | 同上 |

串行依赖（WU-B import WU-A 的两个 helper）按 plan 保持；因 Leader 直做，实际是一次性提交同一份工作区改动。

## 3. 交付物

### 3.1 代码

| 类型 | 文件 | 行数 |
|---|---|---|
| 新增模块 | `nanobot/memory/llm_error.py` | 56 |
| 新增模块 | `nanobot/session/labels.py` | 112 |
| 修改 | `nanobot/memory/extractor.py` | +21/−2 附近 |
| 修改 | `nanobot/memory/profile_extractor.py` | +6 |
| 修改 | `nanobot/memory/experience_extractor.py` | +6 |
| 修改 | `nanobot/memory/scratchpad_writer.py` | +7 |
| 修改 | `nanobot/agent/hooks/memory_extraction.py` | +86/−4 |
| 修改 | `nanobot/agent/loop.py` | +18/−1 |

### 3.2 测试

| 文件 | 用例数 |
|---|---|
| `tests/memory/test_llm_error_surfacing.py` | 13 |
| `tests/memory/test_idle_timer_reset.py` | 10 |
| `tests/memory/test_extraction_log_labels.py` | 5 |
| `tests/session/test_labels.py` | 16 |
| **合计** | **44** |

### 3.3 产物

- spec：[`specs/2026-09-15-memory-extraction-error-and-idle-timer-spec.md`](../specs/2026-09-15-memory-extraction-error-and-idle-timer-spec.md)（`status: approved`）
- plan：[`plans/2026-09-15-memory-extraction-error-and-idle-timer-plan.md`](../plans/2026-09-15-memory-extraction-error-and-idle-timer-plan.md)（`status: approved`）
- 集体测试：[`verifications/2026-09-15-memory-extraction-error-and-idle-timer-collective-test.md`](../verifications/2026-09-15-memory-extraction-error-and-idle-timer-collective-test.md)
- 代码审查：[`reviews/2026-09-15-memory-extraction-error-and-idle-timer-code-review.md`](../reviews/2026-09-15-memory-extraction-error-and-idle-timer-code-review.md)

## 4. 验证证据

```bash
.venv/bin/python -m pytest -q
# 13 failed, 7269 passed, 19 skipped in 174.34s
# 13 个失败全部在 HEAD 基线复现，名单逐条一致 → 与本任务无关（详见集体测试 §3）

.venv/bin/python -m pytest -q tests/memory/ tests/session/ tests/channels/test_websocket_application_boundary.py
# 807 passed in 7.41s

.venv/bin/python -m ruff check <本任务全部改动文件>
# 0 error

uv run --no-sync basedpyright nanobot/memory/ nanobot/agent/hooks/memory_extraction.py nanobot/agent/loop.py
# 452 errors —— 与 HEAD 基线 452 完全一致（零新增）
uv run --no-sync basedpyright nanobot/session/labels.py nanobot/memory/llm_error.py
# 0 errors
```

## 5. 过程中触发的返工

| # | 现象 | 根因 | 处置 |
|---|---|---|---|
| 1 | 全量跑出 1 个**由本任务引入**的失败：`test_persisted_webui_session_prefix_has_one_production_owner` → `assert ['nanobot/session/labels.py'] == []` | 新模块 docstring 里写了字面量 `Processing message from websocket:<sender_id>`，违反仓库「`websocket:` 前缀只有一个生产属主」的架构约束 | 改为 `<channel>:<sender_id>`；复跑 807 passed |
| 2 | `basedpyright` 在新建 `labels.py` 报 4 个 error | `isinstance(..., Mapping)` 冗余；`Any` 型 `content` 迭代产生 Unknown | 去掉冗余 isinstance；用 `cast(list[object], …)` / `cast(Mapping[object, object], …)` 收窄；现 0 error |
| 3 | review 中发现「两侧摘要可能不一致」（R1） | `_run_idle_extraction` 构造 `Session` 未传 `metadata` | **未修**，超范围；记录为已知限制 |
| 4 | review 中发现 plan 偏离（R5：未复用 `webui_turns` 常量） | 该模块重依赖 | 保留偏离 + 补 `TestMetadataKeyDriftGuard` 文本比对兜底（+3 用例） |

## 6. 尾盘清单

| 项 | 状态 |
|---|---|
| 集体测试落盘 | ✅ |
| 集体审查落盘 | ✅（**自审**，非独立实例） |
| 真实 gateway 手工冒烟 | ❌ **未执行**——需起 gateway + 真实会话复现时序；日志格式由单测断言覆盖，但「真机日志出现 `[摘要: …]` 且不再有 `unparseable JSON`」无实测证据 |
| execution-log 关闭 | ✅ |

## 7. 遗留（建议后续单独处理，均不阻塞本次交付）

1. **R1**：给 idle 抽取的 `Session` 补 `metadata`，让 extractor 侧日志也能显示 `metadata["title"]`，与 hook 侧摘要一致。
2. **未提交**：改动仍在工作区，未 `git add` / 未 commit（用户未要求）。
3. **残留 stash**：验证基线时创建了 `stash@{0}`（`baseline-check-2026-09-15`），内容已逐字节核对与工作区一致，但 `git stash drop` 被权限分类器拦截，**未删除**。需要时手动 `git stash drop` 即可。

## 8. 状态

**completed**（带 2 项已知限制 R1/R2 + 1 项 plan 偏离 R5 + 1 项未执行的冒烟）。
