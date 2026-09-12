# OpenAkita 记忆抽取设计调研（抽取器视角）

> 调研对象：`/Users/mima0000/Documents/学习-001/源码学习/openakita/`
> 对照对象：`/Users/mima0000/Documents/学习-001/do-project/Agent-With-Memory/nanobot/memory/extractor.py`
> 调研目的：解决 nanobot 当前 `_render_transcript` "中间大段对话永远无法被抽取"的根问题，并参考 OpenAkita 的多触发 + 持久化优先策略。
> 调研日期：2026-09-12
> 调研方式：subagent `explorer`，只读

---

## 一、目录结构概览

OpenAkita 记忆模块集中在 `src/openakita/memory/`，共 14350 行（22 个文件）：

```
src/openakita/memory/
├── __init__.py            # 公共 API（v2: UnifiedStore / Extractor / Consolidator）
├── types.py               # 数据类：SemanticMemory / Episode / ActionNode / Scratchpad
├── extractor.py           # 核心：1130 行，prompt + 提取逻辑
├── manager.py             # 2728 行，会话生命周期编排（含 on_context_compressing hook）
├── consolidator.py        # 批量整理未处理会话
├── daily_consolidator.py  # 每日凌晨归纳，刷新 MEMORY.md + 跨用户去重
├── storage.py             # 3040 行，SQLite + FTS5 + Vector 检索
├── unified_store.py       # 主存储 + SearchBackend 抽象
├── lifecycle.py           # 1548 行，写入路径（_safe_write_with_backup / retention）
├── retrieval.py           # 1172 行，多路召回 + 重排序
├── json_utils.py          # 202 行，LLM JSON 解析（loads_llm_json 修复尾逗号 / 围栏）
├── retention.py           # 衰减策略
└── ...
```

职责边界清晰：**types 管形状 / extractor 管 AI 提取 / manager 管生命周期钩子 / daily_consolidator 管空窗批量 / storage 管物理存储**。

---

## 二、核心源码定位

### 2.1 `MemoryExtractor` —— AI 提取的两条轨道

文件：`src/openakita/memory/extractor.py`

**会话级批量提取**（核心）：`extractor.py:390-523`

```python
# extractor.py:411-421
from openakita.agent.tools import smart_truncate as _st

conv_lines = []
for t in turns[-30:]:                        # 取最近 30 轮
    role_label = "用户" if t.role == "user" else "助手"
    content, _ = _st(coerce_text(t.content), 1500, save_full=False, label="mem_conv")  # 每条 1500 字
    if content.strip():
        conv_lines.append(f"[{role_label}]: {content}")
    tool_ctx = self._build_tool_context(t.tool_calls, t.tool_results)
    if tool_ctx:
        conv_lines.append(tool_ctx)
```

两条独立 LLM 轨道（profile + experience）：
- `extract_from_conversation` → 用户画像 + 引用评分（extractor.py:291-332 prompt）
- `extract_experience_from_conversation` → 任务经验/教训（extractor.py:334-374 prompt）

LLM 调用统一入口 `_call_brain_main`（extractor.py:928-937）**强制 `enable_thinking=False`**——避免 4k-7k thinking token 浪费。

### 2.2 `smart_truncate` —— head + tail 双向保留（关键差异点）

文件：`src/openakita/runtime/io/truncate.py:39-82`

```python
# truncate.py:60-82
def smart_truncate(content, limit, *, label="content", save_full=True,
                   head_ratio=0.65, ...):
    if not content or len(content) <= limit:
        return content, False

    head = int(limit * head_ratio)       # 默认 65% 给头
    tail = limit - head - 120            # 尾部预留

    overflow_ref = ""
    if save_full:
        # 把溢出内容写到 sidecar 文件，LLM 可用 read_file 取回
        path = save_overflow_fn(label, content)
        overflow_ref = f", 完整内容: {path}, 可用 read_file 查看"

    marker = f"\n[已截断, 原文{len(content)}字{overflow_ref}]\n"

    if tail > 0:
        return content[:head] + marker + content[-tail:], True
    return content[:head] + marker, True
```

**与 nanobot 关键差异**：OpenAkita 默认保留 head 65% + tail（仅剩 ~22%），中间部分丢失但溢出可写 sidecar 文件让 LLM 按需拉回。

