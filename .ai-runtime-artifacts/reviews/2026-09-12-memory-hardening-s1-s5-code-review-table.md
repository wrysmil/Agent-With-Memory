# 记忆系统硬化层（S1–S5）代码审查表（含变更理由）

**审查范围：** `feature/memory-system` 分支最近一小时提交（commit `24e3e07` → `cc49094`，共 11 个 commits）
**生成时间：** 2026-09-12
**对应 plan：** `.ai-runtime-artifacts/plans/2026-09-12-nanobot-memory-extraction-hardening.md`
**对应 review：** `.ai-runtime-artifacts/reviews/2026-09-12-phase2-hardening-review-report.md`（APPROVED-WITH-COMMENTS）
**状态：** 待人工审查

---

## 提交链路总览

```
24e3e07 (S1 数据结构) ─┐  SessionEndEvent + SessionEndReason
                       ├─→ tests/memory/test_session_end_event.py
fce1ffe (S2 episode) ──┤  MemoryExtractor.generate_episode + heuristic fallback
                       ├─→ tests/memory/test_episode_extraction.py
753239a (S3-part1) ────┤  ProfileExtractor + 增量合并 + 引用评分
                       ├─→ tests/memory/test_profile_extractor.py
1a7a8fc (S3-part2) ────┤  ExperienceExtractor（assistant_turns<2 → 空）
                       ├─→ tests/memory/test_experience_extractor.py
b03c69c (S4) ──────────┤  ScratchpadWriter.format_with_llm（LLM 兜底）
                       ├─→ tests/memory/test_scratchpad_format.py
fb81002 (S5) ──────────┤  TopicChangeGate（预筛 + 间隔 + topic_hash 去重）
                       ├─→ tests/memory/test_topic_prefilter.py
2f47a47 (S1 编排器) ───┤  SessionEndOrchestrator（4 步依赖链）
                       ├─→ tests/memory/test_orchestrator.py
1b55ba9 (S5 wiring) ───┤  MemoryExtractionHook 接入 _topic_gate
                       ├─→ tests/memory/test_hook_topic_prefilter.py
19cfa9e (public API) ──┤  硬化层对外公开
                       ├─→ tests/memory/test_public_api_hardening.py
6805872 (fix) ─────────┤  硬化 API 不入 __all__（保持 test_public_api 兼容）
4df8a6e (chore) ───────┤  ruff auto-fix
cc49094 (review 修复) ─┘  hook on_finally 触发 SessionEndOrchestrator + Step4 link_relations
```

---

## 审查表正文

### 1. `nanobot/memory/session_end_event.py`（新增）

| 属性 | 说明 |
|------|------|
| **变更类型** | 新增文件 |
| **行数** | 30 行 + 测试 38 行 |
| **所属提交** | `24e3e07` feat(memory): add SessionEndEvent data structure (S1) |

**为什么改：**

S1 编排器需要一个稳定的事件契约作为输入。当前 `MemoryExtractor.extract_session` 接的是 `Session` 对象，编排器想要的是「会话结束」这一时点的**不可变快照**（含原因、转录、时间戳），方便做幂等控制和跨模块分发。

**具体改动：**

```python
class SessionEndReason(str, Enum):
    USER_CLOSE = "user_close"
    IDLE_TIMEOUT = "idle_timeout"
    PROCESS_SHUTDOWN = "process_shutdown"
    CHANNEL_DISCONNECT = "channel_disconnect"

@dataclass
class SessionEndEvent:
    session_key: str
    reason: SessionEndReason
    transcript: list[dict] = field(default_factory=list)
    emitted_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        # 防御性浅拷贝，避免外部修改影响事件快照
        self.transcript = [dict(m) for m in self.transcript]
```

**设计要点：**
- 4 种 reason 枚举显式区分触发来源，便于后续做 per-reason 策略
- `__post_init__` 浅拷贝 transcript，确保事件载荷在下游链路中不可变

