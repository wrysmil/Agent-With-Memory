# nanobot 记忆抽取优化 Phase 3 — 集体测试报告

> Group: `memory-extractor-phase3`（WU-1 / WU-3A / WU-2 / WU-3B / WU-4）
> 计划: `.ai-runtime-artifacts/plans/2026-09-12-memory-extractor-phase3-plan.md`
> 调研: `.ai-runtime-artifacts/research/2026-09-12-openakita-extractor-survey.md`
> 日期: 2026-09-12
> 状态: **PASS**

---

## 1. 范围

| WU | 主题 | 优先级 | 关键文件 |
|---|---|---|---|
| WU-1 | `_render_transcript` head+tail 双向保留 | P1 | `nanobot/memory/extractor.py` |
| WU-3A | `_resolve_action_success` 孤儿 ActionNode 修复 | P3 | `nanobot/memory/extractor.py` |
| WU-2 | `extract_incremental` 主题切换增量抽取 + hook 接入 | P2 | `nanobot/memory/extractor.py`, `nanobot/memory/models.py`, `nanobot/agent/hooks/memory_extraction.py` |
| WU-3B | `safe_write_with_fallback` + `replay_fallback` | P3 | `nanobot/memory/database.py`, `nanobot/memory/extractor.py`, `nanobot/memory/models.py` |
| WU-4 | `_build_prompt_messages` 参数化（max_chars / head_ratio） | P4 | `nanobot/memory/extractor.py` |

**diff 范围**（vs HEAD）：

```
nanobot/agent/hooks/memory_extraction.py    |  55 +++-
nanobot/memory/database.py                  |  69 +++++
nanobot/memory/extractor.py                 | 326 +++++++++++++++++----
nanobot/memory/models.py                    |  61 ++++
tests/memory/test_extractor.py              | 440 +++++++++++++++++++++++++++-
tests/memory/test_hook_memory_extraction.py |  21 +-
6 files changed, 911 insertions(+), 61 deletions(-)
```

新增测试文件：`tests/memory/test_fallback.py`、`tests/memory/test_memory_extraction_hook.py`。

---

## 2. 执行结果

### 2.1 范围测试（memory）

```bash
.venv/bin/pytest tests/memory/ -q
# 521 passed in 6.17s
```

### 2.2 全量回归

```bash
.venv/bin/pytest tests/ -q
# 9 failed, 6004 passed, 8 skipped in 143.98s
```

**9 个失败用例与本次改动无关**，均为 baseline 既有失败，已用 `git stash` 验证：还原全部本组改动后同样失败，原始 commit `cc49094` 已存在。失败集中在网络/MCP/SSRF 安全测试，与记忆抽取模块无交叉：

- `tests/tools/test_mcp_tool.py::test_connect_mcp_servers_http_clients_reject_unsafe_redirect_targets[*]`（2 个）
- `tests/tools/test_web_fetch_security.py`（3 个：DNS rebind、私有 redirect image、私有 redirect target）
- 以及其他 4 个 `test_mcp_tool.py` 用例

记入「已知失败」清单，等待独立排查；**不影响本次优化交付**。

### 2.3 Lint（ruff）

```bash
.venv/bin/ruff check nanobot/memory/ nanobot/agent/hooks/
# 9 errors — 全部为本组改动前的预存在 F401 / N806，未新增
```

本组改动的文件单独 lint：

```bash
.venv/bin/ruff check nanobot/memory/extractor.py nanobot/memory/database.py nanobot/memory/models.py tests/memory/test_fallback.py
# All checks passed!
```

---

## 3. WU 自测摘要

| WU | 新增/调整测试 | 验证命令 | 结果 |
|---|---|---|---|
| WU-1 | 6 个 `_render_transcript` 用例 | `pytest tests/memory/test_extractor.py -q` | 491 → pass |
| WU-3A | 9 个用例（4 helper + 5 端到端） | 同上 | 500 → pass |
| WU-2 | 端到端 + helper + hook 集成 | `pytest tests/memory/test_extractor.py tests/memory/test_memory_extraction_hook.py -q` | pass |
| WU-3B | 7 个 fallback 用例（replay / safe_write / persist / init） | `pytest tests/memory/ -q` | 518 → pass |
| WU-4 | 3 个 `_build_prompt_messages` 用例 | `pytest tests/memory/test_extractor.py -q` | 74 → pass |

---

## 4. 与方案的一致性

| 方案要求 | 落地状态 |
|---|---|
| head+tail 替代纯 tail 截断（WU-1） | ✅ `_render_transcript` 默认 head=0.4 / tail=0.45 |
| 增量抽取入口（WU-2） | ✅ `extract_incremental(session, last_extracted_index)`，复用规则信号 + 去重 + 持久化 |
| `EpisodeSource.TOPIC_CHANGE` 枚举 | ✅ `models.py:35` |
| hook 主题切换接入增量（30s timeout） | ✅ `_run_incremental_extraction` + `_compute_incremental_start_index` |
| 孤儿 ActionNode success 修正（WU-3A） | ✅ `_resolve_action_success(has_result=False)→False` |
| DB 失败 fallback + replay（WU-3B） | ✅ `MemoryDatabase.fallback_dir` + `replay_fallback` + `_safe_write_with_fallback`，启动时 `MemoryExtractor.__init__` 自动重放 |
| `_build_prompt_messages` 参数化（WU-4） | ✅ `transcript_max_chars` / `transcript_head_ratio` 透传 |

**未引入的 OpenAkita 特性（按方案 §5 决策）**：
- smart_truncate sidecar 文件 — 仍不引入
- 三轨道并发（Episode/Profile/Experience）— 仍不引入
- 去重改为 evolve — 仍不引入
- MEMORY.md 容量治理 — 仍不引入
- `enable_thinking=False` — 不强制改 runtime 配置

---

## 5. 风险与遗留

1. **既有 9 个失败测试**：与本组无关，已 baseline 复现，建议另开 issue。
2. **`fallback_dir` 启动重放**：当前在 `MemoryExtractor.__init__` 触发；如未来 `MemoryDatabase` 单独使用路径不会自动重放，可后续在 `MemoryDatabase.__init__` 也挂一份。属于 WU-3B 的「设计偏差」披露，方案留有 buffer。
3. **topic change 触发频率**：当前 `_topic_gate.interval_seconds=60` 已经在 S5 落到位；增量抽取本身不再重复整 session 扫描。
4. **增量抽取与 T1 full 抽取并存**：T1 after_run 仍每轮跑一次 full extraction；T5 topic-change 现在跑增量；两层职责清晰。如果后续需要进一步降频，需要单独调整 T1（不在本方案范围）。

---

## 6. 完成定义

- [x] 4 个核心 WU 全部落地（WU-1 / WU-3A / WU-2 / WU-3B / WU-4）
- [x] 521 个 memory 测试 100% 通过
- [x] 零新增 ruff 错误
- [x] 全量回归失败用例已确认与本组无关
- [x] 集体测试报告落盘
- [x] 待派 reviewer 独立审查（下一动作）