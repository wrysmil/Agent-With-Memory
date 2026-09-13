"""SessionEndOrchestrator 守卫单元测试（L0/L1/L2）。

L0: 空 transcript 直接跳过
L1: 消息数 < 3 的短会话跳过
L2: 单用户 + 短内容 + 无工具调用 → 跳过（有工具调用时豁免）
"""
from __future__ import annotations

import pytest

from nanobot.memory.orchestrator import SessionEndOrchestrator
from nanobot.memory.session_end_event import SessionEndEvent, SessionEndReason


# ---------------------------------------------------------------------------
# Fake 依赖
# ---------------------------------------------------------------------------


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
    """无副作用的 fake extractor，用于验证守卫逻辑。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def generate_episode(self, transcript, session_key, source="session_end"):
        self.calls.append("episode")
        return _FakeEpisode()

    async def extract_user_profile(self, transcript, episode_id, cited=None):
        self.calls.append(f"profile:{episode_id}")
        return [], []

    async def extract_experience(self, transcript, episode_id):
        self.calls.append(f"experience:{episode_id}")
        return []

    async def link_relations(self, episode_id, memory_ids, turn_ids):
        """可选方法，用于 Step 4。"""
        pass


# ---------------------------------------------------------------------------
# 测试类：L0/L1/L2 守卫
# ---------------------------------------------------------------------------


class TestSessionEndOrchestratorGuards:
    """SessionEndOrchestrator L0/L1/L2 守卫测试。"""

    # -------------------------------------------------------------------------
    # L0 守卫：空 transcript
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_l0_empty_transcript(self):
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
    async def test_l0_none_transcript(self):
        """L0: transcript 为 None 时跳过。"""
        ext = _FakeExtractor()
        orch = SessionEndOrchestrator(extractor=ext, scratchpad_writer=None)
        await orch.run(SessionEndEvent(
            session_key="k1",
            reason=SessionEndReason.USER_CLOSE,
            transcript=None,  # type: ignore
        ))
        assert ext.calls == []

    # -------------------------------------------------------------------------
    # L1 守卫：消息数 < 3
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_l1_single_message(self):
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
    async def test_l1_two_messages(self):
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

    # -------------------------------------------------------------------------
    # L2 守卫：单用户 + 短内容 + 无工具调用
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_l2_short_single_user_no_tool(self):
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
    async def test_l2_single_char_user_message(self):
        """L2: 用户消息只有 1 个字符时应跳过。"""
        ext = _FakeExtractor()
        orch = SessionEndOrchestrator(extractor=ext, scratchpad_writer=None)
        await orch.run(SessionEndEvent(
            session_key="k1",
            reason=SessionEndReason.USER_CLOSE,
            transcript=[
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "..."},
                {"role": "assistant", "content": "..."},
            ],
        ))
        assert ext.calls == []

    @pytest.mark.asyncio
    async def test_l2_9_char_user_message(self):
        """L2: 用户消息 9 个字符（< 10）应跳过。"""
        ext = _FakeExtractor()
        orch = SessionEndOrchestrator(extractor=ext, scratchpad_writer=None)
        await orch.run(SessionEndEvent(
            session_key="k1",
            reason=SessionEndReason.USER_CLOSE,
            transcript=[
                {"role": "user", "content": "123456789"},  # 9 chars
                {"role": "assistant", "content": "..."},
                {"role": "assistant", "content": "..."},
            ],
        ))
        assert ext.calls == []

    # -------------------------------------------------------------------------
    # L2 豁免：有工具调用
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_l2_exempt_with_tool_calls(self):
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
    async def test_l2_exempt_with_multiple_tool_calls(self):
        """L2: 有多个工具调用时也豁免。"""
        ext = _FakeExtractor()
        orch = SessionEndOrchestrator(extractor=ext, scratchpad_writer=None)
        await orch.run(SessionEndEvent(
            session_key="k1",
            reason=SessionEndReason.USER_CLOSE,
            transcript=[
                {"role": "user", "content": "ok"},
                {"role": "assistant", "content": "..."},
                {
                    "role": "assistant",
                    "content": "...",
                    "tool_calls": [
                        {"name": "read", "input": {"path": "/a"}},
                        {"name": "write", "input": {"path": "/b"}},
                    ],
                },
            ],
        ))
        assert "episode" in ext.calls

    @pytest.mark.asyncio
    async def test_l2_exempt_with_tool_calls_on_different_turn(self):
        """L2: 工具调用不在最后一个 assistant 消息中也应豁免。"""
        ext = _FakeExtractor()
        orch = SessionEndOrchestrator(extractor=ext, scratchpad_writer=None)
        await orch.run(SessionEndEvent(
            session_key="k1",
            reason=SessionEndReason.USER_CLOSE,
            transcript=[
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "content": "Doing...",
                    "tool_calls": [{"name": "shell", "input": {"command": "ls"}}],
                },
                {"role": "assistant", "content": "Done!"},
            ],
        ))
        assert "episode" in ext.calls

    # -------------------------------------------------------------------------
    # L2 边界条件
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_l2_long_user_message_proceeds(self):
        """L2: 用户消息 >= 10 字符时正常执行。"""
        ext = _FakeExtractor()
        orch = SessionEndOrchestrator(extractor=ext, scratchpad_writer=None)
        await orch.run(SessionEndEvent(
            session_key="k1",
            reason=SessionEndReason.USER_CLOSE,
            transcript=[
                {"role": "user", "content": "Hello there!"},  # >= 10 chars
                {"role": "assistant", "content": "Hi!"},
                {"role": "assistant", "content": "How can I help?"},
            ],
        ))
        assert "episode" in ext.calls

    @pytest.mark.asyncio
    async def test_l2_exactly_10_char_proceeds(self):
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

    @pytest.mark.asyncio
    async def test_l2_multi_user_proceeds(self):
        """L2: 多用户时（len(user_msgs) != 1）正常执行。"""
        ext = _FakeExtractor()
        orch = SessionEndOrchestrator(extractor=ext, scratchpad_writer=None)
        await orch.run(SessionEndEvent(
            session_key="k1",
            reason=SessionEndReason.USER_CLOSE,
            transcript=[
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "Hi!"},
                {"role": "user", "content": "hi"},
            ],
        ))
        assert "episode" in ext.calls

    @pytest.mark.asyncio
    async def test_l2_multi_user_short_no_tool_still_proceeds(self):
        """L2: 多用户即使都短也正常执行（因为不是 len(user_msgs) == 1）。"""
        ext = _FakeExtractor()
        orch = SessionEndOrchestrator(extractor=ext, scratchpad_writer=None)
        await orch.run(SessionEndEvent(
            session_key="k1",
            reason=SessionEndReason.USER_CLOSE,
            transcript=[
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "Hi!"},
                {"role": "user", "content": "ok"},
            ],
        ))
        assert "episode" in ext.calls

    # -------------------------------------------------------------------------
    # 正常流程（通过所有守卫）
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_normal_transcript_proceeds(self):
        """正常 transcript（3+ 消息，或多用户）应正常触发。"""
        ext = _FakeExtractor()
        orch = SessionEndOrchestrator(extractor=ext, scratchpad_writer=None)
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
        assert "profile:ep-1" in ext.calls

    @pytest.mark.asyncio
    async def test_normal_four_message_transcript(self):
        """4 条消息的正常会话应正常触发。"""
        ext = _FakeExtractor()
        orch = SessionEndOrchestrator(extractor=ext, scratchpad_writer=None)
        await orch.run(SessionEndEvent(
            session_key="k1",
            reason=SessionEndReason.USER_CLOSE,
            transcript=[
                {"role": "user", "content": "Hello!"},
                {"role": "assistant", "content": "Hi!"},
                {"role": "user", "content": "Help me with code."},
                {"role": "assistant", "content": "Sure!"},
            ],
        ))
        assert "episode" in ext.calls

    # -------------------------------------------------------------------------
    # 守卫组合测试
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_guard_priority_l0_before_l1(self):
        """守卫优先级：L0 在 L1 之前执行。"""
        ext = _FakeExtractor()
        orch = SessionEndOrchestrator(extractor=ext, scratchpad_writer=None)
        # 空 transcript 应该在 L0 被拦截
        await orch.run(SessionEndEvent(
            session_key="k1",
            reason=SessionEndReason.USER_CLOSE,
            transcript=[],
        ))
        assert ext.calls == []

    @pytest.mark.asyncio
    async def test_guard_priority_l1_before_l2(self):
        """守卫优先级：L1 在 L2 之前执行。"""
        ext = _FakeExtractor()
        orch = SessionEndOrchestrator(extractor=ext, scratchpad_writer=None)
        # 2 条消息应该在 L1 被拦截（不进入 L2）
        await orch.run(SessionEndEvent(
            session_key="k1",
            reason=SessionEndReason.USER_CLOSE,
            transcript=[
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hi"},
            ],
        ))
        assert ext.calls == []

    # -------------------------------------------------------------------------
    # 边缘情况
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_empty_content_field(self):
        """content 字段为空字符串时视为空。"""
        ext = _FakeExtractor()
        orch = SessionEndOrchestrator(extractor=ext, scratchpad_writer=None)
        await orch.run(SessionEndEvent(
            session_key="k1",
            reason=SessionEndReason.USER_CLOSE,
            transcript=[
                {"role": "user", "content": ""},
                {"role": "assistant", "content": "..."},
                {"role": "assistant", "content": "..."},
            ],
        ))
        # 空字符串长度 < 10，应跳过
        assert ext.calls == []

    @pytest.mark.asyncio
    async def test_whitespace_only_content(self):
        """content 只有空白字符时应被 strip 后判断。"""
        ext = _FakeExtractor()
        orch = SessionEndOrchestrator(extractor=ext, scratchpad_writer=None)
        await orch.run(SessionEndEvent(
            session_key="k1",
            reason=SessionEndReason.USER_CLOSE,
            transcript=[
                {"role": "user", "content": "   "},  # 空白
                {"role": "assistant", "content": "..."},
                {"role": "assistant", "content": "..."},
            ],
        ))
        # strip 后为空，长度 0 < 10，应跳过
        assert ext.calls == []

    @pytest.mark.asyncio
    async def test_none_content_field(self):
        """content 字段为 None 时应安全处理。"""
        ext = _FakeExtractor()
        orch = SessionEndOrchestrator(extractor=ext, scratchpad_writer=None)
        await orch.run(SessionEndEvent(
            session_key="k1",
            reason=SessionEndReason.USER_CLOSE,
            transcript=[
                {"role": "user", "content": None},  # type: ignore
                {"role": "assistant", "content": "..."},
                {"role": "assistant", "content": "..."},
            ],
        ))
        # None.strip() -> "" -> 长度 0 < 10，应跳过
        assert ext.calls == []

    @pytest.mark.asyncio
    async def test_assistant_message_short_circuit(self):
        """assistant 消息短不影响 L2 判断（只看 user 消息）。"""
        ext = _FakeExtractor()
        orch = SessionEndOrchestrator(extractor=ext, scratchpad_writer=None)
        await orch.run(SessionEndEvent(
            session_key="k1",
            reason=SessionEndReason.USER_CLOSE,
            transcript=[
                {"role": "user", "content": "This is a long user message!"},
                {"role": "assistant", "content": "ok"},
                {"role": "assistant", "content": "ok"},
            ],
        ))
        # 用户消息够长，正常执行
        assert "episode" in ext.calls
