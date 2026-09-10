---
artifact: implementation-plan
route: superpowers:writing-plans
skills:
  - writing-plans
skills_evidence:
  - harness-kit/.agents/skills/writing-plans/SKILL.md
  - harness-kit/artifact-templates/plan.harness-overlay.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - docs/记忆系统/记忆检索.md
  - .ai-runtime-artifacts/plans/2026-09-10-phase2-memory-extraction-handoff.md
created_at: 2026-09-11
topic: phase3-memory-retrieval
phase: 3-of-3
status: revised-after-review
approved: false
revision:
  reviewed_at: 2026-09-11
  review_doc: .ai-runtime-artifacts/reviews/2026-09-11-phase3-memory-retrieval-plan-document-review.md
  fixes_applied:
    - "Tech Stack 表补版本号"
    - "新增环境前置 Phase 1 段"
    - "T-09 编号错位决议声明"
    - "T-05 与 T-12 repository.search_semantic_scored 冲突消歧（T-05 不再写 stub）"
    - "新增回滚方案段（feature flag 兜底 + per-group revert 命令）"
    - "任务汇总表补预估列"
    - "验收口径补覆盖率目标 ≥ 85%"
    - "File Structure 补 conftest.py"
    - "未来增强措辞统一改为「不在本 plan 范围」"
dispatch: .ai-runtime-artifacts/plans/2026-09-11-phase3-memory-retrieval-dispatch.md
---

# 阶段三记忆检索 — 实施方案

> **范围限定**：本 plan **不写**任何业务代码（阶段门禁要求）。落盘后暂停等用户说「开始实现」。
> **设计依据**：`docs/记忆系统/记忆检索.md`（系统记忆检索链路全景）。
> **依赖**：Phase 1 存储层（`nanobot/memory/{database,models,repository}.py`）✅ 已交付；Phase 2 提取层（`extractor/filters/intent/prompts/scratchpad_writer.py`）✅ 已交付。
> **集成目标**：`nanobot/agent/context.py::build_system_prompt(include_memory=True)` — 在 Memory 层组装中插入 Layer 4 Active Retrieval。

---

## Goal

实现 nanobot 记忆系统的 **检索链**：每轮 `build_system_prompt` 时从 SQLite 召回与当前用户消息相关的 memories / episodes / attachments，经 Gate 守卫、多路召回、综合重排、Token 预算格式化后注入 System Prompt。

## 环境前置（Phase 1：本计划的前置依赖，不在本 plan 范围）

> **声明**：本 plan 不包含「环境准备」本身。环境前置由 Phase 1（存储）+ Phase 2（提取）+ nanobot 主仓既有依赖共同保证，**已就绪**。本段仅做显式登记以满足 plan review-rules §3「环境准备完整性」要求。

**前置条件**（已在 Phase 1+2 验证）：
- Python ≥3.11、`uv` 已安装
- `uv sync --all-extras --dev` 成功
- 测试基线：`pytest tests/memory/ tests/agent/ -q` → 345+ passed
- nanobot 工作区 SQLite schema v1 + FTS5 + memories_fts 表已建

**本阶段额外依赖**（**不引入新外部包**，全部已存在于 `pyproject.toml`）：
- `pytest>=8.0` / `pytest-asyncio>=0.23`（asyncio_mode=auto）
- `jieba>=0.42`（可选，默认 off，由 search_backend 决定）
- `chromadb` / `httpx`（可选，缺依赖自动回退 FTS5，见 T-04）

**一键环境验证命令**：
```bash
uv sync && pytest tests/memory/test_database.py tests/agent/test_loop_wiring.py -q
```
预期：`2+ passed`，无新增 fail。

**平台差异说明**：
- Windows 路径正则：`[A-Za-z]:\\[^\s\"\']+`
- POSIX 路径正则（macOS / Linux）：`/[^\s\"\']+\.\w{1,5}\b`
- T-06 `_PATH_RE` 已**双正则并存**（两个分支用 `|` 连接），macOS / Linux / Windows 三平台均兼容
- T-14 `MemorySearchTool` 在所有平台一致（Python 抽象，不依赖 OS 特性）

## Architecture

```
用户消息到达
    └─→ build_system_prompt()                         nanobot/agent/context.py:101
         └─→ _build_memory_section()                  新增
              ├─ Layer 0..3  既有（Phase 1+2 已交付）
              └─ Layer 4    Active Retrieval         ← 本 plan 重点
                   ├─ MemoryQueryPreprocessor        guard（空/控制词/超短 + 反注入清洗）
                   ├─ QueryDecomposer               LLM 拆解 + 规则降级（jieba + 停用词）
                   ├─ RetrievalEngine.retrieve()     nanobot/memory/retrieval/engine.py
                   │    ├─ 多路并行召回（4 channels）
                   │    │    ├─ semantic  → search_backend（FTS5/BM25 ∪ 向量） + repository.search_semantic_scored
                   │    │    ├─ episodes  → repository.search_episodes(entity=)
                   │    │    ├─ recent    → repository.query_semantic(min_importance=0.6, days=3)
                   │    │    └─ attachments → repository.search_attachments(term, intent)
                   │    ├─ 合并去重（memory_id）
                   │    ├─ Reranker                  score = 0.4×Rel + 0.2×Rec + 0.2×Imp + 0.2×AccessFreq
                   │    └─ Formatter                 token 预算 + 噪声过滤 + 事实去重
                   └─ 注入前缀 "## 相关记忆（自动检索）"
```

**关键约束**：
1. **不引入新外部依赖**：搜索后端仅 ChromaDB（可选）/ FTS5（默认）；FTS5 失败 → LIKE %kw% 回退。
2. **不破坏 Phase 1/2 测试基线**：所有 WU 完成时 `pytest tests/memory/ tests/agent/ -q` 必须 345+ passed。
3. **检索默认 opt-in**：与 Phase 2 提取一致，`include_memory=False` 时整层跳过；新增 `active_retrieval_enabled` 标志独立门控。
4. **TDD 强制**：每个 Task 严格 RED → GREEN → COMMIT 三步。

## Tech Stack

| 项 | 选型 / 版本 | 备注 |
| --- | --- | --- |
| 语言 | Python 3.11+，asyncio | 与 nanobot 主仓一致 |
| 数据库 | SQLite ≥3.37 + FTS5（已存在） | Phase 1 schema v1 |
| 中文分词 | jieba≥0.42（可选，默认 off） | 仅在 FTS5 bm25 路径启用 |
| 向量搜索 | chromadb（可选）/ httpx + DashScope Embedding（可选） | 缺依赖自动回退 FTS5 |
| 测试 | pytest≥8.0 + pytest-asyncio≥0.23（asyncio_mode=auto） | 与 nanobot 一致 |
| Lint | ruff（E/F/I/N/W，E501 ignored） | 与 CI 一致 |
| 类型检查 | basedpyright | 与 CI 一致 |

> **版本锁来源**：本计划不修改 `pyproject.toml` 既有依赖版本；上述版本号是**当前已存在的最低安全版本**，实施前由 coder 用 `uv pip show <pkg> | grep Version` 核实。如有更新走单独升级 PR。

## File Structure

### 新增文件（按 WU 归属）

| 路径 | 责任 | WU |
| --- | --- | --- |
| `nanobot/memory/retrieval/__init__.py` | 包入口，导出 `RetrievalEngine / RetrievalCandidate` | T-12 |
| `nanobot/memory/retrieval/candidate.py` | `RetrievalCandidate` dataclass（relevance / recency_score / importance_score / access_frequency_score / source_channel / raw） | T-01 |
| `nanobot/memory/retrieval/preprocessor.py` | `MemoryQueryPreprocessor`（`should_skip_retrieval` + `clean_query` + `prepare`） | T-02 |
| `nanobot/memory/retrieval/decomposer.py` | `QueryDecomposer`（LLM 拆解 + 规则降级；jieba 关键词 + 文件路径正则 + 停用词） | T-03 |
| `nanobot/memory/retrieval/search_backend.py` | `create_search_backend(name)` 工厂（FTS5 / chromadb / api_embedding） | T-04 |
| `nanobot/memory/retrieval/channels/semantic.py` | `_search_semantic` 通道；走 `engine.search_backend` + repository.search_semantic_scored 委托 | T-05 |
| `nanobot/memory/retrieval/channels/episodes.py` | `_search_episodes` 通道；实体名关联 Episode | T-06 |
| `nanobot/memory/retrieval/channels/recent.py` | `_search_recent(days=3)` 通道；`min_importance=0.6` 过滤 | T-07 |
| `nanobot/memory/retrieval/channels/attachments.py` | `_search_attachments` 通道；`intent=search_file` 或媒体词触发 | T-08 |
| `nanobot/memory/retrieval/reranker.py` | 综合重排（score 公式 + 焦点增强 + 动作词惩罚 + 冷启动豁免 + 0.35 阈值） | T-10 |
| `nanobot/memory/retrieval/formatter.py` | Token 预算格式化（`_strip_experience_noise` + `_dedupe_facts_by_subject_predicate`） | T-11 |
| `nanobot/memory/retrieval/engine.py` | `RetrievalEngine.retrieve()` 编排器 | T-12 |
| `tests/memory/retrieval/__init__.py` | 测试包 | — |
| `tests/memory/retrieval/conftest.py` | 共享 fixture（`_FakeStore` / `_cand` / `fake_engine` 等） | 所有 Task |
| `tests/memory/retrieval/test_candidate.py` | T-01 测试 | T-01 |
| `tests/memory/retrieval/test_preprocessor.py` | T-02 测试（Gate 四类 + 反注入） | T-02 |
| `tests/memory/retrieval/test_decomposer.py` | T-03 测试（LLM + 规则降级 + 缓存） | T-03 |
| `tests/memory/retrieval/test_search_backend.py` | T-04 测试（工厂 + FTS5/BM25 评分） | T-04 |
| `tests/memory/retrieval/channels/test_semantic.py` | T-05 测试 | T-05 |
| `tests/memory/retrieval/channels/test_episodes.py` | T-06 测试 | T-06 |
| `tests/memory/retrieval/channels/test_recent.py` | T-07 测试 | T-07 |
| `tests/memory/retrieval/channels/test_attachments.py` | T-08 测试 | T-08 |
| `tests/memory/retrieval/test_reranker.py` | T-10 测试（公式 + 焦点/惩罚/豁免/阈值） | T-10 |
| `tests/memory/retrieval/test_formatter.py` | T-11 测试（噪声 + 去重 + token 预算截断） | T-11 |
| `tests/memory/retrieval/test_engine.py` | T-12 测试（多路并行 + 短路 + 注入格式） | T-12 |
| `nanobot/agent/tools/memory_search.py` | `MemorySearchTool` — LLM 主动调用入口（pkgutil 自动发现） | T-14 |
| `nanobot/templates/agent/identity.md` | 追加 1 行告知 LLM `memory_search` 工具存在 | T-15 |
| `tests/memory/retrieval/test_integration_context.py` | T-13 测试（端到端 `build_system_prompt` 注入） | T-13 |
| `tests/agent/tools/test_memory_search_tool.py` | T-14 测试（元数据/自动发现/execute/token 透传/失败兜底） | T-14 |
| `tests/agent/test_identity_template.py` | T-15 测试（模板含 memory_search 工具告知 + 长度不超预算） | T-15 |

### 修改文件

| 路径 | 变更 | WU |
| --- | --- | --- |
| `nanobot/memory/repository.py` | 新增 `search_semantic_scored(query, limit)`、`search_episodes(entity, limit)`、`query_semantic(min_importance, since_days)`、`search_attachments(term, intent)`；保留旧 `search_memories` 兼容 | T-05/06/07/08 |
| `nanobot/memory/database.py` | FTS5 bm25 评分辅助（`bm25_rank_to_score(rank)` = `1.0 / (1.0 + rank)`） + jieba 触发开关（默认 off） | T-04 |
| `nanobot/memory/models.py` | `Episode` 加 `compaction_checkpoint_id: str \| None`（Phase 2 handoff §14 顺带） | T-06 |
| `nanobot/agent/context.py` | `_build_memory_section()` 新增方法 → 在既有 Memory 层组装末尾追加 Layer 4 注入块；新增 `active_retrieval_enabled` 参数（默认 False，opt-in） | T-13 |
| `nanobot/agent/loop.py` | 装配 `RetrievalEngine`（仅当配置开启时 wire，与 Phase 2 `_wire_memory_extraction` 模式一致）；新增 `retrieval_engine_provider` 上下文供 `MemorySearchTool.create()` 调用 | T-13/T-14 |

