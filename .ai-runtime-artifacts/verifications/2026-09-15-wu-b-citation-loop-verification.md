# WU-B 引用评分闭环 — 验证记录

- 日期：2026-09-15
- 分支：`feature/memory-system`
- 计划：`.ai-runtime-artifacts/plans/2026-09-15-profile-extractor-and-citation-loop-plan.md` §4
- scope：WU-A（ProfileExtractor 接线 + 增量合并）之后的 WU-B，让「被检索到且被证明有用」的记忆在后续检索里排更前。

## 1. 验证口径与证据

| # | 命令 | 结果 |
| --- | --- | --- |
| 1 | `uv run --no-sync pytest tests/memory/ -q` | **638 passed** |
| 2 | `uv run --no-sync pytest tests/memory/ tests/config/ -q` | **761 passed, 1 skipped** |
| 3 | `uv run --no-sync pytest tests/agent/ -q` | **1591 passed, 1 skipped, 1 failed**（失败项见 §3，预存） |
| 4 | `uv run --no-sync ruff check <本轮改动文件>` | **All checks passed** |
| 5 | `uv run --no-sync pytest tests/memory/test_citation_loop.py -q` | **9 passed**（新增专项） |

## 2. DoD 对照（plan §4.4）

| DoD 条目 | 覆盖测试 | 状态 |
| --- | --- | --- |
| 注入块 markdown 里能读到 `memory_id` | `test_citation_loop.py::test_cited_section_appends_to_semantic_prompt` + engine 渲染 | ✅ |
| 伪 provider 返回 `useful=true` → `access_count` 0→1 | `TestCitationLoop::test_useful_score_increments_access_count` | ✅ |
| 伪 provider 返回集合外的 id → 不写库 | `test_score_id_outside_cited_set_is_ignored` | ✅ |
| `useful=false` 不写库 | `test_useful_false_does_not_increment` | ✅ |
| 无 cited → prompt 无评分段、不写库 | `test_no_cited_ids_is_noop` | ✅ |
| reranker 对 `access_count=0` 与 `=1` 给出不同 `composite_score` | `TestAccessCountAffectsScoring::test_access_count_0_vs_1_gives_different_composite` | ✅ |
| repository `bump_access_count` 语义 | `TestBumpAccessCount`（3 例：累加、默认 delta、缺行静默） | ✅ |

## 3. 已知遗留（非本 WU 引入）

- `tests/agent/test_mcp_reconnect_crash.py::test_mcp_reconnect_during_shutdown_does_not_crash`
  **预存失败**：`git stash -u` 后在干净树上复现（1 failed, 1 passed），与本 WU 无因果关系，
  属 Windows 子进程 / MCP 关闭时序问题。
- `ruff` 全树扫描中的 `nanobot/agent/context.py:54` I001 与 `nanobot/memory/repository.py:486`
  N806 均为**预存**（stash 后同样报出），未在本轮改动中引入，保持不动以免混入无关 diff。

## 4. 端到端（未执行）

计划 §5.3 的「重启 gateway → 发含偏好的消息 → 等 2 分钟 → 查库」需真实运行时与
2 分钟空闲窗口；本次未执行。判定依赖：

```sql
SELECT count(*) FROM memories;                       -- 非 0
SELECT count(*) FROM episodes;                       -- 非 0
SELECT content, subject, predicate, access_count FROM memories;  -- 重复偏好不增行
```

日志锚点：`idle extraction for session ...: N new messages -> X memories, Y episodes`。

## 5. 结论

WU-B 单测 / 集成层验证通过（口径 1-5 全绿），无本 WU 引入的回归。端到端留待真实环境复核。
