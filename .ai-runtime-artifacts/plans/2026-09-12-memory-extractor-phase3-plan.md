# nanobot 记忆抽取器优化方案（Phase 3）

> 调研依据：`.ai-runtime-artifacts/research/2026-09-12-openakita-extractor-survey.md`
> 当前实现：`nanobot/memory/extractor.py`（1085 行，feature/memory-system 分支）
> 设计依据：`docs/记忆系统/plan/阶段二设计_记忆提取.md`
> 状态：**待用户确认**

## 0. 现状摘要（为什么会做这次优化）

| 问题 | 影响 | 触发场景 |
|---|---|---|
| `_render_transcript` 仅 `joined[-8000:]` 纯 tail 截断（extractor.py:516-518） | **中间大段对话永远无法被 LLM 抽取** | 长 session（> 100 轮） |
| 抽取主要在 `extract_session` 触发（session_end） | 中间事件要等 session end 才被抽取，**长 session 失败成本极高** | 长 session 中途用户说"必须..." |
| ActionNode 的 `_looks_like_error` 在 output 为空时判 success=True（extractor.py:327） | 工具调用孤儿产出污染 episode | tool_call_id 配对失败时 |
| 去重仅"丢弃"，不"evolve" | 历史痕迹丢失，无法审计记忆演化 | 反复表达同一条偏好 |
| LLM 抽取失败时无 fallback 持久化 | LLM 异常 → 数据全丢 | provider 故障 / 超时 |
| `extract_quick_facts` 已在 context_compress 触发，但 prompt 注入的 transcript 仍是尾部 8000 | 压缩前抽取同样有"中间丢" | 上下文压缩 |

## 1. 目标

1. **解决根问题**：长 session 中段对话不再被静默丢弃
2. **提高抽取覆盖率**：从"session_end 单点"扩到"主题切换 + 压缩前 + session_end"三点
3. **增强鲁棒性**：LLM 失败不丢候选，DB 失败不丢数据
4. **保留 nanobot 现有优势**：阶段1 确定性抽取、阶段3 多层防污染、ActionNode 脱敏 + 截断、并发 LLM 调用

## 2. 非目标（这次不做）

- 不引入向量化检索（已在 retrieval/ 子模块，独立工作）
- 不改去重为 evolve 策略（OpenAkita 演进模型更复杂，单独立项）
- 不做 MEMORY.md 容量治理（未在 nanobot 范围）
- 不引入 sidecar 文件机制（依赖 LLM 工具调用，本次走更简单的"主题切换增量抽取"替代）

## 3. 方案设计（4 个 WU）

### WU-1：`_render_transcript` 改为 head+tail 双向保留【优先级 1】

**目标**：解决中间大段对话丢失。

**改动**：

`nanobot/memory/extractor.py:504-518` 当前实现：

```python
def _render_transcript(messages: list[dict[str, Any]], max_chars: int = 8000) -> str:
    lines: list[str] = []
    for msg in messages:
        role = str(msg.get("role") or "")
        if role not in ("user", "assistant"):
            continue
        text = _content_text(msg.get("content")).strip()
        if not text:
            continue
        lines.append(f"[{role}] {text}")
    joined = "\n".join(lines)
    if len(joined) > max_chars:
        joined = "..." + joined[-max_chars:]
    return joined
```

改为（参考 OpenAkita `smart_truncate`，但更简单、无 sidecar）：

```python
# 新增常量（extractor.py 顶部）
_TRANSCRIPT_HEAD_RATIO = 0.4        # 头部 40%
_TRANSCRIPT_TAIL_RATIO = 0.45       # 尾部 45%（剩余 15% 给 marker）
_TRANSCRIPT_TRUNCATE_MARKER = "\n...[中段已截断,完整对话已被压缩或超长]...\n"

def _render_transcript(messages: list[dict[str, Any]], max_chars: int = 8000) -> str:
    """渲染 user/assistant 文本转录,超长时 head + tail 双向保留。

    OpenAkita 用 65% head,我们调成 40% head:开篇任务定义 + 结尾最新进展,
    对 LLM 抽取语义/情节记忆的覆盖率更高。
    """
    lines: list[str] = []
    for msg in messages:
        role = str(msg.get("role") or "")
        if role not in ("user", "assistant"):
            continue
        text = _content_text(msg.get("content")).strip()
        if not text:
            continue
        lines.append(f"[{role}] {text}")
    joined = "\n".join(lines)
    if len(joined) <= max_chars:
        return joined
    head_chars = int(max_chars * _TRANSCRIPT_HEAD_RATIO)
    tail_chars = int(max_chars * _TRANSCRIPT_TAIL_RATIO)
    return joined[:head_chars] + _TRANSCRIPT_TRUNCATE_MARKER + joined[-tail_chars:]
```

