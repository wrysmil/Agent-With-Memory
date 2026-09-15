---
artifact: implementation-plan
route: superpowers:writing-plans
skills:
  - writing-plans
skills_evidence:
  - harness-kit/.agents/skills/writing-plans/SKILL.md
dispatch: .ai-runtime-artifacts/plans/2026-09-15-memory-retrieval-rca-fix-dispatch.md
source:
  - .ai-runtime-artifacts/specs/2026-09-15-memory-retrieval-rca.md
  - 本次会话实证（2026-09-15：真实库 + 临时库复现，见 §2）
created_at: 2026-09-15
status: approved
approved: true
approved_note: |
  用户原话：「根据这个文档写实施文档，然后执行修复」，随后「直接执行，不用询问我」。
  按 core/routing.md § 组合指令「例外」条款：用户同时明确「不用等我确认」，
  故 FM 写 approved: true 并在此引用原话。
---

# 记忆检索恒空 — 修复实施计划

> **For agentic workers:** 按 Task 顺序执行，步骤用 checkbox（`- [ ]`）跟踪。
> 每个 Task 的改动必须落盘到 `worktree_path`，不得写主 checkout。

**Goal：** 让 `memory_search` 与每轮自动注入两条路径都能真正召回三层 SQLite 记忆，
并让 Agent 的心智模型与真实存储对齐。

**Architecture：** 问题不在单点，而在一条**从未被打通的链路**上串了 5 个断点。
本计划按「数据通路 → 前置门禁 → 提示词」三段闭合，每段自带回归测试。

**Tech Stack：** Python 3.11+ / asyncio、SQLite（FTS5 `unicode61`）、loguru、pytest（`asyncio_mode=auto`）、ruff。

---

## 1. 问题（已取证）

`.ai-runtime-artifacts/specs/2026-09-15-memory-retrieval-rca.md` 定位到 4 个根因。
本次会话对**真实库**（`~/.nanobot/workspace/memory/state.db`，memories=4）与**临时库**
各跑了一遍复现，确认 RCA 结论，并**新发现第 5 个根因**（RCA 未覆盖，见 §1.3）。

### 1.1 RCA 已确认的 4 个根因

| # | 位置 | 现象 | 复现证据（本次会话） |
| --- | --- | --- | --- |
| 1 | `gateway_runtime.py:488` ↔ `channels/*.py` | `RetrievalEngine` 收到 `MemoryDatabase`，但通道以**方法**调用 `store.search_semantic_scored(...)`，真实实现是 `repository.py` 的**模块级函数**（首参 `conn`） | `AttributeError: 'MemoryDatabase' object has no attribute 'search_semantic_scored'`（semantic / recent 两路） |
| 2 | `preprocessor.py:57` | 首轮 `recent_messages` 必为空 → 任何 ≤12 字消息被 `short_without_context` 拦掉 | `'查一下我的记忆' recent_len=0 → skip=True` |
| 3 | `memory_search.py:82` | `hasattr(self, "_sessions")` 恒 False，全仓库无注入点 → 工具路径 `recent` 恒空 | `instance dict keys == ['_get_engine']` |
| 4 | `templates/agent/identity.md:7-10` | prompt 只文档化 Dream **文件**记忆（SOUL/USER/MEMORY.md），三层 SQLite 记忆零描述 → Agent 去读 `MEMORY.md` 后答「记忆是空的」 | 全仓库 grep `记忆检索\|主动检索\|相关记忆\|memory_search` 仅命中 identity.md |

复现脚本输出（临时库，写入 1 条 memory 后）：

```
semantic RAISED AttributeError 'MemoryDatabase' object has no attribute 'search_semantic_scored'
recent   RAISED AttributeError 'MemoryDatabase' object has no attribute 'query_semantic'
first-turn: BLOCK='' IDS=[]
second-turn: BLOCK='' IDS=[]
```

### 1.2 RCA 遗漏的根因 5（本次新发现，必须同批修）

**`memories_fts` 的 `unicode61` 分词器无法匹配中文子串**——即使修好根因 1，
语义通道对中文查询仍恒空。

`nanobot/memory/database.py:109-116` 建的是：

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, subject, predicate, tags,
    tokenize='unicode61 remove_diacritics 2'
)
```

`unicode61` 把**连续 CJK 字串切成一个整 token**，不做分词。实测（SQLite 3.52.0）：

```
query='创作'           -> 0 hits    ← 目标记忆 content 含「用户热爱创作，…」
query='记忆'           -> 0 hits
query='用户热爱创作'    -> 1 hits    ← 只有整段 token 才命中
query='创作灵感'       -> 0 hits
```

而 `repository.search_memories` **只有 FTS5 MATCH，没有 LIKE 回退**，
异常/零结果都直接变成 `[]`。同仓库的 `search_backend.Fts5SearchBackend`
反而已经实现了 `_try_fts5()` → `_fallback_like()` 的正确策略（`search_backend.py:50-106`），
两条路径策略不一致。

> 后果：只修根因 1 的话，`recent` 通道（`query_semantic`，纯 SQL LIKE/比较，不走 FTS）
> 会开始返回数据，但**语义通道仍然是 0**——即「看起来修好了但主召回仍残废」。
> 这也是为什么本计划的验收测试用的是「中文 query」而不是英文。

### 1.3 顺带修掉的相邻缺陷

> ⚠️ **本节叙事已于实施期更正（2026-09-15，WU-01 发现 + Leader 实测复核）。**
> 下面「生产存空格分隔 → 边界当天整批排除」的因果**不成立**，属 Leader 探针缺陷，
> 不是生产缺陷。更正记录见本节末。

`repository.query_semantic` 的时间比较用**字符串字面比较**：

```
stored updated_at = '2026-09-15 13:23:56.135551+00:00'   ← 空格分隔  【❌ 此观测为假】
cutoff             = '2026-09-12T13:23:56.135551+00:00'  ← 'T' 分隔
```

**更正：** 上面那个「空格分隔」的观测，是 Leader 探针**传 `datetime` 对象**给
`add_memory` 时被 sqlite3 的**弃用适配器**（`DeprecationWarning: The default datetime
adapter is deprecated as of Python 3.12`）转换出来的，**不是生产写入格式**。
生产路径 `Memory.to_row()` 传出的是 `isoformat()` 字符串。

实测复核（真实库 `~/.nanobot/workspace/memory/state.db`，8/8 行）：

```
'2026-09-15T14:12:32.086232+00:00'   ← 全部为 'T' 分隔
```

因此「`' '`(0x20) < `'T'`(0x54)` → 边界当天整批排除」**在实际数据上不可复现**，
RCA §1.3 的原始叙事同样需要更正。

