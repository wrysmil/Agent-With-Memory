# openakita 子 Agent 集成机制调研（为 nanobot 移植）

> 调研日期：2026-09-30
> 调研对象：`D:\studyspace\源码学习\openakita\openakita`（内层为真实仓库根）
> 改造对象：`D:\studyspace\源码学习\Agent-With-Memory`（nanobot，`feature/memory-system` 分支）
> 用途：搞清 openakita 如何把「子 Agent」集成到运行中的 agent，为 nanobot 记忆/身份系统改造提供事实依据。
> 方法：5 路并行只读探查（调度器 / 定义构建 / 工具与提示词 / 运行时接线 / 组织树），本文为 Leader 汇总。
>
> ⚠️ **仓库自带的 `openakita_orchestration_analysis.md` 已严重过时，不能当实现依据**（详见 §二）。
> ⚠️ 代码块是**节选/重排**，非逐字摘录；作为移植依据前请回源核对行号。

---

## 〇、结论速览

openakita 的子 Agent **不是一层，是两套并列机制**：

| | `agents/`（个人多 Agent） | `orgs/`（组织树编排） |
| --- | --- | --- |
| 规模 | `orchestrator.py` 1929 行 + 其余约 6000 行 | 约 2.6 万行，34 个模块 |
| 形态 | 扁平 AgentProfile 列表，LLM 自选目标 | 虚拟公司树，Agent 有汇报关系 |
| 委派目标 | 任意 profile id | **直属下级**，schema enum 硬锁 |
| 执行体 | 完整 `Agent` 实例（工厂+实例池） | `_BrainBackedNodeAgent` 轻量壳，**复用主 Agent 的 brain** |
| 多级递归 | **禁止**（单跳，最大深度恒为 1） | 支持，DAG 依赖，深度上限 6 |
| 触发方式 | LLM 自主决策 | **纯显式**：UI 开关 / `/org` 命令 |

两者**互不 import**（双向 grep 确认），只共享底层 `Brain`（LLM 客户端）和 `handler_registry`（工具执行器）两个宿主资源。

**给 nanobot 的三条最值得借鉴**：

1. **回执结构化 + 哨兵块**（§五）——子 Agent 结果以带状态头的文本回灌，artifact 用 `__ARTIFACT_RECEIPTS__` 哨兵承载；并行时先摘哨兵、剥正文、最后合并重挂，避免被截断丢失。
2. **ContextVar 收集 → 统一调度**（§七）——把 LLM 的「声明」与运行时的「调度」彻底解耦，天然规避 tool_use 期间跨 await 递归调 LLM。
3. **协作原则提示词**（§六）——教 LLM「怎么写委派 prompt」而不是「你有子 Agent」。

**给 nanobot 的四条反面教训**：

1. **定义形态选错**——44 字段的 Python dataclass、profile 里**没有 `model` 字段**（21 个预设 Agent 全跑同一模型），不可扩展、不可让用户自建。
2. **schema 与 handler 脱节**——`model` / `run_in_background` / `fork` 三个入参在 schema 里但 handler 完全不读，LLM 误以为可用（§八）。
3. **死代码密度极高**——`backends.py`（176 行）零引用、`task_queue.py` 挂着不跑、`tracer.delegation_span()` 未接线、6 个组织工具名只存在于提示词文本里。**预留而不接线的设计在这个项目里成了系统性技术债。**
4. **双实例池**——`main.py:428` 与 `orchestrator.py:452` 各持一个 pool，key 格式还一样，同一 `(session, profile)` 会拿到两个 Agent 对象（§九）。

---

## 一、nanobot 现状基线（对照用）

调研前先固定 nanobot 的起点，避免对照时把已有能力当缺失。

| 项 | nanobot 现状 | 位置 |
| --- | --- | --- |
| 工具名 | `spawn` | `nanobot/agent/tools/spawn.py:62-63` |
| 入参 | `task`(必填) / `label` / `temperature` / `wait` | 同上 `:24-46` |
| 并发控制 | `asyncio.Semaphore(max_concurrent_subagents)` | `subagent.py:151` |
| 执行 | `spawn()` 后台 / `run_inline()` 阻塞，二选一 | `subagent.py:229` / `:293` |
| 结果回传 | 走 MessageBus 发 InboundMessage（`channel="system"`, `sender_id="subagent"`），模板 `agent/subagent_announce.md` | `subagent.py:496-539` |
| 子 Agent prompt | 模板 `agent/subagent_system.md`（约 15 行） | `subagent.py:541-562` |
| 防递归 | **无** —— spawn 工具对子 Agent 仍可见 | 无 |
| 状态存储 | 内存 `dict[str, SubagentStatus]` | `subagent.py:156` |
| 取消 | `cancel_by_session()` 逐个 `task.cancel()` | `subagent.py:564-573` |

**关键差异**：nanobot 的 `spawn` 有 `wait` 参数做「阻塞咨询 vs 后台任务」二选一，openakita 没有对应能力（它的后台参数 `run_in_background` 是死参数）。反过来 openakita 有防递归三道防线、深度熔断、AbortScope 取消树、结构化回执，nanobot 全无。

---

## 二、⚠️ 仓库自带文档已过时

`openakita_orchestration_analysis.md` 描述的多项机制**在当前代码里不存在**：

