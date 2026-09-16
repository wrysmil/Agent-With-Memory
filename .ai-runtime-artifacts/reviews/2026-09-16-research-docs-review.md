# 审查报告：openakita 两份调研文档的事实核验

> 审查日期：2026-09-16
> 审查对象：
> - `.ai-runtime-artifacts/research/2026-09-16-openakita-vectorization-research.md`
> - `.ai-runtime-artifacts/research/2026-09-16-openakita-identity-config-and-memory-md-research.md`
> 审查方式：**双侧源码逐条比对**——openakita 侧由独立 agent 对抗性核验 + Leader 复核关键项；nanobot 侧由 Leader 直接读源码核验
> 审查标准：文档中的每个事实断言必须能被 `文件:行号` 处的源码证实

---

## 一、审查结论

**整体可信度：中高。结构性结论全部成立，但不可"照抄"代码块。**

经核验：
- **结论性判断 100% 成立**（开放式架构、SQLite 为真相源、向量库可选可降级、MEMORY.md 派生化、身份 API 不设防、前端无向量状态）
- **行号类断言绝大多数精确**（`api/routes/identity.py`、`api/routes/memory.py`、`compiler.py`、`types.py`、`lifecycle.py`、`IdentityView.tsx` 近乎零误差）
- **但发现 5 类明确错误 + 12 处偏差**，其中 1 处是**把不存在的东西写成源码**（编造），已全部修正

**⚠️ 对文档的使用提醒（已写入两份文档头部）**：
本文若干代码块是**节选 / 重排**版本，不是逐字摘录。若作为移植依据逐字复制，请回源核对。

---

## 二、明确错误（5 类，全部已修正）

### 错误 1 —— §6.6 `truncate_memory_md` 代码块是编造的（**最严重**）

**文档原稿给出的"源码"**：

```python
sections = re.split(r"(?=#^## )", content)      # ← 正则被拼坏
title = extract_title(section)                  # ← extract_title 全仓不存在
if title in ["重要规则", "规则", "rules"]:        # ← 实际是子串匹配 + 5 个关键词
if len(result) + len(section) <= max_chars:     # ← 实际按 current_len（含 +2 分隔符）累加
    result.append(section)
else:
    break                                       # ← 实际对高优先级段有「截断并标注」分支
```

**源码实际**（`types.py:94-143`，已核验）：
- `re.split(r"(?=^## )", content, flags=re.MULTILINE)` —— 不是 `(?=#^## )`
- 标题用内联 `re.match(r"^## (.+)", stripped)` 提取 —— **`grep -rn "def extract_title" src/` 零命中**
- 判定是 `any(kw in title for kw in _RULE_SECTION_KEYWORDS)` —— **子串包含**，关键词 5 个 `{重要规则, 规则, rules, 行为规则, 用户规则}`
- 预算累加含分隔符：`current_len + len(section) + 2`
- 高优先级段超预算时有**部分截断 + 标注**分支：`section[:remaining] + "\n...(规则被截断)"` 然后 `break`

**危害**：文档 §十 曾建议"该机制值得照抄"，若按原稿代码移植，会得到**与 openakita 不同**的截断行为（少 2 个关键词、等值 vs 子串、缺截断分支）。

**已修正**：替换为源码逐字摘录 + 4 条"易抄错细节"说明。
**附注**：该错误与文档自身 §6.2（正确列了 5 个关键词）**互相冲突** —— 内部矛盾本可作为发现线索。

### 错误 2 —— "`guide.md` 恒定注入 system prompt 顶部"

**实际**（`builder.py:1812-1813` + `646`）：它是 `_build_memory_section` 里的 `parts.append(...)`，最终进 **`developer_parts`**，不是 system prompt 顶部。且：
- 前置条件两层：`builder.py:613` `_memory_scope in {...} and prompt_mode in (FULL, MINIMAL)`；`builder.py:1807` `if not memory_manager: return ""`
- **默认注入 compact 版**（`builder.py:625-634`，`_use_compact = not (_verbose_override or _eligible_for_full)`），full 版仅 `LOCAL_AGENT + LARGE` 或显式 `OPENAKITA_PROMPT_VERBOSE_MEMORY_GUIDE=1`

**危害**：原稿据此把 Layer 0 当作"收益最快、直接照搬"的项时，会误判 openakita 的默认配置。

**已修正**：重写 §八，补注入机制 + 切换条件 + compact/full 双版本全文。

### 错误 3 —— "`/api/memory/entries` 是另一组路由"

