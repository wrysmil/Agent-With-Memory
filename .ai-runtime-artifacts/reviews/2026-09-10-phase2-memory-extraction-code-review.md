# Phase 2 Code Review

**审查者**: reviewer (independent, fresh-context)
**日期**: 2026-09-10
**范围**: WU-05/06/07/08/09 diff（`d72d474..21a1d3c`）
**verdict**: **no-go**（2 个 Critical finding 与 spec 偏离）

---

## 1. Critical（必须修复）

### C-1: `SCRATCHPAD_FORMAT_PROMPT` 定义后从未调用 — T1/T2 LLM 深度格式化路径缺失
- **文件**: `nanobot/memory/prompts.py:130-166` 定义常量；`nanobot/memory/extractor.py:43-46` 只 import 语义/情节两个 prompt；`nanobot/agent/hooks/memory_extraction.py` 无相关调用。
- **设计依据**: 阶段二设计 §4.3、§6.2 T1/T2 路径、§10 Task 13 明文要求实现 `MemoryExtractor._format_scratchpad()`，调 `SCRATCHPAD_FORMAT_PROMPT` → 解析 Markdown 四段 → `upsert_scratchpad()`。
- **复现**: `grep -rn "SCRATCHPAD_FORMAT_PROMPT" nanobot/` 仅出现在 `prompts.py` 与 `tests/memory/test_prompts.py`；`extractor.py` 与 `memory_extraction.py` 完全没有调用点。
- **影响**: T1/T2 路径退化为系统直写（仅写 `action_nodes` + 空 summary），与 plan §6.2 描述的"LLM 深度格式化 → Markdown 四段重组"行为不一致。`scratchpad.active_projects / open_questions / next_steps` 三字段永不会被 LLM 整理。
- **修复方向**: 在 `MemoryExtractor` 新增 `_format_scratchpad(scratchpad_snapshot, action_nodes) -> ScratchpadEntry` 私有方法；在 `extract_session` 的阶段 4c 之后（或独立阶段 4d）调用；解析 Markdown 四段，失败兜底走系统直写；为 `scratchpad_writer.update_focus` 提供 Markdown 模式或在 writer 中加 `replace_with_markdown(markdown)` 方法。

### C-2: T5 话题切换触发完整 `extract_session`（含 episode LLM 调用），与 plan §2.2 "仅做 semantic 提取" 偏离
- **文件**: `nanobot/agent/hooks/memory_extraction.py:315-319`
  ```python
  _spawn_background_task(self._run_extraction(transcript, source="session_end"))
  ```
  其中 `_run_extraction` → `extractor.extract_session` → 阶段 2 并发调 `SEMANTIC + EPISODE` 两路 LLM（`extractor.py:567-571`）。
- **设计依据**: 阶段二设计 §2.2 原文"② fire-and-forget 调用 Extractor 仅做 semantic 提取（不调 episode/scratchpad 避免重复工作）"。
- **影响**: 每次话题切换都触发 **2 次** LLM 调用（semantic + episode），而非计划的 1 次；长会话成本翻倍且触发不必要的 episode INSERT。
- **修复方向**: 在 `MemoryExtractor.extract_session` 增加 `tracks: Sequence[str] = ("semantic", "episode", "scratchpad")` 参数（或 `mode: "full" | "semantic_only"`）；T5 路径传 `tracks=("semantic",)`；或新增 `extract_semantic_only(session)` 入口。

---

## 2. Major（应修复）

### M-1: `compute_content_hash` docstring 与实现不一致
- **文件**: `nanobot/memory/filters.py:151-158`
- **docstring**: "Returns: 40 位 SHA1 十六进制字符串（取 SHA256 前 20 字节，hexlify 产生 40 字符）"
- **实现**: `return hashlib.sha1(normalized.encode("utf-8")).hexdigest()`
- **影响**: docstring 提到 SHA256 但实际是 SHA1；未来维护者可能据此误改实现。哈希长度一致（40 字符），功能不受影响但文档误导。
- **修复方向**: 将 docstring 改为 "Returns: SHA-1 hex digest of `content|subject|predicate` (40 chars)."

### M-2: `_render_transcript` 尾部截断丢上下文
- **文件**: `nanobot/memory/extractor.py:430-444`
  ```python
  if len(joined) > max_chars:
      joined = "..." + joined[-max_chars:]
  ```
