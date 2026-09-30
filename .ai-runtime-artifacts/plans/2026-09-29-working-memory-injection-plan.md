---
artifact: implementation-plan
route: superpowers:writing-plans
skills:
  - writing-plans
skills_evidence:
  - harness-kit/.agents/skills/writing-plans/SKILL.md
source:
  - AGENTS.md
  - core/routing.md
  - .ai-runtime-artifacts/specs/2026-09-29-working-memory-injection-spec.md (v2, 审查后修订)
  - .ai-runtime-artifacts/reviews/2026-09-29-working-memory-injection-spec-review.md
created_at: 2026-09-29
status: draft
approved: false
---

# Plan：工作记忆（Scratchpad）固定注入 system prompt

> spec 已过两轮独立审查并修订为 v2。本 plan 只做 **2 字段**（`current_focus` / `active_projects`）注入 + 提取侧两处独立缺陷修复。**不做 WebUI 页面**（用户明确要求），**不打通 S4**（用户决策，见 spec §0.5）。

---

## Goal

让 agent **每轮真正读到自己写的工作记忆**——今天它只写不读，召回引擎四路里没有它。

完成后：
- 主对话每轮 system prompt 中，`# Memory`（MEMORY.md）**之前**出现 `# Working Memory` 块，含 `## 当前任务` + `## 进行中`
- 用户围绕同一件事连问 5 轮，`## 进行中` **不新增碎片**
- 记忆总开关关掉 → 块消失；派发 subagent / 触发 `/dream` → 块不出现
- 抽取器喂给 LLM 的工作记忆快照**不再恒空**
- 压缩时的 token 探针把工作记忆算进去

## 决策清单（用户已确认）

| # | 决策 | 决定 |
| --- | --- | --- |
| 1 | 范围 | **只补后端链路，不做 WebUI 页面**（openakita 亦无编辑入口，天然一致） |
| 2 | 注入方式 | **固定注入**，每轮无条件读一次，不走相关性召回 |
| 3 | 字段范围 | **只注入 2 个字段**。打通 S4 拿 4 字段是独立后续项 F1 |
| 4 | 轮转去重 | **纳入本轮**。否则注入到 agent 眼前的是碎片 |

## Architecture（现状 → 目标）

### 现状

- `scratchpad` 表（`database.py:95-105`）主键 **(user_id, workspace_id)** 二元组；模型 `ScratchpadEntry`（`models.py:207-215`）5 字段
- **唯一有数据的写入路径**：`MemoryExtractionHook._apply_immediate_focus`（`memory_extraction.py:436-445`）每轮 `after_run` 调 `ScratchpadWriter.update_focus`（`scratchpad_writer.py:99-151`），纯规则不调 LLM，写 `current_focus` + 轮转 `active_projects`
- `user_id` 写入侧 = `ScratchpadWriter.user_id_for_key(session_key)`（`scratchpad_writer.py:63-74`，从 `channel:chat_id` 取 tail）
- `workspace_id` 三处一致：`_MEMORY_WORKSPACE_ID="default"`（`loop.py:134`）→ `memory_services.py:36` → `loop.py:533`
- **读取方不存在**：召回引擎 4 路（`engine.py:200-205`）无 scratchpad；`build_system_prompt`（`context.py:207-273`）不注入
- **D1**：`loop.py:536-541` 构造 `MemoryExtractor` 不传 `user_id`，落回 `"default"`（`extractor.py:710`）→ `extractor.py:1123` 永远查不到行
- **D2**：`update_focus:134-141` 轮转只比 `old_focus != new_focus`，不与 `existing_projects` 去重；`classify_intent` 默认兜底 TASK（`intent.py:146`），CHAT 跳过门（`memory_extraction.py:441`）形同虚设

### 目标

- 新增 `render_working_memory_markdown(entry) -> str`（2 字段 + 清洗 + 单条截断），与写入器同文件
- `ContextBuilder.build_system_prompt` / `build_transcript` / `build_messages` 各加 `working_memory_section: str | None = None`，注入点在 `include_memory` 块之前
- `AgentLoop._build_turn` 新增 `_compute_working_memory_section(session_key)`，形态照 `_compute_retrieval_section`（全 try/except，不阻断 BUILD）；**门控全部在 loop 侧求值**
- 跳过 subagent（复用已有的 `is_subagent` 判据，`loop.py:2076`）与 dream（复用 `ctx.session_key.startswith("dream:")`，`loop.py:2060`）
- `update_focus` 轮转前做归一化去重
- `MemoryExtractor` 装配补 `user_id`
- `Consolidator` 的 token 探针带上工作记忆块

