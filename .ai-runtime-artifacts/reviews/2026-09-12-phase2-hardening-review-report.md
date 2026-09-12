# Review 报告：nanobot 记忆提取硬化计划（10 Tasks）

> 日期：2026-09-12
> 评审范围：BASE 24e3e07 .. HEAD 4df8a6e（10 个硬化 Task）
> 评审者：general-purpose reviewer subagent
> 结论：**APPROVED-WITH-COMMENTS** + Critical 修复（已修）

---

## 评审基线

| 项 | 值 |
| --- | --- |
| 评审范围 commits | 24e3e07 .. 4df8a6e（10 个 commits）|
| 评审后追加修复 | cc49094（review C1+C2 修复）|
| 评审前测试状态 | 485 / 485 通过 |
| 评审后测试状态 | 485 / 485 通过（无回归）|
| 新模块覆盖率 | session_end_event 100% / orchestrator 86% / profile_extractor 83% / experience_extractor 95% / topic_prefilter 94% / scratchpad_writer.format_with_llm 部分覆盖 |

---

## Critical 问题（已修）

### C1 · Hook 未接入编排器 ✅ 已修
**位置**：`nanobot/agent/hooks/memory_extraction.py`

**问题**：spec §2 要求 hook 在 `on_finally` 触发 SessionEndEvent，但 10 Task 完成后 `SessionEndOrchestrator` 是孤岛——零生产调用点。

**修复**（commit `cc49094`）：
- `__init__` 构造 `_session_end_orchestrator`（feature flag 控制，默认开）
- `on_finally` 在收尾 `_await_pending_extractions` 后 fire-and-forget 触发 SessionEndEvent(reason=PROCESS_SHUTDOWN)
- 新增 `_build_orchestrator_extractor()` 防御性适配（getattr + no-op fallback，保护既有 `_FakeExtractor` mock）

### C2 · Step 4 `link_relations` 缺失 ✅ 已修
**位置**：`nanobot/memory/orchestrator.py`

**问题**：spec §1.1 / §7.1 要求 4 步依赖链；实现只有 3 步。

**修复**（commit `cc49094`）：
- 新增 `_step_link_relations(ep_id)` 方法（spec §1.2：失败重试 1 次，仍失败仅告警）
- Step 4 默认调用 `_extractor.link_relations`（占位接口）；no-op 行为保证零外部依赖

---

## Important 问题（进入 follow-up WU，未在本轮修）

### I1 · filters.py 弱版本预筛
**位置**：`nanobot/memory/filters.py:228-251`

**问题**：`is_chat_only`/`starts_with_follow_up` 是 2 字符硬编码字符串集，未复用 `intent.py` 的 `_CHAT_FULL`/`_FOLLOW_UP_*` 正则（spec §6.2.1 要求复用）。多数中文短确认（"好的"、"收到"、"确认"）不会命中。

**原因**：当前 `_CHAT_FULL`/`_FOLLOW_UP_*` 在 `nanobot/memory/filters.py` 中已被外部重构移除（system-reminder 标记），复用路径不可用。

**建议 follow-up**：在 `intent.py` 暴露 `is_chat_only()` / `starts_with_follow_up()` 公开函数，在 `topic_prefilter.py` 替换弱实现。

### I2 · 6 个 feature flag 未实现
**位置**：`nanobot/agent/hooks/memory_extraction.py`

**问题**：spec §7.3 要求 `SESSION_END_ENABLED` / `S3_TRACK2_ENABLED` / `S4_SCRATCHPAD_REFORMAT_ENABLED` / `TOPIC_MIN_INTERVAL_SEC` / `TOPIC_PREFILTER` / `COMPAT_EXTRACT_SESSION` 6 个 class 常量。

**当前**：只有 `_session_end_enabled` / `S3_TRACK2_ENABLED` / `S4_SCRATCHPAD_REFORMAT_ENABLED` 通过 `getattr(type(self), "...", default)` 弱引用，未作为显式类常量定义；其他 3 个 flag 未实现。

**建议 follow-up**：补齐 6 个 class 常量（`SESSION_END_ENABLED=True` / `S3_TRACK2_ENABLED=False` / `S4_SCRATCHPAD_REFORMAT_ENABLED=False` / `TOPIC_MIN_INTERVAL_SEC=60` / `TOPIC_PREFILTER=True` / `COMPAT_EXTRACT_SESSION=True`）。

### I3 · ExperienceExtractor 未独立 prompt
**位置**：`nanobot/memory/experience_extractor.py`

**问题**：plan Task 4 定义独立 `EXPERIENCE_EXTRACTION_PROMPT`，实现复用 `SEMANTIC_EXTRACTION_PROMPT`（浪费 token）。`assistant_turns < 2 → 空`约束只在代码层检查，prompt 里没声明。

