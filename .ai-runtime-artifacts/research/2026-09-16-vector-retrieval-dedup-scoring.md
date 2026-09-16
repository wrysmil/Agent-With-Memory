---
artifact: research
route: web-investigator
source:
  - openakita/openakita src/openakita/memory/manager.py:2037-2164（实测源码）
  - openakita/openakita src/openakita/memory/unified_store.py:329-397
  - openakita/openakita src/openakita/memory/search_backends.py:64-200
  - openakita/openakita src/openakita/memory/manager.py:1529-1593（_fast_dedup_check / _evolve_memory）
created_at: 2026-09-16
topic: vector-write-dedup-and-search-fusion
---

# 向量层写入去重与检索打分融合（实测 openakita 源码版）

## 1. 结论速读

| 主题 | openakita 做法 | nanobot 当前 plan 做法 | 差异 |
| --- | --- | --- | --- |
| 写入去重 | 双层：L1 提取器（extractor.deduplicate） + L2 向量距离阈值 + L3 evolve 合并 | 不做（best-effort 索引） | openakita 有，nanobot 无 |
| 距离阈值 | `DUPLICATE_DISTANCE_THRESHOLD = 0.12` | 无 | openakita 硬编码常量 |
| 去重失败时 | `_evolve_memory` 合并更新 confidence + importance + content | n/a | openakita 有，nanobot 走 extractor._evolve_memory（语义层） |
| 向量回退 | cosine > 0.12 时不命中 → 改用 FTS5 子串包含（`core_lower[:80] in hit.content.lower()`） | n/a | openakita 有兜底，nanobot 无 |
| 检索并集 | 主后端（chroma）+ FTS5 fallback → 取最高分（max） | max（plan 已规定） | **完全一致** |
| 候选宽度 | `limit * 3` | `limit * 3`（plan 已规定） | **完全一致** |
| 元数据二次过滤 | SQLite `get_memory` 回查 + active 校验 + scope 四元组 | SQLite 回查 + active + scope（plan §7 I15） | **完全一致** |

## 2. 写入去重细节（`manager.py:2016-2164`）

### 2.1 关键常量

```python
DUPLICATE_DISTANCE_THRESHOLD = 0.12        # manager.py:2016
COMMON_PREFIXES = [...]                     # 9 个常见前缀：'任务执行复盘：'、'用户偏好：'、'学习到：' 等
```

### 2.2 `add_memory` 完整流程（`:2037-2164`）

```python
def add_memory(self, memory, scope="user", scope_owner="", *, user_id=None, workspace_id=None):
    # 1. 标准化 scope/user/workspace
    memory.scope = scope
    memory.user_id = user_id or self._current_user_id or "default"
    memory.workspace_id = workspace_id or self._current_workspace_id or "default"
    memory.scope_owner = scope_owner

    with self._memories_lock:
        # 2. 拿同 scope 的现有记忆做 L1 dedup（extractor.deduplicate）
        existing = [m for m in self._memories.values()
                    if scope 相等 AND scope_owner 相等 AND user_id 相等 AND workspace_id 相等]
        unique = self.extractor.deduplicate([memory], existing)
        if not unique:
            return ""
        memory = unique[0]

        # 3. L2 dedup：向量层（如果开了）
        if self.vector_store is not None and self.vector_store.enabled and len(self._memories) > 0:
            core_content = self._strip_common_prefix(memory.content)
            similar = self.vector_store.search(core_content, limit=3)
            for mid, distance in similar:
                if distance < self.DUPLICATE_DISTANCE_THRESHOLD:    # ← 硬编码 0.12
                    existing_mem = self._memories.get(mid)
                    if existing_mem:
                        # 跨租户隔离校验
                        if (existing_mem.scope != scope or existing_mem.scope_owner != scope_owner
                            or existing_mem.user_id != user_id or existing_mem.workspace_id != workspace_id):
                            continue
                        existing_core = self._strip_common_prefix(existing_mem.content)
                        if core_content != existing_core:           # ← 还要求去前缀后字符串相等
                            continue
                        return ""                                 # 判定重复，丢弃新写

        # 4. L3 fallback：向量层不可用时改用 FTS5 子串包含
        elif len(self._memories) > 0:
            try:
                core_content = self._strip_common_prefix(memory.content)
                fts_hits = self.store.search_semantic(core_content, limit=5, scope=..., user_id=..., workspace_id=...)
                core_lower = core_content.strip()[:80].lower()    # 取前 80 字符
                for hit in fts_hits:
                    if hit.content and core_lower in hit.content.lower():
                        return ""
            except Exception:
                pass

        # 5. 真正写入
        self._memories[memory.id] = memory
        self._save_memories()
        if self.vector_store is not None:
            self.vector_store.add_memory(memory_id=memory.id, content=memory.content, ...)

    # 6. v2: 落 SQLite + FTS5
    _apply_retention(memory)
    sem = SemanticMemory(...)
    self.store.save_semantic(sem)
```

### 2.3 L1 dedup 细节（`_fast_dedup_check`，`:1529-1552`）

