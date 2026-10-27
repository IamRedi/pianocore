"""Rebuildable SQLite catalog index for the Piano Core library (PIANO-P010).

The index is a disposable local cache: the source MIDI collection remains
the single source of truth. The index can be deleted at any time and
rebuilt by a fresh scan.

Design:

- stdlib ``sqlite3`` (no new dependency); WAL journal mode;
- schema version recorded in a ``meta`` table — a mismatch fails clearly
  (suggest deleting/rebuilding) instead of guessing;
- the inventory phase applies its full diff (added/updated/removed) in
  ONE transaction — readers never observe a partially committed scan;
- enrichment writes bounded batches (one transaction per batch) so runs
  are resumable: an interrupted run leaves ``pending`` rows that the next
  run continues;
- every mutation is parameterized; the index path never points inside the
  source library (enforced by configuration, not by this module).
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
from pathlib import Path
from typing import Iterable, Iterator

from piano_core.library.discovery import DiscoveredFile
from piano_core.library.ids import song_id_for_relpath
from piano_core.library.metadata import SongMetadata
from piano_core.library.model import (
    LibraryIndexError,
    LibraryIndexVersionError,
    SongRecord,
    SongStatus,
)

#: Current index schema version (bump on incompatible schema changes).
SCHEMA_VERSION = "1"

_NOW_UTC = lambda: _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS songs (
    song_id          TEXT PRIMARY KEY,
    relpath          TEXT NOT NULL UNIQUE,
    filename         TEXT NOT NULL,
    extension        TEXT NOT NULL,
    size_bytes       INTEGER NOT NULL,
    mtime_ns         INTEGER NOT NULL,
    status           TEXT NOT NULL,
    midi_format      INTEGER,
    track_count      INTEGER,
    ticks_per_beat   INTEGER,
    duration_seconds REAL,
    tempo_bpm_first  REAL,
    title            TEXT,
    error            TEXT,
    indexed_at_utc   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_songs_status ON songs(status);
"""

_RECORD_COLUMNS = (
    "song_id, relpath, filename, extension, size_bytes, mtime_ns, status, "
    "midi_format, track_count, ticks_per_beat, duration_seconds, "
    "tempo_bpm_first, title, error, indexed_at_utc"
)


def _row_to_record(row: sqlite3.Row) -> SongRecord:
    return SongRecord(
        song_id=row["song_id"],
        relpath=row["relpath"],
        filename=row["filename"],
        extension=row["extension"],
        size_bytes=row["size_bytes"],
        mtime_ns=row["mtime_ns"],
        status=SongStatus.from_value(row["status"]),
        midi_format=row["midi_format"],
        track_count=row["track_count"],
        ticks_per_beat=row["ticks_per_beat"],
        duration_seconds=row["duration_seconds"],
        tempo_bpm_first=row["tempo_bpm_first"],
        title=row["title"],
        error=row["error"],
        indexed_at_utc=row["indexed_at_utc"],
    )