### 关键数据流

```
[Turn N: _build_turn]
   ├─ is_subagent = ctx.kind is TurnKind.SYSTEM and ctx.msg.sender_id == "subagent"   # loop.py:2076 已有
   ├─ ctx.session_key.startswith("dream:")                                              # loop.py:2060 已有
   ├─ (ctx.pending_retrieved_memory_section, ...) = await _compute_retrieval_section(...)  # 既有
   └─ ctx.pending_working_memory_section = await _compute_working_memory_section(ctx.session_key)   # 新增
         ├─ 门控（全部在此，异常不出 loop）：memory_extraction_enabled 已注册
         │        + memory_enabled_provider() + 非 subagent + 非 dream
         ├─ user_id = ScratchpadWriter.user_id_for_key(session_key)   # 与写入侧同一函数
         ├─ get_scratchpad(conn, user_id, self._memory_services.workspace_id)
         └─ render_working_memory_markdown(entry)  →  "" | markdown
   ↓
[Turn N: _run_agent_loop] transcript_builder partial 带上 working_memory_section      # loop.py:1301-1307
   ↓
[build_system_prompt] parts.insert(工作记忆) 在 [Current Project] 之后、include_memory 之前
```

## Tech Stack

Python 3.11+ / asyncio；SQLite（`nanobot/memory/database.py`）；pytest（`asyncio_mode="auto"`）；ruff（E,F,I,N,W，E501 忽略）；basedpyright。

## 文件改动清单

### WU-A（轮转去重）

| 文件 | 改动 |
| --- | --- |
| `nanobot/memory/scratchpad_writer.py` | `update_focus` 轮转前加归一化去重；新增模块级 `_normalize_focus_entry` 私有函数 |
| `tests/memory/test_scratchpad_writer.py` | 加去重用例 |

### WU-B（extractor user_id）

| 文件 | 改动 |
| --- | --- |
| `nanobot/agent/loop.py` | `_build_extractor` 补 `user_id=`；需确认该闭包的可见 `session_key` 来源，必要时加参数 |
| `tests/memory/test_loop_wiring.py` | 断言装配出的 `MemoryExtractor.user_id` 非默认 |

### WU-C（渲染器）

| 文件 | 改动 |
| --- | --- |
| `nanobot/memory/scratchpad_writer.py` | 新增 `render_working_memory_markdown(entry) -> str`（模块级函数）+ `_sanitize_injected_text` |
| `tests/memory/test_scratchpad_render.py` | **新建**：空态 / 单字段 / 超长截断 / `---` 伪造 / `#` 伪造 / 多空行折叠 |

### WU-D（注入链路）

| 文件 | 改动 |
| --- | --- |
| `nanobot/agent/context.py` | `build_system_prompt` / `build_transcript` / `build_messages` 各加 `working_memory_section` 参数；`build_system_prompt` 内在 `include_memory` 块之前 append |
| `nanobot/agent/loop.py` | `__init__` 存 `self._memory_extraction_enabled` / `self._memory_services` / `self._memory_enabled_provider`；`TurnContext` 加 `pending_working_memory_section`；`_build_turn` 加预计算；`_run_agent_loop` 与 `transcript_builder` partial 传参；新增 `_compute_working_memory_section` |
| `nanobot/agent/memory.py` | `Consolidator.__init__` 加 `working_memory_section_for_key: Callable[[str], str] | None = None`；`estimate_session_prompt_tokens` 带入探针 |
| `nanobot/agent/loop.py` | `Consolidator(...)` 构造处传入该 callable |
| `tests/agent/test_context_builder.py` | 加注入位置 / 空串不注入 / 门控用例 |
| `tests/agent/test_loop_working_memory.py` | **新建**：subagent 跳过 / dream 跳过 / 记忆开关关则跳过 / 装配门控 / DB 异常不阻断 / 压缩探针含工作记忆 |

### 不动的文件

