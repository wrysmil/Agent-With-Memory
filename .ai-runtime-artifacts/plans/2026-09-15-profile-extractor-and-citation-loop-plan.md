---
artifact: implementation-plan
route: superpowers:writing-plans
source:
  - .ai-runtime-artifacts/specs/2026-09-12-openakita-nanobot-improvements.md §4 S3
  - .ai-runtime-artifacts/research/2026-09-12-openakita-source-survey.md:155（引用评分用途）
  - .ai-runtime-artifacts/research/2026-09-12-openakita-source-survey-v2.md:249（apply_citation_scores）
  - 本次会话诊断（2026-09-15）
created_at: 2026-09-15
status: draft
approved: false
---

# Plan：接通 ProfileExtractor + 引用评分闭环

## 1. 问题（已取证）

`ProfileExtractor` / `ExperienceExtractor` 是 S3 的产物，**完整实现 + 单测 + `__init__.py` 导出，
但生产代码零调用点**：

```
nanobot/memory/__init__.py:12,23          裸 re-export
nanobot/agent/hooks/memory_extraction.py:354  一句 docstring
```

成因：plan 的 Task 3 / Task 4 的 DoD 只到「模块自己的单测通过」，**没有任何一步负责接线**；
而 Task 7（编排器）走的是另一套接口 `MemoryExtractor.extract_user_profile()`
（spec §4.3 也确实这么定义），实现者在那里留了 TODO 占位。两份产物在 plan 阶段就对不上。

### 1.1 引用评分闭环：4 环全断

| # | 环节 | 现状 | 证据 |
| --- | --- | --- | --- |
| 1 | 注入块暴露 memory_id | 断 | `formatter.py:68-73` 只输出 content/type/score/reason |
| 2 | 传 `cited_memories` 给提取器 | 断 | `orchestrator.py:85` 硬编码 `cited=None`；stub 忽略该参数 |
| 3 | 评分落库 | 断 | schema 无引用评分列 |
| 4 | 检索读评分调排序 | **已铺好** | `reranker.py:18` `_W_ACCESS = 0.20`，来源 `semantic.py:11 _access_freq(access_count)` |

**决定性事实：`access_count` 全仓库没有任何一处写入。** 所有出现都是 schema 定义 /
`to_row()` / `_row_to_memory()` / WebUI 展示——没有一个 UPDATE、没有一个 `+= 1`。

推论：**重排公式 20% 的权重，对所有记忆恒为 0。** 第 4 环是唯一已铺好、只差最后一米的闭环。

> 注：spec §4.2.2 只定义了「产出 `citation_scores`」和 JSON 格式，**从未定义消费方**。
> 「评分回流到排序」的思路出自 OpenAkita（research v2:249 `apply_citation_scores`），
> 但 OpenAkita 内部具体怎么应用，调研文档**没有展开**。本 plan 选择已知有落点的
> `access_count`，不臆造其他机制。

---

## 2. 范围

**做：**
- WU-A：让 `ProfileExtractor` 真正跑在 idle 流水线上（顺带解决「同一偏好被反复抽成多条」）
- WU-B：接通引用评分闭环（注入块暴露 ID → 评分 → `access_count`）

**不做：**
- 不恢复 `SessionEndOrchestrator` 的生产触发（WU-2 刚移除，idle 定时器已承担常态提取）
- 不改 `ProfileExtractor` 已有的 LLM 调用语义之外的行为
- 不实现「importance 调整 / 记忆衰减」——文档未定义，无落点

---

## 3. WU-A：ProfileExtractor 接进 idle 流水线

### 3.1 关键取舍（需确认）

`ProfileExtractor.extract()` 自己拼 prompt（`_format_conv_lines`：最后 30 条、每条 ≤1500 字符）；
现有 semantic track 用 `_build_prompt_messages`，**额外喂阶段1 系统提取结果**
（action_nodes / 规则信号命中 / scratchpad 快照，见 `extractor.py:1107-1115`）。

三选一：

| 选项 | 做法 | 代价 |
| --- | --- | --- |
| **A1（推荐）** | `_llm_extract` 的 semantic 路改由 `ProfileExtractor.extract()` 承担；给 `extract()` 加可选 `system_context` 参数，把它拼进 prompt | 改 `ProfileExtractor` 签名 + 保留阶段1 上下文；`_apply_filters` 输入类型需从 `LLMMemoryItem` 适配 |
| A2 | 完全替换，丢掉阶段1 上下文 | 提取质量下降（action_nodes 是 episode 回溯的依据） |
| A3 | 不做替换，只把 `merge_profile_incremental` 接进 `_persist` 前 | `ProfileExtractor.extract()` 仍是死代码，未满足「接它」 |

### 3.2 A1 的落点

- `ProfileExtractor.extract()`：新增可选 `system_context: SystemExtractionResult | None`，
  非空时在 prompt 尾部拼「阶段1 系统提取结果」段（复用 `_build_prompt_messages` 的措辞）
- `_llm_extract` semantic 路：改为 `self._profile_extractor.extract(...)`；
  `experiences` 字段**在同一 payload 内解析**（prompt 本来就双轨输出，见 `prompts.py:10-75`），
  避免第二次调用
- 阶段3：`_apply_filters` 接受 `ProfileItem`（或先适配成 `LLMMemoryItem`，改动更小）
- 阶段4 **新增合并步骤**：入库前按 `subject + predicate` 查已有记忆，
  命中则走 `merge_profile_incremental`：
  - `action == "update"` → `update_memory()` 更新 content/importance，不新建
  - `action == "keep_old_with_conflict"` → 保留旧值，`conflicts_with` 写进 `metadata` JSON
    （schema 无该列，`metadata TEXT DEFAULT '{}'` 可承载）
  - `action == "create_new"` → 正常 INSERT