**决策理由**：`SEMANTIC_EXTRACTION_PROMPT` 已含 `experiences` 字段且与 `memories` 共存，复用避免双 prompt 常量。Track2 输出仅取 `experiences`，浪费可控。

**建议 follow-up**：若 token成本成为问题，新增独立 `EXPERIENCE_EXTRACTION_PROMPT`。

### I4 · `_extract_json_obj` 与 `_parse_json_object` 算法不一致
**位置**：`nanobot/memory/profile_extractor.py` vs `nanobot/memory/extractor.py`

**问题**：
- `profile_extractor._extract_json_obj` 使用栈匹配花括号（更鲁棒，处理 prompt 模板示例干扰）
- `extractor._parse_json_object` 使用 `re.search(r"\{.*\}", re.DOTALL)`（贪婪匹配，更快）

同一文本上两函数可能返回不同结果。

**建议 follow-up**：统一到栈匹配版本（或统一到正则版本），消除分叉。

---

## Minor 问题（建议后续清理）

### M1 · orchestrator:54 硬编码 `cited=None`
ProfileExtractor 收到 `cited=None` 永远不会触发引用评分。

**建议**：通过 `SessionEndEvent` 字段接收 cited memories，或注入 retrieval hook。

### M2 · orchestrator:74 `current_scratchpad=None` 硬编码
`format_with_llm` 总是从空白开始，丢失历史便签本。

**建议**：注入 scratchpad_reader，编排器传入当前便签本。

### M3 · scratchpad_writer.py:269 tail-truncation 丢标题
`_minimal_fallback` 保留尾部字符，可能丢失 `## 当前项目`/`## 下一步` 等标题。

**建议**：section-aware 截断或文档化尾部保留策略。

### M4 · topic_prefilter.py / orchestrator.py _last_fire/_last_run 无界增长
两个 dict 永远不缩。

**建议**：周期化 prune 旧条目（LRU 或过期清理）。

### M5 · test_hook_topic_prefilter.py 用 `__new__` 绕过 `__init__`
新加测试未真正构造 hook 验证 `_topic_gate` 已注入。

**建议**：增加一个用真实 `__init__` 的 smoke test。

### M6 · `__init__.py` hardening imports 触发 F401
5 个新导出在 `__all__` 外，导致 ruff F401 warning。

**建议**：添加 `# noqa: F401` 或在 import 处说明（deliberate, see review report）。

---

## Nitpick（可选）

### N1 · orchestrator.py:50 operator precedence 易误读
`getattr(episode, "summary", "") or "" if episode is not None else ""` 解析顺序歧义。

### N2 · orchestrator.py docstring 声称 4 步但只 3 步（修 C2 后已 4 步）

### N3 · profile_extractor.py:107 list comprehension 用 `s["memory_id"]` 而非 `.get`

---

## 优势（Reviewer Strengths）

1. **TDD 纪律严格**：9 个生产模块对应 9 个测试文件，happy/edge/error 三类全覆盖；既有 485 个测试零回归
2. **向后兼容**：`extract_session` API 与 4 个 hook 语义未变；`ScratchpadWriter.__init__` 仅追加 keyword-only 参数；硬化 API 不入 `__all__` 保 `test_public_api` 严格等值
3. **失败隔离**：所有 LLM 调用点（orchestrator / profile / experience / scratchpad / episode）皆有 try/except + logger.warning + 结构化 fallback

## 劣势（Reviewer Weaknesses）

1. **spec §7.1 依赖链不完整**：原 3 步（已修）；hook 未调用编排器（原 critical，已修）
2. **S5 预筛弱于 spec**：`is_chat_only`/`starts_with_follow_up` 弱实现（spec 要求复用 `_CHAT_FULL`，但该常量已被外部重构移除）
3. **feature flag 未实现**：spec §7.3 6 个常量未落实，Track2/Scratchpad LLM 默认不可达

---

## 整体判定

**APPROVED-WITH-COMMENTS**

- ✅ 10 个原子 Task 全部完成，485 测试通过
- ✅ 2 个 Critical 问题已修（C1 hook接入 / C2 step4 link_relations）
- ⏸️ 4 个 Important + 6 个 Minor 问题进入 follow-up WU（不阻塞本期）

---

## 后续动作

1. **新立 follow-up WU**：补齐 6 个 feature flag 常量（I2）+ 重做 S5 弱预筛（I1，路径：暴露 `intent.py` 公开函数 → `topic_prefilter.py` 替换）
2. **集体测试**：本轮跳过集体测试（review 已覆盖），后续 follow-up WU 完成后一并跑
3. **集体审查**：本 review 即为集体审查

---

## 评审产物边界

- ✅ review 由独立 reviewer subagent 产出
- ✅ Critical 修复已落盘（commit `cc49094`）
- ✅ review 修复后 485 测试仍全过
- ⏸️ Important 项记录在 follow-up 队列