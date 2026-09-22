---
artifact: research-report
route: web-investigator
skills:
  - agent-browser
source:
  - core/routing.md
  - user-query
created_at: 2026-09-18
topic: memory-injection-baseline-and-openakita-design
scope: 本地源码调研（openakita 参照实现 + nanobot 现状基线），未做联网调研
---

# 记忆注入链路调研：nanobot 现状基线 vs openakita 设计

## 调研目标

用户要求（原话）：**「记忆的注入也要考虑」**、**「直接调研那个本地源码」**。

上下文：正在为 nanobot WebUI 的「身份」设置页设计后端。该页会把 `MEMORY.md` 作为可编辑文件暴露给用户，而 nanobot 的 MEMORY.md 是**由 Dream 流程自动重生成**的 → 手工编辑与自动重生成存在语义冲突，且注入路径的体量控制直接影响 prompt 预算。故需先把两侧的注入链路摸清。

**调研范围**：仅本地源码（openakita 参照实现 + nanobot 现状），未做联网/开源项目横向对比。

**与既有产物的边界**：`2026-09-16-openakita-identity-config-and-memory-md-research.md` 已覆盖 openakita `builder.py:427-438`（注入前自动编译 + TTL 缓存）、`budget.py:89-98`（各层预算常量）、`builder.py:1812-1813`（Layer 0 指南 compact/full）、`lifecycle.py:1384-1458`（`refresh_memory_md`）、`types.py:82-148`（`MEMORY_MD_MAX_CHARS` / `truncate_memory_md`）。**本文不重复上述内容**，聚焦两条此前完全未查的链路：① openakita「召回产物 → 最终注入」的组装与裁剪；② nanobot 自身的注入基线。

---

## 一、nanobot 现状基线（改造起点）

### 1.1 system prompt 注入顺序

`nanobot/agent/context.py:205-267` `ContextBuilder.build_system_prompt()`，各段以 `"\n\n---\n\n"` 连接（`context.py:267`）：

| # | 段 | 来源 | 行号 | 有无预算 |
|---|---|---|---|---|
| 1 | Identity | `_get_identity()` → `agent/identity.md` | `context.py:216` | — |
| 2 | Bootstrap 文件 | `AGENTS.md`(project root) / `SOUL.md` / `USER.md`(workspace) | `context.py:218`, `309-333` | ❌ 全文 |
| 3 | Tool contract | `agent/tool_contract.md` | `context.py:222` | — |
| 4 | Current Project | 仅当 workspace ≠ 默认工作区 | `context.py:224-230` | — |
| 5 | **Memory** | `# Memory\n\n## Long-term Memory\n{memory}` | **`context.py:232-235`** | ❌ **全文，无截断** |
| 6 | Active Skills | 常驻技能正文 | `context.py:237-241` | — |
| 7 | Skills summary | 技能目录摘要 | `context.py:243-248` | — |
| 8 | Archived Context Summary | 会话压缩摘要 | `context.py:250-255` | — |
| 9 | **Layer 4 召回块** | 调用方预算，见 1.2 | `context.py:261-265` | ✅ 见下 |

### 1.2 Layer 4 主动召回（nanobot 已实现的部分）

⚠️ 关键发现：nanobot **已经有**自动召回注入，命名直接沿用 openakita 的「Layer 4」术语（`nanobot/agent/tools/memory_search.py:3` 注释原文：*"This is the 'active recall' path (vs. Layer 4 automatic injection)"*）。

实现位于 `context.py:134-203`：

```python
# context.py:134-145
def _should_retrieve(self) -> bool:
    if not self._active_retrieval_enabled or self._retrieval_engine is None:
        return False
    if self._memory_enabled_provider is not None and not self._memory_enabled_provider():
        return False
    return True
```

- **门禁**：`active_retrieval_enabled` 接线标志 ∧ `retrieval_engine` 非空 ∧ 热切换开关 `memory_enabled` 为真（`memory_enabled_provider` 闭包，改动无需重启 gateway，`context.py:127-132`）。
- **流程**（`context.py:180-203`）：`MemoryQueryPreprocessor.prepare(query, recent_messages)` → `prepared.skip` 为真（空/控制词/超短）则跳过并记 trace → `retrieval_engine.retrieve_with_ids(...)`。
- **失败隔离**：整段包 `try/except Exception`，吞异常仅告警，**保证召回失败不阻断 system prompt 构建**（`context.py:197-201`）。
- **预计算**：召回块在 `AgentLoop._build_turn` 中预计算后经 `retrieved_memory_section` 参数传入，以保持 `build_system_prompt` 的同步 API（`context.py:257-260` 注释）。
- **副产物**：返回 `(block, ids)`，`ids` 为本次注入的 memory_id，供 WU-B 引用评分闭环在 idle 提取时作为 `cited_memories` 送入评分 prompt（`context.py:158-160`, `174`）。