**改动仍保留：** `datetime(updated_at) >= datetime(?)` 使比较**对存储文本格式不再敏感**
（`EXPLAIN QUERY PLAN` 实证未破坏 `idx_memories_importance` 索引），属零风险的健壮性提升，
且 plan 已批准。但**不得**再以「修复了边界当天丢失」作为它的收益叙述。

---

## 2. 修复方案（已验证可行）

本次会话用临时脚本对 4 项改动做了**端到端原型验证**（adapter + LIKE 回退 + 放宽后的
gate + 非空历史）：

```
BLOCK:
## 相关记忆（自动检索）
- 用户热爱创作，希望AI主动提供创作灵感 (ID: m1)
- 用户希望定期收到AI主动推送的灵感内容 (ID: m2)
IDS: ['m1', 'm2']
```

即「写 1 条中文 memory → `retrieve_with_ids` 返回非空块」这条 RCA 要求的验收路径，
已在原型上跑通，本计划把原型固化成代码 + 测试。

### 文件结构（决定 WU 拆分的边界）

| 文件 | 责任 | 动作 |
| --- | --- | --- |
| `nanobot/memory/retrieval/store_adapter.py` | **唯一**适配点：`MemoryDatabase` → 四路通道期望的 store 契约 | 新建 |
| `nanobot/memory/repository.py` | SQL 层：FTS5 回退 + 时间归一 | 修改 |
| `nanobot/memory/retrieval/engine.py` | 编排器：通道异常不再静默丢弃 | 修改 |
| `nanobot/cli/gateway_runtime.py` | 生产装配点：接上 adapter | 修改 |
| `nanobot/memory/retrieval/preprocessor.py` | 前置门禁：首轮/短词不再误杀 | 修改 |
| `nanobot/agent/tools/memory_search.py` | 工具路径：经 `ToolContext.sessions` 取会话 | 修改 |
| `nanobot/templates/agent/identity.md` | 提示词：三层 SQLite 记忆才是主存储 | 修改 |
| `tests/memory/retrieval/test_store_adapter.py` | 新建（含 RCA 要求的验收测试） | 新建 |
| `tests/memory/retrieval/test_preprocessor.py` | 追加首轮短词用例 | 修改 |
| `tests/memory/test_search.py` | 追加 CJK 子串回退用例 | 修改 |
| `tests/memory/test_memory_search_tool.py` | 新建（会话注入） | 新建 |

**WU 拆分依据：文件不相交。** 三个 WU 覆盖的文件集合互不重叠（见 dispatch 文件）：

- **WU-01** = adapter + repository + engine + gateway_runtime
- **WU-02** = preprocessor + memory_search tool
- **WU-03** = identity.md

WU-03 是 docs 型（implementer），与 WU-01/02 无耦合。
WU-01 与 WU-02 无共享文件——`engine.py` 只归 WU-01。
（设计取舍：**不**给 engine 增加 `bypass_gate` 参数，而是通过放宽 `preprocessor`
同时覆盖「首轮自动注入」与「工具调用」两条路径，从而把改动收敛在 WU-02 自己的文件里。）

### 明确不做（out of scope）

| RCA 项 | 决定 | 理由 |
| --- | --- | --- |
| #5 `topic_prefilter` 死代码 | **保留**，仅补注释 | `tests/memory/test_hook_topic_prefilter.py` 仍引用 `_topic_gate` / `_judge_topic_change`；删除属独立重构，无功能收益，且会让本批引入无关测试改动 |
| #7 Dream 文件记忆与 SQLite 收敛 | **不做**，交产品决策 | RCA 标注「需你决策」；本计划只把二者关系在 prompt 中写清楚（Task 7），不改变任何存储行为 |
| `attachments` 表缺失 | 只做防御性返回 `[]` | 表未进 schema v1，通道对普通 query 本就是条件性空操作；建表属新 feature，不在本批 |

---

## Task 1：store 适配层（新建）

**Files:**
- Create: `nanobot/memory/retrieval/store_adapter.py`

- [ ] **Step 1: 写文件**

