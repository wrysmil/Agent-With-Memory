# 记忆系统缺陷记录：会话结束编排器待改进

> 日期：2026-09-13
> 状态：待处理
> 类型：架构缺陷

## 缺陷一：会话结束无消息数量过滤

### 问题描述

`SessionEndOrchestrator` 每次 `on_finally` 触发时，无条件执行 4 步编排器。即使会话只有 1 条消息（如"你好"），也会调 LLM 做完整提取。

### 当前行为

```
用户发 "你好"
  → 进程退出
  → on_finally 触发
  → SessionEndOrchestrator.run()
    → Step 1: generate_episode() 调 LLM（prompt = "你好"）
    → Step 2a: extract_profile() 调 LLM
    → Step 4: 写库（空/浅 episode）
```

### 问题影响

- **Token 浪费**：对无实质内容的短会话调 LLM，无意义
- **数据库污染**：大量"你好"类浅 episode 写入，降低记忆质量
- **无价值提取**：LLM 对单条"你好"无法生成有效摘要

### 源码位置

- 触发入口：`nanobot/agent/hooks/memory_extraction.py:293-295`
- 编排器：`nanobot/memory/orchestrator.py:31-107`
- Episode 生成（截断在 Step1 内部）：`nanobot/memory/extractor.py:1166-1240`

### 建议修复方向

在 `SessionEndOrchestrator.run()` 入口加守卫条件：

```python
# 建议加的过滤逻辑（伪代码）
if len(transcript) < MIN_MESSAGES_FOR_EXTRACTION:
    logger.debug("skip session end extraction: too few messages")
    return

# 或更精细的判断
if len(transcript) == 1 and not has_tool_calls(transcript):
    # 单条消息且无工具调用，跳过
    return
```

可能的判断维度：
- `len(transcript) < 2` → 跳过
- `len(transcript) == 1 and no_tool_calls` → 跳过
- `is_pure_greeting(transcript)` → 跳过
- `action_nodes` 非空 → 不过滤（有工具调用有价值）
- `rule_signals` 非空 → 不过滤（有规则有价值）

---

## 缺陷二：SessionEndReason 三种未接入

### 问题描述

`SessionEndReason` 定义了 4 种会话结束原因，但只有 `PROCESS_SHUTDOWN` 被实际触发。其他三种是悬空的：

| Reason | 含义 | 状态 |
|--------|------|------|
| `PROCESS_SHUTDOWN` | 进程退出 | ✅ 已使用 |
| `USER_CLOSE` | 用户主动关闭会话 | ❌ 未接入 |
| `IDLE_TIMEOUT` | 空闲超时 | ❌ 未接入 |
| `CHANNEL_DISCONNECT` | 频道断开 | ❌ 未接入 |

### 问题影响

- **用户关闭会话丢失**：用户主动结束对话时，不会触发完整记忆提取（除非走 `PROCESS_SHUTDOWN`）
- **空闲超时无感知**：`IDLE_TIMEOUT` 不触发提取，只有 TTL 删除走 T2（语义不同）
- **频道断开无清理**：网络断开时不触发任何记忆提取

### 源码位置

- 事件定义：`nanobot/memory/session_end_event.py:9-16`
- 当前唯一触发：`nanobot/agent/hooks/memory_extraction.py:286-294`
- Channel 基类：各 channel 无事件发射器

### 根本原因

```
channel层（谁感知断开） → 事件总线 → orchestrator（谁来跑）
         ↑ 缺失                  ↑ 缺失
```

- Channel 层没有"会话结束事件发射器"
- `SessionEndOrchestrator` 只在 `on_finally`（进程级）被触发
- 没有"会话级别"的事件路由机制

### 建议修复方向

#### 阶段 1：补全事件接入（高优先级）

1. **Channel 基类加事件发射器**

```python
# nanobot/channels/base.py
class ChannelBase:
    async def emit_session_end(self, session_key: str, reason: SessionEndReason):
        """Hook for subclasses to emit session end events."""
        pass
```

2. **各 Channel 实现**

- Telegram：用户发送 `/close` 或 Bot 被-block 时
- Discord：用户关闭 DM / Bot 被踢出时
- WebUI：用户点击"结束会话"按钮时
- 通用：在 WebSocket 断开 / channel disconnect 事件时

3. **事件总线路由**

```python
# 在 AgentLoop 或 MemoryExtractionHook 中
self.channels.on("session_end", self._handle_session_end)
```

#### 阶段 2：行为区分（可选）

不同 Reason 可能需要不同的处理策略：

| Reason | 是否走 4 步编排 | 说明 |
|--------|----------------|------|
| `PROCESS_SHUTDOWN` | ✅ 完整 4 步 | 进程退出，最完整 |
| `USER_CLOSE` | ✅ 完整 4 步 | 用户主动关闭，同样完整 |
| `IDLE_TIMEOUT` | ⚠️ 可选（轻量） | 空闲超时，可能只提取关键记忆 |
| `CHANNEL_DISCONNECT` | ⚠️ 可选（最轻） | 网络断开，只保底 |

---

## 关联现有路径对比

| 触发路径 | 消息数量过滤 | 触发时机 |
|---------|-------------|---------|
| T0 (after_run) | 有（IntentType != CHAT） | 每轮 |
| T1 (extract_session) | 无 | 每轮 fire-and-forget |
| S1 (SessionEndOrchestrator) | 无 | 进程退出 |
| T2 (会话删除) | 无 | TTL/手动删除 |
| T3 (Quick Facts) | 无 | 压缩成功 |
| T5 (增量) | 有（≥4条消息） | 话题切换 |

对比发现：
- **T0 有意图过滤**（`classify_intent`），但 S1 没有
- **T5 有消息数过滤**（≥4条），但 S1 没有
- 会话结束是最"重"的路径（4步），却没有任何过滤，最需要优化

---

## 优先级建议

| 缺陷 | 优先级 | 理由 |
|------|--------|------|
| 缺陷一：消息数量过滤 | **高** | 影响每次会话，Token 浪费最直接 |
| 缺陷二：USER_CLOSE 接入 | **中** | 用户体验相关 |
| 缺陷二：IDLE_TIMEOUT 接入 | **低** | 可由 T2 会话删除兜底 |
| 缺陷二：CHANNEL_DISCONNECT 接入 | **低** | 网络抖动场景较少 |

---

## 验收标准

- [ ] 缺陷一：单条消息（如"你好"）的会话不触发 LLM 提取
- [ ] 缺陷一：有工具调用的短会话仍正常触发提取
- [ ] 缺陷二：USER_CLOSE 能触发完整 4 步编排
- [ ] 缺陷二：测试覆盖至少一个 Channel（Telegram 或 WebUI）
