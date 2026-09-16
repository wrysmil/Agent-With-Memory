---
artifact: contract
route: api-and-interface-design
skills:
  - api-and-interface-design
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - .ai-runtime-artifacts/specs/2026-09-16-vector-retrieval-spec.md
  - .ai-runtime-artifacts/plans/2026-09-16-vector-retrieval-plan.md
created_at: 2026-09-16
status: frozen
topic: nanobot-vector-retrieval
---

# 接口契约：向量检索层

> **冻结日期**：2026-09-16 ｜ 依据：spec §4.2 组件清单 + §3.4 分数语义契约
>
> 本文件冻结**跨 WU 的接口**。并行开发时，各 WU 以**本文件**为准，不以他人实现为准。
> 任何一方需要改签名 → 先改本文件并通知 Leader，不得单方面变更。

## 1. 为什么需要这份契约

`WU-06`（`VectorStore`）与 `WU-07`（`MemoryIndexer`）若串行开发，关键路径会多出一整个 WU 的时长。二者本可并行——只要 `VectorStore` 的**方法集**先冻结。

同理 `WU-08`（写路径钩子）只需要 `MemoryIndexer.index/remove` 的签名，不需要其实现。

## 2. `VectorSettings` —— 冻结

文件：`nanobot/memory/vector/settings.py`

```python
@dataclass(frozen=True)
class VectorSettings:
    enabled: bool = False
    model: str = "BAAI/bge-small-zh-v1.5"
    dimensions: int = 512
    device: str = "cpu"
    download_source: str = "auto"
    local_model_dir: str = ""
    index_path: str = ""
    sync_on_startup: bool = True
    max_candidates: int = 45
```

**约定**

| 项 | 约定 |
| --- | --- |
| 类型 | `@dataclass(frozen=True)`，**不依赖 pydantic** —— 使 `VectorStore` 可脱离 nanobot 配置独立单测 |
| 字段名 | 全 `snake_case`。pydantic 侧到本结构的映射由 `MemoryVectorConfig.to_vector_settings(*, enabled: bool)` 完成 |
| `enabled` | **唯一**的启用开关。由 `memory_search_backend == "chromadb"` 推导，不在 pydantic 模型里单独存字段 |
| `dimensions` | 单位是**向量维度**（512），不是字节。`VectorStore` 启动时与 `model.get_sentence_embedding_dimension()` 比对，不等则拒绝启用 |
| `max_candidates` | `limit * 3` 的上限（spec §3.3）。当前默认 45 对应 engine 的 `_SEMANTIC_LIMIT = 15` |

## 3. `VectorStore` —— 冻结（WU-06 实现，WU-07/WU-11 消费）

文件：`nanobot/memory/vector/store.py`

```python
class VectorStore:
    def __init__(self, settings: VectorSettings, *, workspace: Path) -> None: ...

    # ---- 只读属性（读取 enabled 可能触发一次冷却到期后的后台重试）----
    @property
    def enabled(self) -> bool: ...
    @property
    def state(self) -> str: ...        # "idle" | "loading" | "ready" | "failed"
    @property
    def error(self) -> str | None: ...
    @property
    def model_name(self) -> str: ...
    @property
    def dimensions(self) -> int: ...

    # ---- 写（幂等）----
    def upsert(self, memory_id: str, content: str, metadata: dict[str, Any]) -> bool: ...
    def remove(self, memory_id: str) -> bool: ...

    # ---- 读 ----
    def search(self, query: str, *, limit: int = 15) -> list[tuple[str, float]]: ...
    def count(self) -> int: ...
    def list_ids(self) -> list[str]: ...
    def delete_ids(self, ids: list[str]) -> int: ...
```

**不变量（任何实现都必须满足）**

