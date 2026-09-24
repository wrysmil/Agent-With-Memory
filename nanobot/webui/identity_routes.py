"""WebUI settings domain handler for identity file management.

Mirrors ``memory_routes.py``: a transport-neutral handler whose
``handle(action, request)`` dispatches against injected operations and returns
a ``SettingsRouteResult``. The router (``settings_routes.py``, WU-04) owns
path → action mapping, WebSocket vs HTTP gating and the transport layer.

Read actions (list/read) are safe over plain HTTP GET; write/reload mutate or
re-derive state and go through the authenticated WebSocket
``requestMutation`` allowlist upstream. This module knows neither about the
transport nor about the concrete file implementation — only about the
``IdentitySettingsOperations`` protocol, whose callables arrive with their
workspace-scoped dependencies already bound (the gateway uses ``partial``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from nanobot.webui.settings_contracts import (
    SettingsRequest,
    SettingsRouteResult,
    WebUISettingsError,
    query_first,
)


@dataclass(frozen=True)
class IdentitySettingsOperations:
    """Transport-neutral entry points injected into the handler.

    Every field is already bound to its workspace-scoped dependency (the
    gateway wires them with ``functools.partial``), so ``dispatch`` passes
    business arguments only. Kept as a plain protocol so tests can substitute
    in-memory doubles instead of touching disk.
    """

    list_files: Callable[..., dict[str, Any]]
    read_file: Callable[..., dict[str, Any]]
    write_file: Callable[..., dict[str, Any]]
    reload: Callable[..., dict[str, Any]]
    list_presets: Callable[..., dict[str, Any]]


# Canonical set of actions this domain understands; the settings router uses
# it to route ``("identity", action)`` into this handler.
IDENTITY_ACTION_NAMES = frozenset({
    "identity-list-files",
    "identity-read-file",
    "identity-write-file",
    "identity-reload",
    "identity-list-presets",
})


class IdentitySettingsHandler:
    """Route a single identity-domain action against injected operations."""

    def __init__(self, operations: IdentitySettingsOperations) -> None:
        self._ops = operations

    def handle(
        self,
        action: str,
        request: SettingsRequest,
    ) -> SettingsRouteResult:
        if action not in IDENTITY_ACTION_NAMES:
            return SettingsRouteResult.failure(404, f"unknown identity action: {action}")
        try:
            payload = dispatch(self._ops, action, request)
        except WebUISettingsError as exc:
            return SettingsRouteResult.failure(exc.status, exc.message)
        return SettingsRouteResult.success(payload)


# ---- per-action dispatch ---------------------------------------------------


def dispatch(
    operations: IdentitySettingsOperations,
    action: str,
    request: SettingsRequest,
) -> dict[str, Any]:
    query = request.query
    payload = request.payload or {}

    if action == "identity-list-files":
        return operations.list_files()

    if action == "identity-list-presets":
        return operations.list_presets()

    if action == "identity-read-file":
        # Read target comes from the query string (?name=SOUL.md).
        raw = query_first(query, "name")
        if not isinstance(raw, str) or not raw.strip():
            raise WebUISettingsError("name is required")
        return operations.read_file(raw.strip())

    if action == "identity-write-file":
        # Write target and body both come from the mutation payload.
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise WebUISettingsError("name is required")
        if "content" not in payload:
            raise WebUISettingsError("content is required")
        content = payload["content"]
        if not isinstance(content, str):
            raise WebUISettingsError("content must be a string")
        return operations.write_file(name=name.strip(), content=content)

    if action == "identity-reload":
        return operations.reload()

    # Unreachable for known actions; kept as a guard against future additions.
    raise WebUISettingsError(f"unsupported identity action: {action}")
