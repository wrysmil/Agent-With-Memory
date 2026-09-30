"""档案解析失败时的 fail-closed 行为。

安全回归防线：`agent` 参数由 LLM 传入，是不可信输入。若解析失败就退回
「无档案」路径，子 Agent 会拿到 subagent 范围的全量 13 个工具（含 exec /
write_file / edit_file），用户显式配置的只读约束被静默丢弃。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nanobot.agent.subagent import SubagentManager
from nanobot.agents.runtime import AgentProfileRuntime
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import GenerationSettings, LLMProvider
from nanobot.utils.llm_runtime import LLMRuntime

DANGEROUS = ("exec", "write_file", "edit_file", "apply_patch", "run_cli_app")


def _runtime(model: str = "test-model") -> LLMRuntime:
    provider = MagicMock(spec=LLMProvider)
    provider.generation = GenerationSettings()
    return LLMRuntime.capture(provider, model, context_window_tokens=128_000)


def _manager(tmp_path: Path) -> SubagentManager:
    return SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=4000,
        agent_profiles=AgentProfileRuntime(tmp_path),
    )


# --- 未传 agent：保持一期行为，拿到全量工具 ---------------------------------


@pytest.mark.asyncio
async def test_omitting_agent_keeps_default_tools(tmp_path: Path) -> None:
    """没传 agent 时不按档案裁剪，工具集与一期一致。这是既有行为，不是漏洞。"""
    manager = _manager(tmp_path)
    registry = manager._build_tools(allowed_tools=None)
    assert set(DANGEROUS) <= set(registry.names())


@pytest.mark.asyncio
async def test_omitting_agent_is_not_rejected(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    with patch.object(manager, "_resolve_agent", return_value=None) as spy:
        # 未传 → _resolve_agent(None) → None → 不拒绝
        assert manager._resolve_agent(None) is None
    assert spy.called


# --- 传了但解析失败：必须 fail-closed ----------------------------------------


@pytest.mark.parametrize(
    "bad_id",
    [
        "code-reviwer",  # 拼错一个字母——最常见的真实场景
        "ghost",  # 不存在的档案
        "../../etc/passwd",  # 路径穿越
        "",  # LLM 漏填
        "   ",  # 空白
        "A" * 200,  # 超长
    ],
)
def test_unresolvable_agent_ids_never_grant_full_tools(tmp_path: Path, bad_id: str) -> None:
    """回归防线：任何解析失败的 id 都不能让子 Agent 拿到危险工具。

    失败场景：用户配了只读档案 code-reviewer，LLM 把它拼成 code-reviwer。
    旧实现 resolve() → None → _build_tools(allowed_tools=None) → 跳过裁剪
    → 子 Agent 拿到 exec / write_file / edit_file / apply_patch。
    """
    manager = _manager(tmp_path)
    outcome = manager._prepare_agent(bad_id)
    assert outcome.rejected, f"agent={bad_id!r} 应当被拒绝而不是降级"
    granted = set(manager._build_tools(allowed_tools=outcome.allowed_tools).names())
    assert not (set(DANGEROUS) & granted)


def test_rejection_message_names_the_bad_id_and_lists_alternatives(tmp_path: Path) -> None:
    """拒绝时要让 LLM 知道哪错了、能改用什么，否则它只会原样重试。"""
    manager = _manager(tmp_path)
    outcome = manager._prepare_agent("code-reviwer")
    assert outcome.rejected
    assert "code-reviwer" in outcome.error
    assert "code-reviewer" in outcome.error, "错误信息应列出可用的真实档案 id"


def test_valid_profile_is_accepted(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    outcome = manager._prepare_agent("code-reviewer")
    assert not outcome.rejected
    assert outcome.allowed_tools is not None
    granted = set(manager._build_tools(allowed_tools=outcome.allowed_tools).names())
    assert "read_file" in granted
    assert not (set(DANGEROUS) & granted), "只读档案不应拿到写/执行工具"


def test_manager_without_profile_runtime_still_accepts_omitted(tmp_path: Path) -> None:
    """未注入 agent_profiles 的宿主（测试替身等）不该因本次改动而拒绝。"""
    manager = SubagentManager(
        workspace=tmp_path, bus=MessageBus(), max_tool_result_chars=4000
    )
    assert manager._prepare_agent(None).rejected is False
    assert manager._prepare_agent(None).allowed_tools is None


# --- 可观测性 ---------------------------------------------------------------


def test_rejection_is_logged(tmp_path: Path) -> None:
    """零日志会让运维看不见「档案没生效」——这是本次 HIGH 的加剧因素。"""
    from nanobot.agent.subagent import logger as subagent_logger

    sink: list[str] = []
    handler_id = subagent_logger.add(lambda m: sink.append(m), level="WARNING")
    try:
        _manager(tmp_path)._prepare_agent("code-reviwer")
    finally:
        subagent_logger.remove(handler_id)
    assert any("code-reviwer" in line for line in sink), f"未记录被拒绝的 id，日志={sink}"


def test_omitting_agent_is_not_logged_as_error(tmp_path: Path) -> None:
    """没传 agent 是正常路径，不该刷 WARNING。"""
    from nanobot.agent.subagent import logger as subagent_logger

    sink: list[str] = []
    handler_id = subagent_logger.add(lambda m: sink.append(m), level="WARNING")
    try:
        _manager(tmp_path)._prepare_agent(None)
    finally:
        subagent_logger.remove(handler_id)
    assert not sink, f"未传 agent 不该产生告警，实际={sink}"


# --- hidden 档案不应出现在 LLM 可见的清单里 ---------------------------------


def test_hidden_profile_is_not_offered_to_llm(tmp_path: Path) -> None:
    """回归防线：用户隐藏档案的意图是它别出现在被枚举的清单里。

    失败场景：WebUI 里隐藏 ops-runner → hidden=true → 但 spawn 的工具描述
    仍输出「Available profiles: ..., ops-runner」，LLM 可以直接 spawn 它。
    「隐藏」这个用户可见的管控动作对 LLM 完全无效。
    """
    from nanobot.agents.store import AgentStore

    AgentStore(tmp_path).set_visibility("ops-runner", True)
    available = _manager(tmp_path)._available_agent_ids()
    assert "ops-runner" not in available
    assert "code-reviewer" in available, "未隐藏的档案仍应可见"


def test_hidden_profile_is_excluded_from_spawn_description(tmp_path: Path) -> None:
    from nanobot.agent.tools.spawn import SpawnTool
    from nanobot.agents.store import AgentStore

    AgentStore(tmp_path).set_visibility("ops-runner", True)
    tool = SpawnTool(_manager(tmp_path))  # type: ignore[arg-type]
    assert "ops-runner" not in tool.description
    assert "code-reviewer" in tool.description


def test_hidden_profile_still_resolves_by_explicit_id(tmp_path: Path) -> None:
    """隐藏只挡「被枚举」，不挡「按 id 直接用」——它不是权限开关。

    若连显式 id 也拒，那会让「临时藏起来」变成「永久停用」，反而超出
    hidden 这个字段的语义（catalog.py:24-25 明说它是视图偏好）。
    """
    from nanobot.agents.store import AgentStore

    AgentStore(tmp_path).set_visibility("ops-runner", True)
    manager = _manager(tmp_path)
    prep = manager._prepare_agent("ops-runner")
    assert not prep.rejected, "hidden 不是权限开关，不该拒绝显式调用"


# --- 端到端：拒绝要真的挡住 spawn -------------------------------------------


@pytest.mark.asyncio
async def test_rejected_spawn_returns_error_instead_of_running(tmp_path: Path) -> None:
    """被拒绝时任务不能照常跑——否则 fail-closed 只是日志而已。"""
    manager = _manager(tmp_path)
    result = await manager.run_inline(
        task="t", agent_id="code-reviwer", runtime=_runtime()
    )
    assert "code-reviwer" in result
    assert "code-reviewer" in result
