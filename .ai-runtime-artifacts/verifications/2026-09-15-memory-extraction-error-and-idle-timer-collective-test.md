---
artifact: collective-test
task: 2026-09-15-memory-extraction-error-and-idle-timer
spec: .ai-runtime-artifacts/specs/2026-09-15-memory-extraction-error-and-idle-timer-spec.md
plan: .ai-runtime-artifacts/plans/2026-09-15-memory-extraction-error-and-idle-timer-plan.md
branch: feature/memory-system
executed_by: Leader（用户指令「你直接改吧，不用子Agent」，未派发 WU）
executed_at: 2026-09-15
status: passed
---

# 集体测试：记忆抽取三项修复

## 1. 范围

| WU | 改动文件 | 状态 |
|---|---|---|
| WU-A | 新增 `nanobot/memory/llm_error.py`、`nanobot/session/labels.py`；改 `nanobot/memory/{extractor,profile_extractor,experience_extractor,scratchpad_writer}.py` | 完成 |
| WU-B | 改 `nanobot/agent/hooks/memory_extraction.py`、`nanobot/agent/loop.py` | 完成 |

改动量：6 个既有文件 +136/−8；新增 2 个模块（56 + 112 行）+ 4 个测试文件（769 行）。

## 2. 执行命令与结果

### 2.1 全量回归

```bash
.venv/bin/python -m pytest -q
```

```
13 failed, 7269 passed, 19 skipped in 174.34s
```

### 2.2 定向回归（本任务直接覆盖面）

```bash
.venv/bin/python -m pytest -q tests/memory/ tests/session/ tests/channels/test_websocket_application_boundary.py
```

```
807 passed in 7.41s
```

### 2.3 新增用例

```bash
.venv/bin/python -m pytest -q --collect-only <4 个新文件>
```

```
44 tests collected
```

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| `tests/memory/test_llm_error_surfacing.py` | 13 | `response_error` 单元；`_call_track` 返回 `call_failed: …403…`；`generate_episode`；`ProfileExtractor` / `ExperienceExtractor` / `ScratchpadWriter` 遇错响应不再落 `Error: …` |
| `tests/memory/test_idle_timer_reset.py` | 10 | 新消息取消旧计时器；`before_run` 不装备；连续打断不触发；`cancelled`/`error` 重新装备；正常路径不被覆盖 |
| `tests/memory/test_extraction_log_labels.py` | 5 | hook 与 extractor 的日志带 `[摘要: …]`；空标签不留空括号 |
| `tests/session/test_labels.py` | 16 | title 优先 / 空标题回退首条用户消息 / 截断 / think 剥离 / `(none)` / 常量漂移守卫 |

### 2.4 Lint 与类型

```bash
.venv/bin/python -m ruff check <本任务改动文件>
uv run --no-sync basedpyright nanobot/memory/ nanobot/agent/hooks/memory_extraction.py nanobot/agent/loop.py
uv run --no-sync basedpyright nanobot/session/labels.py nanobot/memory/llm_error.py
```

```
ruff: 本任务改动文件 0 error（仓库其余 35 error 全在未触碰的既有测试文件）
basedpyright: 改动范围 452 error —— 与 HEAD 基线 452 完全一致（零新增）
basedpyright: 两个新增模块 0 error
```

## 3. 13 个失败用例的归属判定

**结论：全部为既有失败，与本任务无关。已在 HEAD（stash 后）复跑同一批文件确认。**

```bash
# 基线复现（git stash push -u 后）
.venv/bin/python -m pytest -q tests/agent/test_mcp_reconnect_crash.py tests/cli/... tests/tools/...
# → 13 failed, 291 passed   （与改动后数量、名单逐条一致）
```

| 用例 | 失败原因 | 性质 |
|---|---|---|
| `test_probe_uses_default_port_for_http`、`test_probe_tries_next_validated_ip_…` | `unreachable-host.test` 在本机 DNS 解析成功 → 探测返回 `True` | 环境依赖（真实 DNS） |
| `test_web_fetch_*`（3 个） | 真实访问 `example.com` 得 404 / 真实 `ConnectError` | 环境依赖（真实网络） |
| `test_connect_mcp_servers_http_clients_reject_unsafe_redirect_targets`（2 个） | 同上，真实网络 | 环境依赖 |
| `test_mcp_reconnect_during_shutdown_does_not_crash` | 等 `reconnect_started` 超时 5s | 时序/环境 |
| `test_launcher_keeps_the_tui_alive_…` | 同类超时 | 时序/环境 |
| `test_serve_*`（4 个） | `_fake_create_app() got an unexpected keyword argument 'session_manager'` | 既有 test/代码漂移，两侧均未被本任务触碰 |

## 4. 过程中发现并修掉的真实回归

`tests/channels/test_websocket_application_boundary.py::test_persisted_webui_session_prefix_has_one_production_owner`
—— 该架构约束测试要求全仓库（除 `nanobot/webui/session_identity.py`）不得出现字面量 `"websocket:"`。

首次全量跑出 **1 个由本任务引入的失败**：

```
assert ['nanobot/session/labels.py'] == []
```

成因：新增的 `nanobot/session/labels.py` 模块 docstring 里为了说明问题，写了 `Processing message from websocket:<sender_id>`。

修复：改为 `Processing message from <channel>:<sender_id>`（[labels.py:6](nanobot/session/labels.py#L6)）。复跑 807 passed。

> 注：该字面量在 `nanobot/webui/session_list_index.py` / `session_identity.py` 中是合规的，未做改动。

## 5. 验收口径核对

| spec/plan 验收项 | 结果 |
|---|---|
| 新增用例全绿 | ✅ 44 passed |
| `tests/memory/` 无回归 | ✅ |
| `ruff check` 改动文件干净 | ✅ 0 error |
| 类型检查零新增 | ✅ 452 = 452 |
| 「403 响应 → `_call_track` 返回 `call_failed: …403…`」断言存在 | ✅ |
| 「`ScratchpadWriter` 收到 403 时不把 `Error: …` 写库」断言存在 | ✅ |
| 测试名自解释（`test_new_message_cancels_pending_idle_timer` 等） | ✅ |
| plan 偏离项（未复用 `webui_turns` 常量）有兜底 | ✅ `TestMetadataKeyDriftGuard` 文本比对（不 import 重依赖模块） |

## 6. 未覆盖项

- **真实 gateway 手工冒烟**（plan 尾盘第 3 项）：未执行——需起 gateway + 真实会话复现时序，本任务止步于自动化验证。日志格式正确性由 `test_extraction_log_labels.py` 断言覆盖，但「真机日志里确实出现 `[摘要: …]` 且不再有 `unparseable JSON`」这一步没有实测证据。
- 13 个既有失败用例未修（超出本任务范围）。
