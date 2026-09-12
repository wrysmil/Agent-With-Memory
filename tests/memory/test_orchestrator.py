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
        transcript=[{"role": "user", "content": "x"}],
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
        transcript=[{"role": "user", "content": "x"}],
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
        transcript=[{"role": "user", "content": "x"}],
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
        transcript=[{"role": "user", "content": "x"}],
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
        transcript=[{"role": "user", "content": "x"}],
    ))
    await orch.run(SessionEndEvent(
        session_key="k1",
        reason=SessionEndReason.IDLE_TIMEOUT,
        transcript=[{"role": "user", "content": "x"}],
    ))
    assert ext.calls.count("episode") == 2