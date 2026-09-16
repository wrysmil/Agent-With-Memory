---
artifact: research
route: web-investigator
source:
  - .ai-runtime-artifacts/plans/2026-09-16-vector-retrieval-plan.md
  - .ai-runtime-artifacts/research/2026-09-16-vector-retrieval-dedup-scoring.md
  - .ai-runtime-artifacts/research/2026-09-16-openakita-vectorization-research.md
created_at: 2026-09-16
topic: retrieval-flow-with-vector
---

# 向量检索接入后的检索流程

## 1. 数据流总览（双层架构）

```mermaid
flowchart LR
    User([用户 query]) --> AgentLoop[AgentLoop.after_run]
    AgentLoop --> Engine[RetrievalEngine.retrieve]

    Engine --> Channel1[Channel: recent]
    Engine --> Channel2[Channel: episodes]
    Engine --> Channel3[Channel: semantic]
    Engine --> Channel4[Channel: scratchpad]
    Engine --> Channel5[Channel: attachments]

    Channel3 --> Adapter[MemoryStoreAdapter]
    Adapter --> Vector[VectorStore.search<br/>cosine 距离 → score]
    Adapter --> FTS5[(SQLite FTS5<br/>memories_fts)]

    Vector --> Chroma[(ChromaDB<br/>本地嵌入式<br/>workspace/memory/chromadb)]
    FTS5 --> SQLite[(SQLite<br/>memories 表)]
    SQLite --> Adapter

    Channel1 --> Rerank[Reranker<br/>0.4·Rel + 0.2·Rec + 0.2·Imp + 0.2·Access]
    Channel2 --> Rerank
    Channel3 --> Rerank
    Channel4 --> Rerank
    Channel5 --> Rerank

    Rerank -->|threshold ≥ 0.35| Inject[格式化注入块<br/>to_markdown]
    Inject --> LLM[LLM prompt]

    style Chroma fill:#fce4ec,stroke:#c2185b
    style FTS5 fill:#e3f2fd,stroke:#1976d2
    style SQLite fill:#e3f2fd,stroke:#1976d2
```

**核心结论**：用户 query → RetrievalEngine → 5 个并行 channel → 只有 semantic channel
会**双源**查询（Chroma + FTS5），其余 channel 维持现状。其他流程零变化。

## 2. 一次 `search_semantic_scored` 调用的细节（核心变更点）

```mermaid
sequenceDiagram
    autonumber
    participant C as channel/semantic.py
    participant A as MemoryStoreAdapter
    participant V as VectorStore<br/>(state=ready?)
    participant DB as ChromaDB<br/>(本地嵌入式)
    participant F as SQLite<br/>memories_fts
    participant S as SQLite<br/>memories 表
    participant R as Reranker

    C->>A: search_semantic_scored(query, limit=30)
    Note over A: limit * 3 = 90<br/>(给去重留余量)

    par 并行
        A->>V: search(query, limit=90)
        V->>DB: encode(query) +<br/>collection.query(n=90)
        DB-->>V: [(id, distance)] × 90
        V->>V: _distance_to_score<br/>clamp(1-d, 0, 1)
        V-->>A: [(id, score∈[0,1])]
    and
        A->>F: SELECT m.*, bm25(memories_fts)<br/>FROM memories_fts ... ORDER BY _rank
        F-->>A: rows
        A->>A: 页内 min-max 归一化<br/>(hi - rank) / span
        A-->>A: [(Memory, score∈[0,1])]
    end

    A->>A: dict 合并<br/>merged[id] = max(v_score, f_score)

    loop 按 score 降序遍历
        A->>S: SELECT * FROM memories WHERE id=?
        alt 命中 + 无 superseded_by + 未过期 + scope 匹配
            S-->>A: Memory row
            A->>A: append to scored
        else 僵尸 / 过期 / scope 不匹配
            S-->>A: None 或不匹配
            A->>A: 丢弃
        end
        Note over A: 累计到 limit 即停止
    end

    A-->>C: [(Memory, score)] × 30
    C->>C: 构造 RetrievalCandidate<br/>(source_channel="semantic")
    C->>R: reranker 接收综合分<br/>0.4·Rel + 0.2·(Rec+Imp+Acc)
    R->>R: 过滤 _MIN_COMPOSITE=0.35
    R-->>C: 保留的候选
```