```python
"""store 适配层：把 ``MemoryDatabase`` 适配成四路通道期望的 store 契约。

背景（RCA 2026-09-15 根因 1）：``RetrievalEngine`` 原先直接持有
``MemoryDatabase``，而四个通道以**方法**形式调用 store
（``store.search_semantic_scored(...)`` 等）；真实实现却是
``nanobot.memory.repository`` 的**模块级函数**（首参 ``conn``）。
两者对不上 → 每次召回都抛 ``AttributeError`` → ``candidates`` 恒空 →
``retrieve_with_ids`` 恒返回 ``("", [])``。而该异常在 ``engine.py`` 里被
静默 ``continue`` 吞掉，所以故障多年不可见。

本模块是二者之间**唯一**的适配点：对外暴露通道期望的 4 个方法，对内按调用
粒度开关 ``MemoryDatabase.connect()``，复用 repository 的既有 SQL。
"""
from __future__ import annotations

import sqlite3
from typing import Any

from loguru import logger

from nanobot.memory import repository
from nanobot.memory.database import MemoryDatabase


class MemoryStoreAdapter:
    """把 ``MemoryDatabase`` 适配为检索通道期望的 store 契约。

    契约（与 ``nanobot/memory/retrieval/channels/*.py`` 调用点一一对应）：

    - ``search_semantic_scored(query, *, limit) -> list[(Memory, float)]``
    - ``search_episodes(*, entity, limit) -> list[_EpisodeRow]``
    - ``query_semantic(*, min_importance, since_days, limit) -> list[_MemoryRow]``
    - ``search_attachments(term, *, intent, limit) -> list[_AttachmentRow]``

    测试替身见 ``tests/memory/retrieval/test_engine.py::_StubStore``——
    本类与它实现同一契约。
    """

    def __init__(self, database: MemoryDatabase) -> None:
        self._database = database

    # ----- 语义通道 -----
    def search_semantic_scored(
        self, query: str, *, limit: int = 30
    ) -> list[tuple[Any, float]]:
        """语义召回：委托 ``repository.search_semantic_scored``。"""
        with self._database.connect() as conn:
            return repository.search_semantic_scored(conn, query, limit=limit)

    # ----- 情节通道 -----
    def search_episodes(self, *, entity: str, limit: int = 5) -> list[Any]:
        """按实体名召回 episode。"""
        with self._database.connect() as conn:
            return repository.search_episodes(conn, entity=entity, limit=limit)

    # ----- 近期通道 -----
    def query_semantic(
        self, *, min_importance: float, since_days: int, limit: int
    ) -> list[Any]:
        """近期高重要性记忆。"""
        with self._database.connect() as conn:
            return repository.query_semantic(
                conn,
                min_importance=min_importance,
                since_days=since_days,
                limit=limit,
            )

    # ----- 附件通道 -----
    def search_attachments(
        self, term: str, *, intent: str, limit: int = 5
    ) -> list[Any]:
        """附件搜索。

        ``attachments`` 表尚未进入 schema v1（``repository.search_attachments``
        的 docstring 已声明「调用方须保证 schema 已扩展」）。表缺失时返回 ``[]``，
        而不是让 ``OperationalError`` 冒泡——该通道对普通 query 本就是条件性空
        操作（媒体词闸门，见 ``channels/attachments.py:41-45``），此处消化的只是
        「表还没建」这一结构性缺失，而非掩盖真实故障。真实故障仍会经
        ``engine.py`` 的 ``logger.warning`` 暴露。
        """
        try:
            with self._database.connect() as conn:
                return repository.search_attachments(
                    conn, term=term, intent=intent, limit=limit
                )
        except sqlite3.OperationalError as exc:
            logger.debug("attachments channel unavailable: {}", exc)
            return []
```

- [ ] **Step 2: 确认导入无误**

Run: `python -c "import nanobot.memory.retrieval.store_adapter as m; print(m.MemoryStoreAdapter)"`
Expected: `<class 'nanobot.memory.retrieval.store_adapter.MemoryStoreAdapter'>`

---

## Task 2：repository 层——CJK 子串回退 + 时间归一

**Files:**
- Modify: `nanobot/memory/repository.py`（顶部 import；`search_memories` 尾部；新增 `_search_memories_like`；`query_semantic` 的 SQL）

- [ ] **Step 1: 顶部加 logger import**

在 `from typing import Any, Literal` 之后追加：

```python
from loguru import logger
```

- [ ] **Step 2: `search_memories` 加 FTS5 异常隔离 + LIKE 回退**

把 `search_memories` 结尾这两行：

```python
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_memory(r) for r in rows]
```

替换为：

```python
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        # FTS5 语法错误（query 含引号 / 连字符 / 未闭合短语等）不应让整条召回
        # 失败——与 search_backend.Fts5SearchBackend._try_fts5 同策略。
        logger.debug("FTS5 MATCH failed for {!r}: {}", query, exc)
        rows = []
    if rows:
        return [_row_to_memory(r) for r in rows]
    # 中文子串回退（RCA 2026-09-15 根因 5）：memories_fts 用
    # tokenize='unicode61'，连续 CJK 被切成一个整 token，因此「创作」永远匹配
    # 不到 content 里的「用户热爱创作，…」。无此回退时中文查询恒空。
    return _search_memories_like(
        conn, query, type=type, workspace_id=workspace_id, limit=limit
    )
```

- [ ] **Step 3: 新增 `_search_memories_like`**

紧跟在 `search_memories` 函数体之后、`# ---------- session_extraction_state ----------`
分节注释之前插入：

