"""Agent 档案线格式与字段校验的单元测试。"""

from __future__ import annotations

import pytest

from nanobot.agents.models import (
    NAME_MAX_LENGTH,
    PROMPT_MAX_LENGTH,
    AgentCategory,
    AgentProfile,
    AgentProfileError,
    AgentSelection,
)


def test_selection_round_trips_camel_case() -> None:
    original = AgentSelection(mode="include", entries=["memory", "weather"])
    assert AgentSelection.from_dict(original.to_dict()) == original


def test_selection_defaults_to_all_on_missing() -> None:
    assert AgentSelection.from_dict(None) == AgentSelection(mode="all", entries=[])


@pytest.mark.parametrize("mode", ["", "ALL", "only", None, 3])
def test_selection_rejects_unknown_mode(mode: object) -> None:
    with pytest.raises(AgentProfileError) as excinfo:
        AgentSelection.from_dict({"mode": mode, "entries": []})
    assert excinfo.value.status == 400


def test_selection_rejects_duplicate_entries() -> None:
    with pytest.raises(AgentProfileError, match="must not contain duplicates"):
        AgentSelection.from_dict({"mode": "include", "entries": ["a", "a"]})


def test_selection_rejects_empty_entry() -> None:
    with pytest.raises(AgentProfileError, match="must not contain empty ids"):
        AgentSelection.from_dict({"mode": "include", "entries": [""]})


def test_profile_to_dict_uses_frontend_field_names() -> None:
    profile = AgentProfile(id="writer", name="写作", category_id="writing")
    payload = profile.to_dict()
    assert set(payload) == {
        "id", "name", "description", "type", "customized", "categoryId",
        "icon", "color", "prompt", "modelId", "tools", "skills", "subAgents",
        "hidden", "updatedAt",
    }
    assert payload["categoryId"] == "writing"
    assert payload["subAgents"] == {"mode": "all", "entries": []}


def test_profile_from_dict_fills_defaults() -> None:
    profile = AgentProfile.from_dict({"id": "writer", "name": "写作"})
    assert profile.type == "custom"
    assert profile.customized is False
    assert profile.model_id is None
    assert profile.color == "#4A90D9"
    assert profile.tools == AgentSelection()


def test_profile_rejects_blank_name() -> None:
    with pytest.raises(AgentProfileError) as excinfo:
        AgentProfile(id="writer", name="   ").validate()
    assert excinfo.value.status == 422


def test_profile_rejects_oversized_name() -> None:
    with pytest.raises(AgentProfileError) as excinfo:
        AgentProfile(id="writer", name="x" * (NAME_MAX_LENGTH + 1)).validate()
    assert excinfo.value.status == 422


def test_profile_rejects_oversized_prompt() -> None:
    profile = AgentProfile(id="writer", name="写作", prompt="x" * (PROMPT_MAX_LENGTH + 1))
    with pytest.raises(AgentProfileError) as excinfo:
        profile.validate()
    assert excinfo.value.status == 422


def test_profile_rejects_malformed_color() -> None:
    with pytest.raises(AgentProfileError, match="color"):
        AgentProfile(id="writer", name="写作", color="blue").validate()


def test_profile_rejects_unknown_type() -> None:
    with pytest.raises(AgentProfileError, match="type"):
        AgentProfile(id="writer", name="写作", type="preset").validate()


def test_category_round_trips() -> None:
    category = AgentCategory(id="coding", name="编码", color="#8E44AD", order=1)
    assert AgentCategory.from_dict(category.to_dict()) == category
