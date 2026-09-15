"""会话标签：日志里用来一眼认出「是哪个会话」的短字符串。

口径与 WebUI 侧边栏会话列表一致（用户 2026-09-15 以截图确认）：
``metadata["title"]`` 优先，标题为空时回退**首条用户消息**（截断）。

为什么需要它：``Processing message from <channel>:<sender_id>`` 打的是发送者
标识，两个会话在日志里长得一模一样——2026-09-15 的 idle 抽取排障就因此
误判成「同一段连续对话」。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, cast

from nanobot.utils.helpers import strip_think

__all__ = [
    "SESSION_LABEL_MAX_CHARS",
    "SESSION_LABEL_EMPTY",
    "session_label",
    "session_label_suffix",
]

#: 标签最长字符数（超出截断并加省略号）。
SESSION_LABEL_MAX_CHARS = 30
#: 既无标题也无用户消息时的占位。
SESSION_LABEL_EMPTY = "(none)"

# 与 ``nanobot/session/webui_turns.py`` 的 ``WEBUI_TITLE_METADATA_KEY`` /
# ``WEBUI_TITLE_USER_EDITED_METADATA_KEY`` 保持同值。此处不直接 import：那个模块
# 会拉起 bus / providers 一整串重依赖，而本模块服务于 memory 抽取的日志路径。
_TITLE_KEY = "title"
_TITLE_USER_EDITED_KEY = "title_user_edited"


def session_label(
    messages: Iterable[Mapping[str, Any]] | None,
    metadata: Mapping[str, Any] | None = None,
    *,
    max_chars: int = SESSION_LABEL_MAX_CHARS,
) -> str:
    """返回会话的短标签：标题优先，空则首条用户消息，都没有则 ``(none)``。

    Args:
        messages: 会话转录；只用其首条非空 user 消息。
        metadata: 会话 metadata；读 ``title`` / ``title_user_edited``。
        max_chars: 截断长度。

    Returns:
        可直接拼进日志的短字符串（永不为空）。
    """
    title = _title_from(metadata)
    if title:
        return _truncate(title, max_chars)
    preview = _first_user_message(messages)
    if preview:
        return _truncate(preview, max_chars)
    return SESSION_LABEL_EMPTY


def session_label_suffix(label: str) -> str:
    """日志后缀：`` [摘要: xxx]``；``label`` 为空时返回空串（不留空括号）。"""
    return f" [摘要: {label}]" if label else ""


def _title_from(metadata: Mapping[str, Any] | None) -> str:
    """与 ``session.manager._metadata_title`` 同口径：用户改过的不做 think 剥离。"""
    if not isinstance(metadata, Mapping):
        return ""
    data = cast(Mapping[object, object], metadata)
    title = data.get(_TITLE_KEY)
    if not isinstance(title, str):
        return ""
    if data.get(_TITLE_USER_EDITED_KEY) is True:
        return title.strip()
    return strip_think(title).strip()


def _first_user_message(messages: Iterable[Mapping[str, Any]] | None) -> str:
    if not messages:
        return ""
    for message in messages:
        if message.get("role") != "user":
            continue
        text = _content_text(message.get("content")).strip()
        if text:
            return text
    return ""


def _content_text(content: Any) -> str:
    """把消息 content（str / content-block list / 其他）归一化为纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in cast(list[object], content):
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping):
                text = cast(Mapping[object, object], block).get("text")
                if isinstance(text, str):
                    parts.append(text)
        return " ".join(parts)
    return ""


def _truncate(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"
