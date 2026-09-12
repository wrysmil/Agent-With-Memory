# OpenAkita 源码调研报告 v2（深度版）

> 调研日期：2026-09-12（v1 中等深度 → v2 深度版）
> 调研范围：话题检测 / 提取 prompt / 双轨语义 / Scratchpad 重构 / 多路检索 / 长期巩固 / 关系图谱路由 / 存储层
> 代码路径：精确到行号（基于 `/Users/mima0000/Documents/学习-001/源码学习/openakita/`）
> 用途：为 nanobot 加强记忆提取提供事实依据（不复制代码、不"增强"调研对象）

---

## 一、调研对象与版本

```
/Users/mima0000/Documents/学习-001/源码学习/openakita/
├── src/openakita/core/_agent_runtime.py        # 主循环、话题检测、生命周期收尾
├── src/openakita/memory/
│   ├── manager.py                             # MemoryManager、end_session、编排
│   ├── extractor.py                           # 4 个 prompt + 4 个提取方法
│   ├── retrieval.py                           # 多路召回 + 重排序
│   ├── consolidator.py                        # 会话级批量整合
│   ├── daily_consolidator.py                  # 跨会话长期巩固
│   ├── unified_store.py                       # 统一存储层 + Observer
│   ├── storage.py                             # SQLite 主存储
│   ├── search_backends.py                     # FTS5 / Chroma 后端
│   └── relational/
│       ├── bridge.py                          # MemoryModeRouter (mode2 触发)
│       ├── graph_engine.py                    # 关系图谱查询
│       ├── encoder.py                         # 会话级节点/边编码
│       └── store.py                           # 关系图谱存储
└── （未读）lifecycle.py / retention.py / types.py / telemetry.py
```

---

## 二、生命周期与触发模型

### 2.1 触发源（5 种，全部汇聚到 `end_session`）

| 触发源 | 代码位置 | 备注 |
| --- | --- | --- |
| `chat_with_session` 正常完成 | `_agent_runtime.py:5773` | 单轮对话完成 |
| `chat_with_session_stream` 正常完成 | `_agent_runtime.py:5773` | 流式完成 |
| 上述方法抛异常（finally 路径）| `_agent_runtime.py:5781` | 异常兜底 |
| `Agent.shutdown()` | `_agent_runtime.py:8554` | 进程级关闭 |
| Orchestrator 检测 `idle_timeout` | `orchestrator.py:952` | 无进展超时 |
| AgentFactory 回收闲置 Agent | `factory.py:664` | 实例回收 |

### 2.2 端到端流程（`end_session` → `_finalize_session`）

`manager.py:1645-1819` 的核心骨架：

```python
def end_session(self, task_description, success, errors):
    session_id = self._current_session_id
    turns = list(self._session_turns)
    cited = self._consume_cited_memories()
    relational_pending_snapshot = list(self._relational_pending_nodes)
    self._relational_pending_nodes.clear()

    async def _finalize_session():
        # Step 1: 生成 Episode
        episode = await self.extractor.generate_episode(turns, session_id, source="session_end")
        self.store.save_episode(episode)
        ep_id = episode.id

        # Step 2a: 用户画像 + 引用评分（一次 LLM 调用双返回）
        items, scores = await asyncio.wait_for(
            self.extractor.extract_from_conversation(turns, cited_memories=cited),
            timeout=30.0,
        )

        # Step 2b: 任务经验
        exp_items = await asyncio.wait_for(
            self.extractor.extract_experience_from_conversation(turns),
            timeout=30.0,
        )

        # Step 3: 关联回填
        self.store.update_episode(ep_id, {"linked_memory_ids": saved_memory_ids})
        self.store.link_turns_to_episode(session_id, ep_id)

        # Step 4: Relational 编码（可选 mode2/auto）
        if mode in ("mode2", "auto"):
            result = await self.relational_encoder.encode_session(turn_dicts, ...)

    task = loop.create_task(_finalize_session())
    self._pending_tasks.add(task)
```

**关键编排细节**：

| 维度 | 设计 |
| --- | --- |
| 异步执行 | `asyncio.create_task` + `_pending_tasks` 登记 + `add_done_callback` 清理 |
| 失败隔离 | 每步独立 `try/except` + `record_health_event` 上报，不影响主聊天 |
| Episode ID 传递 | `ep_id` 在 Step 2a/2b 注入 `_save_extracted_item(episode_id=ep_id)` 形成关联 |
| Relational 兜底 | 失败仅警告，不影响 episode/memories 链路 |

