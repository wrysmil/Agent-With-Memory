# Phase 2 尾盘执行清单(等 WU-09 完成启动)

> WU-09 完成通知到达后,按 A → B 顺序执行。
> 落盘文件命名沿用 plan 约定的 prefix:`2026-09-10-phase2-memory-extraction`。

## 尾盘 A:集体测试 collective-test

**Goal**:全量回归 + ruff + basedpyright + MemoryStore/SQLite 回归 + prompts 稳定性快照(plan §13)。

### A.1 命令清单(按顺序执行)

```bash
cd /Users/mima0000/Documents/学习-001/do-project/Agent-With-Memory

# 1. 全量 pytest(应包含 WU-09 新文件)
uv run --no-sync pytest tests/ -v 2>&1 | tee /tmp/phase2-collective-pytest.log

# 2. MemoryStore 回归(文件 I/O 层,与 SQLite 链路独立)
uv run --no-sync pytest tests/agent/test_memory_store.py -q

# 3. Phase 1 SQLite 回归(60 用例)
uv run --no-sync pytest tests/memory/test_database.py tests/memory/test_memories.py \
  tests/memory/test_episodes.py tests/memory/test_scratchpad.py tests/memory/test_search.py -q

# 4. Phase 2 新增(应有 WU-01..09 全部)
uv run --no-sync pytest tests/memory/test_filters.py tests/memory/test_intent.py \
  tests/memory/test_prompts.py tests/memory/test_scratchpad_writer.py \
  tests/memory/test_extractor.py tests/memory/test_hook_memory_extraction.py \
  tests/memory/test_loop_wiring.py tests/memory/test_quick_facts.py \
  tests/memory/test_extraction_integration.py -q

# 5. ruff(nanobot + tests)
uv run --no-sync ruff check nanobot/ tests/ 2>&1 | tee /tmp/phase2-collective-ruff.log

# 6. basedpyright(CI 严格类型)
uv run --no-sync basedpyright 2>&1 | tee /tmp/phase2-collective-basedpyright.log

# 7. prompts 稳定性快照(防止 prompt 漂移)
uv run --no-sync python -c "
from nanobot.memory.prompts import (
    SEMANTIC_EXTRACTION_PROMPT, EPISODE_EXTRACTION_PROMPT,
    SCRATCHPAD_FORMAT_PROMPT, TOPIC_CHANGE_DETECTION_PROMPT,
)
import hashlib, json
data = {
    'SEMANTIC': SEMANTIC_EXTRACTION_PROMPT,
    'EPISODE': EPISODE_EXTRACTION_PROMPT,
    'SCRATCHPAD': SCRATCHPAD_FORMAT_PROMPT,
    'TOPIC_CHANGE': TOPIC_CHANGE_DETECTION_PROMPT,
}
snap = {k: {'len': len(v), 'sha256': hashlib.sha256(v.encode()).hexdigest()} for k, v in data.items()}
print(json.dumps(snap, ensure_ascii=False, indent=2))
" > /tmp/phase2-prompts-snapshot.json
cat /tmp/phase2-prompts-snapshot.json
```

### A.2 通过条件
- 所有 pytest 退出码 0
- ruff 0 error
- basedpyright 0 error(允许 informational 警告)
- prompts snapshot sha256 与 WU-03 提交时一致(若有 drift,记录 diff 即可)

### A.3 落盘位置
`/Users/mima0000/Documents/学习-001/do-project/Agent-With-Memory/.ai-runtime-artifacts/verifications/2026-09-10-phase2-memory-extraction-collective-test.md`

报告模板:
```markdown
# Phase 2 集体测试报告

**日期**:2026-09-10
**执行者**:Leader
**触发**:WU-09 完成通知

## 1. 全量 pytest
[粘贴末行 + 失败(若有)]

## 2. MemoryStore 回归
[末行]

## 3. Phase 1 SQLite 回归
[末行]

## 4. Phase 2 新增
[末行]

## 5. ruff
[末行 / 关键 error]

## 6. basedpyright
[末行 / 关键 error]

## 7. prompts 稳定性
[snapshot JSON,或 diff]

## 结论
- [ ] PASS / FAIL
- verdict:n/a → go
- 阻塞项(若有)
```

---

## 尾盘 B:并行审查 fan-out

**Goal**:reviewer + security-auditor 并行审查 WU-05/06/07/08 变更;perf-auditor 按需(本次未涉及 hot path,大概率跳过)。

### B.1 review diff 范围
- 比较 base `d72d474` ↔ HEAD `21a1d3c`:
  ```bash
  git diff d72d474..HEAD --stat -- nanobot/memory/ nanobot/agent/hooks/ nanobot/agent/loop.py nanobot/session/manager.py nanobot/agent/autocompact.py tests/memory/
  ```
