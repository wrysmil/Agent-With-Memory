"""Wiring tests for the identity settings domain (WU-04).

``test_identity_routes.py`` proves the handler itself is correct. This file
proves the *plumbing* around it: the three registries that must agree before
the frontend can reach the domain at all.

The failure this guards against is silent. A path missing from
``_SYSTEM_ROUTES`` 404s; a WS action missing from ``_WEBUI_MUTATION_PATHS``
404s at the translation layer before routing is even attempted; a write path
missing from ``_SETTINGS_MUTATION_PATHS`` lets an unauthenticated GET mutate
state. None of these raise at import time.
"""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from websockets.datastructures import Headers
from websockets.http11 import Response

from nanobot.identity.catalog import IDENTITY_DIR_NAME
from nanobot.webui import identity_api
from nanobot.webui.http_utils import http_json_response
from nanobot.webui.identity_routes import IdentitySettingsOperations
from nanobot.webui.settings_routes import (
    _IDENTITY_MUTATION_PATHS,
    _SETTINGS_MUTATION_PATHS,
    _SYSTEM_ROUTES,
    WebUISettingsRouter,
    _null_identity_operations,
)
from nanobot.webui.settings_services import WebUISettingsServices
from nanobot.webui.ws_http import _WEBUI_MUTATION_PATHS, GatewayHTTPHandler

# path -> canonical action, as specified for WU-04.
EXPECTED_SYSTEM_ROUTES = {
    "/api/settings/identity/files": "identity-list-files",
    "/api/settings/identity/file": "identity-read-file",
    "/api/settings/identity/file/save": "identity-write-file",
    "/api/settings/identity/reload": "identity-reload",
    "/api/settings/identity/compile": "identity-compile",
    "/api/settings/identity/presets": "identity-list-presets",
}

EXPECTED_MUTATION_PATHS = frozenset({
    "/api/settings/identity/file/save",
    "/api/settings/identity/reload",
    "/api/settings/identity/compile",
})

EXPECTED_WS_ACTIONS = {
    "identity.file.save": "/api/settings/identity/file/save",
    "identity.reload": "/api/settings/identity/reload",
    "identity.compile": "/api/settings/identity/compile",
}


# ---- R1: _SYSTEM_ROUTES -----------------------------------------------------


@pytest.mark.parametrize("path,action", sorted(EXPECTED_SYSTEM_ROUTES.items()))
def test_identity_paths_are_in_system_routes(path: str, action: str) -> None:
    assert _SYSTEM_ROUTES.get(path) == action


def test_system_routes_has_exactly_six_identity_entries() -> None:
    assert {
        path for path in _SYSTEM_ROUTES if "/identity/" in path
    } == set(EXPECTED_SYSTEM_ROUTES)


# ---- R2: _SETTINGS_MUTATION_PATHS -------------------------------------------


@pytest.mark.parametrize("path", sorted(EXPECTED_MUTATION_PATHS))
def test_identity_write_paths_require_authenticated_websocket(path: str) -> None:
    assert path in _SETTINGS_MUTATION_PATHS
    assert WebUISettingsRouter.is_mutation_path(path)


def test_identity_mutation_set_matches_registered_writes() -> None:
    assert _IDENTITY_MUTATION_PATHS == EXPECTED_MUTATION_PATHS


def test_identity_reads_are_not_mutations() -> None:
    """读路径走普通 GET；误登记会让整个 settings 页只能从 WS 调用。"""
    for path in (
        "/api/settings/identity/files",
        "/api/settings/identity/file",
        "/api/settings/identity/presets",
    ):
        assert path not in _SETTINGS_MUTATION_PATHS
        assert not WebUISettingsRouter.is_mutation_path(path)


# ---- R3: _WEBUI_MUTATION_PATHS (WS action -> HTTP path) ---------------------