### 2.3 `Episode` —— 完整情节记忆

文件：`src/openakita/memory/types.py:419-507`

```python
# types.py:420-443
@dataclass
class Episode:
    id: str = ...
    session_id: str = ""
    summary: str = ""              # LLM 生成 100-200 字摘要
    goal: str = ""                 # 用户目标
    outcome: str = "completed"     # success/partial/failed/ongoing
    started_at: datetime
    ended_at: datetime
    action_nodes: list[ActionNode] # 完整工具调用链
    entities: list[str]
    tools_used: list[str]
    linked_memory_ids: list[str]   # 反向关联抽取出的 memory
    source: str = "session_end"    # session_end / context_compress / daily_consolidation
    compaction_checkpoint_id: str = ""
    workspace_snapshot_id: str = ""
```

**Episode 用结构化字段保留"完整交互故事"，不仅是 summary**——`action_nodes` / `entities` / `tools_used` 都是结构化存储，可在后续检索/回放时被引用。

### 2.4 多种触发时机并存 —— 不只是"会话结束"

文件：`src/openakita/memory/manager.py`

| 触发点 | 函数 | 文件位置 | 策略 |
|---|---|---|---|
| **主题切换** | `extract_on_topic_change()` | manager.py:1330-1375 | turns 缓冲 ≥3 时调 extract_from_conversation（30s 超时），然后清空缓冲 |
| **每轮实时** | `record_turn()` | manager.py:1259 | 仅 append 到 `_session_turns` + SQLite + JSONL，**不调 LLM** |
| **上下文压缩前** | `on_context_compressing()` | manager.py:1904-1957 | 1) `extract_quick_facts` 规则扫描；2) 前 10 条 turn 入队 enqueue_extraction；3) Relational quick encode |
| **会话结束** | `end_session()` | manager.py:1645-1819 | 三轨道：Episode + profile + experience，全部 `asyncio.wait_for(..., 30.0)` |
| **每日凌晨** | `consolidate_daily()` | daily_consolidator.py:67-123 | 跨会话批量 + 跨用户去重 + 刷新 MEMORY.md + 晋升 PERSONA_TRAIT 到 identity |

### 2.5 `extract_quick_facts` —— 规则扫描（压缩前低延迟）

文件：`extractor.py:816-864`

```python
# extractor.py:816-822
_RULE_SIGNAL_PATTERNS = [
    re.compile(r"(?:每次|总是|always)\s*.{4,80}"),
    re.compile(r"(?:不要|不可以|禁止|never)\s*.{4,80}"),
    re.compile(r"(?:必须|务必|一定要|must)\s*.{4,80}"),
    re.compile(r"(?:永远|永远不要)\s*.{4,80}"),
    re.compile(r"(?:规则|rule)[：:]\s*.{4,120}"),
]
```

无 LLM 调用，纯正则匹配 user 消息中强规则信号，避免上下文压缩时静默丢失规则偏好。

### 2.6 去重（多层防线）

文件：`src/openakita/memory/manager.py:1377-1488` `_save_extracted_item`

```python
# manager.py:1418-1435  L1: subject+predicate 精确命中 → evolve
subject = item.get("subject", "")
predicate = item.get("predicate", "")
self._sync_profile_fact(subject, predicate, content)
if subject and predicate and not self._identity_slot_for(_identity_probe):
    existing = self.store.find_similar(subject, predicate, ...)
    if existing:
        self._evolve_memory(existing, content, importance)  # 进化，不是删除
        return existing.id

# manager.py:1437-1466  L2: 向量相似度搜索 → exact/likely → LLM 二次判定
if content and len(content) >= 10:
    similar = self.store.search_semantic(content, limit=5, ...)
    for s in similar:
        dup_level = self._fast_dedup_check(content, existing_content)
        if dup_level == "exact":    self._evolve_memory(s, content, importance)
        if dup_level == "likely":   LLM 确认后再 evolve
```

**关键策略：去重不等于删除，而是 `_evolve_memory` 合并更新**（extractor.py:1119-1130 也有字符串 dedup）。

跨租户隔离在 `daily_consolidator.py:466-542` `_cleanup_duplicate_memories` —— 按 `(user_id, workspace_id)` 分组独立去重，避免多用户 IM 部署上 user A 和 user B 的相似记忆被合掉。

