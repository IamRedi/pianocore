"""V2/V3 tests: Song/MIDI Library Foundation (PIANO-P010).

All tests run against synthetic fixtures generated in temporary
directories — no real library data, no hardware, no output. MIDI fixture
files are tiny hand-built SMFs (same builders as the P009 loader tests),
parsed through the canonical mido stack where metadata is needed.

Covered: model/ID determinism, discovery (extensions/case/unicode/
nesting/empty), path security (traversal/symlink escape), index lifecycle
(schema version, incremental add/change/remove, rebuild), metadata
extraction and error isolation, query operations, and the P009 playback
boundary (resolved paths feed load_timeline; no output; StateStore
untouched).
"""

import threading
import time
import unittest
from pathlib import Path

from piano_core.library.ids import SONG_ID_NAMESPACE_V1, song_id_for_relpath
from piano_core.library.model import (
    LibraryConfigError,
    LibraryEntryGoneError,
    LibraryIndexVersionError,
    LibraryPathError,
    SongRecord,
    SongStatus,
)
from piano_core.library.service import LibraryConfig, LibraryService
from piano_core.state import PianoState, StateStore

try:
    import mido  # noqa: F401

    MIDO_AVAILABLE = True
except ImportError:
    MIDO_AVAILABLE = False


def _encode_vlq(value: int) -> bytes:
    out = bytearray([value & 0x7F])
    value >>= 7
    while value:
        out.insert(0, 0x80 | (value & 0x7F))
        value >>= 7
    return bytes(out)