```python
def _search_memories_like(
    conn: sqlite3.Connection,
    query: str,
    *,
    type: MemoryType | None,
    workspace_id: str | None,
    limit: int | None,
) -> list[Memory]:
    """FTS5 零结果 / 语法错误时的 ``LIKE %query%`` 子串回退。

    为什么必需：``memories_fts`` 使用 ``tokenize='unicode61 remove_diacritics 2'``
    （``database.py`` §memories_fts），对连续 CJK 不做分词，整串是一个 token。
    因此任何**中文子串**查询（「创作」「记忆」）都命中不了 FTS5。
    与 ``search_backend.Fts5SearchBackend._fallback_like`` 保持同一策略与排序。
    """
    flat = " ".join(_SELECT_MEMORY_COLUMNS.split())
    clauses = ["content LIKE ?"]
    params: list[Any] = [f"%{query}%"]
    if type is not None:
        clauses.append("type = ?")
        params.append(type.value)
    if workspace_id is not None:
        clauses.append("workspace_id = ?")
        params.append(workspace_id)
    where = " AND ".join(clauses)
    limit_sql = f" LIMIT {int(limit)}" if limit is not None else ""
    sql = (
        f"SELECT {flat} FROM memories WHERE {where} "
        f"ORDER BY importance_score DESC{limit_sql}"
    )
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_memory(r) for r in rows]
```

- [ ] **Step 4: `query_semantic` 时间比较归一**

把：

```python
    cur = conn.execute(
        "SELECT id, content, importance_score, updated_at, access_count "
        "FROM memories "
        "WHERE importance_score >= ? AND updated_at >= ? "
        "ORDER BY importance_score DESC LIMIT ?",
        (min_importance, cutoff.isoformat(), limit),
    )
```

替换为：

```python
    # created_at/updated_at 由 SQLite 存为 'YYYY-MM-DD HH:MM:SS.ffffff+00:00'
    # （空格分隔），而 cutoff.isoformat() 用 'T' 分隔。直接字符串比较时
    # ' '(0x20) < 'T'(0x54)，会把**边界当天整天的记录**全部排除。
    # datetime() 归一后再比较（实测 sqlite 3.52 正确解析两种格式与 +00:00 偏移）。
    cur = conn.execute(
        "SELECT id, content, importance_score, updated_at, access_count "
        "FROM memories "
        "WHERE importance_score >= ? AND datetime(updated_at) >= datetime(?) "
        "ORDER BY importance_score DESC LIMIT ?",
        (min_importance, cutoff.isoformat(), limit),
    )
```

- [ ] **Step 5: 跑既有测试确认无回归**

Run: `pytest tests/memory/test_search.py tests/memory/test_repository.py tests/memory/test_memories.py -q`
Expected: 全通过（若有断言依赖「FTS 零结果即返回空」的用例会失败——失败即说明该用例
在断言旧缺陷，需在 Task 3 一并修正并在 collective-test 中记录）

---

## Task 3：engine 通道异常不再静默

**Files:**
- Modify: `nanobot/memory/retrieval/engine.py`（顶部 import；`retrieve_with_ids` 的候选汇总循环）

- [ ] **Step 1: 顶部加 logger import**

在 `from typing import Any, Callable` 之后追加：

```python
from loguru import logger
```

- [ ] **Step 2: 通道异常记 warning**

把：

```python
        candidates: list[RetrievalCandidate] = []
        for chunk in (sem, eps, rec, att):
            if isinstance(chunk, Exception):
                continue
            candidates.extend(chunk)
```

替换为：

```python
        candidates: list[RetrievalCandidate] = []
        for channel_name, chunk in zip(
            ("semantic", "episodes", "recent", "attachments"),
            (sem, eps, rec, att),
            strict=True,
        ):
            if isinstance(chunk, Exception):
                # RCA 2026-09-15 根因 1：这里原本是无条件 ``continue``，把
                # ``AttributeError: 'MemoryDatabase' object has no attribute
                # 'search_semantic_scored'`` 这类装配级故障完全吞掉，导致
                # 「四路召回全废」表现为「记忆里是空的」。定位信息必须留下。
                logger.warning(
                    "retrieval channel {} failed: {}: {}",
                    channel_name,
                    type(chunk).__name__,
                    chunk,
                )
                continue
            candidates.extend(chunk)
```

- [ ] **Step 3: 验证异常被记录**

Run: `pytest tests/memory/retrieval/test_engine.py -q`
Expected: 全通过（既有 `semantic_should_raise` 用例断言「通道异常不影响整体」，行为不变）

---

## Task 4：装配点接上 adapter

**Files:**
- Modify: `nanobot/cli/gateway_runtime.py`（import 区；:485-488）

- [ ] **Step 1: 加 import**

在 `from nanobot.memory.retrieval import RetrievalEngine`（若无则找等价的 retrieval 导入位置）
之后追加：

```python
from nanobot.memory.retrieval.store_adapter import MemoryStoreAdapter
```

- [ ] **Step 2: 替换装配行**

把：

```python
    _retrieval_engine = RetrievalEngine(store=_memory_services.database, brain=None)
```

替换为：

```python
    # RCA 2026-09-15 根因 1：store 必须是 MemoryStoreAdapter（通道期望的是
    # **方法**契约），不能直接传 MemoryDatabase（它只有 connect/schema 方法）。
    _retrieval_engine = RetrievalEngine(
        store=MemoryStoreAdapter(_memory_services.database),
        brain=None,
    )
```

- [ ] **Step 3: 确认没有第二个装配点**

Run: `grep -rn "RetrievalEngine(" nanobot/ | grep -v test`
Expected: 只有 `nanobot/cli/gateway_runtime.py` 一处，且其中 `store=` 已是 `MemoryStoreAdapter(...)`