| 文档说法 | 代码事实 |
| --- | --- |
| 4 个组织工具 `org_delegate_task` / `org_send_message` / `org_submit_deliverable` / `find_colleague` | **只有 2 个**真实存在：`org_delegate_task`（`orgs/_runtime_delegation.py:173`）、`org_submit_deliverable`（`orgs/_runtime_delivery_manifest.py:504`） |
| 节点间消息协调（`org_send_message`） | **全仓无实现**。只有进程内结构化返回值 `DelegationExecutionResult` |
| 「承接者识别 / find_colleague / 最佳承接者选择」 | 无此代码。委派目标由 `build_delegate_tool` 的 `target` 字段 enum 硬约束为直属下级（`_runtime_delegation.py:193`） |
| 「系统自动触发编排」 | 实际是**显式 UI 开关 + `/org` 命令**，零 LLM 自动决策 |
| 「1-2 分钟目标理解阶段」等 | 纯叙事。实际只有 `Organization.status`（5 值枚举）一个粗粒度状态 |

`org_send_message` 等 6 个幽灵工具名大量残留在：`core/policy_v2/classifier.py:108-128`（审批分类表）、`core/_supervisor_runtime.py:138-145`（白名单）、`agents/coordinator_prompt.py`（提示词文本）、`config.py:1323-1341`（专门拦截「用 org_send_message 派任务绕过 chain 注册」的守卫）。

**结论**：该文档是**产品设想稿**。若团队继续引用，建议标注「基于 v1 设想，v2 实现已偏离」。

---

## 三、派发主链路

### 3.1 入口是工具调用分支，不是独立分支

```
LLM 输出 tool_call "delegate_to_agent"
  → ReasoningEngine 顺序 for 循环逐个 await    core/_reasoning_runtime.py:2878
  → ToolExecutor.execute_tool_with_policy
  → AgentToolHandler.handle                    tools/handlers/agent.py:64
  → AgentToolHandler._delegate                 tools/handlers/agent.py:86
  → Orchestrator.delegate                      agents/orchestrator.py:1460
  → _dispatch(depth+1)                         agents/orchestrator.py:594
  → _run_with_progress_timeout                 :636
  → _call_agent                                :1175
  → agent.chat_with_session                    :1246
  → 返回字符串作为 tool_result 回灌 LLM
```

**修正一个常见误解**：`core/_agent_runtime.py`（8604 行）**不含**派发逻辑，它只持有 `_is_sub_agent_call` 标志并据此改变自身行为。真正的接线在 `agents/orchestrator.py` + `tools/handlers/agent.py`。

### 3.2 一次派发的完整时序

```
delegate(session, from_agent, to_agent, message, depth, reason)
  ├─ ① tracer.record_decision("delegation")              :1483
  ├─ ② 预注册 _sub_agent_states[key] = "starting"          :1500
  │    └─ _broadcast_sub_state_change → WS "agents:sub_state"
  ├─ ③ 追加 session.context.handoff_events（上限 100）     :1528
  └─ ④ _dispatch(depth+1)
       ├─ 深度闸门 depth >= MAX_DELEGATION_DEPTH(5)         :605
       ├─ 追加 session.context.delegation_chain            :610
       ├─ _log_delegation("dispatch_start") → JSONL        :474
       └─ _run_with_progress_timeout
            ├─ 取 profile → 覆盖 timeout_seconds / max_turns
            ├─ agent = pool.get_or_create(session.id, profile)   :804
            ├─ 注册 state + stream_meta，广播 "starting"    :824
            ├─ task = create_task(_call_agent(...))         :866  ← 起协程
            └─ while not task.done(): 每 3s 轮询
                 ├─ hard_timeout 到 → task.cancel()        :887
                 ├─ 进度指纹变化 → 刷新 last_progress_time  :901
                 ├─ 广播 "running"                          :936
                 └─ idle_timeout 到 → task.cancel()        :952
            ↓
       _call_agent
            ├─ async with agent._execution_lock   ← 同实例串行   :1195
            ├─ agent._is_sub_agent_call = (depth > 0)         :1199
            ├─ 流式调用 chat_with_session，事件转发 WS        :1233
            ├─ _persist_sub_agent_record → 父 session         :1266
            ├─ 数值幻觉守卫 validate_no_fabricated_numbers   :1335
            └─ 组装 DelegationResult.to_tool_response()      :1357
            ↓ finally
       _cleanup_sub_agent_resources(agent, session)           :1371
  ↓
  终态 → _update_sub_state → 落盘 sub_agent_states.json + 延迟 120s 清理  :980
```

### 3.3 上下文隔离怎么做的

只传任务文本，**不传父对话历史**（`tools/handlers/agent.py:113-118`）：

```python
isolated_message = ""
if context:
    isolated_message += f"[任务背景]\n{context}\n\n"
isolated_message += f"[任务指令]\n{message}"
if reason:
    isolated_message += f"\n[委派原因] {reason}"
```

结果回程还要过一遍防注入包裹（`handlers/agent.py:127`）：

```python
return wrap_external_content(str(result), source=f"sub_agent:{agent_id}")
```

### 3.4 执行上下文复用（关键事实）

`agents/orchestrator.py:1246-1253`：

```python
result = await agent.chat_with_session(
    message=message,
    session_messages=session_messages,   # ← 父 session 的历史
    session_id=session.id,               # ← 父 session 的 id
    session=session,                     # ← 父 session 对象本身
    gateway=gateway, mode=_mode,
)
```