### 1.3 nanobot 记忆文件的读写现状：**存在两条并行写入路径**

⚠️ 这是本次调研最容易误判的地方。`MEMORY.md` 在 nanobot 里有**两个写入者**，安全语义完全不同：

**路径 A — 记忆系统（canonical，已移植 openakita 的加固）** `nanobot/memory/lifecycle.py`：

| 能力 | 实现 | 行号 |
|---|---|---|
| 从 SQLite 派生 MEMORY.md | `refresh_memory_md_sync` | `lifecycle.py:101-156` |
| **备份 + 失败恢复写入** | `_safe_write_with_backup` | `lifecycle.py:193` |
| **段落优先级截断** | `truncate_memory_md`（规则段关键词 `_RULE_SECTION_KEYWORDS` 优先） | `lifecycle.py:242`，关键词表 `:44-47` |
| 截断常量 | `MEMORY_MD_MAX_CHARS` | `lifecycle.py:137-138` |
| 防抖异步刷新 | `schedule_refresh_md(debounce_seconds=...)` | `lifecycle.py:162-187` |
| 实例级锁（修过跨 workspace 阻塞） | `self._derive_lock` | `lifecycle.py:86` |

> 即：**openakita 的 `_safe_write_with_backup` + `truncate_memory_md` 早已移植完毕**，本节初稿误判为缺失，已修正。详见 §5。

**路径 B — 旧 MemoryStore（裸写）** `nanobot/agent/memory.py`：

```python
# memory.py:235-236
def write_memory(self, content: str) -> None:
    self.memory_file.write_text(content, encoding="utf-8")
```

- **无备份、无锁、无原子写**。`nanobot/sdk/clients.py:167` 直接调用它（`self._loop.context.memory.write_memory(text)`）。
- `get_memory_context()`（`memory.py:256-258`）只做 `f"## Long-term Memory\n{long_term}"` 拼接，且**该方法在 `context.py` 中未被使用**——`context.py:232-235` 内联了自己的版本，两者文案不一致（`# Memory` + `## Long-term Memory` vs 仅 `## Long-term Memory`）。
- 身份页若开放 MEMORY.md 写入，**必须走路径 A 的加固语义，不得复用路径 B 的裸写**。

**两路径共同的重叠面**：`_DREAM_CONTENT_PATHS = ("SOUL.md", "USER.md", "memory/MEMORY.md")`（`memory.py:67`）——Dream 流程会改写这三个文件，**与身份页的可编辑文件集高度重叠**。

### 1.4 nanobot 缺口清单

| openakita 层 | nanobot 现状 |
|---|---|
| L0 记忆系统自描述（Memory Guide） | ❌ 无 |
| L1 Scratchpad | ⚠️ **有产出无注入**：DB 表 `scratchpad`（`memory/database.py:95`）、`scratchpad_writer.py`、`repository.get_scratchpad` 齐全，`context.py` 中**无任何引用** |
| L1.5 Pinned Rules | ❌ 无 |
| L2 Core Memory（**截断后**注入） | ⚠️ **截断逻辑已有**（`lifecycle.py:242` `truncate_memory_md`，落盘时按 `MEMORY_MD_MAX_CHARS` 截断），但**注入侧不再截断**：`context.py:232-235` 直接把磁盘文件全文拼进 prompt |
| L3 Experience Hints | ⚠️ **有产出无注入**：`memory/experience_extractor.py` 存在，`context.py` 中无引用 |
| L4 Active Retrieval | ✅ **已实现**（`context.py:134-203`） |
| L5 Relational Graph | ❌ 无 |
| token 预算体系 / 自适应下调 | ❌ 无 |
| PromptCompiler（LM 优化 / 规则编译） | ❌ 无 |

> 「有产出无注入」= 数据已落库/已提取，但没有进入 prompt 的路径。已复查 `context.py` 全文，Scratchpad 与 Experience 均**只写不读**。

**改判要点**：初稿曾判定「L2 无截断 = nanobot 完全缺失体量控制」，**该判定不成立**。正确表述是：
- **落盘侧**：已由 `MemoryLifecycle.truncate_memory_md` 控制（`lifecycle.py:137-138`），文件不会无限膨胀；
- **注入侧**：`context.py` 读文件后原样注入，**不设第二道闸**。两者在正常路径下等价，但当用户**手工写入一个超长 MEMORY.md**（身份页正是要开这个口子）时，落盘侧不再兜底，注入侧也无预算 —— 这才是真实缺口。

