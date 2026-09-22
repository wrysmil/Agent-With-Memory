# MEMORY.md 由 SQLite 派生 + Dream 按钮歧义改造

- 日期：2026-09-17
- 状态：spec，待用户确认
- 范围：`nanobot`（feature/memory-system 分支）`memory/` + `agent/memory.py` + `webui/memory_routes.py` + `webui/src/components/settings/memory/` + `templates/agent/dream.md`
- 引用调研：`.ai-runtime-artifacts/research/2026-09-16-openakita-identity-config-and-memory-md-research.md`、`2026-09-17-openakita-recall-parity-and-fts5-tokenizer-research.md`

---

## 〇、背景与动机

**两个独立但同源的问题**：

1. **MEMORY.md 由 SQLite 派生** —— 2026-09-16 调研发现 openakita 把 SQLite `memories` 表当作**唯一真相源**，MEMORY.md 是从 `query_semantic(scope='user', min_importance=0.5, ...)` 程序化生成的"人类可读视图"（`openakita/memory/lifecycle.py:1384-1458`）。nanobot 缺这条路径，当前 MEMORY.md 完全靠 Dream 让 LLM 直接改写（`nanobot/agent/memory.py:567-617`、`templates/agent/dream.md`）。

2. **Dream 按钮歧义** —— WebUI 「记忆整理」分类下的「执行 Dream」按钮（i18n key `commands.dream.title="执行 Dream"`，`webui/src/i18n/locales/zh-CN/common.json:1381-1384`）调用 `cmd_dream`（`nanobot/command/builtin.py:451-528`）。该命令让 LLM **只读**四份文件 `SOUL.md / USER.md / memory/MEMORY.md / skills/<name>/SKILL.md` 并**直接 `edit_file`/`write_file`/`apply_patch`** 改它们（`MemoryStore.build_dream_tools()`，`memory.py:577-618`）。用户看到「Dream」两字会以为点了"记忆系统自动整理"，但实际上 LLM 在改文件，**且这条路径与 SQLite 记忆系统完全无交集**。

**这两个问题之所以必须同批设计**：openakita 的 MEMORY.md 是 SQLite 的派生物（`manager.py:424-426` 自述「SQLite 是唯一真相源，MEMORY.md 是派生产物」）。如果只做 Dream 路径降级而不接 SQLite 派生，MEMORY.md 会从两个独立来源（LLM 直改 vs SQLite 重建）争抢写入；如果只接 SQLite 派生而不动 Dream，按钮的歧义仍在。**两者一起做才能闭环**。

---

## 一、Dream 的本质与歧义根因（Q1）

### 1.1 必须严肃区分的三件事

| 概念 | nanobot 当前 | openakita 对比 | 决策 |
|---|---|---|---|
| **Dream（手动触发）** | `/dream` slash command → 调 `templates/agent/dream.md` system prompt → LLM 用 restricted filesystem tools **直接 read/write `SOUL.md / USER.md / memory/MEMORY.md / skills/<name>/SKILL.md`**。本质是一次"LLM 写文件的会话" | openakita **没有**这个机制 | **降级为"生成草稿"**（见 §四） |
| **idle 提取（后台）** | 监听用户输入间隔 → 调 `MemoryExtractor.extract_incremental()` → 写 SQLite `memories` 表。**不接触 MEMORY.md**（`memory/extractor.py:611`、`models.py:46-71`） | openakita 的 `process_unextracted_turns` 同源 | 保持不变 |
| **daily consolidation（每日整理）** | 当前 nanobot 用 `DreamConfig.interval_h=2` 每 2 小时跑一遍 Dream（`schema.py:54-80`）。**与 openakita 的"凌晨 3 点 + `consolidate_daily`"语义不同**：nanobot 的 Dream 不是真正的去重/合并/淘汰 | openakita `consolidate_daily` 走 `LifecycleManager.consolidate_daily`（`lifecycle.py:145`） | **保持 Dream 周期但语义改为"草稿生成"，新增 `consolidate_daily` 路径**（见 §三） |

### 1.2 关键澄清

**Q：WebUI「执行 Dream」按钮到底干了什么？**

A：用户按按钮 → WebUI 把 `/dream` 文本投递到聊天 session → `cmd_dream` 在 `asyncio.create_task(_run_dream())` 里跑（`builtin.py:525`）→ 调 `store.build_dream_prompt()` 拼模板 + `history.jsonl` 未处理条目（`memory.py:545-565`）→ `loop.process_direct(prompt, session_key="dream:20260917-...", ephemeral=True, tools=store.build_dream_tools(), runtime=loop.dream_runtime())`（`builtin.py:485-492`）→ LLM 在该会话内用 `read_file / edit_file / write_file / apply_patch` 改写 `MEMORY.md / SOUL.md / USER.md`，`allowed_write_allowed_files` 把范围卡死（`memory.py:592-617`）。完成后 `git.auto_commit("dream: manual run", diff_body)` 落 commit（`builtin.py:514-518`）。

**Q：Dream 的产出跟 SQLite 记忆系统有关系吗？**