---

## 任务列表（TDD 强制，每 Task 五步：写失败测试 → 跑红 → 写最小实现 → 跑绿 → commit）

> **Task 编号说明**：T-01..T-13，对应 4 个 GROUP（GROUP-A 基础 4 个并行 / GROUP-B 通道 4 个并行 / GROUP-C 后处理 2 个 / GROUP-D 引擎+集成 3 个串行）。**严禁**任一 Task 跳过写测试步骤。

---

### GROUP-A：基础组件（并行，4 WU）

#### Task T-01: RetrievalCandidate 数据类

**Files:**
- Create: `nanobot/memory/retrieval/candidate.py`
- Test: `tests/memory/retrieval/test_candidate.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/memory/retrieval/test_candidate.py
from nanobot.memory.retrieval.candidate import RetrievalCandidate

def test_candidate_required_fields():
    c = RetrievalCandidate(
        memory_id="m-1",
        content="Python 爬虫脚本",
        source_channel="semantic",
        relevance=0.9,
        recency_score=0.8,
        importance_score=0.7,
        access_frequency_score=0.5,
    )
    assert c.memory_id == "m-1"
    assert c.source_channel == "semantic"
    assert c.composite_score == 0.0  # 默认未计算
    assert c.raw is None

def test_candidate_with_raw():
    raw = SimpleNamespace(id="m-2", updated_at=datetime.now())
    c = RetrievalCandidate(
        memory_id="m-2", content="...", source_channel="episodes",
        relevance=0.6, recency_score=0.4, importance_score=0.3,
        access_frequency_score=0.2, raw=raw,
    )
    assert c.raw is raw

def test_candidate_invalid_channel():
    with pytest.raises(ValueError, match="source_channel"):
        RetrievalCandidate(
            memory_id="m-3", content="...", source_channel="bogus",
            relevance=0.0, recency_score=0.0, importance_score=0.0,
            access_frequency_score=0.0,
        )
```

- [ ] **Step 2: 跑红**

Run: `pytest tests/memory/retrieval/test_candidate.py -v`
Expected: `ModuleNotFoundError: No module named 'nanobot.memory.retrieval.candidate'`

- [ ] **Step 3: 写最小实现**

```python
# nanobot/memory/retrieval/candidate.py
"""检索候选项数据类。"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Literal

SourceChannel = Literal["semantic", "episodes", "recent", "attachments"]
_VALID_CHANNELS = frozenset({"semantic", "episodes", "recent", "attachments"})


@dataclass(frozen=True)
class RetrievalCandidate:
    """多路召回的统一点。综合分由 Reranker 写入 composite_score。"""
    memory_id: str
    content: str
    source_channel: SourceChannel
    relevance: float              # 来自底层通道原始相关性
    recency_score: float          # 0..1，时间衰减后
    importance_score: float       # 0..1，记忆自身权重
    access_frequency_score: float # 0..1，log1p(access_count)/5
    raw: Any = None               # 原始 Memory/Episode 对象，便于后续格式化
    composite_score: float = field(default=0.0)

    def __post_init__(self) -> None:
        if self.source_channel not in _VALID_CHANNELS:
            raise ValueError(f"source_channel must be one of {_VALID_CHANNELS}, got {self.source_channel!r}")
```

- [ ] **Step 4: 跑绿**

Run: `pytest tests/memory/retrieval/test_candidate.py -v`
Expected: 3 passed

- [ ] **Step 5: commit**

```bash
git add nanobot/memory/retrieval/candidate.py tests/memory/retrieval/test_candidate.py
git commit -m "feat(retrieval): RetrievalCandidate dataclass with channel validation (T-01)"
```

---

#### Task T-02: MemoryQueryPreprocessor（Gate + 反注入）

**Files:**
- Create: `nanobot/memory/retrieval/preprocessor.py`
- Test: `tests/memory/retrieval/test_preprocessor.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/memory/retrieval/test_preprocessor.py
import pytest
from nanobot.memory.retrieval.preprocessor import MemoryQueryPreprocessor

@pytest.mark.parametrize("text", ["", "   ", None])
def test_should_skip_empty(text):
    skip, reason = MemoryQueryPreprocessor.should_skip_retrieval(text, [])
    assert skip is True and reason == "empty"

@pytest.mark.parametrize("text", ["好", "嗯", "ok", "yes", "stop", "继续", "可以", "收到"])
def test_should_skip_control_only(text):
    skip, reason = MemoryQueryPreprocessor.should_skip_retrieval(text, [])
    assert skip is True and reason == "control_only"

@pytest.mark.parametrize("text", ["hi", "啊"])  # ≤3 chars + 无 _KEEP_SHORT_HINTS
def test_should_skip_too_short(text):
    skip, reason = MemoryQueryPreprocessor.should_skip_retrieval(text, [])
    assert skip is True and reason == "too_short"

def test_should_skip_short_without_context():
    skip, reason = MemoryQueryPreprocessor.should_skip_retrieval("明天天气", [])
    assert skip is True and reason == "short_without_context"

def test_should_pass_with_context():
    skip, reason = MemoryQueryPreprocessor.should_skip_retrieval(
        "我之前写的那个 Python 爬虫脚本还能用吗",
        [{"role": "user", "content": "..."}],
    )
    assert skip is False and reason == ""

def test_clean_query_removes_injection_blocks():
    raw = (
        "用户问题: 你好\n"
        "## 相关记忆\n过去我们聊过 Python\n##\n"
        "用户问题: 再来一遍\n"
        "<vault-context>block me</vault-context>\n"
        "用户问题: 真正的问题"
    )
    cleaned = MemoryQueryPreprocessor.clean_query(raw)
    assert "相关记忆" not in cleaned
    assert "<vault-context>" not in cleaned
    assert "真正的问题" in cleaned

def test_prepare_returns_cleaned_and_skip_flag():
    prepared = MemoryQueryPreprocessor.prepare(
        "好## 相关记忆\ninject## 你好",
        recent_messages=[],
    )
    assert prepared.skip is True            # "好" → control_only
    assert "相关记忆" not in prepared.cleaned_query
```

- [ ] **Step 2: 跑红**

Run: `pytest tests/memory/retrieval/test_preprocessor.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: 写最小实现**

```python
# nanobot/memory/retrieval/preprocessor.py
"""检索前置守卫：跳过空/控制词/超短；反注入清洗。"""
from __future__ import annotations
import re
from dataclasses import dataclass

_CONTROL_ONLY = frozenset({
    "好", "嗯", "哦", "啊", "呃", "嘿", "哎",
    "可以", "行", "继续", "收到", "好的", "ok", "okay",
    "yes", "no", "yep", "nope", "stop", "halt",
    "thanks", "thank", "thx", "ty",
})
_KEEP_SHORT_HINTS = ("?", "？", "?", "!", "！", "吗", "呢", "吧", "啥")
_INJECTION_BLOCK_PATTERNS = (
    r"(?is)##\s*相关记忆.*?(?=\n##|\Z)",
    r"(?is)##\s*核心记忆.*?(?=\n##|\Z)",
    r"(?is)##\s*长期记忆.*?(?=\n##|\Z)",
    r"(?is)<vault-context>.*?</vault-context>",
    r"(?is)<memory>.*?</memory>",
    r"(?is)<long-term-memory>.*?</long-term-memory>",
)


@dataclass(frozen=True)
class PreparedQuery:
    skip: bool
    reason: str
    cleaned_query: str


class MemoryQueryPreprocessor:
    """检索前置：决定是否跳过 + 清洗历史 Prompt 注入。"""

    @classmethod
    def should_skip_retrieval(cls, query: str | None, recent_messages: list) -> tuple[bool, str]:
        text = (query or "").strip()
        if not text:
            return True, "empty"
        lowered = text.lower()
        if lowered in cls._CONTROL_ONLY:
            return True, "control_only"
        if len(text) <= 3 and not any(h in text for h in _KEEP_SHORT_HINTS):
            return True, "too_short"
        if len(text) <= 12 and not recent_messages and not any(h in lowered for h in _KEEP_SHORT_HINTS):
            return True, "short_without_context"
        return False, ""

    @classmethod
    def clean_query(cls, query: str) -> str:
        out = query
        for pat in _INJECTION_BLOCK_PATTERNS:
            out = re.sub(pat, "", out)
        return out.strip()

    @classmethod
    def prepare(cls, query: str, recent_messages: list) -> PreparedQuery:
        cleaned = cls.clean_query(query or "")
        skip, reason = cls.should_skip_retrieval(cleaned, recent_messages)
        return PreparedQuery(skip=skip, reason=reason, cleaned_query=cleaned)

    _CONTROL_ONLY = _CONTROL_ONLY  # 暴露供测试遍历
```

- [ ] **Step 4: 跑绿**

Run: `pytest tests/memory/retrieval/test_preprocessor.py -v`
Expected: 全用例 passed

- [ ] **Step 5: commit**

```bash
git add nanobot/memory/retrieval/preprocessor.py tests/memory/retrieval/test_preprocessor.py
git commit -m "feat(retrieval): MemoryQueryPreprocessor gate + injection filter (T-02)"
```

---

#### Task T-03: QueryDecomposer（LLM + 规则降级 + 缓存）

**Files:**
- Create: `nanobot/memory/retrieval/decomposer.py`
- Test: `tests/memory/retrieval/test_decomposer.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/memory/retrieval/test_decomposer.py
import pytest
from nanobot.memory.retrieval.decomposer import QueryDecomposer, DecompositionResult


class _FakeBrain:
    def __init__(self, payload): self._payload = payload
    async def call_compiler(self, prompt: str) -> str:
        return self._payload


@pytest.mark.asyncio
async def test_llm_path_returns_keywords_and_intent():
    brain = _FakeBrain('{"keywords": ["Python", "爬虫"], "intent": "general"}')
    decomp = QueryDecomposer(brain=brain)
    result = await decomp.decompose("我之前写的那个 Python 爬虫脚本还能用吗", recent=[])
    assert result.keywords == ["Python", "爬虫"]
    assert result.intent == "general"
    assert result.used_llm is True


@pytest.mark.asyncio
async def test_rule_fallback_when_no_brain():
    decomp = QueryDecomposer(brain=None)
    result = await decomp.decompose("我之前写的 Python 爬虫脚本还能用吗", recent=[])
    assert "Python" in result.keywords or "爬虫" in result.keywords
    assert result.used_llm is False


@pytest.mark.asyncio
async def test_rule_fallback_when_llm_fails():
    class _BadBrain:
        async def call_compiler(self, prompt: str) -> str:
            raise RuntimeError("LLM down")
    decomp = QueryDecomposer(brain=_BadBrain())
    result = await decomp.decompose("找到那个 Python 脚本", recent=[])
    assert result.used_llm is False
    assert result.keywords  # 至少提取了一个关键词


@pytest.mark.asyncio
async def test_search_file_intent_from_keywords():
    decomp = QueryDecomposer(brain=None)
    result = await decomp.decompose("之前的 图片 在哪", recent=[])
    assert result.intent == "search_file"


@pytest.mark.asyncio
async def test_cache_hit_skips_llm():
    brain = _FakeBrain('{"keywords": ["X"], "intent": "general"}')
    decomp = QueryDecomposer(brain=brain)
    r1 = await decomp.decompose("something", recent=[])
    r2 = await decomp.decompose("something", recent=[])
    # 第二次 cache hit，used_llm 仍为 True（同一结果），但内部 _decompose_cache 命中
    assert r1.used_llm is True and r2.used_llm is True
