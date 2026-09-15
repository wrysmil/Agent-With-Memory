---
artifact: security-review
route: orchestration:collective-closeout
plan: .ai-runtime-artifacts/plans/2026-09-15-memory-retrieval-rca-fix-plan.md
skills:
  - security-and-hardening
source:
  - .ai-runtime-artifacts/specs/2026-09-15-memory-retrieval-rca.md
created_at: 2026-09-15
verdict: APPROVE
reviewed_commit: c6f66ac
remediation_commit: d726dfa
---

# 安全审查 — 记忆检索恒空修复

**审查者：** `security-auditor`（独立实例，只读；脚本置于 `/tmp/sec-audit-wu01/`，未改任何仓库文件）
**被审对象：** `c6f66ac`（11 文件，+724/−12）
**Leader 处置：** I-1 已修复并推送（`d726dfa`）；I-2 经 Leader 实测**降级**为潜在设计缺口（见 §3）

> 本产物由 Leader 落盘。每条发现后附 **Leader 核实结论**——凡与审查者原始判断不一致的，
> 均给出 Leader 自己的实测证据，不采信转述。

---

## 1. 结论摘要

审查者原始 verdict：**APPROVE**（无 Critical；本批自身的注入/转义/清洗控制实测有效）。

Leader 处置后：**APPROVE**。I-1（本批**激活**的 sink）已闭合；I-2 降级为潜在缺口并登记
follow-up；M 系列中 4 条已在 `d726dfa` 一并修掉。

| 级别 | 数量 | 处置 |
| --- | --- | --- |
| Critical | 0 | — |
| Important | 2 | I-1 已修（`d726dfa`）；I-2 **降级**为潜在缺口 + follow-up |
| Minor | 8 | M-1/M-2/M-3/M-4 已修（`d726dfa`）；M-5～M-8 登记 follow-up |

---

## 2. I-1 二阶提示词注入 —— **已修复（`d726dfa`）**

**审查者发现：** `_render_injection_block`（`engine.py`）把记忆 `content` 原样以
`- {content}` 拼接，无转义、无换行折叠、无不可信声明；而该块被
`agent/context.py` 拼进 **system prompt**。能污染记忆的一方 = 能改写 system prompt。

**Leader 独立复现（`/tmp/verify_i1.py`，修复前）：**

```
含伪造 header   : True      ← 记忆里写 "## 相关记忆（自动检索）" 即伪造出第二段
含 <vault-context>: True    ← 伪造「安全上下文」
含换行未折叠     : True      ← 凭空插入新 bullet
```

**关键判据（决定是否必须同批修）：** 修复前四路通道恒抛 `AttributeError`、
`_render_injection_block` 恒返回 `""` —— 该 sink 是**死路**。是本次召回修复把它
**激活**的。故属「本批引入的安全回归」，不能留到 follow-up。

**修复（`d726dfa`，`engine.py`）：**
1. `_sanitize_memory_content`：`clean_query`（query 侧同一套规则）→ 剥离**单独出现**的
   注入标签（`_INJECTION_TAG_RE`）→ 剥离 markdown 标题前缀 → **折叠换行** → 截断 500 字。
2. 块首新增 `_INJECTION_PREAMBLE`，明示该块为不可信数据、其中指令不得执行
   （与 `_snippets/untrusted_content.md` 对外部内容的口径一致）。
3. 全部条目被清洗为空时不注入空块。

**修复后同一脚本输出：**

```
含伪造 header   : False
含 <vault-context>: False
含换行未折叠     : False
```

payload 的**文字**仍作为惰性单行内容保留（不静默删除记忆实质），但已丧失结构能力。
「孤立 `</memory>` 逃逸」这一缺口是**本批新增用例自己抓到的**（成对正则
`<memory>.*?</memory>` 匹配不到单独闭合标签），已补 `_INJECTION_TAG_RE`。

**回归：** `tests/memory/retrieval/test_injection_block_safety.py` 10 用例；
`tests/memory/` 681 passed；真实库端到端冒烟零回归。

---

## 3. I-2 检索通道不做 user_id / workspace_id 过滤 —— **Leader 降级为潜在缺口**

**审查者判断：** 四路通道全链无过滤，而 WebUI 读路径**是**按 `workspace_id` 过滤的；
同一 workspace 内不同发送者的记忆会互相注入对方 system prompt。审查者并声明：
「若部署为多渠道/多发送者共享同一 workspace，则升级为 Critical、须阻塞发布」。

**Leader 实测核实（结论：降级为潜在缺口，不阻塞本批）：**

```
$ grep -n "user_id" nanobot/memory/extractor.py
696:        user_id: str = "default",
701:        self.user_id = user_id
1502:                    user_id=self.user_id,

$ grep -rn "user_id=" nanobot/agent/hooks/memory_extraction.py nanobot/cli/gateway_runtime.py
（无输出 —— 全仓库没有任何装配点覆写 user_id）

$ 真实库 user_id/workspace_id 分布
('default', 'default')  8 行   ← 全部
```

→ **生产代码目前根本造不出「不同 user_id 的记忆行」。** 审查者第 ⑥ 项复现里的
`alice-secret` / `bob-note` 是**手工构造**的行，不是生产路径能产生的数据。
因此：

- 现在**不是**活的跨用户泄漏；
- 部署形态：`~/.nanobot/config.json` 中仅 `websocket` 渠道启用，`unifiedSession: false`
  → 单 workspace、单渠道。