---

## 二、openakita「召回产物 → 注入」的组装与裁剪

### 2.1 六层渐进式披露

`_build_memory_section`（`prompt/builder.py:1788-1882`）按固定顺序拼装，最终 `return "\n\n".join(parts)`（`builder.py:1882`）：

| 层 | 内容 | 产出函数 | 行号 |
|---|---|---|---|
| L0 | 记忆系统自描述（compact / full） | `_MEMORY_SYSTEM_GUIDE_COMPACT` / `_MEMORY_SYSTEM_GUIDE` | `builder.py:1813` |
| L1 | Scratchpad（当前任务 + 近期完成） | `_build_scratchpad_section` → `UnifiedStore.get_scratchpad().to_markdown()` | `builder.py:1816`, `1963-1976` |
| L1.5 | Pinned Rules（仅 `memory_type="rule"`） | `_build_pinned_rules_section` | `builder.py:1821-1827`, `1983-2081` |
| — | （`pinned_only` 模式在此直接 return） | — | `builder.py:1829-1830` |
| — | 压缩保护快照（上一轮压缩前的事实快照） | `get_precompact_snapshot_context(max_chars=1000)` | `builder.py:1832-1839` |
| L2 | Core Memory（MEMORY.md **截断后**） | `_get_core_memory` → `truncate_memory_md(...)` | `builder.py:1841-1847`, `2146-2193` |
| L3 | Experience Hints（高权重经验/教训/技能） | `_build_experience_section(max_items=5)` | `builder.py:1850-1855`, `2200-2258` |
| L4 | Active Retrieval（语义检索） | `_retrieve_by_query` → `get_injection_context(...)` | `builder.py:1857-1874`, `1885-1906` |
| L5 | Relational Graph（图检索） | `_retrieve_relational` → `search_fts` / `search_like` | `builder.py:1876-1880`, `1909-1960` |

### 2.2 注入位置：`## Developer` 段，不是 messages

```python
# builder.py:646
developer_parts.append(memory_section)
# builder.py:711
sections.append("## Developer\n\n" + "\n\n".join(developer_parts))
# builder.py:717
system_prompt = "\n\n---\n\n".join(sections)
```

整段 `system_prompt` 作为**单条 system 消息**下发。`builder.py:705` 注释明确了缓存边界：*「上方 system_parts 在 session 内不变，下方 developer_parts / tool_parts / user_parts 每轮可能变化」*——记忆层落在**每轮可变区**，这正是它不吃 session 级 prompt 缓存的原因。

### 2.3 召回结果如何进来：同步注入，非 tool call

`_retrieve_by_query`（`builder.py:1885-1906`）在**构建 prompt 阶段同步调用** `memory_manager.get_injection_context(task_description, max_related=5, precomputed_keywords=...)`。L5 直接同步调 `relational_store.search_fts`。

⚠️ **已知技术债**（`memory/manager.py:2360-2392`）：若配置了 replace backend，`get_injection_context` 用 `ThreadPoolExecutor` + `asyncio.run` 把异步检索包成同步调用，**超时 10 秒**。同步上下文中跑事件循环、主线程被占 10s 是真实风险。

### 2.4 各层裁剪策略

| 层 | 上限 | 策略 |
|---|---|---|
| L1.5 Pinned Rules | 1500 字符（`500 tok × 3 chars/tok`，`builder.py:1979-1980`） | 按 `importance_score` 降序；`total_chars + len(line) > max_chars` 提前 break（`builder.py:2071-2072`）；内容 sha1[:16] 去重（`builder.py:2056-2060`）；**global scope + 自动来源 + confidence<0.6 直接丢弃**（`builder.py:2046-2049`） |
| L2 MEMORY.md | `min(core_budget×3, MEMORY_MD_MAX_CHARS=1500)`，`core_budget = min(budget_tokens//2, 500)`（`builder.py:1841-1847`） | `truncate_memory_md` 按段落拆分，**规则段优先保留**，超限时在规则段末尾追加 `\n...(规则被截断)`（`types.py:94-148`） |
| L3 Experience | 单条 200 字符、整段 1200 字符、最多 5 条（`builder.py:2196-2197`） | 超限 break；带内容哈希去重 |
| L4 Retrieval | `max_tokens=500`（`builder.py:1870`），下游 `retrieve(max_tokens=700)` | 下游截断 |
| L5 Relational | `max_tokens=500`，单条 `n.content[:200]`，最多 5 节点（`builder.py:1878`, `1956`, `1934`） | — |

