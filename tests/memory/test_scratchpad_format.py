"""ScratchpadWriter.format_with_llm 单测（S4）。"""
from __future__ import annotations

import pytest

from nanobot.memory.scratchpad_writer import ScratchpadWriter


class _FakeProvider:
    def __init__(self, content: str = "", raise_exc: Exception | None = None) -> None:
        self._content = content
        self._raise = raise_exc

    async def chat_with_retry(self, *, model, messages, tools, **kwargs):
        if self._raise is not None:
            raise self._raise

        class _R:
            content = self._content

        return _R()


class _FakeRuntime:
    def __init__(self, content: str = "", raise_exc: Exception | None = None) -> None:
        self.provider = _FakeProvider(content, raise_exc)
        self.model = "test-model"
        self.generation = type("G", (), {"temperature": 0.0, "max_tokens": 1000, "reasoning_effort": None})()


@pytest.mark.asyncio
async def test_format_with_llm_success():
    runtime = _FakeRuntime(
        "## 当前项目\n- 配置 PATH\n\n"
        "## 近期进展\n- 已写入 .zshrc\n\n"
        "## 未解决的问题\n- 无\n\n"
        "## 下一步\n- 重启 shell"
    )
    writer = ScratchpadWriter(
        database=None, user_id="u1", runtime=runtime
    )
    pad = await writer.format_with_llm(
        current_scratchpad=None,
        episode_summary="用户配置 PATH 并写入 .zshrc",
    )
    assert "配置 PATH" in pad.content
    assert "## 下一步" in pad.content


@pytest.mark.asyncio
async def test_format_with_llm_failure_falls_back_to_minimal():
    runtime = _FakeRuntime(raise_exc=RuntimeError("down"))
    writer = ScratchpadWriter(
        database=None, user_id="u1", runtime=runtime
    )
    pad = await writer.format_with_llm(
        current_scratchpad=None,
        episode_summary="测试 episode 摘要",
    )
    assert "测试 episode 摘要" in pad.content


@pytest.mark.asyncio
async def test_format_with_llm_truncates_long_output():
    runtime = _FakeRuntime("## 当前项目\n" + ("x" * 3000))
    writer = ScratchpadWriter(
        database=None, user_id="u1", runtime=runtime
    )
    pad = await writer.format_with_llm(
        current_scratchpad=None,
        episode_summary="y",
    )
    assert len(pad.content) <= 2000