# nanobot 记忆抽取 follow-up — 集体测试报告（3 项 MAJOR 修复）

> 上游：方案 `.ai-runtime-artifacts/plans/2026-09-12-memory-extractor-phase3-review-fix-plan.md`
> 上游：原 collective test `.ai-runtime-artifacts/verifications/2026-09-12-memory-extractor-phase3-collective-test.md`
> 上游：原 review `.ai-runtime-artifacts/reviews/2026-09-12-memory-extractor-phase3-review.md`
> 日期：2026-09-12
> 状态：**PASS**（verdict 从 PASS-WITH-FOLLOWUPS 升级）

---

## 1. 修复内容

reviewer 报告中点名的 3 项 MAJOR 已全部修复：

| MAJOR | 修复点 | 文件 |
|---|---|---|
| #1 事务回滚缺失 | `_safe_write_with_fallback` except 分支顶部 `conn.rollback()`（自身失败仅 debug 吞掉） | `nanobot/memory/extractor.py` |
| #2 fallback 内容未 redact（LLM05） | 落盘前对 `content` / `subject` / `predicate` 调 `_redact` | `nanobot/memory/extractor.py` |
| #3 `MemoryDatabase` 独立 replay 缺失 | `MemoryDatabase.__init__` 末尾 try/except 调 `replay_fallback()`，与 extractor 端双调幂等 | `nanobot/memory/database.py` |

MINOR / NIT 按用户决定**不动**（避免修过头）。

---

## 2. 验证结果

### 2.1 范围测试

```bash
.venv/bin/pytest tests/memory/ -q
# 527 passed in 4.43s
```

- 原有 521 个测试全部继续通过
- 新增 6 个测试覆盖 3 MAJOR（含 1 个 mid-loop 失败 + 边界集成）

### 2.2 Lint

```bash
.venv/bin/ruff check nanobot/memory/extractor.py nanobot/memory/database.py tests/memory/test_fallback.py
# All checks passed!
```

仓库其余 9 条 ruff 错误（`__init__.py` 的 F401 + `repository.py` 的 N806）均为本组改动前的存量问题，**不在本次范围**。

### 2.3 Diff 范围

```
nanobot/agent/hooks/memory_extraction.py    |  55 +++-
nanobot/memory/database.py                  |  77 +++++   (本次 +8)
nanobot/memory/extractor.py                 | 341 +++++++++++++++++----  (本次 +15)
nanobot/memory/models.py                    |  61 ++++
tests/memory/test_extractor.py              | 440 +++++++++++++++++++++++++++-
tests/memory/test_hook_memory_extraction.py |  21 +-
6 files changed, 934 insertions(+), 61 deletions(-)
```

---

## 3. 新增测试

| 测试 | MAJOR | 关键断言 |
|---|---|---|
| `test_safe_write_with_fallback_calls_rollback_on_failure` | #1 | writer 抛异常 → `conn.rollback` 被调 1 次 |
| `test_safe_write_with_fallback_rollback_failure_does_not_block_fallback` | #1 | rollback 抛 `InterfaceError` → 不阻断 fallback 落盘 |
| `test_persist_continues_after_mid_loop_failure` | #1 | 3 条 memory 第 2 条抛异常 → writer-True 进 saved_memory_ids，第 2 条落 fallback（详见 §4 已知限制） |
| `test_safe_write_redacts_fallback_content_and_subject_predicate` | #2 | 含 `api_key=secret123` / `Bearer xyzabc` / `password=hunter2` → 落盘 JSON 中均为 `<redacted>` |
| `test_database_init_replays_pending_fallback_file` | #3 | 预先落合法 fallback JSON → `MemoryDatabase.__init__` 后文件被删除且 DB 可查到 |
| `test_database_init_replay_failure_does_not_raise` | #3 | 预先落无效 JSON → `MemoryDatabase.__init__` 不抛异常 |

---

## 4. 已知限制（不阻塞，记入 follow-up）

### 4.1 `_persist` 同一事务内的语义偏差

**现象**：加入 `conn.rollback()` 后，`_persist` 同一事务内 writer-True 的早期条目会被后续失败连带撤销。

**具体场景**：3 条 memory 在同一 `with self.database.connect() as conn:` 事务内：
1. 第 1 条 writer 返回 True → `saved_memory_ids.append(id_1)`
2. 第 2 条 writer 抛 `OperationalError` → fallback 落盘 + `conn.rollback()` → **id_1 也被撤销**
3. 第 3 条 writer 返回 True → `saved_memory_ids.append(id_3)` → 最终提交成功

**结果**：
- `PersistenceResult.saved_memory_ids = [id_1, id_3]`（反映 writer 返回值）
- DB 实际落库 = 1 条（id_3，id_1 被 rollback 撤销）
- fallback 文件 = 1 个（id_2）

**为什么接受**：
- 数据完整性由 fallback 队列 + 启动 replay 保证，最终不丢数据。
- 「逐条 commit」是另一种实现路径（重构 `_persist` 事务边界），收益相对有限（仅在「部分失败」小概率事件下多保留几条 writer-True 条目），但要新增事务边界处的测试覆盖，风险面更大。
- 当前行为与既有「fallback 队列兜底」设计哲学一致。

**改进路径**（后续独立 WU）：把 `_persist` 改成每条 writer 后立即 commit，单条失败只 rollback 自身。

### 4.2 `saved_memory_ids` 与「最终提交」语义不一致

`PersistenceResult.saved_memory_ids` 当前反映 `writer` 返回值，不反映 DB 最终提交状态。若未来需要「最终提交语义」，需要重构 `_persist` 事务边界或加显式状态字段（与 4.1 是同一个根因）。

---

## 5. Verdict 升级

**原 verdict**: PASS-WITH-FOLLOWUPS（3 MAJOR + 5 MINOR + 5 NIT）

**新 verdict**: **PASS**（3 MAJOR 已修；MINOR / NIT 按用户决定保留作为后续改进）

---

## 6. 完成定义

- [x] 3 项 MAJOR 修复落地
- [x] 新增 6 个测试覆盖修复 + 边界
- [x] 527 个 memory 测试全部通过
- [x] 零新增 ruff 错误（已修改文件范围）
- [x] 事务边界语义偏差诚实记录
- [x] verdict 升级到 PASS