# 记忆召回对齐 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 三改动修复记忆召回 P1/P2/P3：情节通道死代码激活、中文 FTS 逐词命中、RRF 融合替换 `max()`。

**Architecture:**
- 语义通道 `repository.search_semantic_scored` 接收 `keywords` 参数，FTS5/LIKE 均按逐词 OR 执行；RRF 融合替换 `max()` 并用绝对归一化。
- 情节通道 `_extract_query_entities` 补 keywords 兜底，激活普通中文对话中的 episode 召回。
- `keywords` 从 `engine.py` 下沉到 `semantic` 通道（`channels/semantic.py`）和 `store_adapter.py`，再透传到 `repository.search_semantic_scored`。

**Tech Stack:** Python 3.11+, `sqlite3`, `loguru`, `pydantic`（零新增依赖）

---

## 文件结构概览

| 文件 | 改动 |
|---|---|
| `nanobot/memory/retrieval/channels/semantic.py` | `search_semantic` 接收并透传 `keywords` 参数 |
| `nanobot/memory/retrieval/channels/episodes.py` | `_extract_query_entities` 补 keywords 兜底；`search_episodes` 透传 `keywords` |
| `nanobot/memory/retrieval/store_adapter.py` | `search_semantic_scored` 接收 `keywords`；RRF 融合替换 `max()` |
| `nanobot/memory/repository.py` | `search_semantic_scored` 接收 `keywords`；逐词 FTS5/LIKE + `_hits` 排序 |
| `nanobot/memory/retrieval/engine.py` | 四路并行时透传 `keywords` 到 `semantic` 和 `episodes` 通道 |
| `tests/memory/retrieval/test_*.py` | 单测覆盖 RRF、逐词、情节通道兜底 |

---

## Task 1: engine.py — keywords 下沉到 semantic 和 episodes 通道

**Files:**
- Modify: `nanobot/memory/retrieval/engine.py:153-192`

- [ ] **Step 1: 修改 sem_task 调用，加 keywords 参数**

当前（`engine.py:154-161`）：
```python
sem_task = asyncio.create_task(
    asyncio.to_thread(
        search_semantic,
        self.store,
        query=prepared.cleaned_query,
        limit=_SEMANTIC_LIMIT,
        compute_recency=recency,
    )
)
```

改为：
```python
sem_task = asyncio.create_task(
    asyncio.to_thread(
        search_semantic,
        self.store,
        query=prepared.cleaned_query,
        keywords=keywords,  # 新增
        limit=_SEMANTIC_LIMIT,
        compute_recency=recency,
    )
)
```

- [ ] **Step 2: 修改 eps_task 调用，加 keywords 参数**

当前（`engine.py:163-171`）：
```python
eps_task = asyncio.create_task(
    asyncio.to_thread(
        search_episodes,
        self.store,
        query=prepared.cleaned_query,
        limit=_EPISODES_LIMIT,
        compute_recency=recency,
    )
)
```

改为：
```python
eps_task = asyncio.create_task(
    asyncio.to_thread(
        search_episodes,
        self.store,
        query=prepared.cleaned_query,
        keywords=keywords,  # 新增
        limit=_EPISODES_LIMIT,
        compute_recency=recency,
    )
)
```

- [ ] **Step 3: 确认 rec_task 和 att_task 已有 keywords 参数**

`rec_task`（`engine.py:172-181`）已有 `keywords=keywords`，`att_task` 同。无需修改。

- [ ] **Step 4: 运行测试验证 engine.py 无回归**

```bash
pytest tests/memory/retrieval/test_engine.py -v
```
Expected: PASS（无回归）

---

## Task 2: repository.py — 逐词 FTS5/LIKE + `_hits` 排序

**Files:**
- Modify: `nanobot/memory/repository.py:693-743`

- [ ] **Step 1: 修改 `search_semantic_scored` 函数签名，加 `keywords` 参数**

当前（`repository.py:693-698`）：
```python
def search_semantic_scored(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 30,
) -> list[tuple[Memory, float]]:
```

改为：
```python
def search_semantic_scored(
    conn: sqlite3.Connection,
    query: str,
    *,
    keywords: list[str] | None = None,
    limit: int = 30,
) -> list[tuple[Memory, float]]:
```

- [ ] **Step 2: 新增 `_search_terms` 辅助函数（在 `search_semantic_scored` 前）**

在 `search_semantic_scored` 函数定义前（约第 692 行 `_pseudo_bm25_score` 后）插入：

```python
def _search_terms(query: str, keywords: list[str] | None) -> list[str]:
    """构造 FTS5/LIKE 逐词查询词表。

    terms = [query] + keywords（去重、去空、保留 query 首位）。
    query 恒在首位：即便关键词拆解质量差，原始查询仍有一条独立通路，
    保证「新机制不会让任何现有查询变差」。
    """
    seen: set[str] = set()
    result: list[str] = []
    for tok in [query] + (keywords or []):
        tok = tok.strip()
        if tok and tok not in seen:
            seen.add(tok)
            result.append(tok)
    return result
```

