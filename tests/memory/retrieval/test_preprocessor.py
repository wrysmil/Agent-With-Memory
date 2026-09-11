"""T-02: MemoryQueryPreprocessor — Gate + anti-injection tests."""
from __future__ import annotations

import pytest

from nanobot.memory.retrieval.preprocessor import MemoryQueryPreprocessor


class TestGate:
    """Gate conditions: empty / control_only / too_short / short_without_context."""

    @pytest.mark.parametrize("text", ["", "   ", None])
    def test_should_skip_empty(self, text):
        skip, reason = MemoryQueryPreprocessor.should_skip_retrieval(text, [])
        assert skip is True
        assert reason == "empty"

    @pytest.mark.parametrize("text", ["好", "嗯", "ok", "yes", "stop", "继续", "可以", "收到"])
    def test_should_skip_control_only(self, text):
        skip, reason = MemoryQueryPreprocessor.should_skip_retrieval(text, [])
        assert skip is True
        assert reason == "control_only"

    @pytest.mark.parametrize("text", ["hi"])  # ≤3 chars + no _KEEP_SHORT_HINTS; "啊" is in _CONTROL_ONLY
    def test_should_skip_too_short(self, text):
        skip, reason = MemoryQueryPreprocessor.should_skip_retrieval(text, [])
        assert skip is True
        assert reason == "too_short"

    def test_should_skip_short_without_context(self):
        """12-char Chinese phrase without context triggers skip."""
        skip, reason = MemoryQueryPreprocessor.should_skip_retrieval("明天天气怎么样", [])
        assert skip is True
        assert reason == "short_without_context"

    def test_should_pass_with_context(self):
        """Rich query with prior messages should NOT skip."""
        skip, reason = MemoryQueryPreprocessor.should_skip_retrieval(
            "我之前写的那个 Python 爬虫脚本还能用吗",
            [{"role": "user", "content": "..."}],
        )
        assert skip is False
        assert reason == ""

    def test_should_pass_with_hint_char(self):
        """Short query with _KEEP_SHORT_HINTS should NOT skip even without context."""
        skip, reason = MemoryQueryPreprocessor.should_skip_retrieval("明天天气?", [])
        assert skip is False
        assert reason == ""

    def test_should_pass_long_query(self):
        """Query longer than 12 chars should NOT skip regardless of context."""
        skip, reason = MemoryQueryPreprocessor.should_skip_retrieval(
            "你好，请问明天的天气如何呢", []
        )
        assert skip is False
        assert reason == ""


class TestAntiInjection:
    """Injection block removal."""

    def test_clean_query_removes_related_memory_section(self):
        raw = (
            "用户问题: 你好\n"
            "## 相关记忆\n过去我们聊过 Python\n##\n"
            "用户问题: 真正的问题"
        )
        cleaned = MemoryQueryPreprocessor.clean_query(raw)
        assert "相关记忆" not in cleaned
        assert "真正的问题" in cleaned

    def test_clean_query_removes_vault_context(self):
        raw = "<vault-context>block me</vault-context>\n真正的问题"
        cleaned = MemoryQueryPreprocessor.clean_query(raw)
        assert "<vault-context>" not in cleaned
        assert "真正的问题" in cleaned

    def test_clean_query_removes_memory_tag(self):
        raw = "<memory>injected content</memory>\n真正的问题"
        cleaned = MemoryQueryPreprocessor.clean_query(raw)
        assert "<memory>" not in cleaned
        assert "真正的问题" in cleaned

    def test_clean_query_removes_long_term_memory_tag(self):
        raw = "<long-term-memory>injected content</long-term-memory>\n真正的问题"
        cleaned = MemoryQueryPreprocessor.clean_query(raw)
        assert "<long-term-memory>" not in cleaned
        assert "真正的问题" in cleaned

    def test_clean_query_removes_core_memory_section(self):
        raw = "## 核心记忆\n过去我们聊过\n## 长期记忆\n更多信息\n真正的问题"
        cleaned = MemoryQueryPreprocessor.clean_query(raw)
        assert "核心记忆" not in cleaned
        assert "长期记忆" not in cleaned
        assert "真正的问题" in cleaned

    def test_clean_query_removes_sections_with_no_trailing_content(self):
        """When a section is the last thing, it gets fully stripped."""
        raw = "## 核心记忆\n过去我们聊过\n## 长期记忆\n更多信息"
        cleaned = MemoryQueryPreprocessor.clean_query(raw)
        assert "核心记忆" not in cleaned
        assert "长期记忆" not in cleaned
        # content before sections is preserved
        assert "过去我们聊过" in cleaned
        assert "更多信息" in cleaned


class TestPrepare:
    """Combined prepare() method."""

    def test_prepare_returns_cleaned_and_skip_flag(self):
        prepared = MemoryQueryPreprocessor.prepare(
            "## 相关记忆\n之前聊过一些话题\ninject",
            recent_messages=[],
        )
        # "之前聊过一些话题\ninject" is not in _CONTROL_ONLY and > 12 chars
        assert prepared.skip is False
        assert "相关记忆" not in prepared.cleaned_query

    def test_prepare_control_word_is_skipped(self):
        """A pure control word with no injection blocks triggers skip."""
        prepared = MemoryQueryPreprocessor.prepare("好", recent_messages=[])
        assert prepared.skip is True
        assert prepared.reason == "control_only"
        assert prepared.cleaned_query == "好"

    def test_prepare_pass_case(self):
        prepared = MemoryQueryPreprocessor.prepare(
            "我之前写的那个 Python 爬虫脚本还能用吗",
            recent_messages=[{"role": "user", "content": "..."}],
        )
        assert prepared.skip is False
        assert prepared.reason == ""
        assert "Python" in prepared.cleaned_query

    def test_prepare_none_query(self):
        """None input should be treated as empty."""
        prepared = MemoryQueryPreprocessor.prepare(None, recent_messages=[])
        assert prepared.skip is True
        assert prepared.reason == "empty"