| # | 不变量 | 验证方式 |
| --- | --- | --- |
| I1 | 构造**不阻塞**：后台线程加载，`__init__` < 100 ms | D6 测试 |
| I2 | 非 `ready` 状态下所有读写方法**立即**返回 `[]` / `False` / `0`，**绝不等待** | D3 测试 |
| I3 | 任何公开方法**绝不抛异常**——异常吞掉、仅 log | D1/D4 测试 |
| I4 | `search` 返回值第二元是 **score ∈ [0,1]，越大越相关**。Chroma 的 distance **不得**泄漏（符号翻转在 `_distance_to_score` 内完成） | 契约测试 |
| I5 | `upsert` 幂等：同 id 重复写 = 覆盖 | 对账幂等测试 |
| I6 | `metadata` 只接受标量；`tags` 键被丢弃（Chroma `where` 不能多标签过滤，写进去会制造假象） | `_clean_metadata` 单测 |
| I7 | `search` 内部 `encode` 必须 `normalize_embeddings=True`，**不加**任何指令前缀（bge-v1.5 无需指令） | 冒烟自检 dim 校验 |

> **I4 是本契约最重要的一条。** 历史上 openakita 的 distance 泄漏到 reranker 是「移植时最容易踩的坑」（调研文档 §6.4）。任何消费方都**可以假设**拿到的是「越大越相关」。

## 4. `MemoryIndexer` —— 冻结（WU-07 实现，WU-08/WU-11/WU-12 消费）

文件：`nanobot/memory/vector/indexer.py`

```python
class MemoryIndexer:
    def __init__(self, store: Any, database: MemoryDatabase) -> None: ...
    def index(self, memory: Memory) -> bool: ...
    def remove(self, memory_id: str) -> bool: ...
    def sync_from_sqlite(self) -> dict[str, Any]: ...
```

**`sync_from_sqlite()` 返回契约（冻结，三个键恒存在）**

```python
{"indexed": int, "deleted": int, "error": str}   # error 为空串表示无错
```

**不变量**

| # | 不变量 |
| --- | --- |
| I8 | `store` 参数只用到 `upsert` / `remove` / `list_ids` / `delete_ids` 四个方法——**不得**触碰 Chroma 私有属性（`_collection`）。因此可用纯内存替身单测 |
| I9 | `sync_from_sqlite` 双向：删 `chroma_ids - sqlite_ids`，补 `sqlite_ids - chroma_ids` |
| I10 | `sync_from_sqlite` **绝不抛出**。单条失败计入 `error` 并**继续**处理其余条目 |
| I11 | 对账后把结果写入 `vector_sync_state` 单行表（写失败不影响返回值） |
| I12 | `index` / `remove` 幂等 |

**进程级钩子（冻结，WU-08 消费）**

```python
def set_active_indexer(indexer: MemoryIndexer | None) -> None: ...
def get_active_indexer() -> MemoryIndexer | None: ...
def index_memory_best_effort(memory: Memory) -> None: ...   # 绝不抛
def remove_memory_best_effort(memory_id: str) -> None: ...  # 绝不抛
```

> 钩子存在的原因：写路径有 5 个点（`extractor.py` / `database.py` / `memory_api.py` ×3）。若各点各自持有 indexer 引用，就要把向量依赖注入到 5 处构造链路。进程级注册把注入收口到 gateway 启动一处。

## 5. `MemoryStoreAdapter` —— 冻结（WU-10 扩展）

文件：`nanobot/memory/retrieval/store_adapter.py`

```python
class MemoryStoreAdapter:
    def __init__(self, database: MemoryDatabase, *, vector_store: Any = None) -> None: ...

    def search_semantic_scored(self, query: str, *, limit: int = 30) -> list[tuple[Memory, float]]: ...
    def search_episodes(self, *, entity: str, limit: int = 5) -> list[Any]: ...
    def query_semantic(self, *, min_importance: float, since_days: int, limit: int) -> list[Any]: ...
    def search_attachments(self, term: str, *, intent: str, limit: int = 5) -> list[Any]: ...
```

**新增参数的兼容性约定**

| 项 | 约定 |
| --- | --- |
| `vector_store` | **关键字参数、默认 `None`**。`None` → 行为与修复批次**完全一致**（纯 FTS5），既有调用方与 `_StubStore` 测试替身零改动 |
| `vector_store` 需要的接口 | **只有** `search(query, limit) -> list[(id, score)]`。Adapter 不关心它是不是 `VectorStore` |
| 异常纪律 | 向量侧抛异常 → 记 warning + 降级为纯 FTS5。**不得**上抛（Adapter 是契约边界，不假设实现的异常纪律） |

**`search_semantic_scored` 融合契约**

