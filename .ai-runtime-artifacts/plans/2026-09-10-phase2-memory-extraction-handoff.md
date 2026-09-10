---
route: orchestration:dispatcher-workflow
artifact: handoff
plan: docs/记忆系统/plan/阶段二设计_记忆提取.md
dispatch: .ai-runtime-artifacts/plans/2026-09-10-phase2-memory-extraction-dispatch.md
tracking: .ai-runtime-artifacts/execution-logs/tracking/DISPATCH-TRACK-2026-09-10-phase2-memory-extraction.md
execution_log: .ai-runtime-artifacts/execution-logs/2026-09-10-phase2-memory-extraction-execution-log.md
created_at: 2026-09-10
dispatcher: leader
status: paused-before-WU-09
---

# Phase 2 记忆提取 — 交接文档（后续任务执行）

> 本文件是**中断点交接**：Phase 2 记忆提取已完成 GROUP-1 + WU-05/06/07/08（实现全部完成、测试全绿、已合并），
> 剩余 **WU-09（端到端集成测试）与尾盘（集体测试 + 集体审查）** 待执行。
> 已推送到 origin/feature/memory-system（commit `21a1d3c`）。

## 一、当前状态

| 项 | 值 |
| --- | --- |
| 当前分支 | `feature/memory-system`（已合并 worktree 分支） |
| 合并后 HEAD | `21a1d3c`（fast-forward，19 文件 +5079 行） |
| 上游 | `origin/feature/memory-system`（与本地同步，已 push） |
| 测试基线 | `pytest tests/memory/ tests/agent/test_memory_store.py -q` → **345 passed** |
| 全量回归 | `pytest tests/ -q` → 见交接时最后结果（后台跑） |

## 二、已完成范围

| WU | 文件 | 状态 | 证据 |
| --- | --- | --- | --- |
| GROUP-1 (WU-01..04) | `nanobot/memory/filters.py` `intent.py` `prompts.py` `scratchpad_writer.py` + 4 测试 | done | tests/memory 全绿 |
| WU-05 | `nanobot/memory/extractor.py`（795 行主类）+ `test_extractor.py`（37 用例） | done | 37 passed |
| WU-06 | `nanobot/agent/hooks/memory_extraction.py`（503 行）+ `hooks/__init__.py` + `test_hook_memory_extraction.py`（32 用例） | done | 32 passed；tests/agent 1580 passed/1 skipped |
| WU-07 | `nanobot/agent/loop.py`（`memory_extraction_enabled` opt-in + `_wire_memory_extraction`）+ `nanobot/session/manager.py`（`set_delete_session_observer`）+ `test_loop_wiring.py`（10 用例） | done | 10 passed |
| WU-08 | `nanobot/memory/extractor.py::extract_quick_facts` + `nanobot/agent/autocompact.py`（`quick_facts_hook` kwarg）+ `test_quick_facts.py`（17 用例） | done | 17 passed |
| 附带 | `nanobot/memory/models.py` 补 `EpisodeOutcome.ONGOING` | done | — |

## 三、未完成范围（后续执行）

### 1. WU-09：端到端集成测试 `tests/memory/test_extraction_integration.py`
- agent_role: **test-engineer**（只写测试，不改业务实现）。
- 场景与断言（plan §10 Task 10）：
  1. 模拟会话：用户问"如何在 Windows 上配置 uv" → Agent 答 → 用户"我以后都用 uv" → Agent 答。
  2. FakeLLM 语义路返回 `FACT 用户使用 Windows` + `PREFERENCE 用户偏好 uv`；情节路返回合法 episode JSON。
  3. `search_memories("uv")` 能检索到 FACT 与 PREFERENCE，`source == "extraction"`。
  4. `list_episodes_by_session(session.key)` 恰好 1 条，`source == "session_end"`，`action_nodes` 非空。
  5. `get_scratchpad(...).current_focus == "我以后都用 uv"`。
  6. 关闭后重开同一 db_path → 数据仍在（跨重启持久化）。
  7. Hook 层：CHAT 意图不写 focus；任务意图写 focus；`on_finally` 5s 内不抛。
  8. 建议补：tool_call → action_nodes ≥1；LLM 情节路失败 → extract_session 不抛。
- 验收命令：`pytest tests/memory/test_extraction_integration.py -q` 全绿 + `pytest tests/memory/ -q` 无回归。

### 2. 尾盘 A+B（Leader 手动执行，plan 阶段链强制）
- **A. collective-test**：全量 `pytest tests/ -v` + plan §13 检查清单（①全量测试 ②ruff/basedpyright ③MemoryStore 回归 ④SQLite Phase 1 回归 ⑤prompts 稳定性快照）→ 落盘 `.ai-runtime-artifacts/verifications/2026-09-10-phase2-memory-extraction-collective-test.md`。
- **B. code-review**：派 reviewer（独立实例）审查 WU-05/06/07/08 变更；security-auditor 按需 → 落盘 `.ai-runtime-artifacts/reviews/2026-09-10-phase2-memory-extraction-code-review.md`。
- 收尾：关闭 execution-log、WORKTREE-CLOSE。

