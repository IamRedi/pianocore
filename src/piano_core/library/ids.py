"""Deterministic stable song IDs for the Piano Core library (PIANO-P010).

Scheme (v1, precise):

    song_id = str(uuid.uuid5(SONG_ID_NAMESPACE_V1, <POSIX relative path>))

with

    SONG_ID_NAMESPACE_V1 = uuid.uuid5(uuid.NAMESPACE_URL, "piano-core:library:v1")

Properties:

- deterministic and machine-independent (UUIDv5 is pure SHA-1-based
  hashing inside a fixed namespace — no randomness, no wall clock);
- stable across rescans while the source-relative path is unchanged;
- distinct relative paths produce distinct IDs; duplicate files at
  different paths remain distinct catalog entries;
- does NOT survive file renames or moves (the path is the identity) —
  documented honestly; a renamed file appears as removed + added;
- file content is never read to generate an ID (no multi-GB hashing).

SHA-1 inside UUIDv5 is acceptable here because the ID is an identifier,
not a security primitive, and the catalog scale (~10^5 entries) is far
below any practical collision concern for a path-derived namespace.
"""

from __future__ import annotations

import uuid

from piano_core.library.model import LibraryPathError

#: Fixed v1 namespace for Piano Core song IDs (derived deterministically;
#: never changes without a new ID scheme version).
SONG_ID_NAMESPACE_V1 = uuid.uuid5(uuid.NAMESPACE_URL, "piano-core:library:v1")

#: Scheme tag baked into the derivation so a future scheme (v2) cannot
#: collide with v1 IDs even for identical paths.
SCHEME_TAG_V1 = "piano-core:library:v1"


def song_id_for_relpath(relpath: str) -> str:
    """Return the deterministic v1 song ID for a source-relative path.

    The relpath must already be a validated relative POSIX path (the
    discovery layer validates before storing; this is defense in depth).
    """
    if not isinstance(relpath, str) or not relpath:
        raise LibraryPathError("relpath must be a non-empty string")
    if relpath.startswith("/") or ".." in relpath.split("/") or "\\" in relpath:
        raise LibraryPathError(f"relpath is not a safe relative path: {relpath!r}")
    return str(uuid.uuid5(SONG_ID_NAMESPACE_V1, relpath))
