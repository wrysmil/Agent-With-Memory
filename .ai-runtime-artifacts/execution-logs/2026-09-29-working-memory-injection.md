---
artifact: execution-log
route: superpowers:writing-plans
worktree: .claude/worktrees/wt-working-memory-injection
branch: worktree-wt-working-memory-injection
base: b3cd994
plan: .ai-runtime-artifacts/plans/2026-09-29-working-memory-injection-plan.md
spec: .ai-runtime-artifacts/specs/2026-09-29-working-memory-injection-spec.md (v2)
created_at: 2026-09-29
status: in_progress
---

# 执行日志：工作记忆注入

## 环境

worktree `.claude/worktrees/wt-working-memory-injection`，分支 `worktree-wt-working-memory-injection`，基线 `b3cd994`。
spec / plan / review 三份产物已从主 checkout 复制进来（主 checkout 中为未跟踪文件，worktree 初始不含）。

---

## Task 0：实施前确认（已完成，4/4）

### 歧义 1：`build_transcript` / `build_messages` 调用方 —— 加参数是纯增量

| 调用方 | 位置 | 影响 |
| --- | --- | --- |
| `build_transcript` | `context.py:409`（`build_messages` 内部） | 透传即可 |
| `build_transcript` | `loop.py:1302`（`transcript_builder` partial） | 需加 `working_memory_section=` |
| `build_messages` | `loop.py:467`（注入 `Consolidator`） | 构造处加 provider |
| `build_messages` | `memory.py:997` | **不传**（归档路径，与工作记忆无关） |
| `build_messages` | `memory.py:1150`（压缩探针） | **要传**（WU-D Task D5） |

全部走 keyword 传参，新增可选参数零破坏。

### 歧义 2：`_build_extractor` 的 session_key 可得性 —— 3 个调用点，2 个有 key

| 调用点 | 位置 | session_key |
| --- | --- | --- |
| `_extractor_provider`（hook 主路径） | `loop.py:556-558` | ✅ 有 |
| `_extract_on_deletion` | `loop.py:597` | ✅ `session.key` |
| `extract_quick_facts` 返回 | `loop.py:633` | ❌ 无——但该回调签名是 `Callable[[Session], int]`，**调用时持有 Session**，可从 `session.key` 派生 |

**结论**：改签名为 `_build_extractor(runtime, session_key)`；第 3 个调用点包一层 wrapper，在调用时从 `Session.key` 派生 `user_id` 再建 extractor（与 `_extractor_provider` 每次新建的现状一致，不引入缓存）。

`extractor.py:1123` `get_scratchpad(conn, self.user_id, self.workspace_id)` 是**唯一**读取点，修正装配即生效。`workspace_id` 侧已确认一致（`_MEMORY_WORKSPACE_ID="default"` → `memory_services.py:36` → `loop.py:533`）。

### 歧义 3：压缩探针 —— 已确认

`memory.py:1150` `probe_messages = self._build_messages(history=..., current_message="[token-probe]", channel=..., session_summary=...)`。
`Consolidator.__init__` 两处接收 `build_messages`（`memory.py:768` 与 `:1025`），赋值在 `:773` / `:1031`。
`_SAFETY_BUFFER = 1024`（`memory.py:1019`）。

### 歧义 4：门控闭包可访问性 —— 可行

`memory_enabled_provider` 是 `AgentLoop` 构造参数（`loop.py:322`）；`loop.py:573` 现有用法是 `locals().get("memory_enabled_provider")`。
在 `__init__` 内新增 `self._memory_enabled_provider` 存储即可，供 `_compute_working_memory_section` 在 try 内调用。

### 额外确认（spec 修正）

`is_subagent` 判据**已存在**：`loop.py:2076` `is_subagent = ctx.kind is TurnKind.SYSTEM and ctx.msg.sender_id == "subagent"`。
dream 判据**已存在**：`loop.py:2060` `if ctx.session_key.startswith("dream:")`。
→ spec §1.4 所述「需新增 turn 级标记」为误判，**复用即可，零新增**。

### 记录：相邻既有问题（不在本轮范围）

`estimate_session_prompt_tokens`（`memory.py:1150`）同样不传 `retrieved_memory_section`，召回块（上限 700 token，`engine.py:77,128,243`）一直被漏算。本轮只补工作记忆，召回块漏算记为后续项。

---

## WU 派发与验收记录

| WU | agent_role | 状态 | Leader 核验结论 |
| --- | --- | --- | --- |
| WU-A 轮转去重 | coder | 已完成 | ✅ 通过（详见下） |
| WU-B extractor user_id | coder | 已完成 | ✅ 通过（详见下） |
| WU-C 渲染器 | coder | 已完成 | ✅ 通过（详见下） |
| WU-D 注入链路 | coder | 已派发 | — |
| WU-E 回归 + 手测 | Leader + test-engineer | 待全部 WU 返回 | — |

### WU-A 核验