- **影响**: 长会话（>8000 字符）头部被丢弃，LLM 看不到会话早段的关键事实（用户身份、初始任务背景），导致提取质量下降。这是 LLM 输入层的正确性问题。
- **修复方向**: 改为头尾双向采样（head 2000 + tail 6000），保留会话开场 + 末尾；或改用分桶摘要（首条/末条/中间）。

### M-3: T5 跨轮未冷却（无代价控制）— 每轮 LLM judge
- **文件**: `nanobot/agent/hooks/memory_extraction.py:198, 281-322`；模块 docstring §「代价」已披露。
- **影响**: 用户消息数 ≥ 4 后，**每轮** `before_iteration` 都会调一次轻量 LLM（`TOPIC_CHANGE_TIMEOUT=10s`）。模块 docstring 已说明实例跨轮重建使 `_next_check_count` 重置。
- **修复方向**: 在 `SessionManager` 加 `topic_change_cursor: dict[session_key, last_check_count]` 持久化字段（与 session metadata 同寿命）；T5 判定后把 `count + 4` 写回；或退化为：T5 仅在 `before_iteration` 触达 session 边界时跑一次（即 first-iteration-of-turn 而非每 LLM iteration）。

### M-4: `Memory.source_episode_id` 反向回填缺失
- **文件**: `nanobot/memory/extractor.py:765-789`
- **设计依据**: plan §10 Task 5 明确要求 "episode 写入后，对应 memory 的 `source_episode_id` 被回填（虽然 Phase 2 暂不强制 FK 校验）"。当前只实现 `episode.linked_memory_ids` 正向，`memory.source_episode_id` 仍为 `None`。
- **影响**: 反向查询 "某 episode 触发了哪些 memory" 必须走 `episode.linked_memory_ids` JOIN，效率差且新写入后无法回填历史 memory 索引。
- **修复方向**: `_persist` 阶段 4c 写完 episode 后，遍历 `linked_ids`，对每个 ID 执行 `UPDATE memories SET source_episode_id = ? WHERE id = ?`。因 SQLite 无跨表事务保护，单条失败仅 warning。

### M-5: `_load_existing_memories` 多类型串行查询
- **文件**: `nanobot/memory/extractor.py:670-690`
- **影响**: 候选 memory 跨 6 个类型（`MemoryType` 6 个枚举值），则每轮 `extract_session` 阶段 3 前发 6 次 SQL（每次 `limit=500`）。批量会话下 DB 压力叠加。
- **修复方向**: 用 `repository.list_memories(conn, workspace_id=..., type__in={...}, limit=...)` 一次性取回，或把 6 个查询并发 (`asyncio.gather` 在线程池执行)。当前 `_load_existing_memories` 是同步调用无锁，并发需放到 `asyncio.to_thread` 或 DB 层加重入保护。

---

## 3. Minor（可选）

### S-1: 防污染过滤顺序与 plan §5.2 编号不一致
- **文件**: `nanobot/memory/extractor.py:692-735`
- **现状**: 实现顺序为 task_artifact → ai_self_talk → type 校验 → priority 校验 → 精确哈希 → N-Gram。
- **plan §5.2**: L1 精确哈希 → L2 N-Gram → L3 任务流水账正则。功能正确，但编号顺序与 spec 不同，读者对照 spec 时会困惑。
- **修复方向**: 改 plan §5.2 编号顺序对齐实现，或保留现有顺序并在 §5.2 补注 "实现重排了执行顺序——内容级过滤前置、哈希/相似度后置，理由：先低成本过滤减少哈希计算量"。

### S-2: `MemoryExtractor.LLM_TIMEOUT` / `EXISTING_MEMORY_LIMIT` 类级常量不可按实例配置
- **文件**: `nanobot/memory/extractor.py:456-459`
- **影响**: 单测可覆盖实例属性，但生产环境无法调（例如不同 session 设置不同超时）。
- **修复方向**: 改为 `__init__` 参数（默认值与现有常量一致）。

### S-3: `_BACKGROUND_TASKS` 模块级强引用集合在长会话下增长风险
- **文件**: `nanobot/agent/hooks/memory_extraction.py:79-85`
- **现状**: T5 每次话题切换 fire-and-forget 一次，done_callback 自动 discard；正常情况下 set 大小有界。
- **影响**: 如果 LLM 持续失败且 `cancel()` 后回调不触发（极少见），集合会增长。
- **修复方向**: 加 `maxsize` 保护或弱引用。

