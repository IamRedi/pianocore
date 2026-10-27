"""Library service: scan, query, and safe resolution (PIANO-P010).

:class:`LibraryService` is the single boundary future API/UI layers use.
It owns the canonical source root, the index, and the scan pipeline:

- inventory phase: one transactional diff of the discovered file set
  against the committed index (added/updated/unchanged/removed);
- enrichment phase: bounded, resumable metadata extraction over
  ``pending`` rows (per-batch transactions, per-file error isolation);
- query operations with deterministic ordering;
- :meth:`resolve` — song_id to a validated source path under the
  canonical root (traversal/symlink-escape rejected; missing entries
  reported honestly).

The service never writes under the source root, never sends MIDI, and
never touches runtime playback state.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from piano_core.library.discovery import (
    DiscoveredFile,
    discover_files,
    safe_join,
    validate_relpath,
)
from piano_core.library.ids import song_id_for_relpath
from piano_core.library.index import LibraryIndex
from piano_core.library.metadata import (
    MidiParseError,
    SongMetadata,
    UnsupportedMidiError,
    extract_metadata,
    mido_available,
)
from piano_core.library.model import (
    LibraryConfigError,
    LibraryEntryGoneError,
    LibraryPathError,
    ScanResult,
    SongRecord,
    SongStatus,
)

#: Enrichment batch size (rows per transaction).
_DEFAULT_BATCH_SIZE = 200


class LibraryConfig:
    """Validated library configuration (root + index path; both required)."""

    __slots__ = ("root", "index_path", "_root_real")

    def __init__(self, root: object, index_path: object) -> None:
        if not isinstance(root, (str, Path)) or not str(root).strip():
            raise LibraryConfigError(
                "library root is not configured (set --library-root or "
                "PIANO_CORE_LIBRARY_ROOT)"
            )
        if not isinstance(index_path, (str, Path)) or not str(index_path).strip():
            raise LibraryConfigError(
                "index path is not configured (set --index-path or "
                "PIANO_CORE_LIBRARY_INDEX_PATH)"
            )
        self.root = str(root)
        self.index_path = str(index_path)
        from piano_core.library.discovery import canonical_root

        self._root_real = canonical_root(self.root)

    @property
    def root_real(self) -> Path:
        return self._root_real


class LibraryService:
    """Song catalog service over a read-only source library."""

    def __init__(self, config: LibraryConfig, logger: logging.Logger | None = None) -> None:
        if not isinstance(config, LibraryConfig):
            raise TypeError(f"config must be a LibraryConfig, got {type(config).__name__}")
        self._config = config
        self._root = config.root_real
        self._log = logger or logging.getLogger("piano_core.library")

    # ------------------------------------------------------------------
    # scanning
    # ------------------------------------------------------------------
    def scan(
        self,
        enrich: bool = True,
        enrich_limit: int | None = None,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        stop_event: threading.Event | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> ScanResult:
        """Run one inventory + (optionally) bounded enrichment pass.

        Cancelling (``stop_event``) stops between files/batches: the
        current inventory transaction and enrichment batch are committed
        and the result reports ``pending_remaining`` for the next run.
        """
        started = time.monotonic()
        progress = on_progress or (lambda message: None)
        with LibraryIndex(self._config.index_path) as index:
            discovered = list(discover_files(self._config.root))
            progress(f"discovered {len(discovered)} supported files")
            counts = index.apply_inventory(discovered)

            enriched = parsed = invalid = unsupported = 0
            if enrich and not mido_available():
                from piano_core.library.model import LibraryError

                raise LibraryError(
                    "mido is required for metadata enrichment; run with "
                    "--no-enrich for inventory only"
                )
            if enrich:
                remaining_limit = enrich_limit
                while True:
                    if stop_event is not None and stop_event.is_set():
                        break
                    if remaining_limit is not None and remaining_limit <= 0:
                        break
                    batch_limit = batch_size if remaining_limit is None else min(
                        batch_size, remaining_limit
                    )
                    pending = index.pending_relpaths(limit=batch_limit)
                    if not pending:
                        break
                    results = []
                    for song_id, relpath in pending:
                        if stop_event is not None and stop_event.is_set():
                            break
                        results.append(self._enrich_one(song_id, relpath))
                    index.store_metadata(results)
                    for _, status, _, _ in results:
                        enriched += 1
                        if status is SongStatus.PARSED:
                            parsed += 1
                        elif status is SongStatus.INVALID:
                            invalid += 1
                        elif status is SongStatus.UNSUPPORTED:
                            unsupported += 1
                    if remaining_limit is not None:
                        remaining_limit -= len(results)
                    if len(results) < batch_limit:
                        break
                    progress(f"enriched {enriched} files so far")

            pending_remaining = len(index.pending_relpaths())
            elapsed = time.monotonic() - started
            return ScanResult(
                discovered=len(discovered),
                added=counts["added"],
                unchanged=counts["unchanged"],
                updated=counts["updated"],
                removed=counts["removed"],
                enriched=enriched,
                parsed=parsed,
                invalid=invalid,
                unsupported=unsupported,
                pending_remaining=pending_remaining,
                elapsed_seconds=round(elapsed, 3),
            )

    def _enrich_one(self, song_id: str, relpath: str) -> tuple[str, SongStatus, SongMetadata | None, str | None]:
        """Extract metadata for one file; errors are isolated per file."""
        path = safe_join(self._root, relpath)
        try:
            metadata = extract_metadata(path)
        except UnsupportedMidiError as exc:
            return song_id, SongStatus.UNSUPPORTED, None, str(exc)
        except MidiParseError as exc:
            return song_id, SongStatus.INVALID, None, str(exc)
        except OSError as exc:
            return song_id, SongStatus.INVALID, None, f"unreadable file: {exc}"
        return song_id, SongStatus.PARSED, metadata, None

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------
    def stats(self) -> dict[str, int]:
        with LibraryIndex(self._config.index_path) as index:
            return index.stats()

    def list_songs(
        self,
        limit: int = 20,
        offset: int = 0,
        status: SongStatus | None = None,
        midi_format: int | None = None,
    ) -> list[SongRecord]:
        if limit < 0 or offset < 0:
            raise LibraryPathError("limit/offset must be non-negative")
        with LibraryIndex(self._config.index_path) as index:
            return index.list_songs(limit=limit, offset=offset, status=status, midi_format=midi_format)

    def get(self, song_id: str) -> SongRecord | None:
        with LibraryIndex(self._config.index_path) as index:
            return index.get(song_id)

    def search(self, text: str, limit: int = 20) -> list[SongRecord]:
        with LibraryIndex(self._config.index_path) as index:
            return index.search(text, limit=limit)

    def resolve(self, song_id: str) -> Path:
        """Resolve a song_id to a VALIDATED source MIDI path.

        - unknown song_id → :class:`LibraryPathError`;
        - relpath re-validated (traversal rejected);
        - file must exist as a regular file (missing →
          :class:`LibraryEntryGoneError`, rescan will reconcile);
        - realpath containment under the canonical root is enforced
          (symlink escape rejected).
        """
        if not isinstance(song_id, str) or not song_id:
            raise LibraryPathError("song_id must be a non-empty string")
        with LibraryIndex(self._config.index_path) as index:
            record = index.get(song_id)
        if record is None:
            raise LibraryPathError(f"unknown song_id: {song_id}")
        relpath = validate_relpath(record.relpath)
        candidate = safe_join(self._root, relpath)
        real = Path(candidate).resolve()
        root_real = self._root
        if real != root_real and root_real not in real.parents:
            raise LibraryPathError(
                f"resolved path escapes the library root: {relpath!r}"
            )
        if not real.is_file():
            raise LibraryEntryGoneError(
                f"indexed file is missing under the source root: {relpath} "
                "(rescan to reconcile the catalog)"
            )
        return real
