"""The WS mutation map and settings allowlist contain the memory endpoints."""

from nanobot.webui.settings_routes import _SETTINGS_MUTATION_PATHS
from nanobot.webui.ws_http import _WEBUI_MUTATION_PATHS

_MEMORY_ACTIONS = {
    "memory.create",
    "memory.update",
    "memory.delete",
    "episode.update",
    "episode.delete",
    "scratchpad.save",
}

_MEMORY_PATHS = {
    "/api/settings/memory/memories/create",
    "/api/settings/memory/memories/update",
    "/api/settings/memory/memories/delete",
    "/api/settings/memory/episodes/update",
    "/api/settings/memory/episodes/delete",
    "/api/settings/memory/scratchpad/save",
}


def test_ws_http_mutation_paths_contain_memory_actions():
    assert _MEMORY_ACTIONS <= set(_WEBUI_MUTATION_PATHS)


def test_settings_mutation_paths_contain_memory_paths():
    assert _MEMORY_PATHS <= set(_SETTINGS_MUTATION_PATHS)


def test_router_is_mutation_path_detects_memory_paths():
    from nanobot.webui.settings_routes import WebUISettingsRouter

    for path in _MEMORY_PATHS:
        assert WebUISettingsRouter.is_mutation_path(path)


def test_gateway_memory_operations_round_trip(tmp_path):
    from nanobot.webui.gateway_services import build_memory_operations

    ops = build_memory_operations(workspace_id="default", workspace_path=tmp_path)
    created = ops.create_memory(content="自省 smoke", type="fact")
    assert created["memory"]["source"] == "manual"
    listed = ops.list_memories()
    assert any(item["content"] == "自省 smoke" for item in listed["items"])
    stats = ops.fetch_stats()
    assert stats["total"] == 1
    assert stats["by_type"]["fact"] == 1
