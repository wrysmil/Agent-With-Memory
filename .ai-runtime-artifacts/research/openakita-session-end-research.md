# Openakita 会话结束编排器调研报告

> 日期：2026-09-13
> 调研对象：openakita 源码
> 目的：对比 nanobot 缺陷，借鉴 openakita 的设计

---

## 概述

本文档对比 openakita 与 nanobot 在**会话结束编排**、**消息数量过滤**、**事件类型处理**三个维度的实现差异，为 nanobot 的缺陷修复提供借鉴。

---

## 一、核心符号对照表

| nanobot 符号 | openakita 符号 | 说明 |
|-------------|----------------|------|
| `SessionEndOrchestrator` | `MemoryManager.end_session()` | 会话结束编排器 |
| `SessionEndReason` | `HookEvent.SESSION_END` | 事件类型定义 |
| `SessionEndEvent` | `end_session()` 参数 | 事件携带信息 |
| `orchestrator.py` | `manager.py:1645-1819` | 核心文件位置 |

---

## 二、消息数量过滤对比

### 2.1 nanobot 的问题

**文件：** `nanobot/memory/orchestrator.py:31-82`

```python
async def run(self, event: SessionEndEvent) -> None:
    # ❌ 无任何消息数量检查！直接执行：
    episode = await self.generate_episode(event.transcript)  # 可能只有1条"你好"
    await self.extract_user_profile(event.transcript)
    await self.extract_experience(event.transcript)
```

**问题：**
- 对单条"你好"也会调 LLM
- 无消息质量判断
- 无角色分布分析

---

### 2.2 openakita 的多层守卫

**文件：** `openakita/src/openakita/memory/extractor.py`

#### 守卫 1：短用户消息过滤

```python
# extractor.py:390-409 - extract_from_conversation
async def extract_from_conversation(self, turns, ...):
    if not self.brain or not turns:
        return [], []

    # 过滤短用户消息（<10字符）
    user_turns = [
        t for t in turns
        if t.role == "user" and len(coerce_text(t.content).strip()) >= 10
    ]
    if not user_turns:
        return [], []  # 跳过：无有效用户消息
```

#### 守卫 2：短内容但无工具调用

```python
# extractor.py:206-223 - extract_from_turn_v2
async def extract_from_turn_v2(self, turn, ...):
    content = coerce_text(turn.content)
    # 短内容但无工具调用，跳过
    if len(content.strip()) < 10 and not turn.tool_calls:
        return []
```

#### 守卫 3：助手消息阈值

```python
# extractor.py:482-492 - extract_experience_from_conversation
async def extract_experience_from_conversation(self, turns):
    assistant_turns = [
        t for t in turns
        if t.role == "assistant" and coerce_text(t.content)
    ]
    # 少于2个助手消息则跳过
    if len(assistant_turns) < 2:
        return []
```

#### 守卫 4：话题切换阈值

```python
# manager.py:1330-1340 - extract_on_topic_change
async def extract_on_topic_change(self):
    turns = list(self._session_turns)
    # 少于3条消息则跳过
    if len(turns) < 3:
        return 0
```

---

### 2.3 设计模式总结

| 维度 | nanobot | openakita |
|------|---------|-----------|
| 守卫位置 | ❌ 无 | 分布在各提取函数内部 |
| 消息数量阈值 | ❌ 无 | ≥3 条（话题切换） |
| 内容长度阈值 | ❌ 无 | ≥10 字符 |
| 工具调用豁免 | ❌ 无 | 有（`if not tool_calls`） |
| 角色分布分析 | ❌ 无 | user ≥1, assistant ≥2 |
| 幂等保护 | ❌ 无 | `IDEMPOTENCY_WINDOW_SECONDS = 30` |

---

## 三、会话结束事件类型对比

### 3.1 nanobot 的悬空定义

**文件：** `nanobot/memory/session_end_event.py:9-15`

```python
class SessionEndReason(str, Enum):
    USER_CLOSE = "user_close"              # ❌ 未接入
    IDLE_TIMEOUT = "idle_timeout"          # ❌ 未接入
    PROCESS_SHUTDOWN = "process_shutdown"  # ✅ 唯一使用
    CHANNEL_DISCONNECT = "channel_disconnect"  # ❌ 未接入
```

**当前触发：** `nanobot/agent/hooks/memory_extraction.py:287-291`

```python
event = SessionEndEvent(
    session_key=self._session_key,
    reason=SessionEndReason.PROCESS_SHUTDOWN,  # 硬编码
    transcript=list(context.messages),
)
```

---

### 3.2 openakita 的统一处理

**文件：** `openakita/src/openakita/plugins/hooks.py`

```python
# 定义了 SESSION_END hook，但会话结束主要走 end_session() 统一处理
HookEvent.SESSION_END = "session_end"
```