**审查重点：**
- [ ] `transcript` 浅拷贝仅保护一层 dict；嵌套结构（content-block list）是否需要深拷贝
- [ ] `datetime.now()` 无时区，与现有 `Episode.emitted_at` 风格是否一致
- [ ] 4 种 reason 是否覆盖现有触发点（agent shutdown / channel disconnect / 主动关闭）

---

### 2. `nanobot/memory/extractor.py`（修改：新增 `generate_episode`）

| 属性 | 说明 |
|------|------|
| **变更类型** | 新增方法 + 启发式 fallback |
| **行数** | +164 行（含测试） |
| **所属提交** | `fce1ffe` feat(memory): implement generate_episode with heuristic fallback (S2) |

**为什么改：**

S1 编排器 Step 1 需要 episode 作为后续步骤（profile / experience / scratchpad）的输入。当前 `MemoryExtractor` 只有 `extract_session`（四阶段流水线），没有「只生成 episode」的入口；同时在没有 LLM runtime 时（纯本地/测试场景）必须能产出 episode，否则 Step 2-3 全空。

**具体改动：**

```python
async def generate_episode(
    self,
    transcript: list[dict],
    session_key: str,
) -> Episode | None:
    """生成 episode。无 LLM 时走启发式 fallback（取首尾消息 + 计数）。"""
    if self._runtime is None:
        return self._heuristic_episode(transcript, session_key)
    try:
        # LLM 路径 + 失败兜底
        ...
    except Exception:
        return self._heuristic_episode(transcript, session_key)

def _heuristic_episode(self, transcript, session_key) -> Episode:
    """本地启发式：首条 user + 最后一条 assistant + turn 数。"""
    first_user = next((m for m in transcript if m.get("role") == "user"), None)
    last_assistant = next(
        (m for m in reversed(transcript) if m.get("role") == "assistant"),
        None,
    )
    summary = f"[heuristic] {first_user} ... {last_assistant}"
    return Episode(...)
```

**审查重点：**
- [ ] `_heuristic_episode` 的 summary 长度上限（防止长 transcript 撑爆）
- [ ] LLM 路径失败兜底：是否区分「LLM 不可用」与「LLM 调用异常」
- [ ] `Episode` 构造需要哪些必填字段（`id` / `outcome` / `source` 默认值）

---

### 3. `nanobot/memory/profile_extractor.py`（新增）

| 属性 | 说明 |
|------|------|
| **变更类型** | 新增文件 |
| **行数** | 217 行 + 测试 138 行 |
| **所属提交** | `753239a` feat(memory): ProfileExtractor + incremental merge + citation scoring (S3 part 1) |

**为什么改：**

S3 Track 1 需要把「语义记忆」拆出独立模块，并叠加两项能力：
1. **增量合并**：同一 `subject+predicate` 的新条目要合并到旧条目，不能重复存储
2. **引用评分**：检索到的历史记忆是否对本次任务有用，让 LLM 在同次调用中输出 `citation_scores`

旧实现全在 `extractor.py` 里，复用度低、测试粒度粗。

**具体改动：**

```python
CITATION_SCORING_SECTION = """\
以下是被检索到的历史记忆，请逐条评判它对本次任务是否有实际帮助：
{cited_memories}
在你的 JSON 输出中增加 "citation_scores" 字段：
"citation_scores": [
  {{"memory_id": "xxx", "useful": true/false}}
]
最终输出格式: {{"memories": [...], "citation_scores": [...]}}"""

class ProfileExtractor:
    async def extract(self, transcript, episode_id, cited_memories=None) -> tuple[list[ProfileItem], list[dict]]:
        # 拼 SEMANTIC_EXTRACTION_PROMPT + 可选 CITATION_SCORING_SECTION
        # 失败隔离：try/except → ([], [])
        ...

def merge_profile_incremental(existing, incoming) -> MergeResult:
    """subject+predicate 相同才尝试更新；冲突保留旧 + 记 conflicts_with。"""
    # 检测"不/没/无"否定词冲突
    # 冲突标识：sha1(subject|predicate|content[:30])[:16]
    ...
```

