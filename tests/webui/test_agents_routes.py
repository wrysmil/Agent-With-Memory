"""AgentSettingsHandler：动作分发与错误翻译。"""

from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.agents.models import AgentProfile
from nanobot.config.schema import Config
from nanobot.webui import agents_api
from nanobot.webui.agents_routes import (
    AGENTS_ACTION_NAMES,
    AgentSettingsHandler,
    AgentSettingsOperations,
)
from nanobot.webui.settings_contracts import SettingsRequest, WebUISettingsError


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def ops(workspace: Path) -> AgentSettingsOperations:
    # catalog 依赖注入的 Config；用默认构造器，不读用户真实的 ~/.nanobot 配置。
    return AgentSettingsOperations(
        list_profiles=lambda: agents_api.agents_list(workspace),
        list_catalog=lambda: agents_api.agents_catalog(workspace, load_config=Config),
        save_profile=lambda raw: agents_api.agents_save(workspace, raw),
        delete_profile=lambda agent_id: agents_api.agents_delete(workspace, agent_id),
        reset_profile=lambda agent_id: agents_api.agents_reset(workspace, agent_id),
        set_visibility=lambda agent_id, hidden: agents_api.agents_visibility(
            workspace, agent_id, hidden
        ),
    )


def _request(payload: dict | None = None, query: dict | None = None) -> SettingsRequest:
    return SettingsRequest(query=query or {}, payload=payload)


def test_action_names_are_frozen() -> None:
    assert AGENTS_ACTION_NAMES == frozenset({
        "agents-list", "agents-catalog",
        "agents-save", "agents-delete", "agents-reset", "agents-visibility",
    })


def test_unknown_action_is_404(ops: AgentSettingsOperations) -> None:
    result = AgentSettingsHandler(ops).handle("agents-nope", _request())
    assert result.status == 404


def test_list_returns_agents(ops: AgentSettingsOperations) -> None:
    result = AgentSettingsHandler(ops).handle("agents-list", _request())
    assert result.status == 200
    assert result.payload is not None
    assert "agents" in result.payload


def test_catalog_returns_five_collections(ops: AgentSettingsOperations) -> None:
    result = AgentSettingsHandler(ops).handle("agents-catalog", _request())
    assert result.status == 200
    assert result.payload is not None
    assert set(result.payload) == {"tools", "skills", "models", "mcpServers", "categories"}


def test_save_requires_agent_object(ops: AgentSettingsOperations) -> None:
    result = AgentSettingsHandler(ops).handle("agents-save", _request({}))
    assert result.status == 400


def test_save_round_trips(ops: AgentSettingsOperations) -> None:
    handler = AgentSettingsHandler(ops)
    saved = handler.handle(
        "agents-save",
        _request({"agent": {"id": "writer", "name": "写作"}}),
    )
    assert saved.status == 200
    assert saved.payload is not None
    assert saved.payload["agent"]["name"] == "写作"


def test_delete_requires_id(ops: AgentSettingsOperations) -> None:
    result = AgentSettingsHandler(ops).handle("agents-delete", _request({}))
    assert result.status == 400


def test_delete_rejects_system_preset_with_409(ops: AgentSettingsOperations) -> None:
    result = AgentSettingsHandler(ops).handle(
        "agents-delete", _request({"id": "code-reviewer"})
    )
    assert result.status == 409
    assert result.error


def test_visibility_rejects_non_boolean(ops: AgentSettingsOperations) -> None:
    result = AgentSettingsHandler(ops).handle(
        "agents-visibility", _request({"id": "writer", "hidden": "yes"})
    )
    assert result.status == 400


def test_store_errors_keep_their_status(ops: AgentSettingsOperations) -> None:
    result = AgentSettingsHandler(ops).handle(
        "agents-reset", _request({"id": "definitely-not-a-factory-preset"})
    )
    assert result.status in {404, 409}
    assert result.error


def test_missing_list_catalog_degrades_to_503() -> None:
    ops = AgentSettingsOperations(
        list_profiles=lambda: {"agents": []},
        list_catalog=lambda: (_ for _ in ()).throw(
            WebUISettingsError("agent catalog is not configured", status=503)
        ),
        save_profile=lambda raw: {"agent": AgentProfile(id="x", name="y").to_dict()},
        delete_profile=lambda agent_id: {"id": agent_id},
        reset_profile=lambda agent_id: {"agent": AgentProfile(id="x", name="y").to_dict()},
        set_visibility=lambda agent_id, hidden: {
            "agent": AgentProfile(id="x", name="y").to_dict()
        },
    )
    result = AgentSettingsHandler(ops).handle("agents-catalog", _request())
    assert result.status == 503
