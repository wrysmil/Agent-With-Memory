# openakita 记忆向量化实现调研（为 nanobot 移植）

> 调研日期：2026-09-16
> 调研对象：`D:\studyspace\源码学习\openakita\openakita`（openakita）
> 改造对象：`D:\studyspace\源码学习\Agent-With-Memory`（nanobot，feature/memory-system 分支）
> 用途：为 nanobot 接入**真向量检索**提供事实依据。**不复制代码、不"增强"调研对象**，只陈述 openakita 实际做了什么。
>
> ⚠️ **2026-09-16 已做一轮对抗性核验**（独立 agent 逐条比对源码）。行号与结论已按核验结果修正，修正处标 `核验修正`。
> 使用提醒：本文若干代码块是**节选/重排**，非逐字摘录；若要作为移植依据逐字复制，请回源核对。

---

## 〇、先回答一个前置问题：nanobot 现在是什么状态

| 项                     | nanobot 现状                                                               | 位置                                           |
| ---------------------- | -------------------------------------------------------------------------- | ---------------------------------------------- |
| 检索后端               | 只有 FTS5（BM25 + LIKE 兜底）                                              | `nanobot/memory/retrieval/search_backend.py` |
| `chromadb` 分支      | **stub**：检查依赖 → 无论如何 `return Fts5SearchBackend(db_path)` | 同文件`create_search_backend` 109-127 行     |
| `api_embedding` 分支 | **stub**：同上                                                       | 同文件 121-125 行                              |
| SearchBackend 协议     | 只有一个方法`search(query, *, limit) -> list[dict]`                      | 同文件 10-13 行                                |
| 向量依赖               | 未声明                                                                     | `nanobot/config/schema.py` 无相关字段        |
| 配置项                 | 只有`memory_enabled` / `memory_idle_seconds`                           | `schema.py:157-166`                          |
| embedding 表           | 无                                                                         | `nanobot/memory/database.py` schema          |

**结论**：nanobot 的向量能力是"接口占位、实现为空"。参考 openakita 是可行的，但**不能照抄接口**（见 §八）。

---

## 一、openakita 的架构总览

```
MemoryManager (门面, memory/manager.py)
  │
  ├─ self.vector_store: VectorStore | None        ← 只在 backend_type=="chromadb" 时创建
  │
  └─ self.store: UnifiedStore                     ← SQLite 是唯一真相源
       ├─ self.db: MemoryStorage   (storage.py)   ← SQLite + FTS5 表
       ├─ self.search: SearchBackend               ← 可插拔（三选一）
       │    ├─ FTS5Backend        (默认, 零依赖, jieba 分词)
       │    ├─ ChromaDBBackend    (可选) → 包 VectorStore(vector_store.py)
       │    └─ APIEmbeddingBackend(可选) → DashScope/OpenAI + SQLite 缓存
       └─ self._fts5_fallback: FTS5Backend | None  ← 主后端非 FTS5 时启用，做并集
```

### 三条核心设计取舍

1. **SQLite 是唯一真相源，向量库只是可重建的索引**。
   `UnifiedStore.save_semantic` 先写 SQLite，再同步到 SearchBackend。向量库丢了/坏了不影响数据本身。
   ⚠️ 注意区分两个层级：**`UnifiedStore.save_semantic` 是「SQLite → backend」**；而**上层 `MemoryManager.add_memory` 是「VectorStore → SQLite」**（见 §6.2 路径 A）。两者顺序相反且都成立——`MemoryManager` 先经 VectorStore 做**去重查询**并写入向量，再走 `UnifiedStore.save_semantic` 落 SQLite。移植时不要混淆这两个层级。
2. **ChromaDB 启用时仍然并集一次 FTS5**（`unified_store.py:342-344` 有原文注释）。
   理由：向量写入是异步的、可能滞后，新写入的记忆会被静默漏掉。
3. **所有向量操作绝不阻塞启动**。
   模型加载在后台 daemon 线程，加载期间所有查询优雅降级返回空。

---

## 二、关键文件清单

| 路径                                                      | 职责                                                                            |
| --------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `src/openakita/memory/vector_store.py`（580 行）        | VectorStore 本体：ChromaDB client + collection + model.encode，状态机与失败冷却 |
| `src/openakita/memory/model_hub.py`（419 行）           | 模型下载源：HF_ENDPOINT 配置、三源探测、分层重试                                |
| `src/openakita/memory/search_backends.py`（425 行）     | SearchBackend 协议 + 三后端实现 +`create_search_backend` 工厂                 |
| `src/openakita/memory/unified_store.py`（731 行）       | 协调 SQLite + SearchBackend；`search_semantic_scored` 并集检索                |
| `src/openakita/memory/manager.py`                       | MemoryManager：实例化 VectorStore、注入 UnifiedStore、去重、增删                |
| `src/openakita/memory/lifecycle.py`                     | `_sync_vector_store()` 双向索引修复（删 stale / 补 missing）                  |
| `src/openakita/memory/retrieval.py`                     | `_search_semantic` 调 `search_semantic_scored` 生成候选                     |
| `src/openakita/config.py:525-556`                       | 全部向量/embedding/search_backend 配置字段                                      |
| `src/openakita/api/routes/memory.py`                    | 记忆 HTTP API（prefix`/api/memories`）                                        |
| `src/openakita/optional_modules.json`                   | `vector-memory` 可选模块声明（含模型名、包体积）                              |
| `apps/setup-center/src/views/MemoryView.tsx`（1158 行） | 前端记忆管理界面（React + Vite + Tauri）                                        |

**勘误**：`get_injection_context` / `record_turn` **不在** VectorStore 上，它们在 `MemoryManager`（`manager.py:2360` / `manager.py:1227`）。VectorStore 只有 `add_memory` / `search` / `async_search` / `delete_memory` / `update_memory` / `get_stats` / `clear` / `batch_add`。

---

## 三、VectorStore 完整实现（`vector_store.py`）

### 3.1 依赖延迟导入（25-78 行）

