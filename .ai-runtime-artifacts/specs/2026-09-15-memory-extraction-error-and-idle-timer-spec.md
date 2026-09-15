---
artifact: spec
route: Harness:brainstorming
skills:
  - systematic-debugging
skills_evidence:
  - ~/.claude/skills/systematic-debugging/SKILL.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - 用户 2026-09-15 会话内三轮确认（问题定性 / 计时器语义 / 会话摘要定义）
created_at: 2026-09-15
status: approved
approved: true
---

# Spec：记忆抽取三项修复（provider 错误识别 / idle 计时器归零 / 抽取日志带会话摘要）

> 触发：用户 2026-09-15 22:10 的 gateway 日志出现
> `WARNING | memory extraction LLM returned unparseable JSON (episode; len=103; head="Error: {'message': 'Authentication failed...', 'type': 'permission_error'}")`
> 排查过程见本 spec §1 证据链（systematic-debugging Phase 1）。
> 关联代码：`nanobot/providers/openai_compat_provider.py:1879`、`nanobot/providers/base.py:1771/1857`、
> `nanobot/memory/extractor.py:1155/1631/856/908`、`nanobot/memory/profile_extractor.py:119`、
> `nanobot/memory/experience_extractor.py:50`、`nanobot/memory/scratchpad_writer.py:241`、
> `nanobot/agent/hooks/memory_extraction.py:298/443/480/515/544`、`nanobot/agent/runner.py:311-350`、
> `nanobot/agent/loop.py:585-590`。

---

## 1. 证据链（已取证，不再讨论）

