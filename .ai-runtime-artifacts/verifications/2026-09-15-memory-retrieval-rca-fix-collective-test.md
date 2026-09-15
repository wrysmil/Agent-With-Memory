---
artifact: collective-test
route: orchestration:collective-closeout
plan: .ai-runtime-artifacts/plans/2026-09-15-memory-retrieval-rca-fix-plan.md
dispatch: .ai-runtime-artifacts/plans/2026-09-15-memory-retrieval-rca-fix-dispatch.md
skills:
  - verification-before-completion
source:
  - .ai-runtime-artifacts/specs/2026-09-15-memory-retrieval-rca.md
created_at: 2026-09-15
verdict: PASS
worktree:
  id: wt-2026-09-15-memory-retrieval-rca-fix
  path: /Users/mima0000/Documents/学习-001/do-project/.harness-worktrees/Agent-With-Memory/wt-2026-09-15-memory-retrieval-rca-fix
  branch: harness/wt-2026-09-15-memory-retrieval-rca-fix
  base_ref: feature/memory-system @ 20e4d94
  head_ref: c6f66ac
---

# 集体测试 — 记忆检索恒空修复

**执行者：** Leader（3 个 WU 全部归队后，在**无并发写入的静止态**执行）
**提交：** `c6f66ac`
**结论：** **PASS**

---

## 0. 验证环境前置（重要 gotcha，已实测）

`.venv` 通过 `_editable_impl_nanobot_ai.pth` 指向**主 checkout**，且 `sys.path[0]` 是 cwd。
因此：

- ✅ 正确：`cd <worktree> && pytest …`（cwd 指向 worktree，worktree 代码优先）
- ✅ 正确：`cd /tmp && PYTHONPATH=<worktree> python …`
- ❌ 错误：在**主 checkout** cwd 下仅设 `PYTHONPATH=<worktree>` —— `''` 抢先命中主 checkout，
  会静默跑**未修复**的代码。

实测证据：
```
$ cd <worktree> && PYTHONPATH=<worktree> python -c "import nanobot; print(nanobot.__file__)"
/Users/…/.harness-worktrees/Agent-With-Memory/wt-2026-09-15-memory-retrieval-rca-fix/nanobot/__init__.py
```
本产物中所有「worktree 代码」结论均已按此口径重跑确认；一次误用主 checkout plain-SQL 的探针
（仅用 `MemoryDatabase` + 原生 SQL，两分支代码相同）已识别并说明，不影响任何结论。

**无主产物处置：** worktree 根目录 `memory/state.db`（全量套件残留，删前核查 memories/episodes/scratchpad 均 0 行）已删除。
`webui/package-lock.json` 为环境性改动（主 checkout 同样为 M），**未纳入提交**。

---

## 1. 单元 / 集成测试

| # | 命令 | 结果 |
| --- | --- | --- |
| 1 | `pytest tests/memory/retrieval/test_store_adapter.py tests/memory/test_memory_search_tool.py -q` | **13 passed** |
| 2 | `pytest tests/memory/ -q` | **671 passed** |
| 3 | `pytest tests/ -k "loop_wiring or database or repository" -q`（WU-01 报告） | 55 passed, 1 skipped |
| 4 | `pytest -q`（全量） | 13 failed / 7246 passed / 19 skipped |

**全量 13 项失败的存量归因（Leader 独立复核，非采信子 Agent 转述）：**

```
基线 HEAD 20e4d94（git archive 干净导出）：13 failed
本批 worktree                              ：13 failed
diff 失败用例名 → IDENTICAL
```

13 项分布：`test_mcp_reconnect_crash` / `cli/test_commands`(×4) / `test_tui_launcher` /
`test_mcp_probe`(×2) / `test_mcp_tool`(×2) / `test_web_fetch_security`(×3)
—— 全部为网络 / DNS / MCP 环境依赖，**无一涉及记忆模块**。

---

## 2. lint

| 对象 | 基线 HEAD | 本批 | 结论 |
| --- | --- | --- | --- |
| `ruff check nanobot/` | Found 12 errors | Found 12 errors | **零新增** |

本批 2 个变更文件命中的 2 条 —— `gateway_runtime.py:3:1 I001`、`repository.py:488 N806`
—— 在 HEAD 基线**同样存在**（N806 仅行号位移 486→488）。其余 10 条在
`memory/__init__.py`(F401×6 等)、`agent/context.py`、`webui/settings_routes.py`，均在 WU 允许清单之外。

**Leader 裁决（DECISION-DC3）：** 本批验收口径取「**不引入新 lint 错误**」。
全量 lint 清理属独立范围，已记为本批遗留项。

---

## 3. 真实库端到端冒烟（plan Task 9，Leader 手动执行）

真实库 `~/.nanobot/workspace/memory/state.db`（只读打开，**零写入**）：
`memories=8` / `episodes=1` / `scratchpad=2`。

### 3.1 逐通道探测

```
[通道] semantic('创作')            -> 0 条   ← 正确：全库 0 行 content 含「创作」（库内容已非 RCA 时点）
[通道] recent(3d, imp>=0.6)        -> 8 条   ← 通
[通道] episodes('state.db')        -> 0 条   ← 通（无匹配实体）
[通道] attachments('图片')         -> 0 条   ← 设计如此：attachments 表未进 schema v1
```

语义通道另点测（证明 LIKE 回退真的在工作，unicode61 不可能按子串命中）：

```
semantic('自媒体') -> 5 条      semantic('内向') -> 2 条
semantic('黄启华') -> 1 条      semantic('学习') -> 1 条
```

episodes 走通（**RCA 未发现的第 6 个缺陷已修**）：`search_episodes(entity='用户') -> 1 条`，
返回 `updated_at=2026-09-15T14:12:32.086232+00:00`，不再抛 `no such column: updated_at`。