```python
_sentence_transformers_available = None
_chromadb = None

def _lazy_import():
    global _sentence_transformers_available, _chromadb
    if _sentence_transformers_available is None:
        import sys
        if "sentence_transformers" not in sys.modules:
            try:
                from openakita.runtime_env import inject_module_paths_runtime
                inject_module_paths_runtime()          # 支持"运行期安装后免重启"
            except Exception:
                pass
        try:
            import sentence_transformers  # noqa: F401
            _sentence_transformers_available = True
        except ImportError as e:
            ...
            _sentence_transformers_available = False
            return False
    ...
    if _chromadb is None:
        try:
            import os
            os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")   # 先禁遥测
            os.environ.setdefault("CHROMA_TELEMETRY", "False")
            import chromadb
            _chromadb = chromadb
        except ImportError as e:
            ...
            return False
    return True
```

两个细节值得注意：

- `inject_module_paths_runtime()` 刷新 `sys.path`，让"服务运行期间通过设置中心装 optional 模块"无需重启。
- **导入 chromadb 前先设遥测环境变量**，否则 posthog 缺失会导致 ImportError。

### 3.2 初始化状态机（124-133 + 135-298 行）

```python
self._init_state = "idle"        # idle → loading → ready / failed
self._init_failed = False
self._init_fail_time: float = 0.0
self._init_retry_cooldown: float = 300.0   # 失败后 5 分钟冷却
self._retry_count: int = 0
self._import_missing: bool = False         # ImportError 标记
self._lock = threading.RLock()

self._start_background_init()    # 构造末尾立即起后台线程
```

**关键顺序 —— 先设 HF_ENDPOINT，再 import sentence_transformers**（157-252 行）：

```python
def _do_initialize_inner(self) -> None:
    import time as _time
    # ── 关键：在导入 sentence_transformers 之前就配置好 HF_ENDPOINT ──
    # sentence_transformers 导入时会触发 huggingface_hub 导入，
    # 而 huggingface_hub 在模块级缓存 HF_ENDPOINT。
    try:
        from .model_hub import _apply_source_env, _resolve_source
        resolved = _resolve_source(self.download_source)
        if resolved.value == "auto":
            from .model_hub import detect_best_source
            resolved = detect_best_source()
        _apply_source_env(resolved)
    except Exception as e:
        logger.debug(f"[VectorStore] 预配置 HF_ENDPOINT 失败 (非致命): {e}")

    if not _lazy_import():
        with self._lock:
            self._enabled = False
            self._init_state = "failed"
            self._import_missing = True
            self._init_fail_time = _time.monotonic()
            self._retry_count += 1
        return

    try:
        from .model_hub import load_embedding_model
        model = load_embedding_model(
            model_name=self.model_name, source=self.download_source, device=self.device,
        )
        chromadb_dir = self.data_dir / "chromadb"
        chromadb_dir.mkdir(parents=True, exist_ok=True)
        from chromadb.config import Settings
        client = _chromadb.PersistentClient(
            path=str(chromadb_dir),
            settings=Settings(anonymized_telemetry=False),
        )
        collection = client.get_or_create_collection(
            name="memories",
            metadata={"hnsw:space": "cosine"},
        )
        with self._lock:                    # 全部成功 → 原子切 ready
            self._model = model
            self._client = client
            self._collection = collection
            self._enabled = True
            self._init_state = "ready"
            self._init_failed = False
            self._retry_count = 0
    except Exception as e:
        ...
        with self._lock:
            self._enabled = False
            self._init_state = "failed"
            self._init_failed = True
            self._init_fail_time = _time.monotonic()
            self._retry_count += 1
```

**失败冷却与指数退避**（254-298 行）—— 降级链的核心：

```python
def _ensure_initialized(self) -> bool:
    """设计原则：**绝不阻塞调用方**。"""
    global _sentence_transformers_available, _chromadb
    with self._lock:
        if self._init_state == "ready" and self._enabled:
            return True
        if self._init_state == "loading":
            return False                        # 加载中 → 调用方降级

        if self._init_failed:
            import time as _time
            # 依赖缺失：指数退避 300s → 600s → 1200s → … → 3600s 封顶
            if self._import_missing:
                cooldown = min(self._init_retry_cooldown * (2 ** (self._retry_count - 1)), 3600.0)
            else:
                cooldown = self._init_retry_cooldown       # 普通失败：固定 300s
            elapsed = _time.monotonic() - self._init_fail_time
            if elapsed < cooldown:
                return False
            self._init_failed = False
            _sentence_transformers_available = None        # 重置导入缓存
            _chromadb = None
    self._start_background_init()
    return False

@property
def enabled(self) -> bool:
    return self._ensure_initialized()
```

**设计要点**：

- 普通初始化失败 → **固定 300s** 冷却（避免反复重试拖慢）。
- 只有 `ImportError`（依赖可能被用户中途装上）才走**指数退避到 1 小时封顶**。
- 每次读 `enabled` 属性都是一次潜在的重试触发点。

### 3.3 embedding 生成与 ChromaDB metadata

**embedding 由 VectorStore 自己 `model.encode`，不用 ChromaDB 内置 embedding function。**

`add_memory`（305-354 行）：

```python
def add_memory(self, memory_id, content, memory_type, priority, importance, tags=None) -> bool:
    if not self._ensure_initialized():
        return False
    try:
        embedding = self._model.encode(content).tolist()
        with self._lock:
            self._collection.add(
                ids=[memory_id],
                embeddings=[embedding],
                documents=[content],
                metadatas=[{
                    "type": memory_type,
                    "priority": priority,
                    "importance": importance,
                    "tags": ",".join(normalize_tags(tags)),
                }],
            )
        return True
    except Exception as e:
        logger.error(f"Failed to add memory to vector store: {e}")
        return False
```

**存储位置**：`{data_dir}/chromadb/`（PersistentClient 本地目录）；collection 名固定 `"memories"`；metric `cosine`。

**metadata 只有 4 个标量字段**：`type`(str) / `priority`(str) / `importance`(float) / `tags`(逗号拼接 str)。
⚠️ `tags` 被拼成单个字符串，只能整体过滤，**不能做真正的多标签过滤**。

`search`（356-412 行）返回 **`[(memory_id, distance)]`，距离越小越相似**：