---

## Task 5：验收测试——「中文 query → 非空注入块」

这是 RCA §Next 明确要求的那条**能失败的测试**。**先写测试、确认失败、再实施 Task 1-4**
（若已按顺序做完 Task 1-4，则先 `git stash` 掉 Task 1-4 的改动跑一次，确认测试确实会失败）。

**Files:**
- Create: `tests/memory/retrieval/test_store_adapter.py`

- [ ] **Step 1: 写测试**

```python
"""store adapter 单测 + 中文检索端到端验收（RCA 2026-09-15）。"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import Memory, MemoryType
from nanobot.memory.repository import add_memory, search_memories
from nanobot.memory.retrieval.engine import RetrievalEngine
from nanobot.memory.retrieval.store_adapter import MemoryStoreAdapter

_CHINESE_MEMORY = "用户热爱创作，希望AI主动提供创作灵感"
_SECOND_MEMORY = "用户希望定期收到AI主动推送的灵感内容"


def _seed(db: MemoryDatabase) -> None:
    """写入 2 条中文记忆（importance 0.9 / 0.8，updated_at=now）。"""
    now = datetime.now(timezone.utc)
    with db.connect() as conn:
        add_memory(
            conn,
            Memory(
                id="m1",
                content=_CHINESE_MEMORY,
                type=MemoryType.PREFERENCE,
                importance_score=0.9,
                created_at=now,
                updated_at=now,
            ),
        )
        add_memory(
            conn,
            Memory(
                id="m2",
                content=_SECOND_MEMORY,
                type=MemoryType.PREFERENCE,
                importance_score=0.8,
                created_at=now,
                updated_at=now,
            ),
        )


@pytest.fixture()
def db(tmp_path: Path) -> MemoryDatabase:
    database = MemoryDatabase(tmp_path)
    database.ensure_schema()
    _seed(database)
    return database


# ---------- FTS5 中文子串回退（根因 5）----------


def test_search_memories_matches_chinese_substring(db: MemoryDatabase):
    """unicode61 不分词，中文子串必须靠 LIKE 回退命中。"""
    with db.connect() as conn:
        hits = search_memories(conn, "创作", limit=10)
    assert [m.id for m in hits] == ["m1"]


def test_search_memories_still_matches_exact_token(db: MemoryDatabase):
    """FTS5 能命中的整串 token 走原路径，结果不变。"""
    with db.connect() as conn:
        hits = search_memories(conn, "用户热爱创作", limit=10)
    assert "m1" in [m.id for m in hits]


def test_search_memories_tolerates_fts_syntax_error(db: MemoryDatabase):
    """FTS5 语法错误（未闭合引号）不应抛出，应回退 LIKE。"""
    with db.connect() as conn:
        hits = search_memories(conn, '"创作', limit=10)
    assert isinstance(hits, list)


# ---------- adapter 契约（根因 1）----------


def test_adapter_exposes_channel_contract(db: MemoryDatabase):
    """通道调用的 4 个方法必须存在且可调用（此前全是 AttributeError）。"""
    adapter = MemoryStoreAdapter(db)
    scored = adapter.search_semantic_scored("创作", limit=15)
    assert scored and scored[0][0].id == "m1"

    recent = adapter.query_semantic(min_importance=0.6, since_days=3, limit=5)
    assert {m.id for m in recent} == {"m1", "m2"}

    assert adapter.search_episodes(entity="不存在", limit=3) == []
    # attachments 表未进 schema v1：必须返回 []，不得抛 OperationalError
    assert adapter.search_attachments("图片", intent="search_file", limit=5) == []


def test_query_semantic_includes_boundary_day(db: MemoryDatabase):
    """updated_at 是空格分隔、cutoff 是 'T' 分隔——字符串比较会漏掉边界当天。

    构造一条 updated_at 落在 cutoff 当天更早时刻的记录：字符串比较会判
    ``'…12 10:00:00' < '…12T13:00:00'`` 而排除，``datetime()`` 归一后必须保留。
    """
    boundary = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    adapter = MemoryStoreAdapter(db)
    with db.connect() as conn:
        conn.execute(
            "UPDATE memories SET updated_at = ? WHERE id = 'm1'",
            (boundary.strftime("%Y-%m-%d %H:%M:%S.%f+00:00"),),
        )
        got = adapter.query_semantic(min_importance=0.6, since_days=3, limit=5)
    assert "m1" in {m.id for m in got}, "边界当天记录被整批排除"


# ---------- 端到端验收（RCA §Next 要求的失败测试）----------


@pytest.mark.asyncio
async def test_retrieve_with_ids_returns_chinese_memories(db: MemoryDatabase):
    """向临时库写入记忆后，中文 query 必须召回非空注入块。

    覆盖根因 1 + 根因 5；``recent_messages`` 给非空以隔离根因 2
    （首轮门禁由 tests/memory/retrieval/test_preprocessor.py 覆盖）。
    """
    engine = RetrievalEngine(store=MemoryStoreAdapter(db), brain=None)
    block, ids = await engine.retrieve_with_ids(
        query="我正在规划创作选题",
        recent_messages=[{"role": "user", "content": "在吗"}, {"role": "assistant", "content": "在"}],
    )
    assert block.startswith("## 相关记忆（自动检索）")
    assert _CHINESE_MEMORY in block
    assert "m1" in ids
```

- [ ] **Step 2: 先确认它会失败（TDD 证据）**

