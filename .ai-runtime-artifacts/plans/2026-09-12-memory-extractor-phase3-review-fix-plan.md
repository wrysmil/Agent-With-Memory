# nanobot 记忆抽取 follow-up：Reviewer MAJOR 修复（review-fix）

> 上游：集体审查 `.ai-runtime-artifacts/reviews/2026-09-12-memory-extractor-phase3-review.md`
> 上游：集体测试 `.ai-runtime-artifacts/verifications/2026-09-12-memory-extractor-phase3-collective-test.md`
> 上游：方案 `.ai-runtime-artifacts/plans/2026-09-12-memory-extractor-phase3-plan.md`
> 状态：**待用户确认**

## 0. 背景

reviewer 给出 verdict **PASS-WITH-FOLLOWUPS**，3 项 MAJOR 不阻塞合并但应尽快修复。用户已确认范围：**仅修 3 项 MAJOR**（事务回滚 / fallback 内容 redact / Database 独立 replay 入口）。MINOR / NIT 不动。

## 1. 目标

1. `_safe_write_with_fallback` 失败后调 `conn.rollback()` 主动恢复事务，避免后续写入受 `InFailedSqlTransaction` / `InterfaceError` 污染。
2. fallback JSON 落盘前对 `item["content"]` 调用 `_redact`，避免 LLM 注入的凭据 / PII 通过 fallback 路径持久化（OWASP LLM05）。
3. 在 `MemoryDatabase` 上提供独立的 replay 入口，使非 extractor 调用路径（CLI / 迁移脚本）也能 drain fallback 队列。

## 2. 非目标

- 不动 MINOR（`from_row` 命名歧义、API 表面、pre-existing 全量加载等）。
- 不动 NIT（死代码清理、`_FakeProvider` conftest、`fallback_dir=None` 单测缺失）。
- 不动 5 个 WU 的核心语义、source 枚举、persistence 行为。

## 3. 方案（3 个改动点）

### 改动 1：`_safe_write_with_fallback` 失败后 rollback【MAJOR #1】

**位置**：`nanobot/memory/extractor.py:969-1033`

**改动**：

```python
def _safe_write_with_fallback(
    self,
    conn: Any,
    *,
    kind: str,
    item: dict[str, Any],
    writer: Any,
) -> bool:
    try:
        writer(conn)
        return True
    except Exception as exc:  # noqa: BLE001 - 失败隔离
        # 主动回滚当前事务,避免 4a 失败导致 4c 同事务内 InFailedSqlTransaction
        try:
            conn.rollback()
        except Exception:
            logger.debug("rollback after failed write also failed (ignored)")
        fallback_dir = self.fallback_dir
        # ... 原 fallback 落盘逻辑保持不变 ...
```

**关键不变量**：
- `rollback()` 对未失败事务是 no-op，对失败事务恢复手段，幂等。
- 把 `rollback` 也包在 `try/except` 里：rollback 本身失败不能阻断 fallback 落盘（fallback 才是兜底）。
- `_persist` 同事务内的循环（4a 多条 memory、4c episode、4d backfill）现在各自 `_safe_write_with_fallback` 失败时都会先 rollback 再写 fallback，后续写入仍能继续尝试 DB。

**测试**：
- `tests/memory/test_fallback.py` 新增 `test_safe_write_with_fallback_calls_rollback_on_failure`：
  - 用 fake conn，writer 抛 `OperationalError("database is locked")`，
  - 验证 conn.rollback() 被调用一次。
- 新增 `test_persist_continues_after_mid_loop_failure`：
  - 构造 `_persist` 输入：3 条 memory。构造 BrokenWriter：第 1 条成功，第 2 条抛 `OperationalError`，第 3 条又成功。
  - 验证：
    - 第 1 条 id 进 `saved_memory_ids`。
    - 第 2 条落 fallback 文件 + 不进 `saved_memory_ids`。
    - 第 3 条仍然成功，id 进 `saved_memory_ids`（关键：rollback 让后续写入能继续）。
    - 验证 fallback 文件存在 1 个。

### 改动 2：fallback 内容 redact【MAJOR #2 / LLM05】

**位置**：`nanobot/memory/extractor.py:1005-1020`（fallback 落盘段）

**改动**：

在 `payload = {...}` 构造前，对 `item` 中的可读文本字段做 `_redact`：

```python
# LLM05: 对 LLM 抽取的 content 做 redact,避免凭据/PII 落入 fallback JSON
# fallback 文件可能比 DB 持久化周期更长(启动重放前残留数日)
payload_item = dict(item)
if isinstance(payload_item.get("content"), str):
    payload_item["content"] = _redact(payload_item["content"])
if isinstance(payload_item.get("subject"), str):
    payload_item["subject"] = _redact(payload_item["subject"])
if isinstance(payload_item.get("predicate"), str):
    payload_item["predicate"] = _redact(payload_item["predicate"])
payload = {
    "kind": safe_kind,
    "item": payload_item,
    "attempt": ts,
    "error": str(exc),
}
```

**关键不变量**：
- 只 redact 文本字段（`content` / `subject` / `predicate`），不碰 tags（数组，redact 模式不适用；如有需要后续再加）。
- `_redact` 已存在（extractor.py:308-312），是模块级函数，无循环引用风险。
- 不影响内存中的 `Memory` 对象本身，只影响落盘内容。