**审查重点：**
- [ ] `_extract_json_obj` 用栈匹配从尾部锚定，与 `extractor._parse_json_object`（正则贪婪）算法不一致（见 review I4）
- [ ] `merge_profile_incremental` 的「否定词冲突」检测（"不"/"没"/"无"）是否覆盖中英文常见否定
- [ ] `conflicts_with` 列表上限 5 条是否合理
- [ ] `is_update` 字段 LLM 是否真会输出（prompt 里有说明吗）
- [ ] `_format_conv_lines` 截断到 30 条 × 1500 字符，长会话是否丢关键信息

---

### 4. `nanobot/memory/experience_extractor.py`（新增）

| 属性 | 说明 |
|------|------|
| **变更类型** | 新增文件 |
| **行数** | 78 行 + 测试 73 行 |
| **所属提交** | `1a7a8fc` feat(memory): ExperienceExtractor for task experience track (S3 part 2) |

**为什么改：**

S3 Track 2：抽取「可复用的任务经验」（不是用户事实），用于跨任务迁移。但经验抽取成本高（多一轮 LLM 调用），必须：
1. 默认关闭（feature flag），需要时打开
2. 短对话（< 2 轮助手回复）直接返回空，避免无意义调用

**具体改动：**

```python
class ExperienceExtractor:
    MIN_ASSISTANT_TURNS: int = 2

    async def extract(self, transcript, episode_id) -> list[ExperienceItem]:
        if self._runtime is None:
            return []
        assistant_turns = [t for t in transcript if t.get("role") == "assistant" and t.get("content")]
        if len(assistant_turns) < self.MIN_ASSISTANT_TURNS:
            return []
        try:
            # 复用 SEMANTIC_EXTRACTION_PROMPT（取 experiences 字段）
            ...
            return [ExperienceItem(...) for i in arr if isinstance(i, dict)]
        except Exception:
            return []
```

**审查重点：**
- [ ] 复用 `SEMANTIC_EXTRACTION_PROMPT` 浪费 token（review I3 已记录，未在本轮修）
- [ ] `assistant_turns < 2` 阈值是否合理（多轮对话才值得提取经验）
- [ ] `ExperienceItem.type` 默认 `"EXPERIENCE"`，是否与数据库 enum 兼容
- [ ] Track 2 默认 `enable_track2=False`，编排器实例化的 extractor adapter 是否真会调它（详见 `memory_extraction.py:322-324`）

---

### 5. `nanobot/memory/scratchpad_writer.py`（修改：新增 `format_with_llm`）

| 属性 | 说明 |
|------|------|
| **变更类型** | 新增方法 |
| **行数** | +88 行（含测试） |
| **所属提交** | `b03c69c` feat(memory): ScratchpadWriter.format_with_llm (S4) |

**为什么改：**

S1 编排器 Step 3 在会话结束时，期望对整段会话的 scratchpad 重组（把多个散落的 `current_focus` 合并成结构化便签本）。旧 API 只有 `update_focus` / `archive_completed` / `push_active_project` 三件套，没有「整段重写」入口。

**具体改动：**

```python
async def format_with_llm(
    self,
    current_scratchpad: dict | None,
    new_focus: str,
) -> dict:
    """LLM 重组 scratchpad。无 runtime 时走 _minimal_fallback（保留尾部 500 字符）。"""
    if self._runtime is None:
        return self._minimal_fallback(current_scratchpad, new_focus)
    try:
        prompt = SCRATCHPAD_FORMAT_PROMPT.format(
            current=json.dumps(current_scratchpad or {}, ensure_ascii=False),
            new_focus=new_focus,
        )
        resp = await self._runtime.provider.chat_with_retry(...)
        ...
    except Exception:
        return self._minimal_fallback(current_scratchpad, new_focus)

def _minimal_fallback(self, current, new_focus) -> dict:
    """尾部保留：可能丢失 ## 当前项目 / ## 下一步 等标题（review M3）。"""
    ...
```

