"""Read-only discovery of the source MIDI library (PIANO-P010).

The scanner is strictly OBSERVATIONAL: it opens directories and stats
files, and never writes, renames, or permission-changes anything under
the source root.

Policies (deliberate and documented):

- supported files: ``*.mid`` / ``*.midi``, matched CASE-INSENSITIVELY;
  everything else is ignored;
- discovery is recursive, but directory symlinks are never followed;
- file symlinks are IGNORED (not indexed) — a symlink that would escape
  the source root can therefore never enter the catalog;
- relative paths are POSIX-normalized and validated (no absolute paths,
  no ``..`` components) before they leave this module.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator

from piano_core.library.model import LibraryConfigError, LibraryPathError

#: Supported MIDI filename extensions (case-insensitive).
SUPPORTED_EXTENSIONS = (".mid", ".midi")


@dataclass(frozen=True, slots=True)
class DiscoveredFile:
    """One supported file observed under the source root."""

    relpath: str
    size_bytes: int
    mtime_ns: int


def canonical_root(root: object) -> Path:
    """Canonicalize and validate the library root (fail closed)."""
    if not isinstance(root, (str, Path)) or not str(root).strip():
        raise LibraryConfigError("library root must be a non-empty path")
    path = Path(str(root)).expanduser()
    real = Path(os.path.realpath(path))
    if not real.is_dir():
        raise LibraryConfigError(f"library root is not an existing directory: {real}")
    return real


def is_supported_filename(filename: str) -> bool:
    """True for ``*.mid`` / ``*.midi`` (case-insensitive)."""
    return filename.lower().endswith(SUPPORTED_EXTENSIONS)


def validate_relpath(relpath: str) -> str:
    """Validate a source-relative POSIX path; returns it unchanged.

    Rejects absolute paths, ``..`` components, empty components,
    backslashes, and trailing slashes — anything that could turn a catalog
    lookup into arbitrary filesystem access.
    """
    if not isinstance(relpath, str) or not relpath:
        raise LibraryPathError("relpath must be a non-empty string")
    if "\\" in relpath:
        raise LibraryPathError(f"relpath must use POSIX separators: {relpath!r}")
    pure = PurePosixPath(relpath)
    if pure.is_absolute():
        raise LibraryPathError(f"relpath must be relative: {relpath!r}")
    parts = pure.parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise LibraryPathError(f"relpath contains an unsafe component: {relpath!r}")
    if str(pure) != relpath:
        raise LibraryPathError(f"relpath is not in normalized POSIX form: {relpath!r}")
    return relpath


def safe_join(root: Path, relpath: str) -> Path:
    """Join ``relpath`` under the canonical root with full validation.

    Raises :class:`LibraryPathError` for any unsafe relpath. Callers still
    verify ``realpath`` containment afterwards (symlink defense at read
    time).
    """
    validate_relpath(relpath)
    return root.joinpath(*PurePosixPath(relpath).parts)


def discover_files(
    root: object,
    on_warning: Callable[[str], None] | None = None,
) -> Iterator[DiscoveredFile]:
    """Yield :class:`DiscoveredFile` records for supported files under root.

    Read-only walk: never follows directory symlinks, ignores file
    symlinks (with a warning via ``on_warning``), and stats each candidate
    for size/``mtime_ns``. Order is filesystem order — callers sort.
    """
    root_real = canonical_root(root)
    warn = on_warning or (lambda message: None)
    for dirpath, dirnames, filenames in os.walk(root_real, followlinks=False):
        # Never descend into symlinked directories.
        dirnames[:] = [
            d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))
        ]
        for name in filenames:
            if not is_supported_filename(name):
                continue
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                warn(f"ignored symlink (not indexed): {full}")
                continue
            try:
                stat = os.stat(full)
            except OSError as exc:
                warn(f"stat failed, file skipped: {full}: {exc}")
                continue
            relpath = validate_relpath(Path(full).relative_to(root_real).as_posix())
            yield DiscoveredFile(relpath=relpath, size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)
