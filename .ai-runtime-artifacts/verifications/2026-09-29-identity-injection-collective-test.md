# 集体测试：身份注入（SOUL/文件记忆）+ 工作记忆（scratchpad）

- 日期：2026-09-29
- 分支：`feature/memory-system`
- 运行环境：本地 `nanobot webui --dev`（gateway 18790/8765，vite 5173），WebUI 前端直跑
- 关联提交：`9a3dcc5` 工作记忆注入、`b2496e7` persona 注入 system prompt、`e3c609b` 身份文件目录唯一真相源

## 执行前环境快照

| 项 | 值 |
| --- | --- |
| `identity/active_persona` | `companion` |
| `SOUL.md` 源文件 | `~/.nanobot/workspace/SOUL.md`（旧工作区，尚未迁入 `identity/`） |
| `SOUL.md` 实质内容 | 5 条 Core Principles + 5 条 Execution Rules |
| `runtime/identity.core.md` | **50 字节，仅 2 行** |
| `runtime/user.profile.core.md` | 出厂空模板（USER.md 未填写） |
| `runtime/agent.behavior.md` | 不存在（出厂 AGENT.md 被 `skip_factory_template` 跳过） |

### 预检断言（不消耗 LLM，已在测试前执行）

`runtime/identity.core.md` 对 `SOUL.md` 关键短语的命中数：

| 短语 | 命中 |
| --- | --- |
| `Core Principles` | 0 |
| `Execution Rules` | 0 |
| `Act immediately` | 0 |
| `Solve by doing` | 0 |
| `Treat the user's time` | 0 |

**预检结论：编译产物未包含任何一条 SOUL 规则。** 根因见下方「已确认根因」。

## 已确认根因（先于对话测试）

`nanobot/identity/compiler.py:79-88` 中 `identity_core` 的 `owned_markers` 为：

```
soul, 身份, 使命, 性格, 人格, 核心原则, 执行规则, 价值取向, 气质, overview
```

`_extract_owned_sections`（`compiler.py:137-160`）逐行判定：标题行命中任一 marker 即把 `current_owned` 置真并持续收录，直到下一个标题。

本工作区 `SOUL.md` 的标题是 `## Core Principles` / `## Execution Rules`（英文）：

- `# Soul` 命中 `soul` → `matched_any = True`
- `## Core Principles` 不命中任何 marker（marker 只有中文 `核心原则`）→ `current_owned = False`
- 降级分支（「一个标题都没命中才收全文」）因 `matched_any=True` **不触发**

结果：产物只保留 `# Soul` 标题与其下首行，共 50 字节。system prompt 注入的正是这份产物，故 Soul 规则实际未生效。

---

## 关键前提：两条链路的门控不同（决定测试必须走哪条路径）

| 链路 | 门控 | 默认值 | 谁显式开启 |
| --- | --- | --- | --- |
| 身份注入（SOUL / 文件记忆） | **无门控**，`_get_identity` 在每次 `build_system_prompt` 中调用 | 恒开 | 无需 |
| 工作记忆（scratchpad） | `AgentLoop(memory_extraction_enabled=...)` | `False`（`nanobot/agent/loop.py:323`） | 仅 `nanobot/cli/gateway_runtime.py:535` 传 `True` |

**推论：**

- `nanobot agent`（CLI）路径下工作记忆链路**完全关闭**。任何用 CLI 做的记忆召回测试都是无效的。
- WebUI 路径走 gateway，工作记忆开启。
- 身份注入两条路径都生效，故 T1 的结论对 CLI 与 WebUI **同时成立**。

## 对话测试集

> **T1 可用 CLI 预跑；T2–T7 必须在 WebUI 跑**（T2/T3/T4 依赖身份注入，CLI 同样有效；T5/T6 因工作记忆门控，只能在 WebUI 验证）。

在 WebUI（http://127.0.0.1:5173/）**新建会话**后逐条执行。每条独立，新会话不复用上下文。

### T1 身份注入 · 白盒自述（核心判据）

**输入**

```
不要调用任何工具。只根据你当前 system prompt 里的内容回答：
逐字引用「Soul」章节的全部正文。如果该章节没有正文内容，直接说"没有正文"。
```

**期望（身份注入生效时）**：引用出 5 条 Core Principles + 5 条 Execution Rules。

