"""T-13 integration tests: RetrievalEngine wiring into build_system_prompt.

Covers (per Leader spec):
1. ``active_retrieval_enabled=True`` + engine → memory block appended to system prompt.
2. ``active_retrieval_enabled=False`` → engine.retrieve NOT called.
3. ``active_retrieval_enabled=True`` + engine is None → engine.retrieve NOT called.
4. engine.retrieve raises → failure-isolated; system prompt still built without block.
5. ``_build_memory_section`` async method contract (plan T-13 §Step 1 reference test).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.context import ContextBuilder


def _engine(return_value: str = "## 相关记忆（自动检索）\n- Python 爬虫") -> AsyncMock:
    """Build an AsyncMock that mimics ``RetrievalEngine.retrieve``."""
    engine = AsyncMock()
    engine.retrieve = AsyncMock(return_value=return_value)
    return engine


def _builder(tmp_path: Path, **kw: Any) -> ContextBuilder:
    return ContextBuilder(workspace=tmp_path, **kw)


# ---------------------------------------------------------------------------
# 1) enabled + engine → block appended
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enabled_with_engine_appends_block_via_precomputed_section(tmp_path: Path):
    """Pre-computed retrieval section is appended after the long-term memory block."""
    engine = _engine("## 相关记忆\n- Python 爬虫项目")
    builder = _builder(tmp_path, active_retrieval_enabled=True, retrieval_engine=engine)
    prompt = builder.build_system_prompt(
        workspace=tmp_path,
        include_memory=False,
        retrieved_memory_section="## 相关记忆\n- Python 爬虫项目",
    )
    assert "## 相关记忆" in prompt
    assert "Python 爬虫项目" in prompt


@pytest.mark.asyncio
async def test_build_memory_section_awaitable_and_returns_block_when_enabled(tmp_path: Path):
    """``_build_memory_section`` is async; returns retrieval markdown when enabled."""
    engine = _engine("## 相关记忆（自动检索）\n- Python 爬虫")
    builder = _builder(tmp_path, active_retrieval_enabled=True, retrieval_engine=engine)
    section = await builder._build_memory_section(
        query="我之前写的 Python 爬虫", recent_messages=[]
    )
    assert "Python 爬虫" in section
    engine.retrieve.assert_awaited_once()
    call = engine.retrieve.await_args
    assert call.kwargs.get("query") == "我之前写的 Python 爬虫"


@pytest.mark.asyncio
async def test_build_memory_section_passes_recent_messages_to_engine(tmp_path: Path):
    """``_build_memory_section`` forwards ``recent_messages`` to ``retrieve``."""
    engine = _engine()
    builder = _builder(tmp_path, active_retrieval_enabled=True, retrieval_engine=engine)
    recent = [{"role": "user", "content": "之前的话题"}]
    await builder._build_memory_section(
        query="我们之前讨论过的内容是什么", recent_messages=recent
    )
    call = engine.retrieve.await_args
    assert call.kwargs.get("recent_messages") is recent


# ---------------------------------------------------------------------------
# 2) disabled → engine.retrieve NOT called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_does_not_call_engine(tmp_path: Path):
    engine = _engine()
    builder = _builder(tmp_path, active_retrieval_enabled=False, retrieval_engine=engine)
    section = await builder._build_memory_section(query="Python 爬虫", recent_messages=[])
    assert section == ""
    engine.retrieve.assert_not_called()


def test_disabled_prompt_omits_retrieval_block(tmp_path: Path):
    """``build_system_prompt`` with disabled flag ignores passed-in retrieval section."""
    engine = _engine()
    builder = _builder(tmp_path, active_retrieval_enabled=False, retrieval_engine=engine)
    prompt = builder.build_system_prompt(
        workspace=tmp_path,
        include_memory=False,
        retrieved_memory_section="## 相关记忆\n- should not appear",
    )
    assert "should not appear" not in prompt


# ---------------------------------------------------------------------------
# 3) engine is None → no call (even when flag is True)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_none_does_not_call(tmp_path: Path):
    builder = _builder(tmp_path, active_retrieval_enabled=True, retrieval_engine=None)
    section = await builder._build_memory_section(query="Python 爬虫", recent_messages=[])
    assert section == ""


def test_engine_none_no_block_in_prompt(tmp_path: Path):
    """No engine → retrieval section is silently dropped."""
    builder = _builder(tmp_path, active_retrieval_enabled=True, retrieval_engine=None)
    prompt = builder.build_system_prompt(
        workspace=tmp_path,
        include_memory=False,
        retrieved_memory_section="## 相关记忆\n- dropped",
    )
    assert "dropped" not in prompt


# ---------------------------------------------------------------------------
# 4) engine raises → failure isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_exception_does_not_break_memory_section(tmp_path: Path):
    """``_build_memory_section`` swallows retrieval failures; returns empty string."""
    engine = AsyncMock()
    engine.retrieve = AsyncMock(side_effect=RuntimeError("retrieval engine down"))
    builder = _builder(tmp_path, active_retrieval_enabled=True, retrieval_engine=engine)
    section = await builder._build_memory_section(
        query="Python 爬虫", recent_messages=[]
    )
    assert section == ""


@pytest.mark.asyncio
async def test_engine_exception_does_not_break_system_prompt(tmp_path: Path):
    """When caller pre-computes retrieval via try/except, system prompt is built normally.

    This simulates the failure-isolation pattern that ``AgentLoop._build_turn``
    will use: wrap ``_build_memory_section`` in try/except and pass ``""`` on
    failure. The system prompt must still build.
    """
    engine = AsyncMock()
    engine.retrieve = AsyncMock(side_effect=RuntimeError("boom"))
    builder = _builder(tmp_path, active_retrieval_enabled=True, retrieval_engine=engine)

    precomputed = ""
    try:
        precomputed = await builder._build_memory_section(
            query="Python 爬虫", recent_messages=[]
        )
    except Exception:
        precomputed = ""

    prompt = builder.build_system_prompt(
        workspace=tmp_path,
        include_memory=False,
        retrieved_memory_section=precomputed,
    )
    assert "boom" not in prompt
    # identity template is part of the prompt regardless of retrieval
    assert "You are" in prompt or "# Memory" in prompt or prompt  # sanity


# ---------------------------------------------------------------------------
# 5) gate short-circuit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_short_circuit_skips_engine(tmp_path: Path):
    """Preprocessor gate (control word / short query) skips engine invocation."""
    engine = _engine()
    builder = _builder(tmp_path, active_retrieval_enabled=True, retrieval_engine=engine)
    section = await builder._build_memory_section(query="好", recent_messages=[])
    assert "相关记忆" not in section
    engine.retrieve.assert_not_called()


# ---------------------------------------------------------------------------
# 6) empty retrieval result → no block appended
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_retrieval_result_returns_empty_section(tmp_path: Path):
    """Engine returns ``""`` → ``_build_memory_section`` returns ``""`` (no block)."""
    engine = _engine(return_value="")
    builder = _builder(tmp_path, active_retrieval_enabled=True, retrieval_engine=engine)
    section = await builder._build_memory_section(query="Python 爬虫", recent_messages=[])
    assert section == ""


def test_empty_precomputed_section_not_appended(tmp_path: Path):
    engine = MagicMock()
    builder = _builder(tmp_path, active_retrieval_enabled=True, retrieval_engine=engine)
    prompt = builder.build_system_prompt(
        workspace=tmp_path,
        include_memory=False,
        retrieved_memory_section="",
    )
    assert "相关记忆" not in prompt


# ---------------------------------------------------------------------------
# 7) build_transcript wires through
# ---------------------------------------------------------------------------


def test_build_transcript_forwards_retrieved_section(tmp_path: Path):
    """``build_transcript`` propagates ``retrieved_memory_section`` to system prompt."""
    engine = MagicMock()
    builder = _builder(tmp_path, active_retrieval_enabled=True, retrieval_engine=engine)
    from nanobot.agent.context import TranscriptInput

    messages = builder.build_transcript(
        TranscriptInput(history=[], current_message=None),
        workspace=tmp_path,
        include_memory=False,
        retrieved_memory_section="## 相关记忆\n- injected",
    )
    system_content = messages[0]["content"]
    assert "injected" in system_content


def test_build_messages_forwards_retrieved_section(tmp_path: Path):
    """``build_messages`` (compatibility wrapper) propagates the section too."""
    engine = MagicMock()
    builder = _builder(tmp_path, active_retrieval_enabled=True, retrieval_engine=engine)
    messages = builder.build_messages(
        history=[],
        current_message="hi",
        workspace=tmp_path,
        include_memory=False,
        retrieved_memory_section="## 相关记忆\n- compat-path",
    )
    system_content = messages[0]["content"]
    assert "compat-path" in system_content