**子 Agent 没有独立 session**，复用父 session 的 id 和历史。Agent 实例的隔离靠 Pool key（`{session_id}::{profile_id}`），不靠 session。子 Agent 自己的对话**不落盘**，只把结果摘要写进父 session 的 `sub_agent_records`（上限 50）。

---

## 四、并发与隔离

### 4.1 并发：三层限流

| 层级 | 机制 | 位置 |
| --- | --- | --- |
| 单会话串行 | `_session_semaphores[sid] = Semaphore(1)` | `orchestrator.py:414, 569` |
| 同实例串行 | `agent._execution_lock`（惰性创建） | `orchestrator.py:1195` |
| 显式并行 | `asyncio.gather`，一次 2–5 个 | `handlers/agent.py:304` |

**并行上限 5 是 handler 层硬编码，`asyncio.gather` 外面没有信号量兜底。** `TaskQueue(max_concurrent=5)` 那个设置在从未启动的队列上。

### 4.2 ⚠️ 委派是严格同步阻塞的 fork-join

**不存在「主 Agent 边跑子 Agent 边继续对话」。**

- `_reasoning_runtime.py:2878` 是顺序 for 循环逐个 await
- 主 Agent 在子 Agent 跑完前**不能发起新工具调用，也不能再次触发 LLM 推理**
- `delegate_parallel` 的并发只在**子 Agent 之间**，主 Agent 依然阻塞等全部完成
- `_run_with_progress_timeout` 虽然用 `create_task` 起子 Agent，但立刻进 `while not task.done(): await asyncio.sleep(3)` 轮询循环（`:883`）——仍是阻塞等待

### 4.3 隔离边界：基本为零

| 维度 | 实际做法 |
| --- | --- |
| 进程 | 无隔离，同进程同事件循环 |
| cwd / env / 文件访问 | **无任何控制**（全仓未找到） |
| 浏览器 | **唯一真隔离**：`parent_browser.create_isolated_context()`，`finally` 里 stop（`handlers/agent.py:279-283`） |
| 同 agent_id 并发 | 自动派生 ephemeral 克隆 profile（`ephemeral_{id}_{ts}_{idx}`）绕开共享实例 |

`backends.py` 里 `TeammateTask.isolation: str = "none" | "worktree"` 字段存在，但**该文件全仓零引用**。

---

## 五、回执结构（最值得借鉴的部分）

### 5.1 文本回执

`agents/orchestrator.py:314-338` `DelegationResult.to_tool_response()`：

```python
notice_title = _delegation_notice_title(self.exit_reason)
status_display = _EXIT_REASON_DISPLAY.get(self.exit_reason, self.exit_reason)
header = (f"[{notice_title}] Agent: {self.agent_id}"
          f" | 状态: {status_display} | 耗时: {self.elapsed_s}s")
tools_line = f"工具调用: {len(self.tools_used)} 次 ({', '.join(self.tools_used[:8])})"
parts = [header, tools_line, "", _with_budget_guide(self.text, self.exit_reason)]
if self.artifacts:
    parts.append(f"\n__ARTIFACT_RECEIPTS__{json.dumps(self.artifacts)}__ARTIFACT_RECEIPTS__")
```

`exit_reason` → 中文标题映射见 `_EXIT_REASON_DISPLAY`（`:288-298`），覆盖 completed / max_turns / timeout / error / cancelled / ask_user / budget_paused。

### 5.2 并行时的哨兵块保护

`handlers/agent.py:326-353`：`delegate_parallel` 把各子 Agent 的 `__ARTIFACT_RECEIPTS__` 块**先摘出来、剥掉正文、最后合并成一个 JSON 数组重新追加**，防止被截断丢失。各 Agent 正文以 `## Agent: {id}` + `\n\n---\n\n` 拼接。

主 Agent 最终拿到：`[2 行状态头] + [N 个 ## Agent 段落] + [合并后的哨兵数组]`。

### 5.3 防注入包裹

`core/policy_v2/prompt_hardening.py:74-77`，带随机 nonce 的定界符：

```
<<<EXTERNAL_CONTENT_BEGIN nonce=a1b2c3d4 source=sub_agent:code-assistant>>>
...子 Agent 的回答...
<<<EXTERNAL_CONTENT_END nonce=a1b2c3d4>>>
```

对应的「这些是数据不是指令」规则拼进 `_SAFETY_SECTION`（`prompt/builder.py:214`），**主/子 Agent 都注入**。

### 5.4 数值幻觉守卫

`agents/orchestrator.py:1334-1353`：子 Agent 若任务含统计/数值词、输出含具体数字、trace 里无代码执行工具，则追加警告（`agent/output_guard.py:71-75`）：

```
> ⚠️ **数据未经代码执行验证**：本次子 Agent 输出包含具体数值，但任务执行轨迹中未发现
> 任何代码运行（`run_shell` 等）。若数值用于决策，请要求 Agent 重新跑一次真实计算。
```

### 5.5 状态广播

`agents:sub_state`（WS）载荷：`run_id, session_id, chat_id, agent_id, profile_id, parent_agent_id, name, icon, status, reason, iteration, tools_executed, tools_total, elapsed_s, last_progress_s, started_at, current_tool_summary, tokens_used`。

