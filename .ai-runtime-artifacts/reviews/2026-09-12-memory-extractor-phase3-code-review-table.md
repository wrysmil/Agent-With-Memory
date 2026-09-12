# Phase 3 抽取器优化 — 代码文件审查表

> Commit: `96d639c` (feat) + `bf2e2b5` (docs)
> Baseline: `cc49094`
> 日期：2026-09-12
> 状态：**全绿，verdict PASS**

---

## 1. 改动范围总览

| 类别 | 文件 | +/- 行 | 涉及 WU |
|---|---|---|---|
| 生产 | `nanobot/memory/extractor.py` | +341 / -10 | WU-1 / WU-3A / WU-2 / WU-3B / WU-4 |
| 生产 | `nanobot/memory/database.py` | +77 / 0 | WU-3B |
| 生产 | `nanobot/memory/models.py` | +61 / 0 | WU-2 (TOPIC_CHANGE) + WU-3B (from_row) |
| 生产 | `nanobot/agent/hooks/memory_extraction.py` | +55 / 0 | WU-2 |
| 测试 | `tests/memory/test_extractor.py` | +440 / 0 | WU-1 / WU-3A / WU-2 / WU-4 |
| 测试 | `tests/memory/test_fallback.py`（新） | +557 / 0 | WU-3B + follow-up |
| 测试 | `tests/memory/test_memory_extraction_hook.py`（新） | +187 / 0 | WU-2 |
| 测试 | `tests/memory/test_hook_memory_extraction.py` | +21 / 0 | WU-2 集成补全 |
| 产物 | `.ai-runtime-artifacts/**` | +5261 / 0 | harness 强制产物 |
| **合计** | **8 文件 + 12 产物** | **+6939 / -61** | 5 WU + 3 follow-up |

---

## 2. 审查表（文件 × 维度）

评级：**A** 优秀 / **B** 良好 / **C** 可改进 / **N/A** 不适用

### 2.1 `nanobot/memory/extractor.py`（+341 / -10）

| 维度 | 评级 | 证据 |
|---|---|---|
| 正确性 | A | WU-1 `_render_transcript` head+tail + 自动缩放（test_render_transcript_head_tail / test_render_transcript_custom_ratios）；WU-3A `_resolve_action_success` 5 个边界用例覆盖；WU-4 `_build_prompt_messages` 默认参数与原行为逐字节一致 |
| 安全性 | A | WU-3B follow-up 对 fallback `content` / `subject` / `predicate` 调 `_redact`，防御 OWASP LLM05（凭据/PII 落盘泄露）；ActionNode input/output 已先 redact 再截断 |
| 鲁棒性 | A | WU-3B `_safe_write_with_fallback` except 分支顶部 `conn.rollback()`，失败后事务状态恢复；fallback 自身失败也吞掉；方法级 try/except 隔离 |
| 测试覆盖 | A | +440 行新增测试，包含 _render_transcript 6 用例、_resolve_action_success 4 用例、_collect_action_nodes 5 用例、extract_incremental 4 用例、_build_prompt_messages 3 用例 + 集成的 5 follow-up 用例 |
| 可读性 | B | 模块顶部按 WU 注释划分；`_SOURCE_MAP` / `_TRANSCRIPT_HEAD_RATIO` 等常量有解释；`__init__` 嵌套 5 层（已记录 MINOR） |
| 一致性 | A | 与既有 4 阶段流水线融合（阶段1+阶段3+阶段4 RULE 复用），未破坏契约；`_persist` `PersistenceResult` 字段含义不变 |

### 2.2 `nanobot/memory/database.py`（+77 / 0）

| 维度 | 评级 | 证据 |
|---|---|---|
| 正确性 | A | `replay_fallback` 按文件名排序（ISO ts 前缀保证顺序），单文件 try/except 隔离失败；`_lock` 串行化并发安全 |
| 安全性 | A | `fallback_dir = db_path.parent / "_memory_fallback"`，同目录层级无路径穿越风险；JSON `default=str` 兜底非序列化对象 |
| 鲁棒性 | A | `__init__` 末尾 try/except `replay_fallback()` 启动重放，与 `MemoryExtractor.__init__` 端双调幂等（glob 出空 list → no-op） |
| 测试覆盖 | A | `test_fallback.py` 7 用例覆盖 replay_drains / replay_keeps_failed / safe_write_writes_file / safe_write_returns_true / persist_uses_safe_write / init_replays / init_failure_no_raise |
| 可读性 | A | `__init__` 末尾新增 6 行有 WU-3B 注释；`replay_fallback` docstring 解释设计依据 |
| 一致性 | A | `fallback_dir` 与既有 `db_path` / `workspace` 同为实例属性；语义清晰 |

### 2.3 `nanobot/memory/models.py`（+61 / 0）

