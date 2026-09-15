---
artifact: implementation-plan
route: superpowers:writing-plans
skills:
  - writing-plans
skills_evidence:
  - harness-kit/.agents/skills/writing-plans/SKILL.md
dispatch: .ai-runtime-artifacts/plans/2026-09-15-session-end-idle-extraction-dispatch.md
source:
  - AGENTS.md
  - core/routing.md
  - decisions made in conversation 2026-09-15 (user-approved: 10 min idle + incremental extraction + working memory removed from UI)
created_at: 2026-09-15
status: draft
approved: false
---

# Plan：会话搁置触发增量抽取 + 移除工作记忆 UI 表面

> 范围含后端数据流改造（schema / hook 时序 / 新增增量抽取路径）与前端清理（删除工作记忆 tab）。**两个 WU 可并行**（不同文件树，无共享接口变更）。spec 阶段被用户主动跳过，决策来源为本会话内确认的 6 个产品决定（见正文 §决策清单）。

---

## Goal

让记忆抽取的触发**符合「会话真正结束」的语义**（= 该 session 10 分钟内无新消息），且**只抽未抽过的新消息**。同时把工作记忆的所有 UI 表面（设置 tab / chat header 图标 / 回复尾部）全部移除，让工作记忆**仅以「AI 在对话里自然引用」的方式被用户感知**。

完成后：
- 用户连续发 N 条消息 → 0 次抽取（直到 10 分钟无新消息）
- 10 分钟到点 → 1 次增量抽取（只处理 last_count 之后的 messages）
- 用户回来再发 → 上一轮抽过的消息不重抽
- 工作记忆**对用户完全不可见**

## 决策清单（本会话确认，不走 spec）

| # | 决策 | 决定 |
| --- | --- | --- |
| 1 | 搁置阈值 | **600 秒（10 分钟），可配置**（gateway 层配置项，不入 WebUI 设置页） |
| 2 | 新消息进来时旧定时器 | **取消 + 重新计时**（同 session 单定时器） |
| 3 | 抽取在跑、新消息进来 | **不取消**，让 LLM 跑完（下一轮 turn 的 idle 定时器正常启动） |
| 4 | 抽取范围 | **增量**（用 `session_extraction_state.last_extracted_count` 作游标） |
| 5 | session 被 context_compress 压缩 | 走 `extract_session(source="context_compress")` 全量，**完成后同步更新 `last_count = current_count`**，避免 idle 路径再翻旧账 |
| 6 | 工作记忆 UI | **全部移除**：删设置 tab、不加 chat header 图标、不加回复尾巴 |

## Architecture（现状 → 目标）

### 现状