class LibraryIndex:
    """Transactional SQLite catalog index (one writer, safe readers)."""

    def __init__(self, path: object) -> None:
        if not isinstance(path, (str, Path)) or not str(path).strip():
            raise LibraryIndexError("index path must be a non-empty path")
        index_path = Path(str(path))
        if not index_path.is_absolute():
            raise LibraryIndexError(f"index path must be absolute: {index_path}")
        parent = index_path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(index_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error as exc:
            raise LibraryIndexError(f"cannot open index {index_path}: {exc}") from exc
        self._init_schema()

    # ------------------------------------------------------------------
    # lifecycle / schema
    # ------------------------------------------------------------------
    def _init_schema(self) -> None:
        try:
            with self._conn:
                self._conn.executescript(_SCHEMA)
                row = self._conn.execute(
                    "SELECT value FROM meta WHERE key = 'schema_version'"
                ).fetchone()
                if row is None:
                    self._conn.execute(
                        "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
                        (SCHEMA_VERSION,),
                    )
                elif row["value"] != SCHEMA_VERSION:
                    raise LibraryIndexVersionError(
                        f"index schema version {row['value']!r} does not match "
                        f"implemented version {SCHEMA_VERSION!r}; delete the index "
                        "and rescan to rebuild it"
                    )
        except sqlite3.Error as exc:
            raise LibraryIndexError(f"index schema initialization failed: {exc}") from exc

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "LibraryIndex":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # ------------------------------------------------------------------
    # inventory (single transaction: added/updated/removed/unchanged)
    # ------------------------------------------------------------------
    def apply_inventory(self, discovered: list[DiscoveredFile]) -> dict[str, int]:
        """Apply one full discovery pass; returns the inventory diff counts.

        The whole diff is applied in a single transaction: readers never
        observe a partially committed scan. Unchanged entries keep their
        stored metadata (no MIDI reparse needed). Changed entries are reset
        to ``pending`` for (re-)enrichment.
        """
        counts = {"added": 0, "unchanged": 0, "updated": 0, "removed": 0}
        now = _NOW_UTC()
        try:
            with self._conn:
                self._conn.execute(
                    "CREATE TEMP TABLE IF NOT EXISTS discovered("
                    "song_id TEXT PRIMARY KEY, relpath TEXT NOT NULL UNIQUE, "
                    "filename TEXT NOT NULL, extension TEXT NOT NULL, "
                    "size_bytes INTEGER NOT NULL, mtime_ns INTEGER NOT NULL)"
                )
                self._conn.execute("DELETE FROM discovered")
                self._conn.executemany(
                    "INSERT INTO discovered(song_id, relpath, filename, "
                    "extension, size_bytes, mtime_ns) VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            song_id_for_relpath(entry.relpath),
                            entry.relpath,
                            entry.relpath.rsplit("/", 1)[-1],
                            "." + entry.relpath.rsplit(".", 1)[-1].lower()
                            if "." in entry.relpath.rsplit("/", 1)[-1]
                            else "",
                            entry.size_bytes,
                            entry.mtime_ns,
                        )
                        for entry in discovered
                    ],
                )
                counts["added"] = self._conn.execute(
                    "SELECT COUNT(*) FROM discovered d "
                    "LEFT JOIN songs s ON s.relpath = d.relpath "
                    "WHERE s.relpath IS NULL"
                ).fetchone()[0]
                counts["unchanged"] = self._conn.execute(
                    "SELECT COUNT(*) FROM discovered d JOIN songs s "
                    "ON s.relpath = d.relpath "
                    "WHERE s.size_bytes = d.size_bytes AND s.mtime_ns = d.mtime_ns"
                ).fetchone()[0]
                counts["updated"] = self._conn.execute(
                    "SELECT COUNT(*) FROM discovered d JOIN songs s "
                    "ON s.relpath = d.relpath "
                    "WHERE s.size_bytes != d.size_bytes OR s.mtime_ns != d.mtime_ns"
                ).fetchone()[0]
                counts["removed"] = self._conn.execute(
                    "SELECT COUNT(*) FROM songs s LEFT JOIN discovered d "
                    "ON s.relpath = d.relpath WHERE d.relpath IS NULL"
                ).fetchone()[0]
                self._conn.execute(
                    "INSERT INTO songs(song_id, relpath, filename, extension, "
                    "size_bytes, mtime_ns, status, indexed_at_utc) "
                    "SELECT d.song_id, d.relpath, d.filename, d.extension, "
                    "d.size_bytes, d.mtime_ns, 'pending', ? "
                    "FROM discovered d LEFT JOIN songs s ON s.relpath = d.relpath "
                    "WHERE s.relpath IS NULL",
                    (now,),
                )
                self._conn.execute(
                    "UPDATE songs SET size_bytes = d.size_bytes, "
                    "mtime_ns = d.mtime_ns, status = 'pending', "
                    "midi_format = NULL, track_count = NULL, ticks_per_beat = NULL, "
                    "duration_seconds = NULL, tempo_bpm_first = NULL, title = NULL, "
                    "error = NULL, indexed_at_utc = ? "
                    "FROM discovered d WHERE songs.relpath = d.relpath "
                    "AND (songs.size_bytes != d.size_bytes "
                    "OR songs.mtime_ns != d.mtime_ns)",
                    (now,),
                )
                self._conn.execute(
                    "DELETE FROM songs WHERE relpath NOT IN (SELECT relpath FROM discovered)"
                )
                self._conn.execute("DROP TABLE discovered")
        except sqlite3.Error as exc:
            raise LibraryIndexError(f"inventory update failed: {exc}") from exc
        return counts

    # ------------------------------------------------------------------
    # enrichment (bounded batches, resumable)
    # ------------------------------------------------------------------
    def pending_relpaths(self, limit: int | None = None) -> list[tuple[str, str]]:
        """Return (song_id, relpath) for rows awaiting enrichment."""
        sql = "SELECT song_id, relpath FROM songs WHERE status = 'pending' ORDER BY relpath"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return [(row["song_id"], row["relpath"]) for row in self._conn.execute(sql)]

    def store_metadata(self, results: Iterable[tuple[str, SongStatus, SongMetadata | None, str | None]]) -> None:
        """Store one bounded batch of enrichment outcomes (one transaction).

        Each item is ``(song_id, status, metadata_or_None, error_or_None)``.
        """
        now = _NOW_UTC()
        updates = []
        for song_id, status, metadata, error in results:
            if status is SongStatus.PARSED and metadata is not None:
                updates.append(
                    (
                        status.value,
                        metadata.midi_format,
                        metadata.track_count,
                        metadata.ticks_per_beat,
                        metadata.duration_seconds,
                        metadata.tempo_bpm_first,
                        metadata.title,
                        None,
                        now,
                        song_id,
                    )
                )
            else:
                updates.append(
                    (
                        status.value,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        error,
                        now,
                        song_id,
                    )
                )
        try:
            with self._conn:
                self._conn.executemany(
                    "UPDATE songs SET status = ?, midi_format = ?, "
                    "track_count = ?, ticks_per_beat = ?, duration_seconds = ?, "
                    "tempo_bpm_first = ?, title = ?, error = ?, indexed_at_utc = ? "
                    "WHERE song_id = ?",
                    updates,
                )
        except sqlite3.Error as exc:
            raise LibraryIndexError(f"enrichment batch failed: {exc}") from exc

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------
    def get(self, song_id: str) -> SongRecord | None:
        row = self._conn.execute(
            f"SELECT {_RECORD_COLUMNS} FROM songs WHERE song_id = ?", (song_id,)
        ).fetchone()
        return _row_to_record(row) if row is not None else None

    def stats(self) -> dict[str, int]:
        by_status = {
            row["status"]: row["n"]
            for row in self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM songs GROUP BY status"
            )
        }
        total = sum(by_status.values())
        return {"total": total, **{s.value: by_status.get(s.value, 0) for s in SongStatus}}

    def list_songs(
        self,
        limit: int = 20,
        offset: int = 0,
        status: SongStatus | None = None,
        midi_format: int | None = None,
    ) -> list[SongRecord]:
        sql = f"SELECT {_RECORD_COLUMNS} FROM songs"
        clauses, params = [], []
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if midi_format is not None:
            clauses.append("midi_format = ?")
            params.append(int(midi_format))
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY relpath LIMIT ? OFFSET ?"
        params.extend([int(limit), int(offset)])
        return [
            _row_to_record(row) for row in self._conn.execute(sql, params)
        ]

    def search(self, text: str, limit: int = 20) -> list[SongRecord]:
        """Case-insensitive substring search on relpath (deterministic order)."""
        if not text:
            return []
        escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = self._conn.execute(
            f"SELECT {_RECORD_COLUMNS} FROM songs "
            "WHERE relpath LIKE ? ESCAPE '\\' ORDER BY relpath LIMIT ?",
            (f"%{escaped}%", int(limit)),
        )
        return [_row_to_record(row) for row in rows]

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