---

## 三、话题切换检测（Topic Change Detection）

### 3.1 三道前置护栏（`_agent_runtime.py:4818-4825`）

```python
topic_changed = False
_channel = getattr(session, "channel", None) if session else None
_is_im = _channel and _channel not in ("cli", "desktop")    # [护栏1] 仅 IM
if _is_im and session and len(session_messages) >= 4:        # [护栏2] 历史 ≥4 轮
    topic_changed = await asyncio.wait_for(
        self._detect_topic_change(session_messages, message, session),
        timeout=10,                                           # [护栏3] 10s 超时
    )
    if topic_changed:
        session_messages.insert(0, {"role": "system", "content": "[上下文边界] ..."})
        _extraction_task = _loop.create_task(
            self.memory_manager.extract_on_topic_change()
        )
```

### 3.2 检测主体（`_agent_runtime.py:7192-7276`）

| 阶段 | 内容 |
| --- | --- |
| **输入保护** | `len(message.strip()) < 5` → 直接 False；session_messages 为空 → False |
| **多层上下文** | 当前任务 + 对话摘要（≤600）+ 近期对话（≤6 条，每条 ≤500）+ 新消息（≤800）|
| **Prompt 设计** | 单轮 user 消息，结尾"只输出 CONTINUE 或 NEW" |
| **判定策略** | `"NEW" in result and "CONTINUE" not in result`（防呆）|
| **失败处理** | 异常 → False + debug 日志 |

### 3.3 提取触发（`manager.py:1330-1375`）

```python
async def extract_on_topic_change(self) -> int:
    turns = list(self._session_turns)
    if len(turns) < 3:
        return 0
    cited = self._consume_cited_memories()
    items, scores = await asyncio.wait_for(
        self.extractor.extract_from_conversation(turns, cited_memories=cited or None),
        timeout=30.0,
    )
    self._session_turns.clear()    # ⚠️ 已知坑：30s 超时也清空
    return saved
```

### 3.4 已暴露问题（确认）

1. **每轮都过 LLM 判定**（IM 通道无其他节流）→ 高 token 成本
2. **fire-and-forget 无去重** → 连续两次 NEW 判定会启动两次后台提取
3. **30s 超时清空 turns** → 因 LLM 慢把累积对话直接丢弃

---

## 四、提取层：Prompt 与方法

### 4.1 4 个核心 prompt（`extractor.py`）

| Prompt | 行号 | 用途 | 输入 | 输出 |
| --- | --- | --- | --- | --- |
| `EXTRACTION_PROMPT_V2` | `:55-132` | 单轮提取（实体-属性结构）| 单 turn + 上下文 | type∈{PREFERENCE,RULE,FACT,SKILL,ERROR}，最多 2 条 |
| `EPISODE_PROMPT` | `:134-146` | 情节摘要 | 多 turn | summary/goal/outcome/entities/tools_used |
| `EXPERIENCE_EXTRACTION_PROMPT` | `:334-388` | 任务经验提取 | 多 turn | EXPERIENCE/SKILL/ERROR |
| `SCRATCHPAD_PROMPT` | `:148-168` | 工作记忆深度格式化 | 旧便签本 + episode.summary | 4 段 Markdown ≤2000 字符 |
| `CITATION_SCORING_SECTION` | `:380-388` | 引用评分（同次 LLM 拼装）| cited_memories | `citation_scores: [{memory_id, useful}]` |

**Prompt 设计哲学**：
- **宁少勿多**：EXTRACTION_PROMPT_V2 明确写"最多 2 条；NONE 是最常见的正确答案"
- **结构化输出**：所有 prompt 都是 JSON 输出，避免后续解析复杂化
- **同次调用复用**：引用评分塞进提取同次 LLM 调用，省一次往返

### 4.2 4 个提取方法

| 方法 | 行号 | 输入筛选 | 失败兜底 |
| --- | --- | --- | --- |
| `extract_from_conversation` | `:390-480` | 用户消息 ≥10 字符 | 返回 `([], [])` |
| `extract_experience_from_conversation` | `:482-510` | `assistant_turns >= 2` | 返回 `[]` |
| `generate_episode` | `:614-676` | turns 非空 | heuristic summary 回退 |
| `update_scratchpad` | `:743-781` | episode.summary 非空 | 用 episode 摘要追加 |

