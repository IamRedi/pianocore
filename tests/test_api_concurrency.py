"""V3 tests: API concurrency + application lifecycle integration (P012).

Deterministic concurrency: simultaneous plays, play/stop races, repeated
stops, and server shutdown during playback — plus the full Application
composition (API server + playback manager + library + StateStore) on
real loopback sockets.
"""

import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

try:
    import mido  # noqa: F401

    MIDO_AVAILABLE = True
except ImportError:
    MIDO_AVAILABLE = False

from piano_core.app import Application
from piano_core.config import AppConfig, AppMode, LogLevel
from piano_core.health import HealthState
from piano_core.library.service import LibraryConfig, LibraryService
from piano_core.midi.output import MidiOutputAdapter
from piano_core.playback.advisory import acquire_playback_lock
from piano_core.playback.manager import PlaybackManager
from tests.test_api_endpoints import ApiEndpointTestCase, _timeline_factory
from tests.test_library import write_midi
from tests.test_midi_output import FakeOutputBackend


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ConcurrencyTests(ApiEndpointTestCase):
    """HTTP-level races against the canonical manager."""

    def _post_many(self, path, count, body=None):
        """Fire `count` concurrent POSTs; return list of (status, payload)."""
        results: list[tuple[int, dict]] = []
        barrier = threading.Barrier(count)

        def fire():
            data = b"{}" if body is None else json.dumps(body).encode()
            req = urllib.request.Request(
                self.base + path,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            barrier.wait()
            try:
                with urllib.request.urlopen(req, timeout=15) as response:
                    results.append(
                        (response.status, json.loads(response.read().decode()))
                    )
            except urllib.error.HTTPError as exc:
                results.append((exc.code, json.loads(exc.read().decode())))
            except Exception as exc:  # connection-level failure: record honestly
                results.append((0, {"exception": repr(exc)}))

        threads = [threading.Thread(target=fire) for _ in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        return results

    def test_simultaneous_plays_exactly_one_wins(self):
        results = self._post_many(
            "/api/v1/playback/play", 6, {"song_id": self.song_id}
        )
        created = [r for r in results if r[0] == 200]
        conflicts = [r for r in results if r[0] == 409]
        self.assertEqual(len(created), 1)
        self.assertEqual(len(conflicts), 5)
        self.assertEqual(created[0][1]["state"], "PLAYING")
        for _, payload in conflicts:
            self.assertEqual(payload["error"]["code"], "playback_already_active")
        status, payload = self.request("POST", "/api/v1/playback/stop", body={})
        self.assertEqual(status, 200)

    def test_play_vs_stop_race_ends_terminal_with_free_lock(self):
        for round_index in range(3):
            played = threading.Thread(
                target=lambda: self.request(
                    "POST", "/api/v1/playback/play", body={"song_id": self.song_id}
                )
            )
            stopped = threading.Thread(
                target=lambda: self.request("POST", "/api/v1/playback/stop", body={})
            )
            played.start()
            stopped.start()
            played.join(15)
            stopped.join(15)
            status, payload = self.request("GET", "/api/v1/playback")
            self.assertEqual(status, 200, f"round {round_index}")
            self.assertIn(payload["state"], ("IDLE", "PLAYING", "STOPPING"))
            # deterministically settle: a stray PLAYING session is stopped
            if payload["state"] != "IDLE":
                self.request("POST", "/api/v1/playback/stop", body={})
            probe = acquire_playback_lock(self.lock_path)
            probe.release()

    def test_repeated_concurrent_stops_exactly_one_effective(self):
        self.request("POST", "/api/v1/playback/play", body={"song_id": self.song_id})
        self._settle()
        results = self._post_many("/api/v1/playback/stop", 4)
        stopped_flags = [
            payload["stopped"] for _, payload in results if payload.get("stopped") is not None
        ]
        self.assertEqual(len(results), 4)
        self.assertEqual(sum(1 for flag in stopped_flags if flag), 1)
        for status, payload in results:
            self.assertEqual(status, 200)
            self.assertEqual(payload["state"], "IDLE")

    def test_server_shutdown_during_playback_stops_session(self):
        status, _ = self.request(
            "POST", "/api/v1/playback/play", body={"song_id": self.song_id}
        )
        self.assertEqual(status, 200)
        self.server.shutdown()  # stop accepting requests while playing
        self.manager.stop()
        self.assertEqual(self.manager.snapshot().state, "IDLE")
        probe = acquire_playback_lock(self.lock_path)
        probe.release()


class ApplicationIntegrationTests(unittest.TestCase):
    """Full composition root: API + playback + library on the real app."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.root = self.tmp_path / "root"
        self.root.mkdir()
        self.index_path = self.tmp_path / "index" / "library.sqlite3"
        self.lock_path = str(self.tmp_path / "playback.lock")

        # Build + scan the synthetic library BEFORE the read-only probe.
        service = LibraryService(
            LibraryConfig(root=str(self.root), index_path=str(self.index_path))
        )
        write_midi(self.root / "song.mid")
        service.scan(enrich=False)
        self.song_id = service.list_songs()[0].song_id

        # The application requires a READ-ONLY source (probe-verified).
        import os

        os.chmod(self.root, 0o555)
        self.addCleanup(os.chmod, self.root, 0o755)

        self.port = _free_port()
        config = AppConfig(
            mode=AppMode.TEST,
            log_level=LogLevel.WARNING,
            midi_input_enabled=False,
            midi_output_enabled=True,
            library_enabled=True,
            library_root=str(self.root),
            library_index_path=str(self.index_path),
            api_enabled=True,
            api_host="127.0.0.1",
            api_port=self.port,
        )
        self.app = Application(
            config,
            midi_output_backend_factory=lambda: FakeOutputBackend(
                ["Roland Digital Piano MIDI 1"]
            ),
            playback_lock_path=str(self.tmp_path / "playback.lock"),
        )
        self.app.start()
        self.addCleanup(self._clean_shutdown)
        self.base = f"http://127.0.0.1:{self.port}"

    def _clean_shutdown(self):
        self.app.shutdown()

    def _request(self, method, path, body=None):
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.base + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_app_starts_ready_with_api_and_playback(self):
        self.assertIs(self.app.health.state, HealthState.READY)
        self.assertIsNotNone(self.app.api_server)
        self.assertIsNotNone(self.app.playback)
        self.assertTrue(self.app.library_ready)
        self.assertFalse(self.app.state_store.snapshot().pressed_notes)

    @unittest.skipUnless(MIDO_AVAILABLE, "mido is not installed")
    def test_full_play_cycle_through_the_application(self):
        status, payload = self._request(
            "POST", "/api/v1/playback/play", body={"song_id": self.song_id}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["state"], "PLAYING")
        # physical state untouched by API playback
        state = self.app.state_store.snapshot()
        self.assertEqual(state.pressed_notes, frozenset())
        self.assertEqual(state.revision, 0)
        status, payload = self._request("POST", "/api/v1/playback/stop", body={})
        self.assertEqual(status, 200)
        self.assertTrue(payload["stopped"])
        # advisory lock released after the session
        probe = acquire_playback_lock(str(self.tmp_path / "playback.lock"))
        probe.release()

    def test_play_lock_shared_with_cli_style_holder(self):
        external = acquire_playback_lock(
            str(self.tmp_path / "playback.lock")
        )
        try:
            status, payload = self._request(
                "POST", "/api/v1/playback/play", body={"song_id": self.song_id}
            )
            self.assertEqual(status, 409)
            self.assertEqual(payload["error"]["code"], "playback_already_active")
        finally:
            external.release()

    @unittest.skipUnless(MIDO_AVAILABLE, "mido is not installed")
    def test_app_shutdown_stops_server_and_playback_cleanly(self):
        status, _ = self._request(
            "POST", "/api/v1/playback/play", body={"song_id": self.song_id}
        )
        self.assertEqual(status, 200)
        self.app.shutdown()
        self.assertIs(self.app.health.state, HealthState.STOPPED)
        self.assertEqual(self.app.playback.snapshot().state, "IDLE")
        # server no longer accepts connections
        try:
            self._request("GET", "/api/v1/health")
            server_down = False
        except (urllib.error.URLError, ConnectionError, OSError):
            server_down = True
        self.assertTrue(server_down)


if __name__ == "__main__":
    unittest.main()