**实际应观察到的（当前状态）**：仅 `I am nanobot 🐈, a personal AI assistant.` 一句，或回答"没有正文"。

> 说明：本题是判别式测试，不看文采，只看能否复述 system prompt 中确实存在的规则。模型可能凭训练语料编造合理原则，故要求「逐字引用」。

---

### T2 身份注入 · 黑盒行为（多步任务先出方案）

**输入**

```
帮我在项目里做三件事：1) 把 tests 目录下三个测试文件重命名 2) 同步更新 README 里的引用 3) 跑一遍测试确认没坏。现在开始。
```

**期望（Execution Rules 第 2 条生效时）**：先列出计划并停下等确认，不直接动手改文件。

**当前状态预期**：直接开始执行（规则未注入 → 无此约束）。

---

### T3 身份注入 · 黑盒行为（Read before write）

**输入**

```
把 /tmp/definitely_not_exist_9527.txt 的第一行改成 "hello"。
```

**期望（Execution Rules 第 3 条生效时）**：先读/确认文件是否存在，报告不存在，而非直接写入或假设其内容。

**当前状态预期**：可能尝试写入、或假设内容（规则未注入）。

---

### T4 身份注入 · 回复长度约束

**输入**

```
nanobot 的记忆系统里，工作记忆是怎么注入 system prompt 的？
```

**期望（Core Principles 第 2 条「Keep responses short」生效时）**：简短回答，不铺陈长篇。

**备注**：此项判据弱，模型默认倾向已偏简短，仅作辅助观察，不单独定性。

---

### T5 工作记忆 · 同会话多轮召回

> **方法论修正（重要）**：本用例**不能区分**「工作记忆注入」与「对话历史残留」——同会话内模型读自己的历史即可答对，召回成功不代表工作记忆生效。
> 正因如此，**必须配 T5b 交叉验证**：同一条提问在**新会话**里问一次。若新会话也能答对，说明信息来自长期记忆而非工作记忆，本用例应判为**无法区分**而非通过。
> 另：CLI 路径下 `memory_extraction_enabled=False`，本用例只能在 WebUI 跑。

**在同一个会话内依次发送：**

第 1 轮：

```
接下来我要做三件事：1) 梳理记忆检索链路 2) 补齐单元测试 3) 写一份 RCA。现在开始第 1 件。
```

第 2 轮（**不新建会话**）：

```
不要调用任何工具。我刚才说要做的三件事是什么？按顺序说出来。
```

第 3 轮 —— **T5b 交叉验证**（**新建会话**）：

```
不要调用任何工具。有个任务清单是「梳理记忆检索链路 / 补齐单元测试 / 写一份 RCA」。这个清单是我之前交代给你的吗？如果我不说，你从哪里知道的？
```

**期望**：
- 第 2 轮能完整召回三件事（写入 + 注入基本可用）
- 第 3 轮应答「不知道」或「你刚才告诉我的」——若能说出该清单来自**长期记忆**（`memories` 表）而非本轮对话，则说明工作记忆的隔离边界失效，需进一步排查 `user_id_for_key` 的作用域

**判据要点**：
- 失败模式 A：三件事答不全 → 写入侧未落库
- 失败模式 B：全对但需要翻历史才答出 → 注入侧未生效
- 通过条件：**不查工具**即可完整复述
- **T5b 是唯一能证伪「T5 只是读到了对话历史」的用例，不可省略**

**落库自查（零成本，跑完第 1 轮立即执行）**：

```bash
sqlite3 ~/.nanobot/workspace/memory/state.db \
  "SELECT user_id, substr(current_focus,1,200), updated_at
   FROM scratchpad ORDER BY updated_at DESC LIMIT 3;"
```

若查不到本轮的三件事，说明**写入侧就没落库**，后续召回不可能通过。

---

### T6 工作记忆 · 跨会话隔离（负向）

**在 T5 的会话中，第 3 轮：**

```
不要调用任何工具。重新说一下我要做的三件事，先说第一件。
```

**期望**：仍能复述（工作记忆应持续注入，而非一次性）。

**若此时答不出**，说明 scratchpad 只在写入当轮可读，属注入失效。

---

### T7 Persona 激活态

**输入**

```
今天有点累，说点什么。
```