- `nanobot/memory/scratchpad_writer.py` 的 `format_with_llm` / `_build_scratchpad` / `archive_completed`（S4 相关，spec §八 F1）
- `nanobot/memory/orchestrator.py`（S4 断链，属 F1）
- `nanobot/memory/intent.py`（不扩 CHAT 白名单，spec §1.8）
- `nanobot/memory/database.py` / `models.py` / `repository.py`（表结构、模型、CRUD 均已就绪）
- `nanobot/webui/**` 全部（不做页面）
- `nanobot/agent/hooks/memory_extraction.py`（T0 写入路径已正确）

---

## Task 拆分

> WU-A / WU-B / WU-C 互不依赖，可并行；WU-D 依赖 WU-C；WU-E 收尾。

---

### Task 0：实施前确认（阻塞全部 WU 启动）

**目标**：消解 4 个实现细节歧义，结论落执行日志。

**步骤**：
1. `grep -n "build_transcript" nanobot/agent/*.py nanobot/**/*.py` —— 确认 `build_transcript` / `build_messages` 的**全部调用方**（`Consolidator` 经 `self.context.build_messages` 注入，`loop.py:1302` 经 partial 注入），避免加参数时漏改
2. `grep -n "_build_extractor" nanobot/agent/loop.py` —— 看该闭包被谁调用、能否拿到 `session_key`（决定 WU-B 是加参数还是改签名）
3. `sed -n 1140,1165p nanobot/agent/memory.py` —— 确认 `estimate_session_prompt_tokens` 探针的构造参数
4. `grep -n "_should_retrieve\|_memory_enabled_provider" nanobot/agent/context.py` —— 确认门控闭包在 `AgentLoop` 侧的可访问性

**DoD**：4 项结论写入 execution-log；如有冲突先回报 Leader 再改 plan

---

### WU-A：轮转去重

**依赖**：无

**Task A1｜先写失败测试**（`tests/memory/test_scratchpad_writer.py`）

用例：
- 连续 `update_focus` 三次，第三次的 old_focus 与列表中某条归一化后相同 → `active_projects` 长度**不增**
- 归一化需忽略：时间戳前缀 `[MM-DD HH:MM] `、首尾空白、连续空白折叠
- 归一化**不**忽略：大小写、中文字符差异（保持精确匹配，见 spec §1.8）
- 不同 focus 仍正常轮转，`[:MAX_ACTIVE_PROJECTS]` 上限行为不变

**Task A2｜实现**（`nanobot/memory/scratchpad_writer.py:134-141`）

```python
existing_norm = {_normalize_focus_entry(p) for p in existing_projects}
if old_focus and old_focus != new_focus and _normalize_focus_entry(old_focus) not in existing_norm:
    new_projects.insert(0, self._format_project_entry(old_focus))
    new_projects = new_projects[:MAX_ACTIVE_PROJECTS]
```

`_normalize_focus_entry` 剥 `[MM-DD HH:MM] ` 前缀 → `strip()` → 折叠连续空白。**同步执行，不引入相似度算法**（保 T0 < 50ms）。

**DoD**：Task A1 全绿；`pytest tests/memory/test_scratchpad_writer.py` 无回归

---

### WU-B：extractor user_id 装配

**依赖**：无

**Task B1｜先写失败测试**（`tests/memory/test_loop_wiring.py`）

断言：从 `AgentLoop` 装配出的 `MemoryExtractor` 的 `user_id` **等于** `ScratchpadWriter.user_id_for_key(session_key)`，而非 `"default"`。

**Task B2｜实现**（`nanobot/agent/loop.py:536-541`）

给 `_build_extractor` 补 `user_id=ScratchpadWriter.user_id_for_key(session_key)`。**具体签名取决于 Task 0 步骤 2 的结论**——若闭包拿不到 `session_key`，则改为把 `session_key` 作为参数传入（参照 `_runtime_for_key(session_key)` 同款模式，`loop.py:543`）。

**DoD**：Task B1 全绿；`grep -n "get_scratchpad" nanobot/memory/extractor.py` 确认调用处的 user_id 来源已随装配修正

---

### WU-C：渲染器

**依赖**：无

**Task C1｜先写失败测试**（`tests/memory/test_scratchpad_render.py` 新建）

