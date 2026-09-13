"""SessionEndOrchestrator 单测（Task 7）。"""
from __future__ import annotations

import pytest

from nanobot.memory.orchestrator import SessionEndOrchestrator
from nanobot.memory.session_end_event import SessionEndEvent, SessionEndReason


class _FakeEpisode:
    def __init__(self, ep_id: str = "ep-1") -> None:
        self.id = ep_id
        self.summary = "sum"
        self.goal = "g"
        self.outcome = type("O", (), {"value": "completed"})()
        self.entities = []
        self.tools_used = []
        self.action_nodes = []


class _FakeExtractor:
    def __init__(
        self,
        *,
        episode_exc: Exception | None = None,
        profile_exc: Exception | None = None,
        experience_exc: Exception | None = None,
    ) -> None:
        self.episode_exc = episode_exc
        self.profile_exc = profile_exc
        self.experience_exc = experience_exc
        self.calls: list[str] = []

    async def generate_episode(self, transcript, session_key, source="session_end"):
        self.calls.append("episode")
        if self.episode_exc:
            raise self.episode_exc
        return _FakeEpisode()

    async def extract_user_profile(self, transcript, episode_id, cited=None):
        self.calls.append(f"profile:{episode_id}")
        if self.profile_exc:
            raise self.profile_exc
        return [], []

    async def extract_experience(self, transcript, episode_id):
        self.calls.append(f"experience:{episode_id}")
        if self.experience_exc:
            raise self.experience_exc
        return []


class _FakeScratchpad:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def format_with_llm(self, current, episode_summary):
        self.calls.append("scratchpad")
        from nanobot.memory.models import ScratchpadEntry

        return ScratchpadEntry(
            user_id="u", workspace_id="=",
            updated_at="2026-09-12T00:00:00", content="ok",
        )


# 有效的 3 消息 transcript（通过 L1 守卫）
_VALID_TRANSCRIPT = [
    {"role": "user", "content": "Hello there!"},
    {"role": "assistant", "content": "Hi! How can I help you?"},
    {"role": "user", "content": "Can you help with my code?"},
]


@pytest.mark.asyncio
async def test_orchestrator_runs_all_enabled_steps_in_order():
    ext = _FakeExtractor()
    sp = _FakeScratchpad()
    orch = SessionEndOrchestrator(
        extractor=ext,
        scratchpad_writer=sp,
        enable_track2=True,
        enable_scratchpad_reformat=True,
    )
    await orch.run(SessionEndEvent(
        session_key="k1",
        reason=SessionEndReason.USER_CLOSE,
        transcript=_VALID_TRANSCRIPT,
    ))
    assert ext.calls[:4] == ["episode", "profile:ep-1", "experience:ep-1"]
    assert sp.calls == ["scratchpad"]


@pytest.mark.asyncio
async def test_orchestrator_skips_track2_and_scratchpad_by_default():
    ext = _FakeExtractor()
    sp = _FakeScratchpad()
    orch = SessionEndOrchestrator(extractor=ext, scratchpad_writer=sp)
    await orch.run(SessionEndEvent(
        session_key="k1",
        reason=SessionEndReason.USER_CLOSE,
        transcript=_VALID_TRANSCRIPT,
    ))
    assert ext.calls == ["episode", "profile:ep-1"]
    assert sp.calls == []


@pytest.mark.asyncio
async def test_orchestrator_episode_failure_does_not_block_profile():
    ext = _FakeExtractor(episode_exc=RuntimeError("LLM down"))
    orch = SessionEndOrchestrator(extractor=ext, scratchpad_writer=None)
    # 不应抛
    await orch.run(SessionEndEvent(
        session_key="k1",
        reason=SessionEndReason.USER_CLOSE,
        transcript=_VALID_TRANSCRIPT,
    ))
    # profile 仍被调用，episode_id 是 None
    assert ext.calls[:2] == ["episode", "profile:None"]


@pytest.mark.asyncio
async def test_orchestrator_idempotent_within_30s():
    ext = _FakeExtractor()
    orch = SessionEndOrchestrator(extractor=ext, scratchpad_writer=None)
    evt = SessionEndEvent(
        session_key="k1",
        reason=SessionEndReason.USER_CLOSE,
        transcript=_VALID_TRANSCRIPT,
    )
    await orch.run(evt)
    calls_after_first = list(ext.calls)
    await orch.run(evt)
    # 第二次调用列表应不变（被幂等拦下）
    assert ext.calls == calls_after_first