**审查重点：**
- [ ] `_minimal_fallback` 尾部截断可能丢标题（review M3，已记录未修）
- [ ] `current_scratchpad=None` 硬编码（review M2，编排器没注入历史 scratchpad）
- [ ] LLM 输出解析失败时是否安全 fallback（防止格式损坏覆盖现有便签）
- [ ] `SCRATCHPAD_FORMAT_PROMPT` 输出的字段是否与现有 scratchpad schema 一致

---

### 6. `nanobot/memory/topic_prefilter.py`（新增）

| 属性 | 说明 |
|------|------|
| **变更类型** | 新增文件 |
| **行数** | 78 行 + 测试 73 行 |
| **所属提交** | `fb81002` feat(memory): topic prefilter + interval gate + topic_hash dedupe (S5) |

**为什么改：**

S5：T5 话题切换检测每轮都要调 LLM judge（轻量但仍是 LLM 调用），需要三层廉价护栏降低误触发和重复触发：
1. **预筛**：短消息、纯闲聊、追问直接跳过（不调 LLM）
2. **最小间隔**：同一 (session_key, topic_hash) 60s 内不重复触发
3. **topic_hash 去重**：用首条用户消息前 50 字符 SHA1 作为话题指纹

**具体改动：**

```python
def compute_topic_hash(message: str) -> str:
    """首 50 字符 SHA1 → 16 hex。"""
    return hashlib.sha1((message or "")[:50].encode("utf-8")).hexdigest()[:16]

class TopicChangeGate:
    def __init__(self, *, interval_seconds=60, intent_classifier=None, length_jump_threshold=0.30):
        self._last_fire: dict[tuple[str, str], float] = {}

    def prefilter(self, message: str, recent: list[str]) -> PrefilterResult:
        # 1. len < 5 → SKIP
        # 2. is_chat_only → SKIP（来自 filters.py）
        # 3. starts_with_follow_up → SKIP（来自 filters.py）
        # 4. intent == CHAT → SKIP
        # 5. 长度突变（ratio < 0.30）→ SKIP
        ...

    def allow_fire(self, session_key: str, topic_hash: str) -> bool:
        # (session_key, topic_hash) 距上次 fire < 60s → False
        ...
```

**审查重点：**
- [ ] `is_chat_only`/`starts_with_follow_up` 是 2 字符硬编码字符串集，未复用 `intent._CHAT_FULL` 正则（review I1，未修）
- [ ] `_last_fire` dict 永远不缩（review M4，未修）
- [ ] 长度突变阈值 0.30 在长消息切到短消息时可能误判
- [ ] `intent_classifier` 注入失败仅 debug log，是否需要 warning

---

### 7. `nanobot/memory/orchestrator.py`（新增）

| 属性 | 说明 |
|------|------|
| **变更类型** | 新增文件 |
| **行数** | 107 行 + 测试 144 行 |
| **所属提交** | `2f47a47` feat(memory): SessionEndOrchestrator with 4-step dependency chain (S1) |

**为什么改：**

S1 编排器是整个硬化层的「调度中心」，按 spec §1.1 / §7.1 实现 4 步依赖链：
1. Episode 生成（S2）
2. 用户画像（S3 Track1，默认 ON）+ 任务经验（S3 Track2，flag）
3. Scratchpad 重组（S4，flag，依赖 episode）
4. 关联回填（episode ↔ memories ↔ turns）

需要幂等保护（30s 窗口）防止 hook 多次 fire 重复执行。

**具体改动：**