用例：
- 两字段皆空 → 返回 `""`
- 仅 `current_focus` → 只出 `## 当前任务`
- 仅 `active_projects` → 只出 `## 进行中`，条目保留 `[MM-DD HH:MM] ` 前缀
- `active_projects` 超过 5 条 → 截断到 5
- 单条超长 → 截断（写入侧已有 200 上限，**渲染侧再截一次**，防历史行超长）
- 清洗：值内独占一行的 `---` 被剥离
- 清洗：行首 `#` 被剥离
- 清洗：3 个以上连续换行折叠为 2
- 清洗**不**动：行内 `**强调**`、`- 列表`（spec §1.7）
- 截断发生时 `logger.debug` 有记录

**Task C2｜实现**（`nanobot/memory/scratchpad_writer.py`）

模块级新增：
```python
_WORKING_MEMORY_ITEM_LIMIT = 5
_WORKING_MEMORY_ITEM_MAX_CHARS = 200

def render_working_memory_markdown(entry: ScratchpadEntry) -> str: ...
def _sanitize_injected_text(value: str) -> str: ...
```

渲染骨架见 spec §1.2。`## 进行中` 的条目**直接复用** `_format_project_entry` 已写入的时间戳前缀（`scratchpad_writer.py:93`），不重新生成。

**DoD**：Task C1 全绿

---

### WU-D：注入链路

**依赖**：WU-C

**Task D1｜builder 加参数**（`nanobot/agent/context.py`）

- `build_system_prompt`（`:207`）加 `working_memory_section: str | None = None`；注入点在 `if include_memory:`（`:238`）**之前**、即 `[Current Project]` 块之后
- 语义：`if working_memory_section: parts.append(working_memory_section)`——**空串与 None 都不 append**
- `build_transcript`（`:441`）、`build_messages`（`:394`）同步加参数并透传

**Task D2｜loop 预计算**（`nanobot/agent/loop.py`）

- `__init__`：`self._memory_extraction_enabled = memory_extraction_enabled`；`self._memory_services = memory_services`；门控闭包存为可调用的 provider（若构造参数已是 `memory_enabled_provider` 则直接用）
- `TurnContext` 加 `pending_working_memory_section: str = ""`
- `_build_turn`（`:2162` 附近，紧随 `_compute_retrieval_section` 调用之后）：赋值
- 新增 `_compute_working_memory_section(session_key) -> str`，形态照 `_compute_retrieval_section`（`:2179-2200`）：**整体 try/except，异常 `logger.warning` 后返回 `""`**

**Task D3｜门控（全部在 loop 侧求值）**

按顺序短路，任一不满足返回 `""`：
1. `self._memory_extraction_enabled` 为真——**不是** `memory_services is not None`（spec §1.3，`loop.py:522` 抽取照样装配）
2. `_memory_enabled_provider()` 为真——provider 调用**必须在 try 内**（读侧无 try，与写侧 `memory_extraction.py:283-287` 异常语义相反）
3. `not is_subagent`——复用 `loop.py:2076` 已有判据 `ctx.kind is TurnKind.SYSTEM and ctx.msg.sender_id == "subagent"`
4. `not ctx.session_key.startswith("dream:")`——复用 `loop.py:2060` 已有分支
5. `render_working_memory_markdown(entry)` 非空

行定位：`user_id = ScratchpadWriter.user_id_for_key(session_key)`，与写入侧同一函数；`workspace_id = self._memory_services.workspace_id`。

**Task D4｜传参接线**

- `_run_agent_loop` 签名加 `working_memory_section`，`:2221-2222` 区域传入
- `transcript_builder` partial（`:1301-1307`）加 `working_memory_section=...`
- `Consolidator` 构造（`:464-473`）加 `working_memory_section_for_key=self._compute_working_memory_section_sync`

**Task D5｜压缩探针**（`nanobot/agent/memory.py`）

- `Consolidator.__init__` 加 `working_memory_section_for_key: Callable[[str], str] | None = None`（`:773` / `:1031` 两处 `self._build_messages = build_messages` 附近）
- `estimate_session_prompt_tokens`（`:1139-1159`）用 `session.key` 取工作记忆块并传给 `_build_messages`（`:1150`）

> **相邻既有问题（不在本轮范围，但需在 execution-log 记一笔）**：该探针同样**不传** `retrieved_memory_section`，因此召回块（上限 700 token，`engine.py:77,128,243`）也一直被漏算。`_SAFETY_BUFFER = 1024`（`:1019`）小于工作记忆 + 召回块之和。本轮只补工作记忆，召回块漏算记为后续项。

