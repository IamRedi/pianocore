"""V2 tests: piano-core-play CLI surface (PIANO-P009).

Runs the CLI ``main()`` directly with an injected fake output backend,
empty environment, and a private lock file — no mido needed for the
usage/config/lock failure paths; full playback paths additionally need
the canonical mido stack (skipUnless, core-suite rule).
"""

import fcntl
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from piano_core.playback.cli import LOCK_FILENAME, main
from tests.test_midi_output import FakeOutputBackend

try:
    import mido  # noqa: F401

    MIDO_AVAILABLE = True
except ImportError:
    MIDO_AVAILABLE = False


def _vlq(value: int) -> bytes:
    out = bytearray([value & 0x7F])
    value >>= 7
    while value:
        out.insert(0, 0x80 | (value & 0x7F))
        value >>= 7
    return bytes(out)


def _track(*events: tuple[int, bytes]) -> bytes:
    data = b"".join(_vlq(delta) + payload for delta, payload in events)
    return b"MTrk" + len(data).to_bytes(4, "big") + data


def _smf(fmt: int, division: int, *tracks: bytes) -> bytes:
    header = b"MThd" + (6).to_bytes(4, "big") + fmt.to_bytes(2, "big") + len(
        tracks
    ).to_bytes(2, "big") + division.to_bytes(2, "big")
    return header + b"".join(tracks)


def _write_short_song(path: Path) -> None:
    """A ~0.5 s two-note song (tpb=120, default tempo)."""
    path.write_bytes(
        _smf(
            0,
            120,
            _track(
                (60, bytes([0x90, 60, 80])),
                (60, bytes([0x80, 60, 0])),
                (60, bytes([0x90, 64, 80])),
                (60, bytes([0x80, 64, 0])),
                (0, b"\xff\x2f\x00"),
            ),
        )
    )


class CliUsageTests(unittest.TestCase):
    """Failure paths that never touch mido or the output backend."""

    def test_version_flag_exits_zero(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["--version"])
        self.assertEqual(code, 0)
        self.assertIn("piano-core", stdout.getvalue())

    def test_missing_file_argument_returns_2(self):
        code = main([], env={}, lock_path=self._lock())
        self.assertEqual(code, 2)

    def test_bad_tempo_returns_2_before_any_io(self):
        code = main(
            ["--file", "/nonexistent/song.mid", "--tempo", "99"],
            env={},
            lock_path=self._lock(),
        )
        self.assertEqual(code, 2)

    def test_bad_stop_after_returns_2(self):
        code = main(
            ["--file", "/nonexistent/song.mid", "--stop-after", "0"],
            env={},
            lock_path=self._lock(),
        )
        self.assertEqual(code, 2)

    def test_unknown_env_variable_returns_2(self):
        code = main(
            ["--file", "/nonexistent/song.mid"],
            env={"PIANO_CORE_BOGUS": "x"},
            lock_path=self._lock(),
        )
        self.assertEqual(code, 2)

    def test_missing_file_returns_1(self):
        code = main(
            ["--file", "/nonexistent/song.mid"], env={}, lock_path=self._lock()
        )
        self.assertEqual(code, 1)

    def test_garbage_file_returns_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.mid"
            path.write_bytes(b"definitely not a midi file")
            code = main(["--file", str(path)], env={}, lock_path=self._lock())
        self.assertEqual(code, 1)

    def test_held_lock_returns_1_without_touching_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = str(Path(tmp) / LOCK_FILENAME)
            holder = open(lock_path, "w")  # noqa: SIM115 (test-scoped)
            try:
                fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                code = main(
                    ["--file", "/nonexistent/song.mid"],
                    env={},
                    lock_path=lock_path,
                )
            finally:
                holder.close()
        self.assertEqual(code, 1)

    def _lock(self) -> str:
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".lock")
        handle.close()
        return handle.name


@unittest.skipUnless(MIDO_AVAILABLE, "mido is not installed (core suite rule)")
class CliPlaybackTests(unittest.TestCase):
    """Full playback paths through the canonical mido stack."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lock_path = str(Path(self.tmp.name) / LOCK_FILENAME)

    def _backend_factory(self):
        self.backend = FakeOutputBackend(["Roland Digital Piano 1"])
        return lambda: self.backend

    def test_successful_playback_exits_zero(self):
        song = Path(self.tmp.name) / "song.mid"
        _write_short_song(song)
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(
                ["--file", str(song)],
                env={},
                backend_factory=self._backend_factory(),
                lock_path=self.lock_path,
            )
        self.assertEqual(code, 0)
        self.assertIn("outcome=COMPLETED", stdout.getvalue())
        port = self.backend.outputs[-1]
        self.assertEqual(
            port.sent,
            [
                ("note_on", 1, 60, 80),
                ("note_off", 1, 60, 0),
                ("note_on", 1, 64, 80),
                ("note_off", 1, 64, 0),
            ],
        )
        self.assertTrue(port.closed)  # adapter closed on exit

    def test_lock_released_after_run(self):
        import fcntl as _fcntl

        song = Path(self.tmp.name) / "song.mid"
        _write_short_song(song)
        main(
            ["--file", str(song)],
            env={},
            backend_factory=self._backend_factory(),
            lock_path=self.lock_path,
        )
        # The advisory lock must be free again: acquiring must succeed.
        probe = open(lock_path := self.lock_path, "w")  # noqa: SIM115
        try:
            _fcntl.flock(probe.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            _fcntl.flock(probe.fileno(), _fcntl.LOCK_UN)
        finally:
            probe.close()

    def test_stop_after_cap_stops_and_releases_notes(self):
        song = Path(self.tmp.name) / "long.mid"
        # A long song: one note held for ~200 s (tpb=120, one beat = 0.5 s).
        song.write_bytes(
            _smf(
                0,
                120,
                _track(
                    (0, bytes([0x90, 60, 80])),
                    (120 * 400, bytes([0x80, 60, 0])),
                    (0, b"\xff\x2f\x00"),
                ),
            )
        )
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(
                ["--file", str(song), "--stop-after", "0.5"],
                env={},
                backend_factory=self._backend_factory(),
                lock_path=self.lock_path,
            )
        self.assertEqual(code, 0)
        self.assertIn("outcome=IDLE", stdout.getvalue())  # intentional stop
        port = self.backend.outputs[-1]
        self.assertIn(("note_on", 1, 60, 80), port.sent)
        self.assertIn(("note_off", 1, 60, 0), port.sent)  # owned note released
        self.assertTrue(port.closed)

    def test_endpoint_failure_returns_1(self):
        song = Path(self.tmp.name) / "song.mid"
        _write_short_song(song)
        self.backend = FakeOutputBackend(["System Timer"])  # no roland match
        code = main(
            ["--file", str(song)],
            env={},
            backend_factory=lambda: self.backend,
            lock_path=self.lock_path,
        )
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