def write_midi(path: Path, notes=((60, 80),), tempo_µs=500_000, tpb=96) -> None:
    """Write a tiny format-1 SMF: one tempo meta + note pairs (test fixture)."""
    events = [(0, b"\xff\x51\x03" + tempo_µs.to_bytes(3, "big"))]
    for note, velocity in notes:
        events.append((48, bytes([0x90, note, velocity])))
        events.append((48, bytes([0x80, note, 0])))
    track_events = b"".join(
        _encode_vlq(delta) + payload for delta, payload in events
    ) + b"\x00\xff\x2f\x00"
    track = b"MTrk" + len(track_events).to_bytes(4, "big") + track_events
    header = (b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big")
              + (1).to_bytes(2, "big") + tpb.to_bytes(2, "big"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + track)


class LibraryTestCase(unittest.TestCase):
    """Shared fixture: a temporary library root + index + service."""

    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "root"
        self.root.mkdir(parents=True)
        self.index_path = Path(self.tmp.name) / "index" / "library.sqlite3"
        self.config = LibraryConfig(root=str(self.root), index_path=str(self.index_path))
        self.service = LibraryService(self.config)

    def touch(self, relpath: str, notes=((60, 80),)) -> Path:
        path = self.root / relpath
        write_midi(path, notes=notes)
        return path


class IdTests(LibraryTestCase):
    def test_song_id_is_deterministic(self):
        a = song_id_for_relpath("a/b.mid")
        b = song_id_for_relpath("a/b.mid")
        self.assertEqual(a, b)
        self.assertEqual(len(a), 36)  # UUID

    def test_namespace_is_fixed_and_deterministic(self):
        import uuid

        expected = str(uuid.uuid5(uuid.uuid5(uuid.NAMESPACE_URL, "piano-core:library:v1"), "x.mid"))
        self.assertEqual(song_id_for_relpath("x.mid"), expected)
        self.assertEqual(SONG_ID_NAMESPACE_V1, uuid.uuid5(uuid.NAMESPACE_URL, "piano-core:library:v1"))

    def test_different_paths_produce_distinct_ids(self):
        self.assertNotEqual(song_id_for_relpath("a.mid"), song_id_for_relpath("b.mid"))
        self.assertNotEqual(song_id_for_relpath("a/b.mid"), song_id_for_relpath("b.mid"))

    def test_unsafe_relpath_rejected(self):
        for bad in ("", "/abs.mid", "../x.mid", "a/../b.mid", "a\\b.mid"):
            with self.assertRaises(LibraryPathError):
                song_id_for_relpath(bad)

    def test_ids_stable_across_scans(self):
        self.touch("song.mid")
        r1 = self.service.scan(enrich=False)
        id1 = self.service.list_songs()[0].song_id
        r2 = self.service.scan(enrich=False)
        id2 = self.service.list_songs()[0].song_id
        self.assertEqual(r1.added, 1)
        self.assertEqual(r2.added, 0)
        self.assertEqual(r2.unchanged, 1)
        self.assertEqual(id1, id2)


class DiscoveryTests(LibraryTestCase):
    def test_extensions_and_case_policy(self):
        self.touch("a.mid")
        self.touch("b.MID")
        self.touch("c.Midi")
        self.touch("d.midi")
        self.touch("ignore.txt")
        self.touch("noext")
        result = self.service.scan(enrich=False)
        self.assertEqual(result.discovered, 4)  # a, b, c, d
        relpaths = sorted(r.relpath for r in self.service.list_songs(limit=10))
        self.assertEqual(relpaths, ["a.mid", "b.MID", "c.Midi", "d.midi"])

    def test_nested_paths_and_unicode_and_spaces(self):
        self.touch("sub/dir/ünïcode säng.mid")
        self.touch("with spaces.mid")
        self.touch("nested/deep/track 1 - part 2.mid")
        result = self.service.scan(enrich=False)
        self.assertEqual(result.discovered, 3)
        relpaths = sorted(r.relpath for r in self.service.list_songs(limit=10))
        self.assertIn("sub/dir/ünïcode säng.mid", relpaths)
        self.assertIn("with spaces.mid", relpaths)

    def test_empty_library(self):
        result = self.service.scan(enrich=False)
        self.assertEqual(result.discovered, 0)
        self.assertEqual(self.service.stats()["total"], 0)

    def test_root_must_exist(self):
        with self.assertRaises(LibraryConfigError):
            LibraryService(LibraryConfig(root=str(self.root / "nope"),
                                         index_path=str(self.index_path)))

    def test_symlinks_ignored(self):
        real = self.touch("real.mid")
        link_file = self.root / "link.mid"
        link_file.symlink_to(real)
        link_dir = self.root / "linkdir"
        link_dir.symlink_to(self.root)
        result = self.service.scan(enrich=False)
        self.assertEqual(result.discovered, 1)
        self.assertEqual(self.service.list_songs()[0].relpath, "real.mid")


class PathSecurityTests(LibraryTestCase):
    def test_resolve_rejects_unknown_song_id(self):
        with self.assertRaises(LibraryPathError):
            self.service.resolve("not-a-real-id")

    def test_model_rejects_traversal_relpath(self):
        with self.assertRaises(ValueError):
            SongRecord(song_id="x", relpath="../escape.mid", filename="escape.mid",
                       extension=".mid", size_bytes=1, mtime_ns=1,
                       status=SongStatus.PENDING)

    def test_symlink_escape_never_resolves(self):
        import os

        self.touch("inside.mid")
        self.service.scan(enrich=False)
        record = self.service.list_songs()[0]
        outside = Path(self.tmp.name) / "outside-secret.mid"
        write_midi(outside)
        # Simulate index tampering / a later symlink swap: point the catalog
        # entry at a path that now escapes via a symlink.
        target = self.root / "inside.mid"
        os.remove(target)
        os.symlink(outside, target)
        with self.assertRaises(LibraryPathError):
            self.service.resolve(record.song_id)

    def test_missing_file_reports_gone(self):
        self.touch("gone.mid")
        self.service.scan(enrich=False)
        record = self.service.list_songs()[0]
        (self.root / "gone.mid").unlink()
        with self.assertRaises(LibraryEntryGoneError):
            self.service.resolve(record.song_id)


@unittest.skipUnless(MIDO_AVAILABLE, "mido is not installed (core suite rule)")
class IndexLifecycleTests(LibraryTestCase):
    def test_first_scan_indexes_all_as_pending(self):
        self.touch("a.mid")
        self.touch("sub/b.mid")
        result = self.service.scan(enrich=False)
        self.assertEqual(result.added, 2)
        stats = self.service.stats()
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["pending"], 2)
        record = self.service.list_songs()[0]
        self.assertEqual(record.status, SongStatus.PENDING)
        self.assertGreater(record.size_bytes, 0)
        self.assertTrue(record.indexed_at_utc)

    def test_unchanged_rescan_reuses_records(self):
        self.touch("a.mid")
        self.service.scan(enrich=True)
        parsed_before = self.service.list_songs()[0]
        result = self.service.scan(enrich=True)
        self.assertEqual(result.unchanged, 1)
        self.assertEqual(result.enriched, 0)
        parsed_after = self.service.list_songs()[0]
        self.assertEqual(parsed_before, parsed_after)  # no reparse, no change

    def test_changed_file_is_reenriched(self):
        path = self.touch("a.mid", notes=((60, 80),))
        self.service.scan(enrich=True)
        import os as _os

        stamp = _os.stat(path)
        time.sleep(0.01)
        write_midi(path, notes=((72, 90),))  # different content
        stamp2 = _os.stat(path)
        assert (stamp.st_size, stamp.st_mtime_ns) != (stamp2.st_size, stamp2.st_mtime_ns)
        result = self.service.scan(enrich=True)
        self.assertEqual(result.updated, 1)
        self.assertEqual(result.enriched, 1)
        record = self.service.list_songs()[0]
        self.assertEqual(record.status, SongStatus.PARSED)
        self.assertEqual(record.midi_format, 1)

    def test_removed_file_leaves_catalog(self):
        path = self.touch("a.mid")
        self.touch("b.mid")
        self.service.scan(enrich=False)
        path.unlink()
        result = self.service.scan(enrich=False)
        self.assertEqual(result.removed, 1)
        self.assertEqual(self.service.stats()["total"], 1)

    def test_index_rebuild_from_scratch(self):
        self.touch("a.mid")
        self.service.scan(enrich=True)
        self.index_path.unlink()
        fresh = LibraryService(self.config)
        result = fresh.scan(enrich=True)
        self.assertEqual(result.added, 1)
        record = fresh.list_songs()[0]
        self.assertEqual(record.status, SongStatus.PARSED)

    def test_schema_version_mismatch_fails_clearly(self):
        import sqlite3

        self.touch("a.mid")
        self.service.scan(enrich=False)
        self.service = None  # close handled per call
        conn = sqlite3.connect(self.index_path)
        conn.execute("UPDATE meta SET value = '999' WHERE key = 'schema_version'")
        conn.commit()
        conn.close()
        with self.assertRaises(LibraryIndexVersionError):
            LibraryService(self.config).stats()