**DoD**：Task D1–D5 全绿

---

### WU-E：测试收尾

**依赖**：WU-A、WU-B、WU-D

**Task E1｜回归**
```bash
pytest tests/memory/ tests/agent/ -q
ruff check nanobot/
uv run --no-sync basedpyright
```

**Task E2｜手测（不可省，spec §五.4）**

起 gateway（`nanobot gateway`），开记忆，跨 3 个话题对话，逐条核对：

| # | 场景 | 期望 |
| --- | --- | --- |
| 1 | 任意主对话 turn | system prompt 中 `# Working Memory` 出现在 `# Memory` **之前** |
| 2 | 换话题 | `## 当前任务` 随之更新；`## 进行中` 出现带 `[MM-DD HH:MM]` 的条目 |
| 3 | **同话题连问 5 轮**（「那再帮我看看 B」「那 C 呢」…） | `## 进行中` **不新增碎片**（验 WU-A） |
| 4 | 记忆总开关关掉 | 块消失 |
| 5 | 派发一个 subagent | 子代理 system prompt **无**该块 |
| 6 | 触发 `/dream` | 固化会话 **无**该块 |
| 7 | 抽取触发后 | LLM 侧工作记忆快照**非空**（验 WU-B，可用日志或临时探针） |
| 8 | `/cost` 或 provider 计数 | 记录实际 token 数，与 spec §1.6 估算 1300–1600 对照 |

**Task E3｜落盘**
- `.ai-runtime-artifacts/verifications/2026-09-29-working-memory-injection-verification.md`：命令输出 + 8 项手测结果 + 实际 token 数
- `.ai-runtime-artifacts/execution-logs/2026-09-29-working-memory-injection.md`

---

## 风险与权衡

| 风险 | 缓解 |
| --- | --- |
| 自反馈回路：截自用户消息的内容下轮回灌 system prompt | 渲染层三步清洗（WU-C）；Task C1 专门覆盖伪造 `---` / `#` 的用例 |
| 压缩阈值偏乐观 | Task D5 补工作记忆计数；`_SAFETY_BUFFER` 余量在 WU-E 手测中观察 |
| subagent 跳过依赖 `is_subagent` 判据的稳定性 | 该判据是 `loop.py:2076` 既有生产代码，非本轮新增；Task D1 的调用方全量 grep（Task 0 步骤 1）确认无其他 subagent 入口 |
| 去重只挡精确重复，挡不住改写式追问 | 已知残留，spec §八 F2 记录；先上线看实际轮转质量 |
| WU-B 若需改 `_build_extractor` 签名，波及面可能超预期 | Task 0 步骤 2 前置确认；异常则回报 Leader |

## 验收口径

1. `pytest tests/memory/test_scratchpad_writer.py tests/memory/test_scratchpad_render.py tests/agent/test_context_builder.py tests/agent/test_loop_working_memory.py` 全绿
2. `pytest tests/memory/ tests/agent/` 无回归
3. `ruff check nanobot/` 无新增
4. `basedpyright` 无新增错误
5. WU-E Task E2 的 8 项手测全部符合期望

## 不在本次范围

- WebUI 页面 / API 增删
- 打通 S4 拿 4 字段（spec §八 F1，需 3 处改动 + 每会话一次 LLM 调用，且**须先完成 WU-A**）
- `update_focus` / `archive_completed` 的 merge 语义（无数据可保，spec §0.5）
- 第 5 路召回通道、openakita `working_facts` 层
- 扩 `classify_intent` 的 CHAT 白名单
- 压缩探针的召回块漏算（相邻既有问题，见 Task D5 注）

## 派工建议

- WU-A / WU-B / WU-C **可并行派发**（文件树：`scratchpad_writer.py` / `loop.py` 装配段 / `scratchpad_writer.py` 渲染段——A 与 C 同文件但改动函数不相邻，串行更稳，建议 A→C 串行、B 独立并行）
- WU-D 待 WU-C 完成后派发
- WU-E 由 Leader 在全部 WU 返回后统一执行
- `wu_type` → `agent_role`：`feature` → `coder`；`test` → `test-engineer`（仅 Task E1–E2 需要）
- 每个 WU 需在完成声明前提供验证命令证据