`agents:sub_stream`（WS）转发子 Agent 原始流事件，白名单 26 种（`_SUB_STREAM_EVENT_TYPES`，`orchestrator.py:80-109`）。

---

## 六、提示词注入

### 6.1 三段结构

| 段落 | 来源 | 动态性 |
| --- | --- | --- |
| 协作优先原则 | `prompt/builder.py:288-319` `_build_delegation_rules()` | **纯硬编码**，与实际 Agent 清单无关 |
| 架构概况 | `prompt/builder.py:1243-1283` `_build_arch_section()` | 按 `is_sub_agent` 分主/子两版 |
| **Agent 名册** | `core/_agent_runtime.py:3381` `_build_multi_agent_prompt_section()` | **唯一动态列举** |

协作原则只在 `FULL 且 not is_sub_agent 且 mode=="agent"` 时注入（`builder.py:454-455`），插在 Identity 层和 Persona 层**之间**。

### 6.2 协作原则原文（最值得抄的一段）

```
## 协作优先原则

你拥有一支专业 Agent 团队。执行任务前，先判断是否有更合适的专业 Agent：
- 有专业 Agent 能处理 → 立即委派（delegate_to_agent），不要自己尝试
- 任务涉及多个专业领域 → 拆分并行委派（delegate_parallel）
- 只有简单问答或用户明确要你亲自做 → 才自己处理

### 给子 Agent 写 prompt 的原则

像给一个刚进入房间的聪明同事做简报——它没看过你的对话，不知道你试过什么：
- 说明你想完成什么、为什么
- 描述你已经了解到什么、排除了什么
- 给足上下文，让子 Agent 能做判断而不是盲目执行指令
- **永远不要委派理解**：不要写"根据你的调查结果修复问题"。
  写 prompt 要证明你自己理解了问题——包含具体的信息和位置
- 简短的命令式 prompt 会产出肤浅的结果。

### 关键规则
- 启动子 Agent 后简短告知用户你委派了什么，然后结束本轮
- **绝不编造或预测子 Agent 的结果** — 结果以后续消息到达为准
- 子 Agent 失败时，优先带完整错误上下文继续同一个子 Agent；多次失败再换思路或上报用户

以下情况应自己处理，**不要委派**：
- 知识问答、架构讨论、方案分析、计算推理等纯对话任务
- 用户明确要你亲自回答的任务
- 没有明确匹配的专业 Agent 时
```

它教的是**怎么写委派 prompt**（简报隐喻、禁止委派理解、禁止预测结果），而不是「你有子 Agent」这个事实。

### 6.3 动态名册

`core/_agent_runtime.py:3414-3473`。遍历 `SYSTEM_PRESETS`（21 个）排除自己，再拼上 `get_profile_store().list_all(include_ephemeral=False)` 里的用户自建 Agent。格式压缩为 `icon + name + (id) + description`，**不带技能列表**（代码注释：`no skill lists to save tokens`）。

### 6.4 ⚠️ 名册不受预算管控

`apply_budget` 段级预算（`builder.py:659-698`）只作用于 `system_parts` / `developer_parts` / `user_parts` / `tool_parts` 四个列表。名册是在 `build_system_prompt()` **返回之后**由 `_agent_runtime.py:3291` 直接 `prompt +=` 拼接的，**完全绕过所有预算裁剪**。用户自建 Agent 数量无上限。

对比：走 `apply_budget` 的部分预算很硬（`prompt/budget.py:89-114`）：`identity=6000` / `catalogs=8000` / `memory=3500` / `total=22000`。

副作用：名册在 system prompt 缓存**之外**。`_system_prompt_cache` 命中时直接返回，但 `prompt +=` 每次执行——所以切换 Agent Profile 立即反映到名册，不需失效缓存。

---

## 七、组织树层（orgs）

### 7.1 数据模型

`Organization`（`org_models.py:409-738`，47 字段）/ `OrgNode`（`:163-274`，38 字段）/ `OrgEdge`（`:277-374`，8 字段）。

**双表示**：`level: int`（冗余深度标记）+ `edges` 里的 `HIERARCHY` 边（真实拓扑）。三个访问器：`get_root_nodes()`（`level==0`）、`get_children()`、`get_parent()`（`:720-737`）。

**引用解析双轨制**（设计说明在 `:635-670`）：

- `get_node(query)` —— **宽松模糊**：id 归一化 → role_title 子串双向包含 → 分词全命中
- `resolve_reference(query)` —— **严格**：只接受 `exact_id` / `exact_title`，返回 5 态 status

原因写在 `:645-651`：「产品总监 vs 产品经理」前缀相同时模糊匹配会自委派，ReAct 死循环直到 Supervisor 终止。**所有有写副作用的路径都用 `resolve_reference`。**

### 7.2 ⭐ 最值得借鉴的设计：声明与调度解耦

`_runtime_delegation.py` + `_default_agent_builder.py` 的 ContextVar 收集模式：