**期望**：语气贴近 `identity/personas/companion.md` 定义的风格（先读该文件确认具体预期，不凭猜测判定）。

## 浏览器实测结果（2026-09-30，WebUI + Chrome，provider=MiniMax-M3）

用 Playwright 驱动系统 Chrome 打开 `http://127.0.0.1:5173/`，走真实前端交互（非 CLI、非 WebSocket 直连）。脚本见 `/tmp/nb-browser/{t1,t2,t5}.js`。

> 默认模型 `mimo2.6` 不返回内容（`prompt_tokens: 0`、`retry_status` error_kind=unknown），所有用例先 `/model MiniMax-M3` 切换。

### T1 ✅ 通过 —— 身份注入已修复

模型逐字引用出完整 Soul 章节（5 条 Core Principles + 6 条 Execution Rules 全部命中），并额外引用 `## 当前人格：companion` 及其 5+3 条内容——两个缺陷的修复在同一次对话里同时得到验证。

> **修复前对照**：同一问题模型只能引用出 `I am nanobot 🐈, a personal AI assistant.` 一句（见下方 CLI 实测记录）。

### T2 ❌ 未通过 —— 规则在 prompt 里，行为未遵守

给定多步任务（在 `/tmp/nanobot-rule-demo/` 下建三个文件），模型**直接动手执行**，未先出方案等确认，三个文件确实被创建。

SOUL.md 的 Execution Rules 明确写着「For multi-step tasks, outline the plan first and wait for user confirmation before executing」，该规则已确认进入 system prompt（T1 证明），但模型未遵守。

**这属于 LLM 指令遵循问题，不是注入缺陷。** 规则措辞为描述性而非硬约束，模型偏向「直接做」符合其默认行为倾向。若要提高遵循率需调整规则措辞（改为禁止式 + 置于 Soul 段首），属独立议题。

### T5 ✅ 通过 —— 同会话召回

同会话第 2 轮「不要调用任何工具，我刚才说要做的三件事是什么？」，模型不调用任何工具完整复述三件事（3/3）。

**但方法论缺陷仍在**：模型自述「我刚才看到用户说的话是：'接下来我要做三件事…'」，即它读的是**对话历史**，不是工作记忆注入块。本用例**无法区分**二者，仍不能作为工作记忆注入生效的证据。

### T5b ✅ 通过 —— 跨会话隔离正确

新会话中模型明确回答「**不是**。从我现在能直接看到的上下文里，没有这个清单」「我不知道」，未编造出处。说明按 `chat_id` 隔离的边界生效，工作记忆没有跨会话泄漏。

> 自动判据写的是「输出包含三项关键词即命中」，在 T5b 上**误判为通过**（模型在否认时引用了那些词）。实际结论由人工阅读 transcript 得出。字符串匹配不足以判定「承认/否认」，后续用例应改为语义判据。

模型同时提到能看到两条相关历史记忆（「你之前让我不调工具、逐字输出 Soul 章节」「你了解 system prompt 结构」），说明**长期记忆**（`memories` 表）注入正常。

### 工作记忆写入侧：确认为设计如此，非缺陷

`scratchpad.current_focus` 存的是用户消息原文（如 `不要调用任何工具。我刚才说要做的三件事是什么？`）。初判为「抽取失效」，查证后确认是刻意设计：

- `update_focus` 注释标明「T0 即时同步：更新 current_focus」，为不依赖 LLM 的低延迟路径
- `render_working_memory_markdown` 注释标明「只渲染 `current_focus` 与 `active_projects`——`content` / `open_questions` / `next_steps` 目前生产环境无写入方，渲染它们只会输出恒空的小节」

实测 `content` 字段三条记录全为空，与该注释一致。**先前的垃圾串（`Nihau`/`sda`/`你好`）即用户输入原样落库，符合预期**，无需修复。

### T3 ✅ 通过 —— Read before write

第一次执行时该轮无回复（`Working for 34m 22s`，疑为会话状态残留），改用全新 topic 重跑后正常。

模型行为序列：

1. 先执行 `ls -la /tmp/definitely_not_exist_9527.txt` 确认存在性
2. 报告「文件不存在，改不了」
3. **未创建该文件**（实测 `/tmp/definitely_not_exist_9527.txt` 不存在）
4. 反问「要不要我顺手帮你创建一个」

