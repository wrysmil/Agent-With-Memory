# OpenAkita 源码调研报告（中等深度）

> 调研日期：2026-09-12
> 调研范围：话题检测 / 提取 prompt / 双轨语义 / Scratchpad 重构
> 代码路径：所有引用精确到行号（基于 `/Users/mima0000/Documents/学习-001/源码学习/openakita/`）
> 用途：为 nanobot 改进提案提供事实依据（不复制代码、不"增强"调研对象）

---

## 一、目录与关键文件

```
src/openakita/
├── core/_agent_runtime.py        # Agent 主循环、话题检测、生命周期收尾
└── memory/
    ├── manager.py                # MemoryManager、end_session、extract_on_topic_change
    └── extractor.py              # 4 个 prompt + 4 个提取方法
```

辅助模块（未在本轮调研深度内）：`relational/*`（关系图谱编码）、`unified_store.py`、`retrieval.py`。

---

## 二、话题切换检测（Topic Change Detection）

### 触发位置
`_agent_runtime.py:4818-4857` 是**唯一**的触发入口：

```python
topic_changed = False
_channel = getattr(session, "channel", None) if session else None
_is_im = _channel and _channel not in ("cli", "desktop")    # 仅 IM
if _is_im and session and len(session_messages) >= 4:        # 历史 ≥4 轮
    topic_changed = await asyncio.wait_for(
        self._detect_topic_change(session_messages, message, session),
        timeout=10,
    )
    if topic_changed:
        # 插入 [上下文边界] 标记到 session_messages
        ...
        _extraction_task = _loop.create_task(
            self.memory_manager.extract_on_topic_change()    # 触发提取
        )
```

### 检测主体（`_agent_runtime.py:7192-7276`）

**输入保护**：
- `new_message` 长度 < 5 → 直接返回 False（前置廉价规则）
- `session_messages` 为空 → 返回 False

**多层上下文构建**：
| Layer | 来源 | 长度 |
| --- | --- | --- |
| 当前任务 | `session.context.get_variable("task_description")` | 全文 |
| 对话摘要 | `session.context.summary` | ≤600（smart_truncate） |
| 近期对话 | `session_messages[-6:]` | 每条 ≤500 |
| 新消息 | `_new` | ≤800 |

**判定 prompt**（拼成单轮 user 消息）：
```
[Layer1+Layer2+Layer3]

新消息: [...]

判断：新消息是延续当前话题(CONTINUE)，还是开启全新话题(NEW)？
只输出一个单词：CONTINUE 或 NEW
```

**LLM 路径**：`self.brain.compiler_think(prompt, system=...)`

**判定策略**（`_agent_runtime.py:7270`）：
```python
is_new = "NEW" in result and "CONTINUE" not in result
```
（防呆：避免 LLM 同时输出两词）

**失败处理**：异常 → 返回 False（即"不切换"），debug 日志。

### 提取触发（`manager.py:1330-1375`）

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
    # 保存提取的记忆，清空 turns 缓冲（新 topic 重新计数）
    self._session_turns.clear()
    return saved