A：**没有任何关系，两条路径互不读写。**

| 路径 | 读什么 | 写什么 |
|---|---|---|
| Dream | `history.jsonl` + `MEMORY.md/SOUL.md/USER.md` | `MEMORY.md/SOUL.md/USER.md`（LLM 决定）+ 落 git commit |
| idle 提取 | `session.messages` | `memories` 表（SQLite） |
| 检索 API | `memories` 表 + 向量索引 | 只读 |

**这就是歧义的根因**：按钮让用户以为"点了 Dream 记忆系统就整理好了"，但实际上：

1. SQLite 记忆系统**不感知** Dream 产出 —— `search_memories()` 只走 SQLite + FTS5 + 向量检索（`nanobot/memory/repository.py:468-560`），永远查不到 Dream 写进 MEMORY.md 的事实
2. Dream 的产出**不进**向量检索 —— `vector/indexer.py:79` 的 `index_memory_best_effort()` 只索引 SQLite `memories` 表
3. 跟 `MEMORY.md 改由 SQLite 派生` 的新设计**是冲突的** —— 两边都在抢 MEMORY.md 这个文件

### 1.3 当前 `MEMORY.md` 的注入位置（`nanobot/agent/context.py:230-235`）

```python
if memory and not self._is_template_content(memory, "memory/MEMORY.md"):
    parts.append(f"# Memory\n\n## Long-term Memory\n{memory}")
```

- **唯一读取点**，且**单行拼接**，无 token 预算（vs openakita 走 `min(budget//2, 500) * 3` 即 `min(1500, ...)` 字符预算，见 2026-09-16 调研 §6.5）
- `_is_template_content(memory, "memory/MEMORY.md")` 判断若内容等于出厂模板则**跳过注入**（`context.py:107, 230-233`），但实际 MEMORY.md 是被 Dream 改过的，不会被跳过

---

## 二、改造目标与三档建议（Q2）

### 2.1 三档方案对照

| 档位 | 改动 | 用户体验 | 风险 | 推荐 |
|---|---|---|---|---|
| **A. 按钮重命名 + hint（最小）** | 「执行 Dream」→「合并对话到 MEMORY.md」；按钮下方加 hint「此操作会让 LLM 直接改写 4 个 MD 文件，与自动记忆系统互不影响」 | 用户看到文案不再懵 | 两条路径冲突仍在，未来仍要做 B/C | ❌ |
| **B. 按钮改为"清理 MEMORY.md 草稿" + SQLite 派生正式生效（推荐）** | Dream 路径**降级为"生成 MEMORY.md.draft 候选草稿"**，落 `memory/MEMORY.md.draft`，用户手动 review 后合并；MEMORY.md 真值改由 `refresh_memory_md` 从 SQLite 派生；Dream 提示词改写，不再直接覆盖真值文件 | 按钮语义清晰：Dream 是"LLM 建议"，SQLite 是"事实"。两者边界明确 | 需改 Dream prompt、文件 IO、新增 draft 机制 | ✅ |
| **C. 彻底删除 Dream 路径（激进）** | 完全移除 `/dream` command 与 `templates/agent/dream.md` | 最干净 | 现有用户依赖（已写了不少 `SKILL.md`，且 periodic Dream 周期任务仍在跑）会破窗 | ❌ |

### 2.2 推荐 B 档 —— 理由

1. **保留向后兼容**：现有用户已经在 `skills/<name>/SKILL.md` 累积了 Dream 写出的工作流模板；periodic Dream（`DreamConfig.interval_h=2`，`schema.py:54-80`）也是用户可见的"每隔 2 小时自动整理"承诺。删了 Dream 等于把功能后撤。
2. **明确两个语义边界**：按钮文案 + draft 文件 + SQLite 派生三件套配套出现，用户一眼能看明白"自动整理来自 SQLite，手动 Dream 只是建议"。
3. **与 openakita 的渐进式路径一致**：openakita 是从无到有构建 SQLite 派生；nanobot 是从有到更稳。新设计在保留 LLM 创造力的同时，让 SQLite 事实成为基线。

### 2.3 B 档明确做的四件事

1. **MEMORY.md 真值改由 SQLite 派生** —— 新增 `MemoryLifecycle.refresh_memory_md()`，照搬 openakita `lifecycle.py:1384-1458` 的语义（见 §三）
2. **Dream 降级为生成 draft** —— Dream 写到 `memory/MEMORY.md.draft`（**不再直接写 `MEMORY.md`**），`/dream-log` 等其它命令不动；Dream 仍可生成 `skills/<name>/SKILL.md`（这部分跟 SQLite 无关）
3. **Dream 提示词重写** —— 去掉 "edit SOUL.md/USER.md/MEMORY.md" 路由，改为 "read + propose MEMORY.md.draft"，并加"不要覆盖已有事实，与 SQLite 已有的一致性自检"段
4. **前端「立即刷新 MEMORY.md」按钮** —— 在 Settings → Memory 页面加按钮调新端点 `POST /api/memory/refresh-md`，触发 `refresh_memory_md`；文案明确"从 SQLite 重新生成 MEMORY.md"