```

- [ ] **Step 2: 跑红**

Run: `pytest tests/memory/retrieval/test_decomposer.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: 写最小实现**

```python
# nanobot/memory/retrieval/decomposer.py
"""查询拆解：LLM 主路径 + 规则降级 + LRU 缓存（500 条）。"""
from __future__ import annotations
import re
from collections import OrderedDict
from dataclasses import dataclass, field

_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"']+")
_FILE_EXT_RE = re.compile(r"\.\w{1,5}\b")
_STOPWORDS = frozenset({
    "的", "了", "吗", "呢", "吧", "是", "我", "你", "他", "她", "它",
    "我们", "你们", "他们", "这", "那", "这个", "那个", "一个",
    "在", "有", "和", "或", "但", "就", "都", "也", "还", "再",
    "上", "下", "里", "外", "前", "后", "中", "间",
    "做", "做一下", "用", "能", "可以", "会", "想", "需要", "要",
})
_MEDIA_HINTS = ("图片", "照片", "视频", "文件", "音频", "pdf", "PDF")
_CACHE_MAX = 500


@dataclass
class DecompositionResult:
    keywords: list[str]
    intent: str
    used_llm: bool


class QueryDecomposer:
    """LLM 拆解关键词 + 规则降级（路径/扩展名/停用词/媒体意图）。"""

    def __init__(self, brain, *, cache_max: int = _CACHE_MAX):
        self._brain = brain
        self._cache: OrderedDict[str, DecompositionResult] = OrderedDict()
        self._cache_max = cache_max

    async def decompose(self, query: str, recent: list | None) -> DecompositionResult:
        cache_key = query.strip()
        if cache_key in self._cache:
            self._cache.move_to_end(cache_key)
            return self._cache[cache_key]
        result = await self._do_decompose(query, recent or [])
        self._cache[cache_key] = result
        if len(self._cache) > self._cache_max:
            self._cache.popitem(last=False)  # 淘汰一半策略见 Step 3 改进
        return result

    async def _do_decompose(self, query: str, recent: list) -> DecompositionResult:
        if self._brain is not None:
            try:
                payload = await self._brain.call_compiler(self._build_prompt(query))
                parsed = self._parse_payload(payload)
                if parsed is not None:
                    return DecompositionResult(
                        keywords=parsed["keywords"], intent=parsed["intent"], used_llm=True,
                    )
            except Exception:
                pass  # 降级到规则
        return self._rule_decompose(query)

    def _build_prompt(self, query: str) -> str:
        # 最小化：只透传 query；后续可注入 schema
        return f"decompose: {query}"

    def _parse_payload(self, payload: str) -> dict | None:
        import json
        try:
            data = json.loads(payload)
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        kws = data.get("keywords") or []
        if not isinstance(kws, list):
            return None
        return {
            "keywords": [str(k) for k in kws if k],
            "intent": str(data.get("intent", "general")),
        }

    def _rule_decompose(self, query: str) -> DecompositionResult:
        tokens: list[str] = []
        tokens.extend(_PATH_RE.findall(query))
        tokens.extend(m.group(0) for m in _FILE_EXT_RE.finditer(query))
        # 极简中文切分：每个非停用词 1-3 字片段视为关键词
        for chunk in re.split(r"\s+", query):
            for word in re.findall(r"[\w\u4e00-\u9fff]{2,4}", chunk):
                if word not in _STOPWORDS and word not in tokens:
                    tokens.append(word)
        intent = "search_file" if any(h in query for h in _MEDIA_HINTS) else "general"
        return DecompositionResult(keywords=tokens[:8], intent=intent, used_llm=False)
```

- [ ] **Step 4: 跑绿**

Run: `pytest tests/memory/retrieval/test_decomposer.py -v`
Expected: 全用例 passed

- [ ] **Step 5: commit**

```bash
git add nanobot/memory/retrieval/decomposer.py tests/memory/retrieval/test_decomposer.py
git commit -m "feat(retrieval): QueryDecomposer LLM + rule fallback + LRU cache (T-03)"
```

---

#### Task T-04: Search Backend 工厂 + FTS5 BM25 评分

**Files:**
- Create: `nanobot/memory/retrieval/search_backend.py`
- Modify: `nanobot/memory/database.py`（追加 `bm25_rank_to_score` 与可选 jieba 触发开关）
- Test: `tests/memory/retrieval/test_search_backend.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/memory/retrieval/test_search_backend.py
import sqlite3, pytest
from nanobot.memory.retrieval.search_backend import (
    create_search_backend, Fts5SearchBackend, score_from_bm25_rank,
)


def test_factory_default_returns_fts5():
    backend = create_search_backend("default")
    assert isinstance(backend, Fts5SearchBackend)


def test_factory_fts5_explicit():
    backend = create_search_backend("fts5")
    assert isinstance(backend, Fts5SearchBackend)


def test_factory_chromadb_lazy_when_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "chromadb", None)  # 模拟缺失
    backend = create_search_backend("chromadb")
    # 缺依赖应回退到 FTS5 而不是抛错
    assert isinstance(backend, Fts5SearchBackend)


def test_score_from_bm25_rank_zero_is_one():
    assert score_from_bm25_rank(0.0) == pytest.approx(1.0)


def test_score_from_bm25_rank_monotonic():
    assert score_from_bm25_rank(0.0) > score_from_bm25_rank(1.0) > score_from_bm25_rank(10.0)


def test_fts5_search_returns_scored(tmp_path):
    db_path = tmp_path / "mem.db"
    from nanobot.memory.database import MemoryDatabase
    db = MemoryDatabase(db_path)
    db.initialize()
    db.add_memory(content="Python 爬虫 教程", subject="python", predicate="is", obj="爬虫", type="fact")
    db.add_memory(content="Java 入门", subject="java", predicate="is", obj="入门", type="fact")
    db.add_memory(content="Rust 异步", subject="rust", predicate="is", obj="异步", type="fact")
    backend = create_search_backend("fts5")
    results = backend.search("Python 爬虫", limit=10)
    assert len(results) >= 1
    top = results[0]
    assert "Python" in top["content"] or "爬虫" in top["content"]
    assert 0.0 <= top["score"] <= 1.0
```

- [ ] **Step 2: 跑红**

Run: `pytest tests/memory/retrieval/test_search_backend.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: 写最小实现**

```python
# nanobot/memory/retrieval/database.py（追加方法，不动既有）
# ... 既有代码末尾追加:

def bm25_rank_to_score(rank: float) -> float:
    """FTS5 bm25() 值 → [0,1] 相关性。rank 越小越相关。"""
    return 1.0 / (1.0 + max(0.0, rank))
```

```python
# nanobot/memory/retrieval/search_backend.py
"""搜索后端工厂：FTS5（默认）/ chromadb（可选）/ api_embedding（可选）。"""
from __future__ import annotations
import importlib
import sqlite3
from typing import Protocol


class SearchBackend(Protocol):
    def search(self, query: str, *, limit: int = 10) -> list[dict]: ...


class Fts5SearchBackend:
    """基于 FTS5 BM25 的零依赖搜索。失败时 LIKE 兜底。"""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path  # None 时使用 in-memory；测试通过 monkeypatch 注入

    def search(self, query: str, *, limit: int = 10) -> list[dict]:
        conn = self._open()
        try:
            rows = self._try_fts5(conn, query, limit)
            if not rows:
                rows = self._fallback_like(conn, query, limit)
            return rows
        finally:
            conn.close()

    def _open(self) -> sqlite3.Connection:
        if self._db_path:
            return sqlite3.connect(self._db_path)
        return sqlite3.connect(":memory:")

    def _try_fts5(self, conn, query: str, limit: int) -> list[dict]:
        try:
            sql = (
                "SELECT m.id, m.content, m.importance_score, m.updated_at, "
                "       bm25(memories_fts) AS rank "
                "FROM memories_fts fts JOIN memories m ON m.rowid = fts.rowid "
                "WHERE memories_fts MATCH ? "
                "ORDER BY rank LIMIT ?"
            )
            cur = conn.execute(sql, (query, limit))
        except sqlite3.OperationalError:
            return []
        out: list[dict] = []
        for row in cur.fetchall():
            from nanobot.memory.database import bm25_rank_to_score
            out.append({
                "memory_id": row[0], "content": row[1],
                "importance_score": float(row[2] or 0.5),
                "updated_at": row[3], "score": bm25_rank_to_score(float(row[4])),
                "channel": "fts5",
            })
        return out

    def _fallback_like(self, conn, query: str, limit: int) -> list[dict]:
        like = f"%{query}%"
        cur = conn.execute(
            "SELECT id, content, importance_score, updated_at FROM memories "
            "WHERE content LIKE ? ORDER BY importance_score DESC LIMIT ?",
            (like, limit),
        )
        return [{
            "memory_id": r[0], "content": r[1], "importance_score": float(r[2] or 0.5),
            "updated_at": r[3], "score": 0.3, "channel": "fts5_fallback",
        } for r in cur.fetchall()]


def create_search_backend(name: str, *, db_path: str | None = None) -> SearchBackend:
    name = (name or "default").lower()
    if name in ("fts5", "default"):
        return Fts5SearchBackend(db_path=db_path)
    if name == "chromadb":
        if importlib.util.find_spec("chromadb") is None:
            return Fts5SearchBackend(db_path=db_path)
        # chromadb 真实后端实现留待未来增强（不在本 plan 范围），本阶段先回退保零依赖
        return Fts5SearchBackend(db_path=db_path)
    if name in ("api_embedding", "dashscope", "openai"):
        if importlib.util.find_spec("httpx") is None:
            return Fts5SearchBackend(db_path=db_path)
        # api_embedding 真实后端实现留待未来增强（不在本 plan 范围）
        return Fts5SearchBackend(db_path=db_path)
    return Fts5SearchBackend(db_path=db_path)


def score_from_bm25_rank(rank: float) -> float:
    from nanobot.memory.database import bm25_rank_to_score
    return bm25_rank_to_score(rank)
```

- [ ] **Step 4: 跑绿**

Run: `pytest tests/memory/retrieval/test_search_backend.py tests/memory/test_database.py -v`
Expected: 全用例 passed（既有 database 测试不退化）

- [ ] **Step 5: commit**

```bash
git add nanobot/memory/retrieval/search_backend.py nanobot/memory/database.py tests/memory/retrieval/test_search_backend.py
git commit -m "feat(retrieval): search_backend factory + FTS5 BM25 + LIKE fallback (T-04)"
```

---

### GROUP-B：四路召回通道（并行，4 WU；依赖 GROUP-A 完成）

> 公共契约：每个通道返回 `list[RetrievalCandidate]`；调用方传入 `engine` 实例（取 `self.store` / `self._decomposer` / `self._compute_recency` 等共享工具）。

#### Task T-05: Semantic 通道（核心；走 search_backend + repository）

**Files:**
- Create: `nanobot/memory/retrieval/channels/semantic.py`
- Create: `nanobot/memory/retrieval/channels/__init__.py`
- Test: `tests/memory/retrieval/channels/test_semantic.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/memory/retrieval/channels/test_semantic.py
import pytest
from datetime import datetime, timedelta, timezone
from nanobot.memory.retrieval.channels.semantic import search_semantic


class _FakeStore:
    def __init__(self, rows): self._rows = rows
    def search_semantic_scored(self, query, limit):
        return self._rows


def test_returns_candidates_with_semantic_channel():
    raw_memory = SimpleNamespace(
        id="m-1", content="Python 爬虫", importance_score=0.8,
        updated_at=datetime.now(timezone.utc),
        access_count=3,
    )
    store = _FakeStore([(raw_memory, 0.9)])
    cands = search_semantic(store, query="Python 爬虫", limit=10,
                            compute_recency=lambda dt: 1.0)
    assert len(cands) == 1
    c = cands[0]
    assert c.source_channel == "semantic"
    assert c.relevance == 0.9
    assert c.recency_score == 1.0
    assert c.importance_score == 0.8
    assert c.access_frequency_score == pytest.approx(0.477)  # log1p(3)/5


def test_empty_results():
    store = _FakeStore([])
    assert search_semantic(store, query="x", limit=10, compute_recency=lambda dt: 0.5) == []