### 4.3 Episode 结构化捕获（`_extract_action_nodes`，`:678-717`）

```python
for turn in turns:
    for tc in (turn.tool_calls or []):
        nodes.append(ActionNode(
            tool_name=tc.get("name"),
            key_params={k: str(inp[k])[:200] for k in ("command","path","query","url","filename")},
            result_summary=tool_result_content[:200],
            success=not is_error,
            error_message=error_content if is_error else None,
            timestamp=turn.timestamp,
        ))
```

### 4.4 Episode 启发式回退（`:719-737`）

- `_generate_fallback_summary`：拼接用户消息前 3 条作为 summary
- `_extract_entities`：正则匹配 `r'[A-Za-z]:[\\/][^\s"\']+'`（路径）和 `r'[\w-]+\.(?:py|js|ts|md|json|yaml|toml|sh)\b'`（文件名）

---

## 五、多路召回引擎（`retrieval.py`）

### 5.1 总体设计

```python
class RetrievalEngine:
    W_RELEVANCE = 0.40
    W_RECENCY = 0.20
    W_IMPORTANCE = 0.20
    W_ACCESS = 0.20
    MIN_RERANK_SCORE = 0.35
```

**4 路召回 + 4 维重排序 + token 预算控制**。

### 5.2 Query 预处理（`MemoryQueryPreprocessor`，`:59-162`）

| 维度 | 处理 |
| --- | --- |
| **注入清洗** | 6 组正则剥除 `## 相关记忆`、`## 核心记忆`、`<vault-context>`、`<memory>`、`[系统提示]` 等块 |
| **控制词跳过** | `_CONTROL_ONLY`（好/嗯/ok/yes/no/stop 等）→ 直接 skip |
| **长度跳过** | `len(text) <= 3` 无上下文提示 → skip；`len(text) <= 12` 无 recent → skip |
| **保留短句** | `_KEEP_SHORT_HINTS`（这个/那个/刚才/.py 等）允许短句通过 |

### 5.3 4 路召回（`retrieve`，`:228-287`）

| 路 | 方法 | 输入 | 输出 |
| --- | --- | --- | --- |
| **语义搜索** | `_search_semantic(enhanced_query)` | 增强 query（含关键词拼接）| SearchBackend 结果 |
| **情节搜索** | `_search_episodes(enhanced_query)` | 同上 | Episode 列表（按实体/工具关联）|
| **时间搜索** | `_search_recent(days=3, query=enhanced_query)` | 3 天窗口 | 最近记忆 |
| **附件搜索** | `_search_attachments(query, keywords, intent)` | query + 关键词 + intent | 附件（文件/媒体）|

### 5.4 关键词拆解（`QUERY_DECOMPOSE_PROMPT`，`:176-188`）

- 单独一次 LLM 调用：自然语言 → 关键词列表（最多 6 个）
- 输出：`{"keywords": [...], "intent": "search_memory|search_file|general"}`
- **可被前置 IntentAnalyzer 的关键词覆盖**（节省一次 LLM 调用）

### 5.5 重排序（`:283`）

`score = 0.40 × relevance + 0.20 × recency + 0.20 × importance + 0.20 × access_frequency`

低于 `MIN_RERANK_SCORE = 0.35` 的候选被剔除。

### 5.6 Observer Hook（`:285`）

`_dispatch_on_retrieve_sync(query, ranked)` —— 检索即触发引用追踪，便于后续 `apply_citation_scores`。

---

## 六、长期巩固（`consolidator.py`）

### 6.1 增量写入（`:53-66`）

```python
def save_conversation_turn(self, session_id, turn):
    session_file = self.history_dir / f"{session_id}.jsonl"
    with open(session_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(turn.to_dict(), ensure_ascii=False) + "\n")
```

**设计**：每个会话一个 `.jsonl` 文件，**实时追加**，不存数据库。

### 6.2 单会话整合（`consolidate_session`，`:123-166`）