### 2.7 Scratchpad —— 跨 session 工作记忆

文件：`types.py:515-559` + `extractor.py:743-810` `update_scratchpad()`

```python
# types.py:516-525
@dataclass
class Scratchpad:
    user_id: str = "default"
    content: str = ""            # Markdown，≤2000 字符
    active_projects: list[str]
    current_focus: str
    open_questions: list[str]
    next_steps: list[str]
    updated_at: datetime
```

每次会话结束用 `SCRATCHPAD_PROMPT`（extractor.py:148-168）让 LLM 把"当前项目/近期进展/未解决/下一步"四个段落刷新。Scratchpad 在上下文压缩时注入到 prompt 里（manager.py:1959-1979 `contribute_to_compaction`），**压缩后丢失的中间对话，Scratchpad 还在**。

### 2.8 跨会话持久化

文件：`src/openakita/memory/consolidator.py:53-66`

```python
def save_conversation_turn(self, session_id, turn):
    session_file = self.history_dir / f"{session_id}.jsonl"
    with open(session_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(turn.to_dict(), ensure_ascii=False) + "\n")
```

结合 SQLite 存 turn（manager.py:1282-1296 双写）。**每条 turn 都有持久化记录，不会因上下文压缩丢失**——压缩只是"从 prompt 里移除"，原始数据还在。

---

## 三、OpenAkita 解决"长会话抽取"的具体策略

### 3.1 多策略并存，不依赖单一路径

```
┌────────────────────────────────────────────────────────────────┐
│ 主题切换时（增量抽取）    extract_on_topic_change() → 重置缓冲 │
│         ↓                                                       │
│ 实时持久化（每轮）        SQLite + JSONL 双写 → 永不丢失       │
│         ↓                                                       │
│ 压缩前低延迟抽取          extract_quick_facts（纯规则）+ enqueue│
│         ↓                                                       │
│ 会话结束（三轨道）        Episode + Profile + Experience       │
│         ↓                                                       │
│ 每日凌晨（兜底整理）      跨会话批量 + 跨用户去重 + MEMORY.md  │
└────────────────────────────────────────────────────────────────┘
```

### 3.2 截断采用 head + tail + sidecar 文件

`smart_truncate`（truncate.py:39-82）默认 head 65% + tail（剩余空间） + 中间段溢出写 sidecar 文件。LLM 通过 `read_file` 工具可按需取回完整内容。

### 3.3 抽取窗口限定"最近 N 轮"（典型 30 轮）

- `extract_from_conversation` 用 `turns[-30:]`（extractor.py:414）
- `generate_episode` 用 `turns[-20:]`（extractor.py:633）
- `consolidate_daily` 在 `_generate_summary` 中用 `turns[-30:]`（consolidator.py:218-223）

### 3.4 持久化优先于抽取

turn 实时写 SQLite + JSONL（manager.py:1259, 1282-1296），**抽取逻辑全部跑在副本上**，不会因为抽取失败导致数据丢失。即使 LLM 调用 30s 超时，turn 也已落盘；下一次会话开始时 `get_max_turn_index` 从中断处续传（manager.py:654-660）。

### 3.5 通过 Episode 间接"记住"中间段

Episode 包含完整 `action_nodes`（工具调用链，types.py:382-403）和 `entities`（types.py:435），不需要原文就能在后续检索中提供"那次会话做了什么"的上下文。ActionNode 按 `key_params` 只保留 command / path / query / url / filename 五个键（extractor.py:687-690）压缩存储。

---

## 四、对比表：OpenAkita vs nanobot 当前实现