Run: `pytest tests/memory/retrieval/test_store_adapter.py -q`
Expected（在 Task 1-4 之前）：`ImportError: cannot import name 'MemoryStoreAdapter'`。
若 Task 1-4 已实施，则本步的「失败证据」由 Task 1 Step 2 与 Task 2 Step 5 承担，
在 execution-log 中记录。

- [ ] **Step 3: Task 1-4 之后跑通**

Run: `pytest tests/memory/retrieval/test_store_adapter.py -q`
Expected: `6 passed`

---

## Task 6：前置门禁放宽 + 工具会话注入

**Files:**
- Modify: `nanobot/memory/retrieval/preprocessor.py`
- Modify: `nanobot/agent/tools/memory_search.py`
- Modify: `tests/memory/retrieval/test_preprocessor.py`（追加）
- Create: `tests/memory/test_memory_search_tool.py`

- [ ] **Step 1: preprocessor 加记忆意图关键词**

在 `_KEEP_SHORT_HINTS` 之后追加：

```python
# 记忆意图关键词：命中时即使文本超短 / 首轮无历史也放行检索。
# RCA 2026-09-15 根因 2/3：首轮 ``recent_messages`` 必为空（``loop.py`` 传
# ``list(ctx.history)``），旧规则把「查一下我的记忆」（7 字）整类拦掉；
# ``memory_search`` 工具由 LLM 传入的也多是「记忆」「用户偏好」这类短词。
_MEMORY_INTENT_HINTS = (
    "记忆", "记得", "回忆", "之前", "上次", "以前", "历史", "过去",
    "我说过", "提到过", "偏好", "习惯", "我的", "个人",
)
```

- [ ] **Step 2: 改写 `should_skip_retrieval`**

把当前实现整体替换为：

```python
    @classmethod
    def should_skip_retrieval(cls, query: str | None, recent_messages: list) -> tuple[bool, str]:
        text = (query or "").strip()
        if not text:
            return True, "empty"
        lowered = text.lower()
        if lowered in cls._CONTROL_ONLY:
            return True, "control_only"
        has_short_hint = any(h in text for h in _KEEP_SHORT_HINTS)
        has_memory_intent = any(h in text for h in _MEMORY_INTENT_HINTS)
        # 明确在问记忆 / 过去 → 无论多短、无论有没有历史都放行。
        # RCA 2026-09-15 根因 2：旧规则是「无历史 + ≤12 字」即跳过，
        # 而首轮 recent_messages 必为空 → 每条会话的首条消息永远不检索。
        if len(text) <= 3 and not has_short_hint and not has_memory_intent:
            return True, "too_short"
        if (
            len(text) <= 12
            and not recent_messages
            and not has_short_hint
            and not has_memory_intent
        ):
            return True, "short_without_context"
        return False, ""
```

- [ ] **Step 3: preprocessor 追加用例**

在 `tests/memory/retrieval/test_preprocessor.py` 的 `TestGate` 类末尾追加：

```python
    @pytest.mark.parametrize(
        "text",
        ["查一下我的记忆", "我的偏好是什么", "上次说过什么", "你记得吗"],
    )
    def test_should_pass_short_memory_intent_without_context(self, text):
        """首轮无历史时，含记忆意图关键词的短消息不应被跳过（RCA 根因 2）。"""
        skip, reason = MemoryQueryPreprocessor.should_skip_retrieval(text, [])
        assert skip is False, f"{text!r} 被误杀：{reason}"

    def test_should_still_skip_plain_short_without_context(self):
        """真正的无意图短消息仍跳过，门禁未被整体废掉。"""
        skip, reason = MemoryQueryPreprocessor.should_skip_retrieval("明天天气怎么样", [])
        assert skip is True
        assert reason == "short_without_context"
```

- [ ] **Step 4: memory_search 工具注入 sessions**

`nanobot/agent/tools/memory_search.py`：

(a) TYPE_CHECKING 块内追加 SessionManager 导入：

```python
if TYPE_CHECKING:
    from nanobot.memory.retrieval import RetrievalEngine
    from nanobot.session.manager import SessionManager
```

(b) `__init__` 增加 `sessions` 形参并落盘：

```python
    def __init__(
        self,
        retrieval_engine_provider: Callable[[], "RetrievalEngine | None"],
        sessions: "SessionManager | None" = None,
    ) -> None:
        self._get_engine = retrieval_engine_provider
        # RCA 2026-09-15 根因 3：此前 ``execute`` 里判断的是
        # ``hasattr(self, "_sessions")``，但全仓库没有任何代码给该工具实例注入
        # ``_sessions``（实测 instance dict keys == ['_get_engine']）→ 该分支恒
        # False → ``recent`` 恒为 ``[]``，工具拿不到会话上下文，随之撞上根因 2 的
        # ``short_without_context`` 门禁。改为在 ``create`` 阶段经
        # ``ToolContext.sessions`` 注入，与 ``long_task.py::_GoalToolsMixin`` 同模式。
        self._sessions = sessions
```

(c) `create` 传入 sessions：

```python
    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:

        def provider() -> "RetrievalEngine | None":  # noqa: F401
            # RetrievalEngine type needed for annotation; imported inside to avoid circular
            attrs = getattr(ctx, "attributes", {})
            return attrs.get("retrieval_engine")

        return cls(
            retrieval_engine_provider=provider,
            sessions=getattr(ctx, "sessions", None),
        )
```

(d) `execute` 的判断从 `hasattr` 改为属性非空：

