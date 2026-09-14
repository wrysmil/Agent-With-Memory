"""Hot-reload signal for the user-facing memory toggle.

Uses the same bus-based ack pattern as image_generation reload.
The loop does not rebuild any objects here — the provider closures already read
the latest ``defaults.memory_enabled`` on every callback, so a successful ack
is sufficient to guarantee the new value takes effect on the next message.
"""
from __future__ import annotations

import asyncio
from typing import Any

from nanobot.bus.events import (
    INBOUND_META_RUNTIME_CONTROL,
    RUNTIME_CONTROL_ACK,
    RUNTIME_CONTROL_MEMORY_RELOAD,
    InboundMessage,
)
from nanobot.bus.queue import MessageBus


async def request_memory_reload(
    bus: MessageBus,
    *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Ask the running agent loop to refresh its memory-enabled state.

    Returns ``{"reloaded": True}`` on success.
    Raises ``asyncio.TimeoutError`` if the loop does not ack within *timeout*.
    Any bus/loop exception is propagated.
    """
    loop = asyncio.get_running_loop()
    ack: asyncio.Future[dict[str, Any]] = loop.create_future()
    await bus.publish_inbound(
        InboundMessage(
            channel="system",
            sender_id="webui-settings",
            chat_id="runtime",
            content=RUNTIME_CONTROL_MEMORY_RELOAD,
            metadata={
                INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_MEMORY_RELOAD,
                RUNTIME_CONTROL_ACK: ack,
            },
        )
    )
    return await asyncio.wait_for(ack, timeout=timeout)


async def handle_memory_reload(
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Listener entry point registered in the gateway.

    The loop acknowledges without rebuilding anything — the provider closures
    already read ``config.agents.defaults.memory_enabled`` at call time.
    """
    ack: asyncio.Future[dict[str, Any]] | None = metadata.get(RUNTIME_CONTROL_ACK)
    result = {"reloaded": True}
    if ack is not None and not ack.done():
        ack.set_result(result)
    return result