| 维度 | OpenAkita | nanobot (feature/memory-system) | nanobot 8000 字硬截断风险 |
|---|---|---|---|
| **截断策略** | head 65% + tail（truncate.py:64-65），溢出写 sidecar 文件 | `_render_transcript` 仅 `joined[-max_chars:]`（extractor.py:516-518）保留纯尾部 | **中间大段对话完全丢失** |
| **每条 turn 上限** | 1500 字（extractor.py:416） | 不限单条，整体 8000 字 | 旧 turn 内容被彻底切掉 |
| **抽取窗口** | 增量：主题切换 + 每 session 最近 30 轮（extractor.py:414） | 整 session（受 8000 字截断） | 中间被截掉的内容无法抽取 |
| **持久化** | 每 turn 双写 SQLite + JSONL（manager.py:1282-1296） | Session 内 messages 数组 | 压缩/裁剪后原文可能丢失 |
| **触发时机** | 4 个：主题切换 / 每轮 / 压缩前 / session end / 每日（manager.py + daily_consolidator.py） | 1 个：`extract_session` 主要在 session 结束（extractor.py:551-575） | 中间发生的事件要等 session end 才能被抽取 |
| **多轨道并发** | Episode（独立 LLM） + Profile（独立 LLM） + Experience（独立 LLM） | `asyncio.gather` 同时两路：semantic + episode（extractor.py:650-654） | nanobot 已有部分并发能力 |
| **失败隔离** | 30s timeout + record_health_event + 独立 track 失败不阻断其他（manager.py:1346, 1707） | `asyncio.gather return_exceptions=True`（extractor.py:650-654） | nanobot 已有部分隔离 |
| **规则信号抽取** | 压缩前 `_RULE_SIGNAL_PATTERNS` 正则（extractor.py:816-822） | `_collect_rule_signals` 正则（extractor.py:333-346） | 功能对等 |
| **episode 来源** | session_end / context_compress / daily_consolidation 三种 `source` 字段（types.py:441） | 同 `_SOURCE_MAP` 三种枚举（extractor.py:110-115） | 字段对等 |
| **episode 完整性** | `action_nodes` 结构化（含 success/error_message/timestamp） + `entities` + `tools_used` + `linked_memory_ids` + `compaction_checkpoint_id` + `workspace_snapshot_id`（types.py:433-443） | `action_nodes`（dict）+ `entities` + `tools_used` + `linked_memory_ids`（extractor.py:887-901） | openakita 多出 compaction/snapshot 关联 |
| **去重** | L1 subject+predicate evolve + L2 向量+exact/likely + LLM 二次判定 + 跨租户隔离（manager.py:1418-1466, daily_consolidator.py:466-542） | 精确哈希 + N-Gram 0.8 相似度（extractor.py:780-810, filters 模块） | openakita 更细粒度；nanobot 用 LLM 二次判定更省 |
| **LLM thinking 控制** | 强制 `enable_thinking=False`（extractor.py:937） | `reasoning_effort` 透传 runtime 配置 | openakita 注释明确说明"避免 4k-7k thinking 浪费" |
| **Scratchpad** | 跨 session 持久化（types.py:515-559）+ 注入压缩 prompt（manager.py:1968-1979） | ScratchpadEntry 模型 + get_scratchpad（models.py, repository.py） | nanobot 已有类似但未确认是否注入压缩 prompt |
| **MEMORY.md 容量治理** | 三档：1500 字符 / 200 行 / 25KB（types.py:82-89, truncate_memory_md_with_status） | 暂无 | nanobot 无对应 |
| **失败 fallback 文件** | DB 不可用时写 `extraction_fallback/{timestamp}_{turn_index}.json`（manager.py:1879-1893） | 未发现 | nanobot 可借鉴 |

---

## 五、OpenAkita 的优缺点分析

### 5.1 优点（值得 nanobot 借鉴）

1. **`smart_truncate` head+tail 双向保留**（truncate.py:60-82）
   - 解决 nanobot `_render_transcript` 中间大段被丢弃的问题
   - 65% head + tail 分配比纯 tail 更合理（很多记忆点在开头）
   - sidecar 文件机制让 LLM 按需拉回完整内容

2. **多层触发时机**（manager.py + daily_consolidator.py）
   - 主题切换时主动抽取，重置缓冲，避免越攒越长
   - 压缩前抽取，避免上下文压缩后遗忘
   - 每日凌晨兜底，捕获所有遗漏

3. **turn 实时持久化**（manager.py:1259, 1282-1296）
   - SQLite + JSONL 双写，单点故障不影响数据
   - 抽取只跑在副本上，LLM 失败不丢数据

4. **`extract_quick_facts` 规则扫描**（extractor.py:824-864）
   - 上下文压缩前纯正则提取强规则信号，零延迟
   - 避免 LLM 调用失败时规则类记忆丢失

