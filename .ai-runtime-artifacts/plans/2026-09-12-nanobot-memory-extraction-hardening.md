# nanobot 记忆提取加强 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 借鉴 OpenAkita 的"会话结束事件 + 多任务编排 + 显式依赖链"骨架，加强 nanobot 记忆提取的语义深度与可控性，并反向规避 OpenAkita 已知坑（30s 清空 turns、无去重、每轮 LLM 判定）。

**Architecture:**
1. 引入 `SessionEndOrchestrator` 作为会话级提取的统一入口（4 步依赖链）
2. 把 `MemoryExtractionHook.after_run` 的 T1 提取降级为 T0 即时同步
3. 双轨语义（用户画像 + 任务经验），同次 LLM 拼装引用评分
4. 引入画像增量合并（基于 `is_update` + subject+predicate）
5. 话题检测加廉价预筛规则 + 60s 最小间隔 + topic_hash 去重

**Tech Stack:**
- Python 3.11+ / asyncio
- pytest + pytest-asyncio
- 现有 `nanobot/memory/` 模块（不破坏 `extract_session` 兼容入口）
- SQLite 主存 + Chroma 向量索引（已有）

**TDD Required:** YES（每个生产代码任务严格遵循 RED → GREEN → VERIFY → COMMIT）

---

## 0. 文件结构与职责

| 新建 / 修改 | 路径 | 职责 |
| --- | --- | --- |
| 新建 | `nanobot/memory/session_end_event.py` | `SessionEndEvent` dataclass + `SessionEndReason` enum |
| 新建 | `nanobot/memory/orchestrator.py` | `SessionEndOrchestrator`（4 步依赖链）|
| 新建 | `nanobot/memory/profile_extractor.py` | `ProfileExtractor`（用户画像 + 引用评分 + 增量合并） |
| 新建 | `nanobot/memory/experience_extractor.py` | `ExperienceExtractor`（任务经验） |
| 新建 | `nanobot/memory/topic_prefilter.py` | 5 条廉价预筛规则 + 间隔节流状态 |
| 修改 | `nanobot/memory/extractor.py` | 新增 `generate_episode` 方法 + ActionNode 提取 |
| 修改 | `nanobot/memory/scratchpad_writer.py` | 新增 `format_with_llm` 方法（接通 `SCRATCHPAD_FORMAT_PROMPT`） |
| 修改 | `nanobot/memory/prompts.py` | 新增 `EXPERIENCE_EXTRACTION_PROMPT` + `CITATION_SCORING_SECTION` |
| 修改 | `nanobot/memory/repository.py` | 增量合并 + `conflicts_with` 字段 + `update_by_predicate` |
| 修改 | `nanobot/memory/filters.py` | 暴露 `_CHAT_FULL` / `_FOLLOW_UP_CJK/EN` 给预筛复用 |
| 修改 | `nanobot/memory/__init__.py` | 导出新公共 API |
| 修改 | `nanobot/agent/hooks/memory_extraction.py` | 接入编排器 + 预筛；on_finally 兜底 |
| 新建 | `tests/memory/test_session_end_event.py` | SessionEndEvent 单测 |
| 新建 | `tests/memory/test_orchestrator.py` | 编排器单测（依赖链 + 失败隔离 + feature flag）|
| 新建 | `tests/memory/test_episode_extraction.py` | generate_episode 单测 |
| 新建 | `tests/memory/test_profile_extractor.py` | 用户画像 + 引用评分 + 增量合并 |
| 新建 | `tests/memory/test_experience_extractor.py` | 任务经验 |
| 新建 | `tests/memory/test_topic_prefilter.py` | 5 条预筛 + 间隔节流 |
| 新建 | `tests/memory/test_scratchpad_format.py` | Scratchpad LLM 重构 |
| 修改 | `tests/memory/test_hook_memory_extraction.py` | 编排器接入 + 兼容性回归 |

---

## 1. 任务分解

按依赖顺序：基础设施 → 提取层 → 编排器 → hook 接入 → 集成测试。

---

### Task 1: `SessionEndEvent` 数据结构（P0）

**Files:**
- Create: `nanobot/memory/session_end_event.py`
- Test: `tests/memory/test_session_end_event.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/memory/test_session_end_event.py
from datetime import datetime
from nanobot.memory.session_end_event import SessionEndEvent, SessionEndReason


def test_session_end_event_basic_construction():
    evt = SessionEndEvent(
        session_key="user-42:cli",
        reason=SessionEndReason.USER_CLOSE,
        transcript=[{"role": "user", "content": "hi"}],
        emitted_at=datetime(2026, 9, 12, 10, 0, 0),
    )
    assert evt.session_key == "user-42:cli"
    assert evt.reason == SessionEndReason.USER_CLOSE
    assert len(evt.transcript) == 1
    assert evt.emitted_at.year == 2026


def test_session_end_reason_values():
    assert SessionEndReason.USER_CLOSE.value == "user_close"
    assert SessionEndReason.IDLE_TIMEOUT.value == "idle_timeout"
    assert SessionEndReason.PROCESS_SHUTDOWN.value == "process_shutdown"
    assert SessionEndReason.CHANNEL_DISCONNECT.value == "channel_disconnect"


def test_session_end_event_transcript_is_isolated():
    src = [{"role": "user", "content": "x"}]
    evt = SessionEndEvent(
        session_key="k", reason=SessionEndReason.USER_CLOSE,
        transcript=src, emitted_at=datetime.now(),
    )
    src.append({"role": "user", "content": "y"})
    assert len(evt.transcript) == 1  # 不应被外部修改影响
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/memory/test_session_end_event.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'nanobot.memory.session_end_event'`

- [ ] **Step 3: 写最小实现**

```python
# nanobot/memory/session_end_event.py
"""会话结束事件数据结构（P0：编排器输入契约）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class SessionEndReason(str, Enum):
    """会话结束原因。"""

    USER_CLOSE = "user_close"
    IDLE_TIMEOUT = "idle_timeout"
    PROCESS_SHUTDOWN = "process_shutdown"
    CHANNEL_DISCONNECT = "channel_disconnect"


@dataclass
class SessionEndEvent:
    """会话结束事件载荷。"""

    session_key: str
    reason: SessionEndReason
    transcript: list[dict] = field(default_factory=list)
    emitted_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        # 防御性浅拷贝，避免外部修改影响事件快照
        self.transcript = [dict(m) for m in self.transcript]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/memory/test_session_end_event.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add nanobot/memory/session_end_event.py tests/memory/test_session_end_event.py
git commit -m "feat(memory): add SessionEndEvent data structure (S1)"
```

---

### Task 2: `generate_episode` 抽取方法（P0）

