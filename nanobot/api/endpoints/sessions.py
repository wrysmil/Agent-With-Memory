"""Session management API endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aiohttp import web

from nanobot.api.schemas import SessionEndRequest
from nanobot.memory.session_end_event import SessionEndEvent, SessionEndReason

if TYPE_CHECKING:
    from nanobot.session.manager import SessionManager


async def handle_session_end(request: web.Request) -> web.Response:
    """POST /api/sessions/{session_key}/end — emit a SessionEndEvent.

    Allows frontend to trigger a USER_CLOSE event when the user clicks "end session".
    """
    session_key = request.match_info.get("session_key", "")

    # Parse request body
    try:
        body = await request.json()
    except Exception:
        body = {}

    schema = SessionEndRequest(**body)
    reason_str = schema.reason

    # Validate reason; fall back to USER_CLOSE for unknown values
    try:
        reason = SessionEndReason(reason_str)
    except ValueError:
        reason = SessionEndReason.USER_CLOSE

    # Get session_manager from app state
    session_manager: SessionManager | None = request.app.get("session_manager")

    transcript: list[dict[str, Any]] = []
    if session_manager is not None:
        # Try to retrieve transcript for the session
        session = session_manager.get_cached(session_key)
        if session is not None:
            transcript = session.messages
        else:
            # Attempt to load from storage
            stored = session_manager.read_session_file(session_key)
            if stored is not None:
                transcript = stored.get("messages", [])

    # Emit the session end event
    event = SessionEndEvent(
        session_key=session_key,
        reason=reason,
        transcript=transcript,
    )

    # Get event_bus and publish
    event_bus = request.app.get("event_bus")
    if event_bus is not None:
        await event_bus.publish(event)

    return web.json_response({
        "status": "ok",
        "session_key": session_key,
    })