**文件：** `openakita/src/openakita/memory/manager.py:1645-1819`

```python
def end_session(self, task_description="", success=True, errors=None):
    if not self._current_session_id:
        return  # 守卫0: 无会话则跳过

    async def _finalize_session():
        episode = await self.extractor.generate_episode(turns, ...)
        items, scores = await self.extractor.extract_from_conversation(turns, ...)
        exp_items = await self.extractor.extract_experience_from_conversation(turns)
        self.store.link_turns_to_episode(session_id, ep_id)
```

**特点：**
- 不区分会话结束原因
- 统一走 `end_session()` 处理
- 通过内部守卫过滤无效会话

---

## 四、关键文件索引

### nanobot（问题代码）

| 文件路径 | 说明 |
|---------|------|
| `nanobot/memory/orchestrator.py:31-82` | 会话结束编排器，无守卫 |
| `nanobot/memory/session_end_event.py:9-15` | 事件类型定义，3种悬空 |
| `nanobot/agent/hooks/memory_extraction.py:287-291` | 硬编码 PROCESS_SHUTDOWN |
| `nanobot/memory/extractor.py:1166-1240` | Episode 生成，无质量过滤 |

### openakita（参考代码）

| 文件路径 | 说明 |
|---------|------|
| `openakita/src/openakita/memory/extractor.py:390-409` | 消息数量过滤 |
| `openakita/src/openakita/memory/extractor.py:206-223` | 短内容 + 工具调用豁免 |
| `openakita/src/openakita/memory/extractor.py:482-492` | 助手消息阈值 |
| `openakita/src/openakita/memory/manager.py:1330-1340` | 话题切换守卫 |
| `openakita/src/openakita/memory/manager.py:1645-1819` | 完整会话结束流程 |
| `openakita/src/openakita/plugins/hooks.py` | Hook 事件定义 |

---

## 五、借鉴建议

### 5.1 高优先级：添加守卫逻辑

**建议在 `SessionEndOrchestrator.run()` 入口添加：**

```python
# nanobot/memory/orchestrator.py
async def run(self, event: SessionEndEvent) -> None:
    # 守卫0: 无消息跳过
    if not event.transcript:
        return

    # 守卫1: 硬阈值（借鉴 manager.py:1339）
    if len(event.transcript) < 3:
        logger.debug("[S1] skip: less than 3 messages")
        return

    # 守卫2: 短消息且无工具调用（借鉴 extractor.py:222）
    user_msgs = [m for m in event.transcript if m.get("role") == "user"]
    if len(user_msgs) == 1:
        content = user_msgs[0].get("content", "") or ""
        tool_calls = any(m.get("tool_calls") for m in event.transcript)
        if len(content.strip()) < 10 and not tool_calls:
            logger.debug("[S1] skip: single short message without tool calls")
            return
```

### 5.2 中优先级：提取函数内加角色过滤

**建议在 `extract_user_profile` 中借鉴：**

```python
# 过滤短用户消息（<10字符）
user_turns = [t for t in turns if t.role == "user" and len(content) >= 10]
if not user_turns:
    return []  # 跳过：无有效用户消息
```

**建议在 `extract_experience` 中借鉴：**

```python
# 少于2个助手消息则跳过
assistant_turns = [t for t in turns if t.role == "assistant"]
if len(assistant_turns) < 2:
    return []  # 跳过：没有足够交互来提取经验
```

### 5.3 低优先级：接入 SessionEndReason

可参考 openakita 的统一处理模式，或按 Reason 区分行为：

| Reason | 行为建议 |
|--------|---------|
| PROCESS_SHUTDOWN | 完整 4 步 |
| USER_CLOSE | 完整 4 步 |
| IDLE_TIMEOUT | 可选轻量 |
| CHANNEL_DISCONNECT | 最轻量兜底 |

---

## 六、风险评估

| 风险类型 | nanobot | openakita |
|---------|---------|-----------|
| Token 浪费 | ✅ 确认 | 已通过守卫避免 |
| 数据库污染 | ✅ 确认 | 有质量过滤 |
| 事件类型悬空 | ✅ 确认 | 无此问题 |
| 循环依赖 | ❌ 无 | ❌ 无 |
| 未使用代码 | ✅ 确认 | ❌ 无 |

---

## 七、结论

openakita 通过**分层守卫**和**内容质量分析**有效避免了 nanobot 的两个核心缺陷：

1. **消息数量过滤**：通过 ≥3 消息 + ≥10 字符 + 工具调用豁免的多重判断
2. **内容质量过滤**：通过角色分布分析（user ≥1, assistant ≥2）确保有价值的提取

nanobot 可直接借鉴 openakita 的守卫逻辑，在 `SessionEndOrchestrator.run()` 入口添加类似检查。
