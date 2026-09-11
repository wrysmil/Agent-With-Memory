# Phase 2 记忆提取功能代码审查表（含变更理由）

**审查范围：** `feature/memory-system` 分支（commit `0a18ee4` → `6b5e7c8`）  
**生成时间：** 2026-09-11  
**状态：** 待人工审查

---

## 审查表正文

### 1. `nanobot/memory/filters.py`（新增）

| 属性 | 说明 |
|------|------|
| **变更类型** | 新增文件 |
| **行数** | 218 行 + 测试 302 行 |
| **所属提交** | `7c1fc59` feat(memory): 新增防污染过滤器/意图识别器/提示词常量/ScratchpadWriter |

**为什么改：**

Phase 2 设计要求「防止 AI 污染记忆库」。LLM 提取的记忆容易出现三类脏数据：

1. **任务产物**：用户说「帮我写代码」，AI 生成的代码片段被误存为「用户事实」
2. **AI 自答**：AI 回答时以「作为AI，我建议...」开头的句子被误存
3. **精确/相似重复**：同一信息多次出现，占用存储且无信息增量

**具体改动：**

```python
# _TASK_INDICATORS：检测用户请求而非事实陈述
re.compile(r"帮我[做给查找]|帮我生成|帮我写|帮我创建|帮我搜索")

# _AI_SELF_TALK：检测 AI 自我引用
re.compile(r"^作为AI|^作为一个人工智能|^作为语言模型")
re.compile(r"^as an? (ai|language model|assistant)", re.IGNORECASE)
```

**审查重点：**
- [ ] 正则覆盖是否充分（中英文任务请求模式）
- [ ] 是否存在误判（用户真实陈述包含「建议」等词是否被误过滤）

---

### 2. `nanobot/memory/intent.py`（新增）

| 属性 | 说明 |
|------|------|
| **变更类型** | 新增文件 |
| **行数** | 146 行 + 测试 160 行 |
| **所属提交** | `7c1fc59` |

**为什么改：**

T0 触发点需要在**不调 LLM** 的情况下判断用户意图，决定是否写 scratchpad。如果把「你好」「谢谢」这类闲聊也写入 scratchpad，会产生大量无意义条目。

**具体改动：**

```python
class IntentType(str, Enum):
    CHAT = "chat"       # 闲聊 → 不写 scratchpad
    QUERY = "query"     # 疑问句
    TASK = "task"       # 任务请求
    FOLLOW_UP = "follow_up"  # 追问
    COMMAND = "command"  # slash command
```

意图分类采用**规则+枚举**方式，避免 T0 阶段调用 LLM 保证低延迟。

**审查重点：**
- [ ] 分类优先级是否合理（CHAT 最早拦截）
- [ ] 边界case：问句「帮我查一下...好吗？」如何分类

---

### 3. `nanobot/memory/prompts.py`（新增）

| 属性 | 说明 |
|------|------|
| **变更类型** | 新增文件 |
| **行数** | 204 行 + 测试 194 行 |
| **所属提交** | `7c1fc59` |

**为什么改：**

Phase 2 设计要求两路并发 LLM 提取（语义 + 情节），需要两套不同的 prompt 模板。另外 T5 话题切换检测也需要独立 prompt。

**具体改动：**

```python
SEMANTIC_EXTRACTION_PROMPT  # 语义记忆提取 prompt
EPISODE_EXTRACTION_PROMPT   # 情节/事件提取 prompt
TOPIC_CHANGE_DETECTION_PROMPT  # T5 话题切换检测 prompt
```

**审查重点：**
- [ ] prompt 是否包含 Few-shot 示例
- [ ] 是否存在 prompt 注入风险（用户输入是否正确隔离）
- [ ] 输出格式是否与 parser 对齐

---

### 4. `nanobot/memory/scratchpad_writer.py`（新增）

| 属性 | 说明 |
|------|------|
| **变更类型** | 新增文件 |
| **行数** | 178 行 + 测试 264 行 |
| **所属提交** | `7c1fc59` |

**为什么改：**

Phase 2 设计要求 T0 即时同步路径写入 scratchpad，不调 LLM，保证 after_run 钩子的低延迟。

**具体改动：**

```python
class ScratchpadWriter:
    def update_focus(session_key, focus)   # 更新当前焦点
    def archive_completed(session_key, focus)  # 归档已完成项目
    def push_active_project(session_key, project)  # 推入 active_projects
```

**修复的问题：**
- `user_id_for_key()` 从 `session_key` 派生 user_id，避免所有会话共用 `"default"` 导致的跨用户数据串扰（FIX-1 Sec-C-α）

**审查重点：**
- [ ] 并发写入安全性（同一 session_key 的多次 after_run）
- [ ] `archive_completed` 与 `update_focus` 的状态机是否正确

---

### 5. `nanobot/memory/extractor.py`（新增）