5. **去重采用 evolve 而非 delete**（manager.py:1433, 1453, 1460）
   - 保留历史痕迹，便于审计和回滚
   - subject+predicate 精确命中优先于内容相似度

6. **MEMORY.md 三档容量治理**（types.py:82-89）
   - 字符 + 行数 + 字节三维上限
   - 触发截断时按段落优先级（规则段优先保留）

7. **失败 fallback 写文件**（manager.py:1879-1893）
   - DB 写入失败时转存 JSON，永不丢数据

### 5.2 缺点 / OpenAkita 自身的问题

1. **`turns[-30:]` 硬窗口**（extractor.py:414, 633）
   - 虽然窗口外的 turn 在 SQLite/JSONL 里，但**抽取 prompt 不包含**，所以即使明天做 daily consolidation，也是先 `_generate_summary` 用 `turns[-30:]`（consolidator.py:218-223）
   - 真正的"中间段抽取"依赖 daily_consolidator 分批遍历，会多次调 LLM

2. **smart_truncate save_full=False 时仍走 sidecar**
   - extractor 调用时 `save_full=False`（extractor.py:416, 499），意味着 sidecar 文件不会被创建
   - 实际"完整内容溢出可读回"机制在抽取路径**未启用**，与设计意图不完全一致

3. **每 session 多次 LLM 调用**
   - 主题切换 + 压缩前 + session end + 每日
   - 中等规模使用每天可能 5-10 次 LLM 调用，仅为记忆提取
   - 没有 LLM 调用预算控制（虽然 thinking=False 节省）

4. **smart_truncate head 65% 可能误删关键中段**
   - 65% head + 22% tail = 13% 中间丢失
   - 对"中间是讨论焦点"的对话（如代码 review 长讨论）仍会丢上下文

5. **Episode 仅生成 summary 200 字**
   - extractor.py:142 `summary: "一段话描述发生了什么 (100-200字)"`
   - 虽然有 action_nodes 结构化补充，但 quick 检索时只能看到 summary
   - 长会话中间内容只能通过 action_nodes 间接恢复

### 5.3 nanobot 可直接借鉴的具体点

| 借鉴点 | 实施位置（建议） | 借鉴收益 |
|---|---|---|
| `smart_truncate(head_ratio=0.4)` 替换 `_render_transcript` | `nanobot/memory/extractor.py:504-518` | 解决中间段被纯 tail 截断的问题 |
| `extract_on_topic_change` 增量触发 + 重置缓冲 | 新增方法 | 避免单次 session_end 处理过长对话 |
| `on_context_compressing` hook + enqueue_extraction | 接入 session manager | 压缩前不丢数据 |
| turn 实时 SQLite 持久化（已有 session.messages，但需要确认是否每 turn 落盘） | session.manager | 抽取永远跑在持久化副本上 |
| MEMORY.md 三档容量治理 | 新增类型/lifecycle | 防止 context 注入膨胀 |
| 失败 fallback 写文件 | repository / database | LLM 失败不丢候选 |
| `enable_thinking=False` 显式声明 | runtime 调用处 | 避免 thinking token 浪费 |

---

## 六、建议下一步（仅调研结论，不实施）

1. **优先级 1**：把 `_render_transcript` 改成 head+tail 双向截断（参考 `smart_truncate`），立即解决"中间大段对话被遗忘"的根问题。
2. **优先级 2**：增加"主题切换触发增量抽取"的 hook，参考 `extract_on_topic_change` 的 30s 超时 + 缓冲重置模式。
3. **优先级 3**：在 session.messages 上确认 SQLite 持久化（如果尚未做），让抽取永远跑在持久化副本上。
4. **优先级 4**：增加 MEMORY.md 容量治理（参考 `truncate_memory_md_with_status` 三档上限）。

### Skills 使用

- 已加载：agent-browser（web-investigator 默认 skill，本次未触发浏览器交互）
- 未触发：systematic-debugging（无 bug 调查）

> 说明：本任务为只读调研，所有源码证据均直接来自 `/Users/mima0000/Documents/学习-001/源码学习/openakita/` 与 `/Users/mima0000/Documents/学习-001/do-project/Agent-With-Memory/`，未修改任何文件。