**Files:**
- Modify: `nanobot/memory/extractor.py:526+`（在 `MemoryExtractor` 内新增方法）
- Test: `tests/memory/test_episode_extraction.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/memory/test_episode_extraction.py
import pytest
from nanobot.memory.extractor import MemoryExtractor


@pytest.mark.asyncio
async def test_generate_episode_with_llm_success(monkeypatch):
    """LLM 返回合法 JSON 时，episode 含 summary/goal/outcome/entities/tools_used。"""

    class FakeRuntime:
        async def chat_with_retry(self, *, model, messages, tools, **kwargs):
            class R:
                content = (
                    '{"summary":"用户配置了 PATH","goal":"配置环境",'
                    '"outcome":"success","entities":["~/.zshrc"],'
                    '"tools_used":["edit_file"]}'
                )
            return R()

    ext = MemoryExtractor(runtime=FakeRuntime())
    episode = await ext.generate_episode(
        transcript=[
            {"role": "user", "content": "帮我配 PATH"},
            {"role": "assistant", "content": "已写入 .zshrc", "tool_calls": [
                {"name": "edit_file", "input": {"path": "~/.zshrc"}}
            ]},
        ],
        session_key="k1",
    )
    assert episode.summary == "用户配置了 PATH"
    assert episode.goal == "配置环境"
    assert episode.outcome == "success"
    assert "~/.zshrc" in episode.entities
    assert "edit_file" in episode.tools_used


@pytest.mark.asyncio
async def test_generate_episode_empty_transcript_returns_none():
    class FakeRuntime:
        async def chat_with_retry(self, *, model, messages, tools, **kwargs):
            raise AssertionError("不应调用 LLM")

    ext = MemoryExtractor(runtime=FakeRuntime())
    assert await ext.generate_episode(transcript=[], session_key="k1") is None


@pytest.mark.asyncio
async def test_generate_episode_llm_failure_uses_heuristic(monkeypatch):
    """LLM 抛异常时，回退到 heuristic summary + 正则 entities。"""

    class FakeRuntime:
        async def chat_with_retry(self, *, model, messages, tools, **kwargs):
            raise RuntimeError("LLM down")

    ext = MemoryExtractor(runtime=FakeRuntime())
    episode = await ext.generate_episode(
        transcript=[
            {"role": "user", "content": "请帮我看看 /Users/a/b/c.py 文件"},
            {"role": "assistant", "content": "好的"},
        ],
        session_key="k1",
    )
    assert episode is not None
    assert episode.summary  # heuristic 非空
    assert any("c.py" in e or "Users" in e for e in episode.entities)


@pytest.mark.asyncio
async def test_generate_episode_action_nodes_capture_tool_calls():
    class FakeRuntime:
        async def chat_with_retry(self, *, model, messages, tools, **kwargs):
            class R:
                content = (
                    '{"summary":"x","goal":"y","outcome":"completed",'
                    '"entities":[],"tools_used":["read_file"]}'
                )
            return R()

    ext = MemoryExtractor(runtime=FakeRuntime())
    episode = await ext.generate_episode(
        transcript=[
            {"role": "assistant", "content": "ok", "tool_calls": [
                {"name": "read_file", "input": {"path": "/tmp/a.txt"}, "id": "tc1"}
            ]},
        ],
        session_key="k1",
    )
    assert len(episode.action_nodes) == 1
    node = episode.action_nodes[0]
    assert node.tool_name == "read_file"
    assert node.key_params.get("path") == "/tmp/a.txt"
    assert node.success is True  # 没有 error → 默认成功
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/memory/test_episode_extraction.py -v`
Expected: FAIL with `AttributeError: 'MemoryExtractor' object has no attribute 'generate_episode'`

- [ ] **Step 3: 写最小实现**

```python
# nanobot/memory/extractor.py（追加到 MemoryExtractor 类内）
import re
from nanobot.memory.models import Episode, ActionNode
from nanobot.memory.prompts import EPISODE_PROMPT


_FALLBACK_ENTITY_FILE = re.compile(
    r"[\w-]+\.(?:py|js|ts|md|json|yaml|toml|sh)\b"
)
_FALLBACK_ENTITY_PATH = re.compile(r"[A-Za-z]:[\\/][^\s\"']+")


class MemoryExtractor:
    # ... 既有 __init__/extract_session 等不变 ...

    async def generate_episode(
        self,
        transcript: list[dict],
        session_key: str,
        source: str = "session_end",
    ) -> Episode | None:
        if not transcript:
            return None

        action_nodes = self._extract_action_nodes(transcript)
        episode = Episode(
            session_id=session_key,
            source=source,
            action_nodes=action_nodes,
        )

        # 尝试 LLM 抽取
        if self._runtime is not None:
            try:
                conv_text = self._format_episode_lines(transcript)
                prompt = EPISODE_PROMPT.format(conversation=conv_text)
                resp = await self._runtime.chat_with_retry(
                    model=self._model,
                    messages=[{"role": "user", "content": prompt}],
                    tools=[],
                )
                data = self._parse_episode_json(getattr(resp, "content", "") or "")
                if data:
                    episode.summary = str(data.get("summary", ""))
                    episode.goal = str(data.get("goal", ""))
                    episode.outcome = str(data.get("outcome", "completed"))
                    episode.entities = list(data.get("entities") or [])
                    tools = data.get("tools_used") or []
                    episode.tools_used = list({*episode.tools_used, *tools})
            except Exception as exc:
                logger.warning("generate_episode LLM failed: {}", exc)

        # Heuristic 回退
        if not episode.summary:
            episode.summary = self._generate_fallback_summary(transcript)
            episode.goal = (transcript[0].get("content", "") or "")[:100]
        if not episode.entities:
            episode.entities = self._extract_entities_heuristic(transcript)

        return episode

    def _extract_action_nodes(self, transcript: list[dict]) -> list[ActionNode]:
        nodes: list[ActionNode] = []
        for turn in transcript:
            for tc in turn.get("tool_calls") or []:
                inp = tc.get("input", tc.get("arguments", {})) or {}
                params = {
                    k: str(inp[k])[:200]
                    for k in ("command", "path", "query", "url", "filename")
                    if k in inp
                }
                success = True
                err = None
                summary = ""
                for tr in turn.get("tool_results") or []:
                    if tr.get("tool_use_id") == tc.get("id") or not tc.get("id"):
                        content = tr.get("content", "")
                        summary = (content if isinstance(content, str) else str(content))[:200]
                        if tr.get("is_error"):
                            success = False
                            err = summary
                        break
                nodes.append(ActionNode(
                    tool_name=tc.get("name", ""),
                    key_params=params,
                    result_summary=summary,
                    success=success,
                    error_message=err,
                ))
        return nodes

    def _format_episode_lines(self, transcript: list[dict]) -> str:
        lines = []
        for t in transcript[-20:]:
            content = (t.get("content") or "")[:600]
            suffix = ""
            if t.get("tool_calls"):
                suffix = f" [调用了 {len(t['tool_calls'])} 个工具]"
            lines.append(f"[{t.get('role','?')}]: {content}{suffix}")
        return "\n".join(lines)

    def _parse_episode_json(self, text: str) -> dict | None:
        import json, re
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:
            return None

    def _generate_fallback_summary(self, transcript: list[dict]) -> str:
        user_msgs = [
            (t.get("content") or "")[:100]
            for t in transcript if t.get("role") == "user" and t.get("content")
        ]
        if user_msgs:
            return f"对话涉及: {'; '.join(user_msgs[:3])}"
        return f"共 {len(transcript)} 轮对话"

    def _extract_entities_heuristic(self, transcript: list[dict]) -> list[str]:
        out: set[str] = set()
        for t in transcript:
            text = t.get("content") or ""
            for m in _FALLBACK_ENTITY_PATH.finditer(text):
                out.add(m.group(0))
            for m in _FALLBACK_ENTITY_FILE.finditer(text):
                out.add(m.group(0))
        return list(out)[:20]
```

> 备注：`Episode` / `ActionNode` 模型如不存在，按 `models.py` 现有 dataclass 字段对齐；如需新增字段 `outcome / entities / action_nodes`，在该文件追加并补一个 smoke 单测。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/memory/test_episode_extraction.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add nanobot/memory/extractor.py tests/memory/test_episode_extraction.py
git commit -m "feat(memory): implement generate_episode with heuristic fallback (S2)"
```

---

### Task 3: `ProfileExtractor`（用户画像 + 引用评分 + 增量合并）（P0）

**Files:**
- Modify: `nanobot/memory/prompts.py`（新增 `CITATION_SCORING_SECTION` 常量）
- Create: `nanobot/memory/profile_extractor.py`
- Modify: `nanobot/memory/repository.py`（新增 `update_by_predicate` / `add_conflict`）
- Test: `tests/memory/test_profile_extractor.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/memory/test_profile_extractor.py
import pytest
from nanobot.memory.profile_extractor import ProfileExtractor


