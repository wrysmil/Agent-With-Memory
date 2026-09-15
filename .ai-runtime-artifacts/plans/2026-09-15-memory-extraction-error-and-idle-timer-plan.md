---
artifact: plan
route: Harness:writing-plans
skills:
  - writing-plans
skills_evidence:
  - harness-kit/.agents/skills/writing-plans/SKILL.md
source:
  - .ai-runtime-artifacts/specs/2026-09-15-memory-extraction-error-and-idle-timer-spec.md
created_at: 2026-09-15
status: approved
approved: true
---

# Plan：记忆抽取三项修复（错误识别 / 计时器归零 / 日志摘要）

> Spec：`.ai-runtime-artifacts/specs/2026-09-15-memory-extraction-error-and-idle-timer-spec.md`（**未获用户确认前不派发**）
> 分支：`feature/memory-system`；Worktree：`wt-2026-09-15-memory-extract-fix`

## 0. 边界与依赖

**按文件切分 WU（不是按 spec 的 Part 切）**，避免两个 WU 同时改 `memory_extraction.py`：

| WU | 负责文件 | 覆盖 spec |
|---|---|---|
| WU-A | 新增 `nanobot/memory/llm_error.py`、`nanobot/session/labels.py`；改 `nanobot/memory/**`（extractor / profile_extractor / experience_extractor / scratchpad_writer） | Part A（除 hook 那处）+ Part C（extractor 侧日志） |
| WU-B | 改 `nanobot/agent/hooks/memory_extraction.py`、`nanobot/agent/loop.py` | Part B 全部 + Part A 的话题检测一处 + Part C（hook / loop 侧日志） |

**依赖：WU-A → WU-B（串行）**。WU-B 直接 import WU-A 建的两个 helper，不能并行。

## WU-A：helper + `nanobot/memory/**`

**wu_type**：`bugfix`　**agent_role**：`coder`

### 文件
- Create: `nanobot/memory/llm_error.py`（`response_error(response) -> str | None`）
- Create: `nanobot/session/labels.py`（`session_label(messages, metadata) -> str`，title 优先、空则首条 user 消息截 30 字）
- Modify: `nanobot/memory/extractor.py`（`_call_track` L1155 附近、episode 摘要 L1631 附近、日志 L856 / L908）
- Modify: `nanobot/memory/profile_extractor.py`（L119 附近）
- Modify: `nanobot/memory/experience_extractor.py`（L50 附近）
- Modify: `nanobot/memory/scratchpad_writer.py`（L241 附近）
- Create: `tests/memory/test_llm_error_surfacing.py`
- Create: `tests/session/test_labels.py`

### Steps
- [ ] **Step 1（先写失败测试）** `response_error`：伪造 `finish_reason="error"` / `error_status_code=403` 的 `LLMResponse` → 期望返回含 `403` 的原因串；正常响应 → `None`。
- [ ] **Step 2** 实现 `response_error`（拼 `HTTP <status> + error_kind + error_type`，字段全空时降级为 `llm_error`）。
- [ ] **Step 3** 六个改动点逐个接入：命中错误时返回 `call_failed: <reason>` / `error=<reason>` / 走 `_minimal_fallback`，且**日志不再出现 `unparseable JSON`**。
- [ ] **Step 4** `session_label`：三种用例（有 title / title 空回退首条 user / 无 user 消息返回 `(none)`）。
- [ ] **Step 5** extractor 的 `L856` / `L908` 两条日志加 `[摘要: …]`。
- [ ] **Step 6** 自测：`pytest tests/memory/ tests/session/ -q` + `ruff check nanobot/`。

### 验收
```bash
pytest tests/memory/test_llm_error_surfacing.py tests/session/test_labels.py -q   # 新增全绿
pytest tests/memory/ -q                                                          # 无回归
ruff check nanobot/
```
**关键证据**：一条断言"403 响应 → `_call_track` 返回 `call_failed: …403…`"的测试，以及一条"`ScratchpadWriter` 收到 403 响应时不把 `Error: …` 写入草稿本"的测试。

---

## WU-B：hook 计时器 + hook/loop 日志（依赖 WU-A 完成）

**wu_type**：`bugfix`　**agent_role**：`coder`

### 文件
- Modify: `nanobot/agent/hooks/memory_extraction.py`
  - `before_run`（L298）：`_cancel_pending_idle_timer(self._session_key)`（只取消，不装备）
  - `on_finally`（L332）：`stop_reason in ("cancelled", "error")` 时 `_arm_idle_timer(context)`
  - `after_run`（L310）：**不动**
  - 话题切换检测（L443）：接入 `response_error`，日志记明确原因（仍返回 `True`）
  - 日志 L515 / L544：加 `[摘要: …]`
- Modify: `nanobot/agent/loop.py`
  - hook factory（L551-556 附近）：构造时算出会话摘要标签并传给 hook
  - `L585-590`：deletion 路径日志加摘要
- Create: `tests/memory/test_idle_timer_reset.py`

### Steps
- [ ] **Step 1（先写失败测试）** 新消息到达 → 旧计时器被取消；`stop_reason="cancelled"` → 重新装备；`stop_reason="stop"` → 不重复装备（不覆盖 `after_run` 刚装的）。
- [ ] **Step 2** 实现 `before_run` / `on_finally` 两处改动。
- [ ] **Step 3** 摘要标签接入 hook 与 loop 的 3 条日志。
- [ ] **Step 4** 话题检测接入 `response_error`。
- [ ] **Step 5** 自测：`pytest tests/memory/ -q` + `ruff check nanobot/`。

### 验收
```bash
pytest tests/memory/test_idle_timer_reset.py -q
pytest tests/memory/ -q
ruff check nanobot/
```
**关键证据**：测试名直白到能自解释（如 `test_new_message_cancels_pending_idle_timer`、`test_cancelled_turn_rearms_timer`），并给出"同一 session 连发两条 → 只装备一次"的断言。

---

## 尾盘（Leader 手动执行）

1. **集体测试**：`pytest tests/memory/ tests/session/ -q` → 落 `.ai-runtime-artifacts/verifications/*-collective-test.md`
2. **集体审查**：派 `reviewer`（独立实例）审两个 WU 合并 diff → 落 `.ai-runtime-artifacts/reviews/*-code-review.md`
3. **手工验证（不写测试）**：gateway 起一次，用真实会话复现 spec §3.3 的时序，确认日志出现 `[摘要: …]` 且不再有 `unparseable JSON`
4. execution-log 关闭

## 风险

| 风险 | 缓解 |
|---|---|
| 改 `on_finally` 触碰既有"不能取消计时器"的约束（L336-344 注释） | 只新增装备、且用 `stop_reason` 守卫；WU-B 单测显式覆盖"正常路径不被误伤" |
| `ScratchpadWriter` 行为变化影响既有测试 | Step 6 跑全量 `tests/memory/` 回归 |
| 摘要标签取 `metadata["title"]` 的键名 | 复用 `nanobot/session/webui_turns.py:62` 的 `WEBUI_TITLE_METADATA_KEY`，不硬编码 |
| 两个 WU 串行导致总时长变长 | 文件边界清晰，串行是刻意选择；A 的 helper 小且独立，B 可立即接上 |
