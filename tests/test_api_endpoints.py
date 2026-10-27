"""V2/V3 tests: /api/v1 endpoints over canonical subsystems (PIANO-P012).

Real StateStore, real synthetic LibraryService, real PlaybackManager
(scripted output backend + injected timeline loader) — the full endpoint
contract without hardware or mido.
"""

import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from piano_core.api.server import ApiServer
from piano_core.health import HealthState
from piano_core.library.service import LibraryConfig, LibraryService
from piano_core.live_notes import LiveNoteState
from piano_core.midi.events import note_on as input_note_on
from piano_core.midi.output import MidiOutputAdapter
from piano_core.playback.manager import PlaybackManager
from piano_core.state import StateStore
from tests.test_library import write_midi
from tests.test_midi_output import FakeOutputBackend


def _timeline_factory(long_gap: float = 30.0):
    from piano_core.midi.output_events import note_off, note_on
    from piano_core.playback.timeline import Timeline, TimelineEvent

    def _load(path, tempo_scale=1.0):
        return Timeline(
            events=(
                TimelineEvent(0.0, note_on(1, 60, 80)),
                TimelineEvent(long_gap, note_off(1, 60, 0)),
            ),
            duration=long_gap,
            source=str(path),
        )

    return _load


class ApiEndpointTestCase(unittest.TestCase):
    """Shared harness: live API over real canonical subsystems."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.root = self.tmp_path / "root"
        self.root.mkdir()
        self.index_path = self.tmp_path / "index" / "library.sqlite3"
        self.lock_path = str(self.tmp_path / "playback.lock")
        for relpath, notes in (
            ("a.mid", ((60, 80),)),
            ("sub/b.mid", ((62, 80),)),
            ("sub/c.mid", ((64, 80),)),
        ):
            write_midi(self.root / relpath, notes=notes)

        self.backend = FakeOutputBackend(["Roland Digital Piano MIDI 1"])
        self.adapter = MidiOutputAdapter(self.backend, matcher="roland")
        self.adapter.open()
        self.port = self.backend.outputs[-1]
        self.library = LibraryService(
            LibraryConfig(root=str(self.root), index_path=str(self.index_path))
        )
        self.library.scan(enrich=False)

        self.state_store = StateStore()
        self.live_notes = LiveNoteState(runtime_epoch="api-test-runtime")
        self.live_notes.publish_playback(
            available=True,
            owned_notes=self.adapter.owned_notes,
            sustain_channels=self.adapter.owned_sustain_channels,
        )

        def observe_output():
            self.live_notes.publish_playback(
                available=self.adapter.is_open,
                owned_notes=self.adapter.owned_notes,
                sustain_channels=self.adapter.owned_sustain_channels,
            )

        self.manager = PlaybackManager(
            output_adapter=self.adapter,
            library=self.library,
            timeline_loader=_timeline_factory(),
            lock_path=self.lock_path,
            ownership_observer=observe_output,
        )
        self.context = _EndpointContext(
            health_state=HealthState.READY,
            state_store=self.state_store,
            library=self.library,
            library_ready=True,
            playback=self.manager,
            live_notes=self.live_notes,
        )
        self.server = ApiServer(self.context, "127.0.0.1", 0)
        self.server.start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.manager.shutdown)
        self.addCleanup(self.adapter.close)
        host, port = self.server.bound_address
        self.base = f"http://{host}:{port}"
        self.song_id = self.library.list_songs()[0].song_id

    def request(self, method, path, body=None):
        data = None
        headers = {}
        if body is not None:
            import json

            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.base + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                import json

                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            import json

            return exc.code, json.loads(exc.read().decode())

    def _settle(self):
        time.sleep(0.05)


class _EndpointContext:
    """Duck-typed API context (mirrors Application's surface)."""

    def __init__(
        self,
        health_state,
        state_store,
        library,
        library_ready,
        playback,
        live_notes,
    ):
        from piano_core.health import Health

        self.health = Health(state=health_state)
        self.state_store = state_store
        self.library = library
        self.library_ready = library_ready
        self.playback = playback
        self.live_notes = live_notes


class StateEndpointTests(ApiEndpointTestCase):
    def test_state_reflects_physical_input(self):
        self.state_store.apply_event(input_note_on(1, 60, 90, 1.0))
        self.state_store.apply_event(input_note_on(1, 64, 90, 2.0))
        status, payload = self.request("GET", "/api/v1/state")
        self.assertEqual(status, 200)
        self.assertEqual(payload["pressed_notes"], [60, 64])
        self.assertEqual(payload["sustain"], {"value": 0, "pressed": False})
        self.assertEqual(payload["midi_connection"], "DISCONNECTED")
        self.assertEqual(payload["revision"], 2)
        self.assertEqual(payload["last_input"]["note"], 64)

    def test_pressed_notes_stay_sorted_and_physical_only(self):
        self.state_store.apply_event(input_note_on(1, 72, 90, 1.0))
        self.state_store.apply_event(input_note_on(1, 48, 90, 2.0))
        _, payload = self.request("GET", "/api/v1/state")
        self.assertEqual(payload["pressed_notes"], [48, 72])
        # playback runs but the physical state stays untouched
        status, _ = self.request(
            "POST", "/api/v1/playback/play", body={"song_id": self.song_id}
        )
        self.assertEqual(status, 200)
        self._settle()
        _, payload = self.request("GET", "/api/v1/state")
        self.assertEqual(payload["pressed_notes"], [48, 72])
        self.assertEqual(payload["revision"], 2)
        self.manager.stop()


class LiveNoteEndpointTests(ApiEndpointTestCase):
    def test_unstarted_stream_close_releases_reserved_capacity(self):
        from piano_core.api.handlers import live_note_stream

        status, stream = live_note_stream(self.context, None)
        self.assertEqual(status, 200)
        stream.close()
        subscriptions = [self.live_notes.subscribe() for _ in range(8)]
        for subscription in subscriptions:
            subscription.close()

    def test_snapshot_separates_physical_playback_and_voice_identity(self):
        from piano_core.state import MidiConnectionState

        self.state_store.set_midi_connection(MidiConnectionState.CONNECTED)
        self.state_store.apply_event(input_note_on(1, 60, 90, 1.0))
        self.live_notes.publish_physical(self.state_store.snapshot())
        self.request("POST", "/api/v1/playback/play", body={"song_id": self.song_id})
        self._settle()
        status, payload = self.request("GET", "/api/v1/live-notes")
        self.assertEqual(status, 200)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["runtime_epoch"], "api-test-runtime")
        self.assertEqual(payload["physical"]["notes"], [60])
        self.assertEqual(payload["playback"]["notes"], [60])
        self.assertEqual(
            payload["playback"]["voices"], [{"channel": 1, "note": 60}]
        )
        self.manager.stop()
        _, stopped = self.request("GET", "/api/v1/live-notes")
        self.assertEqual(stopped["playback"]["notes"], [])

    def test_stopped_song_can_be_replaced_without_leaking_owned_notes(self):
        song_ids = [song.song_id for song in self.library.list_songs()]
        self.manager.play(song_ids[0])
        self._settle()
        self.manager.stop()
        self.assertEqual(self.live_notes.snapshot().playback_voices, ())
        self.manager.play(song_ids[1])
        self._settle()
        self.assertEqual(self.live_notes.snapshot().playback_voices, ((1, 60),))
        self.manager.stop()

    def test_snapshot_filters_notes_outside_the_88_key_display(self):
        self.state_store.apply_event(input_note_on(1, 12, 90, 1.0))
        self.state_store.apply_event(input_note_on(1, 108, 90, 2.0))
        self.live_notes.publish_physical(self.state_store.snapshot())
        _, payload = self.request("GET", "/api/v1/live-notes")
        self.assertEqual(payload["physical"]["notes"], [108])
        self.assertEqual(payload["physical"]["outside_keyboard_count"], 1)

    def test_stream_starts_with_complete_snapshot(self):
        request = urllib.request.Request(
            self.base + "/api/v1/live-notes/stream", method="GET"
        )
        response = urllib.request.urlopen(request, timeout=5)
        self.addCleanup(response.close)
        self.assertEqual(response.headers.get_content_type(), "text/event-stream")
        lines = [response.readline().decode().strip() for _ in range(4)]
        self.assertTrue(lines[0].startswith("id: "))
        self.assertEqual(lines[1], "event: snapshot")
        self.assertTrue(lines[2].startswith("data: {") and '"physical"' in lines[2])
        self.assertEqual(lines[3], "")