@pytest.mark.asyncio
async def test_orchestrator_different_reason_not_idempotent():
    ext = _FakeExtractor()
    orch = SessionEndOrchestrator(extractor=ext, scratchpad_writer=None)
    await orch.run(SessionEndEvent(
        session_key="k1",
        reason=SessionEndReason.USER_CLOSE,
        transcript=_VALID_TRANSCRIPT,
    ))
    await orch.run(SessionEndEvent(
        session_key="k1",
        reason=SessionEndReason.IDLE_TIMEOUT,
        transcript=_VALID_TRANSCRIPT,
    ))
    assert ext.calls.count("episode") == 2


# ---------------------------------------------------------------------------
# L0/L1/L2 守卫测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_l0_guard_empty_transcript():
    """L0: 空 transcript 直接跳过。"""
    ext = _FakeExtractor()
    orch = SessionEndOrchestrator(extractor=ext, scratchpad_writer=None)
    await orch.run(SessionEndEvent(
        session_key="k1",
        reason=SessionEndReason.USER_CLOSE,
        transcript=[],
    ))
    assert ext.calls == []


@pytest.mark.asyncio
async def test_orchestrator_l1_guard_single_message():
    """L1: 单条消息会话跳过（< 3 消息）。"""
    ext = _FakeExtractor()
    orch = SessionEndOrchestrator(extractor=ext, scratchpad_writer=None)
    await orch.run(SessionEndEvent(
        session_key="k1",
        reason=SessionEndReason.USER_CLOSE,
        transcript=[{"role": "user", "content": "你好"}],
    ))
    assert ext.calls == []


@pytest.mark.asyncio
async def test_orchestrator_l1_guard_two_messages():
    """L1: 两条消息会话跳过（< 3 消息）。"""
    ext = _FakeExtractor()
    orch = SessionEndOrchestrator(extractor=ext, scratchpad_writer=None)
    await orch.run(SessionEndEvent(
        session_key="k1",
        reason=SessionEndReason.USER_CLOSE,
        transcript=[
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
        ],
    ))
    assert ext.calls == []


@pytest.mark.asyncio
async def test_orchestrator_l2_guard_short_single_user_no_tool():
    """L2: 单用户 + 短内容 + 无工具调用 → 跳过。"""
    ext = _FakeExtractor()
    orch = SessionEndOrchestrator(extractor=ext, scratchpad_writer=None)
    # 3 消息但只有 1 个用户消息且 < 10 字符，无工具调用
    await orch.run(SessionEndEvent(
        session_key="k1",
        reason=SessionEndReason.USER_CLOSE,
        transcript=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "Hello!"},
            {"role": "assistant", "content": "How can I help?"},
        ],
    ))
    assert ext.calls == []


@pytest.mark.asyncio
async def test_orchestrator_l2_guard_exempt_with_tool_calls():
    """L2: 有工具调用时豁免，即使内容很短。"""
    ext = _FakeExtractor()
    orch = SessionEndOrchestrator(extractor=ext, scratchpad_writer=None)
    # 3 消息，单用户短内容，但有工具调用
    await orch.run(SessionEndEvent(
        session_key="k1",
        reason=SessionEndReason.USER_CLOSE,
        transcript=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "I'll help!"},
            {
                "role": "assistant",
                "content": "Done!",
                "tool_calls": [{"name": "shell", "input": {"command": "ls"}}],
            },
        ],
    ))
    assert "episode" in ext.calls


@pytest.mark.asyncio
async def test_orchestrator_proceeds_with_normal_transcript():
    """正常 transcript（3+ 消息，或多用户）应正常触发。"""
    ext = _FakeExtractor()
    orch = SessionEndOrchestrator(extractor=ext, scratchpad_writer=None)
    # 3 消息但多用户
    await orch.run(SessionEndEvent(
        session_key="k1",
        reason=SessionEndReason.USER_CLOSE,
        transcript=[
            {"role": "user", "content": "Hello, how are you?"},
            {"role": "assistant", "content": "I'm well, thanks!"},
            {"role": "user", "content": "Can you run a command?"},
        ],
    ))
    assert "episode" in ext.calls


@pytest.mark.asyncio
async def test_orchestrator_proceeds_with_10_char_user_message():
    """L2: 用户消息恰好 10 字符时不触发 L2。"""
    ext = _FakeExtractor()
    orch = SessionEndOrchestrator(extractor=ext, scratchpad_writer=None)
    await orch.run(SessionEndEvent(
        session_key="k1",
        reason=SessionEndReason.USER_CLOSE,
        transcript=[
            {"role": "user", "content": "1234567890"},  # exactly 10 chars
            {"role": "assistant", "content": "OK!"},
            {"role": "assistant", "content": "Anything else?"},
        ],
    ))
    assert "episode" in ext.calls