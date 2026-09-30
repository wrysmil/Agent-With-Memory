---
artifact: spec
route: superpowers:brainstorming
skills:
  - brainstorming
skills_evidence:
  - ~/.claude/skills/brainstorming/SKILL.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
created_at: 2026-09-29
status: draft
approved: false
revision: 2
revision_note: >
  经两轮独立审查（提取侧 / 注入侧）后重写。v1 的 §1.2（四字段渲染）、§1.3（merge 修复）、
  §六 prompt 缓存论据均被证伪或不成立，已按源码证据修正。逐条处置见 §七。
---

# 工作记忆（Scratchpad）固定注入 system prompt

- 日期：2026-09-29
- 状态：spec v2，方向已由用户确认（三轮 AskUserQuestion）→ 待用户 review 后进 writing-plans
- 路由：`「Harness：brainstorming」`
- 范围：`nanobot/agent/context.py`、`nanobot/agent/loop.py`、`nanobot/memory/scratchpad_writer.py`、`nanobot/agent/memory.py`（压缩估算）+ 测试
- 关联：`.ai-runtime-artifacts/plans/2026-09-15-session-end-idle-extraction-plan.md`（决策 #6 工作记忆 UI 全部移除）、openakita `src/openakita/prompt/builder.py:1963-1976`

---

## 〇、背景

### 0.1 用户的诉求

「之前不是把工作记忆去除了吗……现在得加回来了，工作记忆这个还是要的。」

范围已确认：**不需要页面，只要后端链路**；注入方式选**固定注入一段**；注入**只做 2 个字段**（详见 §0.5）；**轮转去重纳入本轮**。

### 0.2 「去除了」到底去除了什么

commit `7876d0d`（2026-09-15，WU-B/Task 5）标题即 `feat(memory): remove working memory UI surface (scratchpad tab)`。删的是**纯前端**：`ScratchpadEditor.tsx`（-207）、`MemorySection.tsx` 第三个 tab、10 个语言包的 8 个 i18n key，合计 -295 行，**零后端文件删除**。

原决策出自 `.ai-runtime-artifacts/plans/2026-09-15-session-end-idle-extraction-plan.md:43`：

> 工作记忆 UI：**全部移除**：删设置 tab、不加 chat header 图标、不加回复尾巴
>
> 目标表述：让工作记忆**仅以「AI 在对话里自然引用」的方式被用户感知**。