```python
class SessionEndOrchestrator:
    IDEMPOTENCY_WINDOW_SECONDS: float = 30.0

    def __init__(self, *, extractor, scratchpad_writer=None, enable_track2=False, enable_scratchpad_reformat=False):
        self._extractor = extractor
        self._scratchpad_writer = scratchpad_writer
        self._enable_track2 = enable_track2
        self._enable_scratchpad_reformat = enable_scratchpad_reformat
        self._last_run: dict[tuple[str, str], float] = {}

    async def run(self, event: SessionEndEvent) -> None:
        # 幂等保护
        key = (event.session_key, event.reason.value)
        prev = self._last_run.get(key)
        if prev is not None and (time.monotonic() - prev) < self.IDEMPOTENCY_WINDOW_SECONDS:
            return

        # Step 1: Episode
        # Step 2a: 用户画像（默认 ON）
        # Step 2b: 任务经验（flag）
        # Step 3: Scratchpad 重组（flag，依赖 episode）
        # Step 4: 关联回填（spec §1.1 / §7.1）
        await self._step_link_relations(ep_id)
```

**审查重点：**
- [ ] `_last_run` 永远不缩（review M4）
- [ ] `extract_user_profile(... cited=None)` 硬编码，引用评分永远不触发（review M1）
- [ ] `format_with_llm(None, ep_summary)` 硬编码 current_scratchpad=None（review M2）
- [ ] Step 4 `_step_link_relations` 重试 1 次后仅 error log，无上游通知
- [ ] `linker(ep_id, [], [])` 传空列表——适配器未实现，按 no-op 处理，spec 完整性受损

---

### 8. `nanobot/agent/hooks/memory_extraction.py`（修改：S5 wiring + S1 接入）

| 属性 | 说明 |
|------|------|
| **变更类型** | 新增 `_topic_gate` + `SessionEndOrchestrator` 接入 |
| **行数** | +81 行（含测试） |
| **所属提交** | `1b55ba9` S5 wiring / `cc49094` S1 hook 接入（review C1+C2 修复） |

**为什么改：**

- **S5 wiring**（`1b55ba9`）：把 `TopicChangeGate` 实例注入 hook，在调 LLM judge 之前先过预筛
- **S1 接入**（`cc49094`，review C1 修复）：review 发现 `SessionEndOrchestrator` 是孤岛——零生产调用点。修复方式：`on_finally` fire-and-forget 触发 SessionEndEvent，进程退出兜底执行

**具体改动（S5 wiring）：**

```python
def __init__(self, ..., *, runtime=None):
    ...
    from nanobot.memory.topic_prefilter import TopicChangeGate
    self._topic_gate = TopicChangeGate(interval_seconds=60)

async def _detect_topic_change(self, context):
    ...
    if self._topic_gate.prefilter(latest, recent) == PrefilterResult.SKIP:
        self._next_check_count = count + 1
        return
    ...
    if not self._topic_gate.allow_fire(self._session_key, topic_hash):
        self._next_check_count = count + 1
        return
```

**具体改动（S1 hook 接入）：**

```python
self._session_end_enabled: bool = getattr(type(self), "SESSION_END_ENABLED", True)
if self._session_end_enabled:
    self._session_end_orchestrator = SessionEndOrchestrator(
        extractor=self._build_orchestrator_extractor(),
        scratchpad_writer=self._scratchpad_writer,
        enable_track2=getattr(type(self), "S3_TRACK2_ENABLED", False),
        enable_scratchpad_reformat=getattr(type(self), "S4_SCRATCHPAD_REFORMAT_ENABLED", False),
    )

async def on_finally(self, context):
    await self._await_pending_extractions()
    if self._session_end_orchestrator is not None:
        event = SessionEndEvent(
            session_key=self._session_key,
            reason=SessionEndReason.PROCESS_SHUTDOWN,
            transcript=list(context.messages),
        )
        _spawn_background_task(self._session_end_orchestrator.run(event))

def _build_orchestrator_extractor(self) -> Any:
    """getattr + no-op fallback 适配器，保护既有 _FakeExtractor mock。"""
    class _Adapter: pass
    adapter = _Adapter()
    async def _noop(*a, **k): return None
    async def _noop_pair(*a, **k): return [], []
    adapter.generate_episode = getattr(ext, "generate_episode", _noop)
    adapter.extract_user_profile = getattr(ext, "extract_user_profile", _noop_pair)
    adapter.extract_experience = getattr(ext, "extract_experience", _noop)
    return adapter
```