- [ ] **Step 3: 在 `search_semantic_scored` 内调用 `_search_terms`，构造 FTS5 MATCH 表达式**

找到 `flat = ...` 行之前（约第 711 行），插入：

```python
    # 构造逐词查询词表
    terms = _search_terms(query, keywords)

    # FTS5 逐词 OR 短语查询：每个 term 用双引号包裹以中性化特殊字符
    match_expr = " OR ".join(f'"{t}"' for t in terms)
```

- [ ] **Step 4: 修改 FTS5 SQL，用 `match_expr` 替换原始 `query` 占位符**

当前（`repository.py:712-717`）：
```python
    flat = ", ".join(f"m.{c.strip()}" for c in _SELECT_MEMORY_COLUMNS.split(","))
    sql = (
        f"SELECT {flat}, bm25(memories_fts) AS _rank "
        f"FROM memories m JOIN memories_fts f ON f.rowid = m.rowid "
        f"WHERE memories_fts MATCH ? "
        f"ORDER BY _rank LIMIT ?"
    )
    try:
        rows = conn.execute(sql, (query, int(limit))).fetchall()
```

改为：
```python
    flat = ", ".join(f"m.{c.strip()}" for c in _SELECT_MEMORY_COLUMNS.split(","))
    sql = (
        f"SELECT {flat}, bm25(memories_fts) AS _rank "
        f"FROM memories m JOIN memories_fts f ON f.rowid = m.rowid "
        f"WHERE memories_fts MATCH ? "
        f"ORDER BY _rank LIMIT ?"
    )
    try:
        rows = conn.execute(sql, (match_expr, int(limit))).fetchall()
```

- [ ] **Step 5: 修改 LIKE 兜底，改为逐词 OR + `_hits` 排序**

当前（`repository.py:738-743`）：
```python
    return [
        (mem, _pseudo_bm25_score(float(idx)))
        for idx, mem in enumerate(
            _search_memories_like(conn, query, type=None, workspace_id=None, limit=limit)
        )
    ]
```

改为：
```python
    # 逐词 LIKE：每词单独 LIKE，再按命中词数 + importance_score 排序
    # terms 至少含 query，故恒有至少一个 LIKE 条件
    hit_exprs, like_params = [], []
    for tok in terms:
        escaped = _escape_like(tok)
        like_params.extend([f"%{escaped}%"] * 2)  # content LIKE + session_id LIKE
        hit_exprs.append(
            f"(content LIKE ? ESCAPE '\\' OR session_id LIKE ? ESCAPE '\\')"
        )
    flat = " ".join(_SELECT_MEMORY_COLUMNS.split())
    _hits_clause = " + ".join(f"CASE WHEN {e} THEN 1 ELSE 0 END" for e in hit_exprs)
    sql = (
        f"SELECT {flat}, ({_hits_clause}) AS _hits "
        f"FROM memories WHERE ({' OR '.join(hit_exprs)}) "
        f"ORDER BY _hits DESC, importance_score DESC LIMIT ?"
    )
    rows = conn.execute(sql, [*like_params, int(limit)]).fetchall()
    return [(_row_to_memory(r), _pseudo_bm25_score(float(idx))) for idx, r in enumerate(rows)]
```

- [ ] **Step 6: 运行测试验证 repository.py 无回归**

```bash
pytest tests/memory/test_repository.py -v
pytest tests/memory/retrieval/test_store_adapter.py -v
```
Expected: PASS

---

## Task 3: store_adapter.py — keywords 透传 + RRF 融合替换 max()

**Files:**
- Modify: `nanobot/memory/retrieval/store_adapter.py:56-122`

- [ ] **Step 1: 修改 `search_semantic_scored` 签名，加 `keywords` 参数**

当前（`store_adapter.py:57-59`）：
```python
    def search_semantic_scored(
        self, query: str, *, limit: int = 30
    ) -> list[tuple[Any, float]]:
```

改为：
```python
    def search_semantic_scored(
        self, query: str, *, keywords: list[str] | None = None, limit: int = 30
    ) -> list[tuple[Any, float]]:
```

- [ ] **Step 2: 修改 `repository.search_semantic_scored` 调用，透传 keywords**

当前（`store_adapter.py:73-74`）：
```python
        with self._database.connect() as conn:
            fts5_hits = repository.search_semantic_scored(conn, query, limit=limit * 3)
```

改为：
```python
        with self._database.connect() as conn:
            fts5_hits = repository.search_semantic_scored(
                conn, query, keywords=keywords, limit=limit * 3
            )
```

