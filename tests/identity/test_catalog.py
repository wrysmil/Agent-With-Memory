"""Tests for identity catalog (whitelist, grouping, constants)."""

from pathlib import Path

from nanobot.identity.catalog import (
    CHAR_LIMIT,
    CORE_FILES,
    IDENTITY_DIR_NAME,
    PERSONAS_SUBDIR,
    IdentityFileSpec,
    discover_personas,
    resolve_identity_dir,
)


def test_char_limit_matches_memory_lifecycle_constant():
    """Frontend charMax and backend truncation constant must be the same number."""
    from nanobot.memory.lifecycle import MEMORY_MD_MAX_CHARS

    assert CHAR_LIMIT == MEMORY_MD_MAX_CHARS


def test_core_files_match_frontend_contract():
    names = [spec.name for spec in CORE_FILES]
    assert names == [
        "SOUL.md",
        "AGENT.md",
        "USER.md",
        "MEMORY.md",
        "POLICIES.yaml",
        "prompts/policies.md",
    ]


def test_restricted_set_matches_frontend_contract():
    restricted = {spec.name for spec in CORE_FILES if spec.restricted}
    assert restricted == {"AGENT.md", "MEMORY.md", "POLICIES.yaml", "prompts/policies.md"}


def test_resolve_identity_dir_is_under_workspace(tmp_path: Path):
    assert resolve_identity_dir(tmp_path) == tmp_path / IDENTITY_DIR_NAME


def test_discover_personas_returns_sorted_markdown_only(tmp_path: Path):
    persona_dir = tmp_path / IDENTITY_DIR_NAME / PERSONAS_SUBDIR
    persona_dir.mkdir(parents=True)
    (persona_dir / "b.md").write_text("B", encoding="utf-8")
    (persona_dir / "a.md").write_text("A", encoding="utf-8")
    (persona_dir / "notes.txt").write_text("ignored", encoding="utf-8")

    assert [p.name for p in discover_personas(tmp_path)] == ["a.md", "b.md"]


def test_discover_personas_missing_dir_is_empty(tmp_path: Path):
    assert discover_personas(tmp_path) == []