**实际**：前端确有该调用（`ChatView.tsx:3403`），但**后端不存在任何 `/api/memory/entries` 路由**（全仓 `.py` grep 零命中；`/api/memory` 系只有 `memory_repair.py` 的 `/api/memory/repair`）。这是前端调了一个后端未实现的端点。

**已修正**：改为明确说明"后端不存在该路由"。

### 错误 4 —— "`editorRef` 声明了但从未被使用"

**实际**：`IdentityView.tsx:83` 声明、`:367` `ref={editorRef}` 绑定 —— **在用**。
正确的可核验说法只能是"未做任何命令式操作（无 monaco / 无 API 调用）"。

**已修正**（`§1.1`）。**危害最小**（未承载下游结论）。

### 错误 5 —— 文件行数系统性 +1

| 文件 | 文档原写 | 实际 |
| --- | --- | --- |
| `vector_store.py` | 581 | **580** |
| `model_hub.py` | 420 | **419** |
| `search_backends.py` | 426 | **425** |
| `unified_store.py` | 732 | **731** |
| `api/routes/identity.py` | 523 | **522** |
| `agent/identity.py` | 688 | **687** |
| `IdentityView.tsx` | 500 | **499** |

（以上文件均以换行结尾，`wc -l` 即真实行数。`compiler.py` 531、`builder.py` 2464、`MemoryView.tsx` 1158 反而正确 —— 说明口径不一致。）
**已修正**。

---

## 三、偏差（12 处，全部已修正）

| # | 文档断言 | 实际 |
| --- | --- | --- |
| 1 | `budget.py:131-189` tier 档 LARGE 6000 / MEDIUM 5000 / SMALL 2500 / TINY 600 | `for_context_window`（116-162）= **5000/3500/2500/600**；`for_tier`（164-193）= SMALL 600、MEDIUM 3000，其余 6000。原写两值源码中不存在 |
| 2 | `record_turn` "只把轮次攒进 `_session_turns`" | 还写 SQLite（`manager.py:1280-1290`）、JSONL、attachments、`_recent_messages`；方法到 **1291** 行不是 1259。**"不写向量"部分正确**，"只"字不成立 |
| 3 | `MemoryView.tsx:193-249` 状态声明 | 实际 **217-237**；且代码块删改了 9 个 state 并重排顺序，非原样摘录 |
| 4 | `manager.py:2076-2164` `add_memory` | 方法从 **2037** 开始；2076 只是去重分支起点 |
| 5 | `config.py:522-561` | 7 个字段实际在 **525-556** |
| 6 | `vector_store.py:157-218` `_do_initialize_inner` | 实际 **157-252** |
| 7 | 三源定义 `36-56 行` | 实际 **36-43** |
| 8 | `scheduler/executor.py:1115`、`evolution/self_check.py:1001`「只传 search_backend」 | 实际 **1108** / **998**；且**同样传了** `embedding_api_provider/key/model` |
| 9 | `delete_not_in`「grep 零命中」 | grep 有 2 处命中（`lifecycle.py:308-309`，即调用点本身）。结论「`ChromaDBBackend` 未实现」正确，但"零命中"说法不准 |
| 10 | guide「full ~600 token / compact ~200 token」 | 源码注释（`builder.py:1812`）自述 200/600，但实测文件 3177/505 字符 ≈ **~1865 / ~295 token**。注释低估约 3 倍 |
| 11 | 前端 token 估算与后端「是两套实现」 | 是**同一公式的重复实现**（cn/1.5 + en/4），非两种算法 |
| 12 | §一 称「`save_semantic` 先写 SQLite」vs §6.2 称「先写 VectorStore」 | 两句分属不同层（`UnifiedStore` vs `MemoryManager`）**都成立**，但并列呈现会误导 |

---

## 四、nanobot 侧核验（Leader 直接读源码，12/12 通过）

两份文档对 nanobot 的全部断言均经我直接读源码验证：

| 断言 | 核验 |
| --- | --- |
| `MemoryStore` 在 `memory.py:60-744` | ✅ 类起 60，745 起下一 section |
| 四文件路径在 `memory.py:74-93` | ✅ |
| `MemoryType` 6 值在 `models.py:11-18` | ✅ |
| `_SKIPPABLE_DEFAULTS` 在 `context.py:107` | ✅ `{"AGENTS.md","USER.md"}` |
| MEMORY.md 注入在 `context.py:230-233` | ✅ |
| bootstrap 加载在 `context.py:307-334` | ✅ |
| 模板跳过在 `context.py:328-331` | ✅ |
| `SearchBackend` 协议在 `search_backend.py:10-13` | ✅ 单方法 |
| chromadb / api_embedding 是 stub | ✅ 两分支都 `return Fts5SearchBackend` |
| 记忆路由在 `settings_routes.py:148-173` | ✅ |
| `identity.md:9` 提到 `memory_search` | ✅（9 行 + 15 行 else 分支） |
| nanobot **无** SQLite→MEMORY.md 生成路径 | ✅ grep `refresh_memory\|generate_memory_md` 零命中 |