```python
def search(self, query, limit=10, filter_type=None, min_importance=0.0) -> list[tuple[str, float]]:
    if not self._ensure_initialized():
        return []
    try:
        query_embedding = self._model.encode(query).tolist()
        with self._lock:
            where = {"type": filter_type} if filter_type else None
            results = self._collection.query(
                query_embeddings=[query_embedding], n_results=limit, where=where,
            )
        if not results["ids"] or not results["ids"][0]:
            return []
        ids = results["ids"][0]
        distances = results["distances"][0] if results.get("distances") else [0] * len(ids)
        if min_importance > 0 and results.get("metadatas"):
            # min_importance 只能在 Python 侧过滤：Chroma 的 where 对 float 比较支持有限
            filtered = []
            for i, (mid, dist) in enumerate(zip(ids, distances, strict=False)):
                if results["metadatas"][0][i].get("importance", 0) >= min_importance:
                    filtered.append((mid, dist))
            return filtered
        return list(zip(ids, distances, strict=False))
    except Exception as e:
        logger.error(f"Vector search failed: {e}")
        return []
```

`async_search`（414-426 行）—— 把 CPU 密集的 encode 丢线程池，避免阻塞事件循环：

```python
async def async_search(self, query, limit=10, filter_type=None, min_importance=0.0):
    return await asyncio.to_thread(self.search, query, limit, filter_type, min_importance)
```

其余：`delete_memory`(428-448) = `collection.delete(ids=[id])`；`update_memory`(450-499) = `collection.update`；`get_stats`(501-512) 返回 `{enabled, count, model, device}`；`clear`(514-531) = 删 collection 重建；`batch_add`(533-580) = `encode(contents)` 批量编码。

### 3.4 降级行为汇总

| 场景                                  | 行为                                                                   |
| ------------------------------------- | ---------------------------------------------------------------------- |
| sentence-transformers / chromadb 未装 | `_import_missing=True` → 指数退避重试；所有方法返回空/False         |
| 模型下载失败                          | `_init_state="failed"`，固定 300s 冷却后台重试；期间查询空           |
| 后台线程仍在加载                      | `_ensure_initialized` 返回 False，查询立即返回空（**不阻塞**） |
| encode / query 运行期抛异常           | 方法内 try/except 吞掉，log error，返回`[]` / `False`              |
| 主后端不可用                          | 上层`create_search_backend` 回退 FTS5                                |

---

## 四、模型加载与多源下载（`model_hub.py`）

### 4.1 三源定义（36-43 行）

```python
HF_MIRROR_ENDPOINT = "https://hf-mirror.com"

class ModelSource(StrEnum):
    AUTO = "auto"
    HUGGINGFACE = "huggingface"
    HF_MIRROR = "hf-mirror"
    MODELSCOPE = "modelscope"
```

### 4.2 三源探测 `detect_best_source`（82-148 行）

```python
def detect_best_source() -> ModelSource:
    # 中文系统环境优先 hf-mirror，避免网络探测浪费时间
    import locale
    try:
        lang = locale.getlocale()[0] or os.environ.get("LANG", "")
        if lang and lang.lower().startswith("zh"):
            return ModelSource.HF_MIRROR
    except Exception:
        pass

    mirror_time = _probe_url(HF_MIRROR_ENDPOINT, timeout=2.0)
    if mirror_time < 2.0:
        return ModelSource.HF_MIRROR           # 国内镜像够快直接用

    hf_time = _probe_url("https://huggingface.co", timeout=2.0)
    if hf_time < mirror_time and hf_time < 2.0:
        return ModelSource.HUGGINGFACE

    best_time = min(mirror_time, hf_time)
    if best_time > 3.0:
        try:
            import modelscope  # noqa: F401
            return ModelSource.MODELSCOPE      # 两个 HF 源都慢 → ModelScope
        except ImportError:
            pass

    if best_time == float("inf"):
        return ModelSource.HF_MIRROR           # 全超时兜底
    return ModelSource.HF_MIRROR if mirror_time < float("inf") else ModelSource.HUGGINGFACE
```

`_probe_url`（64-79 行）用 `urllib.request` 发 HEAD 测延迟，异常返回 `inf`。

### 4.3 HF_ENDPOINT 双写（156-205 行）—— 全项目最关键的一个坑

```python
def _apply_source_env(source: ModelSource) -> None:
    if source == ModelSource.HF_MIRROR:
        os.environ["HF_ENDPOINT"] = HF_MIRROR_ENDPOINT
        _sync_hf_hub_endpoint(HF_MIRROR_ENDPOINT)
    elif source == ModelSource.HUGGINGFACE:
        os.environ.pop("HF_ENDPOINT", None)
        _sync_hf_hub_endpoint("https://huggingface.co")
    ...
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")

def _sync_hf_hub_endpoint(endpoint: str) -> None:
    """huggingface_hub 在模块导入时将 HF_ENDPOINT 缓存到模块常量，
    后续修改 os.environ 不会影响缓存值。必须直接 patch 模块属性。
    不同版本属性名不同:
    - >=0.25: constants.ENDPOINT (不带 HF_ 前缀)
    - 旧版本: constants.HF_ENDPOINT
    """
    import sys
    hub_mod = sys.modules.get("huggingface_hub")
    if hub_mod is None:
        return
    constants = getattr(hub_mod, "constants", None)
    if constants is not None:
        for attr in ("ENDPOINT", "HF_ENDPOINT"):
            if hasattr(constants, attr):
                setattr(constants, attr, endpoint)
    for attr in ("ENDPOINT", "HF_ENDPOINT"):
        if hasattr(hub_mod, attr):
            setattr(hub_mod, attr, endpoint)
```

**只改 `os.environ["HF_ENDPOINT"]` 在 huggingface_hub 已导入时完全无效**，必须同时 patch `huggingface_hub.constants`，且要兼容 0.25 前后两个属性名。这就是 VectorStore 必须在 `import sentence_transformers` 之前调 `_apply_source_env` 的原因。

### 4.4 分层重试 `load_embedding_model`（289-385 行）

```
Layer 1: huggingface_hub 内部重试（5 次，1-2-4s 退避）—— 第三方库自带
Layer 2: 源级别回退（当前源 → hf-mirror → modelscope）—— _load_from_hf
Layer 3: 本函数整体重试（默认 2 轮，3-6s 指数退避）
```

auto 模式先查本地缓存，命中则离线加载（326-342 行）：

