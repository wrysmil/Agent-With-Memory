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
---

# Persona 注入 + 身份文件职责校准

- 日期：2026-09-29
- 状态：spec，方向已由用户确认（brainstorming 多轮 + AskUserQuestion）→ 待用户 review 后进 writing-plans
- 路由：`「Harness：brainstorming」`
- 范围：`nanobot/agent/context.py`（注入编排）+ `nanobot/identity/`（激活态解析）+ `nanobot/templates/`（出厂文案）+ `nanobot/webui/identity_api.py` + `webui/src/components/settings/identity/IdentityView.tsx` + 测试
- 关联：`.ai-runtime-artifacts/specs/2026-09-22-identity-rule-compile-spec.md`（编译产物接管注入）、`.ai-runtime-artifacts/research/2026-09-16-openakita-identity-config-and-memory-md-research.md`（openakita 对照）

---

## 〇、背景

### 0.1 用户诉求

1. `identity/personas/` 下 5 个文件在 WebUI 身份页可见，但**没有任何代码把它们注入 prompt**——全仓 grep `persona` 只命中 `catalog.py`（列清单）、`store.py`（读写）、`bootstrap.py`（播种）、`identity_api.py`（SOUL 编辑器下拉预设），无运行时消费方。人格实际全靠 `SOUL.md` 一个人承载。
2. `catalog.py:73` 的 `FULL_TEXT_PERSONAS`（`default.md` / `tech_expert.md`）给 persona 打「全文注入」徽标，但 nanobot 侧**无消费方**，属 UI 承诺了不存在的行为。
3. 5 个 persona 模板全部套用 `SOUL.md` 的 `# Soul` 标题与两段式结构，像是从 SOUL 复制出来改的。
4. 用户 workspace 的 `identity/AGENT.md` = `2323`、`identity/USER.md` = `2332`、`memory/MEMORY.md` = `232`（各 4 字节垃圾内容），每轮 prompt 都在注入这些垃圾。

### 0.2 openakita 对照结论

openakita 的 `src/openakita/agent/persona.py` 是完整的三层人格系统（预设 → 用户叠加 → 上下文自适应），`/persona <角色ID>` 斜杠命令切换，合并结果由 `prompt/builder.py:461` 注入为独立段落。

**本轮不移植三层**（用户已确认「改内容 + 最小接线」）。只取其中被证明有效的两点：**persona 作为独立段落**、**独立于 SOUL 可切换**。

### 0.3 三个已排除的方向及理由

| 方向 | 排除理由 |
| --- | --- |
| token 硬预算 + 超限截断 | 身份段实测 379 token（SOUL 151 / policies 74 / AGENT、USER、MEMORY 各 1，因内容损坏）。上下文窗口实际 **1M**（`.modelPresets.MiniMax-M3.contextWindowTokens = 1048576`），占比 0.036%。截断要解决「挤占上下文」，而它挤占不了。引入截断反而制造「用户规则被静默砍掉且无法排查」的真实新风险。**改为只做 token 计量展示，不执行截断。** |
| FULL / MINIMAL / NONE 三档 prompt 模式 | nanobot 无此概念（仅 `include_memory` 一个开关）。引入会牵扯整条 prompt 链路，与 persona 无关。**本轮不引入。** |
| 出厂模板 hash 账本（openakita 的 `.file_hashes.json`） | 正确解法，但要处理合并冲突与用户基于旧版的自定义，是独立项目。**本轮记为待办**，本轮模板改动已设计为向后兼容，漂移不构成问题（见 §六）。 |

---

## 一、注入设计（本 spec 的核心）

### 1.1 注入位置

`_load_bootstrap_files` 目前的拼接顺序是 `AGENTS.md → SOUL.md → AGENT.md → USER.md`（`context.py:329-341`），段间用 `\n\n`，各段形态为 `## {文件名}\n\n{正文}`。

persona 作为**独立段落插在 SOUL.md 之后、AGENT.md 之前**：

```
## SOUL.md
<本体：我是谁、价值观>

## 当前人格：tech_expert
<表现层：怎么思考、怎么说话>

## AGENT.md
<方法论：怎么做事>
```

**理由**：persona 是**可切换的表现层**，SOUL 是**不可切换的本体**。独立成段后，用户改本体、切表现两件事互不干扰；将来要长出 `/persona` 切换命令，这个结构直接可用。合并进 SOUL 段（备选 B）会让用户手改 SOUL 时冲掉 persona；persona 顶替 SOUL 编译产物（备选 C）会把用户精心写的本体降级为备用。

### 1.2 文件结构：不解析

persona 文件内容**原样作为自由 markdown 注入**，只做一件事——剥掉首行一级标题（`# 标题`），用**文件名 stem** 作显示名。

```
## 当前人格：{文件名 stem}

{剥掉一级标题后的正文}
```