### 3.2 RCA 原始症状复现对比

**修复前（本会话 §1 复现脚本，临时库）：** `first-turn: BLOCK='' IDS=[]` / `second-turn: BLOCK='' IDS=[]`

**修复后（真实库）：**

```
--- query='查一下我的记忆' (有历史) ---
## 相关记忆（自动检索）
- 用户的名字是黄启华 (ID: 2bc890b0-…)
- 用户自述为 AI，职业是应用工程师 (ID: 4fa2408d-…)
- 用户正在学习做自媒体 (ID: f8fe762c-…)
- 用户性格比较内向 (ID: 32d36de3-…)
- 内向者做自媒体可优先选择不露脸形式：… (ID: 222d6169-…)
ids: [5 条]

--- query='我正在规划创作选题' (有历史) ---  同样 5 条非空

--- query='查一下我的记忆' 首轮 recent_messages=[] ---  同样 5 条非空
```

**RCA 症状 1（答「记忆里目前是空的」）与症状 2（首轮不检索）双双消除。**

### 3.3 附带发现：FTS / LIKE 结果集不对称（非缺陷，记录）

实测 `semantic('自媒体')` 经 FTS 主路径返回 **5** 条，而 `LIKE` 回退单独跑返回 **4** 条。
逐行定位差异行 `18b360e1-…`：其 `content` 不含「自媒体」，但
`tags = ["自媒体","垂直","定位"]` 含。→ **FTS5 索引 content/subject/predicate/tags 四列，
而 `_search_memories_like` 只查 content**，多命中属正常，**非索引漂移**
（`INSERT INTO memories_fts(memories_fts) VALUES('integrity-check')` → OK；
memories / memories_fts 均 8 行、rowid 全对齐）。

**但这是回退路径比主路径窄的真实不对称**：中文 query 若能命中某行的 tags/subject，
走 FTS 可召回、走回退则漏。**非回归**（修复前中文 query 恒返回 0 条），
列为遗留项 LI-3。

---

## 4. 检查表执行结果（Leader 汇总自检 + WU 回报）

| 检查项 | 结果 | 证据 |
| --- | --- | --- |
| 正确性 | ✅ | 验收测试「临时库写中文记忆 → `retrieve_with_ids` 非空」PASS；真实库端到端非空 |
| 边界与错误路径 | ✅ | FTS 语法错误（未闭合引号）、空结果、表缺失、通道异常、时间边界当天 均有用例 |
| 性能反模式 | ✅ 无新增 | LIKE 回退仅在 FTS 零命中时执行且有 LIMIT；`datetime()` 经 `EXPLAIN QUERY PLAN` 实证仍走 `idx_memories_importance` |
| 日志规范 | ✅ | `logger.warning("retrieval channel {} failed: {}: {}", …)` 参数化；warning **不含** query 文本 |
| 安全检查 | ✅ | 值全部参数化绑定；唯一 f-string 为 `LIMIT {int(limit)}`（强制 int） |
| 反注入未被绕过 | ✅ | `clean_query` 仍在门禁**之前**执行；门禁放宽不放行注入内容 |
| 范围合规 | ✅ | 3 个 WU 均未越界；`webui/`、schema、依赖零改动 |
| 无障碍 | N/A | 非 UI 变更 |

---

## 5. 遗留项（不阻塞本批，Leader 判定）

| # | 项 | 处置 |
| --- | --- | --- |
| LI-1 | `ruff check nanobot/` 存量 12 错 | 独立 lint 清理 WU（非本批） |
| LI-2 | `attachments` 通道：`repository` 返回 dataclass，而通道按 `item["id"]` 字典协议消费；当前被「表不存在」挡住，一旦建表会在 `item["id"]` 抛 `TypeError` | 开 `review-fix` WU 收口（连同 `Protocol` 显式契约定义） |
| LI-3 | LIKE 回退只查 `content`，比 FTS 四列窄 | 录此备查；彻底修需 UNION + 去重/排序语义定义 |
| LI-4 | `_MEMORY_INTENT_HINTS` 裸子串匹配会放行「我的天哪」类寒暄（中性短消息约 3% 多一次检索） | **接受**：代价仅一次多余召回（≤700 token），无正确性/安全影响；如需收紧另开 WU |
| LI-5 | 通道失败 warning 缺 correlationId（session/request 关联） | 需从调用方透传，独立改动 |
| LI-6 | `MemoryDatabase.connect` 持全进程非重入锁 → 4 路 `to_thread` 实际被串行化 | 既有设计，非本批引入 |
| LI-7 | RCA §1.3「updated_at 空格分隔导致边界当天整批排除」叙事**不成立** | 该现象由 Leader 探针传 `datetime` 对象触发 sqlite3 弃用适配器所致；生产路径走 `to_row()`/`isoformat()` 存 `'T'` 分隔（真实库 8/8 行实测为 `'T'`）。`datetime()` 归一作为**格式无关健壮性**保留（零风险）。**RCA 该节需更正** |
| LI-8 | `preprocessor.py:3` 模块 docstring 指向的 `docs/记忆系统/记忆检索.md` 仍描述旧门禁逻辑 | 独立 docs WU |

---

## 6. 结论

- **A 集体测试：PASS**（单元 + 集成 + lint 存量归因 + 真实库端到端，全部有原始输出）。
- 三条路径（每轮自动注入 / `memory_search` 工具 / 首轮）均已实测非空召回。
- RCA 4 个根因 + 实施期新发现 2 个（CJK 分词、`episodes.updated_at` 列不存在）全部修复。
- **下一步：** B 集体审查（`reviewer` + `security-auditor` 并行，已派发）→ 落盘 reviews/ → 关闭 execution-log。
