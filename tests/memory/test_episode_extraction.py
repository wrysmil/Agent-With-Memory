"""Episode 抽取单测（S2）。"""
from __future__ import annotations

import pytest

from nanobot.memory.extractor import MemoryExtractor


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
async def test_generate_episode_with_llm_success():
    runtime = _FakeRuntime(
        '{"summary":"用户配置了 PATH","goal":"配置环境",'
        '"outcome":"success","entities":["~/.zshrc"],'
        '"tools_used":["edit_file"]}'
    )
    ext = MemoryExtractor(database=None, runtime=runtime)
    episode = await ext.generate_episode(
        transcript=[
            {"role": "user", "content": "帮我配 PATH"},
            {
                "role": "assistant",
                "content": "已写入 .zshrc",
                "tool_calls": [{"name": "edit_file", "input": {"path": "~/.zshrc"}, "id": "tc1"}],
                "tool_results": [{"tool_use_id": "tc1", "content": "ok", "is_error": False}],
            },
        ],
        session_key="k1",
    )
    assert episode.summary == "用户配置了 PATH"
    assert episode.goal == "配置环境"
    assert episode.outcome.value == "completed"
    assert "~/.zshrc" in episode.entities
    assert "edit_file" in episode.tools_used


@pytest.mark.asyncio
async def test_generate_episode_empty_transcript_returns_none():
    runtime = _FakeRuntime(raise_exc=AssertionError("LLM 不应被调用"))
    ext = MemoryExtractor(database=None, runtime=runtime)
    assert await ext.generate_episode(transcript=[], session_key="k1") is None


@pytest.mark.asyncio
async def test_generate_episode_llm_failure_uses_heuristic():
    runtime = _FakeRuntime(raise_exc=RuntimeError("LLM down"))
    ext = MemoryExtractor(database=None, runtime=runtime)
    episode = await ext.generate_episode(
        transcript=[
            {"role": "user", "content": "请帮我看看 /Users/a/b/c.py 文件"},
            {"role": "assistant", "content": "好的"},
        ],
        session_key="k1",
    )
    assert episode is not None
    assert episode.summary
    assert any("c.py" in e or "Users" in e for e in episode.entities)


@pytest.mark.asyncio
async def test_generate_episode_action_nodes_capture_tool_calls():
    runtime = _FakeRuntime(
        '{"summary":"x","goal":"y","outcome":"completed",'
        '"entities":[],"tools_used":["read_file"]}'
    )
    ext = MemoryExtractor(database=None, runtime=runtime)
    episode = await ext.generate_episode(
        transcript=[
            {
                "role": "assistant",
                "content": "ok",
                "tool_calls": [
                    {"name": "read_file", "input": {"path": "/tmp/a.txt"}, "id": "tc1"}
                ],
                "tool_results": [{"tool_use_id": "tc1", "content": "file content", "is_error": False}],
            },
        ],
        session_key="k1",
    )
    assert len(episode.action_nodes) == 1
    node = episode.action_nodes[0]
    assert node["tool_name"] == "read_file"
    assert node["key_params"].get("path") == "/tmp/a.txt"
    assert node["success"] is True