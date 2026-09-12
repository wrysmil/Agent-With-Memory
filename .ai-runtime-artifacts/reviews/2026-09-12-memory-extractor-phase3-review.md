# Phase 3 抽取器优化 — 集体审查报告

> 审查人：独立 reviewer subagent（与实现 coder 异实例）
> 计划：`.ai-runtime-artifacts/plans/2026-09-12-memory-extractor-phase3-plan.md`
> 集体测试：`.ai-runtime-artifacts/verifications/2026-09-12-memory-extractor-phase3-collective-test.md`
> Follow-up 报告：`.ai-runtime-artifacts/verifications/2026-09-12-memory-extractor-phase3-followup-collective-test.md`
> 调研：`.ai-runtime-artifacts/research/2026-09-12-openakita-extractor-survey.md`
> 日期：2026-09-12
> 状态：**PASS**（已 follow-up 修复 3 项 MAJOR，verdict 从 PASS-WITH-FOLLOWUPS 升级）

---

## 总评

- **verdict**: PASS
- **summary**: 3 项 MAJOR 已修复（事务回滚 / fallback redact / DB.init replay），新增 6 个测试全部通过；527 个 memory 测试 100% 通过 / 零新增 ruff 错误。MINOR / NIT 按用户决定保留作为后续改进（详见 follow-up 报告 §4 已知限制）。

---

## 详细发现

### [MAJOR] 鲁棒性：`_safe_write_with_fallback` 失败后未 `conn.rollback()`，后续写入可能受事务状态污染

- **位置**: `nanobot/memory/extractor.py:969-1033` + `nanobot/memory/database.py`
- **描述**: 在 `_persist` 的 4a (`add_memory`) 与 4c (`add_episode`) 共用同一 `with self.database.connect() as conn:` 块。当 `_safe_write_with_fallback` 捕获到 `OperationalError("database is locked")` 等异常后被吞掉，连接仍处于当前事务内，循环继续尝试后续 memory / episode。SQLite 默认不在 `execute` 失败时自动回滚，存在 `InterfaceError: cannot operate on a closed database` 或事务失败状态污染风险。
- **测试缺口**: `test_writes_file_on_db_error` 只覆盖「首次失败」单点，未覆盖「中间失败后继续」场景。
- **建议**:
  1. 在 `_safe_write_with_fallback` 失败分支顶部加 `conn.rollback()`（幂等，对未失败事务无副作用，对失败事务是恢复手段）。
  2. 增加测试：构造 `BrokenWriter` 让前 2 次成功 / 第 3 次失败 / 之后又成功，验证后续仍能写入且第 3 条落 fallback。

### [MAJOR] 安全性：fallback JSON 内容含 LLM 抽取的原始 `Memory.content`，未做 redact（OWASP LLM05）

- **位置**: `nanobot/memory/extractor.py:1005-1020`
- **描述**: `_safe_write_with_fallback` 把 `item = memory.to_row()` 原样落盘，包括 `content` / `subject` / `predicate` / `tags`。`_redact` 仅作用于 ActionNode 的 input/output（extractor.py:357），不作用于 memory content。LLM 输出在 OWASP LLM05 视角下属不可信输入，恶意 prompt injection 可让 LLM 把内存中的凭据 / PII 放进 `content`，最终落 fallback JSON 并在启动重放前残留数日。
- **建议**: 在落盘前对 `item["content"]` 调一次 `nanobot.memory.extractor._redact`（模块级常量复用成本低），或在重放时校验内容。
- **已通过**: `safe_kind = str(kind).replace("/", "_").replace("\\", "_")` 的路径分隔符清洗正确，防御「kind 字符串注入路径」攻击。

### [MAJOR] 架构：`MemoryDatabase.fallback_dir` 自动重放仅挂在 `MemoryExtractor.__init__`

- **位置**: `nanobot/memory/extractor.py:623-630`
- **描述**: `MemoryDatabase` 自身不会在构造时重放。若未来出现「跳过 extractor 直接用 `add_memory` / `add_episode`」的路径（批量回填、CLI 工具、迁移脚本），fallback 文件将永远堆积。coder 在集体测试报告 §5.2 已披露该偏差。
- **建议**: 将 `replay_fallback` 调用同时挂到 `MemoryDatabase.__init__` 末尾（同样 wrap 在 try/except 里），或暴露 `MemoryDatabase.ensure_replayed()` 公开 API，便于 CLI 入口主动调用。

### [MINOR] 正确性：`Memory.from_row` / `Episode.from_row` 命名歧义