**保留行为**：

- `extract_quick_facts` 不调 `_render_transcript`（不调 LLM），行为不变
- ActionNode 渲染走 `action_summary` JSON 段，不受 head+tail 影响
- 仅影响阶段2 LLM 抽取的对话文本段

**测试**：

- 新增 `tests/memory/test_extractor.py::test_render_transcript_head_tail`
  - 验证短对话（< 8000）原样返回
  - 验证长对话（> 8000）保留头尾、marker 在中间
  - 验证 head_chars + tail_chars + marker 总长不超过 max_chars
  - 验证 user/assistant 文本过滤逻辑保持
  - 验证空 content 跳过

**回归保护**：

- 所有现有 extractor 单元测试必须继续通过
- LLM 抽取的 candidates 数量不应下降（反而可能上升，因为中段信息进入 prompt）

**风险**：

- prompt 长度不变（仍是 8000 字符），无 token 开销变化
- LLM 抽取的语义可能变化（看到开头任务定义 vs 看不到），需运行一次抽取回归

---

### WU-2：增加主题切换增量抽取 hook【优先级 2】

**目标**：长 session 不再等到 end 才一次性抽取。

**改动**：

**新增方法**：`MemoryExtractor.extract_incremental(self, session: Session, last_extracted_index: int) -> ExtractionResult`

```python
# extractor.py 新增（位于 _system_extract 附近）
def extract_incremental(
    self,
    session: Session,
    last_extracted_index: int,
) -> ExtractionResult:
    """从 last_extracted_index 之后的 messages 做增量抽取。

    设计要点:
    - 只对新增 turn 抽取,不重复扫历史
    - 复用 _system_extract / _apply_filters / _persist
    - 不调 LLM(增量场景下 LLM 太重); 只走阶段1+阶段3+阶段4 RULE
    - 30s 超时保护(参考 OpenAkita manager.py:1346)
    """
    if not session.messages:
        return ExtractionResult()
    new_messages = session.messages[last_extracted_index:]
    if not new_messages:
        return ExtractionResult()
    # 复用 quick_facts 路径,只取 RULE
    fragments = _collect_rule_signals(new_messages)
    if not fragments:
        return ExtractionResult()
    candidates = [
        LLMMemoryItem(content=f, type=_QUICK_FACT_TYPE) for f in fragments
    ]
    existing = self._load_existing_memories(candidates)
    filtered = self._apply_filters(
        LLMExtractionResult(memories=candidates),
        existing,
    )
    persisted = self._persist(filtered, session, source="topic_change")
    return ExtractionResult(
        memory_ids=persisted.memory_ids,
        episode_ids=persisted.episode_ids,
        skipped=len(candidates) - len(filtered.memories),
    )
```

**新增 source 枚举**：在 `_SOURCE_MAP`（extractor.py:110-115）增加：

```python
"topic_change": EpisodeSource.TOPIC_CHANGE,  # 新增枚举值
```

需要在 `nanobot/memory/models.py` 的 `EpisodeSource` 枚举里加 `TOPIC_CHANGE = "topic_change"`。

**接入点**：`nanobot/agent/hooks/memory_extraction.py`

新增 `_schedule_topic_change_extraction(context)` 方法，由 hook 工厂在 subject 变化时调用：

```python
# memory_extraction.py 新增（在 _schedule_run_extraction 附近）
def _schedule_topic_change_extraction(
    self, context: AgentRunHookContext, last_extracted_index: int
) -> None:
    task = asyncio.create_task(
        self._run_incremental_extraction(context.messages, last_extracted_index)
    )
    self._pending_tasks.add(task)
    task.add_done_callback(self._pending_tasks.discard)

async def _run_incremental_extraction(
    self, messages: list[dict], last_extracted_index: int
) -> None:
    session = Session(
        key=self._session_key,
        messages=[dict(m) for m in messages],
    )
    try:
        await asyncio.wait_for(
            self._extractor.extract_incremental(session, last_extracted_index),
            timeout=30.0,  # 参考 OpenAkita
        )
    except Exception:
        logger.warning(
            "incremental memory extraction failed for {}",
            self._session_key,
        )
```