```
协调者 LLM 一轮内连续多次调 org_delegate_task
  → queue_delegation()                    _runtime_delegation.py:281
     · 校验 command_id 存在
     · 校验 target ∈ 直属下级（current_delegation_targets_var）
     · 校验 depends_on 只能引用本次 activation 已声明的 step_id（本地 DAG）
     · DelegationLedger.claim() 去重（同一 assignment+slot+tool 只跑一次，max 3 次）
     · 追加到 current_delegation_requests_var（ContextVar 收集，不立即执行）
     · 返回 {"ok":true,"queued":true}    ← 立即返回，LLM 可继续声明下一个
  ↓
LLM 声明完完整 DAG 后返回
  ↓
_BrainBackedNodeAgent._maybe_dispatch()   _default_agent_builder.py:970
     · 按 depends_on 分波，每波 asyncio.gather 并发         :1326
     · 依赖未完成 → 发 delegation_dependency_blocked，标 BLOCKED
     · 依赖完成 → 上游输出（截断 2000 字符）+ artifact 证据装进 UpstreamContext 注入下游
```

**这彻底解耦了 LLM 的「声明」与运行时的「调度」，天然规避了 tool_use 期间跨 await 递归调 LLM 的问题。**

### 7.3 节点执行

**两个常见误解需要澄清**：

- `node_scheduler.py`（469 行）= **定时任务调度器**（只管 cron/interval），**不是节点执行器**。它还有「智能退避」：连续 5 次无异常就把间隔 ×1.5 拉长，上限 ×4（`:396-419`）
- `_runtime_agent_host.py`（359 行）= **工具执行宿主**（解决 orgs 节点的 tool_use 打到哪个 handler）

真正的执行器是 `_runtime_agent_pipeline_executor.py`（1540+ 行）。执行体 `_BrainBackedNodeAgent`（`_default_agent_builder.py:724-777`）是个只持 `spec` + `brain` 的轻量壳，slots 只有 5 个字段，**复用主 Agent 的 brain**，不是新起 Agent 实例也不是新进程。

`AgentCache` 按 `(org_id, node_id)` 缓存，**无容量上限、无 TTL**。

### 7.4 关键约束

| 约束 | 值 | 位置 |
| --- | --- | --- |
| 单次 activation 最多声明派单 | 16 | `_runtime_delegation.py:13` |
| 递归深度上限 | 6（`MAX_DISPATCH_DEPTH`） | `_runtime_agent_pipeline.py:62` |
| 同一 key 重试 | 3 | `_runtime_delegation.py:14` |
| 每 org 同一 root 同时命令 | 1 | `_runtime_dispatch.py:153` |
| 节点工具轮次 / 调用数 | 6 / 16 | `_runtime_node_tools.py:655, 675` |
| 单次 LLM 调用超时 | 240s（用 `RuntimeError` 而非 `TimeoutError`，避免被误归因为节点级超时） | `_runtime_node_tools.py:684` |

### 7.5 blackboard：只写不读

三层作用域 ORG(200) / DEPARTMENT(100) / NODE(50)，JSONL 追加存储。

**共享路径不是 LLM 调工具**——`org_write_blackboard` 之类工具**不存在**。实际写入方是 `OrgRuntime._publish_process_log()`（`runtime.py:1720-1762`），由编排事件驱动。

`_runtime_node_artifacts.persist_node_memory` 的 docstring（`:506-509`）明说「we do NOT yet wire a retrieval step that feeds these files back into the next node's system prompt」——**黑板当前是只写不读的展示层**。

### 7.6 触发方式：纯显式

三条入口，全部需用户显式动作：桌面 UI `orgMode` 开关（`ChatView.tsx:1358`）、IM 斜杠命令 `/org <名字> <任务>`（`gateway.py:3195-3218`）、IM v2 canary（需白名单）。

**组织模式开启时，消息根本不过主 Agent 的 LLM**（`chat.py:2367` 直接取 `org_command_service` 走 org SSE 流）。

---

## 八、工具契约

### 8.1 6 个工具，2 常驻 4 按需

`src/openakita/tools/definitions/agent.py` 的 `AGENT_TOOLS`（`:13-434`）。

**分发策略**（`tools/defer_config.py:11-29`）：只有 `delegate_to_agent` / `delegate_parallel` 在 `STABLE_MAIN_CHAT_CORE_TOOLS` 里常驻完整 schema；`spawn_agent` / `create_agent` / `task_stop` / `send_agent_message` 走 `_deferred=True`，**Brain 不发 API schema**，LLM 只能从文本目录看到名字，需要时先 `tool_search` 提升。

即：**首选路径常驻、备用路径按需**。

### 8.2 `delegate_to_agent` 的 description 原文

```
Delegate a task to an existing specialized agent. This is the PREFERRED way
to use multi-agent collaboration. Use when: (1) An existing agent profile
matches the task, (2) You need domain expertise (code, data, browser, docs),
(3) The task can be fully handled by an existing agent without customization.

IMPORTANT:
- Launch multiple agents concurrently whenever possible for independent tasks
- Do NOT launch more than 4 concurrent agents
- Sub-agent results are not directly visible to the user — summarize them in
  your response
- Prefer 'fast' model for quick, straightforward sub-tasks to minimize cost
- Use 'capable' only when the task requires deep reasoning
```

入参：`agent_id`(必填) / `message`(必填) / `reason` / `model`(enum: fast|default|capable) / `context` / `run_in_background`(bool) / `fork`(bool)。

### 8.3 ⚠️ 三个死参数

`model` / `run_in_background` / `fork` **全仓 grep 只有 schema 定义处出现，handler 完全不读**：

