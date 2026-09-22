"""出厂身份模板：字符上限、YAML 结构、SOUL 与 balanced 同源、播种幂等。"""

from __future__ import annotations

from pathlib import Path

import yaml

from nanobot.identity.bootstrap import (
    PERSONA_PRESET_STEMS,
    ensure_identity_templates,
    load_identity_template,
)
from nanobot.identity.catalog import CHAR_LIMIT, IDENTITY_DIR_NAME
from nanobot.utils.helpers import load_bundled_template, sync_workspace_templates

ALL_TEMPLATE_NAMES = [
    "SOUL.md",
    "USER.md",
    "AGENT.md",
    "POLICIES.yaml",
    "prompts/policies.md",
    "memory/MEMORY.md",
    *[f"personas/{stem}.md" for stem in PERSONA_PRESET_STEMS],
]


def test_all_templates_exist_and_within_char_limit():
    for name in ALL_TEMPLATE_NAMES:
        content = load_bundled_template(name)
        assert content is not None, name
        assert len(content) <= CHAR_LIMIT, f"{name}: {len(content)} > {CHAR_LIMIT}"
        assert content.strip(), name


def test_policies_yaml_is_mapping_with_three_top_keys():
    content = load_bundled_template("POLICIES.yaml")
    assert content is not None
    parsed = yaml.safe_load(content)
    assert isinstance(parsed, dict)
    assert {"tool_policies", "scope_policy", "auto_confirm"} <= set(parsed)
    assert isinstance(parsed["tool_policies"], dict)


def test_root_soul_is_byte_identical_to_balanced_persona():
    soul = load_bundled_template("SOUL.md")
    balanced = load_bundled_template("personas/balanced.md")
    assert soul is not None and balanced is not None
    assert soul == balanced


def test_load_identity_template_maps_core_and_persona():
    assert "User Profile" in (load_identity_template("USER.md") or "")
    assert "行为准则" in (load_identity_template("AGENT.md") or "")
    assert "私人导师" in (load_identity_template("mentor.md") or "")
    assert load_identity_template("id_rsa") is None


def test_ensure_seeds_identity_dir_and_personas(tmp_path: Path):
    added = ensure_identity_templates(tmp_path)

    identity = tmp_path / IDENTITY_DIR_NAME
    assert (identity / "AGENT.md").is_file()
    assert (identity / "POLICIES.yaml").is_file()
    assert (identity / "prompts" / "policies.md").is_file()
    for stem in PERSONA_PRESET_STEMS:
        assert (identity / "personas" / f"{stem}.md").is_file()
    # Legacy root seeds
    assert (tmp_path / "SOUL.md").is_file()
    assert (tmp_path / "USER.md").is_file()
    assert (tmp_path / "memory" / "MEMORY.md").is_file()
    assert added  # first run creates files


def test_ensure_never_overwrites_existing_user_file(tmp_path: Path):
    identity = tmp_path / IDENTITY_DIR_NAME
    identity.mkdir()
    (identity / "AGENT.md").write_text("# mine", encoding="utf-8")
    (tmp_path / "SOUL.md").write_text("# my soul", encoding="utf-8")

    ensure_identity_templates(tmp_path)

    assert (identity / "AGENT.md").read_text(encoding="utf-8") == "# mine"
    assert (tmp_path / "SOUL.md").read_text(encoding="utf-8") == "# my soul"


def test_ensure_is_idempotent(tmp_path: Path):
    first = ensure_identity_templates(tmp_path)
    second = ensure_identity_templates(tmp_path)
    assert first
    assert second == []


def test_sync_workspace_templates_calls_ensure(tmp_path: Path):
    sync_workspace_templates(tmp_path, silent=True)
    identity = tmp_path / IDENTITY_DIR_NAME
    assert (identity / "AGENT.md").is_file()
    assert (identity / "personas" / "balanced.md").is_file()
    # SOUL 与 balanced 同源（播种时字节一致）
    assert (tmp_path / "SOUL.md").read_text(encoding="utf-8") == (
        identity / "personas" / "balanced.md"
    ).read_text(encoding="utf-8")