```
merged = {}                                   # id -> score
for id, s in fts5_hits:  merged[id] = max(merged.get(id, 0), s)
for id, s in vector_hits: merged[id] = max(merged.get(id, 0), s)
ordered = sorted(merged.items(), key=score, desc)[:limit]
```

| # | 不变量 |
| --- | --- |
| I13 | 同一个 id **只出现一次**，分数取两侧**较高**者 |
| I14 | 输出按 score **降序** |
| I15 | 向量独有 id 必须**回查 SQLite**；查不到（僵尸 id）→ 丢弃。查到但 `superseded_by` 非空或 `expires_at` 已过期 → 丢弃（spec §4.6） |
| I16 | FTS5 侧结果**原样透传**，不做额外活性过滤（避免对既有 FTS5 行为引入回归——见 plan §0 修正 3） |

## 6. 分数语义契约 —— 冻结（spec §3.4）

| 层 | 语义 | 实现方 |
| --- | --- | --- |
| Chroma `collection.query` | distance，**越小越相似**，cosine ∈ [0,2] | 第三方 |
| `VectorStore._distance_to_score` | `clamp(1 - distance, 0, 1)` —— **符号翻转的唯一发生点** | WU-06 |
| `repository.search_semantic_scored`（FTS5） | 页内 min-max 归一化，首名 1.0、末名 0.0 | WU-09 |
| Adapter 输出 | `float ∈ [0,1]`，**越大越相关** | WU-10 |
| `RetrievalCandidate.relevance` | 越大越相关 | 既有 |
| `Reranker` | `0.4×Rel + 0.2×Rec + 0.2×Imp + 0.2×Access`，阈值 `_MIN_COMPOSITE = 0.35` | 既有 |

🔴 **任何消费方都可以假设「score 越大越相关」。distance 语义不得越过 `VectorStore` 边界。**

> ⚠️ 阈值 `0.35` 是否被向量分击穿是**已知风险 R-5**，须在 `WU-13` 实测。若击穿，处置见 plan Task 13 Step 3。

## 7. SQLite 契约（WU-04 交付）

```python
@dataclass
class VectorSyncState:
    cursor: str
    indexed: int
    deleted: int
    last_error: str
    updated_at: str

def get_vector_sync_state(conn) -> VectorSyncState | None: ...
def upsert_vector_sync_state(conn, *, cursor: str, indexed: int, deleted: int, last_error: str) -> None: ...
```

| 项 | 约定 |
| --- | --- |
| 表名 | `vector_sync_state`（单行，`id` 恒为 1） |
| schema 版本 | `_SCHEMA_VERSION` 从 `"1"` → `"2"` |
| 幂等 | 新语句必须 `CREATE TABLE IF NOT EXISTS`（`ensure_schema` 全量重跑） |

## 8. HTTP API 契约（WU-11 交付）

| 方法 | 路径 | action | 返回 |
| --- | --- | --- | --- |
| GET | `/api/settings/memory/stats` | `memory-stats` | 既有 `{total, by_type}` **追加** 7 键 |
| POST | `/api/settings/memory/vector/reindex` | `vector-reindex` | `{available, indexed, deleted, error, vector_count}` |
| POST | `/api/settings/memory/vector/sync` | `vector-sync` | `{available, indexed, deleted, error}` |

**`stats` 新增的 7 个键（冻结，字段集恒定）**

```
search_backend       str    "fts5" | "chromadb" | "api_embedding"
vector_available     bool   state == "ready"
vector_state         str    "idle" | "loading" | "ready" | "failed" | "disabled"
vector_count         int
vector_model         str
vector_dimensions    int
vector_error         str | None
```

> **字段集恒定**：向量未启用时这些键**依然存在**（值分别为 `"fts5"` / `False` / `"disabled"` / `0` / `""` / `0` / `None`），使前端无需做存在性判断。
>
> `reindex` / `sync` 是**写操作**，须同时注册进 `_MEMORY_MUTATION_PATHS`。

## 9. 变更记录

| 轮次 | 日期 | 变更摘要 |
| --- | --- | --- |
| 1 | 2026-09-16 | 初稿冻结。关键决策：`__init__.py` 不做 re-export（使 WU-05/06/07 可并行）；`vector_store` 为可选关键字参数（保既有契约零改动） |