## 3. 写入流程（best-effort 挂载）

```mermaid
flowchart LR
    W1[extractor.py<br/>记忆抽取] -->|ok=True| SQLite
    W2[database.py<br/>fallback replay] --> SQLite
    W3[memory_api.py<br/>create] --> SQLite
    W4[memory_api.py<br/>update] --> SQLite
    W5[memory_api.py<br/>delete] --> SQLite

    SQLite[(SQLite<br/>memories 表<br/>真相源)]

    SQLite -.SQLite 落库成功.-> Hook[index_memory_best_effort]
    SQLite -.删除成功.-> Hook2[remove_memory_best_effort]
    Hook --> Indexer[MemoryIndexer]
    Hook2 --> Indexer
    Indexer --> Vector[VectorStore.upsert / remove]
    Vector --> Chroma[(ChromaDB)]

    style Chroma fill:#fce4ec,stroke:#c2185b
    style SQLite fill:#e3f2fd,stroke:#1976d2

    Vector -.失败.-> Log[log + 跳过<br/>绝不抛]
```

**关键边界**：SQLite 是**唯一真相源**。向量写入失败只 log，下次启动 `MemoryIndexer.sync_from_sqlite()` 会**自动补偿**（delete_ids + upsert 补漏）。

## 4. 与现状（无向量层）的差异

| 环节 | 现状（无向量） | 加向量后 |
|---|---|---|
| 检索后端 | FTS5 单源 | **Chroma + FTS5 双源**（同 score 量纲） |
| semantic channel 候选 | FTS5 命中 30 条 | **Chroma 90 + FTS5 90，取最高分合并，回查 SQLite 后留 30** |
| 写入路径 | SQLite only | SQLite first → best-effort 索引到 Chroma |
| 启动耗时 | baseline | **+100ms 后台线程**（spec D6，已实测 WU-06） |
| 离线/依赖缺失 | FTS5 always works | Chroma 不可用 → 静默降级到 FTS5 only |
| 维度错配 | n/a | 模型实际 dim ≠ 配置 → 拒绝启用 + 写 error |

## 5. 启动时的握手

```mermaid
sequenceDiagram
    participant GW as gateway_runtime.py
    participant IDX as MemoryIndexer
    participant VS as VectorStore
    participant CH as ChromaDB
    participant SY as vector_sync_state

    GW->>VS: VectorStore(settings, workspace)
    Note over VS: state=loading<br/>后台线程加载

    GW->>IDX: MemoryIndexer(vs, db)
    GW->>IDX: set_active_indexer(indexer)

    par 后台加载
        VS->>CH: PersistentClient(path)
        VS->>CH: get_or_create_collection<br/>(hnsw:space=cosine)
        VS->>VS: dim 校验
        VS->>VS: state=ready (或 failed)
    and 启动对账
        IDX->>CH: list_ids()
        IDX->>DB: SELECT id FROM memories
        IDX->>CH: delete_ids(stale)
        IDX->>CH: upsert(missing)
        IDX->>SY: upsert_vector_sync_state(<br/>indexed=N, deleted=M)
    end
```

## 6. 关键不变量（保留自查）

1. **SQLite = 真相源**：向量层可重建、可丢失，不影响记忆数据
2. **score 越大越相关**：`_distance_to_score` 翻符号的唯一发生点 = `VectorStore` 边界
3. **零失败上抛**：VectorStore 任一方法异常 → `[]` / `False` / `0` + warning log
4. **启动 +100ms 内完成**：VectorStore 构造非阻塞，后台线程加载
5. **写顺序 = SQLite first**：落库成功后才索引（避免索引里有 SQLite 没的孤儿）

## 7. 一句话总结

**修改是只增不改**：原本 FTS5 单源检索变成「FTS5 + Chroma 取最高分」的双源检索，但所有其他环节（5 个 channel、reranker、注入块、LLM prompt）零改动。Chromadb 是进程内嵌入式，不需要额外装服务。