### 3.3 影响

- **正向**：修复「同一句偏好被反复抽成多条」（用户此前报的「情节记忆重复」同类问题）
- **风险**：合并改变写入语义。若 `subject`/`predicate` 抽取不稳（LLM 可能留空），
  空 subject 会被误判为「命中」→ 需在合并前要求 `subject` 与 `predicate` **均非空**

### 3.4 DoD

- 同一 subject+predicate 的画像二次提取 → `memories` 表**行数不增**，content/importance 被更新
- 语义冲突（否定词反向）→ 旧值保留 + `metadata.conflicts_with` 记录新值 id
- `subject` 或 `predicate` 为空 → 退化为新建，不误合并
- `pytest tests/memory/ -q` 全绿

---

## 4. WU-B：引用评分闭环

### 4.1 管道（`cited_memories` 需跨 2 分钟送达）

idle 提取发生在检索之后 120 秒，链路为：

```
[Turn N] 检索
  engine.retrieve() → markdown 注入块
  @需改：同时暴露本次注入的 memory_ids
        ↓ 存到 TurnContext
  _run_agent_loop(..., retrieved_memory_section=...)
        ↓
  turn_hooks.py:54  AgentTurnHookContext(metadata=spec.metadata)
        ↓
  hook 工厂读 metadata → MemoryExtractionHook(cited_memory_ids=[...])
        ↓ hook 实例持有（per-turn，跨 idle 存活）
[Turn N + 120s] idle 定时器
  _run_idle_extraction → extract_incremental → ProfileExtractor.extract(cited_memories=[...])
        ↓ 返回 citation_scores
  useful=true → access_count += 1（repository.update_memory）
        ↓
  reranker._W_ACCESS 自动吃到（已铺好）
```

### 4.2 文件清单

| 文件 | 改动 |
| --- | --- |
| `nanobot/memory/retrieval/formatter.py` | `format()` 输出 dict 增加 `memory_id` |
| `nanobot/memory/retrieval/engine.py` | `_render_injection_block` 渲染 ID；新增 `retrieve_with_ids()` 返回 `(block, ids)`，`retrieve()` 委托（**保持现有签名兼容**，避免动 2 个调用方 + 测试） |
| `nanobot/agent/context.py` | `_build_memory_section` 增加返回 ids 的伴生方法或改内部实现 |
| `nanobot/agent/loop.py` | `_compute_retrieval_section` 捕获 ids → 存入 `TurnContext` 新字段 → 传入 `spec.metadata` |
| `nanobot/agent/hooks/memory_extraction.py` | 工厂从 `context.metadata` 读 `cited_memory_ids`；`MemoryExtractionHook` 新增该构造参数并透传到 `_run_idle_extraction` |
| `nanobot/memory/extractor.py` | `extract_incremental` / `run_idle_extraction` 接受 `cited_memories` 并传给 ProfileExtractor |
| `nanobot/memory/repository.py` | 新增 `bump_access_count(conn, memory_id, delta=1)` |
| `nanobot/agent/context.py` 调用方 | 把 scores 写回 DB（或由 extractor 在 `_persist` 后统一处理，二选一，倾向后者以便事务化） |

### 4.3 失败与边界

- 注入块**不含 ID** 或 `cited_memories` 为空 → 跳过评分段，prompt 与现在**逐字一致**
- LLM 返回的 `memory_id` 不在传入集合内 → **丢弃**（防幻觉写错记忆）
- `access_count` 自增失败 → warning，不影响 memories 写入
- 检索关闭 / 引擎未接线 → 全链路 no-op

### 4.4 DoD

- 注入块 markdown 里能读到 `memory_id`
- 伪 provider 返回 `useful=true` → 对应 memory 的 `access_count` 从 0 变 1
- 伪 provider 返回集合外的 id → 不写库
- `reranker` 对 `access_count=0` 与 `=1` 的候选给出不同 `composite_score`
- `pytest tests/memory/ -q` 全绿

---

## 5. 验证口径

1. `uv run --no-sync pytest tests/memory/ tests/config/ -q` 全绿
2. `uv run --no-sync ruff check <改动文件>` 干净
3. **端到端**：重启 gateway → 发含偏好的消息 → 等 2 分钟 →
   ```sql
   SELECT count(*) FROM memories;           -- 非 0
   SELECT count(*) FROM episodes;           -- 非 0
   SELECT content, subject, predicate, access_count FROM memories;
   ```
   重复发送同一偏好 → `memories` 行数不增（WU-A 生效）
4. 日志出现 `idle extraction for session ...: N new messages -> X memories, Y episodes`

---

## 6. 风险登记

| 风险 | 等级 | 缓解 |
| --- | --- | --- |
| A1 改动 semantic 路，`_apply_filters` 输入类型迁移引入回归 | 中 | 先加适配层（`ProfileItem` → `LLMMemoryItem`），保持 `_apply_filters` 不动 |
| subject/predicate 抽取为空导致误合并 | 中 | 合并前置条件：两者均非空 |
| `engine.retrieve()` 返回类型变更影响 2 个调用方 | 中 | 不改签名，新增 `retrieve_with_ids()` |
| 评分把「检索到但没用」的记忆也计入 | 低 | 这正是 LLM 评分存在的意义；若幻觉率高，可退回「仅按注入计数」的简化版 |
| 存量 `last_count=21` 游标导致老会话不重抽 | 低 | 需要时 `reset_session_extraction_state` |

---

## 7. 交付顺序建议

WU-A 与 WU-B **互相独立可交付**。建议 **先 A 后 B**：

- A 直接解决「重复记忆」，且改动集中在 `extractor.py` + `profile_extractor.py`
- B 跨 6 个文件、动到检索管道，风险面更大，适合 A 验证通过后再做