- **位置**: `nanobot/memory/models.py:101-129, 143-171`
- **描述**: 这两个 `from_row` 实际语义是「从 `to_row()` 序列化 dict 反构」，不是「从 SQLite row 反构」。生产路径 SQL row dict 字段值确为 JSON 字符串（schema DEFAULT 强制），实际兼容；但命名误导。若调用方传入已被 `_row_to_memory` 反序列化的 dict（list/dict 而非 str），会 `TypeError`。
- **建议**: 重命名为 `from_serialized_dict` 或在 docstring 明确「只接受 `to_row()` 产出」。

### [MINOR] 正确性：`_compute_incremental_start_index`「仅 1 条 user 消息」边界已覆盖

- **位置**: `nanobot/agent/hooks/memory_extraction.py:404-421`
- **测试覆盖**: `test_returns_after_second_last_user_message` / `test_single_user_message_returns_zero` / `test_empty_user_message_is_skipped` / `test_no_user_message_returns_zero` 4 个用例。**通过**。

### [MINOR] 一致性：`EpisodeSource.TOPIC_CHANGE` 已通过 `EpisodeSource` 整体暴露在 `__all__`

- **位置**: `nanobot/memory/__init__.py:43-50`
- **描述**: `EpisodeSource` 在 `__all__` 中，`TOPIC_CHANGE` 可通过 `EpisodeSource.TOPIC_CHANGE` 访问。与既有 4 个 source 排列一致。`_map_source("topic_change")` 映射由单测覆盖（test_extractor.py:1332）。**通过**。

### [MINOR] 性能：`_load_existing_memories` 全量预加载是 pre-existing

- **位置**: `nanobot/memory/extractor.py:887-907`
- **描述**: 增量抽取场景下若历史 RULE 记忆已达 `EXISTING_MEMORY_LIMIT=500`，全量加载存在 N×M N-Gram 比对成本。属于既有设计权衡（`_HIGH_SIMILARITY` 早退），不在本期范围。**不阻断**。

### [MINOR] 鲁棒性：fallback 文件名 `safe_kind` 路径清洗已正确

- **位置**: `nanobot/memory/extractor.py:1008`
- **描述**: `ts` 是 ISO 时间戳，`uid` 是 8 位 hex；调用点 `kind` 仅硬编码 `"memory"` / `"episode"` / `"backfill"`。**路径穿越风险不存在**。

### [NIT] 可读性：`MemoryExtractor.__init__` 嵌套 5 层

- **位置**: `nanobot/memory/extractor.py:609-630`
- **建议**: 抽出 `_resolve_fallback_dir(database) -> Path | None` 静态方法。

### [NIT] 死代码：`extractor.py` 末尾重复定义 `_parse_json_object`

- **位置**: `nanobot/memory/extractor.py:1300-1310`
- **描述**: 文件顶部 (380-408) 已有完整版，末尾又有一个简化版。疑似重构遗留死代码。**ruff 应能识别**（`F811` / `F401`）。

### [NIT] 设计偏离：`fallback_dir is None` 路径未覆盖单测

- **位置**: `nanobot/memory/extractor.py:609, 998-1004`
- **建议**: 补 1 个测试 `test_safe_write_returns_false_when_no_fallback_dir`。

### [NIT] 测试一致性：`_FakeProvider` / `_FakeRuntime` 在 3 个测试文件中重复定义

- **位置**: `tests/memory/test_extractor.py`、`tests/memory/test_memory_extraction_hook.py`、`tests/memory/test_hook_memory_extraction.py`
- **建议**: 后续重构时抽取到 `tests/memory/conftest.py` 或 `tests/memory/_fakes.py`。

---

## 通过项

### 改动文件清单（vs HEAD）

| 文件 | 改动 | 与方案一致性 |
|---|---|---|
| `nanobot/agent/hooks/memory_extraction.py` | +55 | ✅ T5 `_run_incremental_extraction` + `_compute_incremental_start_index` 落地；30s timeout 生效 |
| `nanobot/memory/database.py` | +69 | ✅ `fallback_dir` 字段 + `replay_fallback` 方法；启动时自动 mkdir |
| `nanobot/memory/extractor.py` | +326 | ✅ WU-1 / WU-3A / WU-2 / WU-3B / WU-4 全部落地 |
| `nanobot/memory/models.py` | +61 | ✅ `Memory.from_row` / `Episode.from_row` + `TOPIC_CHANGE` 枚举 |
| `tests/memory/test_extractor.py` | +440 | ✅ WU-1 (6) + WU-3A (9) + WU-2 (4) + WU-4 (3) |
| `tests/memory/test_hook_memory_extraction.py` | +21 | ✅ hook 集成补全 |
| `tests/memory/test_fallback.py` (new) | +354 | ✅ 7 个 fallback 用例 |
| `tests/memory/test_memory_extraction_hook.py` (new) | +188 | ✅ WU-2 hook 集成测试 |