```python
if resolved == ModelSource.AUTO:
    if _is_model_cached(model_name):
        from sentence_transformers import SentenceTransformer
        old_offline = os.environ.get("HF_HUB_OFFLINE")
        os.environ["HF_HUB_OFFLINE"] = "1"
        try:
            return SentenceTransformer(model_name, device=device)
        except Exception as e:
            logger.warning(f"[ModelHub] 离线加载缓存失败 ({e})，将重新下载")
        finally:
            ... # 恢复 HF_HUB_OFFLINE
    resolved = detect_best_source()
```

Layer 2 源回退（253-286 行）：

```python
def _load_from_hf(model_name: str, device: str = "cpu"):
    from sentence_transformers import SentenceTransformer
    current_endpoint = os.environ.get("HF_ENDPOINT", "")
    try:
        return SentenceTransformer(model_name, device=device)
    except Exception as e:
        if HF_MIRROR_ENDPOINT not in current_endpoint:
            _apply_source_env(ModelSource.HF_MIRROR)
            try:
                return SentenceTransformer(model_name, device=device)
            except Exception as e2:
                logger.warning(f"[ModelHub] hf-mirror 也失败了: {e2}")
        try:
            return _load_from_modelscope(model_name, device)
        except Exception:
            raise e
```

`_is_model_cached`（393-419 行）检查 `~/.cache/huggingface/hub/models--<org>--<name>`（含 `HF_HOME` 自定义路径）。

---

## 五、配置项完整列表（`config.py:525-556`）

Pydantic Settings，`env_file=".env"`，字段名大写即环境变量名：

| 配置字段                   | 环境变量                   | 默认值                               | 作用                                          |
| -------------------------- | -------------------------- | ------------------------------------ | --------------------------------------------- |
| `model_download_source`  | `MODEL_DOWNLOAD_SOURCE`  | `auto`                             | 下载源：auto/huggingface/hf-mirror/modelscope |
| `embedding_model`        | `EMBEDDING_MODEL`        | `shibing624/text2vec-base-chinese` | 本地 embedding 模型                           |
| `embedding_device`       | `EMBEDDING_DEVICE`       | `cpu`                              | 运行设备 cpu/cuda                             |
| `search_backend`         | `SEARCH_BACKEND`         | `fts5`                             | 后端：fts5/chromadb/api_embedding             |
| `embedding_api_provider` | `EMBEDDING_API_PROVIDER` | `""`                               | 在线 provider：dashscope/openai               |
| `embedding_api_key`      | `EMBEDDING_API_KEY`      | `""`                               | 在线 API key                                  |
| `embedding_api_model`    | `EMBEDDING_API_MODEL`    | `text-embedding-v3`                | 在线模型名                                    |

**三个已知缺陷（移植时应当修，别照抄）**：

1. **没有显式维度配置字段**。维度只在 `APIEmbeddingBackend.__init__` 硬编码默认 `dimensions: int = 1024`（`search_backends.py:223`），而且 `create_search_backend` 的 `api_dimensions` 参数**没有从 config 传入**（`unified_store.py:56-63` 只传了 provider/key/model），所以实际永远走 1024。**`text-embedding-3-small` 默认 1536 维，与 DashScope v3 的 1024 不同，混用直接报维度错。**
2. **没有 `vector_enabled` 布尔开关**。启用与否由 `search_backend == "chromadb"` 隐式决定。
3. **没有索引路径配置**，固定 `{data_dir}/chromadb`，而 `data_dir = project_root / "data" / "memory"`（`_agent_runtime.py:744`）。

其他：

- 安装向导 (`setup/wizard.py:1165-1208`) 只让用户选 `EMBEDDING_MODEL` / `EMBEDDING_DEVICE` / `MODEL_DOWNLOAD_SOURCE`，**不设 `search_backend`**（保持 fts5 默认，需手改 .env）。
- 可选模块声明 `optional_modules.json:4-18`：id `vector-memory`，包 `sentence-transformers>=2.2.0,<3.0` + `chromadb>=0.4.0` + `regex>=2023.6.3,<2025`，**约 2500MB**，默认模型 `shibing624/text2vec-base-chinese`。

---

## 六、接线链路（实例化 → 注入 → 使用）

### 6.1 链路图

```
settings (config.py:522-556)
   │  embedding_model / embedding_device / model_download_source
   │  search_backend / embedding_api_provider / embedding_api_key / embedding_api_model
   ▼
AgentRuntime.__init__  (core/_agent_runtime.py:743-755)
   │  MemoryManager(data_dir=project_root/"data"/"memory", ...)
   ▼
MemoryManager.__init__  (memory/manager.py:172-282)
   │
   ├─ if search_backend == "chromadb":                  # manager.py:202
   │      self.vector_store = VectorStore(              # manager.py:203-208
   │          data_dir=self.data_dir, model_name=embedding_model,
   │          device=embedding_device, download_source=model_download_source)
   │  else:
   │      self.vector_store = None                      # manager.py:210
   │
   └─ self.store = UnifiedStore(                        # manager.py:275-282
          db_path=data_dir/"openakita.db",
          vector_store=self.vector_store,                # 注入
          backend_type=search_backend,
          api_provider=embedding_api_provider,
          api_key=embedding_api_key,
          api_model=embedding_api_model)
             │
             ▼
      UnifiedStore.__init__  (unified_store.py:40-67)
         self.db = get_shared_storage(db_path)          # SQLite（进程级单例）
         self.search = create_search_backend(
             backend_type, storage=self.db,
             vector_store=vector_store, api_provider=..., api_key=..., api_model=...)
         self._fts5_fallback = FTS5Backend(self.db)  if backend_type != "fts5"
             │
             ▼
      create_search_backend  (search_backends.py:389-425)
         "chromadb" + vector_store 且 backend.available
              → ChromaDBBackend(vector_store)   # 否则 warning + 落 FTS5
         "api_embedding" + api_key 且 available
              → APIEmbeddingBackend(storage, ...)
         兜底 → FTS5Backend(storage)
```

**`self.vector_store` 的持有者是 MemoryManager，不是 UnifiedStore。**
UnifiedStore 通过 `ChromaDBBackend._vs` 间接引用**同一个对象**——所以 MemoryManager 调 `vector_store.add_memory()` 写入，而检索走 `store.search`。

