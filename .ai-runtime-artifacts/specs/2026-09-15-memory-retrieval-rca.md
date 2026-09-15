---
route: systematic-debugging
artifact: rca
date: 2026-09-15
topic: memory-retrieval-broken
branch: feature/memory-system
status: root-cause-confirmed
---

# RCA：记忆检索恒返回空 + 首轮不检索

## 症状

1. 用户问「查一下我的记忆」→ agent 回答「记忆里目前是空的」。
2. 会话首条消息不会触发检索。
3. （用户提问）意图识别器是否有效。

## 证据

真实记忆库 `~/.nanobot/workspace/memory/state.db` **非空**：

| 表 | 行数 |
| --- | --- |
| memories | 4 |
| episodes | 3 |
| scratchpad | 5 |
| session_extraction_state | 2 |

memories 实际内容（示例）：
- `用户热爱创作，希望AI主动提供创作灵感`（preference, importance 0.9）
- `用户希望定期收到AI主动推送的灵感内容`（preference, 0.8）
- `用户的创作方向与健身视频相关（选题、脚本、剪辑）`（fact, 0.7）

即：**记忆已成功提取入库，但检索取不到。**

## 根因 1（致命）：RetrievalEngine 与 store 的接口不匹配 → 四路召回全废

- 装配点：`nanobot/cli/gateway_runtime.py:488`
  ```python
  _retrieval_engine = RetrievalEngine(store=_memory_services.database, brain=None)
  ```
  `_memory_services.database` 是 `MemoryDatabase` 实例（`nanobot/webui/memory_services.py:34`）。

- 实测 `MemoryDatabase` 公开 API 仅：
  `connect, db_path, ensure_schema, fallback_dir, init_schema, replay_fallback, workspace`
  —— **不含任何检索方法**。

- 而通道以**方法**形式调用 store：
  - `nanobot/memory/retrieval/channels/semantic.py:27` → `store.search_semantic_scored(query, limit=...)`
  - `nanobot/memory/retrieval/channels/recent.py:24` → `store.query_semantic(min_importance=..., ...)`

- 真实实现却是 `repository.py` 的**模块级函数**，首参为 `conn`：
  - `repository.py:589` `def search_semantic_scored(conn, query, *, limit=30)`
  - `repository.py:629` `def query_semantic(conn, *, min_importance, since_days, limit)`

- 异常被静默吞掉：`nanobot/memory/retrieval/engine.py:161-170`
  ```python
  sem, eps, rec, att = await asyncio.gather(..., return_exceptions=True)
  for chunk in (sem, eps, rec, att):
      if isinstance(chunk, Exception):
          continue          # ← AttributeError 在此消失
      candidates.extend(chunk)
  ```

### 复现（对真实库）

```
semantic     RAISED  AttributeError: 'MemoryDatabase' object has no attribute 'search_semantic_scored'
recent       RAISED  AttributeError: 'MemoryDatabase' object has no attribute 'query_semantic'
episodes     OK  n=0
attachments  OK  n=0
```

episodes/attachments 未报错，但对普通 query 是**条件性空操作**：
- episodes 仅在 query 含路径/文件名实体时召回（`episodes.py:34`），`创作` 无实体 → 直接返回 `[]`
- attachments 仅在 `intent=search_file` 或命中媒体词时召回（`attachments.py:41-45`），`创作` 不命中 → `[]`

**结论：`candidates` 恒为空 → `retrieve_with_ids` 恒返回 `("", [])`。**
影响**两条**检索路径（每轮自动注入 + `memory_search` 工具），二者共用同一 engine 实例。

## 根因 2：会话首条消息被前置门禁跳过

`nanobot/memory/retrieval/preprocessor.py:57`：

```python
if len(text) <= 12 and not recent_messages and not any(h in lowered for h in _KEEP_SHORT_HINTS):
    return True, "short_without_context"
```

首轮 `ctx.history` 为空（`loop.py:2151` 传 `recent_messages=list(ctx.history)`），
故任何 ≤12 字且不含 `?？吗呢吧啥` 的首条消息一律跳过。

### 复现

```
'查一下我的记忆'  recent_len=0 -> skip=True  reason=short_without_context
'查一下我的记忆'  recent_len=1 -> skip=False
'查一下我的记忆?' recent_len=0 -> skip=False
'你好'            recent_len=0 -> skip=True  reason=too_short
```

即使根因 1 修复，首轮仍不检索。

## 根因 3：memory_search 工具的 recent 恒为空

`nanobot/agent/tools/memory_search.py:82`：

```python
if session_key and hasattr(self, "_sessions"):
```

`__init__` 只赋值 `self._get_engine`；全仓库无任何代码给该工具实例注入 `_sessions`。
实测：`hasattr(t, '_sessions') is False`，`instance dict keys == ['_get_engine']`。

→ 工具路径 `recent` 恒为 `[]`，同样撞上根因 2 的 `short_without_context` 门禁
（LLM 通常传「记忆」「用户信息」这类短词），且拿不到会话上下文。

## 意图识别器的实际作用面

`classify_intent`（`nanobot/memory/intent.py`，纯正则，非 LLM）：

