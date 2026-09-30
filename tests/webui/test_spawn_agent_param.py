"""SpawnTool 的 agent 参数：schema 声明、透传与描述里的可用档案枚举。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.providers.base import GenerationSettings, LLMProvider
from nanobot.utils.llm_runtime import LLMRuntime


def _runtime(model: str = "test-model") -> LLMRuntime:
    provider = MagicMock(spec=LLMProvider)
    provider.generation = GenerationSettings()
    return LLMRuntime.capture(provider, model, context_window_tokens=128_000)


class _FakeManager:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.calls: list[dict] = []

    async def spawn(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        return "ok"

    run_inline = spawn


def test_agent_is_in_the_json_schema(tmp_path: Path) -> None:
    # tool_parameters 把 schema 关在闭包里，只经 parameters 属性暴露
    # （nanobot/agent/tools/base.py:338），所以必须走实例。
    params = SpawnTool(_FakeManager(tmp_path)).parameters  # type: ignore[arg-type]
    assert "agent" in params["properties"]
    assert params["required"] == ["task"]


def test_agent_is_optional(tmp_path: Path) -> None:
    params = SpawnTool(_FakeManager(tmp_path)).parameters  # type: ignore[arg-type]
    assert params["properties"]["agent"]["type"] == "string"
    assert "description" in params["properties"]["agent"]


def test_description_lists_available_agents(tmp_path: Path) -> None:
    tool = SpawnTool(_FakeManager(tmp_path))  # type: ignore[arg-type]
    description = tool.description
    assert "code-reviewer" in description
    assert "researcher" in description


def test_description_falls_back_when_store_is_unreadable(tmp_path: Path) -> None:
    tool = SpawnTool(_FakeManager(tmp_path))  # type: ignore[arg-type]
    assert tool.description  # 至少还有基础描述


def test_description_falls_back_when_manager_has_no_workspace() -> None:
    class _NoWorkspace:
        pass

    tool = SpawnTool(_NoWorkspace())  # type: ignore[arg-type]
    assert tool.description
    assert "Available profiles" not in tool.description


@pytest.mark.asyncio
async def test_execute_omits_agent_id_when_not_given(tmp_path: Path) -> None:
    """没给 agent 时不传 agent_id，旧调用形状不变。"""
    manager = _FakeManager(tmp_path)
    tool = SpawnTool(manager)  # type: ignore[arg-type]
    with request_context(RequestContext(channel="cli", chat_id="c1", runtime=_runtime())):
        await tool.execute("do it")
    assert "agent_id" not in manager.calls[0]


@pytest.mark.asyncio
async def test_execute_forwards_agent_id(tmp_path: Path) -> None:
    """给了 agent 时转发为 agent_id=agent。"""
    manager = _FakeManager(tmp_path)
    tool = SpawnTool(manager)  # type: ignore[arg-type]
    with request_context(RequestContext(channel="cli", chat_id="c1", runtime=_runtime())):
        await tool.execute("do it", agent="code-reviewer")
    assert manager.calls[0]["agent_id"] == "code-reviewer"
