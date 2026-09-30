---
artifact: review
type: spec-review
target: .ai-runtime-artifacts/specs/2026-09-29-working-memory-injection-spec.md
target_revision: 1
reviewed_revision: 2
date: 2026-09-29
verdict: BLOCK → 修订后放行
reviewers:
  - 提取侧（独立实例，read-only）
  - 注入侧（独立实例，read-only）
leader_verification: 全部关键结论经 Leader 复验源码行号
---

# 工作记忆注入 spec 审查记录

**对象**：spec v1
**结论**：**BLOCK**。核心前提「读取链路从未存在」成立，但写入侧 4 条主张中 3 条不成立、1 条部分成立；注入侧另有 5 处遗漏或论据错误。
**处置**：spec 修订为 v2，逐条处置见 v2 §七。本文只记录结论与证据。

---

## 提取侧结论

| # | 主张 | 结论 | 证据 |
| --- | --- | --- | --- |
| E1 | `update_focus` 每轮整行覆盖，抹掉 LLM 抽取结果 | 调用链成立，**归因错误** | `scratchpad_writer.py:145-149` 确实硬编码空值；但 LLM 路径从未落库（见 E2），被抹值恒为空。merge 修复无害但无用 → 移出范围 |
| E2 | S4 能写 `open_questions`/`next_steps` | **双重失效** | ① `memory_extraction.py:265-267` `getattr(type(self),"S4_SCRATCHPAD_REFORMAT_ENABLED",False)`，该属性未定义且全仓无覆盖 → 分支永不执行；② `orchestrator.py:103-107` 调用 `format_with_llm(None, ep_summary)` 后**丢弃返回值**，docstring 明写「调用方负责写库」而调用方未写；③ `_build_scratchpad:288` 只设 `content` |
| E3 | CHAT 跳过能兜住轮转污染 | **护栏不成立** | `intent.py:146` 默认兜底 TASK；`「那再帮我看看 B」`命中 `_TASK_VERBS`（`:53-65`）判 TASK，`「那 C 呢」`命中 `_FOLLOW_UP_CJK`（`:73-76`）判 FOLLOW_UP，均 ≠ CHAT。`update_focus:134-141` 轮转不与 `existing_projects` 去重 → 5 轮同话题即可占满 5 槽位 |
| E4 | user_id / workspace_id 读写一致 | **workspace 成立，user_id 不一致** | `_MEMORY_WORKSPACE_ID="default"`（`loop.py:134`）→ `memory_services.py:36` → `loop.py:533` 三者一致；但 `loop.py:536-541` 构造 `MemoryExtractor` **不传 user_id**，落回 `extractor.py:710` 默认 `"default"`，而写入侧用 `user_id_for_key` 派生的 chat_id。`extractor.py:1123` 永远查不到行 |
| E5 | extractor 快照链路完整 | 链路完整但**恒空** | 因 E4 |
| E6 | 列表项 `[:5]` 截断 | 成立但当前无风险 | 写入侧确无上界，但值恒为空 |

**审查者额外发现**：`archive_completed:198` 同样硬编码 `content=""`，是第二个静默清空点（`archive_completed` 生产零调用）。

---

## 注入侧结论

| # | 主张 | 结论 | 证据 |
| --- | --- | --- | --- |
| I1 | 注入位置影响 prompt 缓存 | **不成立** | `anthropic_provider.py:548-549` system 为单 text block、断点挂末位；任一字节变化即整体失效 → 位置影响恒为零。且召回块已每轮变（`context.py:267-271`），system 缓存**现已每轮失效** |
| I2 | token 量级「几百」 | **低估 3–5 倍** | `current_focus` ≤200 字符 + `active_projects` 5×(13+200) ≈ 1300 字符 → 中文约 1300–1600 token。`build_system_prompt` 全文无 budget/truncate（对比召回块有 700 token 硬预算 `engine.py:77,128,243`），不对称。`memory.py:1019` `_SAFETY_BUFFER=1024` < 该漏算量 |
| I3 | 门控与写侧同源 | 部分成立 | 同源（`gateway_runtime.py:514-515`）；但**异常语义相反**——写侧 `memory_extraction.py:283-287` 异常按 enabled 继续，读侧 `context.py:147` 无 try/except。且 `_memory_services is not None` ≠ 抽取已装配（`loop.py:522` 仍会装配），真门是 `memory_extraction_enabled`（`loop.py:481`） |
| I4 | 调用方未考虑 | **遗漏** | subagent `subagent.py:522` 复用父 `session_key` → 必拿父任务焦点；cron `bound_runner.py:127` 复用原会话 key；dream `memory.py:702-704` 的 `dream:YYYYmmdd-HHMMSS` 派生出时间戳当 user_id → 查不到行，**恰好安全但非设计** |
| I5 | 注入行号准确 | 成立 | `context.py:238 if include_memory:` 前、`# Memory`（`:241`）前，与 spec 一致。但 `loop.py:1305` `include_memory=session.policy.persist` 为 False 时长期记忆摘掉而工作记忆仍注入，语义割裂 |
| I6 | 压缩与 summary 冲突 | 部分成立 | 入口 `autocompact.py:92-103` → `memory.py:1186` → `session.metadata["_last_summary"]` → `context.py:256-261`。重复风险成立但 dream/cron 属 `_is_internal_session`（`autocompact.py:64-65`）不压缩，二者不同现 |
| I7 | §1.5 三步清洗方向 | 成立 | — |

---

## 五轴汇总

| 轴 | 状态 | 关键问题 |
| --- | --- | --- |
| 正确性 | ❌ | E1 归因错误、E2 双重断链 → v1 §五.4 验收项不可达 |
| 完整性 | ❌ | I4 三个调用方未决策；I5 `include_memory` 语义未考虑 |
| 性能 | ⚠️ | I2 实测 ~1.5k token；压缩探针漏算 > 安全缓冲 |
| 架构 | ⚠️ | I3 门控位置与判据均需修正 |
| 安全 | ✅ | I7 保留 |

---

## v2 修订确认

v2 已按本审查逐条处置（映射表见 v2 §七），要点：范围收敛为 2 字段注入、新增 D1/D2 两项修复、门控移 loop 侧并改判据、三个调用方逐一定决策、token 数字与压缩探针补漏、删除缓存论据。S4 打通列为后续项 F1。