### 2.5 预算超限的两级兜底

`memory_budget = 3500`（`budget.py:89-98`）超限时：

**第一级 — 自适应下调** `_adaptive_memory_budget`（`builder.py:1713-1737`），按 `user_input_tokens / context_window` 比值：

```python
# 比值 > 0.5
budget = max(300, base_budget // 5)
skip_experience = True; skip_relational = True
# 比值 > 0.3：线性缩放
budget = base_budget * scale
skip_relational = True
```

**第二级 — 整段兜底截断**：`apply_budget(joined, max(memory_budget + 2500, 800), "developer")`（`builder.py:661`, `675-684`），截断时打 WARNING。

> ⚠️ 该兜底以字符/token 估算**尾部硬切**，可能在记忆条目中间断开。

### 2.6 手工编辑 vs 自动重生成：**无协调，直接覆盖**

这是与身份页最直接相关的一条。

```python
# memory/lifecycle.py:102-125  _safe_write_with_backup
# 先把旧文件复制为 .bak，再 path.write_text(new_content)；写失败从 .bak 恢复
```

- `refresh_memory_md`（`lifecycle.py:1384-1458`）从 SQLite**重建** MEMORY.md：按 `scope="user", min_importance=0.5, limit=200` 拉取 SemanticMemory，排除 `source="profile_fallback"`，按 `(type, content_hash)` 去重，每类 top-4，最终用 `MEMORY_MD_MAX_CHARS=1500` 截断写盘。
- **没有 diff 检测、没有合并层**。用户手工编辑会在下一次 `refresh_memory_md` 时被**直接覆盖**；`.bak` 仅在主文件损坏时作为 fallback 被读取（`builder.py:2157-2172`），不参与合并。
- 注入读的是**磁盘文件**（`memory_manager.memory_md_path.read_text`，`builder.py:2168`），**不是 SQLite 内存态**。

### 2.7 缓存与失效

```python
# builder.py:2159-2165
stat = path_to_try.stat()
cache_key = (str(path_to_try), stat.st_mtime_ns, max_chars)
cached = _CORE_MEMORY_CACHE.get(cache_key)
if cached is not None:
    return cached
```

- `_CORE_MEMORY_CACHE` 为 `(path, st_mtime_ns, max_chars)` 三元组键，LRU 上限 32（`builder.py:2134-2193`）。**外部改文件 → mtime 变 → 缓存自然失效，无需手动 invalidate。**
- **未发现显式版本号字段**，失效完全依赖 mtime。
- ⚠️ 该缓存**只覆盖 L2**。L1 / L1.5 / L3 / L4 / L5 **每次 build 全量重算**，高频 prompt 重建场景下是非平凡开销。

---

## 三、差距矩阵

| 维度 | nanobot 现状 | openakita | 差距性质 |
|---|---|---|---|
| 注入位置 | system prompt 各段，`---` 连接 | system prompt `## Developer` 段 | 结构差异，非能力差异 |
| 记忆分层 | 仅 L4 | L0–L5 六层 | **大** |
| token 预算体系 | 无 | 3500 + 自适应下调 + 兜底截断 | **大** |
| MEMORY.md 截断 | ✅ **落盘侧已有**（`lifecycle.py:242`）；注入侧不复查 | 注入侧再截断，规则段优先 | **小**（仅手工写入路径暴露） |
| 写入加固（备份/失败恢复） | ✅ **已有**（`lifecycle.py:193`，路径 A）；旧路径 B 仍是裸写 | `.bak` 备份 + 失败恢复 | **小**（勿走路径 B 即可） |
| 召回驱动 | 自动（L4）+ 工具（`memory_search`） | 自动（L4）+ 工具 | 已对齐 ✅ |
| 召回失败隔离 | ✅ `try/except` 吞异常 | — | 已对齐 ✅ |
| 手工编辑冲突 | 未定义 | **无协调，直接覆盖** | 中（两边都没解决） |
| 注入缓存 | 无 | L2 走 mtime LRU | 中 |
| Scratchpad / Experience 注入 | ❌ 有产出无注入 | ✅ L1 / L3 | 中 |

---

## 四、对「身份」设置页后端的直接影响