```python
@staticmethod
def _fast_dedup_check(new: str, existing: str) -> str:
    """返回 'exact' / 'likely' / 'no'。'likely' 走 LLM 二次判定。"""
    a, b = new.lower().strip(), existing.lower().strip()
    if a == b:                                       return "exact"
    if len(a) > 15 and len(b) > 15 and (a in b or b in a): return "exact"
    if len(a) >= 10 and len(b) >= 10:
        bigrams_a = {a[i:i+2] for i in range(len(a)-1)}
        bigrams_b = {b[i:i+2] for i in range(len(b)-1)}
        overlap = len(bigrams_a & bigrams_b) / len(bigrams_a | bigrams_b)
        if overlap > 0.8:                            return "exact"    # bigram 0.8 → 直接判重
        if overlap > 0.3:                            return "likely"   # bigram 0.3-0.8 → LLM 判定
    return "no"
```

### 2.4 `_evolve_memory`（`:1574-1593`）

```python
def _evolve_memory(self, existing, new_content, new_importance):
    """不删除老记忆，而是合并：confidence+0.1（封顶 1.0）、importance 取最大。"""
    updates = {"confidence": min(1.0, existing.confidence + 0.1)}
    if new_content and new_content != (existing.content or ""):
        updates["content"] = new_content
    updates["importance_score"] = max(existing.importance_score, new_importance)
    self.store.update_semantic(existing.id, updates)
```

注释特别强调："Same subject+predicate with a different value is an update/conflict, not a duplicate."

### 2.5 LLM 兜底（`:1554-1572`）

```python
async def _check_duplicate_with_llm(self, new_content, existing_content) -> bool:
    brain = getattr(self.extractor, "brain", None)
    resp = await brain.think(
        f"判断这两条记忆是否表达相同的信息（语义重复）。\n"
        f"记忆A: {new_content}\n记忆B: {existing_content}\n\n只回答 YES 或 NO。",
        system="你是记忆去重判断器。如果两条记忆表达的核心信息相同（即使措辞不同），回答YES。",
        enable_thinking=False, max_tokens=16,
    )
    return "YES" in (resp.content or str(resp)).strip().upper() and "NO" not in text
```

### 2.6 写入去重成本

| 步骤 | 成本 |
| --- | --- |
| L1 bigram 比对 | O(1) 字符串哈希，纳秒级 |
| L2 向量近邻 + 距离阈值 | 一次 `vector_store.search(limit=3)`，单条编码 + Chroma 余弦搜索 |
| L2 字符串二次确认 | 一次 strip + 等值比较 |
| L3 FTS5 fallback | 一次 SQLite FTS5 查询（limit=5） |
| LLM 兜底 | 仅当 `_fast_dedup_check` 返回 'likely' 才调 LLM |

**L2 向量去重只在已写 ≥1 条记忆时才生效**（`len(self._memories) > 0`）—— 空库跳过向量检测。
**L3 FTS5 fallback 在向量层 disabled 时才生效**（elif 链）。

## 3. 检索打分融合（`unified_store.py:329-397`）

### 3.1 完整流程

```python
def search_semantic_scored(self, query, limit=10, filter_type=None,
                           scope="user", scope_owner="", user_id="default",
                           workspace_id="default", include_inactive=False):
    # 1. 主后端 = VectorStore/ChromaDB，limit * 3 给去重留余量
    primary = self.search.search(query, limit=limit * 3, filter_type=..., scope=..., user_id=..., workspace_id=...)
    merged: dict[str, float] = {mid: float(s) for mid, s in primary}

    # 2. FTS5 fallback（如果存在 → 必然存在，因为 unified_store 构造时强制建立）
    if self._fts5_fallback is not None:
        try:
            fts_results = self._fts5_fallback.search(query, limit=limit * 3, filter_type=..., scope=..., user_id=..., workspace_id=...)
            for mid, s in fts_results:
                prev = merged.get(mid)
                fs = float(s)
                if prev is None or fs > prev:            # ← max 融合，不加权
                    merged[mid] = fs
        except Exception as _e:
            logger.debug(f"[UnifiedStore] FTS5 union skipped (non-fatal): {_e}")

    # 3. 排序 + 回查 SQLite 过滤 + scope 四元组校验
    ordered = sorted(merged.items(), key=lambda kv: kv[1], reverse=True)

    scored = []
    for memory_id, score in ordered:
        d = self.db.get_memory(memory_id)
        if d:
            if not include_inactive and not self._is_active_dict(d):
                continue                                # 跳过 superseded_by / expires_at 过期
            d_scope = d.get("scope") or "global"
            d_owner = d.get("scope_owner") or ""
            d_user = d.get("user_id") or "default"
            d_workspace = d.get("workspace_id") or "default"
            if (d_scope == scope and d_owner == scope_owner
                and d_user == user_id and d_workspace == workspace_id):
                scored.append((SemanticMemory.from_dict(d), float(score)))
                if len(scored) >= limit:
                    break
    return scored
```

### 3.2 融合公式

**只取最高分（max），无加权、无 RRF、无任何归一化校准。**