### 2.4 不做的事（与 B 档配套）

- **不做** IdentityView 整页（用户原话"优先 MEMORY.md 改由 SQLite 派生，IdentityView 后置"）
- **不做** 编译管线（`PromptCompiler`）—— 不在本次 spec
- **不做** Layer 0（记忆系统自描述 prompt 注入，openakita `prompts/memory/guide.md`）—— 独立后续工单
- **不改** SOUL.md / USER.md 的注入机制 —— 本次只动 MEMORY.md

---

## 三、MEMORY.md 由 SQLite 派生的设计（Q3）

### 3.1 数据源契约

照搬 openakita `lifecycle.py:1384-1458`，但针对 nanobot 的 SQLite schema 适配。

**确认 schema 字段**（`nanobot/memory/database.py:32-59`）：

| 字段 | 类型 | 默认 | 用途 |
|---|---|---|---|
| `id` | TEXT PRIMARY KEY | uuid | 内部 |
| `content` | TEXT NOT NULL | — | 记忆内容（**写入 MEMORY.md 的文本**） |
| `type` | TEXT NOT NULL | `'fact'` | 应用层用 `MemoryType` enum 校验（`models.py:11-18`），6 个值：`fact / preference / skill / error / rule / experience` |
| `importance_score` | REAL NOT NULL | `0.5` | **0-1**，索引 `idx_memories_importance DESC`（`database.py:63`） |
| `scope` | TEXT NOT NULL | `'global'` | 应用层判断；**支持 `'user'`** 但无 CHECK 约束 |
| `source` | TEXT NOT NULL | `'manual'` | 应用层判断；支持 `'manual' / 'idle' / 'session_end' / 'context_compress' / 'topic_change' / 'deletion' / 'daily_consolidation'` |
| `subject` / `predicate` | TEXT | `''` | 关系抽取 |
| `tags` | TEXT(JSON) | `'[]'` | 标签 |
| `metadata` | TEXT(JSON) | `'{}'` | 元数据 |

**关键差异 vs openakita**：

1. **`content_hash` 字段不存在**（openakita 也没存，是计算后 in-memory 用的）—— `nanobot/memory/filters.py:140-157` 的 `compute_content_hash(content, subject, predicate)` 函数现成可用
2. **`scope='user'` 的过滤依赖应用层判断** —— 数据库层无 CHECK，应用层 `repository.list_memories()` 默认按 `workspace_id` 过滤，要加 `scope='user'` 过滤需扩展（见 §3.2）
3. **`source='profile_fallback'` 不存在**（openakita 用来排除会话内非结构化档案补充）—— openakita 的 `ff_filter`（`lifecycle.py:597-598`）在 nanobot 可以**不做**

### 3.2 过滤条件（与 openakita 对齐 + 适配）

```python
memories = store.list_memories(
    conn,
    type=None,                            # 不在 SQL 过滤 type，由 Python 端按 6 段分组
    workspace_id=services.workspace_id,
    scope="user",                         # ★ 新增：过滤 scope='user'
    min_importance=0.5,                   # ★ 新增：importance_score >= 0.5
    limit=200,                            # 与 openakita 一致
    order_by="importance",                # 按 importance_score DESC
)
```

**实现要点**：

- `repository.list_memories`（`nanobot/memory/repository.py:152-208`）**当前不接 `scope` 和 `min_importance`** —— 需要扩展加这两个参数（见 §八 WU-2.1）
- 应用层再加一层 `source != 'profile_fallback'`（即使 nanobot 不产此 source，作为兜底保留，参见 openakita `lifecycle.py:597-598` 的 `ff_filter`）
- 同 `(type, sha1(content)[:16])` 去重（openakita `lifecycle.py:601-610`，用 `compute_content_hash()` 但**只看 content** —— 与 nanobot `filters.py:140` 不同；见 §3.4）

### 3.3 输出格式（固定结构）

```markdown
# 核心记忆

## 偏好
- ...
## 规则
- ...
## 事实
- ...
## 教训
- ...
## 技能
- ...
## 经验
- ...
```

每类 **top-4**，按 `importance_score` 降序，总字符封顶 **1500**（与 openakita `MEMORY_MD_MAX_CHARS` 对齐，来源：`openakita/memory/types.py:82-89`）。

**实现要点**：

- 类型→中文标签映射硬编码（参考 openakita `lifecycle.py:613`）
- 标题用 `## ` + 中文标签；空类不输出
- 单条格式 `f"- {mem.content}"`；超出 1500 字符时**截断最后一条**（避免截到中间）

### 3.4 写入策略

照搬 openakita `_safe_write_with_backup`（`lifecycle.py:102-125`），但**独立实现在 nanobot**（openakita 那段不能直接 import）：

