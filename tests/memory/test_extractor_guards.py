"""MemoryExtractor 守卫单元测试（L3/L4）。

L3: extract_user_profile 过滤 < 10 字符的用户消息
L4: extract_experience 要求 >= 2 个 assistant 轮次（且 content 非空）
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.extractor import MemoryExtractor


# ---------------------------------------------------------------------------
# Fake LLM（与 test_extractor.py 保持一致）
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeGeneration:
    temperature = 0.2
    max_tokens = 2048
    reasoning_effort = None


class _FakeProvider:
    """可编程 provider，用于守卫测试（不调用真实 LLM）。"""

    def __init__(self, raise_exc: Exception | None = None) -> None:
        self._raise = raise_exc
        self.calls: list[list[dict[str, Any]]] = []

    async def chat_with_retry(self, *, messages: list[dict[str, Any]], **_kwargs: Any) -> _FakeResponse:
        self.calls.append(messages)
        if self._raise is not None:
            raise self._raise
        return _FakeResponse(None)  # 占位实现


class _FakeRuntime:
    def __init__(self, raise_exc: Exception | None = None) -> None:
        self.provider = _FakeProvider(raise_exc)
        self.model = "test-model"
        self.generation = _FakeGeneration()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_extractor(
    db: MemoryDatabase,
    raise_exc: Exception | None = None,
    **kwargs: Any,
) -> MemoryExtractor:
    return MemoryExtractor(db, _FakeRuntime(raise_exc), **kwargs)


@pytest.fixture
def db(tmp_path: Path) -> MemoryDatabase:
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return database


# ---------------------------------------------------------------------------
# 测试类：L3 守卫 (extract_user_profile)
# ---------------------------------------------------------------------------


class TestExtractUserProfileGuards:
    """L3 守卫：extract_user_profile 过滤 < 10 字符的用户消息。

    守卫逻辑：
    user_turns = [t for t in transcript
                  if t.get("role") == "user"
                  and len((t.get("content") or "").strip()) >= 10]
    if not user_turns:
        return
    """

    @pytest.mark.asyncio
    async def test_l3_empty_user_messages(self, db):
        """L3: 无用户消息时跳过。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "assistant", "content": "Hello!"},
            {"role": "assistant", "content": "How can I help?"},
        ]
        await extractor.extract_user_profile(transcript, "ep-1")
        # 守卫生效，无异常

    @pytest.mark.asyncio
    async def test_l3_no_user_messages_at_all(self, db):
        """L3: 完全没有 user 角色消息时跳过。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "system", "content": "You are helpful"},
            {"role": "assistant", "content": "Hi!"},
        ]
        await extractor.extract_user_profile(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l3_short_user_messages(self, db):
        """L3: 用户消息 < 10 字符时跳过。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "hi"},  # < 10 chars
            {"role": "assistant", "content": "Hello!"},
        ]
        await extractor.extract_user_profile(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l3_9_char_user_message(self, db):
        """L3: 用户消息 9 字符时跳过。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "123456789"},  # 9 chars
            {"role": "assistant", "content": "OK"},
        ]
        await extractor.extract_user_profile(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l3_single_char_user_message(self, db):
        """L3: 用户消息 1 个字符时跳过。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "..."},
        ]
        await extractor.extract_user_profile(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l3_empty_content(self, db):
        """L3: content 为空字符串的用户消息被过滤。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": ""},
            {"role": "assistant", "content": "OK"},
        ]
        await extractor.extract_user_profile(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l3_whitespace_only_content(self, db):
        """L3: content 只有空白字符时被 strip 后判断，长度 < 10。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "   "},  # strip 后为空
            {"role": "assistant", "content": "OK"},
        ]
        await extractor.extract_user_profile(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l3_none_content(self, db):
        """L3: content 为 None 时安全处理（视为空）。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": None},  # type: ignore
            {"role": "assistant", "content": "OK"},
        ]
        await extractor.extract_user_profile(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l3_all_short_user_messages(self, db):
        """L3: 所有用户消息都短（< 10 字符）时跳过。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "hi"},
            {"role": "user", "content": "ok"},
            {"role": "assistant", "content": "..."},
        ]
        await extractor.extract_user_profile(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l3_valid_user_messages(self, db):
        """L3: 用户消息 >= 10 字符时正常执行。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "Hello, how are you today?"},  # >= 10 chars
            {"role": "assistant", "content": "I'm well, thanks!"},
        ]
        await extractor.extract_user_profile(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l3_exactly_10_char_proceeds(self, db):
        """L3: 用户消息恰好 10 字符时不触发 L3（>= 10）。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "1234567890"},  # exactly 10 chars
            {"role": "assistant", "content": "OK"},
        ]
        await extractor.extract_user_profile(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l3_long_user_message(self, db):
        """L3: 较长的用户消息应通过。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "I need help with my Python code. It's not working properly."},
            {"role": "assistant", "content": "What seems to be the problem?"},
        ]
        await extractor.extract_user_profile(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l3_one_valid_one_short(self, db):
        """L3: 只要有一个 >= 10 字符的用户消息就通过。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "hi"},  # short
            {"role": "assistant", "content": "..."},
            {"role": "user", "content": "I need help!"},  # >= 10 chars
            {"role": "assistant", "content": "OK"},
        ]
        await extractor.extract_user_profile(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l3_only_checks_user_role(self, db):
        """L3: 仅检查 role=user 的消息，其他角色不影响。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "system", "content": "You are helpful"},
            {"role": "assistant", "content": "hi"},
            {"role": "tool", "content": "result"},
        ]
        # 无 user 消息，应跳过
        await extractor.extract_user_profile(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l3_multibyte_characters(self, db):
        """L3: 多字节字符按字符数计算（中文字符每个都算）。"""
        extractor = _make_extractor(db)
        # 5 个中文字符 = 5 个字符 < 10，应跳过
        transcript = [
            {"role": "user", "content": "你好世界你好"},  # 5 chars
            {"role": "assistant", "content": "OK"},
        ]
        await extractor.extract_user_profile(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l3_mixed_length_user_messages(self, db):
        """L3: 混合长度的用户消息，有一个够长即可。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "Hello there!"},
            {"role": "assistant", "content": "Hi!"},
            {"role": "user", "content": "I want to learn Python programming."},
            {"role": "assistant", "content": "Great!"},
        ]
        await extractor.extract_user_profile(transcript, "ep-1")