```python
# tools/handlers/agent.py:87-90 —— _delegate 只取 4 个字段
agent_id = (params.get("agent_id") or "").strip()
message = (params.get("message") or "").strip()
reason = (params.get("reason") or "").strip()
context = (params.get("context") or "").strip()
```

**LLM 会以为自己能选模型、能后台跑、能 fork 上下文，实际全部静默忽略。** 这是文档与实现脱节的 bug 级问题。

### 8.4 `delegate_parallel` 的 examples 教化

`definitions/agent.py:239-286` 的 `examples` 字段含反例：

```python
{
    "scenario": "❌ 错误：把调研任务分给不对口的 Agent",
    "params": {"tasks": [
        {"agent_id": "browser-agent", "message": "调研项目A"},
        {"agent_id": "code-assistant", "message": "调研项目B"},
    ]},
    "expected": "严禁！code-assistant 是代码助手，不擅长网络调研。应该让两个调研任务都用 browser-agent",
}
```

顶层 `context` 会被**自动前置到每个子任务**。

### 8.5 防递归：三道独立防线

| 层 | 机制 | 位置 |
| --- | --- | --- |
| 工具清单剥离 | `_is_sub_agent_call` 为真时从 tools 列表剔除 | `core/_agent_runtime.py:903, 1110` |
| handler 兜底 | 即使调到也直接拒绝 | `handlers/agent.py:64-67` |
| Prompt 硬指令 | 注入「禁止使用 delegate_to_agent…」 | `core/_agent_runtime.py:3396-3401` |

`orchestrator.py:1186-1194` 的 docstring 说明设计意图：

```
Sets _is_sub_agent_call on sub-agents (depth > 0) so that:
1. _finalize_session skips plan auto-close (the plan belongs to the parent)
2. AgentToolHandler blocks re-delegation (prevents infinite recursion)
```

**注意一个矛盾**：`MAX_DELEGATION_DEPTH = 5`（`orchestrator.py:77`）看起来允许 5 层，但因为 `depth>0` 就剥离委派工具，**实际最大深度恒为 1**，这个熔断是永远碰不到的死保险。

### 8.6 权限：走完整 policy_v2

`handlers/agent.py:52-59` 显式声明 ApprovalClass：

```python
TOOL_CLASSES = {
    "delegate_to_agent": ApprovalClass.CONTROL_PLANE,
    "delegate_parallel": ApprovalClass.CONTROL_PLANE,
    "spawn_agent": ApprovalClass.CONTROL_PLANE,
    "create_agent":  ApprovalClass.CONTROL_PLANE,
    "task_stop":     ApprovalClass.INTERACTIVE,
    "send_agent_message": ApprovalClass.INTERACTIVE,
}
```

`policy_v2/engine.py:810-819`：**CONTROL_PLANE 类默认即 owner-only**。`adapter.py:79-91` 的 `_FAIL_CLOSED_TOOL_PREFIXES` 含 `delegate_` / `spawn_` / `create_agent`。

**子 Agent 用父 Agent 的权限，无额外收窄**，但从父 ContextVar 继承策略上下文（`core/_agent_runtime.py:5980-6000`），委派链可追溯（`delegation_chain` 逐层 append），**无法提权**。

### 8.7 一次性任务式，无多轮

`delegate_to_agent` 的 `required: ["agent_id", "message"]`，**无 `conversation_id` / `thread_id` / `continue` 类参数**。

每次委派都从 `session.context.get_messages()` 重新取**父 Agent 的**历史，子 Agent 自己上一轮说了什么**不进历史**。

提示词里说的「继续同一个子 Agent」，实现方式是**重新带完整错误上下文再委派一次**（Agent 实例复用 + 上下文靠主 Agent 手写）——这是一个语义 gap。

**唯一的「多轮」通道 `send_agent_message` + `AgentMailbox` 是断的**：`receive()` 全仓只有定义处（`orchestrator.py:356`），**无消费者**。

---

## 九、失败处理与状态

### 9.1 降级：按健康度换 profile，不是重试

`agents/fallback.py`：失败窗口 300s，**连续失败 3 次**触发降级（`:21-22`）；降级目标是 `profile.fallback_profile_id`（21 个预设全指向 `"default"`）。`_try_fallback_or`（`orchestrator.py:1428-1454`）用**同样的 message 重新 `_dispatch` 一次**。

### 9.2 超时双层，默认都关闭

| 项 | 来源 | 默认 |
| --- | --- | --- |
| `idle_timeout` | `settings.progress_timeout_seconds` | **0 = 禁用** |
| `hard_timeout` | `settings.hard_timeout_seconds`（可被 profile 覆盖） | **0 = 禁用** |

轮询周期 3s，进度指纹 `(iteration, status, tools_count)`。

### 9.3 取消：AbortScope 三层树

`core/abort_scope.py` 文件头点名说旧方案的问题正是「`orchestrator.delegate` 把父 cancel 传给 sub-agent 时是另写一套」。

```
父 TaskState.abort_root.abort()          core/agent_state.py:350
  └→ fanout 到 children                   abort_scope.py:96
      └→ 子 scope.event.set()
          ├─ ① 工具 scope: "tool:delegate_to_agent"    _tool_runtime.py:544
          └─ ② 子 Agent 的 TaskState.abort_root 挂到工具 scope 下
                _reasoning_runtime.py:1442
```