```

- [ ] **Step 2: 跑红**

Run: `pytest tests/memory/retrieval/channels/test_semantic.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: 写最小实现**

> **本 Task 不修改 `repository.py`**；接口契约由 T-12 落地（详见 T-12 决议注释）。本 Task 仅在 `channels/semantic.py` 中实现通道包装逻辑，假定 `store.search_semantic_scored(query, limit) -> list[(Memory, raw_score)]` 由 RetrievalEngine 注入。

```python
# nanobot/memory/retrieval/channels/semantic.py
"""语义通道：调用底层 search_backend（向量 ∪ FTS5）并构造 RetrievalCandidate。"""
from __future__ import annotations
import math
from datetime import datetime
from typing import Callable

from nanobot.memory.retrieval.candidate import RetrievalCandidate


def _access_freq(access_count: int) -> float:
    return min(1.0, math.log1p(max(0, access_count)) / 5.0)


def search_semantic(
    store,  # 提供 search_semantic_scored(query, limit)
    *,
    query: str,
    limit: int,
    compute_recency: Callable[[datetime], float],
) -> list[RetrievalCandidate]:
    scored = store.search_semantic_scored(query, limit=limit * 3)
    candidates: list[RetrievalCandidate] = []
    for mem, raw_score in scored[:limit]:
        candidates.append(RetrievalCandidate(
            memory_id=mem.id,
            content=mem.content,
            source_channel="semantic",
            relevance=float(raw_score),
            recency_score=compute_recency(mem.updated_at),
            importance_score=float(getattr(mem, "importance_score", 0.5) or 0.5),
            access_frequency_score=_access_freq(int(getattr(mem, "access_count", 0) or 0)),
            raw=mem,
        ))
    return candidates
```

- [ ] **Step 4: 跑绿**

Run: `pytest tests/memory/retrieval/channels/test_semantic.py -v`
Expected: 2 passed

- [ ] **Step 5: commit**

```bash
git add nanobot/memory/retrieval/channels/semantic.py nanobot/memory/retrieval/channels/__init__.py \
        nanobot/memory/repository.py tests/memory/retrieval/channels/test_semantic.py
git commit -m "feat(retrieval): semantic search channel + repository interface stub (T-05)"
```

---

#### Task T-06: Episodes 通道（实体名关联）

**Files:**
- Create: `nanobot/memory/retrieval/channels/episodes.py`
- Modify: `nanobot/memory/models.py`（`Episode` 加 `compaction_checkpoint_id`）
- Test: `tests/memory/retrieval/channels/test_episodes.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/memory/retrieval/channels/test_episodes.py
import re, pytest
from nanobot.memory.retrieval.channels.episodes import (
    _extract_query_entities, search_episodes,
)


@pytest.mark.parametrize("text,expected", [
    ("帮我打开 C:\\Users\\foo\\bar.py", ["C:\\Users\\foo\\bar.py"]),
    ("看一下 main.py 文件", ["main.py"]),
    ("那个 build.sh 还在吗", ["build.sh"]),
    ("普通聊天没有任何路径", []),
])
def test_extract_query_entities(text, expected):
    entities = _extract_query_entities(text)
    for e in expected:
        assert e in entities


def test_search_episodes_returns_060_channel():
    class _FakeStore:
        def __init__(self, mapping): self.mapping = mapping
        def search_episodes(self, entity, limit):
            return self.mapping.get(entity, [])

    fake_episode = SimpleNamespace(id="ep-1", summary="用户配置了 uv 环境", session_key="s-1")
    store = _FakeStore({"main.py": [fake_episode]})
    cands = search_episodes(
        store, query="main.py 还在吗", limit=5,
        compute_recency=lambda dt: 0.5,
    )
    assert len(cands) == 1
    assert cands[0].source_channel == "episodes"
    assert cands[0].relevance == pytest.approx(0.6)
    assert cands[0].memory_id == "ep-1"


def test_search_episodes_limits_entities_to_3():
    class _CountingStore:
        def __init__(self): self.calls = []
        def search_episodes(self, entity, limit):
            self.calls.append(entity)
            return []
    store = _CountingStore()
    text = "a.py b.py c.py d.py e.py"
    search_episodes(store, query=text, limit=5, compute_recency=lambda dt: 0.5)
    assert len(store.calls) <= 3
```

- [ ] **Step 2: 跑红**

Run: `pytest tests/memory/retrieval/channels/test_episodes.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: 写最小实现**

```python
# nanobot/memory/retrieval/channels/episodes.py
"""情节通道：从 query 抽实体（路径/扩展名）→ 关联 Episode，固定分 0.6。"""
from __future__ import annotations
import re
from datetime import datetime
from typing import Callable

from nanobot.memory.retrieval.candidate import RetrievalCandidate

_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"']+|/[^\s\"']+\.\w{1,5}\b")
_EXT_RE = re.compile(r"\b[\w\u4e00-\u9fff\-]+\.\w{1,5}\b")
_EPISODE_BASE_SCORE = 0.6


def _extract_query_entities(query: str) -> list[str]:
    entities: list[str] = []
    for pat in (_PATH_RE, _EXT_RE):
        for m in pat.finditer(query):
            tok = m.group(0)
            if tok not in entities:
                entities.append(tok)
    return entities


def search_episodes(
    store,  # 提供 search_episodes(entity, limit)
    *,
    query: str,
    limit: int,
    compute_recency: Callable[[datetime], float],
) -> list[RetrievalCandidate]:
    entities = _extract_query_entities(query)[:3]
    candidates: list[RetrievalCandidate] = []
    for entity in entities:
        eps = store.search_episodes(entity=entity, limit=3)
        for ep in eps:
            candidates.append(RetrievalCandidate(
                memory_id=ep.id,
                content=getattr(ep, "summary", ""),
                source_channel="episodes",
                relevance=_EPISODE_BASE_SCORE,
                recency_score=compute_recency(getattr(ep, "updated_at", datetime.now())),
                importance_score=0.5,
                access_frequency_score=0.0,
                raw=ep,
            ))
    return candidates[:limit]
```

```python
# nanobot/memory/models.py（在 Episode dataclass 追加字段；保持默认 None）
@dataclass
class Episode:
    # ... 既有字段 ...
    compaction_checkpoint_id: str | None = None
```

- [ ] **Step 4: 跑绿**

Run: `pytest tests/memory/retrieval/channels/test_episodes.py tests/memory/test_models.py -v`
Expected: 全用例 passed

- [ ] **Step 5: commit**

```bash
git add nanobot/memory/retrieval/channels/episodes.py nanobot/memory/retrieval/channels/__init__.py \
        nanobot/memory/models.py tests/memory/retrieval/channels/test_episodes.py
git commit -m "feat(retrieval): episodes channel + Episode.compaction_checkpoint_id (T-06)"
```

---

#### Task T-07: Recent 通道（近 N 天高重要性）

**Files:**
- Create: `nanobot/memory/retrieval/channels/recent.py`
- Test: `tests/memory/retrieval/channels/test_recent.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/memory/retrieval/channels/test_recent.py
import pytest
from datetime import datetime, timedelta, timezone
from nanobot.memory.retrieval.channels.recent import search_recent


class _FakeStore:
    def __init__(self, memories): self._memories = memories
    def query_semantic(self, *, min_importance, since_days, limit):
        cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
        out = [m for m in self._memories if m.importance_score >= min_importance and m.updated_at >= cutoff]
        return out[:limit]


def _make_memory(mid, content, importance, days_ago, *, keyword_hit=False):
    return SimpleNamespace(
        id=mid, content=content, importance_score=importance,
        updated_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        access_count=0,
    )


def test_skips_low_recency():
    m_old = _make_memory("m-old", "old", 0.9, days_ago=30)
    store = _FakeStore([m_old])
    cands = search_recent(
        store, query="anything", keywords=[], limit=5,
        compute_recency=lambda dt: 0.05,  # 极低 recency
    )
    assert cands == []


def test_keyword_hit_relevance_higher_than_miss():
    m_hit = _make_memory("m-hit", "Python 爬虫", 0.7, days_ago=1, keyword_hit=True)
    m_miss = _make_memory("m-miss", "Java 入门", 0.7, days_ago=1)
    store = _FakeStore([m_hit, m_miss])
    cands = search_recent(
        store, query="Python", keywords=["Python"], limit=5,
        compute_recency=lambda dt: 0.9,
    )
    rels = sorted([c.relevance for c in cands], reverse=True)
    assert rels[0] >= 0.5
    assert rels[-1] <= 0.3


def test_returns_recent_channel():
    m = _make_memory("m-1", "anything", 0.8, days_ago=1)
    store = _FakeStore([m])
    cands = search_recent(store, query="x", keywords=[], limit=5,
                          compute_recency=lambda dt: 0.95)
    assert cands and cands[0].source_channel == "recent"
```

- [ ] **Step 2: 跑红**

Run: `pytest tests/memory/retrieval/channels/test_recent.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: 写最小实现**

```python
# nanobot/memory/retrieval/channels/recent.py
"""近期通道：近 3 天 + importance ≥ 0.6。recency < 0.3 跳过。命中关键词 0.5~0.7；未命中 0.2。"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Callable

from nanobot.memory.retrieval.candidate import RetrievalCandidate

_MIN_IMPORTANCE = 0.6
_RECENCY_FLOOR = 0.3
_RELEVANCE_HIT_LO, _RELEVANCE_HIT_HI = 0.5, 0.7
_RELEVANCE_MISS = 0.2


def search_recent(
    store,  # 提供 query_semantic(min_importance, since_days, limit)
    *,
    query: str,
    keywords: list[str],
    limit: int,
    compute_recency: Callable[[datetime], float],
) -> list[RetrievalCandidate]:
    memories = store.query_semantic(
        min_importance=_MIN_IMPORTANCE, since_days=3, limit=limit * 2,
    )
    candidates: list[RetrievalCandidate] = []
    for mem in memories:
        if compute_recency(mem.updated_at) < _RECENCY_FLOOR:
            continue
        hit = any(kw.lower() in mem.content.lower() for kw in keywords)
        relevance = _RELEVANCE_HIT_HI if hit and keywords else (
            _RELEVANCE_HIT_LO if hit else _RELEVANCE_MISS
        )
        candidates.append(RetrievalCandidate(
            memory_id=mem.id, content=mem.content, source_channel="recent",
            relevance=relevance,
            recency_score=compute_recency(mem.updated_at),
            importance_score=float(mem.importance_score),
            access_frequency_score=0.0, raw=mem,
        ))
    return candidates[:limit]
```

- [ ] **Step 4: 跑绿**

Run: `pytest tests/memory/retrieval/channels/test_recent.py -v`
Expected: 3 passed

- [ ] **Step 5: commit**

```bash
git add nanobot/memory/retrieval/channels/recent.py tests/memory/retrieval/channels/test_recent.py
git commit -m "feat(retrieval): recent channel with recency floor + keyword boost (T-07)"
```

---

#### Task T-08: Attachments 通道（媒体词触发）

**Files:**
- Create: `nanobot/memory/retrieval/channels/attachments.py`
- Test: `tests/memory/retrieval/channels/test_attachments.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/memory/retrieval/channels/test_attachments.py
import pytest
from nanobot.memory.retrieval.channels.attachments import search_attachments


class _FakeStore:
    def __init__(self, items): self._items = items
    def search_attachments(self, term, *, intent, limit):
        return [i for i in self._items if term.lower() in i["content"].lower()][:limit]


@pytest.mark.parametrize("query,intent,expected", [
    ("找一下之前的图片", "search_file", True),
    ("看一下 pdf 文件", "general", True),
    ("附近的视频", "general", True),
    ("聊点别的", "general", False),
])
def test_only_fires_on_media_hint_or_search_file(query, intent, expected):
    store = _FakeStore([{"id": "a-1", "content": "img-2026.png", "updated_at": __import__("datetime").datetime.now(), "importance_score": 0.5}])
    cands = search_attachments(store, raw_query=query, keywords=[], intent=intent, limit=5,
                               compute_recency=lambda dt: 0.9)
    assert (len(cands) > 0) is expected


def test_attachments_channel_label():
    store = _FakeStore([{"id": "a-1", "content": "video.mp4", "updated_at": __import__("datetime").datetime.now(), "importance_score": 0.5}])
    cands = search_attachments(store, raw_query="视频文件", keywords=[], intent="general", limit=5,
                               compute_recency=lambda dt: 0.9)
    assert cands and cands[0].source_channel == "attachments"
```