| 调用点 | 状态 |
| --- | --- |
| `memory_extraction.py:388` `_apply_immediate_focus`（T0） | **唯一生效**：CHAT 时跳过写 scratchpad `current_focus` |
| `topic_prefilter.py:59`（`TopicChangeGate` 内） | **死代码**：`_topic_gate` 在 `memory_extraction.py:246` 赋值后无人调用 |
| `memory_extraction.py:401` `_detect_topic_change`（T5） | **硬禁用**：函数体首行 `return` |
| `memory_extraction.py:425` `_judge_topic_change` | **死代码**：仅被已禁用的 T5 调用 |

另一个「意图识别器」`QueryDecomposer`（`retrieval/decomposer.py`，LLM 路径）：
`gateway_runtime.py:488` 以 `brain=None` 装配，全仓库唯一生产装配点，
故 `_do_decompose` 恒走 `_rule_decompose` 降级，**LLM 拆解路径从未启用**。
其产出的 `intent` 仅供 attachments 通道使用，而该通道对普通 query 本就是空操作。

结论：**两个「意图识别器」当前都不参与检索召回决策与提取决策**，
`classify_intent` 仅影响 T0 是否覆盖 `current_focus` 一个字段。

## 根因 4（提示词层）：system prompt 描述的是「另一套记忆系统」

用户假设「Agent 不知道 memory_search，是不是 system prompt 的问题」。实测**部分成立，但不是这个原因**。

### 工具确实注册了

装配链完整：
- `gateway_runtime.py:514` `retrieval_engine=_retrieval_engine` → 经 `from_config(**extra)` 转发（`loop.py:620-627`）→ `AgentLoop.__init__` `self._retrieval_engine`（`loop.py:327`）
- `loop.py:799` `attributes={"retrieval_engine": self._retrieval_engine}` → `MemorySearchTool.enabled()`（`memory_search.py:71-73`）为真
- 且 `identity.md:9,15` 明确写出：``Memory search tool: `memory_search` — use it to proactively retrieve...``
- 用户截图 "Searched memory · 2 searches" 即该工具被调用

### 真正的问题：prompt 把「长期记忆」定义成了文件

`nanobot/templates/agent/identity.md:7-10`（模板原文）：

```
- Agent profile: {{...}}/SOUL.md and {{...}}/USER.md (automatically managed by Dream — do not edit directly)
- Long-term memory: {{...}}/memory/MEMORY.md (automatically managed by Dream — do not edit directly)
- Memory search tool: `memory_search` — use it to proactively retrieve relevant long-term memories...
```

全仓库模板中**除这两行外没有任何文字描述三层 SQLite 记忆**（grep `记忆检索|主动检索|相关记忆|memory_search` 仅命中 identity.md）。
即：prompt 只文档化了 **Dream 文件记忆**（SOUL.md / USER.md / MEMORY.md / history.jsonl），
本分支在做的 **SQLite 三层记忆**（memories / episodes / scratchpad）零描述。

→ Agent 的心智模型是「长期记忆 = `memory/MEMORY.md` 那个文件」。
这正好解释了观测行为：工具返回空后，它去 `read_file memory/MEMORY.md`，看到未改动的模板，于是回答"记忆是空的"。

### 附带发现：USER.md / MEMORY.md 当前根本没进 prompt

实测 `ContextBuilder._is_template_content`（`context.py:336`）：

| 文件 | 是否模板 | 结果 |
| --- | --- | --- |
| `memory/MEMORY.md` | True | **被跳过，不注入**（`context.py:232`） |
| `USER.md` | True | **被跳过，不注入**（`_SKIPPABLE_DEFAULTS`，`context.py:107`） |
| `AGENTS.md` | False | 注入 |
| `SOUL.md` | False | 注入 |

两个文件自 2026-05-09 起未改动。故「上下文以 USER.md 为主」不成立——它们当前贡献为零。

## 待修复项（未实施）

| # | 位置 | 修复方向 |
| --- | --- | --- |
| 1 | `gateway_runtime.py:488` / `channels/*.py` / `repository.py` | 补齐 store 适配层：让 store 暴露 `search_semantic_scored` / `query_semantic` / `search_episodes` / `search_attachments`（或让 engine 持有 conn 并调模块函数） |
| 2 | `engine.py:167` | 通道异常不应静默丢弃，至少 `logger.warning` 记录，否则同类故障永不可见 |
| 3 | `preprocessor.py:57` | 首轮无历史时的 `short_without_context` 规则需放宽（如含记忆类关键词时不跳过） |
| 4 | `memory_search.py:82` | 注入 `_sessions`，或改为经 `ToolContext` 取会话（与 `long_task.py:61` 同模式） |
| 5 | `topic_prefilter.py` / `intent.py` | 确认是否保留；若 T5 长期关闭，`_topic_gate` + `_judge_topic_change` 属死代码 |
| 6 | `templates/agent/identity.md:7-10` | 重写记忆段：说明三层 SQLite 记忆才是主存储，界定 `MEMORY.md`（Dream）与 `memory_search`（SQLite）的分工与优先级 |
| 7 | 产品层（需你决策） | Dream 文件记忆与 SQLite 三层记忆**并行且互不同步**，是否收敛为一套 |

## Next

- 用户确认是否按上表排期修复（#1 #2 为同一故障，建议同批）。
- 修复前需先补一条**能失败的测试**：向临时库写入 1 条 memory，断言 `retrieve_with_ids` 返回非空块。
