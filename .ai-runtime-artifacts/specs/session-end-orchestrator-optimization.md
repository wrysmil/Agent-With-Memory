# 记忆系统会话结束编排优化方案

> 日期：2026-09-13
> 类型：Feature Spec
> 状态：草稿
> 关联缺陷：`.ai-runtime-artifacts/decisions/2026-09-13-session-end-orchestrator-defects.md`
> 关联调研：`.ai-runtime-artifacts/research/openakita-session-end-research.md`

---

## 1. 背景与目标

### 1.1 当前问题

`SessionEndOrchestrator` 存在两个核心缺陷：

| 缺陷 | 影响 |
|------|------|
| 无消息数量过滤 | 对单条"你好"也调 LLM，浪费 Token |
| `SessionEndReason` 悬空 | 4种事件类型只用了1种，其他3种未接入 |

### 1.2 优化目标

1. **Token 节省**：短会话（<3条消息）不触发 LLM 提取
2. **质量保障**：有工具调用的会话即使短也正常提取
3. **事件补全**：接入 `USER_CLOSE` 事件（高优）

---

## 2. 方案设计

### 2.1 核心策略：分层守卫

借鉴 openakita 的多层守卫设计，在 `SessionEndOrchestrator.run()` 入口添加守卫：

```
入口守卫 → Step 内部守卫 → 幂等保护
    ↓
```

#### 守卫层级

| 层级 | 守卫位置 | 判断条件 | 行为 |
|------|---------|---------|------|
| L0 | `run()` 入口 | `not transcript` | 直接返回 |
| L1 | `run()` 入口 | `len(transcript) < 3` | 记录 debug log，返回 |
| L2 | `run()` 入口 | 单用户消息 + 短内容 + 无工具 | 记录 debug log，返回 |
| L3 | Step1 内部 | `extract_user_profile()` 返回空 | 跳过 Step2a |
| L4 | Step1 内部 | `extract_experience()` 返回空 | 跳过 Step2b |

### 2.2 详细守卫逻辑

#### L1: 硬阈值守卫

```python
# nanobot/memory/orchestrator.py
async def run(self, event: SessionEndEvent) -> None:
    # L1: 硬阈值
    if len(event.transcript) < 3:
        logger.debug(f"[S1] skip: transcript has {len(event.transcript)} messages, need >= 3")
        return
```

#### L2: 短消息质量守卫

```python
    # L2: 短消息质量检查
    user_msgs = [m for m in event.transcript if m.get("role") == "user"]
    if len(user_msgs) == 1:
        content = (user_msgs[0].get("content") or "").strip()
        has_tool_calls = any(m.get("tool_calls") for m in event.transcript)
        # 短内容 + 无工具调用 = 跳过
        if len(content) < 10 and not has_tool_calls:
            logger.debug("[S1] skip: single short message without tool calls")
            return
```

### 2.3 事件接入方案

#### 2.3.1 WebUI 端点

新增 REST 端点接收前端会话结束信号：

```
POST /api/sessions/{session_key}/end
Body: { "reason": "user_close" }
```

#### 2.3.2 事件发射流程

```
WebUI 点击"结束会话"
    ↓
POST /api/sessions/{session_key}/end
    ↓
API Server 发射 SESSION_END event
    ↓
SessionEndOrchestrator.run(reason=USER_CLOSE)
    ↓
走完整 4 步编排
```

#### 2.3.3 API Server 实现

```python
# nanobot/api/endpoints/sessions.py
@router.post("/api/sessions/{session_key}/end")
async def end_session(session_key: str, request: Request):
    reason = request.json().get("reason", "user_close")
    await event_bus.emit(SessionEndEvent(
        session_key=session_key,
        reason=SessionEndReason(reason),
        transcript=await session_manager.get_transcript(session_key),
    ))
```

---

## 3. 文件变更清单

### 3.1 修改文件

| 文件 | 变更内容 |
|------|---------|
| `nanobot/memory/orchestrator.py` | 添加 L1/L2 守卫逻辑 |
| `nanobot/api/endpoints/sessions.py` | 新增 `POST /sessions/{key}/end` 端点 |
| `nanobot/memory/extractor.py` | Step1 内部添加 L3/L4 守卫 |

### 3.2 新增文件

| 文件 | 说明 |
|------|------|
| `nanobot/api/schemas.py` | `SessionEndRequest` schema |
| `tests/unit/memory/test_orchestrator_guards.py` | 守卫逻辑单元测试 |

---

## 4. 测试用例

### 4.1 守卫逻辑测试

| 用例 | 输入 | 预期行为 |
|------|------|---------|
| 空会话 | `transcript = []` | L0 拦截，不调 LLM |
| 单条"你好" | `transcript = [msg("你好")]` | L1 拦截 |
| 单条长消息 | `transcript = [msg("帮我写一个...")]` | L1 放行，继续 |
| 三条消息 | `transcript = [u1, a1, u2]` | L1 放行，继续 |
| 短消息+工具调用 | `transcript = [u1, tool_call]` | L2 豁免，继续 |
| 正常会话 | `transcript = [u1, a1, u2, a2]` | 全部放行 |

### 4.2 事件路由测试

| 用例 | 触发方式 | 预期行为 |
|------|---------|---------|
| 进程退出 | `on_finally` | `reason = PROCESS_SHUTDOWN` |
| 用户关闭 | `POST /sessions/{key}/end` | `reason = USER_CLOSE` |

---

## 5. 非功能性考量

### 5.1 幂等保护

继承现有 `IDEMPOTENCY_WINDOW_SECONDS = 30` 机制，防止重复触发。

### 5.2 日志规范

```python
logger.debug(f"[S1] skip: transcript has {len(event.transcript)} messages, need >= 3")
logger.debug("[S1] skip: single short message without tool calls")
logger.info(f"[S1] running: {len(event.transcript)} messages, reason={event.reason}")
```

### 5.3 向后兼容

- 无破坏性变更
- 现有 `PROCESS_SHUTDOWN` 触发路径保持不变
- 新增 `USER_CLOSE` 端点为可选增强

---

## 6. 优先级与排期

| 任务 | 优先级 | 复杂度 | 预估工作量 |
|------|--------|--------|-----------|
| L1/L2 守卫逻辑 | P0 | 低 | 1 WU |
| Step1 内部守卫 | P0 | 低 | 0.5 WU |
| WebUI 端点接入 | P1 | 中 | 1 WU |
| 单元测试 | P1 | 低 | 0.5 WU |

---

## 7. 验收标准

- [ ] 单条"你好"会话不触发 LLM 提取
- [ ] 三条消息且无工具调用的会话不触发 LLM 提取
- [ ] 短消息但有工具调用的会话正常触发提取
- [ ] `POST /sessions/{key}/end` 能触发完整 4 步编排
- [ ] 守卫逻辑有单元测试覆盖

---

## 8. 附录：openakita 参考

关键借鉴自 `openakita/src/openakita/memory/extractor.py`：

```python
# extract_from_conversation
user_turns = [t for t in turns if t.role == "user" and len(content) >= 10]
if not user_turns:
    return []

# extract_experience_from_conversation
if len(assistant_turns) < 2:
    return []
```