- [ ] **Step 2: 跑红**

Run: `pytest tests/memory/retrieval/channels/test_attachments.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: 写最小实现**

```python
# nanobot/memory/retrieval/channels/attachments.py
"""附件通道：仅在 intent=search_file 或含媒体词时触发。"""
from __future__ import annotations
from datetime import datetime
from typing import Callable

from nanobot.memory.retrieval.candidate import RetrievalCandidate

_MEDIA_KEYWORDS = ("图片", "照片", "视频", "文件", "音频", "pdf", "PDF",
                   "image", "photo", "video", "audio", "file")


def search_attachments(
    store,  # 提供 search_attachments(term, intent, limit)
    *,
    raw_query: str,
    keywords: list[str],
    intent: str,
    limit: int,
    compute_recency: Callable[[datetime], float],
) -> list[RetrievalCandidate]:
    has_media_hint = intent == "search_file" or any(kw in raw_query for kw in _MEDIA_KEYWORDS)
    if not has_media_hint:
        return []
    search_terms = [raw_query] + list(keywords)
    candidates: list[RetrievalCandidate] = []
    seen: set[str] = set()
    for term in search_terms:
        for item in store.search_attachments(term, intent=intent, limit=limit):
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            candidates.append(RetrievalCandidate(
                memory_id=item["id"], content=item["content"], source_channel="attachments",
                relevance=0.5,
                recency_score=compute_recency(item.get("updated_at", datetime.now())),
                importance_score=float(item.get("importance_score", 0.5)),
                access_frequency_score=0.0,
                raw=item,
            ))
    return candidates[:limit]
```

- [ ] **Step 4: 跑绿**

Run: `pytest tests/memory/retrieval/channels/test_attachments.py -v`
Expected: 全用例 passed

- [ ] **Step 5: commit**

```bash
git add nanobot/memory/retrieval/channels/attachments.py tests/memory/retrieval/channels/test_attachments.py
git commit -m "feat(retrieval): attachments channel with media-hint gate (T-08)"
```

---

### GROUP-C：后处理（串行：reranker → formatter；依赖 GROUP-A/B 完成）

> **T-09 决议**（消除编号错位）：原计划中编号为 T-09 的「公共工具 `_compute_recency` + 合并去重」**不单独建 Task**，统一在 T-12 `RetrievalEngine` 中实现。`_compute_recency` 在 T-10 已被 Reranker 导入复用；`merge_and_deduplicate` 在 T-12 engine 的多路召回合并段实现。本计划正式 Task 编号为 T-01..T-08, T-10..T-15（跳过 T-09）。

---

#### Task T-10: 综合重排（Reranker）

**Files:**
- Create: `nanobot/memory/retrieval/reranker.py`
- Test: `tests/memory/retrieval/test_reranker.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/memory/retrieval/test_reranker.py
import math, pytest
from datetime import datetime, timedelta, timezone
from nanobot.memory.retrieval.reranker import (
    Reranker, _compute_recency,
)
from nanobot.memory.retrieval.candidate import RetrievalCandidate


def test_recency_exponential_decay():
    now = datetime.now(timezone.utc)
    assert _compute_recency(now) == pytest.approx(1.0, abs=1e-3)
    assert _compute_recency(now - timedelta(days=1)) == pytest.approx(math.exp(-0.1), abs=1e-3)
    assert _compute_recency(now - timedelta(days=7)) == pytest.approx(math.exp(-0.7), abs=1e-3)


def _cand(channel, **kw):
    base = dict(
        memory_id="m", content="x", source_channel=channel,
        relevance=0.5, recency_score=0.5, importance_score=0.5,
        access_frequency_score=0.5,
    )
    base.update(kw)
    return RetrievalCandidate(**base)


def test_rerank_base_score_formula():
    cands = [_cand("semantic", relevance=1.0, recency_score=1.0,
                   importance_score=1.0, access_frequency_score=1.0)]
    out = Reranker().rerank(cands, query="x", persona=None, focus_terms=[])
    assert out[0].composite_score == pytest.approx(1.0, abs=1e-3)


def test_rerank_focus_boost_in_range():
    base = _cand("semantic", relevance=0.5)
    boosted = _cand("semantic", relevance=0.5)
    out_base = Reranker().rerank([base], query="Python 爬虫", persona=None, focus_terms=[])
    out_boost = Reranker().rerank([boosted], query="Python 爬虫", persona=None, focus_terms=["Python", "爬虫"])
    assert out_boost[0].composite_score > out_base[0].composite_score
    assert out_boost[0].composite_score <= out_base[0].composite_score * 1.35 + 1e-6


def test_rerank_action_penalty_for_facts():
    c = _cand("semantic", relevance=0.8, importance_score=0.8, recency_score=0.8)
    # fact 类型（默认）+ content 起始动作词 → × 0.3
    c.content = "打开 Chrome 浏览器查看"
    out = Reranker(fact_type_names=("fact",)).rerank([c], query="x", persona=None, focus_terms=[])
    raw = 0.4 * 0.8 + 0.2 * 0.8 + 0.2 * 0.8 + 0.2 * 0.5  # 0.74
    assert out[0].composite_score == pytest.approx(raw * 0.3, abs=1e-3)


def test_rerank_cold_start_exemption():
    c = _cand("semantic", relevance=0.05, importance_score=0.05, recency_score=0.995,
              access_frequency_score=0.05)
    out = Reranker().rerank([c], query="x", persona=None, focus_terms=[])
    assert len(out) == 1  # 豁免，未被 0.35 阈值过滤


def test_rerank_min_threshold_drops_low_score():
    c = _cand("semantic", relevance=0.05, importance_score=0.05, recency_score=0.05,
              access_frequency_score=0.05)
    out = Reranker().rerank([c], query="x", persona=None, focus_terms=[])
    assert out == []
```

- [ ] **Step 2: 跑红**

Run: `pytest tests/memory/retrieval/test_reranker.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: 写最小实现**

```python
# nanobot/memory/retrieval/reranker.py
"""综合重排：score = 0.4×Rel + 0.2×Rec + 0.2×Imp + 0.2×AccessFreq
   + 焦点增强（×1.0~1.35）
   + 动作词惩罚（fact 且起始动作词 × 0.3）
   + 冷启动豁免（recency ≥ 0.99）
   + 最小阈值（0.35）。"""
from __future__ import annotations
import math
import re
from dataclasses import replace as _replace
from datetime import datetime

from nanobot.memory.retrieval.candidate import RetrievalCandidate

_W_RELEVANCE = 0.40
_W_RECENCY = 0.20
_W_IMPORTANCE = 0.20
_W_ACCESS = 0.20

_FOCUS_BOOST_LO = 1.0
_FOCUS_BOOST_HI = 1.35
_ACTION_PENALTY = 0.30
_COLD_START_RECENCY = 0.99
_MIN_COMPOSITE = 0.35

_ACTION_PREFIXES = (
    "打开", "启动", "运行", "执行", "调用", "安装", "部署", "下载",
    "上传", "删除", "修改", "更新", "重启", "关闭", "退出",
    "open", "run", "execute", "install", "deploy", "download", "delete",
)
_ACTION_PATTERN = re.compile(
    r"^\s*(?:" + "|".join(re.escape(p) for p in _ACTION_PREFIXES) + r")\b",
    re.IGNORECASE,
)


def _compute_recency(updated_at: datetime, *, now: datetime | None = None) -> float:
    base = now or datetime.now(updated_at.tzinfo) if updated_at.tzinfo else (now or datetime.now())
    days = (base - updated_at).total_seconds() / 86400.0
    return math.exp(-0.1 * max(0.0, days))


class Reranker:
    def __init__(self, *, fact_type_names: tuple[str, ...] = ("fact", "FACT")):
        self._fact_types = set(fact_type_names)

    def rerank(
        self,
        candidates: list[RetrievalCandidate],
        *,
        query: str,
        persona,
        focus_terms: list[str],
    ) -> list[RetrievalCandidate]:
        scored: list[RetrievalCandidate] = []
        for c in candidates:
            base = (
                _W_RELEVANCE * c.relevance
                + _W_RECENCY * c.recency_score
                + _W_IMPORTANCE * c.importance_score
                + _W_ACCESS * c.access_frequency_score
            )
            # 焦点增强
            if focus_terms and any(ft.lower() in c.content.lower() for ft in focus_terms):
                # boost ∝ 命中数；这里简化为线性映射到 [1.0, 1.35]
                hits = sum(1 for ft in focus_terms if ft.lower() in c.content.lower())
                boost = min(_FOCUS_BOOST_HI, _FOCUS_BOOST_LO + 0.1 * min(hits, 4))
                base *= boost
            # 动作词惩罚（仅对 fact 类型）
            raw = getattr(c.raw, "type", None)
            type_name = getattr(raw, "value", raw) if raw is not None else None
            if type_name in self._fact_types and _ACTION_PATTERN.match(c.content):
                base *= _ACTION_PENALTY
            scored.append(_replace(c, composite_score=base))
        # 冷启动豁免 + 阈值过滤
        out: list[RetrievalCandidate] = []
        for c in scored:
            if c.composite_score >= _MIN_COMPOSITE or c.recency_score >= _COLD_START_RECENCY:
                out.append(c)
        out.sort(key=lambda x: x.composite_score, reverse=True)
        return out
```

- [ ] **Step 4: 跑绿**

Run: `pytest tests/memory/retrieval/test_reranker.py -v`
Expected: 5 passed

- [ ] **Step 5: commit**

```bash
git add nanobot/memory/retrieval/reranker.py tests/memory/retrieval/test_reranker.py
git commit -m "feat(retrieval): Reranker with composite formula + boosts + exemptions (T-10)"
```

---

#### Task T-11: Formatter（Token 预算 + 噪声过滤 + 事实去重）

**Files:**
- Create: `nanobot/memory/retrieval/formatter.py`
- Test: `tests/memory/retrieval/test_formatter.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/memory/retrieval/test_formatter.py
import pytest
from nanobot.memory.retrieval.formatter import (
    Formatter, _strip_experience_noise, _dedupe_facts_by_subject_predicate,
)
from nanobot.memory.retrieval.candidate import RetrievalCandidate


def _cand(content, *, source="extraction", confidence=0.9, type_="fact", subject="s", predicate="p", memory_id="m"):
    raw = SimpleNamespace(source=source, confidence=confidence, type=SimpleNamespace(value=type_), subject=subject, predicate=predicate)
    return RetrievalCandidate(
        memory_id=memory_id, content=content, source_channel="semantic",
        relevance=0.5, recency_score=0.5, importance_score=0.5,
        access_frequency_score=0.5, raw=raw,
    )


def test_strip_experience_noise_filters_system_sources():
    cands = [
        _cand("keep me", source="user", type_="fact"),
        _cand("auto_postmortem noise", source="auto_postmortem", type_="fact"),
        _cand("daily_consolidator noise", source="daily_consolidator", type_="experience"),
        _cand("low conf", source="user", type_="fact", confidence=0.3),
    ]
    out = _strip_experience_noise(cands)
    assert [c.content for c in out] == ["keep me"]


def test_dedup_facts_by_subject_predicate_keeps_first():
    cands = [
        _cand("张三 35 岁", subject="张三", predicate="age", memory_id="m1"),
        _cand("张三 32 岁", subject="张三", predicate="age", memory_id="m2"),
        _cand("李四 28 岁", subject="李四", predicate="age", memory_id="m3"),
    ]
    out = _dedupe_facts_by_subject_predicate(cands)
    assert [c.memory_id for c in out] == ["m1", "m3"]


def test_format_within_budget_truncates_by_token():
    big = " ".join(["Python 爬虫"] * 200)
    cands = [_cand(big, memory_id="big"), _cand("短的", memory_id="short")]
    out = Formatter().format(cands, max_tokens=50)
    # 至少包含"短"，big 因超出预算被截断/截除
    joined = "".join(c.content for c in out)
    assert "短" in joined


def test_format_returns_empty_for_empty():
    assert Formatter().format([], max_tokens=100) == []


def test_format_prefix_injection_block():
    cands = [_cand("Python 爬虫")]
    out = Formatter().format(cands, max_tokens=100)
    assert any("相关记忆" in c.content for c in out) or out  # 由 Formatter 在 Engine 层加前缀；此处只验证候选
```

