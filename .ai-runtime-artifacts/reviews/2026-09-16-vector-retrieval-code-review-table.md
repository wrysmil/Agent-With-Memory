# 向量检索接入 — 人工代码审查表

**分支**：`feature/memory-system`
**审查范围**：`3b61993..9d30d00`（31 files, +2118 -19；13 提交）
**计划**：[2026-09-16-vector-retrieval-plan.md](.ai-runtime-artifacts/plans/2026-09-16-vector-retrieval-plan.md)
**dispatch**：[2026-09-16-vector-retrieval-dispatch.md](.ai-runtime-artifacts/plans/2026-09-16-vector-retrieval-dispatch.md)
**spec**：[2026-09-16-vector-retrieval-spec.md](.ai-runtime-artifacts/specs/2026-09-16-vector-retrieval-spec.md)
**审查方式**：Leader 逐文件对照源码（行号均为 `9d30d00` 处实际位置）

---

## 审查维度

| # | 维度 | 说明 | 关注文件 |
|---|---|---|---|
| 1 | **正确性（并集/符号/活性）** | 向量 ∪ FTS5 按 id 取 max；两侧分数均「越大越相关」；活性过滤 | store_adapter.py, store.py, repository.py |
| 2 | **并发与死锁** | `connect()` 持**非可重入**锁；向量后台加载线程 | database.py, indexer.py, store.py |
| 3 | **降级纪律** | 向量任何失败**绝不上抛**（返 `[]`/`False`/`0`） | store.py, indexer.py, memory_api.py |
| 4 | **向后兼容 / 默认关闭** | 默认 `fts5` 不建目录/不起线程/不联网；核心无重依赖 | schema.py, store.py, database.py, pyproject.toml |
| 5 | **接口一致性** | `stats_payload` 字段集恒定；端点信号明确 | memory_api.py, memory_routes.py, settings_routes.py |
| 6 | **可观测性** | stats 暴露向量状态；reindex/sync 运维入口 | memory_api.py, gateway_runtime.py |
| 7 | **测试有效性** | 是否锁住真实行为（非假绿） | tests/memory/vector/, tests/memory/retrieval/ |
| 8 | **依赖与打包** | vector extra、CPU-only torch index | pyproject.toml, model_hub.py |

---

## 文件清单与审查要点

> 字段：`现` = 当前文件总行数；`+X` = 本次范围新增行数。

### A. 配置与依赖

#### `pyproject.toml` — +15
- **关键定义**：`vector` extra（`torch`/`chromadb`/`sentence-transformers`）；`[tool.uv.sources] torch = {index="pytorch-cpu"}`；`[[tool.uv.index]] name="pytorch-cpu" explicit=true`
- **审查要点**：CPU-only torch 是否真的挡掉 CUDA wheel（R-1）；`vector` 是否只在 `optional-dependencies` 而非核心 `dependencies`（R6）
- **判定**：✅ 核心 dependencies 无三者；`explicit=true` 存在（`test_vector_config_default.py` 锁定）

#### `nanobot/config/schema.py` — 74 行新增（文件 245+）
- **关键定义**：
  - `class MemoryVectorConfig(Base)` — 行 117
  - `to_vector_settings(*, enabled: bool) -> VectorSettings` — 行 164
  - `AgentDefaults.memory_search_backend: Literal["fts5","chromadb","api_embedding"]` — 行 233
  - `AgentDefaults.memory_vector: MemoryVectorConfig` — 行 239
- **审查要点**：字段默认值（`fts5`）保证升级后行为不变；camelCase alias 双向；`dimensions` 显式必填语义（R-3）
- **判定**：✅ 默认 `fts5`；`dimensions`/`model` 均有默认

### B. 向量核心（新增包 `nanobot/memory/vector/`）

#### `nanobot/memory/vector/__init__.py` — 16 行
- **作用**：包 docstring，**刻意不做 re-export**
- **审查要点**：是否会把 chromadb 等重依赖经包导入拉进核心安装
- **判定**：✅ 无 re-export（`database.py` 可安全局部 import）