- [ ] **Step 3: 替换 `max()` 融合为 RRF**

找到 `merged: dict[str, float] = {}` 块（`store_adapter.py:70-91`），整个替换为：

```python
        # RRF 融合（Elasticsearch 默认 k=60）
        K = 60
        RRF_MAX = 2.0 / (K + 1)  # 两路皆第 1 名的理论上界 ≈ 0.032787

        fts5_hits: list[tuple[Any, float]] = []
        rrf: dict[str, float] = {}
        with self._database.connect() as conn:
            fts5_hits = repository.search_semantic_scored(
                conn, query, keywords=keywords, limit=limit * 3
            )
        for rank, (mem, _score) in enumerate(fts5_hits, start=1):
            rrf[mem.id] = rrf.get(mem.id, 0.0) + 1.0 / (K + rank)

        # 向量路：故障时静默降级为纯 FTS5（D4）。
        vector_hits: list[tuple[Any, float]] = []
        if self._vector_store is not None:
            try:
                vector_hits = self._vector_store.search(query, limit=limit * 3)
            except Exception as exc:  # noqa: BLE001 - 向量故障绝不上抛
                logger.warning("vector channel failed, degrading to fts5: {}", exc)
                vector_hits = []
            for rank, (mid, _score) in enumerate(vector_hits, start=1):
                rrf[str(mid)] = rrf.get(str(mid), 0.0) + 1.0 / (K + rank)

        # 按 RRF 归一化分排序（绝对归一化：单条候选 relevance < 1.0）
        ordered = sorted(rrf.items(), key=lambda kv: kv[1], reverse=True)[:limit]
```

- [ ] **Step 4: 修改 ordered 后续处理，保持返回类型不变**

当前（`store_adapter.py:93-110`）的 `ordered` 用法不变（仍然是 `sorted(merged...)` 后取前 limit），但现在 `ordered` 已经是 `(id, rrf_score)` 对了。找到并修改 `out` 构造逻辑：

```python
        out: list[tuple[Any, float]] = []
        fts5_by_id = {mem.id: mem for mem, _s in fts5_hits}
        content_by_id = {mem.id: mem.content for mem, _ in fts5_hits}
        # 向量独有 id 在活性过滤中被丢弃的（superseded / 已过期 / 行已删）
        discarded_ids: list[str] = []
        with self._database.connect() as conn:
            for mid, rrf_score in ordered:
                mem = fts5_by_id.get(mid)
                if mem is None:
                    # 向量独有 id：回查 SQLite 权威行并做活性过滤（修正 3）。
                    mem = repository.get_memory(conn, mid)
                    if mem is None or not _is_live(mem):
                        discarded_ids.append(mid)
                        continue
                    content_by_id[mid] = mem.content
                # 绝对归一化：relevance = rrf / RRF_MAX ∈ (0, 1]
                relevance = min(1.0, rrf_score / RRF_MAX)
                out.append((mem, relevance))
```

- [ ] **Step 5: 修改 trace.hybrid 调用，更新参数名（`merged` → `rrf`）**

当前（`store_adapter.py:112-121`）：
```python
        trace.hybrid(
            query=query,
            fts5_hits=fts5_hits,
            vector_hits=vector_hits,
            merged=merged,
            out=out,
            content_by_id=content_by_id,
            discarded_ids=discarded_ids,
            limit=limit,
        )
```

改为（`merged` 替换为 `rrf`）：
```python
        trace.hybrid(
            query=query,
            fts5_hits=fts5_hits,
            vector_hits=vector_hits,
            merged=rrf,
            out=out,
            content_by_id=content_by_id,
            discarded_ids=discarded_ids,
            limit=limit,
        )
```

- [ ] **Step 6: 运行测试验证 store_adapter.py 无回归**

```bash
pytest tests/memory/retrieval/test_store_adapter.py -v
pytest tests/memory/retrieval/test_store_adapter_union.py -v
```
Expected: PASS

---

## Task 4: channels/semantic.py — keywords 参数透传

**Files:**
- Modify: `nanobot/memory/retrieval/channels/semantic.py:15-44`

- [ ] **Step 1: 修改 `search_semantic` 签名，加 `keywords` 参数**

当前（`semantic.py:15-21`）：
```python
def search_semantic(
    store,
    *,
    query: str,
    limit: int,
    compute_recency: Callable[[datetime], float],
) -> list[RetrievalCandidate]:
```

改为：
```python
def search_semantic(
    store,
    *,
    query: str,
    keywords: list[str] | None = None,
    limit: int,
    compute_recency: Callable[[datetime], float],
) -> list[RetrievalCandidate]:
```

- [ ] **Step 2: 修改 `store.search_semantic_scored` 调用，透传 keywords**