```python
def _safe_write_with_backup(path: Path, content: str) -> None:
    """先备份 MEMORY.md → MEMORY.md.bak，再写入；写失败则恢复。
    
    与 openakita 一致：MEMORY.md.bak 只保存"上一次"内容（不滚多版本）。
    """
    backup = path.with_suffix(path.suffix + ".bak")
    if path.exists():
        shutil.copy2(path, backup)         # 备份现有内容
    try:
        path.write_text(content, encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to write {path}: {e}")
        if backup.exists():
            shutil.copy2(backup, path)     # 写失败时回滚
            logger.info(f"Restored {path} from backup")
        raise
```

**两个安全门槛**（与 openakita 一致，`lifecycle.py:1450-1457`）：

1. **内容 < 10 字符跳过写入** —— 防止意外清空（`if len(new_content.strip()) < 10: return`）
2. **总字符 > 1500 走截断** —— 段落优先级：`## 规则` 类优先保留，其它截断。**直接复用** openakita `truncate_memory_md` 的实现思路（`types.py:94-143`）但**独立写**（不引 openakita），因为 Python 移植无成本

### 3.5 触发时机（三处，与 openakita 对齐）

| 触发点 | 实现位置 | 说明 |
|---|---|---|
| **手动（WebUI 按钮）** | `POST /api/memory/refresh-md` → `lifecycle.refresh_memory_md(workspace)` | 新增路由；前端「立即刷新 MEMORY.md」按钮调用 |
| **自动（每次 SQLite memories 写入后异步触发）** | 在 `memory_api.create_memory / update_memory / delete_memory` 三个入口（`memory_api.py:375-492`）的 SQLite 落库**之后**加 `schedule_refresh_md(workspace)` 调用 | **带去抖**：60 秒内多次变更合并为一次（用 `asyncio.create_task` + workspace 维度 `last_refresh_ts` 即可） |
| **Dream 降级后不再触发** | Dream 命令**不**调 `refresh_memory_md`（Dream 只写 draft） | 消除"两条路径都改 MEMORY.md"的冲突 |

**去抖实现**（在 `MemoryLifecycle` 上）：

```python
class MemoryLifecycle:
    _refresh_tasks: dict[str, asyncio.Task] = {}
    _last_refresh_at: dict[str, float] = {}
    
    async def schedule_refresh_md(self, workspace_id: str, debounce_seconds: float = 60.0) -> None:
        """去抖：60 秒内多次调用只触发一次实际 refresh。"""
        now = time.monotonic()
        last = self._last_refresh_at.get(workspace_id, 0.0)
        if now - last < debounce_seconds:
            return  # 在去抖窗口内，依赖下一次"窗口外"调用覆盖
        self._last_refresh_at[workspace_id] = now
        # 取消未完成的旧任务
        old = self._refresh_tasks.pop(workspace_id, None)
        if old and not old.done():
            old.cancel()
        self._refresh_tasks[workspace_id] = asyncio.create_task(
            self.refresh_memory_md(workspace_id)
        )
```

### 3.6 失败 / 降级语义

照搬 openakita（`manager.py:2564-2574`）：`refresh_memory_md` 抛异常时**不阻塞** 内存写入主路径；记录 `WARNING` 日志；下一轮 60s 窗口会自动覆盖。

- **不回滚 SQLite** —— refresh 是派生产物，不应污染唯一真相源
- **不回滚 draft** —— `MEMORY.md.draft` 由 Dream 单独管，与本次 refresh 无关

### 3.7 stats 端点扩展（最小成本）

`memory_api.stats_payload`（`memory_api.py:238-292`）当前返回 `{total, by_type, ...vector_*}`，**新增一个字段**：

```python
{
    ...,  # 现有字段
    "memory_md": {
        "last_refresh_at": <ISO8601 or null>,   # 上次 refresh_memory_md 完成时间
        "last_refresh_trigger": "manual" | "auto" | null,
        "draft_exists": bool,                    # MEMORY.md.draft 是否存在
        "draft_age_seconds": int,                # draft 距今秒数（None if 不存在）
    },
}
```

`last_refresh_at` 由 `MemoryLifecycle._last_refresh_at[workspace_id]` 维护（ISO8601 UTC 字符串，不用 monotonic 时间戳）；`draft_*` 直接 stat 文件即可。**前端在 Settings → Memory 页面用这两个字段渲染「上次刷新：xx 前」+「有草稿可合并」提示**。

---

## 四、Dream 路径降级方案

### 4.1 改动清单

| 项 | 文件 | 现状 | 改动 |
|---|---|---|---|
| **Dream 写文件白名单** | `nanobot/agent/memory.py:592-617` | `editable_files = [self.memory_file, self.soul_file, self.user_file]` | 改为 `editable_files = [self.soul_file, self.user_file, self.draft_file]`，**移除 `self.memory_file`，新增 `self.draft_file = self.memory_dir / "MEMORY.md.draft"`** |
| **Dream 模板 prompt** | `nanobot/templates/agent/dream.md`（109 行） | 现 prompt 让 LLM "edit SOUL.md/USER.md/MEMORY.md/SKILL.md" | **改写**：去掉 "edit MEMORY.md" 段落，加 "**read MEMORY.md → write MEMORY.md.draft**" 段落；保留 SKILL.md 创建 |
| **Dream 提交信息** | `nanobot/agent/memory.py:706-721` `build_dream_commit_message` | diff_body 含 MEMORY.md | 仍可，但 **MEMORY.md 不再被 Dream 修改**，git diff 只会有 `SOUL.md/USER.md/SKILL.md` —— 这是预期行为 |
| **Dream 命令成功消息** | `nanobot/command/builtin.py:497-518` | `content = f"Dream completed in {elapsed:.1f}s."` | 文案改为："Dream 草稿已生成：`memory/MEMORY.md.draft`，可手动合并。" |
| **Dream 周期任务** | `nanobot/config/schema.py:54-80` `DreamConfig` | 每 2 小时跑 Dream | **保持不变** —— Dream 仍是周期任务，只是不再改 MEMORY.md 真值 |