其他实例化点：

- `agents/factory.py:519-530` — 隔离 sub-agent 的 MemoryManager，传同一批 settings（per-agent data_dir）。
- `scheduler/executor.py:1108`、`evolution/self_check.py:998` — 轻量调用点（同样传了 `embedding_api_provider/key/model`，不是"只传 search_backend"）。

### 6.2 写入路径（三条）

**路径 A：`MemoryManager.add_memory`（`manager.py:2037-2164`）—— 先去重、再双写**

```python
if (self.vector_store is not None and self.vector_store.enabled and len(self._memories) > 0):
    core_content = self._strip_common_prefix(memory.content)
    similar = self.vector_store.search(core_content, limit=3)
    for mid, distance in similar:
        if distance < self.DUPLICATE_DISTANCE_THRESHOLD:      # 向量去重
            ...
            return ""                                          # 判定重复，丢弃
elif len(self._memories) > 0:
    fts_hits = self.store.search_semantic(core_content, limit=5, ...)   # FTS 去重回退
    ...

self._memories[memory.id] = memory
self._save_memories()

if self.vector_store is not None:                              # manager.py:2128-2136
    self.vector_store.add_memory(
        memory_id=memory.id, content=memory.content,
        memory_type=memory.type.value, priority=memory.priority.value,
        importance=memory.importance_score, tags=memory.tags)

_apply_retention(memory)
self.store.save_semantic(..., skip_dedup=True)                 # 再写 SQLite + FTS
```

⚠️ **顺序是先写 VectorStore，再写 SQLite**，且 VectorStore 失败只 log 不抛（用户无感知）。
这产生一个"洞"：SQLite 写成功但向量写失败 → 索引缺条目 → 靠 §6.3 的 `_sync_vector_store` 补偿。

**路径 B：`MemoryManager.record_turn`（`manager.py:1227-1291`）—— 不写向量**

```python
def record_turn(self, role, content, tool_calls=None, tool_results=None, attachments=None):
    content = coerce_text(content)
    backends = self._iter_memory_backends()     # 插件式 MemoryBackend，不是 VectorStore
    if backends:
        with contextlib.suppress(Exception):
            loop = asyncio.get_event_loop()
            for backend in backends:
                if record := getattr(backend, "record_turn", None):
                    loop.create_task(record(role, content))
    turn = ConversationTurn(role=role, content=content, ...)
    self._session_turns.append(turn)
```

⚠️ **核验修正**：`record_turn` **并非"只"攒轮次**。方法实际到 1291 行（不是 1259），除 `self._session_turns.append(turn)` 外还做了：写 SQLite（`self.store.save_turn(...)`，`manager.py:1280-1290`）、写 JSONL、记录 attachments、维护 `_recent_messages`。
**唯一正确的部分是"不写向量"** —— 它不直接调 `vector_store.add_memory`，向量写入留给后续 lifecycle 抽取时走路径 A。

**路径 C：`LifecycleManager._sync_vector_store`（`lifecycle.py:286-341`）—— 索引修复**

```python
def _sync_vector_store(self) -> None:
    """Rebuild vector store index from current SQLite data.
    双向同步：
    - 删 stale：SQLite 已不存在的 id 从向量库剔除
    - 补 missing：SQLite 有但向量库无的 id 重新嵌入（避免 Chroma 启动期
      竞态导致的"写入失败 + 后续无补全"洞口，参考 vector_store.py 300s 冷却）
    """
    try:
        if not hasattr(self.store, "search") or not self.store.search:
            return
        all_mems = self.store.load_all_memories()
        mem_ids = {m.id for m in all_mems}
        search = self.store.search

        existing_ids: set[str] | None = None
        if hasattr(search, "_collection"):
            try:
                existing_ids = set(search._collection.get()["ids"])
            except Exception:
                existing_ids = None

        if hasattr(search, "delete_not_in"):
            search.delete_not_in(mem_ids)      # ChromaDBBackend 未实现 → 走 elif
        elif existing_ids is not None:
            stale = existing_ids - mem_ids
            if stale:
                search._collection.delete(ids=list(stale))

        if existing_ids is not None and hasattr(search, "add"):
            missing = [m for m in all_mems if m.id not in existing_ids]
            for mem in missing:
                search.add(mem.id, mem.content, {
                    "type": mem.type.value, "priority": mem.priority.value,
                    "importance": mem.importance_score, "tags": mem.tags})
    except Exception as e:
        logger.debug(f"[Lifecycle] Vector store sync skipped: {e}")
```

⚠️ **`delete_not_in` 在 `ChromaDBBackend` 上并没有实现**（`search_backends.py` 全文无此方法；grep 只在 `lifecycle.py:308-309` 命中「调用点本身」两处），所以实际永远走 `_collection.get()` + 集合差集分支。这段代码**直接访问 `search._collection` 私有属性**做兜底 —— 移植时应改成 backend 上的公共方法。

调用点：`lifecycle.py:279`（每日整合末尾）、`api/routes/memory.py:997`（LLM 审查）。

### 6.3 读取路径

```
RetrievalEngine.retrieve (retrieval.py:228)
  └─ self._search_semantic(enhanced_query)                        # retrieval.py:266
       └─ for scope, scope_owner, user_id, workspace_id in self._scope_pairs:
            self.store.search_semantic_scored(query, limit=15, ...)   # retrieval.py:408
              └─ UnifiedStore.search_semantic_scored                  # unified_store.py:329
                   ├─ self.search.search(...)   ← ChromaDBBackend → VectorStore.search
                   └─ self._fts5_fallback.search(...)  ← 并集
```

```python
# retrieval.py:403-436
def _search_semantic(self, query: str, limit: int = 15) -> list[RetrievalCandidate]:
    now = datetime.now()
    candidates = []
    seen: set[str] = set()
    for scope, scope_owner, user_id, workspace_id in self._scope_pairs:
        scored_results = self.store.search_semantic_scored(
            query, limit=limit, scope=scope, scope_owner=scope_owner,
            user_id=user_id, workspace_id=workspace_id)
        for mem, raw_score in scored_results:
            if mem.id in seen:
                continue
            if mem.expires_at and mem.expires_at < now:
                continue
            seen.add(mem.id)
            relevance = max(0.0, min(1.0, raw_score))
            candidates.append(RetrievalCandidate(
                memory_id=mem.id, content=mem.to_markdown(),
                relevance=relevance,
                recency_score=self._compute_recency(mem.updated_at),
                importance_score=mem.importance_score,
                access_frequency_score=self._compute_access_score(mem.access_count),
                raw_data=mem.to_dict()))
    return candidates
```