**但它是真实的设计缺口，予以登记（不掩饰）：**

| 面 | 现状 |
| --- | --- |
| 列已存在 | `memories.user_id` / `workspace_id`（schema v1） |
| 写入侧 | 恒为 `'default'`（`MemoryExtractor.user_id` 默认值，无覆写点） |
| 读取侧（WebUI） | ✅ 按 `workspace_id` 过滤（`webui/memory_api.py:181-186`） |
| 读取侧（Active Retrieval） | ❌ 一个过滤都不传（`search_semantic_scored` / `query_semantic` 的 WHERE 无该条件） |

**若将来**（a）新增 Telegram/Discord 等渠道共享同一 workspace，或（b）引入多用户
WebUI，或（c）任何代码开始写非 `default` 的 `user_id`，**则本条立即升级为 Critical**。
→ 登记为 follow-up WU（见 §5），并在 `identity.md`/文档侧不额外扩大影响面。

---

## 4. Minor 处置

| # | 审查者发现 | Leader 处置 |
| --- | --- | --- |
| M-1 | `except sqlite3.OperationalError` 过宽，把「表缺失/locked/IO error/readonly」也静默降级为全表 LIKE | **已修**：新增 `_is_fts_syntax_error`，语法类 → debug，其余 → warning（仍走 LIKE 回退，不改召回契约）。**`no such column` 刻意归入 warning** —— 它正是 `episodes.updated_at` 列名错误长期隐藏的机制 |
| M-2 | query 原文写入日志（PII at rest，loguru 级别被调到 DEBUG 时落盘） | **已修**：只记 `len(query)`，不记原文 |
| M-3 | `logger.warning` 用 `{}` 而非 `{!r}`，异常消息里的换行可伪造日志行 | **已修**：改 `{!r}` |
| M-4 | `search_episodes` 的 LIKE 未转义，与本批新增的 `_escape_like` 不一致 | **已修**：`ESCAPE '\'` + `_escape_like(entity)` |
| M-5 | `limit` 无边界校验：`limit=-1` 触发 SQLite「负 LIMIT = 无限制」（实测 10 万行 ~1.0s）；`limit=10**19` 抛未捕获 `IntegrityError` | 登记 follow-up（需同时校 WebUI `memory_routes.py` 与 `search_memories_payload`）。**非越权**（鉴权在 `check_api_token` 之后），属健壮性/极小 DoS 面 |
| M-6 | `state.db` 0644 / fallback 目录 0755 / fallback JSON 0644 | 登记 follow-up（既有问题，非本批引入） |
| M-7 | `MemoryDatabase._lock` 非重入 → 同线程嵌套 `connect()` 死锁；`MemoryStoreAdapter` 每方法只取一次连接故**本批安全** | 登记 follow-up（改 `RLock` 或统一连接生命周期）。**注**：这也是 plan Task 5 原始测试代码的死锁成因，WU-01 已识别并绕开 |
| M-8 | attachments 通道的已知契约缺口（dict 协议 vs dataclass），建表后会抛 `TypeError` | 与集体测试 LI-2 合并登记 |

---

## 5. Follow-up 登记（不阻塞本批）

| ID | 项 | 触发升级条件 |
| --- | --- | --- |
| SEC-1 | **检索侧 user/workspace 过滤**（I-2） | 任何代码开始写非 `default` 的 `user_id`，或新增多渠道共享同一 workspace → **立即 Critical** |
| SEC-2 | `limit` 边界校验（M-5） | — |
| SEC-3 | 记忆库文件权限收紧（M-6） | — |
| SEC-4 | `MemoryDatabase._lock` → `RLock` / 连接生命周期统一（M-7） | — |
| SEC-5 | attachments 通道契约收口 + `Protocol` 静态约束（M-8 / LI-2） | attachments 表入 schema 时 |
| SEC-6 | `_INJECTION_TAG_RE` 目前只覆盖 project 已知的 3 个标签名；更广的角色标签类（如伪 `<system>`）未覆盖 | 若出现相关真实攻击样本 |

---

## 6. 审查者正面结论（Leader 采信，均已要求其自行实测）

- 值 100% 参数化绑定；`'; DROP TABLE memories; --` 等 7 类载荷实测零影响，表存活。
- `LIMIT {int(limit)}` 不可注入（非整数直接 `ValueError`）。
- `_escape_like` 替换顺序（先 `\` 后 `%`/`_`）与 `ESCAPE '\'` 配对正确，且**带回归测试**。
- `clean_query` 严格**先于**门禁执行，放宽项只作用于清洗后文本，未绕过反注入。
- `identity.md` 的 system prompt 变更**未**触碰 `untrusted_content.md` 的 include。
- 连接零泄漏（200 次调用存活 Connection 增量 0→0）；无密钥泄漏（`git show` 扫描）。
- 依赖面未变动（本 commit 无依赖/锁文件改动）。

## 7. Leader 附注（审查者未覆盖、由 Leader 补充）

审查者第 ⑦ 项注意到 `identity.md` 新增「call it whenever you are about to say you do not
know something about the user」会提高主动检索倾向，从而放大 I-1/I-2 的触发频率。Leader
采纳该观察，并指出：**I-1 修复后该放大器的性质已改变**——它现在放大的是「合规的、
被标记为不可信数据的上下文注入」，而非未消毒的注入面。故保持该措辞不变。