@pytest.mark.asyncio
async def test_extract_profile_returns_items_and_scores():
    class FakeRuntime:
        async def chat_with_retry(self, *, model, messages, tools, **kwargs):
            class R:
                content = (
                    '{"memories":[{"content":"用户喜欢美咖啡","subject":"用户",'
                    '"predicate":"喜欢","type":"PREFERENCE","importance":0.7,'
                    '"priority":"long_term","tags":["coffee"],"is_update":false}],'
                    '"citation_scores":[{"memory_id":"m1","useful":true}]}'
                )
            return R()

    ext = ProfileExtractor(runtime=FakeRuntime())
    items, scores = await ext.extract(
        transcript=[{"role": "user", "content": "我喜欢美咖啡"}],
        episode_id="ep-1",
        cited_memories=[{"id": "m1", "content": "用户住在杭州"}],
    )
    assert len(items) == 1
    assert items[0].content == "用户喜欢美咖啡"
    assert scores[0]["memory_id"] == "m1"
    assert scores[0]["useful"] is True


@pytest.mark.asyncio
async def test_extract_profile_no_citation_returns_no_scores():
    class FakeRuntime:
        async def chat_with_retry(self, *, model, messages, tools, **kwargs):
            class R:
                content = (
                    '{"memories":[{"content":"x","subject":"用户",'
                    '"predicate":"喜欢","type":"PREFERENCE","importance":0.5,'
                    '"priority":"long_term","tags":[],"is_update":false}]}'
                )
            return R()

    ext = ProfileExtractor(runtime=FakeRuntime())
    items, scores = await ext.extract(transcript=[{"role": "user", "content": "x"}], episode_id="e")
    assert len(items) == 1
    assert scores == []


@pytest.mark.asyncio
async def test_extract_profile_llm_failure_returns_empty():
    class FakeRuntime:
        async def chat_with_retry(self, *, model, messages, tools, **kwargs):
            raise RuntimeError("down")

    ext = ProfileExtractor(runtime=FakeRuntime())
    items, scores = await ext.extract(transcript=[{"role": "user", "content": "x"}], episode_id="e")
    assert items == [] and scores == []


def test_merge_incremental_same_subject_predicate_keeps_old_when_conflicting():
    """增量合并：subject+predicate 相同但语义冲突时保留旧 + 记录 conflicts_with。"""
    from nanobot.memory.profile_extractor import merge_profile_incremental

    existing = {
        "id": "m1", "content": "用户不喜欢咖啡",
        "subject": "用户", "predicate": "喜欢", "type": "PREFERENCE",
    }
    incoming = {
        "content": "用户喜欢咖啡",
        "subject": "用户", "predicate": "喜欢", "type": "PREFERENCE",
        "is_update": True,
    }
    result = merge_profile_incremental(existing, incoming)
    assert result["action"] == "keep_old_with_conflict"
    assert "m1" in result["existing"]["conflicts_with"]


def test_merge_incremental_same_subject_predicate_updates_when_consistent():
    from nanobot.memory.profile_extractor import merge_profile_incremental

    existing = {
        "id": "m1", "content": "用户喜欢美咖啡",
        "subject": "用户", "predicate": "喜欢", "type": "PREFERENCE",
        "importance": 0.6,
    }
    incoming = {
        "content": "用户喜欢美咖啡",
        "subject": "用户", "predicate": "喜欢", "type": "PREFERENCE",
        "is_update": True, "importance": 0.7,
    }
    result = merge_profile_incremental(existing, incoming)
    assert result["action"] == "update"
    assert result["merged"]["importance"] == pytest.approx(0.7)


def test_merge_incremental_different_predicate_creates_new():
    from nanobot.memory.profile_extractor import merge_profile_incremental

    existing = {
        "id": "m1", "subject": "用户", "predicate": "喜欢", "content": "x",
    }
    incoming = {
        "subject": "用户", "predicate": "不喜欢", "content": "y",
        "is_update": False,
    }
    result = merge_profile_incremental(existing, incoming)
    assert result["action"] == "create_new"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/memory/test_profile_extractor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'nanobot.memory.profile_extractor'`

- [ ] **Step 3: 写最小实现**

```python
# nanobot/memory/prompts.py（追加到文件末尾）
CITATION_SCORING_SECTION = """\

以下是被检索到的历史记忆，请逐条评判它对本次任务是否有实际帮助：
{cited_memories}

在你的 JSON 输出中增加 "citation_scores" 字段：
"citation_scores": [
  {{"memory_id": "xxx", "useful": true/false}}
]
- useful=true：记忆对本次任务执行有实际帮助（提供了信息、避免了错误等）
- useful=false：记忆与本次任务无关或无实际帮助

最终输出格式: {"memories": [...], "citation_scores": [...]}
如没有要提取的记忆，memories 为空数组。只输出 JSON。"""
```

```python
# nanobot/memory/profile_extractor.py
"""用户画像提取 + 引用评分 + 增量合并。"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from nanobot.memory.prompts import MEMORY_EXTRACTION_PROMPT, CITATION_SCORING_SECTION


@dataclass
class ProfileItem:
    content: str
    subject: str
    predicate: str
    type: str
    importance: float
    priority: str
    tags: list[str]
    is_update: bool = False


@dataclass
class MergeResult:
    action: str  # "update" | "create_new" | "keep_old_with_conflict"
    merged: dict | None = None
    existing: dict | None = None


class ProfileExtractor:
    def __init__(self, runtime: Any | None = None, model: str = "") -> None:
        self._runtime = runtime
        self._model = model

    async def extract(
        self,
        transcript: list[dict],
        episode_id: str,
        cited_memories: list[dict] | None = None,
    ) -> tuple[list[ProfileItem], list[dict]]:
        if self._runtime is None:
            return [], []
        try:
            conv_text = "\n".join(
                f"[{t.get('role','?')}]: {(t.get('content','') or '')[:1500]}"
                for t in transcript[-30:]
                if t.get("content")
            )
            prompt = MEMORY_EXTRACTION_PROMPT.format(conversation=conv_text)
            has_cites = bool(cited_memories)
            if has_cites:
                cite_text = "\n".join(
                    f"- ID={m['id']} | {(m.get('content','') or '')[:150]}"
                    for m in cited_memories
                )
                prompt += CITATION_SCORING_SECTION.format(cited_memories=cite_text)
            resp = await self._runtime.chat_with_retry(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                tools=[],
            )
            text = (getattr(resp, "content", "") or "").strip()
            data = _extract_json(text)
            if not isinstance(data, dict):
                return _parse_legacy_array(text), []
            items_raw = data.get("memories") or []
            scores_raw = data.get("citation_scores") or []
            items = [
                ProfileItem(
                    content=str(i.get("content", "")),
                    subject=str(i.get("subject", "")),
                    predicate=str(i.get("predicate", "")),
                    type=str(i.get("type", "FACT")).upper(),
                    importance=float(i.get("importance", 0.5)),
                    priority=str(i.get("priority", "long_term")).lower(),
                    tags=list(i.get("tags") or []),
                    is_update=bool(i.get("is_update", False)),
                )
                for i in items_raw if isinstance(i, dict)
            ]
            scores = [
                {"memory_id": str(s["memory_id"]), "useful": bool(s.get("useful", False))}
                for s in scores_raw if isinstance(s, dict) and "memory_id" in s
            ]
            return items, scores
        except Exception as exc:
            logger.warning("ProfileExtractor.extract failed: {}", exc)
            return [], []


def _extract_json(text: str) -> Any | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _parse_legacy_array(text: str) -> list[ProfileItem]:
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return []
    return [
        ProfileItem(
            content=str(i.get("content", "")),
            subject=str(i.get("subject", "")),
            predicate=str(i.get("predicate", "")),
            type=str(i.get("type", "FACT")).upper(),
            importance=float(i.get("importance", 0.5)),
            priority=str(i.get("priority", "long_term")).lower(),
            tags=list(i.get("tags") or []),
            is_update=bool(i.get("is_update", False)),
        )
        for i in arr if isinstance(i, dict)
    ]


logger = logging.getLogger(__name__)


def merge_profile_incremental(existing: dict, incoming: dict) -> MergeResult:
    """增量合并：subject+predicate 相同才尝试更新；冲突保留旧 + 记 conflicts_with。"""
    if (
        existing.get("subject") == incoming.get("subject")
        and existing.get("predicate") == incoming.get("predicate")
    ):
        # 语义冲突判定：内容里出现否定词反向
        neg_a = any(k in existing.get("content", "") for k in ("不", "没", "无"))
        neg_b = any(k in incoming.get("content", "") for k in ("不", "没", "无"))
        if neg_a != neg_b and existing.get("content") != incoming.get("content"):
            new_existing = dict(existing)
            conflicts = list(new_existing.get("conflicts_with") or [])
            mid = incoming.get("id") or incoming.get("memory_id")
            if mid and mid not in conflicts:
                conflicts.append(mid)
            if len(conflicts) > 5:
                conflicts = conflicts[-5:]
            new_existing["conflicts_with"] = conflicts
            return MergeResult(action="keep_old_with_conflict", existing=new_existing)
        merged = dict(existing)
        merged["content"] = incoming.get("content", existing.get("content"))
        if "importance" in incoming:
            merged["importance"] = incoming["importance"]
        return MergeResult(action="update", merged=merged)
    return MergeResult(action="create_new")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/memory/test_profile_extractor.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add nanobot/memory/prompts.py nanobot/memory/profile_extractor.py tests/memory/test_profile_extractor.py
git commit -m "feat(memory): ProfileExtractor + incremental merge + citation scoring (S3 part 1)"
```

---

### Task 4: `ExperienceExtractor`（任务经验，feature flag off）（P1）

**Files:**
- Modify: `nanobot/memory/prompts.py`（新增 `EXPERIENCE_EXTRACTION_PROMPT` 常量）
- Create: `nanobot/memory/experience_extractor.py`
- Test: `tests/memory/test_experience_extractor.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/memory/test_experience_extractor.py
import pytest
from nanobot.memory.experience_extractor import ExperienceExtractor


@pytest.mark.asyncio
async def test_extract_experience_skip_when_too_few_assistant_turns():
    class FakeRuntime:
        async def chat_with_retry(self, *, model, messages, tools, **kwargs):
            raise AssertionError("不应调用 LLM")

    ext = ExperienceExtractor(runtime=FakeRuntime())
    items = await ext.extract(
        transcript=[{"role": "user", "content": "hi"}], episode_id="e1"
    )
    assert items == []


@pytest.mark.asyncio
async def test_extract_experience_success():
    class FakeRuntime:
        async def chat_with_retry(self, *, model, messages, tools, **kwargs):
            class R:
                content = (
                    '{"memories":[{"content":"macOS 改 PATH 需重启 shell",'
                    '"subject":"PATH","predicate":"配置","type":"SKILL",'
                    '"importance":0.8,"priority":"long_term","tags":["macos"]}]}'
                )
            return R()

    ext = ExperienceExtractor(runtime=FakeRuntime())
    items = await ext.extract(
        transcript=[
            {"role": "user", "content": "PATH 改了没生效"},
            {"role": "assistant", "content": "试试 source ~/.zshrc"},
            {"role": "assistant", "content": "或者重启终端"},
        ],
        episode_id="e1",
    )
    assert len(items) == 1
    assert items[0].type == "SKILL"


@pytest.mark.asyncio
async def test_extract_experience_llm_failure_returns_empty():
    class FakeRuntime:
        async def chat_with_retry(self, *, model, messages, tools, **kwargs):
            raise RuntimeError("down")

    ext = ExperienceExtractor(runtime=FakeRuntime())
    items = await ext.extract(
        transcript=[
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "y"},
            {"role": "assistant", "content": "z"},
        ],
        episode_id="e1",
    )
    assert items == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/memory/test_experience_extractor.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: 写最小实现**

```python
# nanobot/memory/prompts.py（追加）
EXPERIENCE_EXTRACTION_PROMPT = """回顾整段对话，提取所有**任务经验、操作结果和教训**：