### 6.4 ⚠️ 分数归一化的两处转换（**移植时最容易踩的坑**）

**FTS5 原始分 → [0,1]**（`search_backends.py:108-113`）：

```python
rank = abs(r.get("rank", 0))
score = 1.0 / (1.0 + rank) if rank else 1.0
```

**Chroma 距离 → 分数**（`search_backends.py:178-182`）：

```python
for memory_id, distance in results:
    score = max(0.0, 1.0 - distance)
```

即：**VectorStore 层是 distance（越小越好），SearchBackend 层统一翻成 score（越大越好）**。
上层（`search_semantic_scored`、`retrieval.py`）一律按 score 语义处理。

**这个符号翻转必须在 backend 边界完成**，绝不能让 distance 泄漏到 reranker / formatter。

---

## 七、三后端并集检索（`unified_store.py:329-397`）

```python
def search_semantic_scored(self, query, limit=10, filter_type=None, scope="user",
                           scope_owner="", user_id="default", workspace_id="default",
                           include_inactive=False) -> list[tuple[SemanticMemory, float]]:
    """Like search_semantic but also returns the raw similarity score.

    当主搜索后端（如 Chroma）启用时，**始终**额外 union 一次 FTS5 结果，
    避免向量索引尚未补全/异步未刷新时新写入的记忆被静默漏掉。按 id 去重后
    取最高分，FTS5 结果保留原始分数（FTS5 backend 输出已落在 [0,1]）。
    """
    primary = self.search.search(query, limit=limit * 3, filter_type=filter_type, ...)
    merged: dict[str, float] = {mid: float(s) for mid, s in primary}

    if self._fts5_fallback is not None:
        try:
            fts_results = self._fts5_fallback.search(query, limit=limit * 3, ...)
            for mid, s in fts_results:
                prev = merged.get(mid)
                fs = float(s)
                if prev is None or fs > prev:      # 取最高分
                    merged[mid] = fs
        except Exception as _e:
            logger.debug(f"[UnifiedStore] FTS5 union skipped (non-fatal): {_e}")

    ordered = sorted(merged.items(), key=lambda kv: kv[1], reverse=True)

    scored: list[tuple[SemanticMemory, float]] = []
    for memory_id, score in ordered:
        d = self.db.get_memory(memory_id)
        if d:
            if not include_inactive and not self._is_active_dict(d):
                continue
            # scope/owner/user/workspace 四元组校验（向量库 metadata 不参与）
            if (d.get("scope") or "global") == scope and ...:
                scored.append((SemanticMemory.from_dict(d), float(score)))
                if len(scored) >= limit:
                    break
    return scored
```

`_fts5_fallback` 的建立（`unified_store.py:65-67`）：

```python
self._fts5_fallback: FTS5Backend | None = None
if self.search.backend_type != "fts5":
    self._fts5_fallback = FTS5Backend(self.db)
```

**设计解读**：

1. **候选宽度 `limit * 3`**：两个后端都取 3 倍候选，给去重和排序留余量。
2. **去重：取最高分**（`if prev is None or fs > prev`），不是加权融合。
3. **不同量纲直接混排**：Chroma 的 `1-distance` 与 FTS5 的 `1/(1+bm25)` 被塞进同一个 dict 比大小。**这是有意的近似**，注释承认这一点，不试图校准。
4. **元数据二次过滤**：合并排序后从 SQLite `get_memory` 取权威记录，做 `_is_active_dict`（superseded_by / expires_at）+ scope 四元组校验。**向量库 metadata 不参与校验**（Chroma 的 `where` 只支持 `type`，scope 约束无法下推）。
5. **截断**：校验通过的才计数，满 `limit` 即 break。
6. **容错**：FTS5 union 整体包 try/except，失败只 debug 日志（"non-fatal"）。

---

## 八、前端

### 8.1 技术栈与位置

- 目录：`apps/setup-center/`
- 栈：React + TypeScript + Vite + Tauri + Capacitor
- 记忆界面：`apps/setup-center/src/views/MemoryView.tsx`（1158 行）
- 3D 图谱：`apps/setup-center/src/components/MemoryGraph3D.tsx`
- 降级横幅：`apps/setup-center/src/components/DegradedBanner.tsx`

### 8.2 ⚠️ 向量库状态在 UI 上完全不可见（反面教材）

grep 全前端 `embedding|向量|vector|text2vec`（.ts/.tsx/.json）只命中 3 处，**全部与向量库状态无关**：

- `App.tsx:2374` — 环境变量编辑器的 key 白名单 `"EMBEDDING_MODEL", "EMBEDDING_DEVICE", "MODEL_DOWNLOAD_SOURCE"`（通用 env 表单，不是专用向量面板）
- `MemoryGraph3D.tsx:205` — `new THREE.Vector2(...)`（Three.js 二维向量）
- `PluginManagerView.tsx:132` — 插件权限标签 `"vector.access"`

`search_backend` 在前端源码中**零命中**。后端也没有专门的向量状态路由（`health.py` grep `vector|embedding|chromadb|search_backend` 零命中）。

**后果**：`MemoryView` 的 `total` 来自 `/stats`，向量库降级时该数字不变，**用户无法从 UI 感知向量库是否生效**。

`/api/memories/stats` 返回 `{total, by_type, avg_score}`（`memory.py:727-731`），**不含 `search_backend` / `search_available` / 向量条数 / 模型名**。

（讽刺的是 `UnifiedStore.get_stats` 已经返回了 `search_backend` / `search_available`（`unified_store.py:723-728`），只是没人调用。）

### 8.3 记忆管理界面构成