```

### 已确认问题
1. **每轮都过 LLM 判定**（只要 IM + ≥4 轮，无其他节流），高 token 成本
2. **fire-and-forget 无去重**：连续两次 NEW 判定会启动两次后台提取，第一次的 turns 会被 `clear()` 丢弃
3. **30s 超时分支也执行 `_session_turns.clear()`**，导致宝贵 turns 因 LLM 慢而丢失

---

## 三、提取 Prompt 四件套（`extractor.py`）

### 1. `EXTRACTION_PROMPT_V2`（`extractor.py:55-132`）

**用途**：单轮记忆提取 v2，感知工具调用、输出实体-属性结构。

**关键约束**：
- 最多输出 **2 条记忆**（宁少勿多）
- 绝大部分对话 → 输出 `NONE`（最常见的正确答案）
- type ∈ {PREFERENCE, RULE, FACT, SKILL, ERROR}
- importance: 0.5-1.0
- 含 `is_update`、`update_hint` 字段用于支持"已知事实更新"

### 2. `EPISODE_PROMPT`（`extractor.py:134-146`）

**用途**：情节摘要生成。**输出 JSON**：

```json
{
  "summary": "100-200字发生了什么",
  "goal": "用户的目标/意图",
  "outcome": "success|partial|failed|ongoing",
  "entities": ["文件路径", "项目名", "概念"],
  "tools_used": ["工具名列表"]
}
```

### 3. `EXPERIENCE_EXTRACTION_PROMPT`（`extractor.py:334-388`）

**用途**：**任务经验、操作结果、教训**的提取（与用户画像分离）。

**关键约束**：
- `assistant_turns < 2` → 直接返回（没有经验的轮次不抽）
- 输入是 `turns[-30:]`（截断最近 30 轮）
- 工具调用上下文作为补充行注入
- 输出类型：EXPERIENCE / SKILL / ERROR

### 4. `SCRATCHPAD_PROMPT`（`extractor.py:148-168`）

**用途**：会话级工作记忆深度格式化。

**输入**：`current_scratchpad`（旧便签本全文）+ `episode_summary`（最新情节摘要）

**输出**：**Markdown 4 段**，≤2000 字符：
- `## 当前项目`
- `## 近期进展`
- `## 未解决的问题`
- `## 下一步`

### 5. `CITATION_SCORING_SECTION`（`extractor.py:380-388`）

**用途**：**让 LLM 在抽取记忆的同时对引用过的历史记忆打有用性评分**。

**关键设计**：把"记忆抽取"和"引用评分"塞到**同一次 LLM 调用**里，省一次往返。

---

## 四、四个提取方法

### 1. `extract_from_conversation`（`extractor.py:390-480`）

- 入口：对话级提取（多轮一起送）
- 输入：`turns[-30:]`，每条内容 ≤1500
- 跳过条件：用户消息 < 10 字符、或 `conv_lines` 为空
- 输出：`(items, citation_scores)`
- 失败 → `([], [])`

### 2. `extract_experience_from_conversation`（`extractor.py:482-...`）

- 入口：任务经验提取（独立 prompt）
- 跳过条件：`assistant_turns < 2`（保证有可提取的经验）
- **这是 nanobot 当前完全缺失的双轨之一**

### 3. `generate_episode`（`extractor.py:614-676`）

- 入口：生成情节记忆
- 包含 `_extract_action_nodes`：把每轮的 `tool_calls` + `tool_results` 提取为结构化 `ActionNode`
  - `tool_name` / `key_params`（command/path/query/url/filename 截 200 字符）
  - `result_summary`（前 200 字符）
  - `success` / `error_message`（从 `is_error` 字段判定）
- `_generate_fallback_summary`：LLM 失败时的回退摘要（拼接用户消息前 3 条）
- `_extract_entities`：正则匹配文件路径（`[A-Za-z]:[\\/]...`）和文件名（`.py|.js|.ts|.md|.json|.yaml|.toml|.sh`）

### 4. `update_scratchpad`（`extractor.py:743-...`）

- 入口：会话结束或话题切换时调用 LLM 重构便签本
- 输入：旧便签本内容 + `episode.summary`（或 `episode.to_markdown()` 回退）
- 输出：新的 `Scratchpad` 对象（user_id、content、active_projects、current_focus、open_questions、next_steps、updated_at）
- 失败 → 用 episode 摘要做简单回退

---

## 五、`end_session` 完整流程（`manager.py:1645-1819`）

### 触发源（`_agent_runtime.py`）

| 触发 | 位置 |
| --- | --- |
| `chat_with_session` 正常完成 | `:5773` |
| `chat_with_session` 抛异常 finally | `:5781` |
| `Agent.shutdown()` | `:8554` |

### 流程（`_finalize_session` 异步闭包）

1. **生成 Episode** → `extractor.generate_episode(turns, session_id)` → `store.save_episode(episode)`
2. **Track 1：用户画像抽取** → `extractor.extract_from_conversation(turns, cited)` → 引用评分一并回填
3. **Track 2：任务经验抽取** → `extractor.extract_experience_from_conversation(turns)`
4. **Back-fill**：episode ↔ memories ↔ turns 三向关联
5. **Relational 编码**（可选，mode 2 / auto）：批量编码关系图谱节点/边

