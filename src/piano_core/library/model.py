"""Typed song/catalog model and error hierarchy (PIANO-P010).

The source MIDI collection is the single source of truth; this model only
describes what the scanner observed about it. `SongRecord` is immutable;
`status` records where the entry stands in the inventory/enrichment
pipeline:

- ``pending``    discovered (or changed), metadata not yet extracted
- ``parsed``     metadata successfully extracted
- ``invalid``    file could not be read/parsed (error text recorded)
- ``unsupported`` structurally valid but unsupported (SMF format 2, SMPTE)

No musical metadata is invented: fields are extracted or ``None``.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

_MIDI_7BIT_MAX = 0x7F


class SongStatus(enum.Enum):
    """Lifecycle of one catalog entry."""

    PENDING = "pending"
    PARSED = "parsed"
    INVALID = "invalid"
    UNSUPPORTED = "unsupported"

    @classmethod
    def from_value(cls, value: object) -> "SongStatus":
        for member in cls:
            if member.value == value:
                return member
        raise ValueError(f"unknown song status: {value!r}")


class LibraryError(RuntimeError):
    """Base class for all library-foundation errors."""


class LibraryConfigError(LibraryError):
    """Library configuration is missing or invalid (fail closed)."""


class LibraryIndexError(LibraryError):
    """The index database failed or is corrupt (the source is unaffected)."""


class LibraryIndexVersionError(LibraryIndexError):
    """The index schema version does not match this implementation."""


class LibraryPathError(LibraryError):
    """A requested song path could not be safely resolved."""


class LibraryEntryGoneError(LibraryPathError):
    """The indexed entry no longer exists under the source root."""


@dataclass(frozen=True, slots=True)
class SongRecord:
    """One catalog entry describing a source MIDI file.

    ``relpath`` is the path relative to the configured source root (POSIX
    separators). ``song_id`` is derived from ``relpath`` (see
    :mod:`piano_core.library.ids`) and is stable while the path is
    unchanged. Metadata fields are ``None`` unless the file was
    successfully parsed. ``indexed_at_utc`` records when the row was last
    written (inventory or enrichment), not a musical attribute.
    """

    song_id: str
    relpath: str
    filename: str
    extension: str
    size_bytes: int
    mtime_ns: int
    status: SongStatus
    midi_format: int | None = None
    track_count: int | None = None
    ticks_per_beat: int | None = None
    duration_seconds: float | None = None
    tempo_bpm_first: float | None = None
    title: str | None = None
    error: str | None = None
    indexed_at_utc: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.song_id, str) or not self.song_id:
            raise ValueError("song_id must be a non-empty string")
        if not isinstance(self.relpath, str) or not self.relpath:
            raise ValueError("relpath must be a non-empty string")
        if self.relpath.startswith("/") or ".." in self.relpath.split("/"):
            raise ValueError(f"relpath must be a relative in-root path: {self.relpath!r}")
        if not isinstance(self.filename, str) or not self.filename:
            raise ValueError("filename must be a non-empty string")
        if not isinstance(self.extension, str) or not self.extension.startswith("."):
            raise ValueError("extension must be a dotted suffix")
        for name in ("size_bytes", "mtime_ns"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative int")
        if not isinstance(self.status, SongStatus):
            raise TypeError("status must be a SongStatus")
        for name in ("midi_format", "track_count", "ticks_per_beat"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool)
            ):
                raise TypeError(f"{name} must be an int or None")
        for name in ("duration_seconds", "tempo_bpm_first"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, (int, float)) or isinstance(value, bool)
            ):
                raise TypeError(f"{name} must be a number or None")
        for name in ("title", "error", "indexed_at_utc"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{name} must be a string or None")


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Outcome of one :meth:`LibraryService.scan` invocation.

    ``discovered`` is the number of supported files found under the root.
    ``added``/``updated``/``unchanged``/``removed`` describe the inventory
    diff against the previous committed index. ``enriched`` is how many
    ``pending`` rows were processed this run; ``parsed``/``invalid``/
    ``unsupported`` classify their outcomes; ``pending_remaining`` is what
    is left for a resumable continuation. ``elapsed_seconds`` covers the
    whole scan invocation.
    """

    discovered: int
    added: int
    unchanged: int
    updated: int
    removed: int
    enriched: int
    parsed: int
    invalid: int
    unsupported: int
    pending_remaining: int
    elapsed_seconds: float

    def describe(self) -> str:
        return (
            f"discovered={self.discovered} added={self.added} "
            f"unchanged={self.unchanged} updated={self.updated} "
            f"removed={self.removed} enriched={self.enriched} "
            f"parsed={self.parsed} invalid={self.invalid} "
            f"unsupported={self.unsupported} "
            f"pending_remaining={self.pending_remaining} "
            f"elapsed={self.elapsed_seconds:.3f}s"
        )