用 stem 而非文件内标题作显示名：workspace 里 5 个旧 persona 的标题都是 `# Soul`，用它会显示成「当前人格：Soul」。stem 稳定、可预期，且 WebUI 侧已有 `settings.identity.preset.{stem}.label` 的 i18n key 负责中文展示名——**prompt 里用 stem，界面上用中文**，两者不必一致。

**不做字段解析。** openakita 用正则从 persona md 提取「性格特征」「沟通风格」「提示词片段」（`persona.py:288-291`），用户改个标题就静默失效。本轮不复制这个脆弱设计：

- 零解析 → 零失效
- 零迁移 → workspace 里 5 个旧结构 persona（标题仍是 `# Soul`）照样能用
- 样板工作量从「重写结构 + 写迁移脚本」降到「重写一个文件的文案」

### 1.3 激活状态

新增 `identity/active_persona`，纯文本单行，存 persona 文件名 stem（如 `tech_expert`）。不存在或为空 = 未激活。

**为什么放 workspace 而不是 config.json**：`ContextBuilder` 目前不持有 config 对象（`loop.py:397` 只传 workspace / timezone / disabled_skills / retrieval 相关）。放 config 需要新增一路参数穿透。放 workspace 则与既有先例一致——`_load_policies_section`（`context.py:375`）已经用 `IdentityStore(self.workspace).read_file(...)` 读 `identity/prompts/policies.md`，加一个同构的小文件读取是零新依赖。

`active_persona` 不是身份文件（不在 `CORE_FILES` 白名单里，不在 WebUI 身份文件列表中出现），只作为运行时状态，由专门的下拉控件写入。

### 1.4 失效保护

以下五种情况**静默跳过**（不注入、不报错、不记 warning），与现有 `_is_template_content` 出厂跳过逻辑同构：

| 情况 | 判定 |
| --- | --- |
| `active_persona` 文件不存在或内容为空 | 未激活 |
| `identity/personas/{stem}.md` 不存在 | 目标缺失 |
| 文件内容 strip 后为空 | 空文件 |
| 内容等于出厂模板（未定制过） | 跳过，避免未定制 workspace 白付 token |

出厂模板比对复用既有 `_is_template_content(content, f"personas/{stem}.md")`。

### 1.5 子 agent：零改动

`SubagentManager._build_subagent_prompt`（`subagent.py:541-562`）走 `agent/subagent_system.md` 模板，**完全不经过 `ContextBuilder`**；已 grep 确认该模板无 `persona` / `SOUL` / `AGENT.md` / `identity` 任何匹配。persona 天然不可能泄漏到子 agent。

保留一条回归测试（子 agent prompt 不含 `## 当前人格`），纯防守，无对应实现改动。

### 1.6 注入后的完整顺序

`build_system_prompt` 现有段落顺序（`context.py:207-274`）不变，persona 落在 `_load_bootstrap_files` 内部：

```
1. identity 模板（Jinja2，agent/identity.md）
2. AGENTS.md
3. SOUL.md          ← 编译产物 identity.core.md 优先
4. 当前人格         ← 本轮新增
5. AGENT.md         ← 编译产物 agent.behavior.md 优先
6. USER.md          ← 编译产物 user.profile.core.md 优先
7. prompts/policies.md
8. tool_contract.md
9. Current Project（仅 workspace ≠ 全局时）
10. Memory（include_memory 时）
11. Active Skills / skills_summary / 会话摘要 / Layer 4 检索块
```

段间统一用 `"\n\n---\n\n"` 连接，整体作为 transcript 的 `messages[0]`（`context.py:441-449`）。

**persona 不参与编译。** 编译目标只有 SOUL / AGENT / USER 三个（`compiler.py:74-117`）；persona 是可切换的即时内容，编译只会增加一层过期判定而无收益。

---

## 二、激活态的读写

### 2.1 后端

- 读：`ContextBuilder` 在 `_load_bootstrap_files` 内读 `identity/active_persona`，trim 后取 stem
- 写：新增 WebUI 端点，与现有 identity 端点同构

### 2.2 前端

`IdentityView` 身份文件列表**上方**加一个 persona 选择下拉：

- 选项 = `personas/*.md` 的实际文件名 + 一个「不使用」项（写空文件）
- 默认「不使用」
- 切换只写 `active_persona`，**不触发规则编译**——persona 不在 `COMPILE_TARGETS`（`compiler.py:74-117`）内，编译它不产生任何产物变化；下一轮 `build_system_prompt` 直接读文件，改完即生效
- 当前激活项打「使用中」标记

`FULL_TEXT_PERSONAS`（`catalog.py:73`）**删除**——徽标无消费方，留着是空转承诺。`BADGE_FULL_TEXT_INJECT` 徽标与对应 i18n key 同步删除。

---

## 三、内容改动

### 3.1 `templates/personas/tech_expert.md`（样板）

首行 `# Soul` → `# 技术专家`，正文按「人格 + 表达风格」重写，剥离当前从 SOUL 抄来的「核心原则 / 执行规则」两段结构（执行规则属 AGENT 职责）。