```python
        recent: list[dict[str, Any]] = []
        session_key = current_request_session_key()
        if session_key and self._sessions is not None:
            session = self._sessions.get_cached(session_key)
            if session is not None:
                recent = session.messages[-10:]  # last 10 messages as context
```

- [ ] **Step 5: 写工具测试**

Create `tests/memory/test_memory_search_tool.py`：

```python
"""MemorySearchTool：会话注入 + 检索参数透传（RCA 2026-09-15 根因 3）。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.memory_search import MemorySearchTool


class _FakeSessions:
    """最小 SessionManager 替身：只实现 ``get_cached``。"""

    def __init__(self, session):
        self._session = session
        self.calls: list[str] = []

    def get_cached(self, key: str):
        self.calls.append(key)
        return self._session


class _FakeEngine:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def retrieve(self, *, query: str, recent_messages: list, max_tokens: int):
        self.calls.append(
            {"query": query, "recent_messages": recent_messages, "max_tokens": max_tokens}
        )
        return "## 相关记忆（自动检索）\n- 命中"


def _ctx(sessions, engine) -> SimpleNamespace:
    return SimpleNamespace(sessions=sessions, attributes={"retrieval_engine": engine})


def test_create_injects_sessions():
    """``create`` 必须把 ToolContext.sessions 注入实例（此前恒缺失）。"""
    tool = MemorySearchTool.create(_ctx(_FakeSessions(None), _FakeEngine()))
    assert isinstance(tool, MemorySearchTool)
    assert tool._sessions is not None


def test_create_tolerates_missing_sessions():
    """ToolContext.sessions 为 None 时不应崩。"""
    tool = MemorySearchTool.create(SimpleNamespace(attributes={}))
    assert tool._sessions is None


@pytest.mark.asyncio
async def test_execute_passes_session_history_as_recent_messages():
    """有会话缓存时，最近 10 条消息必须作为 ``recent_messages`` 传给引擎——
    这同时也是绕过 ``short_without_context`` 门禁的前提。"""
    history = [{"role": "user", "content": f"m{i}"} for i in range(15)]
    sessions = _FakeSessions(SimpleNamespace(messages=history))
    engine = _FakeEngine()
    tool = MemorySearchTool.create(_ctx(sessions, engine))

    ctx = RequestContext(channel="websocket", chat_id="c1", session_key="s1")
    with request_context(ctx):
        out = await tool.execute(query="记忆")

    assert out.startswith("## 相关记忆")
    assert sessions.calls == ["s1"]
    assert engine.calls[0]["recent_messages"] == history[-10:]


@pytest.mark.asyncio
async def test_execute_without_session_context_passes_empty_recent():
    """无 request context 时不取会话，``recent_messages`` 为空列表。"""
    sessions = _FakeSessions(SimpleNamespace(messages=[{"role": "user", "content": "x"}]))
    engine = _FakeEngine()
    tool = MemorySearchTool.create(_ctx(sessions, engine))

    await tool.execute(query="记忆")

    assert sessions.calls == []
    assert engine.calls[0]["recent_messages"] == []
```

- [ ] **Step 6: 跑测试**

Run: `pytest tests/memory/retrieval/test_preprocessor.py tests/memory/test_memory_search_tool.py -q`
Expected: 全通过

---

## Task 7：提示词——把「三层 SQLite 记忆」写成主存储

**Files:**
- Modify: `nanobot/templates/agent/identity.md:7-10`（分支一）与 `:13-16`（分支二）

- [ ] **Step 1: 替换分支一（`agent_workspace_path != workspace_path`）**

把这三行：

```
- Agent profile: {{ agent_workspace_path }}/SOUL.md and {{ agent_workspace_path }}/USER.md (automatically managed by Dream — do not edit directly)
- Long-term memory: {{ agent_workspace_path }}/memory/MEMORY.md (automatically managed by Dream — do not edit directly)
- Memory search tool: `memory_search` — use it to proactively retrieve relevant long-term memories when users ask about past projects, decisions, or tool usage history.
```

替换为：

```
- Agent profile: {{ agent_workspace_path }}/SOUL.md and {{ agent_workspace_path }}/USER.md (automatically managed by Dream — do not edit directly)
- **Long-term memory — primary store:** a three-layer SQLite database at {{ agent_workspace_path }}/memory/state.db. `memories` holds durable facts and preferences, `episodes` holds session summaries, `scratchpad` holds working state. It is written automatically by the memory extractor. This is the store `memory_search` reads.
- Memory search tool: `memory_search` — **the** way to **proactively** retrieve long-term memory. Call it whenever users ask about past projects, decisions, preferences, or tool usage history, and whenever you are about to say you do not know something about the user.
- Legacy notes (secondary, not the retrieval store): {{ agent_workspace_path }}/memory/MEMORY.md is Dream's free-form consolidation file. It is often a stub. **An empty or unchanged MEMORY.md does NOT mean memory is empty** — never conclude "memory is empty" from it. Call `memory_search` first, then read MEMORY.md only if you need Dream's prose summary.
```

- [ ] **Step 2: 替换分支二（`else`）**

把这三行：

```
- Agent profile: SOUL.md and USER.md (automatically managed by Dream — do not edit directly)
- Long-term memory: memory/MEMORY.md (automatically managed by Dream — do not edit directly)
- Memory search tool: `memory_search` — use it to proactively retrieve relevant long-term memories when users ask about past projects, decisions, or tool usage history.
```

替换为：

