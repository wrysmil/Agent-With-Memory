---
artifact: review
route: requesting-code-review + code-review-and-quality
skills:
  - requesting-code-review
  - code-review-and-quality
skills_evidence:
  - .claude/skills/requesting-code-review/SKILL.md
  - .claude/skills/code-review-and-quality/SKILL.md
source:
  - .ai-runtime-artifacts/specs/session-end-orchestrator-optimization.md（L1/L2 口径来源）
  - .ai-runtime-artifacts/plans/2026-09-15-idle-extraction-full-pipeline-plan.md
  - 用户原话：「现在不是有记忆提取吗，后台会设置定时任务，你这个得判断消息数也没有达标啥的把，不能直接就无脑提取。我发了个你好也提取那就废了」
created_at: 2026-09-17
batch_id: n/a（Tier 1 Leader 直做，非批次）
worktree_id: n/a
worktree_path: n/a
reviewer_instance: reviewer（独立实例 a6623c0d5cb6ff88e，readonly）
verdict: APPROVE
---

# idle 记忆提取信号门槛 — 代码审查

> **写入者：** Leader（收到 `reviewer` 返回后落盘）。Reviewer 为 readonly，未 Write 本文件。

## 审查范围

- 文件：
  - `nanobot/memory/extractor.py`（类常量 + `_has_enough_idle_signal` + `run_idle_extraction` 守卫 + 注释/docstring）
  - `tests/memory/test_idle_signal_threshold.py`（新增）
  - `tests/memory/test_citation_loop.py`（修订 A 曾改 fixture，修订 B 已还原 → 无净改动）
- BASE_SHA / HEAD_SHA：**不适用** —— 改动全部在工作区未提交，diff 命令为 `git diff HEAD -- <files>` 加未跟踪文件。

## 变更尺寸评估

| 指标 | 值 | 判定 |
|------|----|------|
| 变更行数 | extractor.py 约 +45 / -8；测试文件新增约 300 | 可接受（测试占多数） |
| 变更文件数 | 2 个有净改动 | 理想 |

## 对照依据

- spec：`.ai-runtime-artifacts/specs/session-end-orchestrator-optimization.md`（L1/L2 守卫口径）
- plan：`.ai-runtime-artifacts/plans/2026-09-15-idle-extraction-full-pipeline-plan.md`（确认 idle 是常态提取入口）
- 用户确认的口径：复用 L1/L2、不引入新配置、不动 `orchestrator.py`；追加确认条数下限取 2
- done criteria 勾选：✅ 单条「你好」不触发 LLM ✅ 有工具调用的短切片豁免 ✅ 跳过不推进 state ✅ 门槛有单测覆盖

## Findings（针对修订 A：`IDLE_MIN_NEW_MESSAGES = 3`）

### Critical

- 无。

### Important

1. **注释/docstring 过度承诺「不丢内容」，且「一轮实质对话后沉默」存在内容缺口**（`extractor.py` 注释与 docstring）
   - 修订 A 下切片恒 < 3 时 L1 永远拦截、state 不推进；idle 定时器只在 `after_run` / `on_finally` 重新装备，用户不再发言就不会再抽 → 该段内容永不入库。
   - 相对修复前是**行为回退**（修复前 2 条切片会被抽），且**无兜底**（reviewer 已核实 `SessionEndOrchestrator.run` 与 `/api/sessions/{key}/end` 均为死路径）。
   - Reviewer 给出的两条出路：改注释如实 + 用户显式接受；或放宽 L1。

### Suggestion

2. 阈值字面量在两处重复（`extractor.py` 类常量 vs `orchestrator.py:51,64` 裸字面量），存在静默漂移风险。
3. `_has_enough_idle_signal` 与 `extract_incremental` 各算一次「新增切片」，有两个真相源（当前完全一致，影响极低）。
4. `memory_extraction.py:615` 的 `_run_incremental_extraction` 是死代码（T5 已禁用），若 T5 复活会绕过新门槛。
5. `tests/memory/test_extraction_log_labels.py:189,206` 的会话只有 1 条消息，现已走不到 `extract_incremental`（断言仍在、意图未损，但易误读）。

### Nit

- 测试边界缺 3 例：恰好 10 字符、`tool_calls: []`、`content=None`（reviewer 指出，修订 B 已补）。

### FYI（越界但真实，按用户口径本次不改）

6. **`orchestrator.py:61` 真 bug**：`(content or "").strip()` 在 content 为 content-block 列表时抛 `AttributeError`，实测确认；该行**不在 try 块内**（位于 44-69 行，第一个 try 在 73 行），异常会直接冲出 `run()` 中断整个 4 步编排。当前因 `run()` 是死代码而潜伏，USER_CLOSE 端点接线即爆。**建议单开跟踪。**

## 结构疗法建议

| 重构模式 | 适用场景 | 建议 |
|---------|---------|------|
| 提取方法/函数 | 阈值判定散落两处 | 待 `orchestrator.py` 接线 USER_CLOSE 时，把 L1/L2 抽为共享判定函数，消除 Finding 2 的漂移风险。本次按用户口径不动 |

## 死代码 / 孤儿代码检查

- [x] 本次改动**未新增**死代码
- [x] 无注释掉的代码块
- [x] 旧实现完全替换（非 deprecated 标记）
- [ ] 既有死代码**未处理**（按用户明确口径保留）：`SessionEndOrchestrator`（构造后从不调用）、`_run_incremental_extraction`（无调用者）、`_detect_topic_change`（首行 return）

