"""会话结束事件数据结构单测。"""
from datetime import datetime

from nanobot.memory.session_end_event import SessionEndEvent, SessionEndReason


def test_session_end_event_basic_construction():
    evt = SessionEndEvent(
        session_key="user-42:cli",
        reason=SessionEndReason.USER_CLOSE,
        transcript=[{"role": "user", "content": "hi"}],
        emitted_at=datetime(2026, 9, 12, 10, 0, 0),
    )
    assert evt.session_key == "user-42:cli"
    assert evt.reason == SessionEndReason.USER_CLOSE
    assert len(evt.transcript) == 1
    assert evt.emitted_at.year == 2026


def test_session_end_reason_values():
    assert SessionEndReason.USER_CLOSE.value == "user_close"
    assert SessionEndReason.IDLE_TIMEOUT.value == "idle_timeout"
    assert SessionEndReason.PROCESS_SHUTDOWN.value == "process_shutdown"
    assert SessionEndReason.CHANNEL_DISCONNECT.value == "channel_disconnect"


def test_session_end_event_transcript_is_isolated():
    src = [{"role": "user", "content": "x"}]
    evt = SessionEndEvent(
        session_key="k",
        reason=SessionEndReason.USER_CLOSE,
        transcript=src,
        emitted_at=datetime.now(),
    )
    src.append({"role": "user", "content": "y"})
    assert len(evt.transcript) == 1  # 不应被外部修改影响