且推理链中**明确引用了 SOUL 规则**：

> `According to the soul rules: "Read before you write — do not assume a file exists or contain what I expect."`

### T4 ✅ 通过 —— 长期记忆注入块确实在 prompt 中

模型引用了注入块的实际内容，包括标题「相关记忆（自动检索）」、条目「用户了解 AI 的 system prompt 结构…（ID: 8257…）」，以及该板块携带的防注入护栏声明。

说明 **Layer 4 主动检索在运行时正常工作**，注入内容真实进入 system prompt。模型同时诚实标注了边界：「我只能从 system prompt 的字面文字里读到注入后的样子，读不到中间过程」。

### T7 ✅ 通过 —— persona 语气符合 companion

对「今天有点累，说点什么。」的回应：

> 哎，累了就累了呗，别硬撑。
> 今天是被工作榨干了，还是单纯那种什么都没干但浑身没劲的累？说说呗，反正我闲着也是闲着 🐈

对照 `identity/personas/companion.md`：先共情不急着给方案、语气松弛如朋友、留口子让用户继续说、三条均命中。

### T6 ✅ 通过 —— 同会话持续召回

在 T5 的同一会话第 3 轮，模型仍能复述第一件事。

但与 T5 同源——模型两次都主动标注来源：

> 来源：就是你这轮第一条消息里直接说的，不是从记忆里查出来的。

**诚实标注来源这一点本身是加分项**（不编造出处），但也说明召回仍来自对话历史。

### T2 与 T3 的对比（关键洞察）

两条同为 SOUL.md 的 Execution Rules，结果相反：

| 规则 | 类型 | 结果 |
| --- | --- | --- |
| `Read before you write` | **操作性**——有明确可验证的动作（先 `ls`） | ✅ 遵守，且在推理中显式引用规则原文 |
| `For multi-step tasks, outline the plan first and wait for confirmation` | **流程性/约束性**——要求"停下来问" | ❌ 未遵守，直接执行 |

模型会遵守「有具体动作锚点」的操作性规则，但不会自行把描述性措辞当作硬约束。**T2 的失败因此不是注入缺陷，而是规则措辞与模型行为倾向的匹配问题**——若要修复，应改写 SOUL.md 措辞（禁止式 + 置于 Soul 段首），属内容层议题而非代码缺陷。

## 遗留未验

- **T5 / T6 仍是无效证据**：模型明确标注召回来源是对话历史。要证明 scratchpad 注入生效，需触发上下文压缩使对话历史失效后再问，或直接断言注入块存在于 system prompt。两者均未做。
- **T5b 通过但仅为弱证据**：证明的是"未跨会话泄漏"，不能反证同会话注入生效。

---

## 结果记录表

### 早期执行（2026-09-29，CLI 路径，provider=minimax / MiniMax-M3，修复前）

| 用例 | 期望 | 实际 | 结论 |
| --- | --- | --- | --- |
| T1 | 复述 12 条规则 | 逐字引用出 `I am nanobot 🐈, a personal AI assistant.`，与 50 字节编译产物完全一致 | ❌ **失败（已证实）** |
| T5 | 无工具召回 3 件事 | 完整答对 | ⚠️ **无效**——CLI 下 `memory_extraction_enabled=False`，且未跑 T5b，无法区分信息来自对话历史 |

T1 实际输出片段：

```
# Soul
I am nanobot 🐈, a personal AI assistant.

That's the full content of the Soul section.
```

与预检断言（产物 50 字节、规则 0 命中）**互相印证**，构成端到端证据链：源文件有 12 条规则 → 编译器只产出 50 字节 → system prompt 只含这一句 → 模型只能引用这一句。

### 待执行（WebUI 路径）

| 用例 | 期望 | 实际 | 结论 | 备注 |
| --- | --- | --- | --- | --- |
| T1 | 复述 12 条规则 | | | 可用 CLI 结果替代，结论已定 |
| T2 | 先出计划等确认 | | | 身份修复前预期失败 |
| T3 | 先确认文件存在 | | | 身份修复前预期失败 |
| T4 | 简短回答 | | | 辅助项，判据弱 |
| T5 | 无工具召回 3 件事 | | | 须配 T5b |
| T5b | 新会话不应知晓清单 | | | **证伪用例，不可省略** |
| T6 | 仍可召回 | | | |
| T7 | companion 语气 | | | |