### 4.2 新 Dream prompt 关键段落（建议写法）

```markdown
## File routing

| File | Path | When |
|------|------|------|
| SOUL.md | `SOUL.md` | 行为规则变化时 edit |
| USER.md | `USER.md` | 用户偏好/身份变化时 edit |
| **MEMORY.md.draft** | `memory/MEMORY.md.draft` | **候选核心记忆草稿**。每次 Dream 都覆盖此文件，由用户手动合并 |
| ~~MEMORY.md~~ | ~~`memory/MEMORY.md`~~ | **禁止写入**。MEMORY.md 由记忆系统从 SQLite 自动生成，你的草稿只是建议 |
| SKILL.md | `skills/<name>/SKILL.md` | 复现 ≥2 次的工作流 |

## MEMORY.md.draft generation rules

1. 启动时 **read MEMORY.md**（看 SQLite 派生的当前真值，避免与已有事实冲突）
2. 分析 `history.jsonl` 未处理条目
3. 产出 `MEMORY.md.draft` 内容（用 MEMORY.md 同样格式：`# 核心记忆\n\n## 偏好\n- ...`）
4. **new**：若发现与 MEMORY.md 已有事实冲突，**在 draft 顶部加注释段** `# Draft notes - 2026-09-17\n潜在冲突：...`
5. write `MEMORY.md.draft`（覆盖原 draft）

## Verification