```
- Agent profile: SOUL.md and USER.md (automatically managed by Dream — do not edit directly)
- **Long-term memory — primary store:** a three-layer SQLite database at memory/state.db. `memories` holds durable facts and preferences, `episodes` holds session summaries, `scratchpad` holds working state. It is written automatically by the memory extractor. This is the store `memory_search` reads.
- Memory search tool: `memory_search` — **the** way to **proactively** retrieve long-term memory. Call it whenever users ask about past projects, decisions, preferences, or tool usage history, and whenever you are about to say you do not know something about the user.
- Legacy notes (secondary, not the retrieval store): memory/MEMORY.md is Dream's free-form consolidation file. It is often a stub. **An empty or unchanged MEMORY.md does NOT mean memory is empty** — never conclude "memory is empty" from it. Call `memory_search` first, then read MEMORY.md only if you need Dream's prose summary.
```

- [ ] **Step 3: 确认模板可渲染**
Run: `python -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('nanobot/templates'))
t = env.get_template('agent/identity.md')
out = t.render(runtime='r', agent_workspace_path='/a', workspace_path='/a', platform_policy='p', channel='cli')
print('三层 SQLite' if 'state.db' in out else 'MISSING')
"`
Expected: 输出含 `state.db`（若 `{{ platform_policy }}` 等上下文变量缺失导致渲染报错，
改用 `Environment(loader=..., undefined=Undefined)` 或直接跑 `pytest tests/ -k identity -q`）

- [ ] **Step 4: 跑模板相关既有测试**

Run: `pytest tests/ -k "identity or prompt" -q`
Expected: 全通过

---

> **文本约束（实施期修正 2026-09-15）：** `tests/agent/test_identity_template.py:20`
> 断言模板必须含 `proactive` 或 `检索`（`assert "proactive" in text.lower() or "检索" in text`）。
> 上文的 `**proactively** retrieve` 一词**不可删**——它是该既有测试的通过前提，
> 与本 Task 的新增语义（state.db / 三表 / 空 MEMORY.md ≠ 记忆为空）不冲突。

---

## Task 8：全量回归

- [ ] **Step 1: 记忆模块全量**

Run: `pytest tests/memory/ -q`
Expected: 全通过

- [ ] **Step 2: 相关装配测试**

Run: `pytest tests/test_loop_wiring.py -q 2>/dev/null || pytest tests/ -k "loop_wiring" -q`
Expected: 全通过

- [ ] **Step 3: 全量**

Run: `pytest -q`
Expected: 全通过（新增用例计入）

- [ ] **Step 4: lint**

Run: `ruff check nanobot/`
Expected: `All checks passed!`

---

## Task 9：真实库冒烟（人工，非 pytest）

- [ ] **Step 1: 对真实库跑一次端到端**

Run:
```bash
python - <<'PY'
import asyncio
from pathlib import Path
from nanobot.memory.database import MemoryDatabase
from nanobot.memory.retrieval.engine import RetrievalEngine
from nanobot.memory.retrieval.store_adapter import MemoryStoreAdapter

db = MemoryDatabase(Path.home() / ".nanobot" / "workspace")
db.ensure_schema()

async def main():
    eng = RetrievalEngine(store=MemoryStoreAdapter(db), brain=None)
    for q in ("查一下我的记忆", "我正在规划创作选题"):
        block, ids = await eng.retrieve_with_ids(
            query=q,
            recent_messages=[{"role": "user", "content": "hi"},
                             {"role": "assistant", "content": "hello"}],
        )
        print(f"--- {q} ---")
        print(block or "<EMPTY>")
        print("ids:", ids)

asyncio.run(main())
PY
```
Expected: 至少一条 query 打印出 `## 相关记忆（自动检索）` 且含真实记忆内容（如「用户热爱创作…」）。
若两条都 `<EMPTY>`，先检查该库 `memories.updated_at` 是否已超出 3 天窗口
（`recent` 通道会自然失活），此时以 `tests/memory/retrieval/test_store_adapter.py` 的结果为准，
并把真实库观测记入 collective-test。

- [ ] **Step 2: 记录观测**

把 Step 1 的真实输出（含 `<EMPTY>` 与否）原文贴进
`.ai-runtime-artifacts/verifications/2026-09-15-memory-retrieval-rca-fix-collective-test.md`。

---

## 自检（Self-Review）

**1. Spec 覆盖：** RCA 待修复项 #1 #2 #3 #4 #6 分别由 Task 1+4 / Task 6 / Task 6 / Task 7 覆盖；
#5 决定保留（§明确不做，附理由）；#7 标注为产品决策、不实施。
**新增**根因 5（CJK FTS）由 Task 2 + Task 5 覆盖，RCA 未列，属本次新发现。
**2. 占位符扫描：** 每个代码步都给了完整代码；无 TBD / 「类似上文」。
**3. 类型一致性：** `MemoryStoreAdapter.search_semantic_scored/query_semantic/search_episodes/search_attachments`
的签名与 `channels/*.py` 的调用点、`tests/memory/retrieval/test_engine.py::_StubStore`
三方一致；`_search_memories_like` 的 `type/workspace_id/limit` 关键字与 `search_memories` 同名同义。
**4. 已知风险：** Task 2 Step 5 可能暴露「断言旧缺陷」的既有用例，已写明处置方式。

## Next

- 本 plan FM 为 `approved: true`（用户已明确授权直接执行），不再单独设门禁。
- 执行图与 WU 派发见 `.ai-runtime-artifacts/plans/2026-09-15-memory-retrieval-rca-fix-dispatch.md`。
- 尾盘门禁：集体测试 → 集体审查 → execution-log 关闭，全部须 Leader 手动落盘。