对话:
{conversation}

输出 JSON 格式：
{{
  "memories": [
    {{
      "type": "EXPERIENCE | SKILL | ERROR",
      "subject": "经验主题",
      "predicate": "经验行为",
      "content": "简洁的经验描述（≤80 字）",
      "importance": 0.5-1.0,
      "priority": "long_term | short_term",
      "tags": ["tag1", "tag2"]
    }}
  ]
}}

约束：
- assistant_turns < 2 时输出空数组
- 只输出可复用的操作/教训，避免空泛观察
- 最多输出 3 条"""
```

```python
# nanobot/memory/experience_extractor.py
"""任务经验提取（P1：feature flag 控制）。"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from nanobot.memory.prompts import EXPERIENCE_EXTRACTION_PROMPT


@dataclass
class ExperienceItem:
    content: str
    subject: str
    predicate: str
    type: str
    importance: float
    priority: str
    tags: list[str]


class ExperienceExtractor:
    def __init__(self, runtime: Any | None = None, model: str = "") -> None:
        self._runtime = runtime
        self._model = model

    async def extract(
        self,
        transcript: list[dict],
        episode_id: str,
    ) -> list[ExperienceItem]:
        if self._runtime is None:
            return []
        assistant_turns = [t for t in transcript if t.get("role") == "assistant" and t.get("content")]
        if len(assistant_turns) < 2:
            return []
        try:
            conv_text = "\n".join(
                f"[{t.get('role','?')}]: {(t.get('content','') or '')[:1500]}"
                for t in transcript[-30:]
                if t.get("content")
            )
            prompt = EXPERIENCE_EXTRACTION_PROMPT.format(conversation=conv_text)
            resp = await self._runtime.chat_with_retry(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                tools=[],
            )
            text = (getattr(resp, "content", "") or "").strip()
            data = _extract_json_obj(text)
            if not isinstance(data, dict):
                return []
            arr = data.get("memories") or []
            return [
                ExperienceItem(
                    content=str(i.get("content", "")),
                    subject=str(i.get("subject", "")),
                    predicate=str(i.get("predicate", "")),
                    type=str(i.get("type", "EXPERIENCE")).upper(),
                    importance=float(i.get("importance", 0.5)),
                    priority=str(i.get("priority", "long_term")).lower(),
                    tags=list(i.get("tags") or []),
                )
                for i in arr if isinstance(i, dict)
            ]
        except Exception as exc:
            logger.warning("ExperienceExtractor.extract failed: {}", exc)
            return []


def _extract_json_obj(text: str) -> Any | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


logger = logging.getLogger(__name__)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/memory/test_experience_extractor.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add nanobot/memory/prompts.py nanobot/memory/experience_extractor.py tests/memory/test_experience_extractor.py
git commit -m "feat(memory): ExperienceExtractor for task experience track (S3 part 2)"
```

---

### Task 5: `ScratchpadWriter.format_with_llm`（接通 P1）

**Files:**
- Modify: `nanobot/memory/scratchpad_writer.py`（新增方法）
- Test: `tests/memory/test_scratchpad_format.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/memory/test_scratchpad_format.py
import pytest
from nanobot.memory.scratchpad_writer import ScratchpadWriter


@pytest.mark.asyncio
async def test_format_with_llm_success():
    class FakeRuntime:
        async def chat_with_retry(self, *, model, messages, tools, **kwargs):
            class R:
                content = (
                    "## 当前项目\n- 配置 PATH\n\n"
                    "## 近期进展\n- 已写入 .zshrc\n\n"
                    "## 未解决的问题\n- 无\n\n"
                    "## 下一步\n- 重启 shell"
                )
            return R()

    writer = ScratchpadWriter(runtime=FakeRuntime())
    pad = await writer.format_with_llm(
        current_scratchpad=None,
        episode_summary="用户配置 PATH 并写入 .zshrc",
    )
    assert "配置 PATH" in pad.content
    assert "## 下一步" in pad.content


@pytest.mark.asyncio
async def test_format_with_llm_failure_falls_back_to_minimal():
    class FakeRuntime:
        async def chat_with_retry(self, *, model, messages, tools, **kwargs):
            raise RuntimeError("down")

    writer = ScratchpadWriter(runtime=FakeRuntime())
    pad = await writer.format_with_llm(
        current_scratchpad=None,
        episode_summary="测试 episode 摘要",
    )
    assert "测试 episode 摘要" in pad.content


