# 记忆系统会话结束编排优化 - 实施计划

> 日期：2026-09-13
> 关联 Spec：`.ai-runtime-artifacts/specs/session-end-orchestrator-optimization.md`
> 状态：待批准

---

## 1. 工作单元分解

### WU-1: 守卫逻辑实现（feature/guard）

**类型：** feature
**执行者：** coder
**工作量：** 1 WU

#### 1.1 修改 `nanobot/memory/orchestrator.py`

在 `SessionEndOrchestrator.run()` 方法开头添加守卫：

```python
# L0: 空消息检查（已有保险）
if not event.transcript:
    return

# L1: 硬阈值
if len(event.transcript) < 3:
    logger.debug(f"[S1] skip: transcript has {len(event.transcript)} messages, need >= 3")
    return

# L2: 短消息质量检查
user_msgs = [m for m in event.transcript if m.get("role") == "user"]
if len(user_msgs) == 1:
    content = (user_msgs[0].get("content") or "").strip()
    has_tool_calls = any(m.get("tool_calls") for m in event.transcript)
    if len(content) < 10 and not has_tool_calls:
        logger.debug("[S1] skip: single short message without tool calls")
        return
```

#### 1.2 修改 `nanobot/memory/extractor.py`

在 `generate_episode()` 内部添加 Step 内部守卫：

```python
# Step2a: extract_user_profile
user_turns = [t for t in turns if t.role == "user" and len(coerce_text(t.content).strip()) >= 10]
if not user_turns:
    logger.debug("[S1] skip extract_user_profile: no valid user messages")
else:
    # 继续提取...

# Step2b: extract_experience
assistant_turns = [t for t in turns if t.role == "assistant" and coerce_text(t.content)]
if len(assistant_turns) < 2:
    logger.debug("[S1] skip extract_experience: less than 2 assistant turns")
else:
    # 继续提取...
```

#### 1.3 验证命令

```bash
pytest tests/unit/memory/test_orchestrator.py -v
ruff check nanobot/memory/orchestrator.py nanobot/memory/extractor.py
```

---

### WU-2: WebUI 端点接入（feature/user-close-endpoint）

**类型：** feature
**执行者：** coder
**工作量：** 1 WU
**前置依赖：** WU-1 完成

#### 2.1 新增 API Schema

```python
# nanobot/api/schemas.py
class SessionEndRequest(BaseModel):
    reason: str = "user_close"

    model_config = {"extra": "ignore"}
```

#### 2.2 新增 API 端点

```python
# nanobot/api/endpoints/sessions.py
@router.post("/api/sessions/{session_key}/end")
async def end_session(
    session_key: str,
    request: Request,
    session_manager: SessionManager = Depends(get_session_manager),
    event_bus: EventBus = Depends(get_event_bus),
):
    body = await request.json()
    reason_str = body.get("reason", "user_close")

    # 验证 reason 有效性
    try:
        reason = SessionEndReason(reason_str)
    except ValueError:
        reason = SessionEndReason.USER_CLOSE

    # 获取会话消息
    transcript = await session_manager.get_transcript(session_key)

    # 发射事件
    await event_bus.emit(SessionEndEvent(
        session_key=session_key,
        reason=reason,
        transcript=transcript,
    ))

    return {"status": "ok", "session_key": session_key}
```

#### 2.3 注册路由

确保 `sessions` 路由在 `nanobot/api/server.py` 中注册。

#### 2.4 验证命令

```bash
# 单元测试
pytest tests/unit/api/test_sessions.py -v

# 手动验证
curl -X POST http://localhost:8765/api/sessions/test-key/end \
  -H "Content-Type: application/json" \
  -d '{"reason": "user_close"}'
```

---

### WU-3: 单元测试补强（test/guard-unit-tests）

**类型：** test
**执行者：** test-engineer
**工作量：** 0.5 WU
**前置依赖：** WU-1 完成

#### 3.1 新增测试文件

```python
# tests/unit/memory/test_orchestrator_guards.py
import pytest
from unittest.mock import AsyncMock, MagicMock

class TestSessionEndOrchestratorGuards:
    @pytest.fixture
    def orchestrator(self):
        return SessionEndOrchestrator(...)

    @pytest.mark.parametrize("transcript,expected_skip", [
        ([], True),                          # L0: 空会话
        ([msg("你好")], True),                # L1: 单条消息
        ([msg("你好"), msg("嗨")], True),     # L1: 两条消息
        ([msg("帮我写代码..."), msg("好的")], False),  # 正常
    ])
    async def test_guard_levels(self, orchestrator, transcript, expected_skip):
        event = SessionEndEvent(session_key="test", reason=SessionEndReason.PROCESS_SHUTDOWN, transcript=transcript)
        # 验证是否跳过 LLM 调用
```

#### 3.2 验证命令

```bash
pytest tests/unit/memory/test_orchestrator_guards.py -v --cov=nanobot/memory/orchestrator
```

---

## 2. 文件变更清单

| 文件路径 | 操作 | 变更内容 |
|---------|------|---------|
| `nanobot/memory/orchestrator.py` | 修改 | 添加 L0/L1/L2 守卫 |
| `nanobot/memory/extractor.py` | 修改 | 添加 L3/L4 守卫 |
| `nanobot/api/schemas.py` | 新增 | `SessionEndRequest` schema |
| `nanobot/api/endpoints/sessions.py` | 新增 | `POST /sessions/{key}/end` 端点 |
| `nanobot/api/server.py` | 修改 | 注册 sessions 路由 |
| `tests/unit/memory/test_orchestrator_guards.py` | 新增 | 守卫逻辑测试 |
| `tests/unit/api/test_sessions.py` | 新增 | API 端点测试 |

---

## 3. 实现顺序

```
WU-1: 守卫逻辑实现（feature/guard）
    ↓
WU-2: WebUI 端点接入（feature/user-close-endpoint）
    ↓
WU-3: 单元测试补强（test/guard-unit-tests）
    ↓
[尾盘] verification-before-completion
    ↓
[尾盘] requesting-code-review
```

---

## 4. 验收标准

| # | 标准 | 验证方法 |
|---|------|---------|
| 1 | 单条"你好"会话不触发 LLM 提取 | 单元测试 |
| 2 | 两条消息会话不触发 LLM 提取 | 单元测试 |
| 3 | 三条消息且无工具调用的会话不触发 | 单元测试 |
| 4 | 短消息但有工具调用的会话正常触发 | 单元测试 |
| 5 | `extract_user_profile` 过滤短用户消息 | 单元测试 |
| 6 | `extract_experience` 要求 ≥2 assistant 轮次 | 单元测试 |
| 7 | `POST /sessions/{key}/end` 返回 200 | API 测试 |
| 8 | 事件正确发射到 event_bus | 集成测试 |

---

## 5. 风险与回滚

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 守卫过严导致正常会话被跳过 | 低 | 中 | L2 豁免有工具调用的会话 |
| API 端点引入回归 | 低 | 高 | 完整单元测试覆盖 |

**回滚方案：** 如发现问题，注释掉守卫代码即可恢复。

---

## 6. 后续步骤

- [ ] 用户确认计划
- [ ] 批准后进入实现阶段
- [ ] WU-1 → WU-2 → WU-3 顺序执行
- [ ] 尾盘 verification + review