你的最终 summary 只列已确认的 file 写入。每个写入必须有成功的 tool result 支撑。
**禁止声称** "已写入 MEMORY.md"（你没权限也没必要）。
```

### 4.3 draft 合并机制（用户手动）

**本次 spec 不实现自动合并**。手动合并流程：

1. 用户在 Settings → Memory 页面看到 `draft_exists=true`
2. 用户手动 `cat memory/MEMORY.md.draft` → 与 `memory/MEMORY.md` 对比 → 决定哪些条目值得写进 SQLite（用 `POST /api/settings/memory/memories/create` 接口）
3. SQLite 写入后 §3.5 的自动触发机制会**60 秒内**重新生成 MEMORY.md

**后续工单**：IdentityView 页面提供 draft diff 可视化 + 一键合并（独立工单，本次不做）。

---

## 五、不变量与兼容性

### 5.1 必须保留的不变量

1. **SQLite `memories` 表是唯一真相源**（2026-09-16 调研 §10.1.1 推荐）—— MEMORY.md 是派生产物
2. **`MemoryStore` 仍是纯文件 I/O 类**（`memory.py:60-744`）—— 新增 `MemoryLifecycle` 类负责"派生"，**不与 `MemoryStore` 混合**
3. **`MemoryType` enum 仍是 6 个值**（`models.py:11-18`）—— MEMORY.md 6 段结构与 enum 一一对应
4. **`compute_content_hash()` 仍是 (content, subject, predicate) 三元组**（`filters.py:140-157`）—— refresh_memory_md 的去重只看 content（与 openakita 行为一致，openakita 自己也没用 subject/predicate）
5. **`/dream` 命令路径不变** —— 只是其写文件白名单变了
6. **`MEMORY.md` 注入位置不变**（`context.py:235`）—— 只是内容来源从"LLM 直改"变为"SQLite 派生"
7. **GitStore 仍跟踪 4 文件**（`memory.py:90-92`）—— 但 Dream 跑完后 diff_body 不再有 MEMORY.md（这是预期）

### 5.2 与现有组件的接口

| 组件 | 接口 | 兼容性 |
|---|---|---|
| `/api/settings/memory/stats` | 现有 `fetch_stats()`（`memory_api.py:238`） | **扩展**：新增 `memory_md.*` 字段（向后兼容，前端可选读） |
| `/api/settings/memory/memories/{create,update,delete}` | 现有 actions（`memory_routes.py:140-178`） | **扩展钩子**：写入成功后调 `lifecycle.schedule_refresh_md(workspace)` |
| `/api/settings/memory/vector/{reindex,sync}` | 现有 actions | 不动 —— 派生路径与向量无关 |
| WebUI Settings → Memory 页面 | `MemorySection.tsx:1-123` | **扩展**：加「立即刷新 MEMORY.md」按钮 + 显示 `memory_md.*` 字段 |
| WebUI 「执行 Dream」按钮 | 命令面板（i18n `commands.dream.title`） | **文案改写**：「执行 Dream」→「生成 Dream 草稿」+ 「Dream 是 LLM 建议，最终真值由 SQLite 派生」 |

### 5.3 与 idle 提取 / 向量检索的关系

- **idle 提取**（`nanobot/memory/extractor.py:611` 触发）→ 写 SQLite `memories` → 触发 §3.5 自动 refresh → MEMORY.md 更新
- **向量检索**（`nanobot/memory/vector/indexer.py:79` 索引）→ 索引 SQLite `memories` → 与 MEMORY.md 派生**互不影响**（两条独立下游）
- **召回对齐**（2026-09-17 调研 `channels/episodes.py`、`channels/semantic.py`）→ **本次 spec 不动**，与本次改造独立

### 5.4 与 `DreamConfig.interval_h=2` 的关系

`schema.py:54-80` 的 `DreamConfig.interval_h=2` 默认每 2 小时跑 Dream 周期任务。**保持不变**：Dream 仍是周期任务，只是产物从"MEMORY.md 真值"变为"MEMORY.md.draft 草稿"。

- 周期任务跑完后**不动** `MEMORY.md`
- 周期任务跑完后**触发** `schedule_refresh_md(workspace)` 吗？**不触发**。理由：Dream 是 LLM 建议，不应该触发 SQLite 派生。SQLite 派生只在 SQLite 自身写入后触发（§3.5）
- 周期任务跑完后**也不动** `MEMORY.md.draft` 的 git commit 行为 —— `git.auto_commit` 仍按 `diff_body` 实际变更落 commit

---

## 六、非目标（明确不做）

1. **不做 IdentityView 整页**（用户原话："优先 MEMORY.md 改由 SQLite 派生，IdentityView 后置"）。SOUL.md / USER.md / POLICIES.yaml 的读写 API 与前端页面**不在本次范围**
2. **不做编译管线**（`PromptCompiler` / `_compile_with_rules` / `compile_all`）—— 2026-09-16 调研 §10.2.3 提到的编译模型可配置设计**不在本次范围**
3. **不做 Layer 0**（openakita 记忆系统自描述 prompt 注入）—— 独立后续工单
4. **不做 draft 自动合并 UI** —— IdentityView 页面提供 diff 可视化 + 一键合并；本次只到"告诉用户有 draft"
5. **不做 draft 冲突自动检测** —— 依赖用户在合并时人工对比
6. **不做 USER.md 由 SQLite 派生** —— openakita 的 `refresh_user_md`（`lifecycle.py:1464-1548`）有格式冲突 bug（2026-09-16 调研 §6.3），**不照抄**；USER.md 维持当前"由 update_user_preference 唯一写入"
7. **不做 SOUL.md 由 SQLite 派生** —— openakita 也没做
8. **不做 chatbot ↔ memory 提示词重写** —— MEMORY.md 注入格式（`context.py:235`）的 `# Memory\n\n## Long-term Memory\n{memory}` 不动（虽然改一下更合规，但避免触动其它路径）
9. **不做 schema 迁移工具** —— `compute_content_hash()` 现在不存表里，refresh_memory_md 算 SHA-1 即可；不引入新列

---

## 七、风险与回退

### 7.1 风险表

| 风险 | 影响 | 缓解 |
|---|---|---|
| **`/dream` 写 MEMORY.md 改了 2 年的用户突然发现 Dream 不再覆盖真值** | 用户困惑 / 投诉 | Dream 成功消息明确告知 "Draft 已生成到 `MEMORY.md.draft`，可手动合并"；保留 git 审计（`/dream-log`）让用户看到 Dream 仍跑 |
| **`schedule_refresh_md` 60s 去抖窗口内大量写入合并，可能丢失中间态** | SQLite 写入后 MEMORY.md 不立即更新 | 用户可通过「立即刷新」按钮强制立即 refresh；前端 stats 显示 `last_refresh_at` 让用户看见 |
| **`truncate_memory_md` 段落优先级对中文不友好**（openakita 关键词表 `{"重要规则","规则","rules","行为规则","用户规则"}`，2026-09-16 调研 §6.6） | 截断后可能丢失重要信息 | 本次 MEMORY.md 总字符上限 1500；按类型 top-4 已经限流；用户可通过"立即刷新"按钮触发多次重排 |
| **Dream prompt 改写不彻底，LLM 仍尝试直接写 MEMORY.md** | 误改文件 | Dream 工具 `editable_files` 白名单**硬约束** MEMORY.md 不在写列表（`memory.py:592-617`），即使 prompt 说要写也写不进去 |
| **`last_refresh_at` 时钟漂移** | stats 显示不准 | 用 ISO8601 UTC 字符串记录，不用 monotonic；每次 refresh 完成时刷新 |
| **Periodic Dream 周期任务（2h）与 idle 提取写入的 SQLite race** | Dream 周期跑时若 idle 提取刚写完 SQLite，Dream prompt 会基于旧 MEMORY.md 生成 draft | 这是预期行为 —— draft 本就是"建议"，合并时用户会看到最新 SQLite 状态 |
| **破坏现有 `MemoryStore.git` 审计记录** | git log 缺 MEMORY.md 变更 | 接受：MEMORY.md 真值由 refresh_memory_md 写，无 git commit；但**手动写 MEMORY.md** 仍由 GitStore 跟踪（如果用户绕过 WebUI 直改文件） |