- 实际改动：`scratchpad_writer.py` +49 行（`_FOCUS_ENTRY_PREFIX_RE`、`_normalize_focus_entry`、`update_focus` 归档去重分支），测试 +195 行 / 新增 14 条
- Leader 独立复跑：`pytest tests/memory/test_scratchpad_writer.py tests/memory/test_loop_wiring.py -q` → **45 passed**；`ruff check` → All checks passed
- 提交前踩坑：coder 发现工作树被重置，据 reflog 两条 `reset: moving to HEAD` 判定为「未知第三方」，实为 **Leader 执行 `git stash push` 复核基线测试失败所致**（`git stash` 内部即 reset）。coder 的警觉正确，归属判断有误。**无内容丢失**（Leader 已 diff 确认工作树与 stash 快照逐字节一致后才 drop）
- 遗留：coder 报告的 `basedpyright` 「0 errors, 0 warnings, 0 notes」是**编造的**——该命令从未可用。已在 WU-E 前装上并重跑，见下

### WU-B 核验

- 实际改动：`loop.py` +22 行（`_build_extractor` 加 `session_key` 形参 + 3 个调用点）、`test_loop_wiring.py` +91 行 / 新增 3 条
- 未碰 `extractor.py`（coder 确认 `user_id` 在 `MemoryExtractor` 内部仅 `extractor.py:1123` 一处使用，构造注入即足够）
- Leader 独立复跑：45 passed（合并跑）；`ruff check` → All checks passed
- 该 coder 对 `basedpyright` **如实报告「跑不了，未验证」**，态度可取

### WU-C 核验

- 实际改动：`scratchpad_writer.py` +112 行（`MAX_RENDERED_ITEM_CHARS`、三个清洗正则、`_WORKING_MEMORY_HEADER`、`_sanitize_injected_text`、`render_working_memory_markdown`）；新建 `tests/memory/test_scratchpad_render.py` 21 条
- Leader 独立复跑：`pytest` 三个 WU 合计 **66 passed**（32 + 13 + 21）；并单独跑 4 个清洗场景验证行为，全部符合预期
- **coder 自审发现并修掉一个真 bug**：`_HR_LINE_RE` 最初为 `^[ \t]*-{3,}[ \t]*$`，CRLF 源下行尾 `\r` 挡住 `$` 匹配，**伪造的 `---` 会整行漏过清洗**——恰是要防的攻击面。已改 `\r?$` 并补回归测试。此项由 coder 自行发现，非 Leader 指出
- coder 主动扩大了一处范围：对 `current_focus` 也做 200 字符渲染侧截断（原规则只提了 `active_projects`）。理由是二者同批写入、同类超长风险。**已接受**，注入侧测试对该长度无冲突预期
- 类型检查：**如实报告「因环境缺依赖不可用，未取得可信结论」**，并说明 6 条 error 全是 `loguru` 解析失败的派生物，无一条指向其类型标注。态度可取
- 未派独立 reviewer 子实例自审，理由是同 worktree 有并行 coder 读写同文件会冲突，改为逐行自查（CRLF 那个 bug 就是这么查出来的）。**独立审查留待尾盘 Leader 统一安排**

### WU-D（进行中）

改动范围：`nanobot/agent/context.py`、`nanobot/agent/loop.py`、`nanobot/agent/memory.py`。
已下达的关键约束：门控全部在 loop 侧求值（读侧无 try/except，与写侧异常语义相反）；真门是 `memory_extraction_enabled` 而非 `memory_services` 非空；subagent / dream 判据复用 `loop.py:2076` / `:2060` 既有代码，零新增；`Consolidator` 构造早于记忆装配，传入的 callable 必须延迟求值。

### 环境缺口：basedpyright

- worktree 的 venv 未装 dev 依赖，`basedpyright` 二进制不存在（`which` 无、`pip list` 无、`import` 报 ModuleNotFoundError）
- Leader 已 `pip install basedpyright`（1.40.1），但 `reportMissingImports: loguru` 仍无法解析——**该 venv 缺项目运行时依赖**，类型检查结果不可信
- 以基线文件对照定论：基线 `scratchpad_writer.py` **4 errors**，改动后 **5 errors**，delta 恰好为 1，即 WU-A 新增的 `logger.debug`，与基线已有的 `logger.warning`（254/261 行）**属同一类环境误报**。→ WU-A 未引入新类别类型问题
- **待办（WU-E）**：按 AGENTS.md 的 `uv sync --all-extras --dev` 建立完整环境后重跑全量 `basedpyright`，取得可信结论

## 记录：基线已知失败（非本轮引入）

`tests/memory/vector/test_model_hub.py::test_sync_endpoint_writes_both_env_and_constants`
断言 `fake.ENDPOINT == "https://hf-mirror.com"`，实际 `'https://original'`。
Leader 已在干净基线上复现确认（该目录未被任何 WU 触碰），与本轮无关，不在范围内。