### 关键编排细节

```python
task = loop.create_task(_finalize_session())
self._pending_tasks.add(task)
task.add_done_callback(self._pending_tasks.discard)
```

- 全部异步 + 任务登记
- 失败用 `record_health_event(...)` 兜底（不抛、不影响主聊天）
- Episode ID 通过 `episode_id=ep_id` 传给 `_save_extracted_item` 形成关联

---

## 六、对照 nanobot 现状的差异

| 维度 | OpenAkita | nanobot |
| --- | --- | --- |
| 通道门 | IM only（CLI/Desktop 跳过）| 无 |
| 长度门 | ≥4 轮 | 无（`_next_check_count` 节流 LLM 调用，但每轮都进函数）|
| 廉价前置规则 | `len(message) < 5` 直接 False | ❌ 无 |
| 提取触发 | `end_session` 一次性 4 任务 + 话题切换时 1 任务 | `after_run` 每轮 1 任务 |
| 提取任务数 | Episode + 用户画像（带引用评分）+ 经验 + Scratchpad 重构 + Relational | 仅 `extract_session` 单任务 |
| 双轨分离 | ✅ Track 1（用户画像）/ Track 2（经验） | ❌ 无分离 |
| Episode 抽取 | ✅ 含 `_extract_action_nodes` | ❌ prompt 定义但未接通 |
| Scratchpad 重构 | ✅ 会话结束 LLM 深度格式化 | ❌ `SCRATCHPAD_FORMAT_PROMPT` 常量未接通 |
| 引用评分 | ✅ 同 LLM 调用里 | ❌ 无 |
| 超时分支 | 30s 超时清空 turns（❌ 丢数据）| 5s 不持有累积（✅ 但累积本身就没有）|
| 后台任务节流 | 无（fire-and-forget 多次可能并发）| 无 |
| 健康事件上报 | `record_health_event` 兜底 | `logger.warning` 兜底 |

---

## 七、可借鉴点与不可借鉴点

### 可借鉴
1. **廉价前置规则**（消息 < 5 字符直接 False）—— 零成本挡大量误判
2. **`extract_action_nodes` 结构化捕获工具调用** —— 比纯文本拼接有信息密度
3. **`update_scratchpad` 输入结构**：旧便签本 + `episode.summary`，依赖链清晰
4. **失败兜底 `record_health_event`** —— 把失败归类上报，方便排查
5. **同次 LLM 调用内做"抽取 + 评分"** —— 省一次往返
6. **Episode + 双轨 + Scratchpad 三步走 + 关联回填** —— 完整的依赖链

### 不可直接照搬
1. **IM 通道门**：nanobot 当前不区分通道，且产品形态不同（WebUI/CLI/IM 多通道共存）
2. **30s 超时清空 turns**：这是 OpenAkita 的 bug，nanobot 改进时**反向**——超时不清空
3. **fire-and-forget 无去重**：OpenAkita 的坑，nanobot 改进时**必须加节流**

---

## 八、未在本轮调研深度的项

- `relational/*`（关系图谱、节点编码、实体解析）—— 已确认是 mode 2 选项
- `retrieval.py`（多路召回）—— 与本轮"提取时机"主题弱相关
- `daily_consolidator.py` / `consolidator.py`（长期巩固）—— 记忆生命周期下游
- `unified_store.py`（存储后端）—— 与提取流程无关

后续如需扩展改进范围（如要做关系图谱），可单独再起一轮调研。

---

## 调研产物边界

- ✅ 已读：`_agent_runtime.py:4794-4857`、`:5773-5781`、`:7192-7276`、`:8554`；`manager.py:1311-1375`、`:1645-1819`；`extractor.py:55-388`、`:390-510`、`:614-781`
- ❌ 未读：`relational/*`、retrieval/consolidator/storage 三件套、其他外围模块
- 🚫 本调研**不复制任何代码到 nanobot**，仅做架构/接口/约束的事实记录与对比