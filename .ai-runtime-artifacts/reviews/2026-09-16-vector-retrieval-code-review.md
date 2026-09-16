---
artifact: review
route: orchestration:dispatcher-workflow
skills:
  - requesting-code-review
source:
  - .ai-runtime-artifacts/plans/2026-09-16-vector-retrieval-plan.md
  - .ai-runtime-artifacts/specs/2026-09-16-vector-retrieval-spec.md
created_at: 2026-09-16
scope: 3b61993..9d30d00（13+ 提交 / 向量检索接入全量改动）
reviewer: reviewer subagent（独立实例）
conclusion: 初审 BLOCK → 修复后通过
---

# 向量检索接入 —— 集体审查

> 逐文件对照源码的人工审查表见同目录
> [`2026-09-16-vector-retrieval-code-review-table.md`](2026-09-16-vector-retrieval-code-review-table.md)。

## 结论

独立 reviewer 初审给出 **BLOCK**（1×🔴 + 5×🟡）。Leader 逐条复核，**修复了 🔴 与三项 🟡**，
其余降级为已记录的已知限制。**修复后判定：通过**（修复提交见下表）。

## 🔴 必须修复（已修）

| # | 位置 | 缺陷 | 复核 | 修复 |
| --- | --- | --- | --- | --- |
| 1 | `nanobot/memory/vector/store.py:178` | `_ready()` 读 `self._settings.enabled` 而非 `self.enabled`，导致 `enabled` 属性（spec §4.3 设计的**唯一重试触发点**）全仓无调用方 → 冷却到期重试成死代码，向量层失败后永久停在 `failed` 直到重启。**D1/D2 实际未实现**。 | 已复核属实：`grep '\.enabled' nanobot/` 无一处命中 vector store；`_ready()` 是全部公开方法（upsert/remove/search/count/list_ids/delete_ids）的唯一闸门。 | `_ready()` 改读 `self.enabled`；补 `test_cooldown_expiry_triggers_reload` 锁行为 |

## 🟡 建议修复（已修 3 项）

| # | 位置 | 缺陷 | 处置 |
| --- | --- | --- | --- |
| 2 | `model_hub.py` `_download` | `HF_HUB_DOWNLOAD_TIMEOUT` 经 `setdefault` 设置无效（`huggingface_hub.constants` 在**导入时**读取，之后改 env 无用，恒为默认 10s）——与 R-2 的 `HF_ENDPOINT` 同陷阱 | 已修：新增 `_sync_hf_hub_download_timeout()` 双写 `hf_constants.HF_HUB_DOWNLOAD_TIMEOUT`（照 `_sync_hf_hub_endpoint` 模式） |
| 3 | `memory_api.py` `reindex_vector`/`sync_vector` | store 未 ready 时 `list_ids()` 返 `[]` → 判全量为 missing → `upsert` 静默返 False → 端点报 `{'available': True, 'indexed': 0}` **假成功**，用户无法区分「已一致」与「向量挂了」 | 已修：新增 `_vector_not_ready_reason()`，非 ready 时返回 `available: False` + 原因（兼容无 `state` 的测试替身） |
| 6 | `database.py:250` `replay_fallback` | `index_memory_best_effort` 位于持**非可重入**锁的 `with connect()` **内部**——当前安全（钩子不触 DB），但将来若触达 DB 即自死锁（WU-07 已踩过） | 已修：钩子移到 `with` 块**之外**（`indexed_memory` 暂存后调用） |

## 🟡 建议修复（未修，转已知限制）

| # | 位置 | 缺陷 | 处置理由 |
| --- | --- | --- | --- |
| 4 | `store_adapter.py:_is_live` | 未做 spec §4.6 的 **scope 四元组**校验，向量独有 id 理论上可跨 workspace 泄漏 | 单 workspace 部署下无实际暴露；`MemoryStoreAdapter` 当前不持有 workspace_id，需引入作用域参数（接口变更），**留待 v2** |
| 5 | `config/schema.py:157` / `settings.py:27` | `max_candidates`（默认 45）**全仓无读取方**——Adapter 直接用 `limit*3`（=90），设定后零效果 | spec 对该字段语义未定（45 vs limit*3 冲突），贸然接线会改变候选宽度；**留待 v2 明确语义** |

## 🟢 已记录 Nit（不阻塞）

- `indexer.get_active_store()` 访问私有 `_store`；进程级单例跨 workspace 共享。
- `model_hub.is_cached()` 无调用点（死代码）。
- `stats_payload` 在 `api_embedding` 后端下仍报 `"fts5"`；`reindex` 返 `vector_count` 而 `sync` 不返（端点字段集不一致）。
- `test_store_state_machine.py` 多处用真实模型名构造 store（依赖本机 HF 缓存）——建议 monkeypatch `_do_load` 保 hermetic。

## 已核对、未发现问题

- **并集逻辑**：按 id `max` + 两路各取 `limit*3` + 向量独有 id 回查 SQLite 活性过滤 —— 符合 spec §4.6。
- **分数符号**：`_distance_to_score`（store.py:256）是唯一翻转点，Adapter 未二次翻转；FTS5 页内 min-max `(hi-rank)/span` 方向正确且对符号约定免疫。
- **降级纪律**：向量层公开方法全部吞异常返 `[]`/`False`/`0`；`vector_store=None` 时行为与改造前等价（测试锁定）。
- **向后兼容**：核心路径无重依赖（`database.py` 局部 import 理由成立、`vector/__init__.py` 无 re-export）；默认 `fts5` 不建目录/不起线程/不联网。
- **死锁**：无嵌套 `connect()`（`_record_state` 的传 `conn` 修复正确）。
- **接口一致性**：`stats_payload` 字段集恒定；未注册 indexer 时 `reindex/sync` 返 `available: False` 而非 500。
- **测试质量**：未发现恒真断言；修正了 plan 两处错误断言（`0.99`→`1.0`、FK 插入顺序）。

## 修复提交

`9d30d00` — fix(memory): 修复集体审查发现的向量层三项缺陷