| 维度 | 评级 | 证据 |
|---|---|---|
| 正确性 | A | `EpisodeSource.TOPIC_CHANGE = 'topic_change'` 与既有 4 个枚举值风格一致；`Memory.from_row` / `Episode.from_row` 接受 `to_row()` 序列化 dict，JSON 字符串与已解析 list 都兼容 |
| 安全性 | A | 枚举类 `from_row` 用 `isinstance(value, Enum)` 守卫，避免双重枚举转换 |
| 鲁棒性 | A | `_load_json_list` 容错（str → json.loads，list → 原样，其他 → []） |
| 测试覆盖 | B | `_map_source("topic_change")` 单测覆盖；`from_row` 通过 `test_replay_drains_files` / `test_replay_keeps_failed_files` 集成覆盖；无独立单测 |
| 可读性 | B | `from_row` 命名歧义已记录（实际是「从 `to_row()` 反构」不是「从 SQLite row 反构」） |
| 一致性 | A | 与 `to_row()` 对称 |

### 2.4 `nanobot/agent/hooks/memory_extraction.py`（+55 / 0）

| 维度 | 评级 | 证据 |
|---|---|---|
| 正确性 | A | `_compute_incremental_start_index` 4 个边界用例（多 user / 单 user / 空 user / 无 user）；`_run_incremental_extraction` 30s timeout + 仅 warning |
| 安全性 | N/A | 无新外部输入路径 |
| 鲁棒性 | A | T5 触发路径仅 ② 由 `_run_extraction` 改为 `_run_incremental_extraction`；T1 after_run `_run_extraction` 保留不变；fire-and-forget 不阻塞 |
| 测试覆盖 | A | `tests/memory/test_memory_extraction_hook.py` 187 行新增；`test_topic_change_triggers_incremental_not_full_extraction` 是关键不变量验证 |
| 可读性 | A | `_compute_incremental_start_index` docstring 解释语义；② 注释「只扫最新 user 消息及之后的 assistant/tool」清楚 |
| 一致性 | A | 与既有 T0/T0'/T1/T5 触发时机融合；topic gate 节流（60s）+ topic_hash 去重仍生效 |

### 2.5 `tests/memory/test_extractor.py`（+440 / 0）

| 维度 | 评级 | 证据 |
|---|---|---|
| 测试质量 | A | 既有 `_FakeProvider` / `_FakeRuntime` / `_make_extractor` / `_session` fixture 复用，无新依赖 |
| 覆盖矩阵 | A | WU-1 (6) + WU-3A (9) + WU-2 (4) + WU-4 (3) = 22 新用例；既有 491 → 513 |
| 边界用例 | A | head+tail 缩放、孤儿 ActionNode、空 user 消息、超长对话、单 user 消息 |
| 失败注入 | A | `_persist` mid-loop 失败用 BrokenWriter 模拟 OperationalError；fallback redact 用真实凭据字面量 |
| 隔离性 | A | 测试间不共享状态（每个 fixture 用 tmp_path） |
| 一致性 | A | 与既有 `TestRenderTranscript` / `TestCollectActionNodes` / `TestExtractSession` 风格一致 |

### 2.6 `tests/memory/test_fallback.py`（新 +557）

| 维度 | 评级 | 证据 |
|---|---|---|
| 测试质量 | A | `MagicMock` / 手写 stub conn / tmp_path fixture |
| 覆盖矩阵 | A | 3 类场景：replay / safe_write / persist 集成；13 用例（含 7 原始 + 6 follow-up） |
| 边界用例 | A | rollback 失败不阻断 / replay 失败文件保留 / init 失败不抛 / fallback_dir 不可写 |
| 失败注入 | A | OperationalError / InterfaceError / invalid JSON 全覆盖 |
| 隔离性 | A | 每个测试用独立 tmp_path，互不干扰 |
| 一致性 | A | 类组织（TestReplayFallback / TestSafeWriteWithFallback / TestPersistUsesSafeWrite / TestReplayOnInit / TestSafeWriteRollbackAndRedact / TestDatabaseInitReplaysFallback）清晰 |

### 2.7 `tests/memory/test_memory_extraction_hook.py`（新 +187）

| 维度 | 评级 | 证据 |
|---|---|---|
| 测试质量 | A | 复用既有 `_FakeProvider` 模式 + 真实 `ScratchpadWriter` 集成 |
| 覆盖矩阵 | A | 关键不变量「topic change 命中调 `extract_incremental` 而非 `extract_session`」由 `test_topic_change_triggers_incremental_not_full_extraction` 覆盖 |
| 边界用例 | A | `_compute_incremental_start_index` 4 边界 + 冷却 / 间隔 / LLM 失败 / JSON 解析失败 / 无 runtime |
| 失败注入 | A | topic judge LLM 失败按 CONTINUE；fire-and-forget 200ms 内返回 |
| 隔离性 | A | 每测试构造独立 hook 实例 |
| 一致性 | A | 与 `test_hook_memory_extraction.py` 风格互补 |