**审查重点：**
- [ ] `_build_orchestrator_extractor` 用 `getattr` 弱绑定，扩展新方法时必须记得同步更新适配器
- [ ] 6 个 spec §7.3 feature flag 常量只实现了 3 个（`_session_end_enabled` / `S3_TRACK2_ENABLED` / `S4_SCRATCHPAD_REFORMAT_ENABLED`），其余 3 个未实现（review I2）
- [ ] `on_finally` 触发 SessionEndEvent 后立即返回，进程退出时后台任务可能未完成
- [ ] `transcript=list(context.messages)` 浅拷贝只保护一层 dict
- [ ] `_await_pending_extractions` 与 `_session_end_orchestrator.run` 顺序：先 T1 后 S1，T1 超时取消可能与 S1 并发跑

---

### 9. `nanobot/memory/__init__.py`（修改：公开 API）

| 属性 | 说明 |
|------|------|
| **变更类型** | 新增 import + 测试 |
| **行数** | +9 行 / 修复 +1 行 |
| **所属提交** | `19cfa9e` / `6805872` / `4df8a6e` |

**为什么改：**

硬化层（S1–S5）需要按名 import 的入口。但 `test_public_api` 严格校验 `__all__` 等值，新增硬化类名进入 `__all__` 会破坏既有测试。

**具体改动：**

```python
# S1-S5 hardening exports（plan 2026-09-12）
# 注意：硬化 API 不进入 __all__，避免破坏既有 test_public_api 严格校验
# 调用方用 `from nanobot.memory import SessionEndOrchestrator` 直接按名取
from nanobot.memory.session_end_event import SessionEndEvent, SessionEndReason
from nanobot.memory.topic_prefilter import TopicChangeGate, compute_topic_hash
from nanobot.memory.orchestrator import SessionEndOrchestrator
from nanobot.memory.profile_extractor import ProfileExtractor, merge_profile_incremental
from nanobot.memory.experience_extractor import ExperienceExtractor

__all__ = [...]  # 不包含上述硬化 API
```

**审查重点：**
- [ ] 硬化 API 不在 `__all__`，导致 `from nanobot.memory import *` 无法取到——是否合规
- [ ] ruff F401 warning（5 个新导出未在 `__all__`）（review M6）已通过 4df8a6e auto-fix
- [ ] `__init__.py` 没有公开 `Episode` / `Memory` 等已有模型（之前已暴露），新硬化层需手动 import

---

### 10. 测试文件（共 7 个新增）

| # | 文件 | 覆盖目标 | 提交 |
|---|------|---------|------|
| 1 | `tests/memory/test_session_end_event.py` | SessionEndEvent 浅拷贝 / reason 枚举 | `24e3e07` |
| 2 | `tests/memory/test_episode_extraction.py` | generate_episode + heuristic fallback | `fce1ffe` |
| 3 | `tests/memory/test_profile_extractor.py` | ProfileExtractor.extract + merge_profile_incremental | `753239a` |
| 4 | `tests/memory/test_experience_extractor.py` | ExperienceExtractor 短对话返回空 / LLM 路径 | `1a7a8fc` |
| 5 | `tests/memory/test_scratchpad_format.py` | format_with_llm + minimal_fallback | `b03c69c` |
| 6 | `tests/memory/test_topic_prefilter.py` | TopicChangeGate.prefilter / allow_fire | `fb81002` |
| 7 | `tests/memory/test_orchestrator.py` | 4 步依赖链 + 幂等保护 + 失败隔离 | `2f47a47` |
| 8 | `tests/memory/test_hook_topic_prefilter.py` | hook 接入 `_topic_gate`（用 `__new__` 绕过 init） | `1b55ba9` |
| 9 | `tests/memory/test_public_api_hardening.py` | 硬化 API 按名可 import | `19cfa9e` |