#### `nanobot/memory/vector/settings.py` — 27 行
- **关键定义**：`class VectorSettings`（frozen dataclass）— 行 16；字段 `enabled/model/dimensions/device/download_source/local_model_dir/index_path/sync_on_startup/max_candidates`
- **审查要点**：为何用 dataclass 而非 pydantic（解耦配置体系）；`VectorSettings(enabled=False)` 是否可构造（全字段有默认）
- **判定**：✅；⚠️ `max_candidates`（行 27）**全仓无读取方** → 问题 #6

#### `nanobot/memory/vector/model_hub.py` — 136 行
- **关键定义**：
  - `class ModelUnavailableError(RuntimeError)` — 行 14
  - `_sync_hf_hub_endpoint(endpoint)` — 行 31（R-2 双写）
  - `_sync_hf_hub_download_timeout()` — 行 49（超时双写，`9d30d00` 新增）
  - `apply_source_env(source)` — 行 68
  - `ensure_model(model_name, *, source, local_dir)` — 行 80（`local_dir` 优先 → `auto` 三源探测）
  - `_download(model_name, source)` — 行 108
  - `is_cached(model_name)` — 行 124
- **审查要点**：`local_dir` 命中即返回（不联网）；三源失败聚合为 `ModelUnavailableError`；延迟 import 不污染核心
- **判定**：🔴→✅ 原 `snapshot_download(timeout=)` 非法已修（`79b7fd4`）；超时 setdefault 无效已修（`9d30d00` 双写 constants）—— 问题 #3；ℹ️ `is_cached` 无调用点 —— 问题 #8

#### `nanobot/memory/vector/store.py` — 271 行
- **关键定义**：
  - `_COLLECTION_NAME` — 行 20；`_COOLDOWN_SECONDS=300.0` — 行 22；`_COOLDOWN_MAX_SECONDS=3600.0` — 行 23
  - `class VectorStore` — 行 26；`__init__(settings, *, workspace)` — 行 29（非阻塞，仅 `enabled` 时起后台线程）
  - `enabled` property — 行 49（**唯一重试触发点**）；`state/error/model_name/dimensions` — 行 57/61/65/69
  - `_start_background_load` — 行 75；`_load_worker` — 行 85；`_do_load` — 行 99
  - `_activate` — 行 136；`_mark_failed` — 行 152；`_initialize_now` — 行 167；`_ready` — 行 178
  - `upsert/remove/search/count/list_ids/delete_ids` — 行 185/201/211/228/237/246
  - `_distance_to_score(d)` — 行 260（**唯一符号翻转点**）；`_clean_metadata` — 行 268
- **审查要点**：所有公开方法以 `_ready()` 为闸门且吞异常；`_do_load` 的 dim 校验（R-3）；`_clean_metadata` 丢弃非标量；后台线程与 `_initialize_now` 的并发
- **判定**：🔴→✅ `_ready` 曾读 `_settings.enabled` 使重试成死代码，已修 —— 问题 #1；⚠️ `_initialize_now` 静默吞异常（诊断困难）—— 问题 #9

#### `nanobot/memory/vector/indexer.py` — 171 行
- **关键定义**：
  - 进程级钩子 `set_active_indexer` — 行 28；`get_active_indexer` — 行 34；`get_active_store` — 行 38；`index_memory_best_effort` — 行 44；`remove_memory_best_effort` — 行 59
  - `_metadata_of` — 行 70；`_latest_updated_at(conn)` — 行 85（**须复用已持有 conn**）
  - `class MemoryIndexer` — 行 99；`index` — 行 106；`remove` — 行 110；`sync_from_sqlite` — 行 113；`_record_state` — 行 160
- **审查要点**：钩子绝不抛出；`sync_from_sqlite` 双向对账（删 stale + 补 missing）不中断；**不得嵌套 `connect()`**
- **判定**：✅ 已修自死锁（`_latest_updated_at` 收 `conn`）；ℹ️ `get_active_store` 访问私有 `_store` —— 问题 #7

### C. 检索集成