@pytest.mark.parametrize("action,path", sorted(EXPECTED_WS_ACTIONS.items()))
def test_ws_action_maps_to_http_path(action: str, path: str) -> None:
    assert _WEBUI_MUTATION_PATHS.get(action) == path
    resolved = GatewayHTTPHandler._webui_mutation_path(action, {})
    assert resolved == path


def test_ws_action_names_match_frontend_contract() -> None:
    """前端提交的就是这两个字符串；改名即断链。"""
    assert {a for a in _WEBUI_MUTATION_PATHS if a.startswith("identity.")} == set(
        EXPECTED_WS_ACTIONS
    )


def test_compile_is_a_registered_ws_action() -> None:
    """``identity.compile`` 现在是可触达的真编译，不再是刻意不登记的占位。

    占位时代它不登记（登记了就变成点了没反应的静默 no-op），前端靠 404 走
    「编译能力尚未启用」提示分支。现在 ``identity_compile`` 会真写
    ``identity/runtime/`` 产物、前端也要用返回的 ``compiledFiles`` 计数做反馈，
    所以漏登记的表现会退化成「按钮一点就 404」——正是这次要修的毛病。
    """
    assert _WEBUI_MUTATION_PATHS["identity.compile"] == "/api/settings/identity/compile"
    # HTTP 层的写路径登记同样在，避免匿名 GET 触发编译。
    assert "/api/settings/identity/compile" in _SETTINGS_MUTATION_PATHS


def test_unregistered_ws_action_is_404() -> None:
    result = GatewayHTTPHandler._webui_mutation_path("identity.frobnicate", {})
    assert isinstance(result, Response)
    assert result.status_code == 404


def test_ws_mapped_paths_are_known_mutations() -> None:
    """翻译表与 mutation 名单必须对得上，否则跳到目标路径也会被 404/405 弹回。"""
    for path in EXPECTED_WS_ACTIONS.values():
        assert WebUISettingsRouter.is_mutation_path(path)


# ---- R4: null fallback ------------------------------------------------------


def test_null_identity_operations_returns_503_for_every_action() -> None:
    from nanobot.webui.identity_routes import IdentitySettingsHandler
    from nanobot.webui.settings_contracts import SettingsRequest

    handler = IdentitySettingsHandler(_null_identity_operations())
    # 输入必须合法，否则 dispatch 会先在参数校验上返回 400，碰不到 operations。
    cases = [
        ("identity-list-files", SettingsRequest(query={})),
        ("identity-read-file", SettingsRequest(query={"name": ["SOUL.md"]})),
        (
            "identity-write-file",
            SettingsRequest(
                query={}, payload={"name": "SOUL.md", "content": "# x"}
            ),
        ),
        ("identity-reload", SettingsRequest(query={})),
        ("identity-compile", SettingsRequest(query={}, payload={"mode": "rules"})),
        ("identity-list-presets", SettingsRequest(query={})),
    ]
    for action, request in cases:
        result = handler.handle(action, request)
        assert result.status == 503, action
        assert result.error == "identity service is not configured"
        assert result.payload is None


# ---- router end-to-end ------------------------------------------------------