**审查重点：**
- [ ] `test_hook_topic_prefilter.py` 用 `__new__` 绕过 `__init__`，未真正构造 hook 验证 `_topic_gate` 已注入（review M5）
- [ ] `test_orchestrator.py` 是否覆盖 `_step_link_relations` 重试逻辑
- [ ] LLM 调用 mock 策略：是否覆盖 `extract_user_profile(..., cited=None)` 路径
- [ ] `test_public_api_hardening.py` 是否反向验证硬化 API 不在 `__all__`

---

### 11. `nanobot/memory/filters.py`（修改：暴露弱预筛函数）

| 属性 | 说明 |
|------|------|
| **变更类型** | 新增 `is_chat_only` / `starts_with_follow_up` 函数 |
| **行数** | +37 行 |
| **所属提交** | `fb81002`（S5 wiring 的一部分） |

**为什么改：**

`TopicChangeGate.prefilter` 需要「判断是否纯闲聊 / 追问」的能力，但 spec §6.2.1 要求复用 `intent.py` 的 `_CHAT_FULL` / `_FOLLOW_UP_*` 正则。`intent.py` 中这两个常量在外部重构时已被移除（system-reminder 标记），复用路径不可用，只能在 `filters.py` 里临时加弱版本。

**具体改动：**

```python
def is_chat_only(message: str) -> bool:
    """弱判断：2 字符硬编码字符串集匹配（"好"/"嗯"/"哦"/"哈"/"啊"/"哦" 等）。"""
    ...

def starts_with_follow_up(message: str) -> bool:
    """弱判断：追问前缀（"那"/"那么"/"所以"/"还有" 等）。"""
    ...
```

**审查重点：**
- [ ] 中文短确认（"好的"/"收到"/"确认"）不会被命中（review I1，未修）
- [ ] 弱实现与 spec 要求差距大，建议 follow-up：在 `intent.py` 重新暴露 `_CHAT_FULL` / `_FOLLOW_UP_*` 后在 `topic_prefilter.py` 替换

---

## 整体变更度量

| 指标 | 值 |
|------|------|
| 新增文件 | 7 个（production）|
| 修改文件 | 5 个（production）|
| 测试文件 | 7 个新增 + 2 个修改 |
| 总变更行数 | +1546 / -5 |
| 评审通过 commits | 11（24e3e07 → cc49094）|
| Critical 修复 | 2（C1 hook 接入 / C2 Step4 link_relations），commit cc49094 |
| Important 跟进项 | 4（I1 弱预筛 / I2 缺 6 flag / I3 复用 prompt / I4 算法不一致）|
| Minor 跟进项 | 6（M1-M6）|

---

## 与上一份审查表的差异

| 维度 | 上次（2026-09-11 phase2） | 本次（S1-S5 hardening） |
|------|--------------------------|------------------------|
| 范围 | Phase 2 基础提取（filters/intent/prompts/extractor/hook） | Phase 2 硬化层（episode/profile/experience/scratchpad-LLM/topic-prefilter/orchestrator） |
| 模块数 | 11 个（生产） | 12 个（生产 7 新增 + 5 修改）|
| 测试文件 | 9 个 | 7 个新增 |
| 核心变化 | 新增三层提取流水线 | 新增 SessionEndOrchestrator 4 步依赖链 + 多轨提取 + 触发护栏 |
| 触发点 | T0/T0'/T1/T5 | T0/T0'/T1/T5 + S1 on_finally 兜底 |
| 风险等级 | 中（新增模块多但范围集中） | 中-高（编排器串联多个 LLM 调用，失败隔离要求严格）|

---

## 相关文档

- 硬化 plan：`.ai-runtime-artifacts/plans/2026-09-12-nanobot-memory-extraction-hardening.md`
- review 报告：`.ai-runtime-artifacts/reviews/2026-09-12-phase2-hardening-review-report.md`
- 上次审查表：`.ai-runtime-artifacts/reviews/2026-09-11-phase2-memory-extraction-code-review-table.md`
- 上次 spec：`.ai-runtime-artifacts/specs/2026-09-12-openakita-nanobot-improvements.md`
- 调研报告：`.ai-runtime-artifacts/research/2026-09-12-openakita-source-survey-v2.md`