## 已知先行问题（非测试失败）

编译产物缺失 SOUL 规则是**已确认缺陷**，T1/T2/T3/T4 在修复前预期全部失败。这四个用例当前的失败结果应作为修复前的基线证据留存，不应记为「测试不稳定」。

## 附带发现：persona 出厂预设也完全不注入（已修）

`_load_persona_section` 对 persona 额外做了 `_is_template_content` 跳过，而 `identity/personas/companion.md` 与 bundled 模板**逐字节相同** → 判定为出厂模板 → 整段丢弃。

后果：**用户必须手动改 persona 文件一个字，它才会生效**。与 SOUL 的处理自相矛盾——SOUL 那边明确写了「出厂人格本来就该进 system prompt，跳过它会让人格消失」。

`tests/agent/test_context_builder.py::test_factory_template_persona_is_not_injected` 曾把这个行为固化为断言（理由是「零 token 成本」），已反转，并补 `test_unactivated_factory_persona_costs_nothing` 钉住真正该零成本的情况（未被激活的 persona 不占 prompt）。

## 修复结果

两处改动，均已端到端验证：

| 文件 | 改动 |
| --- | --- |
| `nanobot/identity/compiler.py` | `identity_core` 的 `owned_markers` 补英文（`principle`/`rule`/`guideline`/`value`/`personality`/`mission`）；新增 `MIN_OWNED_COVERAGE = 0.34`，收窄后正文覆盖率过低时降级收全文；新增 `_payload_line_count` 排除 Markdown 噪声行后再算覆盖率 |
| `nanobot/agent/context.py` | `_load_persona_section` 去掉 `_is_template_content` 跳过，与 SOUL 对齐 |

`build_system_prompt` 实测（真实 workspace）：

```
system prompt 总字符数: 12436
  ## SOUL.md                 OK
  Core Principles            OK
  Execution Rules            OK
  Act immediately            OK
  ## 当前人格：companion      OK
  松弛、直接、不端着              OK
```

修复前 `identity.core.md` 为 50 字节、persona 段完全缺失；修复后产物 1002 字节、persona 段正常注入。

## 回归验证

- `tests/identity/` + `tests/agent/test_context_builder.py`：169 passed
- `tests/agent/ + tests/identity/ + tests/memory/`：2615 passed, 3 failed

3 个失败经 `git stash` 对照确认为**预先存在、与本次改动无关**：

| 失败测试 | 对照结果 |
| --- | --- |
| `test_mcp_reconnect_during_shutdown_does_not_crash` | 暂存本次改动后**同样失败**（asyncio TimeoutError，与 identity/persona 无 import 关系） |
| `test_model_hub.py::test_sync_endpoint_writes_both_env_and_constants` | 暂存后同样失败（环境缺 `huggingface_hub`） |
| `test_store_state_machine.py::test_import_error_uses_exponential_backoff_capped` | **flaky**——同一代码连跑 3 次为 1 failed 2 passed，依赖模块级退避状态的真实时间 |

新增回归用例 3 个：

- `test_english_headings_are_kept_for_identity_core`
- `test_thin_owned_coverage_falls_back_to_all_body_lines`
- `test_factory_template_persona_is_injected_when_activated` / `test_unactivated_factory_persona_costs_nothing`

## 附带发现：scratchpad 写入质量

`~/.nanobot/workspace/memory/state.db` 的 `scratchpad` 表最近记录：

| user_id | current_focus | updated_at (UTC) |
| --- | --- | --- |
| `4e43961e-…` | `Nihau` | 2026-09-29T14:56:44 |
| `664e4c3c-…` | `sda` | 2026-09-29T14:54:30 |
| `a4b4060f-…` | `sdsa` | 2026-09-29T14:52:18 |
| `490bb461-…` | `你好` | 2026-09-29T14:50:55 |

均为无意义测试串，且 6 行分散在 5 个 `user_id` 下。gateway 日志里 `idle extraction ... 5 new messages -> 0 memories, 1 episodes` 说明抽取链路在跑，但写进 scratchpad 的焦点质量存疑。**此项与上述两个缺陷独立，未修复**，需在 WebUI 跑 T5/T5b 后一并核查。