# ---------------------------------------------------------------------------
# 测试类：L4 守卫 (extract_experience)
# ---------------------------------------------------------------------------


class TestExtractExperienceGuards:
    """L4 守卫：extract_experience 要求 >= 2 个 assistant 轮次（且 content 非空）。

    守卫逻辑：
    assistant_turns = [t for t in transcript
                       if t.get("role") == "assistant" and t.get("content")]
    if len(assistant_turns) < 2:
        return
    """

    @pytest.mark.asyncio
    async def test_l4_zero_assistant_turns(self, db):
        """L4: 没有任何 assistant 轮次时跳过。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "Hello!"},
        ]
        await extractor.extract_experience(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l4_one_assistant_turn(self, db):
        """L4: 只有 1 个 assistant 轮次时跳过。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi!"},
        ]
        await extractor.extract_experience(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l4_one_assistant_turn_with_long_content(self, db):
        """L4: 即使 1 个 assistant 消息很长，仍需 >= 2 轮。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hello! How can I help you today? This is a very long response with lots of helpful information."},
        ]
        await extractor.extract_experience(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l4_two_assistant_turns(self, db):
        """L4: 恰好 2 个 assistant 轮次时正常执行。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "Help me!"},
            {"role": "assistant", "content": "I'll help."},
            {"role": "assistant", "content": "Done!"},
        ]
        await extractor.extract_experience(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l4_three_assistant_turns(self, db):
        """L4: 3 个 assistant 轮次时正常执行。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "Help me!"},
            {"role": "assistant", "content": "Step 1"},
            {"role": "assistant", "content": "Step 2"},
            {"role": "assistant", "content": "Step 3"},
        ]
        await extractor.extract_experience(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l4_assistant_with_empty_content(self, db):
        """L4: assistant 消息 content 为空不计入有效轮次。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "Help!"},
            {"role": "assistant", "content": ""},  # 空 content
            {"role": "assistant", "content": "Done!"},
        ]
        # 只有 1 个有效 assistant（第二个），应跳过
        await extractor.extract_experience(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l4_multiple_empty_assistant(self, db):
        """L4: 多个空 content 的 assistant 不计入。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "Help!"},
            {"role": "assistant", "content": ""},
            {"role": "assistant", "content": ""},
            {"role": "assistant", "content": ""},
        ]
        # 0 个有效 assistant，应跳过
        await extractor.extract_experience(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l4_assistant_with_none_content(self, db):
        """L4: assistant 消息 content 为 None 不计入。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "Help!"},
            {"role": "assistant", "content": None},  # type: ignore
            {"role": "assistant", "content": "Done!"},
        ]
        # 只有 1 个有效 assistant，应跳过
        await extractor.extract_experience(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l4_only_one_valid_assistant(self, db):
        """L4: 只有 1 个有 content 的 assistant 时跳过。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
            {"role": "assistant"},  # 无 content 字段
        ]
        await extractor.extract_experience(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l4_assistant_no_content_key(self, db):
        """L4: assistant 消息无 content 字段时不计入。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant"},  # 无 content
            {"role": "assistant", "content": "OK"},
        ]
        # 只有 1 个有效 assistant，应跳过
        await extractor.extract_experience(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l4_mixed_valid_and_invalid_assistant(self, db):
        """L4: 混合有效和无效 assistant，只计算有效的。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "OK1"},
            {"role": "assistant", "content": ""},  # 无效
            {"role": "assistant", "content": "OK2"},
        ]
        # 2 个有效 assistant >= 2，应执行
        await extractor.extract_experience(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l4_with_tool_calls_not_counted(self, db):
        """L4: tool_calls 不影响 assistant 轮次计数（只看 content）。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "Run command"},
            {
                "role": "assistant",
                "content": "Running...",
                "tool_calls": [{"name": "shell", "input": {"command": "ls"}}],
            },
            # 注意：这是 tool 结果消息，role 是 tool 不是 assistant
            {"role": "tool", "content": "file1 file2"},
            {
                "role": "assistant",
                "content": "Done!",
                "tool_calls": [{"name": "read", "input": {"path": "/a"}}],
            },
        ]
        # 2 个有 content 的 assistant 消息 >= 2，应执行
        await extractor.extract_experience(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l4_only_tool_messages(self, db):
        """L4: 只有 tool 消息（无 assistant content）时跳过。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "Run command"},
            {
                "role": "assistant",
                "tool_calls": [{"name": "shell", "input": {"command": "ls"}}],
            },
            {"role": "tool", "content": "result"},
        ]
        # 0 个有 content 的 assistant，应跳过
        await extractor.extract_experience(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l4_whitespace_content_not_valid(self, db):
        """L4: content 只有空白字符也不算有效轮次。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "   "},  # 空白
            {"role": "assistant", "content": "   "},  # 空白
        ]
        # 0 个有效 assistant，应跳过
        await extractor.extract_experience(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l4_many_assistant_turns(self, db):
        """L4: 多个 assistant 轮次时正常执行。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "Help with all of this"},
            {"role": "assistant", "content": "First, do X"},
            {"role": "assistant", "content": "Now do Y"},
            {"role": "assistant", "content": "Then Z"},
            {"role": "assistant", "content": "All done!"},
        ]
        await extractor.extract_experience(transcript, "ep-1")


# ---------------------------------------------------------------------------
# L3/L4 守卫组合测试
# ---------------------------------------------------------------------------


class TestExtractorGuardsIntegration:
    """L3/L4 守卫的集成测试。"""

    @pytest.mark.asyncio
    async def test_l3_guards_independent_of_l4(self, db):
        """L3 和 L4 守卫相互独立，各自独立生效。"""
        extractor = _make_extractor(db)

        # L3 跳过但 L4 通过的场景
        transcript_l3_skip = [
            {"role": "user", "content": "hi"},  # < 10 chars
            {"role": "assistant", "content": "OK"},
            {"role": "assistant", "content": "OK"},
        ]
        # L3 跳过
        await extractor.extract_user_profile(transcript_l3_skip, "ep-1")
        # L4 通过（2 个 assistant）
        await extractor.extract_experience(transcript_l3_skip, "ep-1")

        # L3 通过但 L4 跳过的场景
        transcript_l4_skip = [
            {"role": "user", "content": "This is a valid user message!"},  # >= 10
            {"role": "assistant", "content": "OK"},
        ]
        # L3 通过
        await extractor.extract_user_profile(transcript_l4_skip, "ep-1")
        # L4 跳过（只有 1 个 assistant）
        await extractor.extract_experience(transcript_l4_skip, "ep-1")

    @pytest.mark.asyncio
    async def test_both_guards_proceed(self, db):
        """L3 和 L4 都通过的场景。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "I need help with my code!"},  # >= 10
            {"role": "assistant", "content": "What seems to be the problem?"},
            {"role": "assistant", "content": "Here's a solution."},
        ]
        await extractor.extract_user_profile(transcript, "ep-1")
        await extractor.extract_experience(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_both_guards_skip(self, db):
        """L3 和 L4 都跳过的场景。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "hi"},  # < 10 chars
            {"role": "assistant", "content": "OK"},
        ]
        # L3 跳过（用户消息短）
        await extractor.extract_user_profile(transcript, "ep-1")
        # L4 跳过（只有 1 个 assistant）
        await extractor.extract_experience(transcript, "ep-1")


# ---------------------------------------------------------------------------
# 守卫边界条件测试
# ---------------------------------------------------------------------------


class TestExtractorGuardsEdgeCases:
    """L3/L4 守卫边界条件测试。"""

    @pytest.mark.asyncio
    async def test_l3_with_chinese_characters(self, db):
        """L3: 中文字符按字符数计算。"""
        extractor = _make_extractor(db)
        # 10 个中文字符
        transcript = [
            {"role": "user", "content": "你好世界你好世界你好世界"},  # 10+ chars
            {"role": "assistant", "content": "OK"},
        ]
        await extractor.extract_user_profile(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l3_with_emoji(self, db):
        """L3: emoji 按字符数计算。"""
        extractor = _make_extractor(db)
        # 10 个 emoji
        transcript = [
            {"role": "user", "content": "😀😁😂😃😄😁😃😄😁😃"},  # 10 chars
            {"role": "assistant", "content": "OK"},
        ]
        await extractor.extract_user_profile(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l4_single_assistant_with_multiline_content(self, db):
        """L4: 单个 assistant 多行内容仍只有 1 轮。"""
        extractor = _make_extractor(db)
        transcript = [
            {"role": "user", "content": "Tell me a story"},
            {"role": "assistant", "content": "Once upon a time...\nThen...\nFinally...\nThe end."},
        ]
        # 只有 1 个 assistant 轮次，即使内容很多也跳过
        await extractor.extract_experience(transcript, "ep-1")

    @pytest.mark.asyncio
    async def test_l3_l4_none_values_in_list(self, db):
        """L3/L4: transcript 列表中可能出现 None 值。"""
        extractor = _make_extractor(db)
        transcript = [
            None,  # type: ignore
            {"role": "user", "content": "Hello, how are you?"},
            {"role": "assistant", "content": "Fine!"},
            {"role": "assistant", "content": "And you?"},
        ]
        # 应安全处理 None 值
        await extractor.extract_user_profile(transcript, "ep-1")  # type: ignore
        await extractor.extract_experience(transcript, "ep-1")  # type: ignore