| 属性 | 说明 |
|------|------|
| **变更类型** | 新增文件 |
| **行数** | 835 行 + 测试 803 行 |
| **所属提交** | `a9bd0bc` feat(memory): 实现 MemoryExtractor 四阶段提取流水线与 SQLite 持久化 |

**为什么改：**

这是 Phase 2 的核心模块，实现四阶段提取流水线：
1. `_system_extract` — 规则信号提取（Quick Facts，不调 LLM）
2. `_llm_extract` — 并发两路 LLM 提取（语义 + 情节）
3. `_apply_filters` — 防污染 + 去重
4. `_persist` — SQLite 持久化

**具体改动：**

```python
async def extraction_pipeline(
    self,
    session: Session,
    runtime: LLMRuntime,
    trigger: str,  # T0/T1/T5 触发源标识
) -> ExtractionResult:
    # 阶段1: 规则信号
    facts = self._system_extract(session.messages)
    # 阶段2: LLM 提取（并发）
    semantic, episode = await self._llm_extract(messages, runtime)
    # 阶段3: 防污染
    filtered = self._apply_filters([...])
    # 阶段4: 持久化
    await self._persist(conn, filtered, episode)
```

**安全加固（FIX-3）：**
- `ACTION_NODE_INPUT_MAX_CHARS = 512`：截断 ActionNode 输入
- `ACTION_NODE_OUTPUT_MAX_CHARS = 2048`：截断 ActionNode 输出
- `_REDACT_PATTERNS`：脱敏 API key、password、token、AWS 密钥

**审查重点：**
- [ ] 四阶段流转是否完整，失败是否正确隔离
- [ ] importance_score clamp 是否生效
- [ ] 凭据脱敏正则是否覆盖常见模式

---

### 6. `nanobot/memory/models.py`（修改）

| 属性 | 说明 |
|------|------|
| **变更类型** | 新增 1 个字段 |
| **所属提交** | `a9bd0bc` |

**为什么改：**

新增 `source_episode_id` 字段，建立 memory 与 episode 的双向关联：

```python
@dataclass
class Memory:
    # ... 其他字段 ...
    source_episode_id: str | None = None  # 新增：关联的 episode ID
```

**审查重点：**
- [ ] `to_row()` 是否包含新字段
- [ ] 向后兼容：旧记录该字段为 None 是否正常

---

### 7. `nanobot/memory/repository.py`（修改）

| 属性 | 说明 |
|------|------|
| **变更类型** | 新增函数 |
| **所属提交** | 多个 |

**为什么改：**

Phase 2 需要：
1. `add_episode` — 持久化 episode 记录
2. `update_memory_source_episode` — 事后回填 memory 的 source_episode_id（因为 memory 先于 episode 写入）
3. `upsert_scratchpad` / `get_scratchpad` — scratchpad CRUD

**审查重点：**
- [ ] `update_memory_source_episode` 是否正确处理批量更新
- [ ] 事务边界是否正确

---

### 8. `nanobot/agent/hooks/memory_extraction.py`（新增）

| 属性 | 说明 |
|------|------|
| **变更类型** | 新增文件 |
| **行数** | 503 行 + 测试 552 行 |
| **所属提交** | `8764198` feat(memory): 新增 MemoryExtractionHook 生命周期钩子 |

**为什么改：**

Phase 2 设计要求将记忆提取接入 Agent 生命周期钩子系统。触发点：

| 触发点 | 时机 | 行为 |
|--------|------|------|
| **T0** | `after_run` | 意图分类 → 写 scratchpad（不调 LLM） |
| **T0'** | `on_error` | 异常时紧急保存 scratchpad |
| **T1** | `after_run` + `on_finally` | Fire-and-forget 提取任务（最多等 5s） |
| **T5** | `before_iteration` | 话题切换检测 → 触发提取 |

**具体实现：**

```python
class MemoryExtractionHook(AgentHook):
    async def after_run(self, context, result, **kwargs):
        # T0: 意图分类 + 同步写 scratchpad
        intent = classify_intent(last_user_message)
        if intent != IntentType.CHAT:
            self.scratchpad_writer.update_focus(...)
    
    async def before_iteration(self, context, **kwargs):
        # T5: 话题切换检测
        topic = await self._check_topic_change(messages, runtime)
        if topic == TopicChange.NEW:
            self._fire_forget_extraction(...)
```

**审查重点：**
- [ ] T0 异步任务不登记到 hook（防止 GC）
- [ ] T5 为什么不用实例缓冲（文档已解释）
- [ ] 四个回调的错误隔离是否正确

---

### 9. `nanobot/agent/autocompact.py`（修改）

| 属性 | 说明 |
|------|------|
| **变更类型** | 新增参数 + 调用 |
| **所属提交** | `9bfc5fa` feat(memory): 上下文压缩后 Quick Facts 规则提取 |

**为什么改：**