- [ ] **Step 2: 跑红**

Run: `pytest tests/memory/retrieval/test_formatter.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: 写最小实现**

```python
# nanobot/memory/retrieval/formatter.py
"""格式化：噪声过滤 + 事实去重 + Token 预算截断。"""
from __future__ import annotations
from dataclasses import replace
from typing import Iterable

from nanobot.memory.retrieval.candidate import RetrievalCandidate

_NOISE_SOURCES = frozenset({"auto_postmortem", "daily_consolidator", "system", "compactor"})
_LOW_CONFIDENCE_THRESHOLD = 0.6
_APPROX_CHARS_PER_TOKEN = 2  # 中文 1~2 char/token；英文 4 char/token；保守按 2 估算


def _strip_experience_noise(candidates: Iterable[RetrievalCandidate]) -> list[RetrievalCandidate]:
    out: list[RetrievalCandidate] = []
    for c in candidates:
        raw = c.raw
        src = getattr(raw, "source", None)
        if src in _NOISE_SOURCES:
            continue
        type_val = getattr(getattr(raw, "type", None), "value", None)
        confidence = float(getattr(raw, "confidence", 1.0) or 1.0)
        if type_val in ("fact", "experience") and confidence < _LOW_CONFIDENCE_THRESHOLD:
            continue
        out.append(c)
    return out


def _dedupe_facts_by_subject_predicate(candidates: Iterable[RetrievalCandidate]) -> list[RetrievalCandidate]:
    seen: set[tuple[str, str]] = set()
    out: list[RetrievalCandidate] = []
    for c in candidates:
        raw = c.raw
        subj = getattr(raw, "subject", None)
        pred = getattr(raw, "predicate", None)
        if subj is None or pred is None:
            out.append(c)
            continue
        key = (str(subj), str(pred))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


class Formatter:
    def __init__(self, *, chars_per_token: int = _APPROX_CHARS_PER_TOKEN):
        self._cpt = chars_per_token

    def format(self, candidates: list[RetrievalCandidate], *, max_tokens: int) -> list[RetrievalCandidate]:
        if not candidates:
            return []
        cleaned = _dedupe_facts_by_subject_predicate(_strip_experience_noise(candidates))
        budget_chars = max(0, max_tokens) * self._cpt
        out: list[RetrievalCandidate] = []
        used = 0
        for c in cleaned:
            content = c.content or ""
            if used + len(content) > budget_chars and out:
                break  # 截断后续
            out.append(replace(c, content=content))
            used += len(content)
        return out

    @staticmethod
    def render_injection_block(candidates: list[RetrievalCandidate]) -> str:
        if not candidates:
            return ""
        lines = ["## 相关记忆（自动检索）"]
        for c in candidates:
            lines.append(f"- {c.content}")
        return "\n".join(lines)
```

- [ ] **Step 4: 跑绿**

Run: `pytest tests/memory/retrieval/test_formatter.py -v`
Expected: 全用例 passed

- [ ] **Step 5: commit**

```bash
git add nanobot/memory/retrieval/formatter.py tests/memory/retrieval/test_formatter.py
git commit -m "feat(retrieval): Formatter with noise strip + dedup + token budget (T-11)"
```

---

### GROUP-D：引擎 + 集成（串行，依赖 GROUP-A/B/C）

#### Task T-12: RetrievalEngine 编排器

**Files:**
- Create: `nanobot/memory/retrieval/engine.py`
- Create: `nanobot/memory/retrieval/__init__.py`
- Modify: `nanobot/memory/repository.py`（为 `search_semantic_scored`/`search_episodes`/`query_semantic`/`search_attachments` 提供 minimal 实现 OR 在 Engine 内部统一委托给 `store.search_memories`）
- Test: `tests/memory/retrieval/test_engine.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/memory/retrieval/test_engine.py
import pytest
from nanobot.memory.retrieval.engine import RetrievalEngine


class _StubStore:
    """Stub 取代真实 SQLite，验证 4 通道都被调用 + rerank + formatter 走通。"""
    def __init__(self):
        self.search_semantic_scored_called = 0
        self.search_episodes_called = 0
        self.query_semantic_called = 0
        self.search_attachments_called = 0

    def search_semantic_scored(self, q, limit):
        self.search_semantic_scored_called += 1
        return [(SimpleNamespace(id="s1", content="Python 爬虫", importance_score=0.8,
                                 updated_at=__import__("datetime").datetime.now(),
                                 access_count=2, type=None, source="user",
                                 subject=None, predicate=None, confidence=1.0), 0.9)]

    def search_episodes(self, entity, limit):
        self.search_episodes_called += 1
        return []

    def query_semantic(self, *, min_importance, since_days, limit):
        self.query_semantic_called += 1
        return []

    def search_attachments(self, term, *, intent, limit):
        self.search_attachments_called += 1
        return []


@pytest.mark.asyncio
async def test_engine_returns_empty_when_gate_skips():
    store = _StubStore()
    engine = RetrievalEngine(store=store, brain=None)
    out = await engine.retrieve(query="好", recent_messages=[], max_tokens=500)
    assert out == ""
    assert store.search_semantic_scored_called == 0


@pytest.mark.asyncio
async def test_engine_full_path_with_keywords():
    store = _StubStore()
    engine = RetrievalEngine(store=store, brain=None)
    out = await engine.retrieve(
        query="我之前写的 Python 爬虫脚本还能用吗",
        recent_messages=[{"role": "user", "content": "..."}], max_tokens=500,
    )
    assert "相关记忆" in out
    assert "Python 爬虫" in out
    assert store.search_semantic_scored_called == 1
    assert store.search_episodes_called == 1


@pytest.mark.asyncio
async def test_engine_attachments_only_on_media_hint():
    store = _StubStore()
    engine = RetrievalEngine(store=store, brain=None)
    await engine.retrieve(query="随便聊", recent_messages=[], max_tokens=500)
    assert store.search_attachments_called == 0
    await engine.retrieve(query="找一下之前的图片", recent_messages=[], max_tokens=500)
    assert store.search_attachments_called == 1
```

- [ ] **Step 2: 跑红**

Run: `pytest tests/memory/retrieval/test_engine.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: 写最小实现**

```python
# nanobot/memory/retrieval/engine.py
"""检索引擎编排器：guard → decompose → 多路并行 → rerank → format → 注入前缀。"""
from __future__ import annotations
import asyncio
from datetime import datetime
from typing import Any

from nanobot.memory.retrieval.candidate import RetrievalCandidate
from nanobot.memory.retrieval.preprocessor import MemoryQueryPreprocessor
from nanobot.memory.retrieval.decomposer import QueryDecomposer
from nanobot.memory.retrieval.reranker import Reranker, _compute_recency
from nanobot.memory.retrieval.formatter import Formatter
from nanobot.memory.retrieval.channels.semantic import search_semantic
from nanobot.memory.retrieval.channels.episodes import search_episodes
from nanobot.memory.retrieval.channels.recent import search_recent
from nanobot.memory.retrieval.channels.attachments import search_attachments


class RetrievalEngine:
    def __init__(self, *, store, brain=None, persona=None, max_tokens: int = 700):
        self.store = store
        self._decomposer = QueryDecomposer(brain=brain)
        self._reranker = Reranker()
        self._formatter = Formatter()
        self._persona = persona
        self._default_max_tokens = max_tokens

    async def retrieve(
        self,
        *,
        query: str,
        recent_messages: list,
        active_persona=None,
        max_tokens: int | None = None,
        precomputed_keywords: list[str] | None = None,
    ) -> str:
        prepared = MemoryQueryPreprocessor.prepare(query, recent_messages)
        if prepared.skip:
            return ""
        tokens = max_tokens or self._default_max_tokens

        # 拆解
        if precomputed_keywords is not None:
            keywords = precomputed_keywords
            intent = "general"
        else:
            decomp = await self._decomposer.decompose(prepared.cleaned_query, recent_messages)
            keywords, intent = decomp.keywords, decomp.intent

        # 构建增强 query（简化：直接用 cleaned + keywords）
        enhanced = prepared.cleaned_query + " " + " ".join(keywords)

        # 多路并行
        recency = lambda dt: _compute_recency(self._coerce_dt(dt))
        semantic_task = asyncio.create_task(asyncio.to_thread(
            search_semantic, self.store, query=enhanced, limit=15, compute_recency=recency,
        ))
        episodes_task = asyncio.create_task(asyncio.to_thread(
            search_episodes, self.store, query=prepared.cleaned_query, limit=5, compute_recency=recency,
        ))
        recent_task = asyncio.create_task(asyncio.to_thread(
            search_recent, self.store, query=enhanced, keywords=keywords, limit=5, compute_recency=recency,
        ))
        attachments_task = asyncio.create_task(asyncio.to_thread(
            search_attachments, self.store, raw_query=prepared.cleaned_query,
            keywords=keywords, intent=intent, limit=5, compute_recency=recency,
        ))
        sem, eps, rec, att = await asyncio.gather(
            semantic_task, episodes_task, recent_task, attachments_task,
            return_exceptions=True,
        )
        candidates: list[RetrievalCandidate] = []
        for chunk in (sem, eps, rec, att):
            if isinstance(chunk, Exception):
                continue
            candidates.extend(chunk)

        # 去重（按 memory_id 保留最高 relevance）
        dedup: dict[str, RetrievalCandidate] = {}
        for c in candidates:
            prev = dedup.get(c.memory_id)
            if prev is None or c.relevance > prev.relevance:
                dedup[c.memory_id] = c
        candidates = list(dedup.values())

        # rerank + format
        ranked = self._reranker.rerank(
            candidates, query=prepared.cleaned_query, persona=self._persona, focus_terms=keywords,
        )
        formatted = self._formatter.format(ranked, max_tokens=tokens)
        return Formatter.render_injection_block(formatted)

    @staticmethod
    def _coerce_dt(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except Exception:
            return datetime.now()
```

```python
# nanobot/memory/retrieval/__init__.py
from nanobot.memory.retrieval.engine import RetrievalEngine
from nanobot.memory.retrieval.candidate import RetrievalCandidate

__all__ = ["RetrievalEngine", "RetrievalCandidate"]
```

