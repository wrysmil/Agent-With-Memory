"""provider 错误的统一识别（记忆抽取链路）。

背景
----
provider 层在 HTTP 失败时**不抛异常**：``_handle_error`` 把错误体拼成
``LLMResponse(content="Error: {...}", finish_reason="error")``
（``nanobot/providers/openai_compat_provider.py``），重试层对非瞬时错误
（401/403/...）也原样返回给调用方（``nanobot/providers/base.py``）。

记忆抽取原先只看 ``content``，于是：

- 日志报成 ``unparseable JSON``，真实原因（如 403）被埋进 ``head=`` 里；
- 轨道失败原因记成 ``invalid_json``；
- 更糟的是 ``ScratchpadWriter`` 会把 ``Error: {...}`` 当正文写进草稿本落库。

用法：读 ``content`` 之前先问一句：:

    reason = response_error(response)
    if reason is not None:
        logger.warning("memory extraction LLM call failed ({}): {}", track, reason)
        return None, f"call_failed: {reason}"
"""

from __future__ import annotations

from typing import Any

__all__ = ["response_error"]

_PREFIX = "llm_error"


def response_error(response: Any) -> str | None:
    """provider 用 content 承载错误时返回简短原因；正常响应返回 ``None``。

    只认 ``finish_reason == "error"``——这是 provider 层标记错误响应的唯一约定，
    因此对既有的「只有 content 的 duck-typed 响应」完全无影响。
    """
    if getattr(response, "finish_reason", None) != "error":
        return None

    parts: list[str] = []
    status = getattr(response, "error_status_code", None)
    if status is not None:
        parts.append(f"HTTP {status}")
    for value in (
        getattr(response, "error_kind", None),
        getattr(response, "error_type", None),
        getattr(response, "error_code", None),
    ):
        text = str(value).strip() if value else ""
        if text and text not in parts:
            parts.append(text)
    if not parts:
        return _PREFIX
    return f"{_PREFIX}: {' '.join(parts)}"