```python
async def consolidate_session(self, session_id) -> tuple[SessionSummary, list[Memory]]:
    turns = self.load_session_history(session_id)
    if not turns: return None, []

    # 1. 生成摘要（LLM）
    summary = await self._generate_summary(session_id, turns)

    # 2. 基于规则提取（每 turn）
    for turn in turns:
        memories.extend(self.extractor.extract_from_turn(turn))

    # 3. LLM 高级提取
    if self.brain:
        memories.extend(await self.extractor.extract_with_llm(turns, ...))

    # 4. 去重
    memories = self.extractor.deduplicate(memories, [])

    # 5. 摘要关联 memory ID
    summary.memories_created = [m.id for m in memories]

    self._save_summary(summary)
    return summary, memories
```

### 6.3 批量整合（`consolidate_all_unprocessed`，`:168-189`）

- 扫描 `history_dir/*.jsonl`
- 与 `summaries_file` 中已处理列表对比 → 未处理列表
- 适合 **空闲时段（凌晨）批量执行**

### 6.4 摘要生成（`_generate_summary`，`:191-271`）

**两层兜底**：
1. `len(turns) < 3` 或无 brain → 取第一条用户消息前 200 字符
2. LLM 生成 `task_description / outcome / key_actions / learnings / errors`

---

## 七、统一存储层（`unified_store.py`）

### 7.1 双后端架构（`:51-67`）

```python
class UnifiedStore:
    def __init__(self, db_path, search_backend, ...):
        self.db = get_shared_storage(db_path)        # SQLite 主存储
        if search_backend:
            self.search = search_backend
        else:
            self.search = create_search_backend(backend_type, ...)  # 默认 fts5 / chroma

        # FTS5 永远作为 fallback 准备
        if self.search.backend_type != "fts5":
            self._fts5_fallback = FTS5Backend(self.db)
```

**关键设计**：Chroma 启用时**额外 union 一次 FTS5 结果**（`:357-374`），避免向量索引异步未刷新导致新记忆静默漏掉。

### 7.2 Observer 模式（`:75-103`）

```python
self._observers: list[StoreObserver] = []

def register_observer(self, fn):
    with self._observer_lock:
        self._observers.append(fn)

def _fire(self, kind, payload):
    with self._observer_lock:
        observers = tuple(self._observers)
    for fn in observers:
        try: fn(kind, payload)
        except Exception: pass    # Observer 失败不影响主写入
```

- **事件**：`"upsert"` / `"delete"`
- **payload**：分别是 `SemanticMemory` 对象 / `memory_id: str`
- **典型订阅者**：`MemoryManager._memories` 缓存镜像

### 7.3 关键查询方法

| 方法 | 行号 | 用途 |
| --- | --- | --- |
| `search_semantic` / `search_semantic_scored` | `:306 / :329` | 主语义查询（含 FTS5 union 回退）|
| `search_episodes` | `:511` | 情节记忆搜索 |
| `get_recent_episodes(days=7, limit=10)` | `:515` | 时间窗口 |
| `save_episode` / `get_episode` | `:503 / :507` | Episode CRUD |
| `link_turns_to_episode` | `:530` | 反向关联 turn → episode |
| `search_attachments` | `:667` | 附件召回 |
| `get_scratchpad` / `save_scratchpad` | `:537 / :541` | Scratchpad CRUD |

### 7.4 范围（Scope）模型（`:208-226`）

4 元组：`(scope, scope_owner, user_id, workspace_id)`
- 默认 `("user", "", "default", "default")`
- `scope == "global"` 自动归一为 `"user"`
- **检索时强制过滤**：主查 + FTS5 fallback 都按相同 scope 过滤（`:388-...`）

---

## 八、关系图谱路由（`relational/bridge.py`）

### 8.1 三态路由（MemoryModeRouter）

```python
mode1: 仅 fragment 记忆（默认，零开销）
mode2: 仅关系图谱
auto:  按查询特征动态选择
```

### 8.2 模式 2 触发关键词（`:16-31`）

| 类别 | 正则 |
| --- | --- |
| **因果类** | `为什么|原因|导致|根因|因为|cause|reason|root.?cause` |
| **时间线类** | `过程|时间线|之前发生|上次|历史|previously|last time|history` |
| **跨会话类** | `类似问题|以前怎么|之前也|similar.?issue|done.?before` |
| **实体追踪类** | `关于.{1,10}的所有|.{1,10}的完整记录|everything.?about|all.?records.?of` |

