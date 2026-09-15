"""``session_label``：记忆抽取日志里「会话摘要」的口径（2026-09-15 spec §4.1）。

口径 = WebUI 侧边栏会话列表那一行文字：``metadata["title"]`` 优先，
标题为空时回退**首条用户消息**（截断）。
"""

from __future__ import annotations

from pathlib import Path

from nanobot.session import labels as labels_module
from nanobot.session.labels import SESSION_LABEL_MAX_CHARS, session_label


class TestTitlePriority:
    def test_prefers_title(self):
        messages = [{"role": "user", "content": "你好，我的名字叫做黄启华"}]
        assert (
            session_label(messages, {"title": "初次问候与助手介绍"})
            == "初次问候与助手介绍"
        )

    def test_falls_back_to_first_user_message_when_title_empty(self):
        """真实案例：首轮 403 导致标题生成没跑，title 是空串。"""
        messages = [{"role": "user", "content": "你好，我的名字叫做黄启华"}]
        assert session_label(messages, {"title": ""}) == "你好，我的名字叫做黄启华"

    def test_falls_back_when_title_missing(self):
        assert session_label([{"role": "user", "content": "今日金价"}], None) == "今日金价"

    def test_ignores_non_string_title(self):
        messages = [{"role": "user", "content": "今日金价"}]
        assert session_label(messages, {"title": 123}) == "今日金价"

    def test_user_edited_title_is_not_think_stripped(self):
        messages = [{"role": "user", "content": "x"}]
        metadata = {"title": "用户改的标题", "title_user_edited": True}
        assert session_label(messages, metadata) == "用户改的标题"

    def test_generated_title_gets_think_stripped(self):
        messages = [{"role": "user", "content": "x"}]
        metadata = {"title": "<thinking>\n嗯，需要起个标题\n</thinking>初次问候与助手介绍"}
        assert session_label(messages, metadata) == "初次问候与助手介绍"


class TestPreviewSelection:
    def test_skips_assistant_and_blank_user_messages(self):
        messages = [
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "   "},
            {"role": "user", "content": "真正的问题"},
        ]
        assert session_label(messages, {}) == "真正的问题"

    def test_flattens_content_blocks(self):
        messages = [{"role": "user", "content": [{"type": "text", "text": "块内容"}]}]
        assert session_label(messages, {}) == "块内容"


class TestTruncation:
    def test_truncates_long_label(self):
        label = session_label([{"role": "user", "content": "问" * 100}], {})
        assert len(label) == SESSION_LABEL_MAX_CHARS + 1
        assert label.endswith("…")

    def test_short_label_untouched(self):
        assert session_label([{"role": "user", "content": "短"}], {}) == "短"

    def test_max_chars_override(self):
        label = session_label([{"role": "user", "content": "abcdef"}], {}, max_chars=3)
        assert label == "abc…"


class TestEmpty:
    def test_no_messages_returns_none_marker(self):
        assert session_label([], {}) == "(none)"

    def test_only_assistant_returns_none_marker(self):
        assert session_label([{"role": "assistant", "content": "hi"}], {}) == "(none)"

    def test_none_messages_returns_none_marker(self):
        assert session_label(None, None) == "(none)"


class TestMetadataKeyDriftGuard:
    """``_TITLE_KEY`` / ``_TITLE_USER_EDITED_KEY`` 是 ``webui_turns`` 常量的复本。

    复本的理由是依赖轻量（见 ``labels`` 模块注释），代价是可能悄悄漂移。
    这里用**文本比对**而非 import 来兜底：``webui_turns`` 会拉起 bus / providers
    重依赖，测试不值得为它付这笔钱。
    """

    def _webui_turns_source(self) -> str:
        module_path = Path(labels_module.__file__).with_name("webui_turns.py")
        return module_path.read_text(encoding="utf-8")

    def test_title_key_matches_webui_turns(self):
        assert (
            f'WEBUI_TITLE_METADATA_KEY = "{labels_module._TITLE_KEY}"'
            in self._webui_turns_source()
        )

    def test_title_user_edited_key_matches_webui_turns(self):
        assert (
            f'WEBUI_TITLE_USER_EDITED_METADATA_KEY = "{labels_module._TITLE_USER_EDITED_KEY}"'
            in self._webui_turns_source()
        )
