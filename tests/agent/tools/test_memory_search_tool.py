"""Tests for MemorySearchTool (T-14)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.tools.memory_search import MemorySearchTool


class _FakeEngine:
    def __init__(self, result: str) -> None:
        self._result = result
        self.retrieve = AsyncMock(return_value=result)

    async def retrieve(self, *, query: str, recent_messages: list, max_tokens: int) -> str:
        return self._result


@pytest.fixture
def fake_engine() -> _FakeEngine:
    return _FakeEngine("## 相关记忆\n- Python 爬虫")


class TestToolMetadata:
    def test_name(self) -> None:
        assert MemorySearchTool.name == "memory_search"

    def test_description_has_memory_keyword(self) -> None:
        desc = MemorySearchTool.description
        assert "记忆" in desc

    def test_description_distinguishes_from_auto_injection(self) -> None:
        desc = MemorySearchTool.description
        # Must explain it's proactive vs passive
        assert ("自动" in desc or "auto" in desc.lower()
                or "互补" in desc or "token" in desc.lower())


class TestExecute:
    @pytest.mark.asyncio
    async def test_returns_engine_result(self, fake_engine: _FakeEngine) -> None:
        provider = MagicMock(return_value=fake_engine)
        tool = MemorySearchTool(retrieval_engine_provider=provider)
        result = await tool.execute(query="Python 爬虫", max_tokens=500)
        assert "Python 爬虫" in result
        fake_engine.retrieve.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_gate_skip_returns_empty(self) -> None:
        provider = MagicMock(return_value=_FakeEngine(""))
        tool = MemorySearchTool(retrieval_engine_provider=provider)
        result = await tool.execute(query="好", max_tokens=500)
        assert result == ""

    @pytest.mark.asyncio
    async def test_passes_max_tokens(self) -> None:
        engine = _FakeEngine("result")
        provider = MagicMock(return_value=engine)
        tool = MemorySearchTool(retrieval_engine_provider=provider)
        await tool.execute(query="test", max_tokens=200)
        call_kwargs = engine.retrieve.call_args.kwargs
        assert call_kwargs.get("max_tokens") == 200

    @pytest.mark.asyncio
    async def test_engine_none_returns_empty(self) -> None:
        provider = MagicMock(return_value=None)
        tool = MemorySearchTool(retrieval_engine_provider=provider)
        result = await tool.execute(query="test", max_tokens=500)
        assert result == ""

    @pytest.mark.asyncio
    async def test_exception_isolation(self) -> None:
        engine = MagicMock()
        engine.retrieve = AsyncMock(side_effect=RuntimeError("boom"))
        provider = MagicMock(return_value=engine)
        tool = MemorySearchTool(retrieval_engine_provider=provider)
        result = await tool.execute(query="test", max_tokens=500)
        assert result == ""

    def test_read_only(self) -> None:
        provider = MagicMock(return_value=None)
        tool = MemorySearchTool(retrieval_engine_provider=provider)
        assert tool.read_only is True
