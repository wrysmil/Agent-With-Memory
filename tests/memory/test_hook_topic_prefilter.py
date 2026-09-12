"""MemoryExtractionHook 接入编排器 + 话题预筛 单测（Task 8 追加 case）。"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


class _Ctx:
    def __init__(self, messages):
        self.messages = messages


@pytest.mark.asyncio
async def test_topic_change_uses_prefilter(monkeypatch):
    """话题切换走预筛，预筛 skip → 不调 LLM 判定。"""
    from nanobot.agent.hooks.memory_extraction import MemoryExtractionHook
    from nanobot.memory.topic_prefilter import PrefilterResult

    hook = MemoryExtractionHook.__new__(MemoryExtractionHook)
    hook._session_key = "k1"
    hook._next_check_count = 0
    gate = MagicMock()
    gate.prefilter.return_value = PrefilterResult.SKIP
    hook._topic_gate = gate

    judge_calls = {"n": 0}

    async def fake_judge(self, recent, latest):
        judge_calls["n"] += 1
        return True

    monkeypatch.setattr(MemoryExtractionHook, "_judge_topic_change", fake_judge)

    ctx = _Ctx([{"role": "user", "content": "x"}] * 5)
    await hook._detect_topic_change(ctx)
    assert judge_calls["n"] == 0  # 预筛 skip → 不调 LLM


@pytest.mark.asyncio
async def test_topic_change_fire_blocked_by_interval(monkeypatch):
    """话题切换：fire 间隔被 gate 拦下时不再 background task。"""
    from nanobot.agent.hooks.memory_extraction import MemoryExtractionHook
    from nanobot.memory.topic_prefilter import PrefilterResult

    hook = MemoryExtractionHook.__new__(MemoryExtractionHook)
    hook._session_key = "k1"
    hook._next_check_count = 0
    hook._runtime = MagicMock()
    hook._scratchpad_writer = MagicMock()
    hook._scratchpad_writer.update_focus = AsyncMock()
    hook._extractor = MagicMock()

    gate = MagicMock()
    gate.prefilter.return_value = PrefilterResult.PASS
    gate.allow_fire.return_value = False  # 间隔拦截
    hook._topic_gate = gate

    async def fake_judge(self, recent, latest):
        return False  # 判定为 NEW

    monkeypatch.setattr(MemoryExtractionHook, "_judge_topic_change", fake_judge)
    monkeypatch.setattr(
        "nanobot.agent.hooks.memory_extraction._spawn_background_task",
        lambda coro: None,
    )

    ctx = _Ctx([{"role": "user", "content": "x"}] * 5)
    await hook._detect_topic_change(ctx)
    # allow_fire 返回 False → 不应该调 update_focus
    hook._scratchpad_writer.update_focus.assert_not_awaited()