| # | 事实 | 证据 |
|---|---|---|
| 1 | 那次调用真的 403 了，不是"JSON 格式不对" | `~/.nanobot/llm_usage.sqlite3` 22:10:05 两条 `custom-provider / google/gemini-3.7-flash / stream=0 / finish_reason=error / error_status_code=403`，正好对应 `failed_tracks=['semantic','episode']` 两轨 |
| 2 | provider 层把 HTTP 错误包装成"正常回复" | `_handle_error` → `LLMResponse(content=f"Error: {body}", finish_reason="error")`，**不抛异常**（[openai_compat_provider.py:1879](nanobot/providers/openai_compat_provider.py#L1879)） |
| 3 | 重试层对非瞬时错误原样返回 | `_run_with_retry` 仅在 `finish_reason != "error"` 时返回；403 非瞬时 → 走 1857 行分支把错误响应交给调用方 |
| 4 | 记忆子系统**从不检查** `finish_reason` | `grep -rn "finish_reason" nanobot/memory/ nanobot/agent/hooks/memory_extraction.py` → **0 命中** |
| 5 | 同一 key 打 `deepseek-v4.1-flash` 是成功的 | 22:10:05 同 provider 同 base URL 的成功记录 → 是**模型权限**问题（403 permission_error），不是 key 失效 |
| 6 | idle 计时器只在 `after_run` 装备 | `_arm_idle_timer` 唯一调用点在 [memory_extraction.py:311](nanobot/agent/hooks/memory_extraction.py#L311)；轮次被取消时 runner 直接 `raise CancelledError`，不走 `after_run`（[runner.py:317-321](nanobot/agent/runner.py#L317-L321)） |
| 7 | `Processing message from websocket:anon-…` 打的是 sender_id | [loop.py:1958](nanobot/agent/loop.py#L1958) → 两个会话在日志里长得一模一样，是本次误判的根源 |

---

## 2. Part A（P0）：记忆子系统统一识别 provider 错误

### 2.1 问题

任何 agent 侧 HTTP 失败（401/403/429/5xx）在记忆抽取链路里都会被读成"模型回了段没法解析的文字"，表现为：

- 日志误导：报 `unparseable JSON`，真实原因（403）被埋进 `head=` 里；
- 失败原因误导：`failed_tracks=['semantic','episode']`，原因记为 `invalid_json`；
- **更糟的一条**：`scratchpad_writer.py` 把 `resp.content` 直接当正文写入（[L241](nanobot/memory/scratchpad_writer.py#L241) 之后 `_build_scratchpad(text, …)`），403 的 `"Error: {...}"` 会被**当成草稿本内容落库**。

### 2.2 方案

新增 `nanobot/memory/llm_error.py`：

```python
def response_error(response: Any) -> str | None:
    """provider 把 HTTP 错误转成 content 时，返回简短原因；正常响应返回 None。"""
    if getattr(response, "finish_reason", None) != "error":
        return None
    ...  # HTTP <status> + error_kind + error_type，拼成 "llm_error: HTTP 403 authentication"
```

各调用点在**读 content 之前**先调用它，命中则返回 `call_failed: <reason>` / `error=<reason>`，并把日志从 `unparseable JSON` 降级为明确的 `LLM call failed: llm_error: HTTP 403 …`。

### 2.3 改动点

| 文件:行 | 现状 | 改为 |
|---|---|---|
| [extractor.py:1155](nanobot/memory/extractor.py#L1155) `_call_track` | 只看 content → `invalid_json` | 先判错误 → `call_failed: <reason>` |
| [extractor.py:1631](nanobot/memory/extractor.py#L1631) episode 摘要补全 | 静默忽略 | warning（保留原兜底行为） |
| [profile_extractor.py:119](nanobot/memory/profile_extractor.py#L119) | → `error="invalid_json"` | → `error="<reason>"` |
| [experience_extractor.py:50](nanobot/memory/experience_extractor.py#L50) | 返回 `[]` | 返回 `[]` + warning 带原因 |
| [scratchpad_writer.py:241](nanobot/memory/scratchpad_writer.py#L241) | 错误文本当正文落库 | 命中则走 `_minimal_fallback` |
| [memory_extraction.py:443](nanobot/agent/hooks/memory_extraction.py#L443) 话题切换检测 | parse 失败 → True | 命中 → 记录明确原因（仍返回 True，保守不变） |

### 2.4 明确不做

- **不做"第一轨发现 401/403 就取消另一轨"的短路**：保持现有失败隔离结构，代价只是多打一次必然失败的请求。若后续确定要省这次请求，另开 WU。
- 不改 provider 层（`_handle_error` 把错误塞进 content 是全框架既有约定，动它影响面远超本 spec）。

---

## 3. Part B：idle 计时器按「用户消息」归零

### 3.1 问题

计时器实际语义是「距上一轮**正常结束** 120s」，不是「距用户最后一条消息 120s」。用户在模型还在流式输出时发下一条，那一轮被取消 → 不重置 → 计时器仍从更早的那一轮往下数。

### 3.2 目标语义（用户原话：「如果有对话应该重置计时器 idle」）

> 触发时刻 = `max(最后一条用户消息, 最后一轮结束) + IDLE_THRESHOLD_SECONDS`，且**轮次进行中绝不触发**。

### 3.3 数据案例（节奏取自 22:09 真实日志：用户 30s 一条、模型回复慢、常被打断）

| 时刻 | 事件 | 现状：计时器指向 | 修复后 |
|---|---|---|---|
| 22:20:00 | 一轮正常结束 | **22:22:00** | 22:22:00 |
| 22:20:10 | 用户发「B」 | 22:22:00 不动 | 22:22:10 |
| 22:20:40 | 用户发「C」，打断 B | 22:22:00 不动 | 22:22:40 |
| 22:21:10 | 用户发「D」，打断 C | 22:22:00 不动 | 22:23:10 |
| 22:21:40 | 用户发「E」，打断 D | 22:22:00 不动 | 22:23:40 |
| **22:22:00** | — | ⚠️ **触发**：用户 20s 前才说过话，且抽的是 22:20:00 那份旧对话（B~E 不在内） | 不触发 |
| **22:23:40** | — | — | ✅ 触发，内容含 B~E |

### 3.4 方案（两处改动）

| 位置 | 改动 |
|---|---|
| [memory_extraction.py:298](nanobot/agent/hooks/memory_extraction.py#L298) `before_run` | 只**取消**该 session 的 pending idle 计时器（归零，不重新装备） |
| [memory_extraction.py:332](nanobot/agent/hooks/memory_extraction.py#L332) `on_finally` | 当 `context.stop_reason in ("cancelled", "error")` 时**装备**计时器（覆盖"最后一轮被打断/失败后就没人再 arm"的漏洞） |
| `after_run`（[L310](nanobot/agent/hooks/memory_extraction.py#L310)） | 不变，仍负责正常路径的装备 |

**为什么不能简单在 `before_run` 里"取消 + 重新装备"**：装备意味着 120s 后抽取，而 `before_run` 时刻这一轮还没跑完——长轮次（工具调用几分钟）会在**对话进行中**被抽。所以归零 = 只取消；装备仍绑定在"轮次结束/被打断"这两个已定格的时间点上。

**约束**：[L336-344](nanobot/agent/hooks/memory_extraction.py#L336-L344) 的既有注释明确 `on_finally` 不能"取消"计时器（与 `after_run` 相隔微秒）。本次只**新增装备**，且用 `stop_reason` 守卫（正常路径 `stop_reason` 为 `stop`/`length`，不会误伤 `after_run` 刚装的那个）。

### 3.5 边界行为

| 场景 | 修复后行为 |
|---|---|
| 用户连发消息、每轮都被打断 | 全程不触发（计时器始终处于 disarm） |
| 用户说完最后一条、那一轮被打断后就不说话了 | 取消时刻 +120s 触发（`on_finally` 装备） |
| 用户说完最后一条、轮次正常结束 | 轮次结束 +120s 触发（`after_run` 装备，行为不变） |
| 会话被删除 | 走既有 deletion 路径，不受影响 |

---

## 4. Part C：记忆抽取日志带「会话摘要」

### 4.1 摘要定义（用户以截图确认，已存 memory）

**就是 WebUI 侧边栏会话列表那行标签**：`session.metadata["title"]` 优先；title 为空时回退**首条用户消息**（截断 ~30 字）。

- 有 title：`初次问候与助手介绍`
- title 为空：`你好，我的名字叫做黄启华`（真实案例——首轮 403 导致标题生成没跑，title 是空串）

### 4.2 改动点（5 处日志）

| 位置 | 日志 |
|---|---|
| [memory_extraction.py:515](nanobot/agent/hooks/memory_extraction.py#L515) | `idle timer fired for session …` |
| [extractor.py:908](nanobot/memory/extractor.py#L908) | `idle extraction start for session …` |
| [extractor.py:856](nanobot/memory/extractor.py#L856) | `… extraction for session …: N new messages -> M memories` |
| [memory_extraction.py:544](nanobot/agent/hooks/memory_extraction.py#L544) | `idle memory extraction finished for session …` |
| [loop.py:585-590](nanobot/agent/loop.py#L585-L590) | 会话删除时的抽取（顺带） |

目标格式（示例即本次真实会话）：

```
idle timer fired for session websocket:a589b74f-340c-4a04-9fc2-07805fdfcef2 [摘要: 你好，我的名字叫做黄启华] after 120.0s idle; …
```

### 4.3 数据来源

- extractor 侧：`extract_incremental(session)` 已拿到 `Session` → 直接取 `session.metadata["title"]`，空则回退 `session.messages` 首条 user。
- hook 侧：hook 只有 `session_key`；在 [loop.py:551-556](nanobot/agent/loop.py#L551-L556) 构造 hook 时**本来就已经** `get_or_create(session)`，顺手算出标签传入，不新增查询。
- 新增 `nanobot/session/labels.py:session_label(messages, metadata) -> str`，两侧共用（纯函数，易测）。

### 4.4 明确不做

- 不打"LLM 生成的对话摘要"（成本、延迟、且与用户所指不是一回事）。
- 不改 `Processing message from …`（属 agent loop 主链路，非本次记忆抽取范围）；如需另开 WU。

---

## 5. 验收

| 项 | 验收标准 |
|---|---|
| A | 伪造 `finish_reason="error"` + `error_status_code=403` 的响应：`_call_track` 返回 `call_failed: …403…`；日志不再出现 `unparseable JSON`；`ScratchpadWriter` 不把错误文本落库 |
| B | 单测模拟「新消息打断上一轮」：计时器被取消且不再从旧锚点触发；`stop_reason="cancelled"` 时 `on_finally` 装备新计时器 |
| C | 5 处日志均带摘要；title 为空时回退首条用户消息；摘要函数单测覆盖「有 title / 空 title / 无 user 消息」三种 |
| 回归 | `pytest tests/memory/ -q` 全绿；`ruff check nanobot/` 通过 |

---

## 6. Next

1. 用户确认本 spec（门禁）。
2. 落 plan（WU 拆分：WU-A 建两个 helper + 改 `nanobot/memory/**`；WU-B 改 `nanobot/agent/hooks/` + timer + loop 日志；串行，B 依赖 A 的 helper）。
3. 编排派发 → 集体测试 → 集体审查 → execution-log 关闭。