ContextVar `current_abort_scope` 传递。另有独立路径 `orchestrator.cancel_request(session_id)` 直接 `task.cancel()` 硬取消，并立即 `purge_session_states`（不等那 120 秒延迟清理）。

### 9.4 状态存储三层

| 层 | 位置 | 生命周期 |
| --- | --- | --- |
| 内存 dict | `orchestrator._sub_agent_states`（`orchestrator.py:418`） | 进程内，权威实时 |
| 磁盘快照 | `data/sub_agent_states.json` | 跨重启，原子写 |
| 会话记录 | `session.context.sub_agent_records` | 随会话序列化，上限 50 |

重启时残留的 `running`/`starting` **一律改写为 `interrupted`**（`orchestrator.py:1420-1422`）。

### 9.5 可观测性

- **文件日志**：`_log_delegation` 写 `data/delegation_logs/YYYYMMDD.jsonl`，事件 `dispatch_start` / `dispatch_ok` / `dispatch_timeout` / `progress`
- **健康指标**：`AgentHealth` 累加 total_requests / successful / avg_latency
- **Tracing**：`tracer.py:400-409` 的 `delegation_span()` **全仓无调用方**——定义了正规 Span 上下文管理器却未接线

---

## 十、技术债清单（移植时不要继承的）

| # | 问题 | 证据 |
| --- | --- | --- |
| 1 | **死参数**：`model` / `run_in_background` / `fork` 在 schema 不在 handler | `definitions/agent.py:75,80` vs `handlers/agent.py:87-90` |
| 2 | **双实例池**：`_desktop_pool` 与 `orchestrator._pool` key 格式相同但 dict 不同，同 `(session, profile)` 拿到两个 Agent，各带独立 FileTool / memory_manager | `main.py:428` vs `orchestrator.py:452` |
| 3 | **模块单例耦合**：状态读写依赖 `openakita.main._orchestrator` 全局变量，注释已自陈此坑 | `api/routes/agents.py:1215-1225` |
| 4 | **`backends.py` 完全死代码**：176 行零引用，docstring 承诺的 `SubprocessBackend` 不存在 | 全仓 0 引用 |
| 5 | **`task_queue.py` 挂着不跑**：`start()` 从不调用，优先级 / 父任务门禁 / lease 全是死机制 | `orchestrator.py:1661-1663` 注释自陈 |
| 6 | **mailbox 断链**：`send_agent_message` 写入队列无消费者 | `receive()` 全仓仅定义处 `orchestrator.py:356` |
| 7 | **`task_stop` 可能恒失效**：遍历的 `_agent_pool` / `_background_tasks` 属性名全仓找不到写入点 | `handlers/agent.py:687` |
| 8 | **6 个幽灵组织工具名**残留在审批表 / 白名单 / 提示词 / 配置守卫 | 见 §二 |
| 9 | **死枚举**：`SUB_AGENT_STATE`、`HookType.SUB_AGENT_START/STOP` 只有定义无发射方 | `events.py:70`、`hooks.py:38-39` |
| 10 | **状态机只告警不阻断**：`_VALID_TRANSITIONS` 非法跃迁照常写入 | `orchestrator.py:996-1002` |
| 11 | **prompt 预算失控**：动态名册在 `apply_budget` 之外拼接，用户自建 Agent 无上限 | `_agent_runtime.py:3291` |
| 12 | **双深度闸门不统一**：`Organization.max_delegation_depth`(5) vs 运行时 `MAX_DISPATCH_DEPTH`(6)，前者是否被消费未找到 | `org_models.py:432` |
| 13 | **orgs 能力寄生**：拿不到主 Agent 的 brain 就 `BuilderUnavailable`；拿不到 handler_registry 退化成空全局 registry（v17 审计记录 12/12 工具全失败） | `_default_agent_builder.py:1717`、`_runtime_agent_host.py:88-95` |
| 14 | **orgs 单进程假设**：`node_scheduler.py:118-123` docstring 明说「JSON backends are single-process」 | — |
| 15 | **ephemeral 目录泄漏**：`remove_ephemeral()` 只删内存 dict 不删目录 | `profile.py:739-747` |
| 16 | **分身不继承隔离意图**：`derive()` 原样复制父 profile 的 `identity_mode` / `memory_mode` | `profile.py:373, 386-399` |
| 17 | **profile 无 `model` 字段**：21 个预设 Agent 全跑同一模型 | `profile.py` 全文 |
| 18 | **命名撞车**：`agents/`（个人多 Agent 包）与 `orgs/`（组织树包）语义相近，易混淆 | — |

---

## 十一、给 nanobot 的移植建议

### 11.1 最小可运行切片（若要补齐基础能力）

| # | 能力 | openakita 参照 | nanobot 现状 |
| --- | --- | --- | --- |
| 1 | **防递归** | `_is_sub_agent_call` 标志 + 工具剥离 + handler 兜底 | **无** —— 最高优先级 |
| 2 | **深度熔断** | `orchestrator.py:77` + `_dispatch` 闸门 | 无 |
| 3 | **上下文隔离** | `[任务背景]/[任务指令]/[委派原因]` 拼装，不传父历史 | 部分（传父历史） |
| 4 | **回执结构化** | 状态头 + 哨兵块 + 并行合并保护 | 模板固定文本 |
| 5 | **防注入包裹** | `wrap_external_content` 带 nonce | 无 |
| 6 | **状态机 + 广播** | 8 态枚举 + WS 推送 + 磁盘快照 + 重启改写 interrupted | 内存 dict only |
| 7 | **取消传导** | AbortScope 三层树 | `task.cancel()` 逐个 |