**任一命中 → Mode 2**（因果/时序/跨会话/实体追踪类查询）；否则 → Mode 1。

### 8.3 关系图谱编码（`encoder.encode_session`）

- 输入：turn_dicts（role/content/tool_calls/tool_results）
- 输出：`Result(nodes, edges)`
- 失败仅警告，不阻塞主流程（`end_session` Step 4）

---

## 九、对照 nanobot 现状（v2 加强版）

| 维度 | OpenAkita | nanobot | 加强建议 |
| --- | --- | --- | --- |
| **会话结束事件** | 5 触发源汇聚 | ❌ 无（after_run 近似）| **P0**：新增 SessionEndEvent |
| **会话级 4 任务编排** | Episode + Track1+Track2 + Scratchpad | 仅 extract_session | **P0**：引入编排器 |
| **Episode 抽取** | ✅ 含 ActionNode + heuristic 回退 | ❌ prompt 未接通 | **P0**：接通 |
| **双轨语义** | ✅ 用户画像 + 任务经验 + 引用评分 | ❌ 单轨 | **P1**：新增 EXPERIENCE prompt |
| **Scratchpad 重构** | ✅ 会话结束 LLM | ❌ 常量未接通 | **P1**：接通 |
| **多路召回** | 4 路（语义/情节/时间/附件）| 单路（retrieval/） | 暂列参考（P2） |
| **4 维重排序** | relevance×0.4 + recency/importance/access×0.2 | ❌ 无 | 暂列参考（P2） |
| **Query 预处理** | 注入清洗 + 控制词跳过 + 长度跳过 | ❌ 无 | **P1**：加轻量预处理器 |
| **长期巩固** | 每日凌晨批量 consolidate | ❌ 无 | 暂列参考（P3） |
| **Observer 模式** | store.upsert/delete 回调镜像缓存 | ❌ 无 | 暂列参考（P3） |
| **存储双后端** | SQLite + FTS5/Chroma | SQLite + chromadb | 已对齐 |
| **模式 2 路由器** | 4 组正则触发关系图谱 | ❌ 无 | 不做（非本轮） |
| **话题检测** | IM 通道门 + 长度门 + LLM 判定 | 全部通道 + 节流 | **P1**：加预筛规则 + 最小间隔 |

---

## 十、可借鉴 / 不可借鉴清单（更新版）

### 可借鉴（确认可落地）
1. **廉价前置规则**（消息 < 5 字符 / 控制词 / 长度跳过）—— 零成本挡大量误判
2. **Episode `_extract_action_nodes` 结构化捕获** —— 比纯文本拼接有信息密度
3. **`update_scratchpad` 输入结构**：旧便签本 + `episode.summary`，依赖链清晰
4. **失败兜底 `record_health_event`** —— 把失败归类上报，方便排查
5. **同次 LLM 调用内做"抽取 + 评分"** —— 省一次往返
6. **Episode + 双轨 + Scratchpad 三步走 + 关联回填** —— 完整依赖链
7. **Observer 模式镜像缓存** —— store 写入即时同步内存缓存

### 反向规避（OpenAkita 已知坑）
1. **30s 超时清空 turns** —— 改为超时不清空，累积留给下次会话级提取
2. **fire-and-forget 无去重** —— 加最小间隔（≥60s）+ topic_hash 去重
3. **每轮过 LLM 判定话题** —— 加廉价预筛（消息 < 5 字符 / 控制词 / 长度突变）

### 不可直接照搬
1. **IM 通道门**：nanobot 通道形态不同（WebUI/CLI/IM 共存）
2. **Relational 模式 2**：本轮明确排除
3. **每日凌晨批量巩固**：依赖 cron 基础设施，本轮不做

---

## 十一、调研产物边界

- ✅ **已读**：`extractor.py`（55-781）、`manager.py`（1311-1819）、`retrieval.py`（1-319）、`consolidator.py`（1-300）、`unified_store.py`（1-579）、`relational/bridge.py`（1-90）
- ❌ **未读**：`lifecycle.py` / `retention.py` / `daily_consolidator.py` / `search_backends.py` / `relational/graph_engine.py` / `relational/encoder.py`
- 🚫 **本调研不复制任何代码到 nanobot**，仅做架构/接口/约束的事实记录与对比