1. **MEMORY.md 的 badge「自动重生成」是准确的**，但后端语义必须显式定义：用户在身份页保存 MEMORY.md 后，下一次 Dream/`refresh_memory_md` 会**覆盖**它。UI 上"已保存"的反馈会制造"改动持久生效"的错误预期。
2. **写入必须走路径 A（`MemoryLifecycle`），不得复用路径 B 的裸写。** `lifecycle.py` 已有 `_safe_write_with_backup` + 实例锁（§1.3），身份页保存 MEMORY.md 时直接复用即可；若图省事调 `agent/memory.py:235 write_memory`，就会绕过备份与锁——这是本次最容易踩的坑。
3. **注入侧无第二道闸**：`context.py:232-235` 读盘后原样注入。正常路径下落盘侧已截断（`lifecycle.py:137-138`），但**用户手工写入一条超长 MEMORY.md 会直接全额进入 system prompt**，且落在 `---` 连接的无缓存可变区。身份页正是要开这个口子，所以需要补注入侧兜底。
4. **`_DREAM_CONTENT_PATHS` 与身份页可编辑文件集重叠**（`SOUL.md` / `USER.md` / `memory/MEMORY.md`）——这三个文件同时被自动流程写入和用户手工编辑，是冲突高发区，需要统一的并发/覆盖策略。
5. 身份页把 `AGENT.md` 标为「需编译」，但 **nanobot 没有编译器**。该 badge 在当前后端下无法兑现。

---

## 五、可借鉴的设计取舍（建议）

按「对轻量 agent 框架的性价比」排序：

1. **把前端 `charMax` 与后端 `MEMORY_MD_MAX_CHARS` 统一到同一个常量。** nanobot 已有 `truncate_memory_md`（`lifecycle.py:242`，规则段优先保留），**不需要新写算法**——只需：① 保存时复用该函数做校验/截断；② 让前端 `IdentityView.tsx:386` 的 `charMax = 1500` 从后端常量派生。当前两处都是 1500，数值巧合一致但**没有任何机制保证不漂移**，这是身份页开放编辑后第一个会暴露的问题。
2. **手工编辑采用「备份 + 明示会被覆盖」而非合并。** openakita 验证过"无 diff 无合并 + `.bak`"这条路，代价可接受且实现简单；但 UI 必须把语义说清（badge 已有「自动重生成」，建议编辑器再加一行提示）。
3. **pinned rules 独立通道值得保留。** openakita 将其与检索结果分开（`builder.py:1988-1993` 注释理由：与自动检索结果合并会污染无关任务）。nanobot 目前没有 pinned 概念，若后续要做"用户明确要求长期遵守的规则"，应独立于 L4，不要混进召回结果排序。

**不建议现在做的**：L5 关系图谱、自适应预算、PromptCompiler。前两者是 openakita 在更大上下文窗口下的优化，nanobot 当前连 L2 截断都没有，投入产出比低；PromptCompiler 是独立子系统（已确认与本次"文件读写 + 重载"范围解耦）。

---

## 结论

- nanobot 的注入链路**不是从零开始**：L4 自动召回（含门禁、失败隔离、cited_memories 副产物）已经建好且与 openakita 语义对齐。
- 真正的缺口比初稿判断的**窄**：体量控制与写入加固在落盘侧**已经具备**（`truncate_memory_md` / `_safe_write_with_backup`），缺的是**注入侧兜底**与**手工编辑的语义定义**。
- 本次范围内需要处理的只有两件事：① 身份页写入时复用路径 A 的加固语义，并把 `charMax` 常量统一；② 明确并告知「手工编辑会被自动重生成覆盖」。
- 其余（L0 / L1 / L3 / L5 分层、自适应预算、PromptCompiler）建议延后——它们是 openakita 在大上下文窗口下的优化，nanobot 当前没有非做不可的理由。

## 已核实（初稿标注的「待核实」已全部消解）

- ✅ `scratchpad_writer.py` / `experience_extractor.py` → **有产出无注入**，`context.py` 全文无引用（见 §1.4）。
- ✅ MEMORY.md 重生成入口 → `MemoryLifecycle.refresh_memory_md_sync`（`lifecycle.py:101`），触发方式为**手动 + 防抖异步**（`schedule_refresh_md`，`lifecycle.py:162-187`，`REFRESH_DEBOUNCE_SECONDS`）。
- ✅ **WebUI 已有现成入口，身份页可直接复用**：`nanobot/webui/memory_api.py:666 refresh_memory_md(services)`，且已在 `nanobot/webui/gateway_services.py:83` 以 `refresh_memory_md=partial(memory_api.refresh_memory_md, services)` 注入。身份页的「重载」按钮不必新建链路。

## Next

- 需要写 spec → 说「写方案」或「写计划」
- 直接进入实施计划 → 说「写计划」
- 补充调研 → 指向上面「待核实」两项即可
