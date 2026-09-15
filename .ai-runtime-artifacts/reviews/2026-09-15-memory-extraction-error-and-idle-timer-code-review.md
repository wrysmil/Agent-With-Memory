---
artifact: code-review
task: 2026-09-15-memory-extraction-error-and-idle-timer
spec: .ai-runtime-artifacts/specs/2026-09-15-memory-extraction-error-and-idle-timer-spec.md
plan: .ai-runtime-artifacts/plans/2026-09-15-memory-extraction-error-and-idle-timer-plan.md
collective_test: .ai-runtime-artifacts/verifications/2026-09-15-memory-extraction-error-and-idle-timer-collective-test.md
reviewer: Leader（自审）
independence: ⚠️ 非独立实例
reviewed_at: 2026-09-15
verdict: 通过（含 2 项已知限制、1 项 plan 偏离）
---

# 代码审查：记忆抽取三项修复

## 0. 审查独立性声明（必读）

harness 规范要求「`reviewer` 必须与 coder/implementer 不同 subagent 实例」。**本次未满足**：

- 用户指示「你直接改吧，不用子Agent」→ 代码由 Leader 手写，未派 WU；
- 同一指示排除了派独立 reviewer 子 Agent。

故本次审查为 **Leader 自审**，证据效力**低于**独立审查：作者对自己的实现假设存在盲区，本报告不能替代独立复核。若后续需要合并到主干，建议补一次独立审查。

## 1. 审查范围

`git diff -- nanobot/` 全部 6 个改动文件 + 2 个新增模块 + 4 个测试文件。

## 2. 逐项核查

### 2.1 spec §3 计时器语义（Part B）—— 核心

**结论：正确。已核对 `runner.py` 真实调用链，非仅靠单测。**