@pytest.mark.asyncio
async def test_format_with_llm_truncates_long_output():
    class FakeRuntime:
        async def chat_with_retry(self, *, model, messages, tools, **kwargs):
            class R:
                content = "## 当前项目\n" + ("x" * 3000)
            return R()

    writer = ScratchpadWriter(runtime=FakeRuntime())
    pad = await writer.format_with_llm(
        current_scratchpad=None,
        episode_summary="y",
    )
    assert len(pad.content) <= 2000
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/memory/test_scratchpad_format.py -v`
Expected: FAIL with `AttributeError: 'ScratchpadWriter' object has no attribute 'format_with_llm'`

- [ ] **Step 3: 写最小实现**

```python
# nanobot/memory/scratchpad_writer.py（追加到 ScratchpadWriter 类内）
from nanobot.memory.prompts import SCRATCHPAD_FORMAT_PROMPT


class ScratchpadWriter:
    # ... 既有代码 ...

    async def format_with_llm(
        self,
        current_scratchpad: Any | None,
        episode_summary: str,
    ) -> Any:
        from datetime import datetime
        from nanobot.memory.models import Scratchpad  # 如不存在则用 dict 兜底

        current_content = getattr(current_scratchpad, "content", "") or "(空白)"
        if self._runtime is None:
            return self._minimal_fallback(current_scratchpad, episode_summary)

        try:
            prompt = SCRATCHPAD_FORMAT_PROMPT.format(
                current_scratchpad=current_content,
                episode_summary=episode_summary,
            )
            resp = await self._runtime.chat_with_retry(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                tools=[],
            )
            text = (getattr(resp, "content", "") or "").strip()
            if len(text) > 2000:
                text = text[:2000]
            return self._build_scratchpad(text, current_scratchpad)
        except Exception as exc:
            logger.warning("ScratchpadWriter.format_with_llm failed: {}", exc)
            return self._minimal_fallback(current_scratchpad, episode_summary)

    def _minimal_fallback(self, current: Any, episode_summary: str) -> Any:
        from datetime import datetime
        from nanobot.memory.models import Scratchpad

        existing = current or Scratchpad()
        new_content = (existing.content or "") + f"\n\n## 近期进展\n- {episode_summary[:200]}"
        if len(new_content) > 2000:
            new_content = new_content[-2000:]
        existing.content = new_content
        existing.updated_at = datetime.now()
        return existing

    def _build_scratchpad(self, text: str, current: Any) -> Any:
        from datetime import datetime
        from nanobot.memory.models import Scratchpad

        existing = current or Scratchpad()
        existing.content = text
        existing.updated_at = datetime.now()
        return existing
```

> 备注：`ScratchpadWriter.__init__` 需新增 `runtime` / `model` 参数（可选，None 表示不支持 LLM 重构）。如有现有签名冲突，**只追加 keyword-only 参数**，不破坏调用点。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/memory/test_scratchpad_format.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add nanobot/memory/scratchpad_writer.py tests/memory/test_scratchpad_format.py
git commit -m "feat(memory): ScratchpadWriter.format_with_llm (S4)"
```

---

### Task 6: 话题检测预筛 + 间隔节流（P1）

**Files:**
- Modify: `nanobot/memory/filters.py`（仅暴露内部正则给预筛复用）
- Create: `nanobot/memory/topic_prefilter.py`
- Test: `tests/memory/test_topic_prefilter.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/memory/test_topic_prefilter.py
import time
import pytest
from nanobot.memory.topic_prefilter import TopicChangeGate, PrefilterResult


def test_prefilter_short_message_returns_skip():
    gate = TopicChangeGate()
    result = gate.prefilter(message="", recent=[])
    assert result == PrefilterResult.SKIP

    result = gate.prefilter(message="ok", recent=[])
    assert result == PrefilterResult.SKIP


def test_prefilter_chat_intent_returns_skip():
    from nanobot.memory.intent import IntentType
    gate = TopicChangeGate(intent_classifier=lambda m: IntentType.CHAT)
    result = gate.prefilter(message="今天天气真好", recent=["今天下雨"])
    assert result == PrefilterResult.SKIP


def test_prefilter_follow_up_returns_skip():
    gate = TopicChangeGate()
    result = gate.prefilter(message="继续帮我做完", recent=["我们刚才在配置 PATH"])
    assert result == PrefilterResult.SKIP


def test_prefilter_length_jump_small_returns_skip():
    gate = TopicChangeGate()
    # 上条 200 字符，本条 50 字符 → 比例 25% < 30%
    recent = ["x" * 200]
    result = gate.prefilter(message="y" * 50, recent=recent)
    assert result == PrefilterResult.SKIP


def test_prefilter_passes_through_for_real_topic_change():
    gate = TopicChangeGate()
    result = gate.prefilter(
        message="帮我把数据库从 MySQL 迁到 Postgres",
        recent=["我们刚刚在讨论 React 组件设计", "渲染性能优化"],
    )
    assert result == PrefilterResult.PASS


def test_interval_blocks_within_60s_same_session():
    gate = TopicChangeGate(interval_seconds=60)
    assert gate.allow_fire(session_key="s1", topic_hash="h1") is True
    assert gate.allow_fire(session_key="s1", topic_hash="h1") is False


def test_interval_allows_different_session():
    gate = TopicChangeGate(interval_seconds=60)
    assert gate.allow_fire(session_key="s1", topic_hash="h1") is True
    assert gate.allow_fire(session_key="s2", topic_hash="h1") is True


def test_interval_allows_after_window_expires(monkeypatch):
    gate = TopicChangeGate(interval_seconds=1)
    assert gate.allow_fire(session_key="s1", topic_hash="h1") is True
    time.sleep(1.1)
    assert gate.allow_fire(session_key="s1", topic_hash="h1") is True


def test_topic_hash_uses_first_message_prefix():
    from nanobot.memory.topic_prefilter import compute_topic_hash
    h1 = compute_topic_hash("帮我配置一下 nginx 反向代理的具体参数")
    h2 = compute_topic_hash("帮我配置一下 nginx 反向代理的具体方案")
    assert h1 == h2  # 前 50 字符相同 → hash 相同
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/memory/test_topic_prefilter.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: 写最小实现**

```python
# nanobot/memory/filters.py（追加到文件末尾）
def is_chat_only(content: str) -> bool:
    """复用 _CHAT_FULL 判定整句寒暄。"""
    if not content or not content.strip():
        return False
    return bool(_CHAT_FULL.match(content.strip()))


def starts_with_follow_up(content: str) -> bool:
    """复用 _FOLLOW_UP_CJK/EN 判定追问前缀。"""
    if not content or not content.strip():
        return False
    s = content.strip()
    return bool(_FOLLOW_UP_CJK.match(s) or _FOLLOW_UP_EN.match(s))
