"""Identity file catalog package: single source of truth + read/write."""

from nanobot.identity.catalog import (
    CHAR_LIMIT,
    CORE_FILES,
    IDENTITY_DIR_NAME,
    PERSONAS_SUBDIR,
    IdentityFileSpec,
    discover_personas,
    resolve_identity_dir,
)

__all__ = [
    "CHAR_LIMIT",
    "CORE_FILES",
    "IDENTITY_DIR_NAME",
    "PERSONAS_SUBDIR",
    "IdentityFileSpec",
    "discover_personas",
    "resolve_identity_dir",
]