### 7.2 回退策略

**保留 B 档的"软回退"路径**（不删代码，只关功能）：

1. **回退 1：禁用 SQLite 派生** —— 在 `MemoryConfig` 加 `refresh_from_sqlite: bool = True`；关掉后 Dream 仍按旧行为写 MEMORY.md 真值（恢复当前状态）
2. **回退 2：禁用自动触发** —— `schedule_refresh_md` 加 `auto_schedule=True`；关掉后只剩手动「立即刷新」按钮
3. **回退 3：完全回滚到现状** —— 移除 `MemoryLifecycle` 类 + 撤销 `memory.py` 的 `editable_files` 改动 + 还原 Dream prompt

**"软回退"开关在 config**：保留 6 个月内可用，超期代码可删。

---

## 八、WU 拆分建议

| WU ID | 范围 | 文件 | agent_role | 依赖 |
|---|---|---|---|---|
| **WU-MD-1** `MemoryLifecycle` 类 + `_safe_write_with_backup` + `refresh_memory_md()` 主体 | 新文件 `nanobot/memory/lifecycle.py`（约 200-300 行，照搬 openakita `lifecycle.py:1384-1458` 思路） | `coder` | — |
| **WU-MD-2** repository 扩展：`list_memories` 加 `scope` / `min_importance` 参数 | `nanobot/memory/repository.py:152-208` + 单测 | `coder` | — |
| **WU-MD-3** `MemoryLifecycle.schedule_refresh_md` 去抖 + 在 `memory_api.create_memory/update_memory/delete_memory` 三处加钩子 | `nanobot/memory/lifecycle.py` + `nanobot/webui/memory_api.py:375-492` | `coder` | WU-MD-1 |
| **WU-MD-4** `POST /api/memory/refresh-md` 路由 + 操作协议扩展 + MEMORY_ACTION_NAMES 加 `memory-refresh-md` | `nanobot/webui/memory_routes.py:31-65, 91-190` + `nanobot/webui/settings_routes.py:148-167, 169-178` + 单测 | `coder` | WU-MD-1 |
| **WU-MD-5** `stats_payload` 扩展 `memory_md.*` 字段 | `nanobot/webui/memory_api.py:238-292` + 单测 | `coder` | WU-MD-1 |
| **WU-MD-6** WebUI「立即刷新 MEMORY.md」按钮 + 显示 stats 字段 + i18n 11 语种 | `webui/src/components/settings/memory/MemorySection.tsx` + `webui/src/i18n/locales/*/common.json` | `coder`（UI 子任务） | WU-MD-4, WU-MD-5 |
| **WU-MD-7** Dream 工具白名单改 `editable_files`：`MEMORY.md` → `MEMORY.md.draft` | `nanobot/agent/memory.py:577-618` + 单测 | `coder` | — |
| **WU-MD-8** `templates/agent/dream.md` 改写（去掉 MEMORY.md 路由，加 draft 段落） | `nanobot/templates/agent/dream.md`（109 行 → 约 130 行） | `implementer`（轻量） | WU-MD-7 |
| **WU-MD-9** `cmd_dream` 成功消息文案改写 + 文案 i18n 11 语种 | `nanobot/command/builtin.py:497-518` + i18n | `implementer`（轻量） | WU-MD-7, WU-MD-8 |
| **WU-MD-10** 端到端集成测试：手动 create → 自动 refresh → stats 更新 → 按钮调手动 refresh | `tests/memory/test_lifecycle_integration.py` + `tests/webui/test_refresh_md_route.py` | `test-engineer` | WU-MD-1 ~ WU-MD-6 |
| **WU-MD-11** 文档：`/dream` 与 MEMORY.md 关系说明更新到 `nanobot/templates/agent/dream.md` 注释 + 用户可见的 hint 文案 | `webui/src/i18n/locales/zh-CN/common.json:1381-1384` 等 | `implementer`（轻量） | WU-MD-8, WU-MD-9 |

**依赖链**：WU-MD-1 → {WU-MD-2, WU-MD-3, WU-MD-4, WU-MD-5} → {WU-MD-6, WU-MD-10}；{WU-MD-7} → {WU-MD-8} → {WU-MD-9, WU-MD-11}。两链可**并行委派**（refresh 路径 与 Dream 降级路径独立），最后在 WU-MD-10 集成测试合流。

**单测覆盖点**（每 WU 都需自带）：
- `refresh_memory_md` 在空库 / 全是 rule / 全是 fact / 混合 / 超 1500 字符 / content < 10 字符时行为
- `schedule_refresh_md` 去抖窗口（60s 内多次调用合并为一次）
- `_safe_write_with_backup` 写失败回滚
- `list_memories` scope / min_importance 过滤生效
- Dream 工具白名单不包含 MEMORY.md
- WebUI 按钮调通且 stats 显示正确