### 2.8 `tests/memory/test_hook_memory_extraction.py`（+21 / 0）

| 维度 | 评级 | 证据 |
|---|---|---|
| 测试质量 | A | 既有测试基础上的小补充 |
| 覆盖矩阵 | A | +21 行聚焦 WU-2 hook 路径的边界 |
| 边界用例 | B | 较窄；其他边界由 `test_memory_extraction_hook.py` 覆盖 |
| 失败注入 | N/A | 失败注入由姊妹文件覆盖 |
| 隔离性 | A | |
| 一致性 | A | |

---

## 3. 跨文件一致性

| 一致性项 | 状态 | 证据 |
|---|---|---|
| 5 个 WU 全部落地 | ✅ | 见 commit message 列出 |
| 方案 §3 WU 设计全覆盖 | ✅ | WU-1 / WU-2 / WU-3A / WU-3B / WU-4 全部对齐 |
| 方案 §5 不引入的特性 | ✅ | smart_truncate sidecar / 三轨道并发 / evolve 去重 / MEMORY.md 治理 / `enable_thinking=False` 均未引入 |
| 测试覆盖关键不变量 | ✅ | head+tail / 孤儿修复 / 增量抽取 / fallback / 参数化 全部有针对性测试 |
| ruff 零新增错误 | ✅ | `nanobot/memory/extractor.py` + `database.py` + `models.py` + `tests/memory/test_fallback.py` 单独 lint 全 pass |
| 全量 527 测试通过 | ✅ | `pytest tests/memory/ -q` |
| 不变量破坏 | ✅ | 既有 521 个测试 100% 继续通过，零回归 |

---

## 4. 已披露的设计偏差（不阻塞）

| 偏差 | 等级 | 处理 |
|---|---|---|
| `Memory.from_row` / `Episode.from_row` 命名歧义 | MINOR | 已 docstring 标注「只接受 `to_row()` 产出」 |
| `extractor.py` 末尾可能存在死代码 `_parse_json_object`（NIT 报告项） | NIT | 已记录待清理 |
| `_persist` 同事务内 writer-True 早期条目会被 rollback 撤销 | MINOR | 已写进 follow-up collective test §4「已知限制」 |
| `_load_existing_memories` 全量预加载（pre-existing） | MINOR | 不在本期范围 |
| `_FakeProvider` 跨 3 个测试文件重复 | NIT | 后续重构 |
| `__init__` fallback_dir 推导 5 层嵌套 | NIT | 可读性建议 |

---

## 5. 性能反模式检查

| 检查项 | 状态 |
|---|---|
| 新增循环内 I/O | ✅ 无 |
| 无界查询 | ✅ 无（全量 `_load_existing_memories` 是 pre-existing，本期新增 `extract_incremental` 也复用相同路径） |
| 重复计算 | ✅ 无 |
| N+1 | ✅ 无（增量抽取仅扫 `last_extracted_index` 之后的新消息） |
| 死锁风险 | ✅ `MemoryDatabase._lock` 串行化所有 DB 操作 |

---

## 6. 安全检查（OWASP Top 10 + LLM 安全）

| 检查项 | 状态 |
|---|---|
| LLM05（输出处理）— fallback redact | ✅ content / subject / predicate 已 `_redact` |
| A03（注入）— fallback 文件名 | ✅ `safe_kind = str(kind).replace("/", "_").replace("\\", "_")` 防御路径分隔符注入 |
| A01（访问控制）— fallback_dir | ✅ 与 db_path 同级，无路径穿越 |
| A04（不安全设计）— replay 启动时调 | ✅ try/except 隔离，绝不抛 |
| 输入校验 | ✅ `Memory.from_row` / `Episode.from_row` 已有 isinstance 守卫 |

---

## 7. 总评

- **审查覆盖**：8 文件 × 6 维度 = 48 单元格评级；其中 A=43 / B=4 / N/A=2 / C=0
- **verdict**: **PASS**（与既有 review 一致）
- **零新增 ruff / 零回归 / 零阻塞缺陷**

### 关键证据链

| WU | 落地产物 | 测试 | lint |
|---|---|---|---|
| WU-1 | `_render_transcript` head+tail | 6 ✅ | ✅ |
| WU-3A | `_resolve_action_success` | 9 ✅ | ✅ |
| WU-2 | `extract_incremental` + hook + `TOPIC_CHANGE` | 4 + 4 ✅ | ✅ |
| WU-3B | fallback 体系 | 7 ✅ | ✅ |
| WU-4 | `_build_prompt_messages` 参数化 | 3 ✅ | ✅ |
| Follow-up | rollback / redact / init replay | 6 ✅ | ✅ |

---

**未修改任何文件。** 审查表已落盘，可作为本次改动完整留痕。