| 核查点 | 证据 |
|---|---|
| `before_run` 在生产路径真的被调用（不是只有单测调它） | [runner.py:319](nanobot/agent/runner.py#L319) `await hook.before_run(context)` |
| `stop_reason` 的两个取值确实是 `"cancelled"` / `"error"` | [runner.py:323](nanobot/agent/runner.py#L323)、[runner.py:329](nanobot/agent/runner.py#L329) |
| 正常结束不会误入 `on_finally` 的装备分支 | 成功路径 `stop_reason = result.stop_reason`（[runner.py:339](nanobot/agent/runner.py#L339)），取值 `stop` / `length` / `tool_calls` / `empty_final_response` / `max_iterations`，均不在守卫集合内 |
| 被打断时快照不丢本轮消息 | `_run_core` 对传入的 `messages` **原地** `append`（[runner.py:499](nanobot/agent/runner.py#L499)、[runner.py:544](nanobot/agent/runner.py#L544)），`finally` 的 `deepcopy(messages)`（[runner.py:350](nanobot/agent/runner.py#L350)）拿到的是实时转录，不是初始转录 |
| 「轮次进行中绝不触发」 | `before_run` 只取消不装备；装备点只有 `after_run`（正常结束）与 `on_finally`（取消/报错），两者都在轮次落定之后 |
| 钩子链顺序无冲突 | `AgentHookChain.on_finally` 逐个 `_for_each_hook_safe`，本 hook 与其它 hook 无共享状态 |

### 2.2 spec Part A 错误识别 —— 正确

- 四个接入点（`_call_track` / `generate_episode` / `ProfileExtractor` / `ExperienceExtractor` / `ScratchpadWriter` / 话题检测）**都在读 `content` 之前**调用 `response_error`，符合「provider 把错误当正文返回」的既有约定。
- `ScratchpadWriter` 走 `_minimal_fallback` 而非返回空——正确，避免把 `Error: …` 写进草稿本正文。
- `response_error` 对字段缺失降级为 `llm_error`，不会返回空串导致日志出现 `call_failed: `。

### 2.3 spec Part C 日志摘要 —— 正确，但有 1 项已知限制

- hook 侧 3 条日志 + extractor 侧 2 条 + loop 删除路径 1 条，均已带 `[摘要: …]`；空标签不留空括号（`session_label_suffix` 返回 `""`）。
- `label_provider` 失败隔离到位（try/except → 空标签），不会因为拿不到摘要而让日志/主流程挂掉。

## 3. 发现项

### R1 —— extractor 侧摘要拿不到 title（**已知限制，不阻塞**）

`_run_idle_extraction` 构造 `Session` 时**未传 `metadata`**（[memory_extraction.py:589-592](nanobot/agent/hooks/memory_extraction.py#L589-L592)）：

```python
session = Session(key=self._session_key, messages=[dict(m) for m in context.messages])
```

后果：extractor 侧日志 `session_label(session.messages, session.metadata)` 里 `metadata` 为空 → 永远走「首条用户消息」回退；而 hook 侧日志走 loop 注入的 `label_provider`，能拿到 `metadata["title"]`。

**同一会话的摘要可能显示成两个不同字符串**（如 hook 侧 `初次问候与助手介绍`、extractor 侧 `你好，我的名字叫做黄启华`）。

判定：不影响验收（两处都能辨识会话），但削弱了「一眼认出是哪个会话」的目的。修复成本低（给 `Session` 补 `metadata` 或让 hook 把 label 传进 `run_idle_extraction`），建议作为后续小改动，**本次不扩范围**。

### R2 —— `stop_reason == "error"` 的成功返回会重复装备一次（**无害**）

`_run_core` 有一条「成功返回但 `stop_reason = "error"`」的路径（[runner.py:720](nanobot/agent/runner.py#L720)）。此时 `after_run` 已装备计时器，紧随的 `on_finally` 因守卫命中会**取消并重新装备**。

判定：无害。两次相隔微秒、`context` 同源，净效果与只装备一次相同（新任务替换旧任务）。不修。

### R3 —— 新增模块违反「唯一生产属主」架构约束（**已修**）

见集体测试 §4。`labels.py` docstring 里的字面量 `"websocket:"` 触发
`test_persisted_webui_session_prefix_has_one_production_owner` 失败。已改为 `<channel>:<sender_id>`。

### R4 —— 每轮 `label_provider` 的调用开销（**可接受**）

每轮构建 hook 时调 `sessions.get_or_create(session_key)`：命中缓存即字典查找，**无落盘副作用**（[manager.py:1763-1772](nanobot/session/manager.py#L1763-L1772) 只 `_remember` 到内存）。未命中会触发一次 store 读取，但该会话刚被 loop 创建，实际必然命中。

### R5 —— 偏离 plan：未复用 `webui_turns` 常量（**已记录，已加守卫**）

plan §风险表写「复用 `nanobot/session/webui_turns.py:62` 的 `WEBUI_TITLE_METADATA_KEY`，不硬编码」。实现改为在 `labels.py` 本地复制两个常量，理由：`webui_turns` 会拉起 bus / providers 一串重依赖，而 `labels` 服务于 memory 抽取日志路径，为两个字符串付这笔依赖不划算。

代价是「两边可能悄悄漂移」。**已加兜底**：`TestMetadataKeyDriftGuard` 用文本比对（不 import）断言 `webui_turns.py` 中的常量值与 `labels.py` 一致——漂移时测试立刻红。已在 `tests/session/test_labels.py` 落地。

### R6 —— 测试覆盖偏差点（**可接受**）

plan 只列了 3 个测试文件，实际交付 4 个（多出 `tests/memory/test_extraction_log_labels.py`）。原因：Part C 的日志格式在 plan 中没有对应测试归属，若不单独建文件则 Part C 无自动化证据。属正向偏离。

## 4. 未覆盖 / 明确不做

| 项 | 说明 |
|---|---|
| 真实 gateway 冒烟 | 未执行；日志格式由单测断言，但「真机不再出现 `unparseable JSON`」无实测证据 |
| 13 个既有失败用例 | 与本任务无关，未修（见集体测试 §3） |
| R1 修复 | 超范围，建议后续单独处理 |

## 5. 审查结论

**通过。** 核心语义（计时器归零 / 取消后重新装备）已用 `runner.py` 真实调用链核对，不是只靠单测自证；错误识别接入点齐全且位置正确；日志摘要三条链路落地并有漂移守卫。

带 3 个尾巴交付：R1（摘要两侧不一致，已知限制）、R2（无害重复装备）、R5（plan 偏离，已加守卫）。