---

## 九、决策摘要（一页纸）

| 项 | 决策 |
|---|---|
| **Dream 本质** | `/dream` slash command → LLM 直接 read/write 四份 MD 文件；与 SQLite 记忆系统互不读写（**两条独立路径**） |
| **歧义根因** | 用户按「执行 Dream」以为点了"记忆系统整理"，实际是"LLM 改文件"；与 SQLite 派生新设计在文件层面冲突 |
| **推荐档位** | **B 档**（Dream 降级为生成 draft + SQLite 派生正式生效 + 前端加「立即刷新」按钮） |
| **MEMORY.md 真值来源** | `MemoryLifecycle.refresh_memory_md()` 从 SQLite `memories` 表派生，照搬 openakita `lifecycle.py:1384-1458` |
| **数据源** | `memories` 表，过滤 `scope='user'` + `min_importance >= 0.5` + limit 200 + `(type, content_hash)` 去重 + 按 importance 降序 + 每类 top-4 + 总 1500 字符 |
| **触发时机** | 三处：手动（按钮 → `POST /api/memory/refresh-md`）/ 自动（SQLite 写入后 60s 去抖异步）/ Dream 不触发 |
| **Dream 降级** | 写文件白名单 MEMORY.md → MEMORY.md.draft；prompt 改写；成功消息明确告知 draft 位置 |
| **draft 合并** | 本次不做自动合并；IdentityView 页面提供 diff 可视化（独立后续工单） |
| **stats 扩展** | `memory_md.{last_refresh_at, last_refresh_trigger, draft_exists, draft_age_seconds}` |
| **不变量** | SQLite 是唯一真相源 / MemoryStore 仍是纯文件 I/O / MemoryType 6 段 / context.py:235 注入点不变 / GitStore 仍跟踪 4 文件 |
| **不做** | IdentityView 整页 / 编译管线 / Layer 0 / draft 冲突自动检测 / USER.md 自动派生 / SOUL.md 自动派生 |
| **回退** | 6 个月内可软回退：`refresh_from_sqlite: bool = True` + `auto_schedule: bool = True` 双重 config 开关 |
| **WU 数** | 11 个，按两条独立依赖链并行委派，最后在 WU-MD-10 集成测试合流 |

---

## 十、附录：源码索引（便于实施时定位）

| 主题 | 文件 / 行 |
|---|---|
| `cmd_dream` 实现 | `nanobot/command/builtin.py:451-528` |
| `MemoryStore.build_dream_tools()` | `nanobot/agent/memory.py:577-618` |
| `MemoryStore.build_dream_prompt()` | `nanobot/agent/memory.py:545-565` |
| `templates/agent/dream.md` | `nanobot/templates/agent/dream.md`（109 行） |
| MEMORY.md 注入 | `nanobot/agent/context.py:230-235` |
| `_SKIPPABLE_DEFAULTS` | `nanobot/agent/context.py:107, 328-331` |
| `DreamConfig` | `nanobot/config/schema.py:54-80` |
| `MemoryStore.git` 跟踪 4 文件 | `nanobot/agent/memory.py:90-92` |
| `MemoryType` enum | `nanobot/memory/models.py:11-18` |
| `Memory` dataclass | `nanobot/memory/models.py:46-71` |
| `memories` 表 schema | `nanobot/memory/database.py:32-59` |
| `idx_memories_importance` 索引 | `nanobot/memory/database.py:63` |
| `compute_content_hash()` | `nanobot/memory/filters.py:140-157` |
| `repository.list_memories()` | `nanobot/memory/repository.py:152-208` |
| `memory_api.create_memory` | `nanobot/webui/memory_api.py:375-420` |
| `memory_api.update_memory` | `nanobot/webui/memory_api.py:437-481` |
| `memory_api.delete_memory` | `nanobot/webui/memory_api.py:484-492` |
| `memory_api.stats_payload` | `nanobot/webui/memory_api.py:238-292` |
| `MemorySettingsOperations` 协议 | `nanobot/webui/memory_routes.py:31-54` |
| `MEMORY_ACTION_NAMES` | `nanobot/webui/memory_routes.py:59-65` |
| `_SYSTEM_ROUTES` 路由表 | `nanobot/webui/settings_routes.py:148-167` |
| `_MEMORY_MUTATION_PATHS` | `nanobot/webui/settings_routes.py:169-178` |
| WebUI MemorySection | `webui/src/components/settings/memory/MemorySection.tsx:1-123` |
| i18n 「执行 Dream」key | `webui/src/i18n/locales/zh-CN/common.json:1381-1384`（共 11 语种） |
| openakita `refresh_memory_md`（参照） | openakita `memory/lifecycle.py:1384-1458` |
| openakita `_safe_write_with_backup` | openakita `memory/lifecycle.py:102-125` |
| openakita `truncate_memory_md` | openakita `memory/types.py:94-143` |
| openakita `MEMORY_MD_MAX_CHARS = 1500` | openakita `memory/types.py:82-89` |