#### `nanobot/memory/retrieval/store_adapter.py` — 77 行新增（文件 176）
- **关键定义**：
  - `class MemoryStoreAdapter` — 行 30；`__init__(database, *, vector_store=None)` — 行 49
  - `search_semantic_scored(query, *, limit=30)` — 行 56（FTS5 `limit*3` + 向量 `limit*3` → max 合并 → 回查 SQLite 活性过滤）
  - `_is_live(memory)` — 行 159（superseded/expired 过滤）
- **审查要点**：两处 `connect()` 是否嵌套（否）；`vector_store=None` 行为是否与改造前等价；向量路 try/except 降级
- **判定**：✅ 并集/符号正确；⚠️ `_is_live` 缺 spec §4.6 的 **scope 四元组**校验 —— 问题 #5

#### `nanobot/memory/repository.py` — 109 行新增
- **关键定义**：
  - `search_semantic_scored(conn, query, *, limit)` — 行 693（真 `bm25()` + 页内 min-max `(hi-rank)/span`）
  - `class VectorSyncState` — 行 883；`get_vector_sync_state` — 行 893；`upsert_vector_sync_state` — 行 910
- **审查要点**：min-max 对 bm25 符号约定是否免疫（旧实现 `1/(1+|rank|)` 被阶梯分压制）；首名=1.0/末名=0.0
- **判定**：✅ 方向正确（更负=更相关 → 首名 1.0）

#### `nanobot/memory/database.py` — 27 行变更
- **关键定义**：`_SCHEMA_VERSION = "2"` — 行 20；`vector_sync_state` 建表 — 行 155；`replay_fallback` 钩子 — 行 277（`index_memory_best_effort`，**已移出持锁 `with connect()`**）
- **审查要点**：schema v2 是否幂等补建；钩子调用点是否在锁外
- **判定**：🔴→✅ 钩子原在持锁 `connect()` 内（死锁地雷），已移出 —— 问题 #4

#### `nanobot/memory/extractor.py` — +3
- **关键定义**：import — 行 72；`index_memory_best_effort(memory)` — 行 1516（在 `if not ok: continue` 守卫**之后**）
- **审查要点**：是否 SQLite 落库成功后才索引；是否影响 `saved_memory_ids`
- **判定**：✅ 语义等价 `if ok:`

### D. WebUI 与启动接线

#### `nanobot/webui/memory_api.py` — 155 行变更
- **关键定义**：
  - `stats_payload(services, *, vector_runtime=None)` — 行 238（未传参 fallback `get_active_store()`；字段集恒定）
  - `_vector_not_ready_reason` — 行 295；`reindex_vector` — 行 305；`sync_vector` — 行 343
  - `create_memory` — 行 375；`fetch_and_index` — 行 423；`update_memory` — 行 437；`delete_memory` — 行 484
- **审查要点**：三处写路径钩子是否在落库成功后；`fetch_and_index` 与原 `fetch_memory_payload` 等价；端点未就绪信号
- **判定**：🔴→✅ reindex/sync 未就绪时曾报「0 改动」假成功，已修 —— 问题 #2；ℹ️ `api_embedding` 后端仍报 `fts5`、reindex 多返 `vector_count` —— 问题 #10

#### `nanobot/webui/memory_routes.py` — +9
- **关键定义**：dataclass 字段 `reindex_vector`/`sync_vector` — 行 53/54；`MEMORY_ACTION_NAMES` 加两项 — 行 64；dispatch 分支 — 行 122/125
- **审查要点**：action 名与路由映射是否一致；dispatch 顺序
- **判定**：✅

#### `nanobot/webui/settings_routes.py` — +6
- **关键定义**：路由 dict 两条 — 行 161/162；`_MEMORY_MUTATION_PATHS` 两条 — 行 176/177；`_null_memory_operations` 补两字段
- **审查要点**：reindex/sync 是否列为写操作（mutation paths）
- **判定**：✅ 两者均已列入

#### `nanobot/webui/gateway_services.py` — +5
- **关键定义**：`reindex_vector`/`sync_vector` 的 partial — 行 81/82（**不注入** indexer，走进程级 fallback）
- **审查要点**：单实例纪律（避免双 `VectorStore` 撞 chromadb sqlite 锁）
- **判定**：✅