```python
if prev is None or fs > prev:
    merged[mid] = fs
```

`prev` 是 Chroma 侧（已翻符号 `1 - distance`），`fs` 是 FTS5 侧（`1/(1+|bm25|)`）。
两边都是 score 语义（越大越好），但**量纲未校准** —— 这是 openakita 的有意近似。

### 3.3 边界

- `primary` 已经按 limit*3 取，FTS5 也按 limit*3 取 —— `limit*6` 候选总量，去重后 limit 输出
- FTS5 fallback 是**整体 try/except**，失败只 debug 日志，不上抛
- 向量 metadata **不参与** scope/user_id/workspace_id 校验，Chroma `where` 不能下推这四元组

## 4. 与 nanobot plan 的对照表

| 项 | openakita | nanobot plan | 是否需要改 plan |
| --- | --- | --- | --- |
| 写入去重 L1（bigram + LLM 兜底） | ✅ extractor.deduplicate | ❌ 不做（plan v1） | **不改** —— extractor._evolve_memory 已覆盖语义层；写入路径是 best-effort |
| 写入去重 L2（向量距离阈值） | ✅ `DUPLICATE_DISTANCE_THRESHOLD = 0.12` | ❌ 不做 | **不改** —— 见 §5 |
| 写入去重 L3（FTS5 子串包含） | ✅ `core_lower[:80] in hit.content.lower()` | ❌ 不做 | **不改** —— 同上 |
| 检索并集公式 | `max(v, f)` | `max(v, f)`（plan §0 / contract §5 I13） | **一致，无需改** |
| 候选宽度 | `limit * 3` | `limit * 3`（plan §7 已规定） | **一致，无需改** |
| 回查 SQLite 滤 active | ✅ `_is_active_dict(d)`（superseded_by + expires_at） | ✅ contract §5 I15、plan §7 已规定 | **一致，无需改** |
| scope 四元组校验 | ✅ scope/scope_owner/user_id/workspace_id | ✅ I15 | **一致，无需改** |
| 距离翻符号 | `score = max(0.0, 1.0 - distance)`（search_backends.py:180） | `_distance_to_score`（plan Task 6 / WU-06） | **一致** |
| FTS5 分数公式 | `1/(1+\|bm25\|)`（search_backends.py:111） | 页内 min-max 归一化（plan Task 9 / WU-09） | **nanobot 更优**（符号免疫） |
| 写入顺序 | VectorStore 先 → SQLite 后（带 retry） | SQLite 先 → VectorStore 后（best-effort） | **设计差异**：nanobot 选择「真相源优先」（plan §0 修正 4 已记录） |

## 5. 为什么 nanobot 不引入 L2 写入去重

虽然 openakita 用了 `DUPLICATE_DISTANCE_THRESHOLD = 0.12`，但**这个阈值未在调研文档中说明选型依据**。强行移植有 3 个风险：

1. **未实测**：阈值在中文短句 + bge 模型下的命中率未实测。0.12 可能是 cosine distance 在某种特定数据集上的经验值。
2. **会污染 best-effort 契约**：plan 写入路径钩子是 best-effort，向量挂了就 log + 跳过；但写入前去重会让向量层**阻塞**写入决策（必须先查 Chroma 拿近邻），破坏「SQLite 是真相源」的边界。
3. **重复判定是语义层职责**：两条「我喜欢创作」「我喜欢写东西」是 paraphrase 还是真的重复？openakita 自己也承认这一点，所以 L2 命中后还要 `_strip_common_prefix` + 字符串等值二次确认 —— 等于又退化为精确匹配。

**Leader 倾向**：nanobot 沿用 plan v1（不引入写入去重）。理由：
- L1（extractor._evolve_memory）由语义层负责，已在 nanobot
- L2 引入未实测阈值，且破坏 best-effort 契约
- L3 在 L2 不可用时才生效，L2 没做 L3 也无意义

如果未来要补，应作为 v2 任务，附实测报告 + 阈值选型依据。

## 6. 给 plan 的最小补丁建议

仅在 plan §0 末尾加一段「v1 写入去重与打分融合立场」说明（30 行内），链接到本调研文档。

**不改 WU-10 实现**（max 融合、limit*3、回查 SQLite、scope 校验均已对齐 openakita）。

## 7. 附：FTS5 分数公式对比

| 方案 | 公式 | 符号免疫 | 备注 |
| --- | --- | --- | --- |
| openakita | `1/(1+\|bm25\|)` | ❌ 依赖 bm25 是正数（实际 FTS5 返回负数，abs 后才正） | `abs(r.get("rank", 0))` 在 search_backends.py:110 |
| nanobot plan v1 | 页内 min-max 归一化 `(hi - rank) / span` | ✅ 只依赖次序 | plan Task 9 / WU-09 已落地 |

**nanobot 比 openakita 更稳**：openakita 偷偷 `abs` 了负数 bm25，结果首名 = 1.0 但后续名可能 > 1（看 bm25 取值），且 L1/ln 系数会引入常量漂移；nanobot 的 min-max 与符号无关，永远首名 = 1.0 末名 = 0.0。