**主题判定逻辑**：复用现有 `topic_prefilter.py`（仓库已有）。在 hook 的 `on_message` 入口判断当前 message 与上一条的 topic id，变化时触发增量抽取。

**测试**：

- `tests/memory/test_extractor.py::test_extract_incremental_only_new_messages`
- `tests/memory/test_extractor.py::test_extract_incremental_no_llm_called`
- 集成测试：`tests/memory/test_memory_extraction_hook.py::test_topic_change_triggers_incremental`

**风险**：

- 增加 topic 判定开销（已有 topic_prefilter，复用即可）
- 增加 episodes 数量（但都是 TOPIC_CHANGE 类型，可单独查询）

---

### WU-3：ActionNode 孤儿判定修复 + DB 失败 fallback【优先级 3】

**目标**：消除"output 为空也判成功"的潜在 bug；LLM 抽取失败不丢候选。

**改动 A：孤儿判定修复**（extractor.py:327）

当前代码：

```python
output = results.get(call_id, "")
nodes.append(
    ActionNode(
        tool=name,
        input=_truncate(_redact(args), ActionNode.INPUT_MAX_CHARS),
        output=_truncate(_redact(output), ActionNode.OUTPUT_MAX_CHARS),
        success=not _looks_like_error(output),  # ← 空字符串会让 success=True
    )
)
```

修复方案：增加"output 为空但能配对上时 success=False + warning 日志"。

```python
# 新增辅助函数
def _resolve_action_success(output: str, has_result: bool) -> bool:
    if not has_result:
        logger.warning(
            "action_node missing tool result, marking as failed: output_len=0"
        )
        return False
    return not _looks_like_error(output)

# 修改 _collect_action_nodes(extractor.py:322-329)
nodes.append(
    ActionNode(
        tool=name,
        input=_truncate(_redact(args), ActionNode.INPUT_MAX_CHARS),
        output=_truncate(_redact(output), ActionNode.OUTPUT_MAX_CHARS),
        success=_resolve_action_success(
            output, has_result=bool(output or call_id in results)
        ),
    )
)
```

**改动 B：DB 失败 fallback**（参考 OpenAkita manager.py:1879-1893）

在 `nanobot/memory/repository.py` 的写入路径增加 try/except：

```python
def safe_write_with_fallback(
    payload: dict, fallback_dir: Path
) -> None:
    """DB 写入失败时转存 JSON,下次启动重试。"""
    try:
        _do_write(payload)
    except Exception as exc:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        idx = payload.get("turn_index", 0)
        path = fallback_dir / f"{ts}_{idx}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        logger.warning(
            "memory write failed, saved to fallback {}: {}",
            path, exc,
        )
```

在 `extractor._persist` 里包装写入：

```python
# 伪代码
for memory_item in filtered.memories:
    try:
        memory_id = add_memory(conn, memory_item, ...)
    except Exception as exc:
        safe_write_with_fallback(
            {"kind": "memory", "item": memory_item.to_dict()},
            self.database.fallback_dir,
        )
        continue
```

**测试**：

- `test_orphan_action_node_marked_failed`
- `test_db_failure_writes_fallback_file`
- `test_fallback_file_replay_on_next_startup`

**风险**：

- fallback 文件需要重放机制，启动时扫描并尝试重写
- 已有 fallback 目录需在 `MemoryDatabase.__init__` 创建

---

### WU-4（可选）：`_build_prompt_messages` 暴露 transcript 截断参数【优先级 4】

**目标**：让测试与未来调优可以注入不同 head/tail 比例。

**改动**：`extractor.py:724-743` 的 `_build_prompt_messages` 接受可选参数：

```python
def _build_prompt_messages(
    self,
    prompt: str,
    session: Session,
    system: SystemExtractionResult,
    *,
    transcript_max_chars: int = 8000,
    transcript_head_ratio: float = 0.4,
) -> list[dict[str, Any]]:
    transcript = _render_transcript(
        session.messages,
        max_chars=transcript_max_chars,
        head_ratio=transcript_head_ratio,
    )
    ...
```

仅做参数化，不做行为变更默认值。

**测试**：补全 `_render_transcript` 已被参数化覆盖后，这里的集成测试。

---

## 4. 实施顺序与门禁