class LibraryEndpointTests(ApiEndpointTestCase):
    def test_library_stats(self):
        status, payload = self.request("GET", "/api/v1/library")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["total"], 3)
        self.assertEqual(payload["pending"], 3)
        self.assertEqual(payload["parsed"], 0)

    def test_library_unavailable_is_503(self):
        self.context.library_ready = False
        status, payload = self.request("GET", "/api/v1/library")
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"]["code"], "library_unavailable")

    def test_songs_deterministic_order_and_pagination(self):
        status, page1 = self.request("GET", "/api/v1/library/songs?limit=2&offset=0")
        status2, page2 = self.request("GET", "/api/v1/library/songs?limit=2&offset=2")
        self.assertEqual(status, 200)
        self.assertEqual(status2, 200)
        relpaths = [s["relpath"] for s in page1["songs"] + page2["songs"]]
        self.assertEqual(relpaths, sorted(relpaths))
        self.assertEqual([s["relpath"] for s in page1["songs"]], ["a.mid", "sub/b.mid"])
        self.assertEqual(page1["count"], 2)

    def test_songs_bounded_page_size(self):
        status, payload = self.request("GET", "/api/v1/library/songs?limit=101")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_parameter")
        status, payload = self.request("GET", "/api/v1/library/songs?limit=0")
        self.assertEqual(status, 400)

    def test_songs_search(self):
        status, payload = self.request("GET", "/api/v1/library/songs?q=b.mid")
        self.assertEqual(status, 200)
        self.assertEqual([s["relpath"] for s in payload["songs"]], ["sub/b.mid"])

    def test_songs_status_filter(self):
        status, payload = self.request(
            "GET", "/api/v1/library/songs?status=parsed"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 0)
        status, payload = self.request(
            "GET", "/api/v1/library/songs?status=pending"
        )
        self.assertEqual(payload["count"], 3)

    def test_songs_rejects_unknown_params_and_bad_values(self):
        status, _ = self.request("GET", "/api/v1/library/songs?zzz=1")
        self.assertEqual(status, 400)
        status, _ = self.request("GET", "/api/v1/library/songs?status=bogus")
        self.assertEqual(status, 400)
        status, _ = self.request("GET", "/api/v1/library/songs?format=7")
        self.assertEqual(status, 400)
        status, _ = self.request("GET", "/api/v1/library/songs?q=x&offset=3")
        self.assertEqual(status, 400)

    def test_song_lookup_found_and_missing(self):
        status, payload = self.request("GET", f"/api/v1/library/songs/{self.song_id}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["song_id"], self.song_id)
        self.assertEqual(payload["relpath"], "a.mid")
        self.assertNotIn("error", payload)  # internals stay internal
        self.assertNotIn("mtime_ns", payload)
        status, payload = self.request(
            "GET", "/api/v1/library/songs/00000000-0000-5000-8000-000000000000"
        )
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "song_not_found")