- `MemoryExtractionHook.after_run` 每轮 turn 触发 `_schedule_run_extraction`（[memory_extraction.py:460](nanobot/agent/hooks/memory_extraction.py#L460)）
- 该任务调 `extractor.extract_session(session, source="session_end")`（[memory_extraction.py:511](nanobot/agent/hooks/memory_extraction.py#L511)）
- `extract_session` 是**全量**：每轮都把整个 `session.messages` 喂给 LLM（[extractor.py:671](nanobot/memory/extractor.py#L671)）
- `on_finally` 等待最多 5 秒，超时取消（[memory_extraction.py:474-498](nanobot/agent/hooks/memory_extraction.py#L474-L498)）—— LLM 慢一点就被砍
- 无「上次抽到哪」状态，**无法做增量**
- 工作记忆在 WebUI「设置 → 记忆」有 `scratchpad` tab（[MemorySection.tsx:13-17](webui/src/components/settings/memory/MemorySection.tsx#L13-L17)）

### 目标

- `MemoryExtractionHook.after_run` **不再立即抽**，改为：(a) 取消该 session 的旧 idle 定时器 (b) 启动新定时器 `asyncio.sleep(IDLE_THRESHOLD_SECONDS)`
- 定时器到点 → 调 `extractor.extract_incremental(session, start_index=last_count, source="idle")`（fire-and-forget，不再 `on_finally` 等）
- 新表 `session_extraction_state`（per-session 游标）持久化 `last_count`，gateway 重启可恢复
- `extract_session` 保留：只给 `source="deletion"`（[loop.py:578](nanobot/agent/loop.py#L578)）和 `source="context_compress"`（[extractor.py:728](nanobot/memory/extractor.py#L728)）用
- `EXTRACTION_WAIT_TIMEOUT` 删掉（fire-and-forget 不再需要等）
- WebUI `MemorySection` 只剩 `semantic` / `episode` 两个 tab

### 关键数据流

```
[Turn N 完成]
   ↓
MemoryExtractionHook.after_run()
   ├─ T0: 写 scratchpad.current_focus（保留，纯规则，不调 LLM）
   └─ _arm_idle_extraction_timer(session_key)
         ├─ _PENDING_IDLE_TIMERS[session_key].cancel()  # 取消旧定时器
         └─ new task: asyncio.sleep(600) → _run_idle_extraction()

[10 分钟到点 / 期间无新消息]
   ↓
_run_idle_extraction(session)
   ├─ SELECT last_count FROM session_extraction_state WHERE session_key=?
   ├─ 若 current_count == last_count：return（无事可做）
   ├─ extractor.extract_incremental(session, start_index=last_count, source="idle")
   └─ UPSERT last_count = current_count（仅抽取成功时）
```

```
[用户在 idle 期间发新消息]
   ↓
新的 after_run 触发
   ↓
cancel(_PENDING_IDLE_TIMERS[session_key])  # 旧定时器死
   ↓
启动新定时器，从 0 重新计 10 分钟
   ↓
（如果旧定时器到点时正在抽 LLM，让它跑完；不 cancel）
```

## Tech Stack

- 后端：Python 3.11+、asyncio、SQLite、现有 Pydantic v2
- 前端：React 18 + TS、react-i18next
- 依赖无新增

---

## 文件改动清单

### 后端（WU-A）

| 文件 | 改动 |
| --- | --- |
| `nanobot/memory/database.py` | schema v2：新建表 `session_extraction_state` |
| `nanobot/memory/repository.py` | 新增 `get_extraction_state` / `upsert_extraction_state` / `reset_extraction_state` |
| `nanobot/memory/extractor.py` | `extract_incremental` 改用新 state 表（替代原 `_compute_incremental_start_index` 启发式）；新增薄方法 `run_idle_extraction` 包装 state 读写 |
| `nanobot/agent/hooks/memory_extraction.py` | 删 `_schedule_run_extraction` / `_await_pending_extractions` / `_run_extraction`；改用 `_arm_idle_extraction_timer` + 模块级 `_PENDING_IDLE_TIMERS: dict[str, asyncio.Task]`；新增 `_run_idle_extraction` |
| `nanobot/agent/hooks/memory_extraction.py` | `on_finally` 改为只取消本 session 的 idle 定时器（防止 session 死亡后定时器还在跑） |
| `nanobot/config/schema.py` | `AgentDefaults` 加 `memory_idle_seconds: int = 600`（可配） |
| `nanobot/memory/scratchpad_writer.py` | `update_focus` 写完后**不再**写 `last_focus_change_at`（本计划不引入该字段） |
| `tests/memory/test_extraction_state.py`（新） | state 表 CRUD / 推进 / 重置 / 并发 |
| `tests/memory/test_extractor_incremental.py`（新） | `extract_incremental` 走 state 路径 / 压缩时重置 / 失败不推进 |
| `tests/memory/test_hook_idle_timer.py`（新） | 定时器到点触发 / 新消息取消 / 多 session 互不干扰 / session 删除时取消 |

### 前端（WU-B）

| 文件 | 改动 |
| --- | --- |
| `webui/src/components/settings/memory/MemorySection.tsx` | 删 `scratchpad` tab + `ScratchpadEditor` import + `MemoryTab` 类型第三个值 |
| `webui/src/components/settings/memory/ScratchpadEditor.tsx` | **整个文件删除**（无引用方后） |
| `webui/src/i18n/locales/{en,zh-CN,es,fr,id,ja,ko,pt-BR,vi}/common.json` | 删 `settings.memory.tabScratchpad` key |
| `webui/src/tests/settings-memory-section.test.tsx` | 改断言：tab 列表只含 `semantic` / `episode`，不含 `scratchpad` |

### 不动的文件

- `nanobot/memory/prompts.py`（不动 prompt，工作记忆不展示意味着不需要让 LLM 知道它"被显示"）
- `nanobot/memory/intent.py`（T0 不动）
- `webui/src/components/thread/ThreadHeader.tsx`（不加图标）
- 任何 `extract_session` 的调用方（`source="deletion"` 和 `"context_compress"` 路径）

---

## Task 拆分

> 编号延续项目惯例 `Task N: <组件名>`。**WU-A 与 WU-B 无共享文件，可完全并行**。

---

### Task 0：实施前确认（阻塞 Task 1 启动）

**目标**：消除 3 个潜在歧义点。

**步骤**：
1. `grep -n "session_extraction_state" nanobot/memory/` —— 确认表名不与现有 schema 冲突
2. `grep -n "MemoryDatabase.init_schema" nanobot/memory/database.py` —— 看现有 schema migration 模式（是否已分 v1/v2）
3. `cat nanobot/agent/hooks/memory_extraction.py | head -80` —— 看 `MemoryExtractionHook` 是否被其他代码直接 `__init__`（决定 `__init__` 签名能否收紧）

**DoD**：3 项 grep 结论落到执行日志；如有冲突，调整 plan

---

### WU-A：后端（4 个 Task，可串行）

#### Task 1：schema + repository（最小可测单元）

**Files**：
- Modify: `nanobot/memory/database.py`
- Modify: `nanobot/memory/repository.py`
- Create: `tests/memory/test_extraction_state.py`

**Step 1：写失败测试（state 表 CRUD）**

```python
def test_extraction_state_upsert_and_get(db):
    upsert_extraction_state(db.connect(), "s1", last_count=5, source="idle")
    state = get_extraction_state(db.connect(), "s1")
    assert state.last_count == 5
    assert state.source == "idle"

def test_extraction_state_reset(db):
    upsert_extraction_state(db.connect(), "s1", last_count=10, source="idle")
    reset_extraction_state(db.connect(), "s1")
    state = get_extraction_state(db.connect(), "s1")
    assert state is None or state.last_count == 0
```

**Step 2：实现**

- `database.py` 的 `init_schema` 加：
  ```sql
  CREATE TABLE IF NOT EXISTS session_extraction_state (
      session_key TEXT PRIMARY KEY,
      last_count INTEGER NOT NULL,
      last_source TEXT NOT NULL,
      last_extracted_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
  );
  ```
- `repository.py` 加 3 个函数（沿用现有 add_memory 风格：`conn` 第一个参，`with self.database.connect() as conn:` 由调用方管理）
- `models.py` 加 `ExtractionState` dataclass

**Step 3：跑测试**

- `pytest tests/memory/test_extraction_state.py -q` 全绿
- `pytest tests/memory/ -q` 全绿（确认不破坏现有）

**DoD**：state 表 CRUD 测试 100% 过；现有 `tests/memory/` 全绿

---

#### Task 2：`MemoryExtractor.extract_incremental` 改用新 state 表

**Files**：
- Modify: `nanobot/memory/extractor.py`（`extract_incremental` 方法 + 新增 `run_idle_extraction` 包装）
- Create: `tests/memory/test_extractor_incremental.py`

**Step 1：写失败测试**

```python
async def test_incremental_uses_state_table_start_index(db, tmp_path):
    # state 表里有 last_count=3
    upsert_extraction_state(db.connect(), "s1", last_count=3, source="idle")
    # session 有 5 条消息
    session = _session(messages=[{"role": "user", "content": f"msg{i}"} for i in range(5)])
    # 调 extract_incremental，应该只看到 messages[3:]
    result = await extractor.extract_incremental(session, source="idle")
    # 验证 LLM prompt 里只含 msg3 / msg4
    assert "msg0" not in captured_prompt
    assert "msg3" in captured_prompt
    # 验证成功后 state.last_count 被推到 5
    state = get_extraction_state(db.connect(), "s1")
    assert state.last_count == 5


async def test_incremental_failure_does_not_advance_state(db):
    upsert_extraction_state(db.connect(), "s1", last_count=3, source="idle")
    provider = _FakeProvider(semantic="坏", episode="坏")  # 让 LLM 失败
    extractor = _make_extractor(db, provider)
    session = _session(messages=[{"role": "user", "content": "msg4"}] * 4)
    result = await extractor.extract_incremental(session, source="idle")
    # 失败时 last_count 应保持 3，下次能重试
    state = get_extraction_state(db.connect(), "s1")
    assert state.last_count == 3


async def test_incremental_resets_when_session_shrinks(db):
    # state 记录 last_count=10，但当前 session 只有 5 条（被压缩过）
    upsert_extraction_state(db.connect(), "s1", last_count=10, source="idle")
    session = _session(messages=[{"role": "user", "content": "msg"}] * 5)
    # 触发 extract_incremental → 内部应重置 last_count=0 并全量
    ...
```

**Step 2：实现**

```python
async def run_idle_extraction(self, session: Session) -> ExtractionResult:
    """Idle 定时器触发的入口：读 state、调 incremental、推进 state。"""
    with self.database.connect() as conn:
        state = get_extraction_state(conn, session.key)
        last_count = state.last_count if state else 0
    
    current_count = len(session.messages)
    if current_count <= last_count:
        return ExtractionResult(memory_ids=[], episode_ids=[], skipped=0)
    
    # 防御：session 被压缩过 → 重置并当作全量
    start_index = last_count if current_count > last_count else 0
    
    try:
        result = await self.extract_incremental(
            session, last_extracted_index=start_index, source="idle"
        )
    except Exception as exc:
        logger.warning("idle extraction failed for session {}: {}", session.key, exc)
        return ExtractionResult(memory_ids=[], episode_ids=[], skipped=0, failed_tracks=["exception"])
    
    # 成功才推进 state
    with self.database.connect() as conn:
        upsert_extraction_state(
            conn, session.key,
            last_count=current_count,
            source="idle",
            extracted_at=_now_iso(),
        )
    return result
```

`extract_incremental` 保留 `_compute_incremental_start_index` 作为内部实现，但**外部入口**用 `run_idle_extraction` 包装 state 读写。

**Step 3：跑测试**：`pytest tests/memory/test_extractor_incremental.py -q` 全绿

**DoD**：3 个新测试过；`extract_incremental` 在 `last_count == current_count` 时 0 LLM 调用

---

#### Task 3：`MemoryExtractionHook` 替换为 idle 定时器

**Files**：
- Modify: `nanobot/agent/hooks/memory_extraction.py`（大改：删 `_schedule_run_extraction` / `_await_pending_extractions` / `_run_extraction` / `EXTRACTION_WAIT_TIMEOUT`）
- Modify: `nanobot/agent/hooks/memory_extraction.py`（新增模块级 `_PENDING_IDLE_TIMERS: dict[str, asyncio.Task] = {}` + 类方法 `_arm_idle_timer` / `_run_idle_extraction`）
- Modify: `nanobot/agent/hooks/memory_extraction.py`（`on_finally` 简化为取消本 session 的 idle 定时器）
- Create: `tests/memory/test_hook_idle_timer.py`

**Step 1：写失败测试**

```python
async def test_after_run_arms_idle_timer():
    hook = _make_hook()
    await hook.after_run(_run_ctx("我叫什么"))
    assert "s1" in _PENDING_IDLE_TIMERS
    assert not _PENDING_IDLE_TIMERS["s1"].done()


async def test_after_run_cancels_previous_idle_timer():
    hook = _make_hook()
    await hook.after_run(_run_ctx("msg1"))
    first_task = _PENDING_IDLE_TIMERS["s1"]
    await hook.after_run(_run_ctx("msg2"))
    assert first_task.cancelled() or first_task.done()
    assert _PENDING_IDLE_TIMERS["s1"] is not first_task


async def test_idle_timer_fires_extraction():
    hook = _make_hook(extractor=mock_extractor)
    hook.IDLE_THRESHOLD_SECONDS = 0.05  # 加速测试
    await hook.after_run(_run_ctx("msg"))
    await asyncio.sleep(0.1)
    assert mock_extractor.run_idle_extraction.call_count == 1


async def test_session_delete_cancels_idle_timer():
    hook = _make_hook()
    await hook.after_run(_run_ctx("msg"))
    task = _PENDING_IDLE_TIMERS["s1"]
    # 模拟 session 删 → on_finally 取消定时器
    await hook.on_finally(_run_ctx())
    assert task.cancelled()
```

**Step 2：实现**

```python
# 模块级（替换 _BACKGROUND_TASKS 旁的现有定义）
_PENDING_IDLE_TIMERS: dict[str, asyncio.Task[Any]] = {}


class MemoryExtractionHook(AgentHook):
    IDLE_THRESHOLD_SECONDS: float = 600.0
    # EXTRACTION_WAIT_TIMEOUT 删掉
    
    async def after_run(self, context):
        if self._memory_disabled():
            return
        try:
            await self._apply_immediate_focus(context)
        except Exception:
            logger.exception(...)
        try:
            self._arm_idle_timer(context)
        except Exception:
            logger.exception(...)
    
    def _arm_idle_timer(self, context):
        # 取消该 session 的旧定时器
        existing = _PENDING_IDLE_TIMERS.get(self._session_key)
        if existing and not existing.done():
            existing.cancel()
        
        # 启动新定时器
        async def _wait_then_extract():
            try:
                await asyncio.sleep(self.IDLE_THRESHOLD_SECONDS)
            except asyncio.CancelledError:
                return
            await self._run_idle_extraction(context)
        
        task = asyncio.create_task(_wait_then_extract())
        _PENDING_IDLE_TIMERS[self._session_key] = task
        task.add_done_callback(
            lambda t: _PENDING_IDLE_TIMERS.pop(self._session_key, None)
        )
    
    async def _run_idle_extraction(self, context):
        session = Session(key=self._session_key, messages=list(context.messages))
        try:
            await self._extractor.run_idle_extraction(session)
        except Exception:
            logger.warning("idle extraction failed for session {}", self._session_key)
    
    async def on_finally(self, context):
        # 不再等任务完成（fire-and-forget）。只清理本 session 的定时器。
        task = _PENDING_IDLE_TIMERS.pop(self._session_key, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
```

**Step 3：跑测试**

- `pytest tests/memory/test_hook_idle_timer.py -q` 全绿
- `pytest tests/memory/test_hook_memory_extraction.py -q` —— **预期有失败**（T5 测试 + 旧 T1 测试依赖被删的 `_schedule_run_extraction` / `EXTRACTION_WAIT_TIMEOUT`）

**DoD**：
- 新 timer 测试全绿
- 旧 `test_hook_memory_extraction.py` 的失败在**预期清单**内（详见 Task 5：删死测试）

---

#### Task 4：`AgentDefaults.memory_idle_seconds` + 测试可注入

**Files**：
- Modify: `nanobot/config/schema.py`（`AgentDefaults` 加 `memory_idle_seconds: int = 600`）
- Modify: `nanobot/agent/hooks/memory_extraction.py`（`MemoryExtractionHook.__init__` 加 `idle_seconds: float | None = None`，优先级：实例属性 > 类默认）

**Step 1：写失败测试**

```python
def test_agent_defaults_has_memory_idle_seconds():
    defaults = AgentDefaults()
    assert defaults.memory_idle_seconds == 600

def test_hook_uses_instance_idle_seconds():
    hook = _make_hook()
    hook.IDLE_THRESHOLD_SECONDS = 0.01
    # 验证 timer 用了 0.01 而不是 600
    ...
```

**Step 2：实现**：直白字段 + 注入机制（保持向后兼容：gateway 不传 = 用默认 600）

**DoD**：测试过；现有 `pytest tests/ -q` 全绿（除 Task 3 列出的预期失败）

---

### WU-B：前端（2 个 Task，可串行）

#### Task 5：删 `scratchpad` tab + 删 `ScratchpadEditor`

**Files**：
- Modify: `webui/src/components/settings/memory/MemorySection.tsx`
  - 删 `MemoryTab = "semantic" | "episode" | "scratchpad"` 第三个值
  - 删 `MEMORY_TABS` 数组第三项
  - 删 `import { ScratchpadEditor }`
  - 删 `{tab === "scratchpad" && <ScratchpadEditor ... />}` 分支
  - 删对应的 i18n key 引用（`tabScratchpad`）
- Delete: `webui/src/components/settings/memory/ScratchpadEditor.tsx`
- Modify: `webui/src/i18n/locales/{en,zh-CN,es,fr,id,ja,ko,pt-BR,vi}/common.json`
  - 删 `"settings.memory.tabScratchpad"` 键
  - 删其他与 working memory / scratchpad 相关的 UI label（如果有）

**Step 1：grep 引用方**

```bash
grep -rln "ScratchpadEditor\|tabScratchpad\|working memory\|工作记忆" webui/src/
```

确认无其他文件引用。

**Step 2：删 + 改**

按 Files 清单操作。

**Step 3：typecheck + 测试**

- `cd webui && npx --no-install tsc --noEmit` 干净
- `cd webui && npx --no-install vitest run src/tests/settings-memory-section.test.tsx` 绿

**Step 4：手动验证**

- 构建（`cd webui && bun run build`）→ dist 进 wheel
- WebUI 打开「设置 → 记忆」→ 只剩 2 个 tab

**DoD**：
- `tsc --noEmit` 无错
- `vitest` 全绿
- 9 个 locale JSON 校验通过（无悬空 key）
- 视觉验证：设置页只剩 2 tab

---

### WU-C：测试收尾（依赖 WU-A、WU-B，可最后单独跑）

#### Task 6：删死测试 + 补回归

**Files**：
- Modify: `tests/memory/test_hook_memory_extraction.py`
  - 删 `TestTopicChangeDetection` 全部（8 个，T5 已死）
  - 删 `TestTopicChangeIncrementalExtraction` 全部（12 个，T5 已死）
  - 删依赖 `_schedule_run_extraction` / `EXTRACTION_WAIT_TIMEOUT` / `_await_pending_extractions` 的旧 T1 测试
- Modify: `tests/memory/test_memory_extraction_hook.py`（同上的 T5 死测试清理）
- Create: `tests/memory/test_extraction_integration.py`（端到端：开总开关 → 模拟多次 turn + 暂停 → 验证 state.last_count 推进、memories 入库、scratchpad.current_focus 更新）

**Step 1：列出要删的测试**

`grep -n "EXTRACTION_WAIT_TIMEOUT\|_schedule_run_extraction\|TopicChangeDetection\|TopicChangeIncremental" tests/memory/test_hook_memory_extraction.py tests/memory/test_memory_extraction_hook.py`

**Step 2：删 + 跑**

- 删后 `pytest tests/memory/ -q` 应只剩 1 个预期失败（如果有）= 0
- 跑新增的 `test_extraction_integration.py`：模拟「T0 + idle timer + extract_idle + state 推进」完整链路

**DoD**：
- `pytest tests/memory/ -q` **0 失败**（之前 20 个 T5 死测试 + Task 3 列出的旧 T1 测试都清掉）
- 集成测试覆盖 happy path

---

## 风险与权衡

| 风险 | 概率 | 缓解 |
| --- | --- | --- |
| 用户高频短对话（10 分钟内来回 20 次）| 中 | **每轮都重置定时器** → 永不触发 → 用户感知不到自动抽取。这反而是设计目标 |
| LLM 在 idle 抽取时崩了 | 低 | `run_idle_extraction` 包 try/except + state 不推进 → 下次重试（幂等） |
| 同一 session 多 turn 并发 | 低 | 模块级 dict + cancel 旧任务 → 旧 task.done() 时新 task 才能注册。asyncio 单线程 → 无真正并发 |
| session 被 context_compress 压缩但 state 表未同步 | 中 | `extract_session(source="context_compress")` 完成后**强制 sync** state.last_count = current_count（见 Task 1 helper `reset_extraction_state`） |
| gateway 重启时 idle 定时器丢了 | 高 | **预期行为**：重启后用户发新消息，after_run 重新 arm 定时器。无数据丢失（state 表持久化） |
| 旧 `_schedule_run_extraction` 还有调用方没找到 | 低 | Task 0 grep 全覆盖；如发现新调用方，列入 plan 修订 |
| 旧 `EXTRACTION_WAIT_TIMEOUT` 还有测试 override | 已确认 | `test_hook_memory_extraction.py:267` 是 T5 路径里的，Task 6 一并清理 |

## 验收口径

完成后必须满足：

1. `pytest tests/memory/ -q` **0 失败**（包括 Task 6 删死测试后）
2. `pytest tests/agent/ -q` 全绿
3. `ruff check nanobot/ tests/` 无新增告警
4. WebUI `tsc --no-install tsc --noEmit` 干净
5. WebUI `vitest run` 全绿
6. 端到端手工验证（文档化的）：
   - 关闭总开关 → 发消息 → **不抽**（idle 定时器不启动）
   - 打开总开关 → 发消息 → 等 10 分钟 → memories 表新增 1~N 条
   - 10 分钟内连续发 5 条 → 只在最后那次起 10 分钟后抽 1 次
   - 抽取后立刻发新消息 → 上一轮的 messages **不**重抽（state.last_count 已推进）
   - 「设置 → 记忆」只剩 2 个 tab
   - 工作记忆在 WebUI **任何地方都看不到**

## 不在本次范围

- ❌ embedding-based semantic dedup（之前讨论过，超出"搁置触发"主线）
- ❌ 跨 session 的记忆关联（独立 feature）
- ❌ 工作记忆 prompt 工程（让 AI 主动引用）
- ❌ T0 `update_focus` 的去重判断（之前讨论过，但本次不动 T0）
- ❌ idle 阈值接入 WebUI 设置页（gateway 层配置，需要时再说）

---

## 派工建议

- **WU-A**（后端 Task 1-4）派给 **coder**，独占 worktree
- **WU-B**（前端 Task 5）派给 **coder**，独占 worktree
- WU-A 与 WU-B **无文件冲突**，可完全并行
- **WU-C**（Task 6 测试收尾）必须等 WU-A + WU-B 都合入后再做
- 派工前我会先写 `*-dispatch.md` 明确每个 WU 的边界
