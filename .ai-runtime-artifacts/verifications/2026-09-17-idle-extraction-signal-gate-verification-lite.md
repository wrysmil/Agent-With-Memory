---
artifact: verification-lite
route: leader-direct | 小改动直做
skills:
  - source-driven-development
  - verification-before-completion
skills_evidence:
  - .claude/skills/source-driven-development/SKILL.md
  - .claude/skills/verification-before-completion/SKILL.md
source:
  - 用户原话：「现在不是有记忆提取吗，后台会设置定时任务，你这个得判断消息数也没有达标啥的把，不能直接就无脑提取。我发了个你好也提取那就废了」
  - .ai-runtime-artifacts/specs/session-end-orchestrator-optimization.md（L1/L2 口径来源）
  - .ai-runtime-artifacts/plans/2026-09-15-idle-extraction-full-pipeline-plan.md（idle 路径即常态提取入口）
created_at: 2026-09-17
tier: 1
---

# idle 记忆提取信号门槛 — 轻量验证（Tier 1）

> **适用：** Leader 直做简单任务（≥2 写文件 / 跑测试 / fix·实现类），**不**走 spec/plan/WU 编排。

## 范围

- 改动文件（3 个，均未提交）：
  - `nanobot/memory/extractor.py`（类常量 2 个 + 新方法 `_has_enough_idle_signal` + `run_idle_extraction` 守卫 + docstring/注释）
  - `tests/memory/test_idle_signal_threshold.py`（新增，12 个用例）
  - `tests/memory/test_citation_loop.py`（**已还原**：一度扩过 fixture，L1 降为 2 后不再需要，最终无净改动）
- 路由判定：Tier 1（Leader 直做）。依据：bugfix + ≥2 写文件；写文件数 < 3 且无并行 WU → 不触发 Tier 2 编排。
- 设计口径（用户经结构化提问确认）：复用 `SessionEndOrchestrator` 的 L1/L2 那套门槛、不引入新配置、**不动** `orchestrator.py`；追加确认：idle 的条数下限取 **2**（不照搬 L1 的 3）。

## 问题与修复

**问题**：正常聊天唯一活的提取入口是 idle 定时器（`_arm_idle_timer` → sleep 120s → `run_idle_extraction` → `extract_incremental`），而它修复前只有 `current_count <= last_count` 一个判断——不看条数、不看长度。于是一句「你好」+ 助手回复（2 条）在空闲 120 秒后必然触发两路 LLM 提取。会话结束编排器虽有一套 L1/L2 门槛，但它在生产代码里从未被调用（hook 只构造实例、从不调 `run()`；`/api/sessions/{key}/end` 只 publish 事件而全仓无订阅者），属死代码。

**修复**：在 `run_idle_extraction` 入口加 `_has_enough_idle_signal(new_slice)` 守卫，跳过时 return 空结果且**不推进** `state.last_count`。门槛口径：

- 条数：新增切片 < `IDLE_MIN_NEW_MESSAGES`（=2）→ 拦。取 2 而非编排器的 3：那边以「整个会话」为抽取单元，idle 抽的是增量切片，一轮实质对话本身就是完整单元。
- 内容：切片只有一条 user 消息、其文本 < `IDLE_MIN_SINGLE_USER_CHARS`（=10）且无工具调用 → 拦（即编排器的 L2）。「你好」由此拦下。

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `uv run --no-sync python -m pytest tests/memory/test_idle_signal_threshold.py -q` | **12 passed** |
| `uv run --no-sync python -m pytest tests/memory/ tests/config/ -q` | **904 passed, 1 skipped**（0 failed） |
| `uv run --no-sync ruff check nanobot/memory/extractor.py tests/memory/test_idle_signal_threshold.py tests/memory/test_citation_loop.py` | **All checks passed!** |
| `uv run --no-sync --with "basedpyright>=1.39.0,<2.0.0" basedpyright nanobot/memory/extractor.py` | **145 errors**；对 HEAD 版本同文件跑基线同为 **145 errors** → 零新增。且错误行号全部落在改动区间（685-710 / 880-990）之外 |
| `uv run --no-sync ruff check nanobot/ tests/`（全树） | 52 errors，**存量**；确认不含 `nanobot/memory/extractor.py` |

### 红-绿验证（回归测试确实能捕获缺陷）

```
RED   把 IDLE_MIN_NEW_MESSAGES / IDLE_MIN_SINGLE_USER_CHARS 临时置 0（门槛失效）
      → pytest tests/memory/test_idle_signal_threshold.py → 8 failed, 4 passed
      （失败的 8 个正是「应被拦」的用例；4 个「应放行」用例本就该通过）
GREEN 恢复常量 2 / 10
      → pytest tests/memory/test_idle_signal_threshold.py → 12 passed
```

### TDD 合规

- **非 test-first**：生产代码与测试为同一轮写入，工作区未提交，没有「测试 commit 早于代码 commit」的 git 证据 → `TDD compliance: N/A（无独立 commit 证据）`。
- 代之以上面的**红-绿验证**：确认测试在没有修复时会失败，不是「写完必过」的空断言。
- 质量覆盖：happy path（2 轮对话放行）、边界（恰好 10 字符放行 / `tool_calls: []` 不豁免 / `content=None` / content-block list）、错误隔离（沿用既有用例）、语义（跳过不推进 state、被跳过内容随下一切片一并抽取）——均有覆盖。

## 未验证项

- **端到端未见真机**：未启动 gateway、未用真实会话验证「发一句『你好』→ 等 120 秒 → 确认无 LLM 调用」。本机无可用 provider key，无法跑真 LLM 链路。已用单测覆盖到 `run_idle_extraction` 这一层（含 DB state 落盘断言），但 hook → 定时器 → extractor 的整链在真实进程中的行为未实测。
- **`basedpyright` 非本机已装依赖**：用 `uv run --with` 临时拉起（未执行 `uv sync --all-extras --dev`）。存量 145 条错误未修（本次范围外）。
- **存量失败用例**：`tests/agent/test_mcp_reconnect_crash.py::test_mcp_reconnect_during_shutdown_does_not_crash` 稳定失败（独立 reviewer 连跑 3/3）。该文件不 import 本次改动，且其依赖的 `nanobot/config/schema.py` 恰在工作区其他无关未提交改动中。判定与本次改动无关，但**未追根因**。

## 已披露的代价（非缺陷，设计取舍）

跳过时**不推进** `state.last_count`，因此：

- 用户继续发言 → 被跳过的消息随下一段切片一并抽取（不会丢）。
- **用户此后不再发言 → 这段内容不会入库。** idle 定时器只在 `after_run` / `on_finally` 重新装备，会话结束编排器是死路径，没有兜底。这是相对修复前的行为差异（修复前 2 条切片会抽），已在代码注释与 docstring 中如实写明。

## Next

- 任务完成 → 无需暂停（除非用户要求 commit/MR）
- 范围扩大 → 补 spec/plan 或升级 Tier 2 编排
- 建议单开跟踪（本次范围外）：`nanobot/memory/orchestrator.py:61` 的 `(content or "").strip()` 在 content 为 content-block list 时抛 `AttributeError`，且不在 try 块内 → [reviews/2026-09-17-idle-extraction-signal-gate-code-review.md](../reviews/2026-09-17-idle-extraction-signal-gate-code-review.md)
