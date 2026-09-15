---
artifact: implementation-plan
route: superpowers:writing-plans
source:
  - systematic-debugging 结论（2026-09-15 会话）
  - .ai-runtime-artifacts/plans/2026-09-15-session-end-idle-extraction-plan.md（Task 2 原意）
created_at: 2026-09-15
status: draft
approved: false
---

# Plan：idle 抽取接回完整流水线（LLM + episode 落库 + 画像/经验）

## 1. 诊断结论（已取证）

正常聊天的记忆提取链路**在当前实现下是死的**，两个症状同一根因。

### 1.1 症状

| 症状 | 观察 |
| --- | --- |
| 不调 LLM | idle 提取日志 `memories=[] episodes=[] skipped=0 failed=[]`，网关日志无任何 LLM 调用 |
| episode 不落库 | `state.db` 中 `episodes` 表恒为 0 行（21 条消息的会话跑完后仍为 0） |

### 1.2 根因：idle 路径委派给了 rule-only 的旧方法

`run_idle_extraction` → `extract_incremental`。而 `extract_incremental` 是 96d639c
（2026-09-13）为 T5 话题切换写的轻量路径，该 commit message 明写：

> WU-2: extract_incremental(session, last_extracted_index) 增量抽取入口 **(rule-only, no LLM)**

ca61552（2026-09-15，WU-A Task 2）把它接成了 idle 入口，并原样保留该语义：

> delegates to extract_incremental **(no LLM)** ... pure rule-scan path makes zero LLM calls

后果链条：

1. `extract_incremental` 只构造 `LLMExtractionResult(memories=candidates)`（纯正则片段），
   **从不调 `_llm_extract`** → 不调 LLM。
2. 全仓库唯一调 `_llm_extract` 的是 `extract_session`（extractor.py:685），
   而它的调用方只剩 `loop.py:579` 的 `source="deletion"` → 正常聊天不经过它。
3. episode 只在 `_persist` 的 `if episode is not None or filtered.action_nodes:` 分支写
   （extractor.py:1322）。idle 路径喂进去的 `filtered` 里 episode=None、action_nodes=[]
   → 条件恒 False → episode 永不落库。

### 1.3 与 plan 原意相反

| plan 原文（2026-09-15-session-end-idle-extraction-plan.md） | 位置 |
| --- | --- |
| 「抽取在跑、新消息进来 → 不取消，让 **LLM 跑完**」 | 决策 #3 |
| `assert "msg3" in captured_prompt` / `_FakeProvider(semantic="坏", episode="坏")` | Task 2 测试 |
| 「`last_count == current_count` 时 **0 LLM 调用**」（言下之意：有新消息时非 0） | Task 2 DoD |
| 「**LLM** 在 idle 抽取时崩了 → state 不推进，下次重试」 | 风险表 |

`tests/memory/test_extractor_incremental.py` 整个文件（含
`test_run_idle_pure_rule_scan_no_llm_call`）把偏离写成了断言，故测试全绿而功能是死的。

### 1.4 关键发现：画像/经验**无需新写代码**

`SEMANTIC_EXTRACTION_PROMPT`（prompts.py:10）已设计为双轨输出 `memories[]`（用户画像）
+ `experiences[]`（任务经验）；`_llm_extract` 把两者合并进同一个 memories 列表
（extractor.py:1012-1013），由 `_persist` 落到 `memories` 表。

**即「画像/经验 LLM 提取」早已实现，只是从未被调用。** 修好调用链即可获得，
不需要另写 LLM 提取代码，也不需要给 `extract_user_profile` / `extract_experience`
两个 TODO 占位写实现（它们与 semantic track 职责重叠）。

---

## 2. 范围

**做：**
- WU-1：`extract_incremental` 改为在新增切片上跑完整流水线
- WU-2：收敛 `SessionEndOrchestrator` 的重复/错误触发
- WU-3：测试重写 + 端到端验证

**不做：**
- 不为 `extract_user_profile` / `extract_experience` 写独立 LLM 实现（见 §1.4）
- 不改 prompt 内容
- 不改前端

---

## 3. WU-1：`extract_incremental` 完整流水线（核心）

**File**：`nanobot/memory/extractor.py`（仅 `extract_incremental` 方法体）

**改法**：把 rule-only 实现替换为「切片 Session → 阶段1 + 阶段2 → 阶段3 → 阶段4」。