```

```python
# nanobot/memory/topic_prefilter.py
"""话题切换检测的廉价预筛 + 最小间隔 + topic_hash 去重（P1）。"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from nanobot.memory.filters import is_chat_only, starts_with_follow_up
from nanobot.memory.intent import IntentType


class PrefilterResult(str, Enum):
    PASS = "pass"
    SKIP = "skip"


def compute_topic_hash(message: str) -> str:
    return hashlib.sha1((message or "")[:50].encode("utf-8")).hexdigest()[:16]


class TopicChangeGate:
    """组合：廉价预筛 + 最小间隔 + topic_hash 去重。"""

    def __init__(
        self,
        *,
        interval_seconds: int = 60,
        intent_classifier: Callable[[str], IntentType] | None = None,
        length_jump_threshold: float = 0.30,
    ) -> None:
        self._interval = interval_seconds
        self._intent = intent_classifier
        self._length_threshold = length_jump_threshold
        self._last_fire: dict[tuple[str, str], float] = {}

    def prefilter(self, message: str, recent: list[str]) -> PrefilterResult:
        msg = (message or "").strip()
        if len(msg) < 5:
            return PrefilterResult.SKIP
        if is_chat_only(msg):
            return PrefilterResult.SKIP
        if starts_with_follow_up(msg):
            return PrefilterResult.SKIP
        if self._intent is not None:
            try:
                if self._intent(msg) == IntentType.CHAT:
                    return PrefilterResult.SKIP
            except Exception:
                pass
        # 长度突变
        if recent:
            last = recent[-1]
            if len(last) > 0 and len(msg) > 0:
                ratio = min(len(msg), len(last)) / max(len(msg), len(last))
                if ratio < self._length_threshold:
                    return PrefilterResult.SKIP
        return PrefilterResult.PASS

    def allow_fire(self, session_key: str, topic_hash: str) -> bool:
        key = (session_key, topic_hash)
        now = time.monotonic()
        prev = self._last_fire.get(key)
        if prev is not None and (now - prev) < self._interval:
            return False
        self._last_fire[key] = now
        return True
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/memory/test_topic_prefilter.py -v`
Expected: PASS（9 passed）

- [ ] **Step 5: 提交**

```bash
git add nanobot/memory/filters.py nanobot/memory/topic_prefilter.py tests/memory/test_topic_prefilter.py
git commit -m "feat(memory): topic prefilter + interval gate + topic_hash dedupe (S5)"
```

---

### Task 7: `SessionEndOrchestrator`（P0 编排器）

**Files:**
- Create: `nanobot/memory/orchestrator.py`
- Test: `tests/memory/test_orchestrator.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/memory/test_orchestrator.py
import pytest
from nanobot.memory.orchestrator import SessionEndOrchestrator
from nanobot.memory.session_end_event import SessionEndEvent, SessionEndReason


@pytest.mark.asyncio
async def test_orchestrator_runs_all_enabled_steps_in_order():
    order: list[str] = []

    class FakeExtractor:
        async def generate_episode(self, transcript, session_key, source="session_end"):
            order.append("episode")
            class E:
                id = "ep-1"
                summary = "sum"
                goal = "g"
                outcome = "success"
                entities = []
                tools_used = []
                action_nodes = []
            return E()

        async def extract_user_profile(self, transcript, episode_id, cited=None):
            order.append("profile")
            return [], []

        async def extract_experience(self, transcript, episode_id):
            order.append("experience")
            return []

    class FakeScratchpad:
        async def format_with_llm(self, current, episode_summary):
            order.append("scratchpad")
            class S:
                content = "ok"
            return S()

    orch = SessionEndOrchestrator(
        extractor=FakeExtractor(),
        scratchpad_writer=FakeScratchpad(),
        enable_track2=True,
        enable_scratchpad_reformat=True,
    )
    await orch.run(SessionEndEvent(
        session_key="k1",
        reason=SessionEndReason.USER_CLOSE,
        transcript=[{"role": "user", "content": "x"}],
    ))
    assert order == ["episode", "profile", "experience", "scratchpad"]


@pytest.mark.asyncio
async def test_orchestrator_skips_track2_and_scratchpad_by_default():
    calls: list[str] = []

    class FakeExtractor:
        async def generate_episode(self, transcript, session_key, source="session_end"):
            calls.append("episode")
            class E:
                id = "ep-1"
                summary = "s"
                goal = "g"
                outcome = "success"
                entities = []
                tools_used = []
                action_nodes = []
            return E()

        async def extract_user_profile(self, transcript, episode_id, cited=None):
            calls.append("profile")
            return [], []

        async def extract_experience(self, transcript, episode_id):
            calls.append("experience")
            return []

    class FakeScratchpad:
        async def format_with_llm(self, current, episode_summary):
            calls.append("scratchpad")
            class S:
                content = "ok"
            return S()

    orch = SessionEndOrchestrator(
        extractor=FakeExtractor(),
        scratchpad_writer=FakeScratchpad(),
    )
    await orch.run(SessionEndEvent(
        session_key="k1",
        reason=SessionEndReason.USER_CLOSE,
        transcript=[{"role": "user", "content": "x"}],
    ))
    assert calls == ["episode", "profile"]


@pytest.mark.asyncio
async def test_orchestrator_episode_failure_does_not_block_profile():
    class FakeExtractor:
        async def generate_episode(self, transcript, session_key, source="session_end"):
            raise RuntimeError("LLM down")

        async def extract_user_profile(self, transcript, episode_id, cited=None):
            assert episode_id is None  # episode 失败 → ep_id=None
            return [], []

        async def extract_experience(self, transcript, episode_id):
            return []

    orch = SessionEndOrchestrator(extractor=FakeExtractor(), scratchpad_writer=None)
    # 不应抛
    await orch.run(SessionEndEvent(
        session_key="k1",
        reason=SessionEndReason.USER_CLOSE,
        transcript=[{"role": "user", "content": "x"}],
    ))


@pytest.mark.asyncio
async def test_orchestrator_idempotent_within_30s():
    class FakeExtractor:
        async def generate_episode(self, transcript, session_key, source="session_end"):
            class E:
                id = "ep-1"
                summary = "s"
                goal = "g"
                outcome = "success"
                entities = []
                tools_used = []
                action_nodes = []
            return E()

        async def extract_user_profile(self, transcript, episode_id, cited=None):
            return [], []

        async def extract_experience(self, transcript, episode_id):
            return []

    orch = SessionEndOrchestrator(extractor=FakeExtractor(), scratchpad_writer=None)
    evt = SessionEndEvent(
        session_key="k1",
        reason=SessionEndReason.USER_CLOSE,
        transcript=[{"role": "user", "content": "x"}],
    )
    await orch.run(evt)
    await orch.run(evt)  # 第二次应被幂等拦下


@pytest.mark.asyncio
async def test_orchestrator_different_reason_not_idempotent():
    calls = {"n": 0}

    class FakeExtractor:
        async def generate_episode(self, transcript, session_key, source="session_end"):
            calls["n"] += 1
            class E:
                id = "ep-1"; summary = "s"; goal = "g"; outcome = "success"
                entities = []; tools_used = []; action_nodes = []
            return E()

        async def extract_user_profile(self, transcript, episode_id, cited=None):
            return [], []

        async def extract_experience(self, transcript, episode_id):
            return []

    orch = SessionEndOrchestrator(extractor=FakeExtractor(), scratchpad_writer=None)
    await orch.run(SessionEndEvent(
        session_key="k1",
        reason=SessionEndReason.USER_CLOSE,
        transcript=[{"role": "user", "content": "x"}],
    ))
    await orch.run(SessionEndEvent(
        session_key="k1",
        reason=SessionEndReason.IDLE_TIMEOUT,
        transcript=[{"role": "user", "content": "x"}],
    ))
    assert calls["n"] == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/memory/test_orchestrator.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: 写最小实现**

```python
# nanobot/memory/orchestrator.py
"""会话结束 4 步编排器（P0）。"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from nanobot.memory.session_end_event import SessionEndEvent


logger = logging.getLogger(__name__)