@unittest.skipUnless(MIDO_AVAILABLE, "mido is not installed (core suite rule)")
class MetadataTests(LibraryTestCase):
    def test_format1_metadata_extracted(self):
        self.touch("a.mid", notes=((60, 80), (64, 80)))
        self.service.scan(enrich=True)
        record = self.service.list_songs()[0]
        self.assertEqual(record.status, SongStatus.PARSED)
        self.assertEqual(record.midi_format, 1)
        self.assertEqual(record.track_count, 1)
        self.assertEqual(record.ticks_per_beat, 96)
        self.assertGreater(record.duration_seconds, 0.0)
        self.assertIsNone(record.error)

    def test_tempo_derivation(self):
        path = self.root / "tempo.mid"
        path.parent.mkdir(parents=True, exist_ok=True)
        events = [(0, b"\xff\x51\x03\x07\xa1\x20")]  # 500000 us/beat = 120 BPM
        track_events = b"".join(
            _encode_vlq(d) + p for d, p in events
        ) + b"\x00\xff\x2f\x00"
        track = b"MTrk" + len(track_events).to_bytes(4, "big") + track_events
        path.write_bytes(b"MThd" + (6).to_bytes(4, "big") + (0).to_bytes(2, "big")
                         + (1).to_bytes(2, "big") + (96).to_bytes(2, "big") + track)
        self.service.scan(enrich=True)
        record = self.service.list_songs()[0]
        self.assertEqual(record.status, SongStatus.PARSED)
        self.assertEqual(record.tempo_bpm_first, 120.0)
        # track holds only the tempo meta: zero playable duration is correct
        self.assertGreaterEqual(record.duration_seconds, 0.0)

    def test_invalid_midi_isolated(self):
        self.touch("good.mid")
        bad = self.root / "bad.mid"
        bad.write_bytes(b"not a midi file at all")
        self.touch("good2.mid")
        result = self.service.scan(enrich=True)
        stats = self.service.stats()
        self.assertEqual(stats["invalid"], 1)
        self.assertEqual(stats["parsed"], 2)
        record = self.service.get(song_id_for_relpath("bad.mid"))
        self.assertEqual(record.status, SongStatus.INVALID)
        self.assertIn("cannot parse", record.error)
        # valid neighbors unaffected
        self.assertEqual(
            self.service.get(song_id_for_relpath("good.mid")).status,
            SongStatus.PARSED,
        )

    def test_unreadable_file_isolated(self):
        import os

        self.touch("locked.mid")
        self.touch("ok.mid")
        os.chmod(self.root / "locked.mid", 0o000)
        try:
            result = self.service.scan(enrich=True)
        finally:
            os.chmod(self.root / "locked.mid", 0o644)
        stats = self.service.stats()
        self.assertEqual(stats["invalid"], 1)
        self.assertEqual(stats["parsed"], 1)