**HTTP 面最小集**：`/sub-tasks`（轮询状态）+ `/topology`（图）即可，`collaboration` 与 `/sub-records` 可后置。

### 11.2 建议直接借鉴的设计

1. **哨兵块 + 并行合并保护**（§5.2）——解决「子 Agent 产物在长文本拼接中被截断丢失」这个真实痛点
2. **ContextVar 收集 → 统一调度**（§7.2）——如果要支持 DAG 式多子 Agent，这是最干净的解法
3. **协作原则提示词**（§6.2）——尤其是「像给刚进房间的聪明同事做简报」和「永远不要委派理解」两条
4. **`examples` 字段带反例**（§8.4）——比纯 description 有效
5. **首选常驻 / 备用按需**（§8.1）——控制 prompt 体积
6. **降级而非重试**（§9.1）——换 profile 重跑同一任务
7. **数值幻觉守卫**（§5.4）——结合 nanobot 记忆系统尤其相关（避免编造记忆内容）

### 11.3 建议避免的

1. **不要照抄 44 字段 dataclass 定义形态**——nanobot 已在 `nanobot/agents/` 走了 store + JSON 路线（`catalog.py` / `models.py` / `store.py`），且 WebUI 有 agents 管理界面，路线更合理
2. **不要引入「预留而不接线」的设计**——openakita 的 18 项技术债里至少一半是这个模式造成的
3. **不要让 schema 和 handler 脱节**——openakita 的三个死参数是活生生的教训
4. **不要在 system prompt 之外拼装名册**——openakita 的名册绕过预算裁剪，用户自建 Agent 一多就是 prompt 炸弹

---

## 附录 A：关键文件清单

### openakita

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `src/openakita/agents/orchestrator.py` | 1929 | 委派核心：delegate / _dispatch / _call_agent / 状态机 / 降级 |
| `src/openakita/agents/profile.py` | 862 | `AgentProfile`(44 字段) / `ProfileStore` / `derive()` |
| `src/openakita/agents/factory.py` | 939 | `AgentFactory.create()` / `AgentInstancePool` |
| `src/openakita/agents/presets.py` | 686 | 21 个内置 `SYSTEM_PRESETS` |
| `src/openakita/agents/fallback.py` | 142 | 健康度降级 |
| `src/openakita/agents/task_queue.py` | 300 | 优先级队列（**未接入**） |
| `src/openakita/agents/lock_manager.py` | 144 | 资源锁（**仅 browser 用**） |
| `src/openakita/agents/backends.py` | 176 | **死代码，零引用** |
| `src/openakita/agents/coordinator_prompt.py` | 106 | org 协调者提示词 |
| `src/openakita/tools/definitions/agent.py` | 434 | 6 个工具 schema + description + examples |
| `src/openakita/tools/handlers/agent.py` | 753 | 派发入口 / 子 Agent 拦截 / 上下文拼装 |
| `src/openakita/prompt/builder.py` | — | 协作原则 / 架构概况 / 段落顺序 / 预算 |
| `src/openakita/core/abort_scope.py` | — | 取消树 |
| `src/openakita/orgs/runtime.py` | 2253 | 组织运行时 |
| `src/openakita/orgs/_runtime_delegation.py` | — | ⭐ ContextVar 收集 + DAG |
| `src/openakita/orgs/_default_agent_builder.py` | 82KB | `_BrainBackedNodeAgent` / `_maybe_dispatch` |
| `src/openakita/orgs/_runtime_agent_pipeline_executor.py` | 68KB | 真正的节点执行器 |
| `src/openakita/orgs/blackboard.py` | 699 | 三层共享黑板（只写不读） |

### nanobot 对照

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `nanobot/agent/tools/spawn.py` | 107 | `spawn` 工具 |
| `nanobot/agent/subagent.py` | 594 | `SubagentManager` |
| `nanobot/templates/agent/subagent_system.md` | ~15 | 子 Agent prompt |
| `nanobot/templates/agent/subagent_announce.md` | ~10 | 结果回传模板 |
| `nanobot/agents/{catalog,models,store}.py` | — | Agent 定义（2026-09-30 拉取新增） |

---

## 附录 B：调研方法与未覆盖项

**5 路并行只读探查**：调度器 / 定义构建 / 工具与提示词 / 运行时接线 / 组织树。

**未覆盖（可能需要后续单独调研）**：
- `runtime/supervisor.py` + `supervisor_factory.py` —— 命令级生命周期层，是唯一有 SQLite checkpointer 持久化的一层，与 executor 的职责边界未查清
- `orgs/_runtime_templates.py`（82KB）—— 内置组织模板，只看了类名概览
- `orgs/command_service.py`（3657 行）—— 只读了 `submit()` 入口
- nanobot 侧 `nanobot/agents/`（2026-09-30 刚拉取的 Agent Store）的实现细节未展开

**已知未确证项**：
- `_active_tasks` 的填充点（`cancel_request` 依赖它，写入方未定位）
- `task_stop` 工具与 `cancel_request` 的衔接路径
- `Organization.max_delegation_depth` 是否被消费
