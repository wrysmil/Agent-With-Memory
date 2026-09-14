from __future__ import annotations

import pytest

from nanobot.config.schema import Config
from nanobot.webui.settings_system import (
    coerce_channel_value,
    system_settings_payload,
    update_agent_system_settings,
)


def test_system_domain_owns_runtime_dto_and_agent_updates(tmp_path) -> None:
    config = Config()

    changed, restart_required = update_agent_system_settings(
        config,
        {
            "timezone": ["Asia/Shanghai"],
            "tool_hint_max_length": ["120"],
        },
    )
    payload = system_settings_payload(
        config,
        config_path=tmp_path / "config.json",
        version="0.3.0",
    )

    assert changed is True
    assert restart_required is True
    assert config.agents.defaults.timezone == "Asia/Shanghai"
    assert config.agents.defaults.timezone_mode == "manual"
    assert config.agents.defaults.tool_hint_max_length == 120
    assert payload["runtime"]["config_path"] == str(tmp_path / "config.json")
    assert payload["version"] == {"current": "0.3.0"}
    assert payload["docs"]["version"] == "0.3.0"
    assert set(payload) == {"runtime", "usage", "advanced", "version", "docs"}


def test_system_domain_validates_channel_field_values() -> None:
    assert coerce_channel_value("allow_from", "alice, bob", "list") == [
        "alice",
        "bob",
    ]
    assert coerce_channel_value("enabled", "yes", "bool") is True
    assert coerce_channel_value("port", "8765", "int") == 8765


def test_memory_enabled_default_is_off_and_round_trips_through_payload(tmp_path) -> None:
    config = Config()
    assert config.agents.defaults.memory_enabled is False

    changed, restart_required = update_agent_system_settings(
        config, {"memory_enabled": ["true"]}
    )
    payload = system_settings_payload(
        config, config_path=tmp_path / "config.json", version="0.3.0"
    )

    assert changed is True
    assert restart_required is False
    assert config.agents.defaults.memory_enabled is True
    assert payload["runtime"]["memory_enabled"] is True


@pytest.mark.parametrize("raw", ["true", "True", "1", "on", "ON"])
def test_memory_enabled_accepts_common_truthy_tokens(tmp_path, raw: str) -> None:
    config = Config()
    update_agent_system_settings(config, {"memory_enabled": [raw]})
    assert config.agents.defaults.memory_enabled is True


@pytest.mark.parametrize("raw", ["false", "0", "off", "OFF"])
def test_memory_enabled_accepts_common_falsy_tokens(tmp_path, raw: str) -> None:
    config = Config()
    config.agents.defaults.memory_enabled = True
    update_agent_system_settings(config, {"memory_enabled": [raw]})
    assert config.agents.defaults.memory_enabled is False