WS_BASE_ARGS: dict[str, Any] = {
    "bus": SimpleNamespace(),
    "logger": SimpleNamespace(exception=lambda *_args: None),
    "check_api_token": lambda _request: True,
    "parse_query": lambda path: parse_qs(urlsplit(path).query),
    "json_response": http_json_response,
    "error_response": lambda status, message: http_json_response(
        {"error": message}, status=status
    ),
    "runtime_surface": "browser",
    "runtime_capabilities": {},
}


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    identity_dir = tmp_path / IDENTITY_DIR_NAME
    identity_dir.mkdir(parents=True)
    (identity_dir / "SOUL.md").write_text("# soul", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def refresh_calls() -> list[int]:
    return []


@pytest.fixture()
def operations(workspace: Path, refresh_calls: list[int]) -> IdentitySettingsOperations:
    def _refresh() -> dict[str, Any]:
        refresh_calls.append(1)
        return {"status": "ok", "chars": 7}

    def _compile(mode: str) -> dict[str, Any]:
        return {
            "status": "ok",
            "modeUsed": "rules",
            "requestedMode": mode or "rules",
            "compiledFiles": [],
            "skipped": [],
        }

    return IdentitySettingsOperations(
        list_files=partial(identity_api.identity_list_files, workspace),
        read_file=partial(identity_api.identity_read_file, workspace),
        write_file=partial(identity_api.identity_write_file, workspace),
        reload=partial(identity_api.identity_reload, refresh_memory_md=_refresh),
        compile=_compile,
        list_presets=identity_api.identity_list_presets,
    )


def _router(config_path: Path, operations: IdentitySettingsOperations | None):
    return WebUISettingsRouter(
        settings=WebUISettingsServices.create(config_path),
        identity_operations=operations,
        **WS_BASE_ARGS,
    )


def _mutation_request(path: str, payload: dict[str, Any]) -> SimpleNamespace:
    request = SimpleNamespace(path=path, headers=Headers())
    request._nanobot_webui_mutation_request = True
    request._nanobot_webui_mutation_payload = payload
    request._nanobot_trusted_proxy_authenticated = True
    return request


@pytest.mark.asyncio
async def test_router_dispatches_identity_write_over_websocket(
    tmp_path: Path, workspace: Path, operations: IdentitySettingsOperations
) -> None:
    router = _router(tmp_path / "config.json", operations)
    path = "/api/settings/identity/file/save"
    request = _mutation_request(path, {"name": "SOUL.md", "content": "# updated"})

    response = await router.dispatch(None, request, path)

    assert response is not None
    assert response.status_code == 200
    assert json.loads(response.body) == {"name": "SOUL.md", "saved": True}
    assert (workspace / IDENTITY_DIR_NAME / "SOUL.md").read_text(encoding="utf-8") == "# updated"


@pytest.mark.asyncio
async def test_router_dispatches_identity_reload(
    tmp_path: Path, operations, refresh_calls: list[int]
) -> None:
    router = _router(tmp_path / "config.json", operations)
    path = "/api/settings/identity/reload"
    request = _mutation_request(path, {})

    response = await router.dispatch(None, request, path)

    assert response is not None
    assert response.status_code == 200
    assert json.loads(response.body) == {"status": "ok", "chars": 7}
    assert refresh_calls == [1]


@pytest.mark.asyncio
async def test_router_dispatches_identity_read_over_get(
    tmp_path: Path, operations
) -> None:
    router = _router(tmp_path / "config.json", operations)
    path = "/api/settings/identity/file"
    request = SimpleNamespace(path=f"{path}?name=SOUL.md", headers=Headers())

    response = await router.dispatch(None, request, path)

    assert response is not None
    assert response.status_code == 200
    assert json.loads(response.body) == {
        "name": "SOUL.md",
        "content": "# soul",
        "exists": True,
        "fromTemplate": False,
    }


@pytest.mark.asyncio
async def test_router_without_operations_serves_503_not_500(tmp_path: Path) -> None:
    """没注入 operations 时走 null 兜底：可读的 503，而不是崩掉或 500。"""
    router = _router(tmp_path / "config.json", None)
    path = "/api/settings/identity/files"
    request = SimpleNamespace(path=path, headers=Headers())

    response = await router.dispatch(None, request, path)

    assert response is not None
    assert response.status_code == 503
    assert json.loads(response.body) == {"error": "identity service is not configured"}


@pytest.mark.asyncio
async def test_identity_mutation_without_websocket_is_405(tmp_path: Path, operations) -> None:
    """写路径必须被 WS gating 拦住，普通 HTTP 不能改身份文件。"""
    router = _router(tmp_path / "config.json", operations)
    path = "/api/settings/identity/file/save"
    request = SimpleNamespace(path=path, headers=Headers())

    response = await router.dispatch(None, request, path)

    assert response is not None
    assert response.status_code == 405