class PlaybackEndpointTests(ApiEndpointTestCase):
    def test_playback_state_shape(self):
        status, payload = self.request("GET", "/api/v1/playback")
        self.assertEqual(status, 200)
        self.assertTrue(payload["available"])
        self.assertEqual(payload["state"], "IDLE")

    def test_play_pause_resume_stop_roundtrip(self):
        status, payload = self.request(
            "POST", "/api/v1/playback/play", body={"song_id": self.song_id}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["state"], "PLAYING")
        self.assertEqual(payload["song_id"], self.song_id)
        self.assertEqual(payload["tempo_multiplier"], 1.0)

        status, payload = self.request("POST", "/api/v1/playback/pause", body={})
        self.assertEqual(status, 200)
        self.assertEqual(payload["state"], "PAUSED")

        status, payload = self.request("POST", "/api/v1/playback/resume", body={})
        self.assertEqual(status, 200)
        self.assertEqual(payload["state"], "PLAYING")

        status, payload = self.request("POST", "/api/v1/playback/stop", body={})
        self.assertEqual(status, 200)
        self.assertTrue(payload["stopped"])
        self.assertEqual(payload["state"], "IDLE")
        self.assertIsNone(payload["song_id"])

        status, payload = self.request("POST", "/api/v1/playback/stop", body={})
        self.assertEqual(status, 200)
        self.assertFalse(payload["stopped"])

    def test_play_with_tempo_multiplier(self):
        status, payload = self.request(
            "POST",
            "/api/v1/playback/play",
            body={"song_id": self.song_id, "tempo_multiplier": 2.0},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["tempo_multiplier"], 2.0)
        self.manager.stop()

    def test_completed_playback_retains_song_identity_and_duration(self):
        self.manager._load_timeline = _timeline_factory(long_gap=0.05)
        status, payload = self.request(
            "POST", "/api/v1/playback/play", body={"song_id": self.song_id}
        )
        self.assertEqual(status, 200)
        self.assertTrue(self.manager._engine.wait(5.0))
        for _ in range(50):
            if self.manager._watcher is None:
                break
            time.sleep(0.02)
        status, payload = self.request("GET", "/api/v1/playback")
        self.assertEqual(status, 200)
        self.assertEqual(payload["state"], "COMPLETED")
        self.assertEqual(payload["song_id"], self.song_id)
        self.assertEqual(payload["position_seconds"], payload["duration_seconds"])

    def test_play_conflict_is_409(self):
        self.request("POST", "/api/v1/playback/play", body={"song_id": self.song_id})
        self._settle()
        status, payload = self.request(
            "POST", "/api/v1/playback/play", body={"song_id": self.song_id}
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "playback_already_active")
        self.manager.stop()

    def test_invalid_transitions_are_409(self):
        status, payload = self.request("POST", "/api/v1/playback/pause", body={})
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "invalid_state_transition")
        status, payload = self.request("POST", "/api/v1/playback/resume", body={})
        self.assertEqual(status, 409)

    def test_unknown_song_is_404(self):
        status, payload = self.request(
            "POST",
            "/api/v1/playback/play",
            body={"song_id": "00000000-0000-5000-8000-000000000000"},
        )
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "song_not_found")

    def test_song_source_missing_is_410(self):
        (self.root / "a.mid").unlink()
        song_id = next(
            record.song_id
            for record in self.library.list_songs(limit=10)
            if record.relpath == "a.mid"
        )
        status, payload = self.request(
            "POST", "/api/v1/playback/play", body={"song_id": song_id}
        )
        self.assertEqual(status, 410)
        self.assertEqual(payload["error"]["code"], "song_source_missing")

    def test_stop_releases_owned_notes_never_panic(self):
        self.request("POST", "/api/v1/playback/play", body={"song_id": self.song_id})
        self._settle()
        self.request("POST", "/api/v1/playback/stop", body={})
        sent = self.port.sent
        note_offs = [s for s in sent if s[0] == "note_off"]
        self.assertTrue(any(s[2] == 60 for s in note_offs))
        for entry in sent:
            if entry[0] == "control_change":
                self.assertNotIn(entry[2], (120, 123))


if __name__ == "__main__":
    unittest.main()