### 3. 未决项（交接时如实记录）
- WU-06 报告：T1 触发时机为**每轮**（非会话结束）→ 每轮完整 LLM 提取 + on_finally 最多等 5s，成本偏高；生产默认 `memory_extraction_enabled=False`（opt-in）。后续如需启用需会话级门控或按 §12 限速。
- T5 用 `extract_session` 全量提取（extractor 无 semantic-only 开关），非 plan Task 12 的"仅 semantic"。
- `quick_facts_hook` 无生产装配点（需在启用处注入 `AutoCompact(..., quick_facts_hook=...)`，当前 loop.py 已接入 `_wire_memory_extraction`）。
- plan §14（Phase 3）：episodes.compaction_checkpoint_id、_llm_format_scratchpad、embedding 缓存、持久化提取队列、检索层。

## 四、关键 API 表面（供 WU-09 与后续使用）

```python
# nanobot/memory/extractor.py
class MemoryExtractor:
    def __init__(self, database: MemoryDatabase, runtime: LLMRuntime,
                 workspace_id: str = "default", user_id: str = "default") -> None: ...
    async def extract_session(self, session: Session, *, source: str = "session_end") -> ExtractionResult: ...
    def extract_quick_facts(self, session: Session) -> int: ...   # 同步、无 LLM

# nanobot/agent/hooks/memory_extraction.py
class MemoryExtractionHook(AgentHook): ...   # before_iteration(T5) / after_run(T0+T1) / on_error(T0') / on_finally(T1)
def create_memory_extraction_hook_factory(*, extractor_provider, scratchpad_writer,
                                          runtime_provider=None) -> AgentTurnHookFactory: ...

# nanobot/agent/loop.py  AgentLoop.__init__(..., memory_extraction_enabled: bool = False, memory_services: MemoryServices | None = None)
# nanobot/session/manager.py  SessionManager.set_delete_session_observer(observer: Callable[[Session], None])
# nanobot/memory/scratchpad_writer.py  ScratchpadWriter.update_focus(session_key, new_focus) / archive_completed(resolved_items)
# nanobot/memory/intent.py  classify_intent(msg) -> IntentType  (CHAT/QUERY/TASK/FOLLOW_UP/COMMAND)
# nanobot/memory/filters.py  is_task_artifact / is_ai_self_talk / compute_content_hash / ngram_similarity
# nanobot/memory/prompts.py  SEMANTIC/EPISODE/SCRATCHPAD/TOPIC_CHANGE prompts
# nanobot/memory/models.py  EpisodeOutcome 含 ONGOING
```

## 五、提交清单（本次已入 feature/memory-system）

```
21a1d3c feat(memory): AgentLoop/SessionManager 装配记忆提取与删除观察者（WU-07，opt-in）
9bfc5fa feat(memory): 上下文压缩后 Quick Facts 规则提取（WU-08）
8764198 feat(memory): 新增 MemoryExtractionHook 生命周期钩子（T0/T1/T5 触发点，WU-06）
a9bd0bc feat(memory): 实现 MemoryExtractor 四阶段提取流水线与 SQLite 持久化（WU-05）
7c1fc59 feat(memory): 新增防污染过滤器/意图识别器/提示词常量/ScratchpadWriter（Phase 2 GROUP-1）
```

## 六、参考
- 设计/计划：`docs/记忆系统/plan/阶段二设计_记忆提取.md`（§10 Task 拆分、§13 检查清单、§14 Phase 3）
- dispatch：`.ai-runtime-artifacts/plans/2026-09-10-phase2-memory-extraction-dispatch.md`
- tracking：`.ai-runtime-artifacts/execution-logs/tracking/DISPATCH-TRACK-2026-09-10-phase2-memory-extraction.md`
- execution-log：`.ai-runtime-artifacts/execution-logs/2026-09-10-phase2-memory-extraction-execution-log.md`

## 七、后续执行入口

```bash
# 新会话继续
git checkout feature/memory-system && git pull --ff-only
# WU-09（test-engineer 执行）
cd d:\studyspace\源码学习\Agent-With-Memory
python -m pytest tests/memory/test_extraction_integration.py -q
# 尾盘 A
python -m pytest tests/ -v && ruff check nanobot/memory/ nanobot/agent/hooks/memory_extraction.py tests/memory/
# 尾盘 B
# 派 reviewer 审查 → 落盘 reviews/
```
