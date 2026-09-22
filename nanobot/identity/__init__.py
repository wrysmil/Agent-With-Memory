"""Identity file catalog package: single source of truth + read/write."""

from nanobot.identity.bootstrap import (
    PERSONA_PRESET_STEMS,
    ensure_identity_templates,
    load_identity_template,
    migrate_legacy_identity_files,
)
from nanobot.identity.catalog import (
    CHAR_LIMIT,
    COMPILED_SCHEMA_VERSION,
    CORE_FILES,
    IDENTITY_DIR_NAME,
    LIFECYCLE_OWNED_FILES,
    PERSONAS_SUBDIR,
    RUNTIME_SUBDIR,
    IdentityFileSpec,
    discover_personas,
    resolve_identity_dir,
)
from nanobot.identity.compiler import (
    compile_identity,
    compiled_status,
    read_compiled,
    runtime_dir,
)
from nanobot.identity.store import IdentityStore, IdentityStoreError

__all__ = [
    "CHAR_LIMIT",
    "COMPILED_SCHEMA_VERSION",
    "CORE_FILES",
    "IDENTITY_DIR_NAME",
    "LIFECYCLE_OWNED_FILES",
    "PERSONA_PRESET_STEMS",
    "PERSONAS_SUBDIR",
    "RUNTIME_SUBDIR",
    "IdentityFileSpec",
    "IdentityStore",
    "IdentityStoreError",
    "compile_identity",
    "compiled_status",
    "discover_personas",
    "ensure_identity_templates",
    "load_identity_template",
    "migrate_legacy_identity_files",
    "read_compiled",
    "resolve_identity_dir",
    "runtime_dir",
]