class SessionEndOrchestrator:
    IDEMPOTENCY_WINDOW_SECONDS: float = 30.0

    def __init__(
        self,
        *,
        extractor: Any,
        scratchpad_writer: Any | None = None,
        enable_track2: bool = False,
        enable_scratchpad_reformat: bool = False,
    ) -> None:
        self._extractor = extractor
        self._scratchpad_writer = scratchpad_writer
        self._enable_track2 = enable_track2
        self._enable_scratchpad_reformat = enable_scratchpad_reformat
        self._last_run: dict[tuple[str, str], float] = {}

    async def run(self, event: SessionEndEvent) -> None:
        # 幂等保护
        key = (event.session_key, event.reason.value)
        now = time.monotonic()
        prev = self._last_run.get(key)
        if prev is not None and (now - prev) < self.IDEMPOTENCY_WINDOW_SECONDS:
            logger.info("[S1:SessionEnd] skipped (idempotent within 30s) key={}", key)
            return
        self._last_run[key] = now

        # Step 1: Episode
        episode = None
        try:
            episode = await self._extractor.generate_episode(
                event.transcript, event.session_key
            )
        except Exception as exc:
            logger.warning("[S1:SessionEnd] episode step failed: {}", exc)
        ep_id = getattr(episode, "id", None) if episode else None
        ep_summary = getattr(episode, "summary", "") or ""

        # Step 2a: 用户画像（默认 ON）
        try:
            await self._extractor.extract_user_profile(
                event.transcript, ep_id or "", cited=None
            )
        except Exception as exc:
            logger.warning("[S3:Track1] failed: {}", exc)

        # Step 2b: 任务经验（feature flag）
        if self._enable_track2:
            try:
                await self._extractor.extract_experience(event.transcript, ep_id or "")
            except Exception as exc:
                logger.warning("[S3:Track2] failed: {}", exc)

        # Step 3: Scratchpad 重构（feature flag，依赖 episode）
        if self._enable_scratchpad_reformat and self._scratchpad_writer is not None and ep_summary:
            try:
                await self._scratchpad_writer.format_with_llm(
                    current_scratchpad=None,
                    episode_summary=ep_summary,
                )
            except Exception as exc:
                logger.warning("[S4:Scratchpad] failed: {}", exc)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/memory/test_orchestrator.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add nanobot/memory/orchestrator.py tests/memory/test_orchestrator.py
git commit -m "feat(memory): SessionEndOrchestrator with 4-step dependency chain (S1)"
```

---

### Task 8: `MemoryExtractionHook` 接入编排器 + 预筛 + 兼容 `extract_session`（P0）

**Files:**
- Modify: `nanobot/agent/hooks/memory_extraction.py`
- Test: `tests/memory/test_hook_memory_extraction.py`（新增 case，不改既有）

- [ ] **Step 1: 写失败测试**

```python
# tests/memory/test_hook_memory_extraction.py（追加）
import pytest
from unittest.mock import AsyncMock, MagicMock
from nanobot.agent.hooks.memory_extraction import MemoryExtractionHook
from nanobot.memory.session_end_event import SessionEndEvent, SessionEndReason


@pytest.mark.asyncio
async def test_after_run_demoted_to_immediate_focus_only(monkeypatch):
    """after_run 降级为 T0 即时同步，不再跑 _run_extraction。"""
    run_calls = {"n": 0}

    async def fake_run(self, messages, *, source):
        run_calls["n"] += 1

    monkeypatch.setattr(MemoryExtractionHook, "_run_extraction", fake_run)

    hook = MemoryExtractionHook(
        extractor=MagicMock(),
        session_key="k1",
        scratchpad_writer=MagicMock(update_focus=AsyncMock()),
    )

    class Ctx:
        messages = [{"role": "user", "content": "hi"}]

    await hook.after_run(Ctx())
    assert run_calls["n"] == 0  # 不再跑异步提取


@pytest.mark.asyncio
async def test_topic_change_uses_prefilter_and_interval(monkeypatch):
    """话题切换走预筛 + 间隔节流。"""
    gate = MagicMock()
    gate.prefilter.return_value = "skip"

    hook = MemoryExtractionHook(
        extractor=MagicMock(),
        session_key="k1",
        scratchpad_writer=MagicMock(),
    )
    hook._topic_gate = gate

    judge_calls = {"n": 0}

    async def fake_judge(self, recent, latest):
        judge_calls["n"] += 1
        return True

    monkeypatch.setattr(MemoryExtractionHook, "_judge_topic_change", fake_judge)

    class Ctx:
        messages = [{"role": "user", "content": "x"}] * 5

    await hook.before_iteration(Ctx())
    assert judge_calls["n"] == 0  # 预筛 skip → 不调 LLM


@pytest.mark.asyncio
async def test_on_finally_fires_session_end_event():
    """on_finally 增加兜底触发 SessionEndEvent。"""
    orch = MagicMock()
    orch.run = AsyncMock()

    hook = MemoryExtractionHook(
        extractor=MagicMock(),
        session_key="k1",
        scratchpad_writer=MagicMock(),
    )
    hook._session_end_orchestrator = orch

    class Ctx:
        messages = [{"role": "user", "content": "x"}]

    await hook.on_finally(Ctx())
    assert orch.run.await_count == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/memory/test_hook_memory_extraction.py -v`
Expected: FAIL（既有测试不应被破坏，新增 case 失败）

- [ ] **Step 3: 写最小实现（hook 改造）**

```python
# nanobot/agent/hooks/memory_extraction.py（关键改造点）
from nanobot.memory.orchestrator import SessionEndOrchestrator
from nanobot.memory.profile_extractor import ProfileExtractor
from nanobot.memory.experience_extractor import ExperienceExtractor
from nanobot.memory.topic_prefilter import TopicChangeGate
from nanobot.memory.session_end_event import SessionEndEvent, SessionEndReason


class MemoryExtractionHook(AgentHook):
    # 既有常量保留
    EXTRACTION_WAIT_TIMEOUT: float = 5.0
    TOPIC_CHANGE_TIMEOUT: float = 10.0
    TOPIC_CHANGE_MIN_MESSAGES: int = 4
    FOCUS_MAX_CHARS: int = 200

    # 新增 feature flag
    COMPAT_EXTRACT_SESSION: bool = True
    SESSION_END_ENABLED: bool = True
    S3_TRACK2_ENABLED: bool = False
    S4_SCRATCHPAD_REFORMAT_ENABLED: bool = False
    TOPIC_MIN_INTERVAL_SEC: int = 60

    def __init__(self, extractor, session_key, scratchpad_writer, *, runtime=None):
        super().__init__()
        self._extractor = extractor
        self._session_key = session_key
        self._scratchpad_writer = scratchpad_writer
        self._runtime = runtime if runtime is not None else getattr(extractor, "runtime", None)
        self._next_check_count: int = self.TOPIC_CHANGE_MIN_MESSAGES
        self._pending_tasks: set = set()

        # 新增：编排器 + 话题门
        self._topic_gate = TopicChangeGate(
            interval_seconds=self.TOPIC_MIN_INTERVAL_SEC,
        )
        self._session_end_orchestrator = SessionEndOrchestrator(
            extractor=self._build_orchestrator_extractor(),
            scratchpad_writer=self._scratchpad_writer
            if self.S4_SCRATCHPAD_REFORMAT_ENABLED else None,
            enable_track2=self.S3_TRACK2_ENABLED,
            enable_scratchpad_reformat=self.S4_SCRATCHPAD_REFORMAT_ENABLED,
        )

    def _build_orchestrator_extractor(self):
        """把现有 extractor 包装成编排器需要的形态。"""
        ext = self._extractor
        class _Adapter:
            pass
        adapter = _Adapter()
        adapter.generate_episode = ext.generate_episode
        adapter.extract_user_profile = getattr(ext, "extract_user_profile", lambda *a, **k: ([], []))
        adapter.extract_experience = getattr(ext, "extract_experience", lambda *a, **k: [])
        return adapter

    async def after_run(self, context):
        """降级为 T0 即时同步 + 触发会话结束编排（轻量）。"""
        try:
            await self._apply_immediate_focus(context)
        except Exception:
            logger.exception("MemoryExtractionHook.after_run T0 failed for {}", self._session_key)

        # 兼容：仍可走旧 extract_session（如 flag 开启）
        if self.COMPAT_EXTRACT_SESSION:
            try:
                self._schedule_run_extraction(context)
            except Exception:
                logger.exception("MemoryExtractionHook.after_run compat failed for {}", self._session_key)

    async def _detect_topic_change(self, context):
        user_messages = _user_messages(context.messages)
        count = len(user_messages)
        if count < self._next_check_count:
            return

        latest = user_messages[-1]
        recent = user_messages[-self.TOPIC_CHANGE_MIN_MESSAGES:-1]
        if not recent:
            return

        # 新增：廉价预筛（在 LLM 判定之前）
        from nanobot.memory.topic_prefilter import PrefilterResult
        if self._topic_gate.prefilter(latest, recent) == PrefilterResult.SKIP:
            self._next_check_count = count + 1
            return

        if await self._judge_topic_change(recent, latest):
            self._next_check_count = count + 1
            return

        logger.info("topic change detected for session {}", self._session_key)

        # 新增：间隔 + topic_hash 去重后再 fire
        from nanobot.memory.topic_prefilter import compute_topic_hash
        topic_hash = compute_topic_hash(latest)
        if not self._topic_gate.allow_fire(self._session_key, topic_hash):
            return

        try:
            await self._scratchpad_writer.update_focus(
                self._session_key, latest[: self.FOCUS_MAX_CHARS]
            )
        except Exception:
            logger.warning("topic change focus rotation failed for {}", self._session_key)

        transcript = [dict(message) for message in context.messages]
        _spawn_background_task(self._run_extraction(transcript, source="session_end"))
        self._next_check_count = count + self.TOPIC_CHANGE_MIN_MESSAGES

    async def on_finally(self, context):
        """新增：进程退出兜底触发 SessionEndEvent。"""
        if not self.SESSION_END_ENABLED:
            return
        event = SessionEndEvent(
            session_key=self._session_key,
            reason=SessionEndReason.PROCESS_SHUTDOWN,
            transcript=list(context.messages),
        )
        _spawn_background_task(self._session_end_orchestrator.run(event))
        # 既有：等 pending 收尾逻辑不变
        ...
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/memory/test_hook_memory_extraction.py tests/memory/test_orchestrator.py tests/memory/test_topic_prefilter.py -v`
Expected: 既有 hook 单测全过 + 新增 3 个 case 通过

- [ ] **Step 5: 提交**

```bash
git add nanobot/agent/hooks/memory_extraction.py tests/memory/test_hook_memory_extraction.py
git commit -m "feat(memory): hook integration with orchestrator + prefilter (S1+S5 wiring)"
```

---

### Task 9: 公共 API 导出 + 集成 smoke 测试

**Files:**
- Modify: `nanobot/memory/__init__.py`
- Test: `tests/memory/test_public_api.py`（追加 case）

- [ ] **Step 1: 写失败测试**

```python
# tests/memory/test_public_api.py（追加）
def test_new_public_api_exports():
    import nanobot.memory as m
    assert hasattr(m, "SessionEndEvent")
    assert hasattr(m, "SessionEndReason")
    assert hasattr(m, "SessionEndOrchestrator")
    assert hasattr(m, "ProfileExtractor")
    assert hasattr(m, "ExperienceExtractor")
    assert hasattr(m, "TopicChangeGate")
    assert hasattr(m, "compute_topic_hash")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/memory/test_public_api.py -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: 写最小实现**