```python
async def extract_incremental(
    self,
    session: Session,
    last_extracted_index: int,
    *,
    source: str = "topic_change",
) -> ExtractionResult:
    """增量抽取：只把 messages[last_extracted_index:] 喂给完整四阶段流水线。

    与 extract_session 的差别仅在输入切片，流水线本身完全一致 —— 因此语义记忆
    （含 SEMANTIC_EXTRACTION_PROMPT 的 memories[] 画像 + experiences[] 经验）与
    episode 都会正常产出并落库。
    """
    if not session.messages:
        return ExtractionResult()
    start = max(0, last_extracted_index)
    new_messages = session.messages[start:]
    if not new_messages:
        logger.info(
            "{} extraction: no new messages for session {} (start={})",
            source, session.key, start,
        )
        return ExtractionResult()

    from nanobot.session.manager import Session as _Session

    slice_session = _Session(
        key=session.key,
        messages=[dict(message) for message in new_messages],
        created_at=session.created_at,
    )
    try:
        system = self._system_extract(slice_session)
        llm_result = await self._llm_extract(slice_session, system)
        existing = self._load_existing_memories(llm_result.memories)
        filtered = self._apply_filters(llm_result, existing)
        persisted = self._persist(filtered, slice_session, source=source)
    except Exception as exc:  # noqa: BLE001 - 增量抽取失败绝不能上抛
        logger.opt(exception=exc).warning("incremental extraction failed: {}", exc)
        return ExtractionResult()

    logger.info(
        "{} extraction for session {}: {} new messages -> {} memories, {} episodes "
        "(skipped={} failed_tracks={})",
        source, session.key, len(new_messages),
        len(persisted.memory_ids), len(persisted.episode_ids),
        len(llm_result.memories) - len(filtered.memories), llm_result.failed_tracks,
    )
    return ExtractionResult(
        memory_ids=persisted.memory_ids,
        episode_ids=persisted.episode_ids,
        skipped=len(llm_result.memories) - len(filtered.memories),
        failed_tracks=list(llm_result.failed_tracks),
    )
```

**成本**：每次 idle 触发 = 2 次 LLM 调用（semantic + episode 并发）。
这正是 plan 决策 #3 的设计意图，也是「2 分钟无消息才抽一次」的意义。

**收益**：`memories`（画像/经验）与 `episodes` 同时落库。

**DoD**：
- idle 触发后 `state.db` 的 `memories`、`episodes` 表均有新增行
- 网关日志出现 `idle extraction for session ...: N new messages -> X memories, Y episodes`

---

## 4. WU-2：收敛 `SessionEndOrchestrator`

**问题**：`MemoryExtractionHook.on_finally`（memory_extraction.py:347-362）在**每一条消息**
的 run 收尾都 fire 一次 `SessionEndOrchestrator.run(reason=PROCESS_SHUTDOWN)`。
但 `on_finally` 是 per-run 收尾，不是进程退出。

**后果**：
- 与 WU-1 后重复做 LLM 提取（同一份转录抽两遍）
- 30s 幂等窗口只压制了爆量，不改变语义错误
- `generate_episode` 生成 Episode 却**不入库**（docstring 明写「不入库，由调用方决定」），
  编排器拿到后只读 `.id`/`.summary` 就丢弃 → 纯粹浪费

**改法（推荐 A，待确认）**：

- **A（推荐）**：从 `on_finally` 移除 SessionEnd 派发，保留编排器类与 `USER_CLOSE` 能力，
  仅在真正的会话结束事件触发。常态提取完全交给 idle 定时器。
- **B**：保留每轮派发，但把编排器改为调用同一完整流水线 —— 与 idle 重复，不推荐。
- **C**：整体删除编排器 —— 若 `USER_CLOSE` 端点近期不做，可选。

**DoD**：一条消息只产生一轮提取 LLM 调用（由 idle 定时器触发）。

---

## 5. WU-3：测试

**改：`tests/memory/test_extractor_incremental.py`**

当前 5 处「不调 LLM」断言需反转（文件级 docstring 第 5、9 行 + 第 103、126、175、187、
221、244、253-262 行）。改为：

- `test_incremental_calls_llm_on_new_slice`：provider 被调用，prompt 只含 `messages[start:]`
- `test_incremental_persists_episode`：切片含工具调用时 `_persist` 落 episode
- `test_incremental_persists_profile_and_experience`：semantic payload 的 `memories[]` 与
  `experiences[]` 都进 `memories` 表
- `test_incremental_llm_failure_does_not_raise`：失败隔离仍然成立
- 保留：`no-op when current_count == last_count`（该分支 0 LLM 调用仍然正确）

**新增：`tests/memory/test_idle_extraction_e2e.py`**

- 伪 provider 返回固定 semantic/episode payload → 跑 `run_idle_extraction` →
  断言 `memories` / `episodes` 表有行、`session_extraction_state.last_count` 已推进

**回归**：`pytest tests/memory/ tests/config/ -q` 全绿；`ruff check` 干净。

---

## 6. 验证口径

1. `uv run --no-sync pytest tests/memory/ tests/config/ -q` 全绿
2. `uv run --no-sync ruff check nanobot/ tests/` 干净
3. **端到端**：重启 gateway → 发若干条含偏好的消息 → 等 idle 阈值 →
   查 `state.db`：
   ```sql
   SELECT count(*) FROM memories;
   SELECT count(*) FROM episodes;
   SELECT * FROM session_extraction_state;
   ```
   三张表均有数据，且日志出现 `idle extraction for session ...: N new messages -> X memories, Y episodes`

---

## 7. 风险

| 风险 | 等级 | 缓解 |
| --- | --- | --- |
| LLM 在 idle 抽取时失败 | 低 | `run_idle_extraction` 已有 try/except + state 不推进 → 下次重试 |
| 增量切片丢失前文，episode 质量下降 | 中 | plan 原意即如此；如质量不佳，后续可把「切片 + 前 N 条上下文」一并喂入 |
| 存量 `last_count=21` 游标导致老会话不重抽 | 低 | 需要时用 `reset_session_extraction_state` 重置 |