## Reviewer 证据（已读/已跑）

- 实测边界：恰好 10 字符放行、9 字符拦截，与编排器一致
- 实测 `([{'type':'text','text':'hi'}] or '').strip()` → `AttributeError`（确认 Finding 6）
- 独立复现 `pytest tests/memory/ tests/config/ -q` → 899 passed, 1 skipped（修订 A 时点）
- 独立复现 ruff 三个文件干净；确认全树 52 errors 不含 `nanobot/memory/extractor.py`
- `tests/agent/test_mcp_reconnect_crash.py::test_mcp_reconnect_during_shutdown_does_not_crash` 连跑 3/3 稳定失败，该文件不 import 本次改动 → 判定存量/环境性

## 修订 B（审查后应用）

| Finding | 处置 |
| --- | --- |
| Important 1 | **已修**：① `IDLE_MIN_NEW_MESSAGES` 3 → **2**，关闭「一轮实质对话后沉默」的内容缺口；② 注释/docstring 改为如实描述，明写「用户此后不再发言则该段内容不会入库」 |
| Suggestion 2 | **已修**：常量处加交叉引用注释，指向 `orchestrator.py` 的 L1/L2 |
| Nit（3 个边界例） | **已修**：补 `test_exactly_ten_chars_passes` / `test_empty_tool_calls_does_not_exempt` / `test_none_content_single_user_turn_skipped`，另补 `test_single_unanswered_user_message_skipped` / `test_substantive_two_message_slice_passes` |
| Suggestion 3、4、5 | **未修**（Minor，记录在案；4 需在启用 T5 时一并处理） |
| FYI 6 | **未修**（范围外，建议单开跟踪） |

## 修订 B 独立复核（同一 reviewer 实例，已完成）

**Important #1：已关闭（两半都关）。**

- 注释过度承诺：已修正为如实描述，复核确认新措辞断言的两条事实（idle 定时器只在 `after_run` / `on_finally` 重新装备；`SessionEndOrchestrator.run` 是死路径且 `SessionEndEvent` 无订阅者）均成立，无新的过度承诺。
- 内容缺口（场景 C）：常见形态已闭合。实测 `[user("我以后都用 uv 管理依赖"), assistant]` → 门槛放行，`test_substantive_two_message_slice_passes` 断言打满 2 次 LLM 并推进 `last_count=2`；同时 `[user("你好"), assistant]` 实测仍被拦，原目标未破坏。
- **L1=2 的偏离判定为「更正确的翻译」而非违背口径**：编排器 L1=3 的校准单位是「整个会话」，idle 抽的是增量切片，最小完整单元就是一轮对话 = 2 条。把「是否成单元」交给条数门槛、「是否琐碎」交给内容门槛（L2），职责划分更干净。
- 残余（已如实披露，不阻塞）：user 内容 < 10 字符但 assistant 回复很长、之后用户沉默 → 仍不入库（L2 只量 user 文本），属 L2 口径的设计行为。

### 复核新增 Finding（Minor，非阻塞，后续项）

7. **L1=2 后 `len(user_msgs) == 1` 守卫失效的 2 条切片会整体漏过 L2。** 实测 `[user, user]`、`[assistant, assistant]`、`[assistant, tool]` 均放行。形态可达（`on_finally` 对 cancelled/error 轮次也装备定时器，用户连发两条而 assistant 未答时转录里即为相邻两条 user）。
   - 影响有界：代价是**一对多余的 semantic+episode LLM 调用**（正是本次想省的浪费），**不是数据丢失**；且继承自编排器自身 `if len(user_msgs) == 1` 的写法，属口径内行为。
   - 若需收紧，最小做法是把 L2 判定改为「切片内没有任何 assistant 文本」，但会偏离已批准口径 → **留作后续项，本次不改**。

### 复核证据

- `pytest tests/memory/test_idle_signal_threshold.py tests/memory/test_citation_loop.py tests/memory/test_extractor_incremental.py tests/memory/test_extraction_integration.py -q` → 45 passed
- `pytest tests/memory/ tests/config/ -q` → 904 passed, 1 skipped（与 Leader 一致）
- `ruff check` → All checks passed
- 红-绿拆分自洽：逐函数推演 12 个用例，「应拦」恰 8 个、「应放行」恰 4 个 —— 与 Leader 报的 8 failed / 4 passed 吻合
- `test_citation_loop.py` 确认**无净改动**，还原正确

## 未验证项

- 端到端未见真机（无可用 provider key，未跑真实 LLM 链路）。已用单测覆盖到 `run_idle_extraction` 层（含 DB state 落盘断言），hook → 定时器 → extractor 整链在真实进程中的行为未实测。
- `basedpyright` 非本机已装依赖，用 `uv run --with` 临时拉起；存量 145 条错误未修（范围外）。

## 结论

**verdict:** **APPROVE**

修订 A 无 Critical；唯一阻塞项（Important #1）已在修订 B 实质关闭（注释如实 + 场景 C 常见形态放行且原目标仍达成），并获同一 reviewer 实例复核确认。新增 Minor 7 影响有界（一对多余 LLM 调用、无数据丢失）、继承自编排器既有写法，列为后续项。

## Next

- APPROVE → 可合并/提测
- 后续项：Minor 7（收紧 L2 判定）、Finding 6（`orchestrator.py:61` 的 `AttributeError`，建议单开跟踪）、Minor 4（T5 复活时补门槛）