**测试**：
- `tests/memory/test_fallback.py` 新增 `test_safe_write_redacts_fallback_content`：
  - 构造 memory content 含 `"api_key=secret123"` / `Bearer abcdefghij` 等典型凭据模式。
  - 让 writer 抛异常触发 fallback。
  - 读 fallback 文件 JSON，断言 `content` 已含 `<redacted>` 占位符。
- 新增 `test_safe_write_redacts_subject_and_predicate`：同上，但字段是 `subject` / `predicate`。
- 注意现有 `test_writes_file_on_db_error` 不要被新逻辑破坏（其 fixture 内容已是合法文本，应仍原样落盘）。

### 改动 3：`MemoryDatabase` 独立 replay 入口【MAJOR #3】

**位置**：`nanobot/memory/database.py:142-245`

**设计决策**：在 `MemoryDatabase.__init__` 末尾调用 `self.replay_fallback()`，与 `MemoryExtractor.__init__` 的双调用是幂等的（replay_fallback 完成后 `fallback_dir` 为空），保留 extractor 端调用作为「extractor 创建即清理」的入口语义。同时把 `replay_fallback` 改成 **幂等**：执行前先扫描文件数 + 全局锁防止重复并行（连接锁 `_lock` 已覆盖）。

**改动**：

```python
class MemoryDatabase:
    def __init__(self, workspace: Path, *, db_path: Path | None = None) -> None:
        self.workspace = Path(workspace)
        self.db_path = Path(db_path) if db_path is not None else (self.workspace / "memory" / "state.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.fallback_dir = self.db_path.parent / "_memory_fallback"
        self.fallback_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # 启动时尝试 drain fallback 队列(幂等,失败仅 warning)
        try:
            self.replay_fallback()
        except Exception as exc:  # noqa: BLE001 - 启动路径绝不被污染
            logger.warning("memory fallback replay at init failed: {}", exc)
```

**关键不变量**：
- `MemoryExtractor.__init__` 已有的 `database.replay_fallback()` 调用保留（双调用幂等：第一次清空，第二次 no-op）。
- `MemoryDatabase` 直接被构造时（CLI / 迁移脚本），构造时也会自动 drain。
- `replay_fallback` 已用 `with self.connect() as conn:` 内部串行化，多线程并发安全。

**测试**：
- `tests/memory/test_fallback.py` 新增 `test_database_init_replays_fallback`：
  - 在 `MemoryDatabase.__init__` 之前预先往 `fallback_dir` 写一个 valid memory JSON。
  - 构造 `MemoryDatabase`。
  - 验证：fallback 文件被删除，DB 中能查到对应 memory。
- 新增 `test_database_init_replay_failure_does_not_break_init`：
  - 预先往 `fallback_dir` 写一个**无效** JSON（缺 `item` 字段）。
  - 构造 `MemoryDatabase`，验证构造不抛异常，仅 warning 日志。
- 现有 `test_drains_files` / `test_keeps_failed_files` 不变。
- `TestReplayOnInit`（现有测试，验证 `MemoryExtractor.__init__` 触发的 replay）不变；现在新增的测试验证 `MemoryDatabase` 自身也能 replay。

## 4. 验证清单

```bash
cd /Users/mima0000/Documents/学习-001/do-project/Agent-With-Memory
.venv/bin/pytest tests/memory/ -q                              # 期望 ≥ 528 passed
.venv/bin/ruff check nanobot/memory/ nanobot/agent/hooks/       # 期望 0 新增错误
```

## 5. 风险与回滚

| 风险 | 回滚方案 |
|---|---|
| `conn.rollback()` 在某些 sqlite3 驱动上抛 `InterfaceError`（连接已关） | rollback 也包 try/except，吞掉 |
| `_redact` 误伤合法内容（如英文括号、密钥字面量） | `_REDACT_PATTERNS` 是精确模式（`api_key=xxx`、`Bearer xxx`），误伤概率极低；后续可加单测覆盖合法字段 |
| 双调用 replay_fallback 性能开销 | 已 `if not self.fallback_dir.exists(): return 0`，第二次调用 fallback 已被清空 → glob 出空 list → 0 work |

## 6. 改动文件清单

| 文件 | 改动 | 估算行数 |
|---|---|---|
| `nanobot/memory/extractor.py` | `_safe_write_with_fallback` 加 rollback + redact | +20 / -2 |
| `nanobot/memory/database.py` | `__init__` 末尾调 `replay_fallback` | +6 / -1 |
| `tests/memory/test_fallback.py` | 新增 5 个测试 | +150 / 0 |

**总计**：约 +180 / -3 行。

## 7. 完成定义

- [ ] 3 项 MAJOR 修复落地
- [ ] 新增测试覆盖：rollback 调用 / mid-loop 失败后继续 / fallback 内容 redact / database init replay
- [ ] 既有 521 个 memory 测试全部通过 + 新增测试 0 失败
- [ ] 零新增 ruff 错误
- [ ] 更新 `.ai-runtime-artifacts/verifications/2026-09-12-memory-extractor-phase3-followup-collective-test.md`

---

**请用户确认**：

1. 改动 1（rollback）是否同意？
2. 改动 2（redact 范围只覆盖 content/subject/predicate，不覆盖 tags 数组）是否同意？
3. 改动 3（`MemoryDatabase.__init__` 自动调 `replay_fallback`，与 `MemoryExtractor.__init__` 的现有调用双调用幂等）是否同意？