| 顺序 | WU | 门禁 |
|---|---|---|
| **1** | WU-1 `_render_transcript` head+tail | 单测全过 + 现有 extractor 单测回归 |
| **2** | WU-3A 孤儿判定修复 | 单测通过 |
| **3** | WU-2 主题切换增量抽取 | 单测 + 集成测试 + topic 切换触发链路验证 |
| **4** | WU-3B DB 失败 fallback | 单测 + 重放链路验证 |
| **5**（可选）| WU-4 参数化 | 单测 |

每个 WU 完成后落 `verifications/*-verification-lite.md` 摘要。

## 5. 不引入的 OpenAkita 特性（说明理由）

| OpenAkita 特性 | 是否引入 | 理由 |
|---|---|---|
| `smart_truncate` sidecar 文件 | ❌ | 依赖 LLM 工具调用循环，nanobot 抽取路径无该能力；head+tail 已覆盖80%场景 |
| 三轨道并发（Episode+Profile+Experience） | ❌ | nanobot 已有 semantic+episode 两路并发，再加一路会增加 LLM 成本与失败面 |
| 去重改为 evolve | ❌ | 复杂度高、单独立项；当前精确哈希 + N-Gram 在小规模足够 |
| MEMORY.md 容量治理 | ❌ | 未在 nanobot 范围；如未来需要，独立 plan |
| `enable_thinking=False` | ⚠️ 半引入 | 不强制改 runtime 配置，由调用方决定；记入决策文档 |

## 6. 验证清单（每个 WU 完成后跑）

```bash
# 单测
pytest tests/memory/test_extractor.py -v
pytest tests/memory/test_memory_extraction_hook.py -v
# 全量
pytest tests/ -q
# Lint
ruff check nanobot/memory/
# 类型
basedpyright nanobot/memory/extractor.py
```

## 7. 风险与回滚

| 风险 | 回滚方案 |
|---|---|
| head+tail 比例调错，LLM 抽取质量下降 | 保持旧实现可用（通过参数化），A/B 测试 |
| 主题切换触发过频，episode 数量爆炸 | 节流：同一 topic 30s 内只触发一次；上限：单 session 增量抽取不超过 N 次 |
| Fallback 文件堆积 | 增加 retention 策略，保留最近 7 天 |

## 8. 后续议题（独立 plan）

1. 去重改为 evolve 策略
2. 引入向量化召回到去重链路
3. Episode 摘要长度可配置
4. 抽取 LLM 调用预算控制（每日 token 限额）

---

## 附录 A：核心文件改动清单

| 文件 | 改动类型 | 估算行数 |
|---|---|---|
| `nanobot/memory/extractor.py` | 修改 `_render_transcript` + 新增 `extract_incremental` + 新增 source 常量 | +60 / -10 |
| `nanobot/memory/models.py` | `EpisodeSource` 新增 `TOPIC_CHANGE` 枚举值 | +1 |
| `nanobot/memory/repository.py` | 新增 `safe_write_with_fallback` | +30 |
| `nanobot/agent/hooks/memory_extraction.py` | 新增 `_schedule_topic_change_extraction` + `_run_incremental_extraction` | +40 |
| `tests/memory/test_extractor.py` | 新增 5 个测试用例 | +150 |
| `tests/memory/test_memory_extraction_hook.py` | 新增 1 个集成测试 | +60 |

**总计**：约 +340 / -10 行，跨 4 个生产文件 + 2 个测试文件。

---

## 附录 B：与 OpenAkita 的最终对照（优化后）

| 维度 | 优化前 nanobot | 优化后 nanobot | OpenAkita |
|---|---|---|---|
| 截断策略 | 纯 tail | **head + tail (40% + 45%)** | head + tail (65% + 22%) |
| 抽取窗口 | 全 session 受 8000 限制 | 全 session head+tail + 主题切换增量 | 最近 30 轮 + 主题切换增量 |
| 触发时机 | 1 个 | **3 个**（主题切换 + 压缩前 + session_end）| 4 个 |
| 孤儿 ActionNode | success=True 误判 | **success=False + warning** | 无此 bug |
| DB 失败 | 数据丢失 | **fallback JSON 重放** | 同 |
| 去重策略 | drop | drop（保持） | evolve |
| MEMORY.md 治理 | 无 | 无 | 三档 |

---

**请用户确认**：
1. WU-1 / WU-2 / WU-3 优先级排序是否同意
2. 是否接受 head_ratio=0.4（vs OpenAkita 0.65）
3. 是否需要 WU-4（参数化）
4. 主题切换触发增量抽取的节流策略：30s 内同 topic 只触发一次 是否合理