**核验中补入文档的 3 项**（原稿遗漏，非错误）：
1. **MEMORY.md 已有模板跳过保护**（`context.py:232`）—— 不只 bootstrap 文件有
2. **`/api/settings/memory/stats` 端点已存在**（`settings_routes.py:160`）—— 原稿建议"增加字段"时未说明端点已在，读起来像要新建
3. **`GitStore` 是 openakita `.file_hashes.json` 的对等物**（`memory.py:90-92`，tracked `SOUL.md`/`USER.md`/`memory/MEMORY.md`/`memory/.dream_cursor`）—— 两份文档都没提，导致"nanobot 没有版本追踪"这个印象是错的；它已 tracked，只是用途不同（给 Dream commit 做 diff，不是升级覆盖检测）

---

## 五、因事实错误而不成立的结论（修正后）

| 原结论 | 状态 |
| --- | --- |
| §十「`truncate_memory_md` 段落优先级可直接抄」 | **方向成立，代码块不可作为依据** —— 原稿代码行为与源码不同。已换成源码摘录 |
| §八「Layer 0 是恒定注入，收益最快」 | **需修正认知** —— 实际是 `developer_parts` 条件注入，且 openakita **默认只注入 compact 版**。移植建议已改为"直接上 compact 规模" |
| 「guide full 版 ~600 token」 | **不成立** —— 实测 ~1865。作为预算参考会低估约 3 倍 |
| 「`/api/memory/entries` 是另一组路由」 | **不成立** —— 后端无此端点 |
| 「`editorRef` 未使用」 | **事实反证** —— 在用。未承载下游结论，危害最小 |

**未受影响的核心结论**（核验后更加稳固）：
- SQLite 是唯一真相源，向量索引与 MEMORY.md 都是其下游派生物
- openakita 的向量能力是**真接通的**（非 stub），且带完整三源下载 / 状态机 / 优雅降级
- `.bak` 单版本备份、无 diff 检测、无合并
- `_RESTRICTED_FILES` 不做服务端拦截
- 前端完全没有向量库状态展示
- 两条并行生成路径（`LifecycleManager` 主 + `DailyConsolidator` 备用）标题格式不一致

---

## 六、遗留风险提示

1. **文档内的代码块普遍是节选**：`MemoryView.tsx` 状态块、`record_turn`、`IdentityView` 若干片段都经过删减重排。已在两份文档头部加说明。
2. **openakita 自身存在多组前后端常量不一致**（非文档错误，是调研发现）：`MEMORY_MAX_CHARS` 双份维护、`_BUDGET_MAP` 与 `BudgetConfig` 数字脱节、SOUL.md "全文注入" 徽标与代码不符。这些**已在文档中标注**，移植时应作为"不要照抄"的反例。
3. **`truncate_memory_md` 的 `_RULE_SECTION_KEYWORDS` 需回源确认**：核验报告给出 5 个关键词，但文档 §6.2 与 §6.6 均引用同一来源，若该常量本身有出入，两处会一起错。建议移植前直接 `grep _RULE_SECTION_KEYWORDS` 确认。

---

## 七、修正清单（已落盘）

**doc1 `2026-09-16-openakita-vectorization-research.md`**：9 处
行数×4、`config.py` 行号、`_do_initialize_inner` 范围、三源定义范围、`add_memory` 行号、`MemoryView` 行号 + 节选声明、`executor/self_check` 行号与描述、`delete_not_in` 表述、`/api/memory/entries` 修正、`save_semantic` 顺序澄清、`record_turn` 修正、stats 端点说明、头部核验声明

**doc2 `2026-09-16-openakita-identity-config-and-memory-md-research.md`**：8 处
§6.6 重写（编造代码块 → 源码摘录）、§八 重写（注入机制 + 双版本全文）、`editorRef` 修正、token 估算修正、tier 档位修正、行数×5、索引表行号、nanobot 基线表补 3 行、头部核验声明
