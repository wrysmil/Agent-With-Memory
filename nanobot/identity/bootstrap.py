"""Identity directory initialization and legacy workspace migration."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from nanobot.identity.catalog import IDENTITY_DIR_NAME, resolve_identity_dir

logger = logging.getLogger(__name__)

# Files historically scattered at workspace root, now consolidated under identity/.
LEGACY_ROOT_FILES: tuple[str, ...] = ("SOUL.md", "USER.md")


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