`MemoryView.tsx:217-237` 的状态（⚠️ **核验修正**：以下代码块是**节选**，非逐字摘录——原文件状态声明区共 21 个 state，此处删去 9 个不相关的并重排了顺序。原样还包含 `loading` / `reviewPollRef` / `showReviewConfirm` / `confirmDialog` / `isMobile` / `claimingLegacy` / `dismissingLegacy` / `sessionLegacyDismissed` / `graphRefreshKey`）：

```typescript
const [memories, setMemories] = useState<MemoryItem[]>([]);
const [stats, setStats] = useState<Stats | null>(null);
const [searchQuery, setSearchQuery] = useState("");
const [filterType, setFilterType] = useState<string>("");
const [selected, setSelected] = useState<Set<string>>(new Set());
const [editingId, setEditingId] = useState<string | null>(null);
const [editContent, setEditContent] = useState("");
const [editScore, setEditScore] = useState(0);
const [reviewing, setReviewing] = useState(false);
const [reviewProgress, setReviewProgress] = useState<ReviewProgress>({ status: "idle" });
const [viewMode, setViewMode] = useState<"list" | "graph">("list");
const [sortBy, setSortBy] = useState("importance_score");
const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
const [page, setPage] = useState(0);
const [totalCount, setTotalCount] = useState(0);
const [migrationStatus, setMigrationStatus] = useState<MigrationStatus | null>(null);
```

功能区块：顶部统计卡片（总数 / 平均分）、legacy 恢复横幅、搜索框 + 类型下拉、排序、列表/图谱视图切换、刷新、**LLM 全量审查**（带进度轮询与取消）、多选批量删除、单条编辑（内容 + 重要度）、删除确认、分页。

**搜索是服务端搜索，且"搜索框就是语义检索入口"**（`MemoryView.tsx:259-282`）：

```typescript
const params = new URLSearchParams();
if (searchQuery) params.set("search", searchQuery);
if (filterType) params.set("type", filterType);
if (!searchQuery) {                                  // ← 只在非搜索时传分页
  params.set("sort_by", sortBy);
  params.set("sort_order", sortOrder);
  params.set("limit", String(PAGE_SIZE));
  params.set("offset", String(page * PAGE_SIZE));
}
const res = await safeFetch(`${API_BASE}/api/memories?${params}`);
```

后端分支：`?search=` 存在 → `store.search_semantic`（**语义检索**）；否则 `store.query_paged`（纯 SQLite 结构化分页）。
**所以当 `search_backend=chromadb` 时，这个搜索框已经在用向量检索，但 UI 上没有任何提示。**

### 8.4 API 调用方式

- 统一 `safeFetch` + `apiBaseUrl` prop。
- 前端路径 `/api/memories`（复数）↔ 后端 `APIRouter(prefix="/api/memories")`。
- 批量删除：`POST /api/memories/batch-delete`，body `{ids: string[]}`。
- `/memory` 斜杠命令走 `GET /api/memory/entries?limit=20` —— ⚠️ **核验修正：后端并不存在这个路由**（全仓 `.py` grep `memory/entries` 零命中；`/api/memory` 系只有 `memory_repair.py` 的 `/api/memory/repair`）。这是前端调用了一个后端未实现的端点，不能作为"两条路由体系"的证据。

---

## 九、API 路由（`api/routes/memory.py`，prefix `/api/memories`）

| 方法   | 路径                                | 行号 | 用途                                                                                                |
| ------ | ----------------------------------- | ---- | --------------------------------------------------------------------------------------------------- |
| POST   | `/api/memories`                   | 590  | 创建记忆，走`mm.save_user_memory`                                                                 |
| GET    | `/api/memories`                   | 647  | **列表/搜索**。`?search=` 存在 → `search_semantic`（语义检索入口）；否则 `query_paged` |
| GET    | `/api/memories/stats`             | 707  | `{total, by_type, avg_score}`；**不含向量信息**                                             |
| GET    | `/api/memories/migration-status`  | 734  | legacy 迁移诊断                                                                                     |
| POST   | `/api/memories/legacy/dismiss`    | 783  | 不再提醒 legacy                                                                                     |
| POST   | `/api/memories/claim-legacy`      | 799  | 认领 legacy 记忆                                                                                    |
| POST   | `/api/memories/migrate-workspace` | 847  | 工作区迁移                                                                                          |
| POST   | `/api/memories/merge-owner`       | 903  | owner 合并                                                                                          |
| POST   | `/api/memories/review`            | 953  | 启动 LLM 全量审查（997 行调`_sync_vector_store`）                                                 |
| GET    | `/api/memories/review/status`     | 1012 | 审查进度                                                                                            |
| POST   | `/api/memories/review/cancel`     | 1020 | 取消审查                                                                                            |
| POST   | `/api/memories/batch-delete`      | 1030 | 批量删除                                                                                            |
| GET    | `/api/memories/graph`             | 1050 | 3D 图谱数据                                                                                         |
| GET    | `/api/memories/{id}`              | 1179 | 单条详情                                                                                            |
| PUT    | `/api/memories/{id}`              | 1191 | 更新                                                                                                |
| DELETE | `/api/memories/{id}`              | 1218 | 删除                                                                                                |
| POST   | `/api/memories/refresh-md`        | 1238 | 刷新 MEMORY.md（见另一份文档）                                                                      |

**向量库相关路由：无。** 没有 `/vector/status`、`/reindex` 之类端点；索引重建只能靠每日整合或 LLM 审查触发，**运维上很别扭**。

---

## 十、移植到 nanobot 的注意事项

### 10.1 可以照搬的部分

1. **VectorStore 的状态机 + 后台线程模型**（`vector_store.py:124-298`）—— 纯工程问题，与业务无关。daemon 线程 + `idle/loading/ready/failed` + `_ensure_initialized` 永不阻塞 + 普通失败 300s 固定冷却 / ImportError 指数退避。nanobot 的 `MemoryDatabase` 是纯 SQLite，同步初始化向量库会直接拖慢启动，**这套机制是必须的**。
2. **`model_hub._sync_hf_hub_endpoint`**（180-205 行）—— 值得原样复制。只设 `os.environ["HF_ENDPOINT"]` 在 huggingface_hub 已导入时无效，任何做国内镜像的项目都会踩。
3. **`_sync_vector_store` 的双向修复思路**（`lifecycle.py:286-341`）—— nanobot 有 SQLite 作真相源，同样需要"删 stale + 补 missing"。**但必须改为公共方法**（别学它访问 `_collection` 私有属性）。
4. **"失败即降级为空、绝不抛异常"的纪律** —— openakita 每个向量方法都 try/except 吞掉返回 `[]`/`False`。
5. **API embedding 缓存表**（`search_backends.py:292-306`）：`sha256(f"{model}:{text}")` 做 key，`struct.pack(f"{n}f", ...)` 存 blob。nanobot 加一张 `embedding_cache` 表即可复用。