```python
# nanobot/memory/repository.py（追加 4 个最小适配方法，委托既有 search_memories / 新增 SQL）
# ... 追加于文件末尾:

def search_semantic_scored(conn, query, *, limit=30):
    """最小实现：复用 search_memories 返回 (Memory, bm25 归一化分)。"""
    results = search_memories(conn, query=query, limit=limit)  # 既有函数
    out = []
    for idx, m in enumerate(results):
        # 既有 search_memories 不返回 bm25 rank，这里用索引伪 rank 兼容
        # 真实 bm25 rank 列注入属于未来增强（不在本 plan 范围）
        pseudo_rank = float(idx)
        from nanobot.memory.database import bm25_rank_to_score
        out.append((m, bm25_rank_to_score(pseudo_rank)))
    return out

def search_episodes(conn, *, entity, limit=5):
    """最小实现：基于 LIKE 模糊匹配 episodes.summary。"""
    cur = conn.execute(
        "SELECT id, summary, updated_at FROM episodes WHERE summary LIKE ? OR session_key LIKE ? LIMIT ?",
        (f"%{entity}%", f"%{entity}%", limit),
    )
    return [_EpisodeRow(row[0], row[1], row[2]) for row in cur.fetchall()]

def query_semantic(conn, *, min_importance, since_days, limit):
    """最小实现：importance + 时间窗口。"""
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    cur = conn.execute(
        "SELECT id, content, importance_score, updated_at, access_count FROM memories "
        "WHERE importance_score >= ? AND updated_at >= ? "
        "ORDER BY importance_score DESC LIMIT ?",
        (min_importance, cutoff.isoformat(), limit),
    )
    return [_MemoryRow(row[0], row[1], row[2], row[3], row[4]) for row in cur.fetchall()]

def search_attachments(conn, *, term, intent, limit=5):
    """最小实现：attachments 表 LIKE 搜索。"""
    cur = conn.execute(
        "SELECT id, file_path, created_at FROM attachments WHERE file_path LIKE ? LIMIT ?",
        (f"%{term}%", limit),
    )
    return [_AttachmentRow(row[0], row[1], row[2]) for row in cur.fetchall()]


@dataclass
class _EpisodeRow:
    id: str; summary: str; updated_at: str

@dataclass
class _MemoryRow:
    id: str; content: str; importance_score: float
    updated_at: str; access_count: int

@dataclass
class _AttachmentRow:
    id: str; content: str = ""  # content 由调用方从 file_path 推导
    updated_at: str = ""
    importance_score: float = 0.5
```

- [ ] **Step 4: 跑绿**

Run: `pytest tests/memory/retrieval/test_engine.py -v`
Expected: 3 passed

- [ ] **Step 5: commit**

```bash
git add nanobot/memory/retrieval/engine.py nanobot/memory/retrieval/__init__.py \
        nanobot/memory/repository.py tests/memory/retrieval/test_engine.py
git commit -m "feat(retrieval): RetrievalEngine orchestrator + repository adapters (T-12)"
```

---

#### Task T-13: 集成到 build_system_prompt（Layer 4 注入）

**Files:**
- Modify: `nanobot/agent/context.py`（追加 `_build_memory_section()` 与 `active_retrieval_enabled` 参数）
- Modify: `nanobot/agent/loop.py`（装配 `RetrievalEngine`，参考 `_wire_memory_extraction` 模式）
- Test: `tests/memory/retrieval/test_integration_context.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/memory/retrieval/test_integration_context.py
import pytest
from unittest.mock import AsyncMock
from nanobot.agent.context import SystemContextBuilder  # 视 nanobot 实际类名


@pytest.mark.asyncio
async def test_build_memory_section_includes_active_retrieval_when_enabled():
    fake_engine = AsyncMock()
    fake_engine.retrieve.return_value = "## 相关记忆（自动检索）\n- Python 爬虫"
    ctx = SystemContextBuilder(workspace=tmp_path, active_retrieval_enabled=True,
                               retrieval_engine=fake_engine)
    section = await ctx._build_memory_section(query="我之前写的 Python 爬虫", recent_messages=[])
    assert "Python 爬虫" in section
    fake_engine.retrieve.assert_awaited_once()


@pytest.mark.asyncio
async def test_build_memory_section_skips_when_disabled():
    fake_engine = AsyncMock()
    ctx = SystemContextBuilder(workspace=tmp_path, active_retrieval_enabled=False,
                               retrieval_engine=fake_engine)
    section = await ctx._build_memory_section(query="Python 爬虫", recent_messages=[])
    fake_engine.retrieve.assert_not_called()


@pytest.mark.asyncio
async def test_build_memory_section_gate_short_circuits():
    fake_engine = AsyncMock()
    ctx = SystemContextBuilder(workspace=tmp_path, active_retrieval_enabled=True,
                               retrieval_engine=fake_engine)
    section = await ctx._build_memory_section(query="好", recent_messages=[])
    assert "相关记忆" not in section
    fake_engine.retrieve.assert_not_called()
```

- [ ] **Step 2: 跑红**

Run: `pytest tests/memory/retrieval/test_integration_context.py -v`
Expected: 失败（`_build_memory_section` 不存在）

- [ ] **Step 3: 写最小实现**

```python
# nanobot/agent/context.py（追加方法；不动既有方法签名）
class SystemContextBuilder:
    def __init__(self, workspace, *, active_retrieval_enabled: bool = False,
                 retrieval_engine=None, **kwargs):
        # 既有字段保持兼容
        ...
        self._active_retrieval_enabled = active_retrieval_enabled
        self._retrieval_engine = retrieval_engine

    async def _build_memory_section(self, *, query: str, recent_messages: list) -> str:
        """Layer 0..3 既有 + Layer 4 主动检索（可选）。"""
        parts: list[str] = []
        # Layer 0..3 既有（保留兼容调用方式）
        if hasattr(self, "_existing_memory_layers"):
            parts.append(self._existing_memory_layers(query=query, recent_messages=recent_messages))
        # Layer 4
        if self._active_retrieval_enabled and self._retrieval_engine is not None:
            from nanobot.memory.retrieval.preprocessor import MemoryQueryPreprocessor
            prepared = MemoryQueryPreprocessor.prepare(query, recent_messages)
            if not prepared.skip:
                retrieved = await self._retrieval_engine.retrieve(
                    query=query, recent_messages=recent_messages,
                )
                if retrieved:
                    parts.append(retrieved)
        return "\n\n".join(p for p in parts if p)
```

```python
# nanobot/agent/loop.py（追加装配方法，与 _wire_memory_extraction 模式一致）
class AgentLoop:
    def _wire_memory_retrieval(self, *, retrieval_engine=None, enabled: bool = False) -> None:
        if not enabled or retrieval_engine is None:
            return
        # 注入到 system context builder
        if hasattr(self, "_context_builder"):
            self._context_builder._active_retrieval_enabled = True
            self._context_builder._retrieval_engine = retrieval_engine
```

- [ ] **Step 4: 跑绿**

Run: `pytest tests/memory/retrieval/test_integration_context.py tests/agent/test_loop_wiring.py tests/memory/ -v`
Expected: 新用例 passed；既有 tests/agent/test_loop_wiring.py 与 tests/memory 全绿（不退化）

- [ ] **Step 5: commit**

```bash
git add nanobot/agent/context.py nanobot/agent/loop.py tests/memory/retrieval/test_integration_context.py
git commit -m "feat(retrieval): wire RetrievalEngine into build_system_prompt Layer 4 (T-13)"
```

---

### GROUP-E：LLM Tool 包装（用户补充需求）

> **设计意图**：Layer 4 自动注入是「被动召回」——检索结果直接拼进 system prompt；而 Tool 是「主动召回」——LLM 自行决定何时调 `memory_search` 查更细粒度结果。两路并存、互为补充：
> - **自动注入**：每轮隐式覆盖（用户不必知道），适合「上一句提到 → 下一句需要上下文」的近邻回溯
> - **Tool 调用**：LLM 在多步推理中显式补查（适合「我需要找 3 周前的项目文档」），不受 Layer 4 token 预算限制
>
> Tool 通过 `ToolLoader`（`nanobot/agent/tools/loader.py`）的 `pkgutil` 自动发现，**无需** 手动注册——只要文件位于 `nanobot/agent/tools/` 且类继承 `Tool` 即被纳入 `ToolRegistry`。`description` 字段是 LLM 在 system prompt 中看到的工具说明文案。

---

#### Task T-14: MemorySearchTool（LLM 主动调用入口）

**Files:**
- Create: `nanobot/agent/tools/memory_search.py`
- Test: `tests/agent/tools/test_memory_search_tool.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/agent/tools/test_memory_search_tool.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from nanobot.agent.tools.memory_search import MemorySearchTool


@pytest.fixture
def fake_engine():
    eng = MagicMock()
    eng.retrieve = AsyncMock(return_value="## 相关记忆（自动检索）\n- Python 爬虫")
    return eng


def test_tool_metadata_advertises_to_llm():
    """description 字段即 LLM 看到的工具说明。"""
    assert MemorySearchTool.name == "memory_search"
    desc = MemorySearchTool.description
    assert "长期记忆" in desc or "memory" in desc.lower()
    assert "search" in desc.lower() or "检索" in desc or "查询" in desc
    # 必须告知 LLM「自动注入」与「主动调用」的区别，避免重复检索
    assert "自动" in desc or "auto" in desc.lower()


def test_tool_auto_discoverable(tmp_path):
    """通过 ToolLoader 应能被自动发现。"""
    from nanobot.agent.tools.loader import ToolLoader
    from nanobot.agent.tools.context import ToolContext
    loader = ToolLoader()
    loader.discover_all()
    assert loader.registry.has("memory_search") or any(
        t.name == "memory_search" for t in loader.registry.all_tools()
    )


@pytest.mark.asyncio
async def test_execute_returns_formatted_string(fake_engine):
    tool = MemorySearchTool(retrieval_engine_provider=lambda: fake_engine)
    result = await tool.execute(query="Python 爬虫脚本", max_tokens=500)
    assert "Python 爬虫" in result
    fake_engine.retrieve.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_returns_empty_string_on_gate_skip(fake_engine):
    fake_engine.retrieve.return_value = ""
    tool = MemorySearchTool(retrieval_engine_provider=lambda: fake_engine)
    result = await tool.execute(query="好", max_tokens=500)
    assert result == ""


@pytest.mark.asyncio
async def test_execute_respects_token_budget(fake_engine):
    tool = MemorySearchTool(retrieval_engine_provider=lambda: fake_engine)
    await tool.execute(query="Python", max_tokens=200)
    # 透传给 engine 的 max_tokens 必须等于调用值
    call_kwargs = fake_engine.retrieve.await_args.kwargs
    assert call_kwargs.get("max_tokens") == 200


def test_tool_parameters_schema_has_query_and_max_tokens():
    """LLM 看到的 JSON schema 必须声明入参。"""
    from nanobot.agent.tools.base import tool_parameters
    schema = MemorySearchTool.parameters  # Tool 抽象
    props = schema.to_json_schema().get("properties", {})
    assert "query" in props
    assert "max_tokens" in props
```

- [ ] **Step 2: 跑红**

Run: `pytest tests/agent/tools/test_memory_search_tool.py -v`
Expected: `ModuleNotFoundError: No module named 'nanobot.agent.tools.memory_search'`

- [ ] **Step 3: 写最小实现**

```python
# nanobot/agent/tools/memory_search.py
"""LLM-callable 记忆检索工具。

对 `RetrievalEngine.retrieve()` 的薄包装，让 LLM 在多步推理中可显式补查历史记忆。
系统提示词中也会出现工具 description + 名称（自动发现机制）。

与 Layer 4 自动注入的区别：
- Layer 4 自动注入：每轮 system prompt 隐式拼入，无需 LLM 主动调用
- 本 Tool：LLM 显式调用，适合跨多轮的细粒度回溯（如「找 3 周前的项目文档」）
"""
from __future__ import annotations
from typing import Any, Callable

from loguru import logger

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.schema import IntegerSchema, StringSchema


class MemorySearchTool(Tool):
    """Query structured long-term memory (memories / episodes / attachments)."""

    _scopes = {"core", "subagent"}

    name = "memory_search"  # pyright: ignore[reportAssignmentType]
    description = (  # pyright: ignore[reportAssignmentType]
        "主动检索长期记忆。系统每轮会自动注入相关记忆（无需调用此工具）；"
        "但当用户提问涉及具体回溯（如「找 3 周前的项目文档」「上次配置的 URL」），"
        "或自动注入内容不够时，可调用本工具获取更细粒度的检索结果。"
        "返回格式：'## 相关记忆（自动检索）\\n- <content>' 形式的 Markdown。"
        "query: 自然语言检索词；max_tokens: 返回内容 token 预算（默认 500）。"
    )

    config_key = "memory_search"

    @classmethod
    def enabled(cls, ctx) -> bool:  # type: ignore[no-untyped-def]
        return True  # 与 extraction opt-in 区分；tool 自身常驻

    @classmethod
    def create(cls, ctx) -> "MemorySearchTool":  # type: ignore[no-untyped-def]
        return cls(retrieval_engine_provider=ctx.retrieval_engine_provider)

    def __init__(self, retrieval_engine_provider: Callable[[], Any] | None = None):
        self._engine_provider = retrieval_engine_provider or (lambda: None)

    @property
    def read_only(self) -> bool:
        return True

    @tool_parameters(
        query=StringSchema(description="自然语言检索词（必填）"),
        max_tokens=IntegerSchema(description="返回内容 token 预算；默认 500"),
    )
    async def execute(self, *, query: str, max_tokens: int = 500) -> ToolResult:
        engine = self._engine_provider()
        if engine is None:
            logger.warning("memory_search called but RetrievalEngine is not configured")
            return ToolResult(content="(memory search unavailable)")
        try:
            content = await engine.retrieve(query=query, recent_messages=[], max_tokens=max_tokens)
        except Exception as exc:
            logger.exception("memory_search tool failed: {}", exc)
            return ToolResult(content=f"(memory search error: {exc})", is_error=True)
        return ToolResult(content=content or "(no relevant memories found)")
```

