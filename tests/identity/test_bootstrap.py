"""Tests for identity bootstrap (migration of legacy files)."""

from pathlib import Path

from nanobot.identity.bootstrap import migrate_legacy_identity_files
from nanobot.identity.catalog import IDENTITY_DIR_NAME


def test_migrates_existing_soul_and_user(tmp_path: Path):
    (tmp_path / "SOUL.md").write_text("# my soul", encoding="utf-8")
    (tmp_path / "USER.md").write_text("# my profile", encoding="utf-8")

    moved = migrate_legacy_identity_files(tmp_path)

    assert sorted(moved) == ["SOUL.md", "USER.md"]
    identity_dir = tmp_path / IDENTITY_DIR_NAME
    assert (identity_dir / "SOUL.md").read_text(encoding="utf-8") == "# my soul"
    assert (identity_dir / "USER.md").read_text(encoding="utf-8") == "# my profile"
    # Old files must be removed to avoid duplicate conflicts
    assert not (tmp_path / "SOUL.md").exists()
    assert not (tmp_path / "USER.md").exists()


def test_does_not_clobber_existing_identity_copy(tmp_path: Path):
    identity_dir = tmp_path / IDENTITY_DIR_NAME
    identity_dir.mkdir()
    (identity_dir / "SOUL.md").write_text("# new", encoding="utf-8")
    (tmp_path / "SOUL.md").write_text("# old", encoding="utf-8")

    moved = migrate_legacy_identity_files(tmp_path)

    assert moved == []
    assert (identity_dir / "SOUL.md").read_text(encoding="utf-8") == "# new"
    # Old file stays in place — user must handle manually; never silently overwrite
    assert (tmp_path / "SOUL.md").read_text(encoding="utf-8") == "# old"


def test_missing_legacy_files_is_noop(tmp_path: Path):
    assert migrate_legacy_identity_files(tmp_path) == []
