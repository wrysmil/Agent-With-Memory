"""SubagentManager 按档案裁剪工具与注入 prompt。"""

from __future__ import annotations

from pathlib import Path

from nanobot.agent.subagent import SubagentManager
from nanobot.agents.models import AgentProfile, AgentSelection
from nanobot.agents.runtime import AgentProfileRuntime
from nanobot.agents.store import AgentStore
from nanobot.bus.queue import MessageBus


def _manager(tmp_path: Path, **kwargs: object) -> SubagentManager:
    return SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=4000,
        **kwargs,  # type: ignore[arg-type]
    )


def test_default_manager_has_no_profile_runtime(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    assert manager._agent_profiles is None


def test_resolve_returns_none_without_runtime(tmp_path: Path) -> None:
    assert _manager(tmp_path)._resolve_agent(None) is None
    assert _manager(tmp_path)._resolve_agent("ghost") is None


def test_resolve_finds_injected_profile(tmp_path: Path) -> None:
    manager = _manager(tmp_path, agent_profiles=AgentProfileRuntime(tmp_path))
    resolved = manager._resolve_agent("code-reviewer")
    assert resolved is not None
    assert resolved.id == "code-reviewer"


def test_build_tools_without_filter_keeps_everything(tmp_path: Path) -> None:
    registry = _manager(tmp_path)._build_tools()
    assert "read_file" in registry.names()
    assert "exec" in registry.names()


def test_build_tools_applies_allowed_names(tmp_path: Path) -> None:
    registry = _manager(tmp_path)._build_tools(allowed_tools=frozenset({"read_file"}))
    assert registry.names() == ["read_file"]


def test_build_tools_with_empty_allowed_removes_all(tmp_path: Path) -> None:
    assert _manager(tmp_path)._build_tools(allowed_tools=frozenset()).names() == []


def test_build_prompt_without_agent_keeps_template(tmp_path: Path) -> None:
    prompt = _manager(tmp_path)._build_subagent_prompt(workspace=tmp_path)
    assert prompt
    assert "nanobot" in prompt.lower() or "subagent" in prompt.lower()


def test_build_prompt_prepends_agent_identity(tmp_path: Path) -> None:
    """身份行拼在出厂模板之前；档案给了 prompt 才拼。"""
    AgentStore(tmp_path).save_profile(
        AgentProfile(
            id="reviewer",
            name="代码评审",
            description="只读地审查改动，指出缺陷与风险，不动手改代码。",
            prompt="审查时逐条列出问题，不要给修复代码。",
        )
    )
    manager = _manager(tmp_path, agent_profiles=AgentProfileRuntime(tmp_path))
    resolved = manager._resolve_agent("reviewer")
    prompt = manager._build_subagent_prompt(workspace=tmp_path, agent=resolved)
    assert prompt.startswith("你是代码评审，只读地审查改动")
    assert "审查时逐条列出问题" in prompt
    assert "# Subagent" in prompt


def test_build_prompt_without_description_omits_empty_clause(tmp_path: Path) -> None:
    AgentStore(tmp_path).save_profile(
        AgentProfile(id="silent", name="沉默", prompt="只做事。")
    )
    manager = _manager(tmp_path, agent_profiles=AgentProfileRuntime(tmp_path))
    resolved = manager._resolve_agent("silent")
    assert manager._build_subagent_prompt(
        workspace=tmp_path, agent=resolved
    ).startswith("你是沉默。")


def test_build_prompt_skips_identity_when_prompt_empty(tmp_path: Path) -> None:
    """出厂档案 prompt 为空，此时行为必须与一期完全一致（不拼身份行）。"""
    manager = _manager(tmp_path, agent_profiles=AgentProfileRuntime(tmp_path))
    resolved = manager._resolve_agent("general-assistant")
    prompt = manager._build_subagent_prompt(workspace=tmp_path, agent=resolved)
    assert prompt.startswith("# Subagent")
    assert "你是通用助理" not in prompt


def test_prompt_excludes_skills_not_enabled(tmp_path: Path) -> None:
    AgentStore(tmp_path).save_profile(
        AgentProfile(id="narrow", name="窄", skills=AgentSelection("include", []))
    )
    manager = _manager(tmp_path, agent_profiles=AgentProfileRuntime(tmp_path))
    resolved = manager._resolve_agent("narrow")
    prompt = manager._build_subagent_prompt(workspace=tmp_path, agent=resolved)
    # include 空列表 = 一个技能都不要；摘要里不该出现任何技能条目
    assert "Built-in skills" not in prompt


def test_model_resolver_not_injected_keeps_runtime(tmp_path: Path) -> None:
    sentinel = object()
    manager = _manager(tmp_path, agent_profiles=AgentProfileRuntime(tmp_path))
    assert manager._apply_model_override(sentinel, None) is sentinel  # type: ignore[arg-type]


def test_model_resolver_returning_none_keeps_runtime(tmp_path: Path) -> None:
    manager = _manager(tmp_path, agent_profiles=AgentProfileRuntime(tmp_path))
    sentinel = object()
    assert manager._apply_model_override(sentinel, "default") is sentinel  # type: ignore[arg-type]


def test_model_resolver_result_is_used(tmp_path: Path) -> None:
    sentinel = object()
    replacement = object()
    manager = _manager(
        tmp_path,
        agent_profiles=AgentProfileRuntime(tmp_path),
        model_resolver=lambda model_id: replacement if model_id == "default" else None,
    )
    assert manager._apply_model_override(sentinel, "default") is replacement  # type: ignore[arg-type]


def test_spawn_passes_agent_id_through(tmp_path: Path) -> None:
    """spawn 必须把 agent_id 一路传到 _resolve_agent，否则整条链路是断的。"""
    import inspect

    source = inspect.getsource(SubagentManager.spawn)
    assert "agent_id=agent_id" in source
    source = inspect.getsource(SubagentManager.run_inline)
    assert "agent_id=agent_id" in source
    source = inspect.getsource(SubagentManager._run_admitted_subagent)
    assert "_resolve_agent" in source
    assert "_apply_model_override" in source


def test_tools_and_skills_are_trimmed_together(tmp_path: Path) -> None:
    """回归防线：档案的 tools 与 skills 必须在同一次装配里都被应用。

    只裁工具不裁技能（或反之）会让 prompt 说一套、实际能做另一套。
    """
    AgentStore(tmp_path).save_profile(
        AgentProfile(
            id="narrow",
            name="窄",
            tools=AgentSelection("include", ["read_file"]),
            skills=AgentSelection("include", []),
        )
    )
    manager = _manager(tmp_path, agent_profiles=AgentProfileRuntime(tmp_path))
    resolved = manager._resolve_agent("narrow")
    assert resolved is not None
    registry = manager._build_tools(allowed_tools=resolved.tool_names)
    prompt = manager._build_subagent_prompt(workspace=tmp_path, agent=resolved)
    assert registry.names() == ["read_file"]
    assert "Built-in skills" not in prompt


def test_subagent_never_sees_spawn(tmp_path: Path) -> None:
    """防递归回归防线：子 Agent 的工具集里不能出现 spawn。

    依据是 ``SpawnTool`` 未声明 ``_scopes``，走 ``Tool.base`` 默认的
    ``{"core"}``，在 ``loader.py:100`` 的 scope 过滤处即被剔除。
    若本测试失败，说明 scope 机制被意外改动，必须回滚排查。
    """
    manager = _manager(tmp_path, agent_profiles=AgentProfileRuntime(tmp_path))
    resolved = manager._resolve_agent("code-reviewer")
    registry = manager._build_tools(
        allowed_tools=resolved.tool_names if resolved is not None else None
    )
    assert "spawn" not in registry.names()