### 关键不变量验证矩阵

| WU | 关键不变量 | 证据 |
|---|---|---|
| WU-1 | head+tail 双向保留 + 自动缩放 | `test_render_transcript_head_tail` 系列 + `test_render_transcript_custom_ratios` |
| WU-3A | 孤儿 ActionNode → success=False | `test_resolve_action_success_*` + `test_collect_action_nodes_orphan_*` |
| WU-2 | 话题切换命中调 `extract_incremental` 而非 `extract_session` | `test_topic_change_triggers_incremental_not_full_extraction` |
| WU-2 | 不调 LLM | `test_extract_incremental_no_llm_called` |
| WU-2 | 30s timeout | hook `_run_incremental_extraction` + `_compute_incremental_start_index` 4 边界用例 |
| WU-3B | DB 失败 → fallback JSON | `test_writes_file_on_db_error` |
| WU-3B | DB 成功 → 不落文件 | `test_returns_true_on_success` |
| WU-3B | replay 排空 + 失败保留 | `test_drains_files` / `test_keeps_failed_files` |
| WU-3B | `__init__` 自动 replay | `TestReplayOnInit` |
| WU-4 | 默认行为与原一致 + 自定义透传 | `TestBuildPromptMessages` 3 用例 |

### 与方案一致性（落地表）

| 方案要求 | 落地 | 验证 |
|---|---|---|
| head+tail 替代纯 tail（WU-1） | ✅ | `_render_transcript` head=0.4 / tail=0.45 |
| `extract_incremental` + `last_extracted_index`（WU-2） | ✅ | extractor.py:699-739 |
| `EpisodeSource.TOPIC_CHANGE` 枚举 | ✅ | models.py:35 |
| hook 主题切换接入增量（30s timeout） | ✅ | hook `_run_incremental_extraction` |
| 孤儿 ActionNode success 修正（WU-3A） | ✅ | `_resolve_action_success(has_result=False)→False` |
| DB 失败 fallback + replay（WU-3B） | ✅ | `database.fallback_dir` + `replay_fallback` + `_safe_write_with_fallback` |
| `_build_prompt_messages` 参数化（WU-4） | ✅ | `transcript_max_chars` / `transcript_head_ratio` 透传 |

**未引入的 OpenAkita 特性（按方案 §5 决策保持）**：

- ❌ smart_truncate sidecar 文件
- ❌ 三轨道并发（Episode/Profile/Experience）
- ❌ 去重改为 evolve
- ❌ MEMORY.md 容量治理
- ❌ 强制 `enable_thinking=False`

### 五轴检查摘要

| 轴 | 状态 | 关键点 |
|---|---|---|
| 正确性 | ⚠️ | `_safe_write_with_fallback` 失败后未 rollback（MAJOR #1）；`from_row` 命名歧义（MINOR）；其余通过 |
| 可读性 | ⚠️ | `__init__` 嵌套 5 层；末尾死代码 `_parse_json_object` (NIT) |
| 架构 | ⚠️ | `MemoryDatabase` 与 `MemoryExtractor` 之间 fallback 重放责任未对齐（MAJOR #3） |
| 安全 | ⚠️ | fallback JSON 未对 `content` 做 redact（MAJOR #2 / LLM05）；路径分隔符清洗已正确 |
| 性能 | ✅ | 既有 `_load_existing_memories` 全量加载是 pre-existing；增量抽取仅扫新消息，无 N+1 |

---

## 改进建议（非阻塞，按优先级）

| 优先级 | 项 | 处理建议 |
|---|---|---|
| MAJOR #1 | `_safe_write_with_fallback` 失败后 `conn.rollback()` 缺失 | 下个 WU 修复（`review-fix`） |
| MAJOR #2 | fallback JSON 内容 redact 缺失 | 下个 WU 修复（`review-fix`） |
| MAJOR #3 | `MemoryDatabase` fallback replay 仅挂在 `MemoryExtractor.__init__` | 已在集体测试报告 §5.2 披露；建议新增 issue 跟踪 |
| MINOR #1 | `from_row` 命名歧义 | docstring 补充 OR 重命名为 `from_serialized_dict` |
| NIT #1 | 死代码 `_parse_json_object` (extractor.py:1300-1310) | 下一次 lint pass 清理 |
| NIT #2 | `fallback_dir is None` 路径单测缺失 | 补 1 个用例 |
| NIT #3 | 测试 `_FakeProvider` 抽取到 conftest | 后续重构 |

---

**未修改任何文件。** 审查结论已完整输出。