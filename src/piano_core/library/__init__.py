"""Piano Core Song / MIDI Library Foundation (PIANO-P010).

A deterministic, queryable catalog over the existing read-only MIDI
collection: typed :class:`~piano_core.library.model.SongRecord` model,
path-derived stable song IDs, read-only discovery, a rebuildable
schema-versioned SQLite index with incremental/resumable scanning, MIDI
metadata extraction, and query/lookup/resolve operations.

Safety boundaries:

- the source library is only ever READ; the scanner never writes under
  the source root;
- the index is a disposable cache outside the source root;
- :meth:`~piano_core.library.service.LibraryService.resolve` always
  validates the resolved path under the canonical source root;
- no MIDI output, no StateStore access, no playback scheduling —
  resolution feeds the P009 Playback Core, which stays authoritative.
"""

from piano_core.library.ids import song_id_for_relpath
from piano_core.library.model import (
    LibraryConfigError,
    LibraryEntryGoneError,
    LibraryError,
    LibraryIndexError,
    LibraryIndexVersionError,
    LibraryPathError,
    ScanResult,
    SongRecord,
    SongStatus,
)
from piano_core.library.service import LibraryConfig, LibraryService

__all__ = [
    "LibraryConfig",
    "LibraryConfigError",
    "LibraryEntryGoneError",
    "LibraryError",
    "LibraryIndexError",
    "LibraryIndexVersionError",
    "LibraryPathError",
    "LibraryService",
    "ScanResult",
    "SongRecord",
    "SongStatus",
    "song_id_for_relpath",
]