其余 4 个 persona（`balanced` / `mentor` / `creative` / `companion`）**本轮不动**——它们靠 §1.2 的零解析设计继续可用。是否同步重写留到下一轮。

### 3.2 SOUL / AGENT 职责

**仓库出厂模板已经拆好了**：`templates/AGENT.md` 开头即「在 SOUL 人格之上，以下规则优先级更高，约束『怎么做事』」，分「任务执行 / 工具与环境 / 沟通契约」三段。`templates/SOUL.md` 分「核心原则 / 执行规则」两段。

本轮**不改出厂文案**，只做 §3.3 的用户侧数据修复。

理由：出厂模板的拆分已经符合「SOUL=本体 / AGENT=方法论」的目标；用户 workspace 里的 SOUL 是「工程搭档」定制版（正常用户编辑），AGENT 是损坏数据（异常）。问题在数据不在模板。

### 3.3 损坏数据修复

`identity/AGENT.md` = `2323`、`identity/USER.md` = `2332`、`memory/MEMORY.md` = `232` 三个文件内容为无意义数字，来源不明（非出厂模板，出厂模板正常）。

修复路径（**利用既有能力，不写新功能**）：删除损坏文件 → `identity_read_file`（`identity_api.py:43-60`）对不存在的文件返回出厂模板内容并标记 `fromTemplate: true` → 用户在 WebUI 确认后保存。

`memory/MEMORY.md` 由 Dream 生命周期托管（`LIFECYCLE_OWNED_FILES`），`IdentityStore.write_file` 硬拒写入（`store.py:179-184`）。**该文件不手工修**——下次 Dream 运行时按 `MemoryLifecycle` 语义重新派生。

> **本节是一次性数据修复，不属于代码交付。** 修复对象在 `~/.nanobot/workspace/` 下、不在仓库内，不产生 diff。执行前会先向用户确认。

---

## 四、token 计量

WebUI 身份页每个文件旁显示「约 N tokens」。估算公式沿用 openakita 的口径（中文 /1.5 + 英文 /4），在 nanobot 侧自实现一份，不引入依赖。

**只展示，不执行。** 无预算总额、无百分比分配、无截断。理由见 §〇 表格。

---

## 五、测试

| 场景 | 断言 |
| --- | --- |
| 未激活 | prompt 不含 `## 当前人格` |
| 激活 tech_expert | prompt 含 `## 当前人格：tech_expert`，且正文为剥掉首行一级标题后的内容 |
| persona 正文保留二级及以下标题 | 注入结果中原样保留 |
| `active_persona` 指向不存在的文件 | 静默跳过，不抛异常，不阻断 prompt 构建 |
| persona 文件为空 / 全空白 | 静默跳过 |
| persona 内容等于出厂模板 | 静默跳过 |
| 顺序 | `## 当前人格` 出现在 `## SOUL.md` 之后、`## AGENT.md` 之前 |
| 子 agent | `subagent_system.md` 渲染结果不含 `## 当前人格` |
| 出厂模板 | `tech_expert.md` 首行是 `# 技术专家` |
| 旧结构兼容 | 标题为 `# Soul` 的 persona 仍能注入，显示名取 stem 而非文件内标题 |

Python 侧落在 `tests/agent/test_context_builder.py`；WebUI 侧落在 `webui/src/tests/` 既有 identity 测试文件。

---

## 六、已知限制与待办

| 项 | 说明 |
| --- | --- |
| 出厂模板升级漂移 | `ensure_identity_templates`（`bootstrap.py:61-107`）是 skip-if-exists，模板改进了老 workspace 拿不到。正确解法是 openakita 式 hash 账本（`agent/identity.py` 的 `.file_hashes.json`），**本轮不做**。本轮模板改动（persona 标题、SOUL/AGENT 结构）已设计为向后兼容，漂移不构成问题。 |
| persona 无切换命令 | 只能从 WebUI 下拉切换，没有 `/persona` 斜杠命令。留待后续。 |
| persona 无用户叠加层 | openakita 的层二（`user_custom.md` + 偏好记忆）不移植。 |
| persona 不参与编译 | 每次切换即时生效，不走编译产物。persona 通常很短，收益不足以换取一层过期判定。 |
| 其余 4 个 persona 未重写 | 靠零解析设计继续可用，风格仍是旧的 SOUL 复制体。 |

---

## 七、验收标准

- `pytest tests/agent/test_context_builder.py tests/identity/` 通过
- `ruff check nanobot/` 与 `basedpyright` 无新增告警
- `webui` 侧 `bun run test` 与 `bun run build` 通过
- 手动验证：WebUI 选中 tech_expert 后，新会话的 system prompt 含 `## 当前人格：tech_expert`，切回「不使用」后消失
- 启动日志无新增 warning

---

## Next

**（写入后须暂停，等用户明确继续 — 见 `harness-kit/core/routing.md` § 阶段门禁）**

- 确认方案无误 → 说「写计划」或「制定实施计划」
- 变更范围小、无需计划 → 说「直接实现」或「直接做」
- 需要调整方案 → 直接说修改意见