上下文压缩（autocompact）触发后，session messages 被截断，此时从压缩后的消息中提取 Quick Facts（规则类记忆）。

**具体改动：**

```python
class AutoCompact:
    def __init__(..., quick_facts_hook: Callable[[Session], int] | None = None):
        self._quick_facts_hook = quick_facts_hook

    def _maybe_archive(self, session):
        if self._quick_facts_hook:
            count = self._quick_facts_hook(session)
            # 返回写入的记忆数量
```

**审查重点：**
- [ ] 压缩后提取的内容质量（截断后上下文是否足够）
- [ ] `_quick_facts_hook` 的返回值含义是否明确

---

### 10. `nanobot/agent/loop.py`（修改）

| 属性 | 说明 |
|------|------|
| **变更类型** | 新增参数 + 装配逻辑 |
| **所属提交** | `21a1d3c`, `c02c23b` |

**为什么改：**

将记忆提取装配到 AgentLoop，使整个系统可插拔（opt-in）：

```python
class AgentLoop:
    def __init__(
        self,
        ...
        memory_extraction_enabled: bool = False,  # 新增开关
        memory_services: MemoryServices | None = None,
    ):
        if memory_extraction_enabled:
            quick_facts_hook = self._wire_memory_extraction(memory_services)
            self.auto_compact = AutoCompact(
                ...
                quick_facts_hook=quick_facts_hook,
            )
```

**装配内容：**
1. 注册 T0/T1/T5 钩子工厂
2. 注册 session 删除观察者（T2）
3. 返回 Quick Facts 回调给 AutoCompact

**审查重点：**
- [ ] `memory_extraction_enabled=False` 时行为是否完全不变
- [ ] `_memory_extraction_tasks` 的生命周期管理

---

### 11. `nanobot/session/manager.py`（修改）

| 属性 | 说明 |
|------|------|
| **变更类型** | 新增 observer 机制 |
| **所属提交** | `21a1d3c` |

**为什么改：**

T2 触发点：session 删除时，触发删除溯源提取（将删除内容保存为 episode）。

**具体改动：**

```python
def set_delete_session_observer(self, callback: Callable[[str], None]):
    """注册 session 删除观察者，session 销毁时调用。"""
    self._delete_observers.append(callback)
```

**审查重点：**
- [ ] observer 是否在 session 真正删除前执行
- [ ] 多个 observer 的调用顺序

---

### 12-20. 测试文件（共 9 个）

| # | 文件 | 覆盖目标 |
|---|------|---------|
| 12 | `tests/memory/test_filters.py` | `filters.py` 单元测试 |
| 13 | `tests/memory/test_intent.py` | `intent.py` 单元测试 |
| 14 | `tests/memory/test_prompts.py` | `prompts.py` 单元测试 |
| 15 | `tests/memory/test_scratchpad_writer.py` | `scratchpad_writer.py` 单元测试 |
| 16 | `tests/memory/test_extractor.py` | `extractor.py` 单元测试 |
| 17 | `tests/memory/test_extraction_integration.py` | 集成测试 |
| 18 | `tests/memory/test_hook_memory_extraction.py` | 钩子行为测试 |
| 19 | `tests/memory/test_loop_wiring.py` | 装配链路测试 |
| 20 | `tests/memory/test_quick_facts.py` | Quick Facts 测试 |

**审查重点：**
- [ ] 边界条件覆盖
- [ ] mock 策略（是否正确 mock 了 LLM 调用）
- [ ] 集成测试是否覆盖真实数据库操作

---

## 提交链路总结

```
7c1fc59 (GROUP-1) ──┬── filters.py    防污染
                    ├── intent.py     意图识别
                    ├── prompts.py    提示词常量
                    └── scratchpad_writer.py  scratchpad 写入
                        ↓
a9bd0bc (WU-05) ────┬── extractor.py   四阶段流水线
                    └── models.py     +source_episode_id
                        ↓
8764198 (WU-06) ────┬── hooks/memory_extraction.py  生命周期钩子
                        ↓
9bfc5fa (WU-08) ────┬── autocompact.py  Quick Facts 提取
                        ↓
21a1d3c (WU-07) ────┬── loop.py        装配记忆提取
                    └── manager.py     删除观察者
                        ↓
c02c23b ─────────────┼── extractor.py   并发安全 + scratchpad 写入
                    ├── scratchpad_writer.py  删除溯源
                    └── models.py     +source_episode_id 回填
                        ↓
2769eae ─────────────┼── extractor.py   backfill source_episode_id
996ac7c ─────────────┼── extractor.py   importance clamp
```

---

## 相关文档

- 执行日志：`.ai-runtime-artifacts/execution-logs/2026-09-10-phase2-memory-extraction-execution-log.md`
- 交接文档：`.ai-runtime-artifacts/plans/2026-09-10-phase2-memory-extraction-handoff.md`
- 设计文档：`docs/记忆系统/plan/阶段二设计_记忆提取.md`