### 10.2 必须改造、不能照抄的部分

**（1）接口签名不兼容**

|             | openakita                                                               | nanobot                                                                  |
| ----------- | ----------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| search 返回 | `list[tuple[str, float]]`                                             | `list[dict]`（含 `memory_id`/`content`/`score`/`channel`/...） |
| 参数        | 位置 + 关键字混合，含`scope`/`user_id`/`workspace_id`             | 仅`query, *, limit`                                                    |
| 其他方法    | `add` / `delete` / `batch_add` / `available` / `backend_type` | **无**                                                             |

两条路：

- **A（推荐，改动小）**：保持 nanobot 的 `list[dict]` 契约，ChromaBackend 自己组装 dict。注意 `content` 字段需要 `db.get_memory(id)` 回查 —— 这恰好和 openakita `search_semantic_scored` 的做法一致。
- **B**：把全套协议搬过来并改所有调用点。工作量大。

**（2）分数语义必须统一，符号翻转最容易错**

nanobot 现在 `bm25_rank_to_score` / `_fallback_like` 硬编码 `0.3`，语义是**越高越相关**。
移植时务必在 backend 边界做 `score = max(0.0, 1.0 - distance)`，不要让 distance 泄漏到 `reranker.py` / `formatter.py`。

另外注意：nanobot 有 `reranker.py`（`0.4×rel + 0.2×rec + 0.2×imp + 0.2×access`）。Chroma 的 `1-distance` 与 FTS5 的 `1/(1+bm25)` 混排会影响排序质量 —— openakita 是硬混不校准，nanobot 如果在意质量，需要额外考虑归一化。

**（3）并集逻辑没有直接对应物**

openakita 的并集在 `UnifiedStore.search_semantic_scored`；nanobot 的对应位置是 `nanobot/memory/retrieval/engine.py` + `channels/`。
需要把"`limit*3` 取候选 → dict 去重取最高分 → SQLite 回查校验 active/scope → 截断"这套逻辑放进 nanobot 的 engine。
openakita 的注释明确解释了动机（向量索引异步滞后会静默漏新记忆），nanobot 有 idle 抽取 / session_end 抽取等异步写入路径，会遇到同样问题。

**（4）nanobot 的写入路径是三对三映射**

| nanobot 写入路径 | openakita 对应                                           | 说明                                           |
| ---------------- | -------------------------------------------------------- | ---------------------------------------------- |
| idle 抽取        | lifecycle`process_unextracted_turns` → `add_memory` | 抽取后需**同时**写 SQLite + 向量         |
| session_end 抽取 | `record_turn` 攒 `_session_turns` → lifecycle 抽取  | openakita 的`record_turn` **不写**向量 |
| ProfileExtractor | `agents/factory.py:519-530` 独立 MemoryManager         | 隔离 sub-agent 有独立 vector_store             |

**关键**：nanobot 的 `MemoryDatabase` 是纯 SQLite，**没有 UnifiedStore 这层抽象**。
两条路：

- **引入 UnifiedStore 薄层（推荐）** —— 让三条写入路径统一收口到 `save_semantic`，顺便解决去重和索引修复。
- 在三条路径各自手动加"写 SQLite 后再写向量" —— 会重演 openakita v4 之前"ad-hoc cache 更新散布各处"的痛点（`manager.py:285-291` 的注释明确记录了这次重构）。

**（5）配置项要补齐，特别是维度**

- 补 `search_backend` / `embedding_model` / `embedding_device` / `model_download_source` / `embedding_api_provider` / `embedding_api_key` / `embedding_api_model` / **`embedding_dimensions`**（openakita 漏了这个）。
- **维度必须显式配置**：`text-embedding-3-small` 默认 1536，DashScope v3 默认 1024，混用直接报错。

**（6）⚠️ `APIEmbeddingBackend` 是全表暴力，只适合小库**

`search_backends.py:258-278`：`storage.query(limit=200)` 拉 200 条 → 逐条 `_get_embedding` → 余弦相似度 → 排序。
nanobot 如果记忆量超过几百条，这个后端会非常慢。**只适合小库或作为过渡**；主力应该是本地 ChromaDB。

**（7）UI 层的空白是反面教材（nanobot 应补上）**

建议：

- **`/api/settings/memory/stats` 端点已存在**（`settings_routes.py:160`，返回 `{total, by_type}`）—— 只需**扩字段**：加 `search_backend` / `search_available` / `vector_count` / `vector_model`
- 前端在记忆视图加一个**后端状态徽章 + 索引条数**
- 提供 `POST /api/settings/memory/reindex` 手动重建端点（openakita 没有，只能等每日整合，运维很别扭）

**（8）打包**

openakita 把 sentence-transformers + chromadb 声明为 **2500MB 的可选模块**。
nanobot 若是 pip 分发，设为 extras（`pip install nanobot[vector]`）比硬依赖友好得多。

---

## 十一、附：与另一份文档的接口

本文档（向量化）与《nanobot MEMORY.md 改造调研》在数据流上**是同一条链的下游**：

```
SQLite memories 表  ←── 唯一真相源
   ├─→ 向量索引（本文档）      ：镜像 memories，供语义召回
   └─→ MEMORY.md（另一文档）   ：从 memories 程序化生成，供人类审阅 + system prompt 注入
```

**两条下游的共同约束**：

1. 向量索引的写入必须**挂在 SQLite 写入路径上**（openakita 的 `save_semantic` 双写），否则会漂移。
2. MEMORY.md 的生成必须**读同一张表**（openakita 的 `refresh_memory_md` 查 `query_semantic`）。

一旦 MEMORY.md 改为程序化生成，两条下游就共享同一个数据源与同一套 `type` / `importance_score` 语义 —— 这是两个改动应当**同批设计**的原因。