#### `nanobot/cli/gateway_runtime.py` — 39 行变更
- **关键定义**：import — 行 40；向量构造块 — 行 493-504（`_vector_settings`/`_vector_store`/`_vector_indexer`/`set_active_indexer`）；`MemoryStoreAdapter(..., vector_store=...)` — 行 509；启动对账 — 行 546-561
- **审查要点**：默认 `fts5` 时是否不建目录不起线程（D5）；`set_active_indexer` 时序（须在 WebUI 构造前）；对账是否非阻塞（`asyncio.to_thread`）；indexer 绑定局部变量规避 basedpyright 收窄
- **判定**：✅

### E. 测试

| 文件 | 新增 | 审查要点 |
|---|---|---|
| `tests/memory/vector/test_settings.py` | 28 | VectorSettings 默认/构造 |
| `tests/memory/vector/test_model_hub.py` | 63 | 三源探测、local_dir 优先、endpoint 双写 |
| `tests/memory/vector/test_store_state_machine.py` | 116 | 状态机/冷却/**冷却到期触发重载**（本轮新增） |
| `tests/memory/vector/test_store_degradation.py` | 40 | D1 缺依赖 / D4 encode 异常 |
| `tests/memory/vector/test_indexer.py` | 118 | 双向对账、幂等、错误记录 |
| `tests/memory/vector/test_write_hooks.py` | 85 | 钩子 no-op / records / swallows |
| `tests/memory/retrieval/test_store_adapter_union.py` | 151 | 并集契约 10 例 |
| `tests/memory/retrieval/test_semantic_vector_recall.py` | 85 | 端到端同义召回 V3/V5/V6 |
| `tests/memory/test_vector_stats_api.py` | 135 | stats 扩字段 + reindex/sync |
| `tests/memory/test_vector_config_default.py` | 48 | D5/R6/R-1 默认配置回归 |
| `tests/memory/test_repository_bm25.py` | 79 | bm25 页内归一化 |
| `tests/memory/test_vector_sync_state.py` | 41 | 游标表读写 |

- **审查要点**：是否锁住真实行为（非恒真断言）；是否只测替身不测契约
- **判定**：✅ 未发现恒真断言；修正了 plan 两处错误断言（`0.99`→`1.0`、FK 插入顺序）；ℹ️ `test_store_state_machine` 用真实模型名构造（非 hermetic）—— 问题 #10

---

### 汇总

| 类别 | 文件数 | 新增行数 |
|---|---|---|
| 配置与依赖 | 2 | 89 |
| 向量核心（`vector/`） | 5 | 621 |
| 检索集成 | 4 | 216 |
| WebUI 与启动接线 | 5 | 214 |
| 测试 | 14 | 1232 |
| **合计** | **30** | **~2372** |

---

## 审查问题登记

> Severity: CRITICAL / MAJOR / MINOR / INFO。状态：`已修` / `转已知限制`

| # | Severity | 文件:行 | 问题描述 | 处置 |
|---|---|---|---|---|
| 1 | **CRITICAL** | `store.py:178` | `_ready()` 读 `self._settings.enabled` 而非 `self.enabled` → `enabled` 属性（spec §4.3 设计的唯一重试触发点）全仓无调用方，冷却到期重试成**死代码**，向量层失败后永久 `failed` 直到重启（D1/D2 实际未实现） | ✅ 已修 `9d30d00` + 补 `test_cooldown_expiry_triggers_reload` |
| 2 | MAJOR | `memory_api.py:305,343` | store 未 ready 时 `list_ids()` 返 `[]`→判全量 missing→`upsert` 静默 False→端点报 `available:True, indexed:0` **假成功** | ✅ 已修 `9d30d00`（`_vector_not_ready_reason`） |
| 3 | MAJOR | `model_hub.py:108` | `snapshot_download(timeout=...)` 非法形参 → hf-mirror/huggingface 两源恒 `TypeError`；且超时经 `setdefault` 设置对 `constants` 无效 | ✅ 已修 `79b7fd4` + `9d30d00`（双写 constants） |
| 4 | MAJOR | `database.py:277` | `index_memory_best_effort` 原位于持**非可重入**锁的 `with connect()` **内**（将来触达 DB 即自死锁） | ✅ 已修 `9d30d00`（移出 `with`） |
| 5 | MINOR | `store_adapter.py:159` | `_is_live` 未做 spec §4.6 要求的 **scope 四元组**校验，向量独有 id 理论上可跨 workspace 泄漏 | ⏳ 转已知限制（单 workspace 无暴露；需引入作用域参数，接口变更）→ v2 |
| 6 | MINOR | `settings.py:27` | `max_candidates`（默认 45）**全仓无读取方**，Adapter 实际用 `limit*3`（90），设定后零效果 | ⏳ 转已知限制（spec 语义未定）→ v2 |
| 7 | INFO | `indexer.py:38` | `get_active_store()` 访问私有 `_store`；进程级单例跨 workspace 共享 | 记录（当前单 workspace 成立） |
| 8 | INFO | `model_hub.py:124` | `is_cached()` 无调用点（死代码） | 记录（预留 API） |
| 9 | INFO | `store.py:167` | `_initialize_now()` 静默吞异常（返回 `False` 但 state/error 均不设），诊断困难 | 记录（仅探针用；WU-13 实测踩到） |
| 10 | INFO | `memory_api.py:238` / `test_store_state_machine.py` | `api_embedding` 后端仍报 `"fts5"`；`reindex` 返 `vector_count` 而 `sync` 不返；状态机测试用真实模型名（非 hermetic） | 记录 |

---

## 审查结论

| 维度 | 结论 | 说明 |
|---|---|---|
| 1 正确性（并集/符号/活性） | ✅ | 并集按 id max、两侧符号统一、活性过滤方向正确；scope 维度缺失计为 MINOR（#5） |
| 2 并发与死锁 | ✅ | 无嵌套 `connect()`；#1（重试死代码）、#4（钩子持锁）已修 |
| 3 降级纪律 | ✅ | 公开方法全部吞异常返 `[]`/`False`/`0`；`vector_store=None` 与改造前等价 |
| 4 向后兼容 / 默认关闭 | ✅ | 默认 `fts5` 不建目录/不起线程/不联网；核心无重依赖 |
| 5 接口一致性 | ✅ | `stats_payload` 字段集恒定；未就绪返 `available:False` 而非 500/假成功 |
| 6 可观测性 | ✅ | stats 7 字段可见；reindex/sync 运维入口已注册为写操作 |
| 7 测试有效性 | ✅ | 未发现假绿；补了锁住重试行为的测试 |
| 8 依赖与打包 | ✅ | CPU-only torch index 生效（R-1 双重验证） |

**总体结论**：**APPROVE**（初审判 BLOCK，🔴 + 3×MAJOR 已修复于 `9d30d00`；余 2×MINOR 转已知限制并留 v2）

---

## 审查命令

```bash
# 审查范围
git log --oneline 3b61993..9d30d00
git diff --stat 3b61993..9d30d00

# 逐文件审查（向量核心）
git diff 3b61993..9d30d00 -- nanobot/memory/vector/store.py
git diff 3b61993..9d30d00 -- nanobot/memory/vector/indexer.py
git diff 3b61993..9d30d00 -- nanobot/memory/vector/model_hub.py
git diff 3b61993..9d30d00 -- nanobot/memory/retrieval/store_adapter.py

# 逐文件审查（集成）
git diff 3b61993..9d30d00 -- nanobot/memory/database.py nanobot/memory/extractor.py
git diff 3b61993..9d30d00 -- nanobot/webui/memory_api.py nanobot/cli/gateway_runtime.py

# Lint
uv run ruff check nanobot/

# 测试（验证）
uv run pytest tests/memory/ -q
uv run pytest tests/memory/ tests/webui/ -q

# 真实链路（V4 / R-5）
uv run python d:/tmp/_probe_v4.py
```
