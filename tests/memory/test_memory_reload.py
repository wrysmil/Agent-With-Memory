"""Tests for the memory reload bus event."""
from __future__ import annotations

import asyncio

import pytest

from nanobot.bus.events import RUNTIME_CONTROL_MEMORY_RELOAD, InboundMessage
from nanobot.memory.reload import handle_memory_reload, request_memory_reload


@pytest.mark.asyncio
async def test_request_memory_reload_publishes_correct_message():
    """Verify request_memory_reload sends the right inbound message."""
    captured: list[InboundMessage] = []
    resolved_ack = {"set": False}

    class CapturingBus:
        async def publish_inbound(self, msg: InboundMessage) -> None:
            captured.append(msg)
            ack = msg.metadata.get("_ack")
            if ack is not None and not ack.done():
                ack.set_result({"reloaded": True})
                resolved_ack["set"] = True

    await request_memory_reload(CapturingBus(), timeout=2.0)

    assert len(captured) == 1
    msg = captured[0]
    assert msg.channel == "system"
    assert msg.sender_id == "webui-settings"
    assert msg.content == RUNTIME_CONTROL_MEMORY_RELOAD
    assert msg.metadata.get("_runtime_control") == RUNTIME_CONTROL_MEMORY_RELOAD
    assert resolved_ack["set"]


@pytest.mark.asyncio
async def test_handle_memory_reload_sets_ack():
    """Listener must call ack.set_result so the caller unblocks."""
    loop = asyncio.get_running_loop()
    ack = loop.create_future()
    metadata = {"_ack": ack}
    result = await handle_memory_reload(metadata)
    assert result == {"reloaded": True}
    assert ack.done()
    # .result() is a method on asyncio.Future
    assert ack.result() == {"reloaded": True}


@pytest.mark.asyncio
async def test_handle_memory_reload_no_ack_is_noop():
    """Missing ack must not raise."""
    metadata = {}
    result = await handle_memory_reload(metadata)
    assert result == {"reloaded": True}