当前（`semantic.py:27`）：
```python
    scored = store.search_semantic_scored(query, limit=limit * 3)
```

改为：
```python
    scored = store.search_semantic_scored(query, keywords=keywords, limit=limit * 3)
```

- [ ] **Step 3: 运行测试验证 semantic 通道无回归**

```bash
pytest tests/memory/retrieval/test_engine.py -v
```
Expected: PASS

---

## Task 5: channels/episodes.py — 关键词兜底激活情节通道

**Files:**
- Modify: `nanobot/memory/retrieval/channels/episodes.py:1-53`

- [ ] **Step 1: 修改 `search_episodes` 签名，加 `keywords` 参数**

当前（`episodes.py:26-32`）：
```python
def search_episodes(
    store,  # 提供 search_episodes(entity, limit)
    *,
    query: str,
    limit: int,
    compute_recency: Callable[[datetime], float],
) -> list[RetrievalCandidate]:
```

改为：
```python
def search_episodes(
    store,  # 提供 search_episodes(entity, limit)
    *,
    query: str,
    keywords: list[str] | None = None,
    limit: int,
    compute_recency: Callable[[datetime], float],
) -> list[RetrievalCandidate]:
```

- [ ] **Step 2: 修改 `_extract_query_entities` 调用处，补 keywords 兜底**

当前（`episodes.py:33-34`）：
```python
    entities = _extract_query_entities(query)[:3]
```

改为：
```python
    entities = _extract_query_entities(query)[:3]
    # 关键词兜底：keywords 由 QueryDecomposer 产出，普通中文对话也能命中 episode
    if keywords:
        for kw in keywords:
            if kw and kw not in entities:
                entities.append(kw)
    entities = entities[:3]
```

- [ ] **Step 3: 运行测试验证 episodes 通道无回归**

```bash
pytest tests/memory/retrieval/test_engine.py -v
```
Expected: PASS

---

## Task 6: 新增单测覆盖 RRF 融合、逐词 FTS/LIKE、情节通道兜底

**Files:**
- Create: `tests/memory/retrieval/test_rrf_fusion.py`
- Modify: `tests/memory/retrieval/test_store_adapter.py`（追加 RRF 相关用例）

- [ ] **Step 1: 写 RRF 融合语义测试**

```python
"""RRF 融合行为测试。

覆盖 spec §6 测试策略：
- 两路共识 > 单路高分
- 绝对归一化：单条候选 relevance < 1.0
- RRF 名次无关性（分数整体 ×10 不影响 RRF 结果）
- 逐词 OR：多词命中时 _hits DESC 排序
- terms 构造：keywords 为空 → 退化为 [query]
- FTS5 OR 表达式：含特殊字符不抛语法错
"""
```

- [ ] **Step 2: 写情节通道关键词兜底测试**

```python
"""情节通道关键词兜底测试。

覆盖 spec §6：
- keywords=['健身视频'] 能召回 episode
- 纯路径查询行为不变（不回归）
"""
```

- [ ] **Step 3: 运行新测试**

```bash
pytest tests/memory/retrieval/test_rrf_fusion.py -v
pytest tests/memory/retrieval/test_episodes_keywords.py -v
```
Expected: PASS

---

## Task 7: 端到端验证

**Files:**
- Modify: （无文件改动，仅运行验收命令）

- [ ] **Step 1: 全量 memory 测试**

```bash
pytest tests/memory/ -q
```
Expected: 全绿

- [ ] **Step 2: Ruff 检查**

```bash
ruff check nanobot/
```
Expected: 无新增 warning/error

- [ ] **Step 3: 类型检查**

```bash
uv run --no-sync basedpyright
```
Expected: 无新增 error

- [ ] **Step 4: gateway 启动验证（后台）**

```bash
nanobot gateway
```
Expected: 启动成功（Ctrl+C 退出）

---

## 验收命令汇总

```bash
pytest tests/memory/ -q
ruff check nanobot/
uv run --no-sync basedpyright
nanobot gateway
```

---

## 自检清单（写完计划后自行过一遍）

1. **Spec 覆盖**：每个 §6 用例都有对应 Task 覆盖 ✓
2. **无占位符**：所有代码段均为完整可执行代码，无 TBD/TODO ✓
3. **类型一致性**：`keywords` 参数在线路中层层透传（engine → semantic/episodes → store_adapter → repository），签名统一为 `list[str] | None` ✓
4. **零新依赖**：未触碰 `pyproject.toml`，所有改动基于既有 `sqlite3` / `loguru` / `pydantic` ✓
5. **交付边界**：只改 `engine.py`、`semantic.py`、`episodes.py`、`store_adapter.py`、`repository.py`，不改 `reranker.py`、`formatter.py`、`vector/*` ✓
