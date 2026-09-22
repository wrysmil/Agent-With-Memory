"""Identity directory initialization and legacy workspace migration."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from nanobot.identity.catalog import (
    IDENTITY_DIR_NAME,
    PERSONAS_SUBDIR,
    resolve_identity_dir,
)

logger = logging.getLogger(__name__)

# Files historically scattered at workspace root, now consolidated under identity/.
LEGACY_ROOT_FILES: tuple[str, ...] = ("SOUL.md", "USER.md")

# Core identity files that live under identity/ (bundled template path == logical path
# relative to nanobot/templates/, except MEMORY.md which is lifecycle-owned).
_IDENTITY_CORE_SEEDS: tuple[tuple[str, str], ...] = (
    ("AGENT.md", "AGENT.md"),
    ("POLICIES.yaml", "POLICIES.yaml"),
    ("prompts/policies.md", "prompts/policies.md"),
)

# Factory persona presets under templates/personas/ → identity/personas/.
PERSONA_PRESET_STEMS: tuple[str, ...] = (
    "balanced",
    "mentor",
    "creative",
    "companion",
    "tech_expert",
)

# Whitelisted logical name → bundled template path (relative to nanobot/templates/).
_BUNDLED_TEMPLATES: dict[str, str] = {
    "SOUL.md": "SOUL.md",
    "USER.md": "USER.md",
    "AGENT.md": "AGENT.md",
    "POLICIES.yaml": "POLICIES.yaml",
    "prompts/policies.md": "prompts/policies.md",
    "MEMORY.md": "memory/MEMORY.md",
}


def load_identity_template(name: str) -> str | None:
    """Bundled factory template for a whitelisted identity file, if any."""
    from nanobot.utils.helpers import load_bundled_template  # local: avoid import cycle

    bundled = _BUNDLED_TEMPLATES.get(name)
    if bundled is not None:
        return load_bundled_template(bundled)
    # Personas are addressed by bare filename in the catalog.
    if "/" not in name and name.endswith(".md"):
        return load_bundled_template(f"{PERSONAS_SUBDIR}/{name}")
    return None


def ensure_identity_templates(workspace: Path) -> list[str]:
    """Seed missing identity files from bundled templates. Never overwrites.

    Covers the gap left by ``sync_workspace_templates`` (root SOUL/USER +
    memory/MEMORY.md only): AGENT.md / POLICIES.yaml / prompts/policies.md and
    the persona presets all land under ``identity/``. SOUL/USER/MEMORY are
    re-checked so this function is also correct when called standalone.

    Returns workspace-relative paths actually created.
    """
    from nanobot.utils.helpers import load_bundled_template  # local: avoid import cycle

    added: list[str] = []
    identity_dir = resolve_identity_dir(workspace)

    def _seed(dest: Path, bundled: str) -> None:
        if dest.exists():
            return
        content = load_bundled_template(bundled)
        if content is None:
            logger.warning("Bundled template missing: %s", bundled)
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        added.append(str(dest.relative_to(workspace)))

    for logical, bundled in _IDENTITY_CORE_SEEDS:
        _seed(identity_dir / logical, bundled)

    for stem in PERSONA_PRESET_STEMS:
        _seed(identity_dir / PERSONAS_SUBDIR / f"{stem}.md", f"{PERSONAS_SUBDIR}/{stem}.md")

    # Legacy root files: seed only when neither identity/ nor workspace root has them.
    for filename in LEGACY_ROOT_FILES:
        if (identity_dir / filename).exists() or (workspace / filename).exists():
            continue
        _seed(workspace / filename, filename)

    # MEMORY.md derived artifact: only when neither location has it.
    if not (identity_dir / "MEMORY.md").exists() and not (
        workspace / "memory" / "MEMORY.md"
    ).exists():
        _seed(workspace / "memory" / "MEMORY.md", "memory/MEMORY.md")

    if added:
        logger.info("Seeded identity templates: %s", ", ".join(added))
    return added


def migrate_legacy_identity_files(workspace: Path) -> list[str]:
    """Move SOUL.md / USER.md from workspace root into identity/.

    Returns list of actually migrated filenames.

    Conflict policy: if a file already exists in identity/,
    **do not migrate and do not overwrite** — old file stays in place,
    with a WARNING logged. Silent overwrite would lose user edits.
    """
    identity_dir = resolve_identity_dir(workspace)
    moved: list[str] = []

    for filename in LEGACY_ROOT_FILES:
        legacy = workspace / filename
        if not legacy.is_file():
            continue
        target = identity_dir / filename
        if target.exists():
            logger.warning(
                "identity/%s already exists, skipping migration of workspace/%s; "
                "please manually confirm whether to merge",
                filename,
                filename,
            )
            continue
        identity_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy), str(target))
        moved.append(filename)
        logger.info("Migrated %s -> %s/%s", filename, IDENTITY_DIR_NAME, filename)

    return moved