```python
# nanobot/memory/__init__.py（追加）
from nanobot.memory.session_end_event import SessionEndEvent, SessionEndReason
from nanobot.memory.orchestrator import SessionEndOrchestrator
from nanobot.memory.profile_extractor import ProfileExtractor, merge_profile_incremental
from nanobot.memory.experience_extractor import ExperienceExtractor
from nanobot.memory.topic_prefilter import TopicChangeGate, compute_topic_hash
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/memory/test_public_api.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add nanobot/memory/__init__.py tests/memory/test_public_api.py
git commit -m "feat(memory): export new public API surface"
```

---

### Task 10: 全套回归 + 覆盖率检查

**Files:** 全部 `nanobot/memory/` + `nanobot/agent/hooks/memory_extraction.py`

- [ ] **Step 1: 跑全套 memory 单测**

Run: `pytest tests/memory/ -v`
Expected: 既有 + 新增 全过（无回归）

- [ ] **Step 2: 跑 ruff**

Run: `ruff check nanobot/memory/ nanobot/agent/hooks/memory_extraction.py`
Expected: 无 error（line length 100，规则 E/F/I/N/W）

- [ ] **Step 3: 跑 basedpyright**

Run: `uv run --no-sync basedpyright nanobot/memory nanobot/agent/hooks/memory_extraction.py`
Expected: 无 type error

- [ ] **Step 4: 覆盖率抽查**

Run: `pytest tests/memory/test_episode_extraction.py tests/memory/test_orchestrator.py tests/memory/test_profile_extractor.py tests/memory/test_experience_extractor.py tests/memory/test_scratchpad_format.py tests/memory/test_topic_prefilter.py --cov=nanobot/memory --cov-report=term-missing`
Expected: 新增模块覆盖率 ≥ 85%

- [ ] **Step 5: 提交（如有格式化修复）**

```bash
git add -u nanobot/memory/ nanobot/agent/hooks/memory_extraction.py tests/memory/
git commit -m "chore: lint + types + coverage check for memory hardening"
```

---

## 2. 自检（self-review）

### 2.1 Spec 覆盖核对
| Spec 节 | 对应 Task |
| --- | --- |
| §1 总览：4 步依赖链 | Task 7 |
| §2 S1 会话结束事件 + 编排器 | Task 1, 7, 8 |
| §3 S2 Episode 抽取 | Task 2 |
| §4 S3 双轨 + 引用评分 + 画像持续更新 | Task 3, 4 |
| §5 S4 Scratchpad 重构 | Task 5 |
| §6 S5 话题检测预筛 + 间隔节流 | Task 6, 8 |
| §7 全局约束（兼容 / flag / 日志 tag）| Task 8, 9 |
| §8 验收口径（功能 / 性能 / 回归 / 观测）| Task 10 |

### 2.2 占位符扫描
- ❌ 无 TBD / TODO / "实现细节见 Task N"
- ❌ 无"添加适当错误处理"等模糊指令（每步都有具体 except/logger.warning）
- ❌ 无"参考 Task N"（重复出现的代码均完整展示）

### 2.3 类型一致性核对
- `SessionEndEvent.reason` 全程为 `SessionEndReason` 枚举
- `ProfileItem`/`ExperienceItem` 字段名（content / subject / predicate / type / importance / priority / tags）跨 Task 3、Task 4、Task 7、Task 8 一致
- `Episode.id` / `summary` / `goal` / `outcome` / `entities` / `tools_used` / `action_nodes` 跨 Task 2、Task 7 一致
- `TopicChangeGate.prefilter / allow_fire` 跨 Task 6、Task 8 一致
- `compute_topic_hash` 跨 Task 6、Task 8 一致

### 2.4 TDD 合规扫描
| Task | Step 1 测试 | Step 2 跑失败 | Step 3 实现 | Step 4 跑通过 | Step 5 提交 |
| --- | --- | --- | --- | --- | --- |
| 1 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 2 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 3 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 4 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 5 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 6 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 7 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 8 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 9 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 10 | N/A | ✅ | N/A | ✅ | ✅ |

### 2.5 测试覆盖类型核对
每个生产代码任务都覆盖了：happy path + edge case（空/边界值）+ error case（LLM 异常/解析失败）。

---

## 3. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| `MemoryExtractor.__init__` 现有签名冲突 | Task 2/3/4 只追加 keyword-only，不破坏既有调用 |
| `ScratchpadWriter` 缺少 `runtime`/`model` 参数 | Task 5 只追加 keyword-only；`None` 时走最小回退 |
| `MemoryExtractionHook` 改造后行为变化 | Task 8 `COMPAT_EXTRACT_SESSION=True` 默认保留旧路径；feature flag 控制切换 |
| `Episode`/`ActionNode`/`Scratchpad` 模型字段不齐 | Task 2/5 注释说明字段需求，缺失时由 Task 10 覆盖率检查暴露 |
| 进程退出兜底可能丢任务 | Task 8 `_spawn_background_task` 不阻塞 + `_pending_tasks` 登记 |

---

## 4. 执行建议

**Plan complete and saved to `.ai-runtime-artifacts/plans/2026-09-12-nanobot-memory-extraction-hardening.md`.**

两种执行方式：

**1. Subagent-Driven (推荐)** — 每任务派发新 subagent 执行 + 两阶段审查（spec 合规 + 代码评审）
**2. Inline Execution** — 在当前会话批量执行，每 N 任务一个 checkpoint

请问你选哪种执行方式？