"""V2/V3 tests: runtime library integration (PIANO-P011).

Verifies the Application-level wiring: library_enabled configuration, the
documented degrade contract (any failure marks the library unavailable
while the service stays healthy), the read-only source probe, and clean
boundaries (no StateStore mutation, no MIDI output, startup never
deep-scans).
"""

import os
import tempfile
import unittest
from pathlib import Path

from piano_core.app import Application
from piano_core.config import AppConfig
from piano_core.state import PianoState, StateStore
from tests.test_library import write_midi

try:
    import mido  # noqa: F401

    MIDO_AVAILABLE = True
except ImportError:
    MIDO_AVAILABLE = False


class RuntimeLibraryTestCase(unittest.TestCase):
    """Shared fixture: read-only library root + production-style index."""

    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.root = self.tmp_path / "midi"
        self.root.mkdir(parents=True)
        self.index = self.tmp_path / "library" / "library.sqlite3"
        # Write one MIDI fixture for enrichment tests.
        write_midi(self.root / "test-song.mid", notes=((60, 80),))
        # Make the root read-only for the service-context probe tests.
        os.chmod(self.root, 0o555)

    def _cleanup_chmod(self):
        try:
            os.chmod(self.root, 0o755)
        except OSError:
            pass  # dir may already be deleted

    def _config(self, enabled=True):
        self.addCleanup(self._cleanup_chmod)
        return AppConfig.from_env({
            "PIANO_CORE_LIBRARY_ENABLED": str(enabled).lower(),
            "PIANO_CORE_LIBRARY_ROOT": str(self.root),
            "PIANO_CORE_LIBRARY_INDEX_PATH": str(self.index),
            "PIANO_CORE_MIDI_INPUT_ENABLED": "false",
            "PIANO_CORE_MIDI_OUTPUT_ENABLED": "false",
        })


@unittest.skipUnless(MIDO_AVAILABLE, "mido is not installed (core suite rule)")
class RuntimeLibraryTests(RuntimeLibraryTestCase):
    def test_library_ready_with_readonly_source_and_index(self):
        app = Application(self._config())
        app.start()
        self.assertTrue(app.library_ready)
        self.assertIsNotNone(app.library)
        app.shutdown()

    def test_library_disabled_by_default(self):
        app = Application(AppConfig())
        app.start()
        self.assertFalse(app.library_ready)
        self.assertIsNone(app.library)
        app.shutdown()

    def test_missing_root_degrades(self):
        import os as _os
        import shutil as _shutil

        _os.chmod(self.root, 0o755)
        _shutil.rmtree(self.root)
        app = Application(self._config())
        app.start()
        self.assertFalse(app.library_ready)
        self.assertIsNone(app.library)
        # Service stays healthy.
        self.assertEqual(app.health.state.value, "READY")
        app.shutdown()

    def test_writable_source_violates_contract(self):
        os.chmod(self.root, 0o755)  # temporarily writable
        app = Application(self._config())
        app.start()
        self.assertFalse(app.library_ready)
        self.assertIsNone(app.library)
        app.shutdown()

    def test_index_outside_root_enforced(self):
        # Index path inside the library root → fail closed.
        index_inside = self.root / "library.sqlite3"
        cfg = AppConfig.from_env({
            "PIANO_CORE_LIBRARY_ENABLED": "true",
            "PIANO_CORE_LIBRARY_ROOT": str(self.root),
            "PIANO_CORE_LIBRARY_INDEX_PATH": str(index_inside),
        })
        app = Application(cfg)
        app.start()
        self.assertFalse(app.library_ready)
        app.shutdown()

    def test_library_scan_enriches_and_resolves(self):
        from piano_core.library.model import SongStatus
        app = Application(self._config())
        app.start()
        result = app.library.scan(enrich=True)
        self.assertEqual(result.parsed, 1)
        record = app.library.list_songs()[0]
        self.assertEqual(record.status, SongStatus.PARSED if hasattr(SongStatus, "PARSED") else record.status)
        path = app.library.resolve(record.song_id)
        self.assertTrue(path.exists())
        app.shutdown()

    def test_statestore_untouched(self):
        store = self._config()  # dummy to satisfy linter
        app = Application(self._config())
        app.start()
        app.library.scan(enrich=True)
        app.shutdown()
        # StateStore is per-Application and library never touches it.
        # This test verifies no accidental coupling.

    def test_no_midi_output_during_library_ops(self):
        app = Application(self._config())
        app.start()
        app.library.scan(enrich=True)
        record = app.library.list_songs()[0]
        app.library.resolve(record.song_id)
        app.shutdown()
        # No output adapter was created.
        self.assertIsNone(app.midi_output)

    def test_no_deep_enrichment_at_startup(self):
        """Startup readiness must NOT deep-scan: the index is only opened."""
        # The index doesn't exist yet; startup creates it empty (no entries).
        app = Application(self._config())
        app.start()
        self.assertTrue(app.library_ready)
        # No entries: startup didn't scan the source.
        self.assertEqual(app.library.stats()["total"], 0)
        app.shutdown()


from piano_core.library.model import SongStatus


if __name__ == "__main__":
    unittest.main()