@unittest.skipUnless(MIDO_AVAILABLE, "mido is not installed (core suite rule)")
class QueryTests(LibraryTestCase):
    def setUp(self):
        super().setUp()
        for relpath, notes in (("a.mid", ((60, 80),)), ("sub/b.mid", ((62, 80),)),
                               ("sub/c.MID", ((64, 80),)), ("d.midi", ((65, 80),))):
            self.touch(relpath, notes=notes)
        self.service.scan(enrich=True)

    def test_stats(self):
        stats = self.service.stats()
        self.assertEqual(stats["total"], 4)
        self.assertEqual(stats["parsed"], 4)
        self.assertEqual(stats["pending"], 0)

    def test_list_deterministic_order_and_pagination(self):
        page1 = self.service.list_songs(limit=2, offset=0)
        page2 = self.service.list_songs(limit=2, offset=2)
        relpaths = [r.relpath for r in page1 + page2]
        self.assertEqual(relpaths, sorted(relpaths))
        self.assertEqual(len(page1), 2)
        self.assertEqual(len(page2), 2)
        self.assertEqual(len({r.song_id for r in page1 + page2}), 4)

    def test_exact_lookup(self):
        record = self.service.list_songs()[0]
        self.assertEqual(self.service.get(record.song_id), record)
        self.assertIsNone(self.service.get("nope"))

    def test_filename_search(self):
        hits = self.service.search("b.mid")
        self.assertEqual([r.relpath for r in hits], ["sub/b.mid"])
        hits = self.service.search("SUB/")
        self.assertEqual({r.relpath for r in hits}, {"sub/b.mid", "sub/c.MID"})
        self.assertEqual(self.service.search("zzz-nothing"), [])

    def test_filters(self):
        fmt1 = self.service.list_songs(status=SongStatus.PARSED, midi_format=1)
        self.assertEqual(len(fmt1), 4)
        pend = self.service.list_songs(status=SongStatus.PENDING)
        self.assertEqual(pend, [])


@unittest.skipUnless(MIDO_AVAILABLE, "mido is not installed (core suite rule)")
class PerformanceTests(LibraryTestCase):
    """Large synthetic record counts: set-based SQL, no quadratic behavior."""

    def _entries(self, n, mtime_base=1_000_000_000):
        from piano_core.library.discovery import DiscoveredFile

        return [
            DiscoveredFile(
                relpath=f"dir{i % 20}/song{i}.mid",
                size_bytes=1000 + i,
                mtime_ns=mtime_base + i,
            )
            for i in range(n)
        ]

    def test_large_inventory_is_set_based_and_correct(self):
        from piano_core.library.index import LibraryIndex

        n = 5000
        with LibraryIndex(self.config.index_path) as index:
            t0 = time.perf_counter()
            counts = index.apply_inventory(self._entries(n))
            t1 = time.perf_counter()
            self.assertEqual(counts["added"], n)
            counts = index.apply_inventory(self._entries(n))
            t2 = time.perf_counter()
            self.assertEqual(counts["unchanged"], n)
            # half the files change mtime, the other half disappears
            counts = index.apply_inventory(self._entries(n // 2, mtime_base=9_000_000_000))
            t3 = time.perf_counter()
        self.assertEqual(counts["updated"], n // 2)
        self.assertEqual(counts["removed"], n // 2)
        for label, seconds in (("initial", t1 - t0), ("rescan", t2 - t1), ("diff", t3 - t2)):
            self.assertLess(seconds, 10.0, f"{label} inventory too slow: {seconds:.2f}s")
        stats = self.service.stats()
        self.assertEqual(stats["total"], n // 2)


@unittest.skipUnless(MIDO_AVAILABLE, "mido is not installed (core suite rule)")
class PlaybackBoundaryTests(LibraryTestCase):
    def test_resolved_path_feeds_p009_timeline(self):
        from piano_core.playback.timeline import load_timeline

        self.touch("integration.mid", notes=((60, 80),))
        self.service.scan(enrich=True)
        record = self.service.list_songs()[0]
        path = self.service.resolve(record.song_id)
        timeline = load_timeline(path)
        self.assertEqual(len(timeline.events), 2)
        self.assertGreater(timeline.duration, 0.0)

    def test_statestore_untouched_by_scan_and_resolve(self):
        store = StateStore()
        before = store.snapshot()
        self.touch("a.mid")
        self.service.scan(enrich=True)
        record = self.service.list_songs()[0]
        self.service.resolve(record.song_id)
        self.assertEqual(store.snapshot(), before)
        self.assertEqual(store.events_received(), 0)

    def test_library_sources_have_no_output_or_statestore_surface(self):
        import glob as _glob

        forbidden = (
            "piano_core.state",
            "StateStore(",
            "apply_event(",
            "open_output(",
            ".send(",
            "send_note_on",
            "send_note_off",
            "send_control_change",
            "PANIC",
            "PlaybackEngine",
            "piano_core.midi.output",
            "piano_core.midi.input",
        )
        offenders = []
        pkg = Path(__file__).resolve().parent.parent / "src" / "piano_core" / "library"
        for path in sorted(_glob.glob(str(pkg / "*.py"))):
            text = Path(path).read_text(encoding="utf-8")
            for token in forbidden:
                if token in text:
                    offenders.append(f"{Path(path).name}: {token!r}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