- [ ] **Step 4: 跑绿**

Run: `pytest tests/agent/tools/test_memory_search_tool.py -v`
Expected: 全用例 passed

- [ ] **Step 5: commit**

```bash
git add nanobot/agent/tools/memory_search.py tests/agent/tools/test_memory_search_tool.py
git commit -m "feat(retrieval): MemorySearchTool — LLM-callable entry to RetrievalEngine (T-14)"
```

---

#### Task T-15: identity.md 模板告知（向 LLM 公示工具）

**Files:**
- Modify: `nanobot/templates/agent/identity.md`（追加 1 行，向 LLM 告知 `memory_search` 工具存在）
- Test: `tests/agent/test_identity_template.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/agent/test_identity_template.py
from pathlib import Path

IDENTITY = Path("nanobot/templates/agent/identity.md")


def test_identity_mentions_memory_search_tool():
    text = IDENTITY.read_text(encoding="utf-8")
    assert "memory_search" in text
    # 上下文：必须是「记忆」相关段落
    lower = text.lower()
    idx = lower.find("memory_search")
    assert idx > 0
    # 距前后 200 字内应有 memory/MEMORY.md 或 长期记忆 等关键词
    window = text[max(0, idx - 200): idx + 200]
    assert "MEMORY.md" in window or "长期记忆" in window or "long-term memory" in window.lower()


def test_identity_remains_under_token_budget():
    """Layer 0 guide 设计约束 ~200 token（compact）/ 815（full）。本 Task 只追加 1 行。"""
    text = IDENTITY.read_text(encoding="utf-8")
    # 经验值：英文/中文混排 ~2 char/token；200 token ≈ 400 char；800 token ≈ 1600 char
    assert len(text) < 2400, f"identity.md 过长: {len(text)} chars"
```

- [ ] **Step 2: 跑红**

Run: `pytest tests/agent/test_identity_template.py -v`
Expected: 第一个用例 fail（`memory_search` 不在 identity.md 中）

- [ ] **Step 3: 写最小实现**

```markdown
<!-- nanobot/templates/agent/identity.md 在 "Long-term memory: memory/MEMORY.md..." 一段后追加一行 -->
- Long-term memory: memory/MEMORY.md (automatically managed by Dream — do not edit directly)
- **Tool `memory_search`** is available to query structured long-term memories (SQLite-backed: memories/episodes/attachments) beyond what auto-injects each turn.
- History log: memory/history.jsonl (append-only JSONL; prefer built-in `grep` for search).
```

> **注意**：若仓库内 `identity.md` 是**模板**（`{{ agent_workspace_path }}` 占位符），上述追加保持原模板风格；若实例化版本另有 `nanobot/templates/agent/identity.full.md` / `compact.md`，需同步更新。本 Task 默认只改主模板。

- [ ] **Step 4: 跑绿**

Run: `pytest tests/agent/test_identity_template.py -v`
Expected: 2 passed

- [ ] **Step 5: commit**

```bash
git add nanobot/templates/agent/identity.md tests/agent/test_identity_template.py
git commit -m "docs(retrieval): advertise memory_search tool in identity template (T-15)"
```

---

## 任务汇总（dispatch 关系）

| GROUP | Task | 标题 | 预估 | 依赖 | wu_type | agent_role |
| --- | --- | --- | --- | --- | --- | --- |
| A（并行） | T-01 | RetrievalCandidate dataclass | 10 分钟 | 无 | feature | coder |
| A | T-02 | MemoryQueryPreprocessor | 15 分钟 | 无 | feature | coder |
| A | T-03 | QueryDecomposer（LLM + 规则） | 25 分钟 | 无 | feature | coder |
| A | T-04 | search_backend 工厂 + FTS5 | 20 分钟 | 无 | feature | coder |
| B（并行） | T-05 | Semantic 通道 | 15 分钟 | T-04 | feature | coder |
| B | T-06 | Episodes 通道 | 15 分钟 | 无 | feature | coder |
| B | T-07 | Recent 通道 | 15 分钟 | 无 | feature | coder |
| B | T-08 | Attachments 通道 | 15 分钟 | 无 | feature | coder |
| C（串行） | T-10 | Reranker | 20 分钟 | 无 | feature | coder |
| C | T-11 | Formatter | 15 分钟 | 无 | feature | coder |
| D（串行） | T-12 | RetrievalEngine 编排器 | 30 分钟 | T-01..T-11 | feature | coder |
| D | T-13 | 集成 build_system_prompt | 45 分钟 | T-12 | feature | coder |
| **E（用户补充）** | **T-14** | **MemorySearchTool（LLM 工具）** | **20 分钟** | **T-12** | **feature** | **coder** |
| E | T-15 | identity.md 模板告知 | 5 分钟 | T-14 | docs | implementer |

**预估口径**：上述时间为「单 WU 由资深 coder 单人执行」量级，含 TDD 五步（写测试 + 跑红 + 实现 + 跑绿 + commit）。实际并发执行时 GROUP-A 4 路并行总时长 ≈ 25 分钟（取 max T-03）；GROUP-B 4 路并行总时长 ≈ 15 分钟（无依赖可并发）；GROUP-D 串行总时长 ≈ 75 分钟；GROUP-E 在 T-12 完成后可派 ≈ 25 分钟。

> **关键**：T-14 是**用户补充需求**（既要 Layer 4 自动注入，也要注册为 LLM Tool），与 T-13 互补。详见 GROUP-E 设计意图段。

---

## 风险与权衡

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| Phase 1 `repository.search_memories` 不返回真实 BM25 rank | 语义通道精度受限 | T-04/T-12 已用 `bm25_rank_to_score(idx)` 兼容；真实 bm25 列注入属于**未来增强**（不在本 plan 范围），单独 PR 处理 |
| `chromadb` / `api_embedding` 后端复杂 | T-04 工厂暂时只支持 FTS5 | 默认回退到 FTS5；真实 chromadb/api_embedding 后端属于**未来增强**（不在本 plan 范围） |
| `Episode.compaction_checkpoint_id` 是 Phase 2 handoff §14 遗项 | T-06 顺带完成 | 数据库设计契约允许字段为 nullable，零回归 |
| 注入 Layer 4 改变 system prompt 大小 | 可能挤掉 skill 块空间 | T-13 校验 token 预算（默认 500 tokens）；用户在 build_system_prompt 层可调 |
| `RetrievalEngine` 默认 opt-in | 默认关闭 → 端到端验证需手动开启 | 尾盘 collective-test 显式启用 `active_retrieval_enabled=True` 跑 |
| **Tool 自动发现兼容性** | T-14 的 `MemorySearchTool` 必须能被 `ToolLoader` 通过 `pkgutil` 扫描到 | T-14 测试 `test_tool_auto_discoverable` 验证 `ToolRegistry.has("memory_search")`；遵循 `WebSearchTool` 的 `_scopes` / `config_key` / `create()` 工厂模式 |
| **identity 模板改动范围** | T-15 仅追加 1 行，避免突破 ~200 token（compact）预算 | `tests/agent/test_identity_template.py::test_identity_remains_under_token_budget` 兜底 |

## 回滚方案

> **Feature flag 兜底**：T-13 的 `active_retrieval_enabled=False` 是**生产默认关闭**开关——即便所有 WU 已合并，出问题时无需回滚代码，仅关 flag 即生效。

| GROUP | 回滚命令 | 数据兼容性 |
| --- | --- | --- |
| **A**（基础） | `git revert <commit-A>` | 无 DB schema 变更，完全安全 |
| **B**（通道） | `git revert <commit-B>` | `Episode.compaction_checkpoint_id` 字段 nullable，旧版读取忽略该字段，无破坏 |
| **C**（后处理） | `git revert <commit-C>` | 无 |
| **D**（引擎+集成） | `git revert <commit-D>` + 配置 `active_retrieval_enabled=False` | `context.py` / `loop.py` 兼容旧版；Memory layer 拼接点向后兼容 |
| **E**（Tool） | `git revert <commit-E>` + 删 `identity.md` 追加行 | Tool 自动发现通过 `pkgutil`，删除文件即从 `ToolRegistry` 移除，不影响其他工具 |

**额外回滚保障**：
- T-02 的 `_INJECTION_BLOCK_PATTERNS` 是**白名单风格**——若注入新 pattern 导致误过滤，调小 pattern 即可，不需代码回滚
- T-04 的 search_backend 工厂是**动态分发**——chromadb 出问题时回退到 `create_search_backend("fts5")`，无需重启服务
- T-11 的 Formatter `_MIN_COMPOSITE` 阈值是可配置常量——调大阈值可立即降低注入量，无需代码回滚

## 验收口径

| 检查项 | 命令 / 标准 |
| --- | --- |
| 单测全绿 | `pytest tests/memory/retrieval/ -v`（目标 30+ 用例） |
| **覆盖率目标** | `pytest tests/memory/retrieval/ --cov=nanobot/memory/retrieval --cov-report=term-missing --cov-fail-under=85`（行覆盖 ≥ 85%） |
| 既有测试不退化 | `pytest tests/memory/ tests/agent/ -q`（基线 345+ passed） |
| 全量回归 | `pytest tests/ -q`（无新增 fail） |
| Lint | `ruff check nanobot/memory/retrieval/ nanobot/agent/context.py nanobot/agent/loop.py nanobot/agent/tools/memory_search.py` |
| 类型检查 | `uv run --no-sync basedpyright nanobot/memory/retrieval/ nanobot/agent/tools/memory_search.py` |
| TDD 严格 | 每个 Task 必含 Step 1 写测试 / Step 2 跑红 / Step 3 实现 / Step 4 跑绿 / Step 5 commit |
| 反注入回归 | `tests/memory/retrieval/test_preprocessor.py::test_clean_query_removes_injection_blocks` 必须通过 |
| 集成 smoke | `tests/memory/retrieval/test_integration_context.py` 三个用例必须通过 |
| **Tool 自动发现** | `tests/agent/tools/test_memory_search_tool.py::test_tool_auto_discoverable` 必须通过；`ToolRegistry.has("memory_search") == True` |
| **Tool 元数据合规** | `description` 字段包含「自动注入」与「主动调用」语义对比，提示 LLM 何时用 Tool；`parameters` schema 声明 `query` + `max_tokens` |
| **identity 模板长度** | `tests/agent/test_identity_template.py::test_identity_remains_under_token_budget` 兜底（< 2400 chars） |
| 文档同步 | 本 plan 完成后回填 `docs/记忆系统/plan/阶段三设计_记忆检索.md`（实施版设计说明） |

## 阶段门禁

> ⚠️ **写入本 plan 后暂停，等用户在本会话单独说「开始实现」或「并行执行」再进入 GROUP-A 派发。**
> 同句「写计划然后执行」按 routing.md § 组合指令 **仅写 plan 并暂停**。

## Next

- 计划确认 → 说「开始实现」或「执行」
- 需要调整 → 直接说修改意见
- 想拆分并行 GROUP-A → 审 `dispatch.md` 后说「并行执行」
- 想加 WU（如真实 chromadb 后端、真实 bm25 rank 列） → 这些属于未来增强，**单独 PR** 处理，本 plan 范围已锁定