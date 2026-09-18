"""Pure payload conversion + action functions for the WebUI identity domain.

Owns the JSON wire format for identity file listing/reading/writing and the
transport-neutral entry points consumed by ``identity_routes.py``. Mirrors
``memory_api.py``: it does NOT depend on HTTP/WebSocket machinery, so the
router (``settings_routes.py``, WU-04) can wire it to either transport.

The only documented cross-domain write is ``MEMORY.md``: it is a derived
artifact owned by ``MemoryLifecycle`` (Dream), so the manual-edit path must
go through the lifecycle's backup semantics instead of ``IdentityStore``.
That split lives here — ``IdentityStore.write_file`` refuses MEMORY.md on
purpose, so a caller that forgets the branch gets a loud 500 rather than a
silent unbacked write.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.identity.catalog import CHAR_LIMIT, LIFECYCLE_OWNED_FILES
from nanobot.identity.store import IdentityStore, IdentityStoreError
from nanobot.webui.settings_contracts import WebUISettingsError


def identity_list_files(workspace: Path) -> dict[str, Any]:
    """Return the identity file manifest plus the shared char limit.

    ``charLimit`` is read from ``CHAR_LIMIT`` rather than restated here so the
    frontend's identity editor and MEMORY.md truncation can never drift apart.
    """
    try:
        files = IdentityStore(workspace).list_files()
    except IdentityStoreError as exc:
        raise WebUISettingsError(exc.message, status=exc.status) from exc
    return {"files": files, "charLimit": CHAR_LIMIT}


def identity_read_file(workspace: Path, name: str) -> dict[str, Any]:
    """Read one whitelisted identity file. Unknown/escaping names are 4xx."""
    try:
        content = IdentityStore(workspace).read_file(name)
    except IdentityStoreError as exc:
        raise WebUISettingsError(exc.message, status=exc.status) from exc
    return {"name": name, "content": content}


def identity_write_file(
    workspace: Path,
    name: str,
    content: str,
    *,
    lifecycle: Any = None,
) -> dict[str, Any]:
    """Write one identity file, routing MEMORY.md to the memory lifecycle.

    ``MEMORY.md`` is a derived artifact: it is written through
    ``MemoryLifecycle.write_memory_md`` so the pre-write ``.bak`` backup is
    preserved. Everything else goes through ``IdentityStore``.
    """
    if name in LIFECYCLE_OWNED_FILES:
        if lifecycle is None:
            raise WebUISettingsError("MEMORY.md 写入需要 MemoryLifecycle", status=500)
        try:
            lifecycle.write_memory_md(content)
        except ValueError as exc:
            # Length / type validation from the lifecycle surfaces as a 4xx.
            raise WebUISettingsError(str(exc), status=400) from exc
        except OSError as exc:
            logger.exception("Failed to write MEMORY.md via MemoryLifecycle")
            raise WebUISettingsError("MEMORY.md 写入失败", status=500) from exc
        return {"name": name, "saved": True, "autoRegenerated": True}

    try:
        IdentityStore(workspace).write_file(name, content)
    except IdentityStoreError as exc:
        raise WebUISettingsError(exc.message, status=exc.status) from exc
    return {"name": name, "saved": True}


def identity_reload(*, refresh_memory_md: Any = None) -> dict[str, Any]:
    """Re-read identity files from disk for the injection path.

    Injection reads the files on every turn, so there is nothing to cache —
    the only side effect worth triggering is a MEMORY.md re-derivation. When
    the gateway has not injected the service (e.g. standalone HTTP handler),
    report ``skipped`` instead of failing; a reload is never worth a 500.
    """
    if refresh_memory_md is None:
        return {"status": "skipped", "reason": "no_services"}
    try:
        return dict(refresh_memory_md())
    except Exception:
        logger.exception("identity_reload: MEMORY.md refresh failed")
        return {"status": "error"}


def identity_compile(mode: str) -> dict[str, Any]:
    """Placeholder for prompt compilation.

    nanobot has no PromptCompiler yet (staged rollout confirmed with the
    user), so this reports ``not_enabled`` rather than pretending to compile.
    The action name exists now so the frontend can wire the button and the
    response shape stays stable when compilation lands.
    """
    return {"status": "not_enabled", "mode": mode or "rules"}
