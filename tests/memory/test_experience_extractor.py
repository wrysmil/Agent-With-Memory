"""ExperienceExtractor 单测（S3 Track 2：任务经验）。"""
from __future__ import annotations

import pytest

from nanobot.memory.experience_extractor import ExperienceExtractor


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
async def test_extract_experience_skip_when_too_few_assistant_turns():
    runtime = _FakeRuntime(raise_exc=AssertionError("LLM 不应被调用"))
    ext = ExperienceExtractor(runtime=runtime)
    items = await ext.extract(
        transcript=[{"role": "user", "content": "hi"}], episode_id="e1"
    )
    assert items == []


@pytest.mark.asyncio
async def test_extract_experience_success():
    runtime = _FakeRuntime(
        '{"experiences":[{"content":"macOS 改 PATH 需重启 shell",'
        '"subject":"PATH","predicate":"配置","type":"SKILL",'
        '"importance":0.8,"priority":"long_term","tags":["macos"]}]}'
    )
    ext = ExperienceExtractor(runtime=runtime)
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
    runtime = _FakeRuntime(raise_exc=RuntimeError("down"))
    ext = ExperienceExtractor(runtime=runtime)
    items = await ext.extract(
        transcript=[
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "y"},
            {"role": "assistant", "content": "z"},
        ],
        episode_id="e1",
    )
    assert items == []