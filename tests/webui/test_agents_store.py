"""AgentStore 的读写、路径防御与出厂预置行为。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nanobot.agents.catalog import FACTORY_PROFILES
from nanobot.agents.models import AgentProfile, AgentSelection
from nanobot.agents.store import AgentStore, AgentStoreError


def test_list_includes_factory_presets_without_touching_disk(tmp_path: Path) -> None:
    store = AgentStore(tmp_path)
    ids = {p.id for p in store.list_profiles()}
    assert {p.id for p in FACTORY_PROFILES} <= ids
    # 读路径不落盘：刷新页面不该创建目录
    assert not (tmp_path / "agents").exists()


def test_save_writes_one_json_file_per_profile(tmp_path: Path) -> None:
    store = AgentStore(tmp_path)
    store.save_profile(AgentProfile(id="writer", name="写作"))
    path = tmp_path / "agents" / "profiles" / "writer.json"
    assert json.loads(path.read_text(encoding="utf-8"))["name"] == "写作"


def test_save_round_trips_through_disk(tmp_path: Path) -> None:
    AgentStore(tmp_path).save_profile(
        AgentProfile(id="writer", name="写作", skills=AgentSelection("include", ["memory"]))
    )
    reloaded = AgentStore(tmp_path).get_profile("writer")
    assert reloaded.skills == AgentSelection("include", ["memory"])


def test_save_stamps_updated_at_ignoring_client_value(tmp_path: Path) -> None:
    store = AgentStore(tmp_path)
    saved = store.save_profile(
        AgentProfile(id="writer", name="写作", updated_at="1999-01-01T00:00:00+00:00")
    )
    assert saved.updated_at != "1999-01-01T00:00:00+00:00"
    assert saved.updated_at.startswith("20")


def test_save_of_factory_id_derives_system_type_and_customized(tmp_path: Path) -> None:
    store = AgentStore(tmp_path)
    factory = next(p for p in FACTORY_PROFILES if p.id == "code-reviewer")
    untouched = AgentProfile.from_dict(factory.to_dict())
    assert store.save_profile(untouched).customized is False
    assert store.save_profile(untouched).type == "system"

    edited = AgentProfile.from_dict({**factory.to_dict(), "prompt": "改过了"})
    result = store.save_profile(edited)
    assert result.customized is True
    assert result.type == "system"


def test_save_of_unknown_id_is_custom_and_never_customized(tmp_path: Path) -> None:
    saved = AgentStore(tmp_path).save_profile(AgentProfile(id="mine", name="我的"))
    assert (saved.type, saved.customized) == ("custom", False)


def test_save_ignores_client_supplied_type(tmp_path: Path) -> None:
    profile = AgentProfile(id="mine", name="我的", type="system", customized=True)
    saved = AgentStore(tmp_path).save_profile(profile)
    assert (saved.type, saved.customized) == ("custom", False)


def test_get_missing_profile_is_404(tmp_path: Path) -> None:
    with pytest.raises(AgentStoreError) as excinfo:
        AgentStore(tmp_path).get_profile("ghost")
    assert excinfo.value.status == 404


def test_get_rejects_traversal_id(tmp_path: Path) -> None:
    with pytest.raises(AgentStoreError) as excinfo:
        AgentStore(tmp_path).get_profile("../../etc/passwd")
    assert excinfo.value.status == 400


def test_delete_removes_profile(tmp_path: Path) -> None:
    store = AgentStore(tmp_path)
    store.save_profile(AgentProfile(id="mine", name="我的"))
    store.delete_profile("mine")
    assert not (tmp_path / "agents" / "profiles" / "mine.json").exists()
    with pytest.raises(AgentStoreError):
        AgentStore(tmp_path).get_profile("mine")


def test_delete_rejects_system_preset(tmp_path: Path) -> None:
    with pytest.raises(AgentStoreError) as excinfo:
        AgentStore(tmp_path).delete_profile("code-reviewer")
    assert excinfo.value.status == 409


def test_delete_missing_is_404(tmp_path: Path) -> None:
    with pytest.raises(AgentStoreError) as excinfo:
        AgentStore(tmp_path).delete_profile("ghost")
    assert excinfo.value.status == 404


def test_reset_restores_factory_value_and_keeps_hidden(tmp_path: Path) -> None:
    store = AgentStore(tmp_path)
    factory = next(p for p in FACTORY_PROFILES if p.id == "code-reviewer")
    store.save_profile(AgentProfile.from_dict({**factory.to_dict(), "prompt": "改过了"}))
    store.set_visibility("code-reviewer", True)

    reset = AgentStore(tmp_path).reset_profile("code-reviewer")
    assert reset.prompt == factory.prompt
    assert reset.customized is False
    assert reset.hidden is True


def test_reset_rejects_custom_profile(tmp_path: Path) -> None:
    store = AgentStore(tmp_path)
    store.save_profile(AgentProfile(id="mine", name="我的"))
    with pytest.raises(AgentStoreError) as excinfo:
        store.reset_profile("mine")
    assert excinfo.value.status == 409


def test_set_visibility_persists(tmp_path: Path) -> None:
    AgentStore(tmp_path).set_visibility("code-reviewer", True)
    assert AgentStore(tmp_path).get_profile("code-reviewer").hidden is True


def test_list_sorts_system_first_then_category_then_name(tmp_path: Path) -> None:
    store = AgentStore(tmp_path)
    store.save_profile(AgentProfile(id="zeta", name="泽塔", category_id="writing"))
    store.save_profile(AgentProfile(id="alpha", name="阿尔法", category_id="ops"))
    listed = store.list_profiles()
    custom_ids = [p.id for p in listed if p.type == "custom"]
    assert custom_ids == ["alpha", "zeta"]


def test_corrupt_file_is_skipped_not_fatal(tmp_path: Path) -> None:
    store = AgentStore(tmp_path)
    store.save_profile(AgentProfile(id="writer", name="写作"))
    (tmp_path / "agents" / "profiles" / "broken.json").write_text("{oops", encoding="utf-8")

    reloaded = AgentStore(tmp_path)
    assert reloaded.get_profile("writer").name == "写作"
    with pytest.raises(AgentStoreError):
        reloaded.get_profile("broken")


def test_missing_fields_fall_back_to_defaults(tmp_path: Path) -> None:
    profiles = tmp_path / "agents" / "profiles"
    profiles.mkdir(parents=True)
    (profiles / "sparse.json").write_text('{"id": "sparse", "name": "稀"}', encoding="utf-8")

    profile = AgentStore(tmp_path).get_profile("sparse")
    assert profile.color == "#4A90D9"
    assert profile.tools == AgentSelection()


def test_categories_default_to_factory_list(tmp_path: Path) -> None:
    categories = AgentStore(tmp_path).list_categories()
    assert [c.id for c in categories] == [
        "general", "coding", "writing", "research", "ops", "efficiency",
    ]


def test_save_categories_round_trips(tmp_path: Path) -> None:
    store = AgentStore(tmp_path)
    store.save_categories([AgentCategoryHelper("x", "自定义", "#111111", 9)])
    assert [c.id for c in AgentStore(tmp_path).list_categories()] == ["x"]


def AgentCategoryHelper(cid: str, name: str, color: str, order: int):  # noqa: N802
    from nanobot.agents.models import AgentCategory

    return AgentCategory(id=cid, name=name, color=color, order=order)