### S-4: `ScratchpadWriter.update_focus` 忽略 `session_key` 参数
- **文件**: `nanobot/memory/scratchpad_writer.py:75-125`；docstring §"注意：session_key 参数保留用于未来扩展（当前不使用）"。
- **影响**: 当前 scratchpad 是 user/workspace 级，确实不需要 session_key；但参数保留会让调用方误以为支持 per-session focus。
- **修复方向**: 在 `MemoryExtractionHook` 层做映射（如 `session_key → (user_id, workspace_id)`），writer 接受 user/workspace 二元组。

### S-5: `_normalize_outcome` 仅映射 `success`，其他历史别名未覆盖
- **文件**: `nanobot/memory/extractor.py:385-395`
- **影响**: LLM 偶尔输出 `done` / `succeeded` 等变体会被 ValueError 兜底为 COMPLETED；属可接受降级。
- **修复方向**: 可扩展 `mapping = {"success": "completed", "succeeded": "completed", "done": "completed"}`。

---

## 4. 维度评分

| 维度 | 评级 | 备注 |
| --- | --- | --- |
| Correctness | ⚠️ | C-1（scratchpad LLM 路径缺失）/ C-2（T5 多调一次 LLM）/ M-2（截断丢上下文）/ M-4（反向回填缺失）均偏离 plan |
| Readability | ✅ | 模块 docstring 充分、命名一致（`action_nodes / rule_signals / LLMExtractionResult`）、dataclass 字段顺序与 plan §8.4 一致 |
| Architecture | ✅ | 模块边界清晰（`MemoryExtractor` / `MemoryExtractionHook` / `ScratchpadWriter` / `filters` / `intent`）；opt-in 装配（`memory_extraction_enabled=False`）合理；工厂模式解耦 |
| Security | ✅ | SQL 走参数化（`?` 占位符，无字符串拼接）；`MemoryDatabase` 用 `threading.Lock` 串行化（`database.py:144-157`）；`workspace_id / user_id` 作用域在 `_persist` 显式传入；错误日志不含 LLM 原始 prompt/response 全文 |
| Performance | ⚠️ | T5 每轮 judge LLM（M-3）、`_load_existing_memories` 多查询（M-5）、`_render_transcript` 尾部截断（M-2）；其余性能开销可控（每轮 1 次 30s LLM 超时） |

---

## 5. 五轴证据

- **已读文件**:
  - `nanobot/memory/extractor.py`（836 行全量）
  - `nanobot/memory/filters.py`（219 行全量）
  - `nanobot/memory/intent.py`（147 行全量）
  - `nanobot/memory/prompts.py`（205 行全量）
  - `nanobot/memory/scratchpad_writer.py`（179 行全量）
  - `nanobot/agent/hooks/memory_extraction.py`（504 行全量）
  - `nanobot/agent/loop.py:1-1950`（含 `_wire_memory_extraction`）
  - `nanobot/session/manager.py:1685-1745`（含 `set_delete_session_observer`）
  - `nanobot/agent/autocompact.py:1-150`（含 `_run_quick_facts`）
  - `nanobot/memory/database.py:140-180`（`connect()` 线程锁）
  - `nanobot/memory/models.py:1-50`（enum 定义）
  - `nanobot/memory/repository.py:52-220`（`add_memory / add_episode / list_memories`）
  - `tests/memory/test_extraction_integration.py`（478 行全量，8 用例）
- **已运行**: `grep` 系列命令定位 prompt 调用点 / SQL 接口 / session observer 入口
- **未运行**: 完整 pytest（按 review 职责边界，未亲自跑测试；仅静态审查）

---

## 6. 结论

**verdict: no-go**

修复 C-1、C-2 后再行复审。M-1 ~ M-5 建议在同一 review-fix 任务中合并处理以避免反复修改 `_persist`。S-1 ~ S-5 不阻塞 merge。

### Skills 使用
- **已加载**: `code-review-and-quality`（五轴审查骨架）, `agent-skills:code-reviewer`（评审方法论）
- **已跳过**: `requesting-code-review`（非发起 review，而是执行 review）, `security-and-hardening`（本次重点为 correctness/security/performance 五轴全覆盖，安全审计由独立 WU 处理）