- 关键文件清单(供子代理聚焦):
  - `nanobot/memory/extractor.py`(795 行)
  - `nanobot/agent/hooks/memory_extraction.py`(503 行)
  - `nanobot/memory/filters.py` / `intent.py` / `prompts.py` / `scratchpad_writer.py`
  - `nanobot/agent/loop.py`(`_wire_memory_extraction` 方法)
  - `nanobot/session/manager.py`(`set_delete_session_observer`)
  - `nanobot/agent/autocompact.py`(`quick_facts_hook` kwarg)
  - `nanobot/memory/models.py`(EpisodeOutcome.ONGOING)
  - 9 个测试文件

### B.2 reviewer 派发 prompt(独立 subagent 实例)

```
独立审查任务(WU-05/06/07/08 记忆提取流水线变更)。

不要相信你自己之前的判断,以 fresh-context 视角审查:
- 范围:`git diff d72d474..HEAD` 中 `nanobot/memory/` `nanobot/agent/hooks/memory_extraction.py` `nanobot/agent/loop.py` `nanobot/session/manager.py` `nanobot/agent/autocompact.py` `tests/memory/` 的所有变更
- 设计依据:`docs/记忆系统/plan/阶段二设计_记忆提取.md`(§3 流水线、§5 防污染、§6 scratchpad、§7 集成、§8 数据契约、§10 Task 拆分)
- 5 维度:correctness / readability / architecture / security / performance
- 必须检查:
  1. 失败隔离:`MemoryExtractor._persist` 是否逐条失败不抛(plan §9)
  2. 防污染:filters / N-Gram / L1 任务产物 / L2 精确哈希 / L3 N-Gram(plan §5.2)
  3. T0 意图门:CHAT 不写、其余写;FOCUS_MAX_CHARS=200 截断(plan §6.5)
  4. T1 5s 超时:`EXTRACTION_WAIT_TIMEOUT` 兜底,超时取消并 warning,不抛
  5. T5 话题切换:≥4 user 消息判定、LLM 失败按 CONTINUE、fire-and-forget 不阻塞
  6. 持久化字段:`Memory.source="extraction"` / `Episode.source="session_end"` / `linked_memory_ids` / `source_episode_id` 反向回填
  7. 测试覆盖:8 个新测试文件 + 边界(空消息、JSON 解析失败、超时、LLM 异常)
- 报告格式:`.ai-runtime-artifacts/reviews/2026-09-10-phase2-memory-extraction-code-review.md`,Markdown,5 维度分节,严重度分级(critical/major/minor),每条结论含文件:行号 + 复现步骤 + 建议修复方向
- 2000 字以内
```

### B.3 security-auditor 派发 prompt(并行 fan-out)

```
独立安全审查(WU-05/06/07/08)。

范围:`git diff d72d474..HEAD` 中 `nanobot/memory/` `nanobot/agent/hooks/` `nanobot/agent/loop.py` `nanobot/session/manager.py`。

重点(OWASP Top 10 + LLM-Sec):
1. 注入:LLM 输出直接拼 SQL?JSON 解析后未类型校验?`_coerce_memory_item` 是否限制 type/priority 枚举(plan §8.2 兜底)
2. 路径穿越:`db_path` 来自用户配置?是否有 allowlist
3. 信息泄露:错误日志是否泄露 LLM 原始 prompt/response 全文
4. 资源耗尽:`extract_session` 是否对超大 messages 有限制(`_render_transcript` max_chars=8000 是否真的截断)
5. 并发竞争:`MemoryDatabase.connect()` 线程安全?`_persist` 在并发会话下是否冲突
6. 跨会话越权:`Memory` 的 `workspace_id` / `user_id` 隔离是否正确(LLM 提取后写入是否带正确 workspace)
7. 持久化 prompt injection:`add_memory` 接受外部 `content`,是否会被后续查询路径当作指令执行

报告:`.ai-runtime-artifacts/reviews/2026-09-10-phase2-memory-extraction-security-review.md`
1500 字以内,critical/major/minor 分级。
```

### B.4 fan-out 调度
- reviewer + security-auditor 同时派发(独立 subagent 实例)
- 等两者都返回后,Leader 汇总判定 go/no-go
- 若有 critical/major finding → 打回 coder 修复 → 重跑 WU-09 + 尾盘 A

### B.5 收尾
- 关闭 execution-log:更新 `.ai-runtime-artifacts/execution-logs/2026-09-10-phase2-memory-extraction-execution-log.md` 状态为 `closed`
- 更新 DISPATCH-TRACK:WORKTREE-CLOSE → done
- 若无 worktree,跳过 WORKTREE-CLOSE;若有,`git worktree remove <path>`
- 最后推送:`git push origin feature/memory-system`(若 WU-09 引入新 commit)