**这个决策的前提不成立。** 召回引擎 [engine.py:200-205](../../nanobot/memory/retrieval/engine.py#L200-L205) 只有 semantic / episodes / recent / attachments 四路，`git log -S"search_scratchpad"` 零记录——scratchpad **只写不读**。agent 从没读到过自己写的工作记忆，「自然引用」从未发生。所以这不是「把 UI 加回来」，是**补上一条从未存在的读取链路**。

### 0.3 openakita 对照（源码已逐条核对）

openakita 根在 `D:\studyspace\源码学习\openakita\openakita\`。它有**同名同结构**的 `Scratchpad`（`src/openakita/memory/types.py:515-525`），字段与 nanobot 的 `ScratchpadEntry`（`nanobot/memory/models.py:207-215`）逐一对齐——nanobot 的表结构派生自它。

openakita **已经注入**，nanobot 漏了这一步。读取函数十几行，可直接照抄：

```python
# src/openakita/prompt/builder.py:1963-1976
def _build_scratchpad_section(memory_manager: Optional["MemoryManager"]) -> str:
    """从 UnifiedStore 读取 Scratchpad，注入当前任务 + 近期完成"""
    store = getattr(memory_manager, "store", None)
    if store is None:
        return ""
    try:
        pad = store.get_scratchpad()
        if pad:
            md = pad.to_markdown()
            if md:
                return md
    except Exception:
        pass
    return ""
```

```python
# src/openakita/memory/types.py:552-559
    def to_markdown(self) -> str:
        """Render scratchpad as markdown for system prompt injection."""
        lines: list[str] = []
        if self.current_focus:
            lines.append(f"## 当前任务\n{self.current_focus}")
        if self.active_projects:
            lines.append("## 近期完成\n" + "\n".join(f"- {p}" for p in self.active_projects[:5]))
        return "\n\n".join(lines)
```

极简、纯读、**不相关性打分、不排序、不查 embedding**，每轮无条件读一次。本 spec 照此形态落地。

注入位置 `builder.py:1788-1830` 的 `_build_memory_section`：Scratchpad 是 **Memory 层 Layer 1**，紧随「记忆系统自描述」之后，**早于 Core Memory（MEMORY.md）、早于 Pinned Rules、早于相关性召回**，且在 `if pinned_only: return` **之前**——最省记忆模式下工作记忆依然注入，是刻意设计。

openakita **也没有任何 scratchpad 用户编辑入口**（`api/` `cli/` `commands/` 全仓零命中），与「不要页面」的要求天然一致。

### 0.4 与 openakita 的关键差异：openakita 只渲染 2 字段不是疏漏

v1 spec 曾判断「openakita 只用 `current_focus` + `active_projects`，另两个字段建了不用，是它的疏漏」。**这个判断是错的。** 查 openakita 的写入侧：它有 `_parse_list_section` / `_parse_first_item`（`memory/extractor.py:763-772`）把 LLM 产出的四段 Markdown **解析回结构化字段**。所以 openakita 的两个字段就是它有数据支撑的全部。

nanobot 这半截**没有**（详见 §0.5）。

### 0.5 字段可用性：只有 2 个字段真有数据

全仓追踪 `open_questions` / `next_steps` / `content` 的**写入点**，只有四处，无一在生产路径生效：

| 写入点 | 状态 |
| --- | --- |
| `scratchpad_writer.py:148-149` `update_focus` | 硬编码写 `content=""` / `open_questions=[]` / `next_steps=[]` |
| `scratchpad_writer.py:201` `archive_completed` | 按精确串过滤旧值，**生产零调用** |
| `memory_routes.py:189` / `memory_api.py:697` | WebUI 手填，UI 已按决策 #6 移除 |
| `format_with_llm` | **双重失效，见下** |

`format_with_llm` 这条 LLM 路径断在两处：

1. **开关默认关**：`memory_extraction.py:265-267` 用 `getattr(type(self), "S4_SCRATCHPAD_REFORMAT_ENABLED", False)` 弱引用取值，该类属性**未定义**，全仓无任何地方打开 → Step 3 分支永不执行（`orchestrator.py:98-101`）
2. **即使打开也断链**：`orchestrator.py:103-107` 调用 `await format_with_llm(None, ep_summary)` 后**丢弃返回值**。函数 docstring 明写「调用方负责写库」，而调用方没写。`format_with_llm` 返回 entry 而非自行持久化 → 整条链路从未落库
3. **即便落库也只写 `content`**：`_build_scratchpad`（`scratchpad_writer.py:288`）只设 `existing.content = text`，从不回填 `open_questions` / `next_steps`

**结论：生产环境中工作记忆实际只有 `current_focus` + `active_projects` 有数据**，且这两个由 T0 路径每轮无条件写入（`memory_extraction.py:436-445` → `scratchpad_writer.py:99-151`），不依赖任何 LLM、任何开关。

**用户已决策：本轮只注入这 2 个字段。** 与 openakita 实际行为一致，零额外 LLM 成本，立即可用。打通 S4 使其具备 4 字段（`format_with_llm` 回写库 + 增加 section 解析 + 开关置 True，3 处改动 + 每会话一次额外 LLM 调用）记为独立后续项，见 §八。

> 顺带修正 v1 的一处错误归因：v1 §1.3 主张「`update_focus` 整行覆盖会抹掉 LLM 抽取的 `open_questions` / `next_steps`」并作为必修项。**该覆盖确实存在（`scratchpad_writer.py:148-149` 硬编码空），但由于 LLM 路径从未落库，被抹掉的值恒为空**——merge 修复无害，但也修不了任何东西。`archive_completed:198` 同理。本轮不做。

### 0.6 提取侧另两个独立缺陷（本轮修复）

**D1. `MemoryExtractor` 缺 `user_id` 装配 → 喂给 LLM 的工作记忆快照恒空。**
`MemoryExtractor.__init__` 有 `user_id: str = "default"`（`extractor.py:705-715`），但装配处 `loop.py:536-541` **不传**，落回 `"default"`；而写入侧用 `ScratchpadWriter.user_id_for_key(session_key)` 从 `channel:chat_id` 派生出 chat_id（`scratchpad_writer.py:63-74`）。`extractor.py:1123` 的 `get_scratchpad(conn, "default", ...)` **永远查不到 `update_focus` 写的行** → 抽取时永远看不到已有工作记忆，`_render_scratchpad` 恒返回 `"{}"`（`extractor.py:620-621`）。

`scratchpad` 表主键是 **(user_id, workspace_id)** 二元组（`database.py:95-105`），两边不一致就是查的不是同一行。

已核实无歧义的部分：`_MEMORY_WORKSPACE_ID = "default"`（`loop.py:134`）→ `MemoryServices.for_workspace` 原样存（`memory_services.py:36`）→ `ScratchpadWriter(workspace_id=services.workspace_id)`（`loop.py:533`）→ 三者一致，**workspace 侧安全**，读取侧照此取值即可。

**D2. T0 轮转不去重 + CHAT 跳过门形同虚设 → `进行中` 会被同一话题的碎片占满。**
`update_focus`（`scratchpad_writer.py:134-141`）只比较 `old_focus != new_focus`，**不与 `existing_projects` 去重**，不匹配就 `insert(0, ...)` 再 `[:5]`。

原以为 `_apply_immediate_focus` 的 `classify_intent(message) == IntentType.CHAT` 跳过能兜底（`memory_extraction.py:441`），**不能**：
- `_TASK_VERBS`（`intent.py:53-65`）含「帮我/看看/那」类词，「那再帮我看看 B」判 TASK
- `_FOLLOW_UP_CJK`（`intent.py:73-76`）以「那」开头，「那 C 呢」判 FOLLOW_UP
- 两者都 ≠ CHAT，**默认兜底还是 TASK**（`intent.py:146`）

失败场景：用户围绕同一件事连聊 5 轮，每轮 user 消息都不同 → 每轮触发一次轮转 → 5 个槽位在 5 轮内全被「那 C 呢」这类无信息量碎片占满，真实历史焦点被挤掉，`## 当前任务` 也显示成「那 C 呢」。**这直接决定注入内容质量，必须本轮修。**

### 0.7 本轮排除的方向

| 方向 | 排除理由 |
| --- | --- |
| 恢复 WebUI scratchpad tab | 用户明确不要。openakita 亦无。 |
| 作为第 5 路召回通道按需注入 | 工作记忆语义是「当前在做什么」，通常**就是**当前 query 的主题，过相关性筛选会经常命中不全。它是最该常驻而非按需召回的一类信息。 |
| 打通 S4 拿 4 个字段 | 用户已决策本轮只做 2 字段。记为 §八 后续项。 |
| 补 openakita `working_facts`（会话内短事实层，`builder.py:585-595`） | 独立需求，会牵动 `AgentRunner` 轮次状态，混进来会让工作记忆这条链路迟迟无法验证。 |

---

## 一、设计

### 1.1 注入位置与形态

`ContextBuilder.build_system_prompt`（[context.py:207-273](../../nanobot/agent/context.py#L207-L273)）当前 parts 顺序：

```
identity → bootstrap(AGENTS/SOUL/AGENT/USER) → policies → tool_contract
        → [Current Project] → Memory(MEMORY.md) → Active Skills
        → skills_summary → [Archived Context Summary] → retrieved_memory_section(Layer 4 召回)
```

工作记忆插在 **`Memory`（MEMORY.md 长期记忆）之前**，即追加点 `[Current Project]` 之后、`include_memory` 块之前。

**理由**：对齐 openakita 的 Layer 1 序（Scratchpad 早于 Core Memory、早于召回）。工作记忆回答「我现在在做什么」，长期记忆回答「我平时知道什么」，前者是后者的语境，顺序反了会让 agent 先被历史事实淹没。

**明确不用「保住 prompt 缓存」当论据**（v1 曾误用，已修正）：`anthropic_provider.py:548-549` 把整个 system prompt 作为**单个 text block、缓存断点挂在末尾**，任一字节变化即整体失效，因此注入位置的缓存影响**恒为零**。且召回块本就每轮变化（`context.py:267-271`），**system 缓存现在就已经每轮失效**——本改动既不改善也不恶化。

命名用独立块 `# Working Memory`，与 `# Memory` / `## Long-term Memory` 并列。子段 `##` 标注「短时、跨会话、可过时」，让 agent 不会把它当长期事实去固化。

新增参数 `working_memory_section: str | None = None`，与既有 `retrieved_memory_section` 同风格：调用方预计算，builder 保持同步、无 IO 依赖。**空串或 None 一律不 append**，不产生空块。

### 1.2 渲染格式

新函数 `render_working_memory_markdown(entry: ScratchpadEntry) -> str`，放 `nanobot/memory/scratchpad_writer.py`（与写入器同文件，便于对照）。只渲染两个字段：

```markdown
# Working Memory

以下是你自己维护的短时工作状态，跨会话保留，可能已经过时；以当前对话为准。

## 当前任务
{current_focus}

## 进行中
- [MM-DD HH:MM] {active_projects[i]}
```

- `active_projects` 直接用 `ScratchpadWriter._format_project_entry` 已产出的 `[MM-DD HH:MM] ` 前缀（`scratchpad_writer.py:93`），**时间信息随内容走**，无需另加日期字段
- 字段为空则不输出该子段；两者皆空则返回 `""`，不注入
- 段间用 `"\n\n"`，与 openakita `to_markdown` 一致

### 1.3 读取与门控

`_build_turn` 中新增 `_compute_working_memory_section(session_key)`，形态完全照 `_compute_retrieval_section`（[loop.py:2179-2200](../../nanobot/agent/loop.py#L2179-L2200)）：**全 try/except 包裹，异常只 `logger.warning` 并返回 `""`，绝不阻断 BUILD**。

**门控全部在 loop 侧求值，只把最终字符串传给 builder**。理由：`ContextBuilder._should_retrieve`（`context.py:147`）读 `self._memory_enabled_provider()` 时**没有 try/except**，而写侧 `_memory_disabled()`（`memory_extraction.py:283-287`）在 provider 抛异常时按 enabled 继续。两侧异常语义相反，门控若留在 builder 内，provider 异常会击穿 BUILD。

门控条件：

| 门控 | 来源 | 备注 |
| --- | --- | --- |
| 记忆系统总开关开 | `AgentLoop._memory_enabled_provider` | 与写侧同源（`gateway_runtime.py:514-515` 同一闭包），不会出现「写停读开」 |
| 记忆抽取已装配 | `memory_extraction_enabled`（`loop.py:481`） | **不能用 `memory_services is not None`**——`loop.py:522` 抽取照样装配；反例：传了 services 但 `memory_extraction_enabled=False` → 无写入却持续注入陈旧焦点 |
| 该 turn 非 subagent / 非 dream | 见 §1.4 | |
| 渲染结果非空 | — | |

### 1.4 调用方：三个非主对话场景

`build_system_prompt` 的入口只有两个：`AgentLoop._build_turn`（`loop.py:2162-2172`）与 `Consolidator/MemoryArchiver` 经 `build_messages`（`loop.py:467` → `memory.py:997,1150`）。后者不传参，天然不注入。

但 `_build_turn` **不分 turn kind**，三类都被波及：

| 场景 | 机制 | 决策 |
| --- | --- | --- |
| **subagent** | `subagent.py:522` `override = origin.get("session_key") or f"{channel}:{chat_id}"` — **子代理复用父 session_key**，必然拿到父的「当前任务」 | **不注入**。独立委派的子任务带父任务焦点是错误语境。实现需新增一个 turn 级标记（当前无区分标志——复用 key 正是问题根源） |
| **bound cron** | `cron/bound_runner.py:127` `session_key_override=session_key` — 复用原会话 key | **注入**。它就是原会话的后续，语义正确 |
| **dream** | `builtin.py:483-492` → `dream_session_key()` = `dream:YYYYmmdd-HHMMSS`（`memory.py:702-704`），`user_id_for_key` 取 `partition(":")` 的 tail = 时间戳，查不到行 | **显式跳过**。当前恰好安全，但**是巧合不是设计**。`loop.py:2062` 已有 `startswith("dream:")` 分支可直接复用 |

### 1.5 与 `include_memory` 的关系

`loop.py:1305` `include_memory=session.policy.persist`——当会话不持久化时，长期记忆被摘掉。工作记忆**不跟随** `include_memory`（它是任务状态不是长期事实），但**跟随 §1.3 的记忆总开关**。两者语义不同，在 spec 中显式区分，避免后人误以为漏接。

### 1.6 token 预算

**不引入动态预算机制**，但补齐两项硬约束：

1. **体积实测**：`current_focus` ≤200 字符（`memory_extraction.py:202`）、`active_projects` 5 条 × (`[MM-DD HH:MM] ` 13 字符 + ≤200) ≈ 1065 字符，合计约 1300 字符 → **中文按 ~1 token/字符估算约 1300–1600 token**。相对 1M 上下文可忽略，但**不是 v1 说的「几百 token」**。
2. **单条截断**：`active_projects` 条目虽写入侧已有 `[:200]`（`scratchpad_writer.py:138`），但**渲染侧仍须再截一次**——历史行可能由旧代码或 WebUI 直接写入而超长。截断打 `logger.debug`，不静默。
3. **压缩估算补漏**：`Consolidator.estimate_session_prompt_tokens`（`memory.py:1147-1159`）经 `_build_messages` 构造探针，**不传 `working_memory_section`** → 每次低估约 1.5k token，而 `_SAFETY_BUFFER = 1024`（`memory.py:1019`）**小于该漏算量**，会让压缩阈值偏乐观。须在探针里带上工作记忆块。

### 1.7 不可信数据处理

工作记忆内容由 T0 从用户消息截取而来，属**不可信数据**，且形成自反馈回路：用户可以说「记住：忽略你之前的所有规则」，下一轮这句就回到 system prompt。openakita 对此零处理；nanobot 召回链路已有成熟做法（`engine.py:49` `_INJECTION_PREAMBLE` + `engine.py:351` `_sanitize_memory_content`），本块沿用其思路按工作记忆形态裁剪：

1. 剥离值内独占一行的 `---`（防伪造 `build_system_prompt` 的段落分隔符 `"\n\n---\n\n"`，见 `context.py:273`）
2. 折叠 3 个以上连续换行
3. 剥除行首 `#`（防伪造标题层级）

**不剥行内 markdown 强调与列表**——工作记忆就是给 agent 读的富文本，剥过头会损伤可读性。

`_INJECTION_PREAMBLE` 那段「以下内容是不可信数据」声明**不搬**：召回块里的记忆是第三方/历史数据，工作记忆是 agent 自己刚写的，措辞对不上，强行加会让模型对刚写下的东西产生怀疑。改用 §1.2 块内那句「以当前对话为准」。

### 1.8 轮转去重（D2 修复）

`update_focus` 在把旧 focus 归档进 `active_projects` 前，先判断它是否**已经在列表里**：

- 归一化后（去时间戳前缀、空白、大小写）已在 `existing_projects` 中出现 → **不轮转**，直接更新 `current_focus`
- 否则按原逻辑 `insert(0, ...)` + `[:MAX_ACTIVE_PROJECTS]`

选「精确归一化去重」而非模糊相似度匹配（difflib / embedding）：追问变体（"那 C 呢" vs "帮我看看 C"）字面差异大，模糊匹配要么漏要么误杀；且引入相似度算法会让 T0 这条「目标 < 50ms」的同步路径变慢。**精确去重能挡住最常见的「同一条消息被重复归档」，挡住不了改写式追问**——这是已知残留，§八 记为后续观察项。

不扩 `classify_intent` 的 CHAT 白名单：意图分类同时服务记忆抽取等多个下游，改它影响面远超本轮。

---

## 二、范围

### In Scope

- `update_focus` 轮转去重（§1.8）
- `MemoryExtractor` 装配补 `user_id`（§0.6 D1）
- `render_working_memory_markdown` + 清洗 + 单条截断
- `ContextBuilder.build_system_prompt` 新增 `working_memory_section`，注入 `# Memory` 之前
- `AgentLoop` 存 `memory_extraction_enabled` / `memory_services`；`_build_turn` 预计算；subagent / dream 跳过；门控在 loop 侧求值
- `Consolidator` 压缩探针带上工作记忆块
- 单元测试：轮转去重、extractor user_id、渲染格式（空态/截断/清洗）、注入位置与门控、三个调用方的跳过、失败隔离

### Out of Scope

- WebUI 页面 / API 增删（**保持现状**：`memory_api.py:228 scratchpad_payload` 与 `:678 save_scratchpad` 已在，只是不挂 UI）
- 打通 S4 拿 4 字段（§八）
- `update_focus` / `archive_completed` 的 merge 语义（§0.5 末已说明：本轮修了也无数据可保）
- 第 5 路召回通道、openakita `working_facts` 层
- 压缩时把工作记忆并入 session summary——**不做的理由**：压缩产出 `[Archived Context Summary]`（`context.py:256-261`）说的是「已发生什么」，工作记忆说的是「现在做什么」，两者不冲突；且工作记忆每轮照常注入，覆盖率不打折。dream / cron 属 `_is_internal_session`（`autocompact.py:64-65`）不压缩不产摘要，二者不会同现于一个 prompt。
- 扩 `classify_intent` 的 CHAT 白名单（§1.8）

---

## 三、Work Unit 拆分

| WU | 内容 | 依赖 | 验证 |
| --- | --- | --- | --- |
| **WU-A** | `update_focus` 轮转去重 | — | `tests/memory/test_scratchpad_writer.py` |
| **WU-B** | `MemoryExtractor` 补 `user_id` 装配（D1） | — | `tests/memory/test_loop_wiring.py` + extractor 快照非空 |
| **WU-C** | 渲染器 + 清洗 + 截断 | — | `tests/memory/test_scratchpad_render.py`（新） |
| **WU-D** | 注入链路：builder 参数/位置、loop 预计算、门控、subagent/dream 跳过、压缩探针 | C | `tests/agent/test_context_builder.py`、`tests/agent/test_loop_working_memory.py`（新） |
| **WU-E** | 全量回归 + 手测 | A,B,D | pytest + basedpyright + 手工对话 |

A / B / C 互不依赖，可并行。

---

## 四、关键代码位置索引

| 位置 | 作用 |
| --- | --- |
| `nanobot/agent/context.py:207` `build_system_prompt` | 加参数 + 注入点（`:237 if include_memory:` 之前） |
| `nanobot/agent/context.py:147` `_should_retrieve` | **无 try/except 的门控反例**，故门控移到 loop 侧 |
| `nanobot/agent/context.py:273` `"\n\n---\n\n".join(parts)` | 段落分隔符，清洗要防伪造 |
| `nanobot/agent/loop.py:134` `_MEMORY_WORKSPACE_ID` | workspace 一致性锚点（= `"default"`） |
| `nanobot/agent/loop.py:320` 构造参数 / `:481` 装配 / `:522-533` services | 存 `self._memory_extraction_enabled` / `self._memory_services` |
| `nanobot/agent/loop.py:536-541` `_build_extractor` | **D1 修复点：补 `user_id`** |
| `nanobot/agent/loop.py:1305` `include_memory=session.policy.persist` | §1.5 语义区分 |
| `nanobot/agent/loop.py:2062` `startswith("dream:")` | dream 跳过，复用此分支 |
| `nanobot/agent/loop.py:2167` / `:2179-2200` | 预计算调用点 / 失败隔离照抄模板 |
| `nanobot/memory/scratchpad_writer.py:63-74` `user_id_for_key` | 读写的 user_id 派生唯一真相源 |
| `nanobot/memory/scratchpad_writer.py:93` `_format_project_entry` | `[MM-DD HH:MM] ` 前缀，渲染直接复用 |
| `nanobot/memory/scratchpad_writer.py:99-151` `update_focus` | **轮转去重修复点**（`:134-141`） |
| `nanobot/memory/repository.py:447` `get_scratchpad` | 读取，无需新写 |
| `nanobot/agent/hooks/memory_extraction.py:436-445` | T0 每轮写入，已存在 |
| `nanobot/memory/intent.py:146` | 默认兜底 TASK，CHAT 门失效的根因 |
| `nanobot/agent/memory.py:1147-1159` / `:1019` | 压缩探针补漏 / `_SAFETY_BUFFER` |
| openakita `src/openakita/prompt/builder.py:1963-1976` | `_build_scratchpad_section` 照抄源 |
| openakita `src/openakita/memory/types.py:552-559` | `to_markdown` 照抄源 |

---

## 五、验收口径

1. `pytest tests/memory/test_scratchpad_writer.py tests/memory/test_scratchpad_render.py tests/agent/test_context_builder.py tests/agent/test_loop_working_memory.py` 全绿
2. `pytest tests/memory/ tests/agent/` 无回归
3. `basedpyright` 无新增错误
4. **手测（不可省）**：起 gateway，跨 3 个话题对话后检查 system prompt：
   - 注入块出现在 `# Memory`（MEMORY.md）**之前**
   - `## 当前任务` 随话题更新，`## 进行中` 有带时间戳的历史条目
   - **围绕同一件事连问 5 轮**（如「那再帮我看看 B」「那 C 呢」），`## 进行中` **不新增碎片**（验证 §1.8 去重）
   - 记忆总开关关掉 → 注入块消失
   - 派发一个 subagent → 它的 system prompt **无**工作记忆块
   - 触发 `/dream` → 固化会话 **无**工作记忆块
5. 记录工作记忆注入块的实际 token 数（`/cost` 或 provider 侧计数），与 §1.6 的 1300–1600 估算对照

---

## 六、风险与权衡

| 风险 | 缓解 |
| --- | --- |
| 自反馈回路：截取自用户消息的内容下轮回灌 system prompt | §1.7 三步清洗 + 独立窗口测试 |
| 工作记忆是**跨会话**单行，可能注入几周前的陈旧任务 | 接受。§1.2 块内声明「可能已过时，以当前对话为准」；`active_projects` 条目自带 `[MM-DD HH:MM]` 时间戳供模型自行判断 |
| 门控挂 `memory_extraction_enabled` 后，若该标志与实际写入不一致，仍会注入陈旧数据 | 真门是「抽取 hook 是否注册」，`loop.py:481` 即该判据。实现时在 `_build_turn` 侧用**注册状态**而非布尔参数二次确认 |
| 每请求固定多付约 1.5k token | 1M 上下文下占比 0.15%，可忽略。若将来上下文窗口变小或引入 prompt 预算，再评估 |
| 精确去重挡不住改写式追问（§1.8 残留） | 记为 §八 观察项，先上线看实际轮转质量再决定是否上模糊匹配 |

---

## 七、v1 → v2 审查处置记录

两轮独立审查（提取侧 / 注入侧）逐条核实，处置如下。**所有「已核实」项均经 Leader 复验源码行号。**

| # | v1 主张 | 审查结论 | 处置 |
| --- | --- | --- | --- |
| 1 | §1.3 merge 修复是必需的（LLM 抽取会写那三个字段） | **不成立**（前提错误）——LLM 路径从未落库，被抹掉的值恒为空 | 移出 In Scope，见 §0.5 末 |
| 2 | §五.4 验收「触发抽取后待解决问题非空」 | **不可达**——S4 双重失效 | 验收项删除；改由 §八 后续项承接 |
| 3 | §1.2 四字段齐上 | **不成立**——只有 2 个字段有数据 | 收敛为 2 字段（§0.5、§1.2） |
| 4 | 注入位置理由含「保住 prompt 缓存」 | **不成立**——system 单块、末位断点，位置无影响；召回块已使 system 每轮失效 | 理由改纯语义，显式写明缓存无关（§1.1） |
| 5 | 「token 几百」 | **低估 3–5 倍**——实测约 1300–1600 | 修正数字 + 补单条截断 + 补压缩探针漏算（§1.6） |
| 6 | §二 Out of Scope 用「CHAT 跳过」当轮转护栏 | **不成立**——默认兜底 TASK，门形同虚设 | 改为本轮必修 §1.8 轮转去重 |
| 7 | 门控 `memory_services is not None` | **不等价**——`loop.py:522` 抽取照样装配 | 改用 `memory_extraction_enabled` + 注册状态（§1.3） |
| 8 | 门控求值位置未指定 | **有风险**——`_should_retrieve` 无 try/except，provider 异常击穿 BUILD | 门控全部移 loop 侧（§1.3） |
| 9 | 未考虑调用方 | **遗漏**——subagent 复用父 key 会拿到父任务焦点；dream 靠巧合安全 | 三个场景逐一定决策（§1.4） |
| 10 | 未考虑 `include_memory=False` | **遗漏**——长期记忆摘掉但工作记忆仍注入 | 显式区分语义（§1.5） |
| 11 | 与 `# Memory` 命名可能混淆 | **风险成立** | 独立块 `# Working Memory` + 短时声明（§1.2） |
| 12 | 压缩与 session summary 可能冲突 | **部分成立**——实际不冲突 | 记入 Out of Scope 并说明理由（§二） |
| 13 | 未发现 | **新增 D1**：`MemoryExtractor` 缺 `user_id`，快照恒空 | 纳入 WU-B（§0.6） |
| 14 | §1.5 三步清洗方向 | **成立** | 保留，微调 §1.7 |

---

## 八、后续项（不在本轮）

| # | 项 | 说明 |
| --- | --- | --- |
| F1 | **打通 S4，恢复 4 字段工作记忆** | 三处改动：`orchestrator.py:103-107` 回写 `format_with_llm` 返回值；`format_with_llm` 增加 section 解析（照 openakita `_parse_list_section` / `_parse_first_item`）；`S4_SCRATCHPAD_REFORMAT_ENABLED` 置 True。代价：每会话一次额外 LLM 调用。**且须先完成 §1.8 去重，否则新字段同样被碎片污染** |
| F2 | 改写式追问的去重 | §1.8 精确去重的残留。先观测实际轮转质量再决定是否上模糊匹配 |
| F3 | openakita `working_facts` 会话内短事实层 | 独立需求，优先级低于 F1 |
| F4 | `include_memory` 与工作记忆的统一语义 | 当前刻意不同（§1.5），若将来产品上想统一需重新评估 |
