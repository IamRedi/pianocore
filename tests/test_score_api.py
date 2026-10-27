"""PIANO-UI-005: /api/v1/score endpoint tests (HTTP + serialization).

Real loopback sockets (P012 envelope discipline), a real temporary
library catalog, and a deterministic injected timeline loader so the
endpoint behavior is testable without mido on the host. The projection
itself is covered by tests/test_playback_score.py.
"""

import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from piano_core.api.serialization import score_payload
from piano_core.api.server import ApiServer
from piano_core.health import Health, HealthState
from piano_core.library.service import LibraryConfig, LibraryService
from piano_core.playback.score import (
    ScoreProjection,
    ScoreSpan,
    ScoreTooLargeError,
    ScoreService,
)
from piano_core.playback.timeline import Timeline, TimelineEvent
from piano_core.midi.output_events import note_off, note_on
from tests.test_library import write_midi


def _timeline(pairs, duration):
    events = tuple(TimelineEvent(time, command) for time, command in pairs)
    return Timeline(events=events, duration=duration, source="score.mid")


class ScoreApiTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        root = self.tmp / "root"
        root.mkdir()
        write_midi(root / "song.mid", notes=((60, 80), (64, 80)))
        self.library = LibraryService(
            LibraryConfig(
                root=str(root),
                index_path=str(self.tmp / "index" / "library.sqlite3"),
            )
        )
        self.library.scan(enrich=False)
        self.song_id = self.library.list_songs(limit=10)[0].song_id

        self.timeline = _timeline(
            (
                (0.0, note_on(1, 60, 90)),
                (1.0, note_off(1, 60, 0)),
                (1.0, note_on(1, 64, 80)),
                (2.0, note_off(1, 64, 0)),
            ),
            duration=2.0,
        )
        self.loads = []

        def loader(path, *args, **kwargs):
            self.loads.append(str(path))
            return self.timeline

        self.loader = loader

        from piano_core.health import Health

        class Context:
            health = Health()
            state_store = None
            library = self.library
            library_ready = True
            playback = None
            practice = None
            score = ScoreService(self.library, loader=loader)

        self.context = Context()
        self.context.health.transition(HealthState.READY)
        self.server = ApiServer(self.context, "127.0.0.1", 0)
        self.server.start()
        self.addCleanup(self.server.shutdown)
        host, port = self.server.bound_address
        self.base = f"http://{host}:{port}"

    def request(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_score_payload_shape_and_honest_capabilities(self):
        status, payload = self.request(
            f"/api/v1/score/{self.song_id}"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["song_id"], self.song_id)
        self.assertRegex(payload["source_version"], r"^score-sha256:[0-9a-f]{64}$")
        self.assertEqual(payload["title"], "song.mid")
        self.assertEqual(payload["duration_seconds"], 2.0)
        self.assertEqual(payload["time_basis"], "song_seconds")
        self.assertEqual(payload["note_count"], 2)
        self.assertEqual(len(payload["notes"]), 2)
        first, second = payload["notes"]
        self.assertEqual(
            (
                first["index"], first["midi"], first["channel"],
                first["velocity"], first["onset_seconds"],
                first["duration_seconds"],
            ),
            (0, 60, 1, 90, 0.0, 1.0),
        )
        self.assertEqual(second["onset_seconds"], 1.0)
        # Diagnostics + honest notation capability contract.
        self.assertEqual(
            payload["diagnostics"],
            {
                "percussion_note_ons": 0,
                "outside_keyboard_note_ons": 0,
                "unpaired_note_offs": 0,
                "retriggered_note_ons": 0,
                "unterminated_note_ons": 0,
            },
        )
        self.assertIn("pitch", payload["notation"]["supported"])
        for unsupported in ("measure", "beat", "hand", "fingering", "tempo_map"):
            self.assertIn(unsupported, payload["notation"]["unsupported"])

    def test_score_is_cached_per_resolved_path(self):
        for _ in range(3):
            status, _payload = self.request(f"/api/v1/score/{self.song_id}")
            self.assertEqual(status, 200)
        self.assertEqual(len(self.loads), 1)

    def test_unknown_song_is_typed_404(self):
        status, payload = self.request("/api/v1/score/00000000-dead-beef-0000-000000000000")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "song_not_found")

    def test_oversized_score_is_typed_413(self):
        from piano_core.playback import score as score_module

        original = score_module.MAX_SCORE_SPANS
        score_module.MAX_SCORE_SPANS = 1
        self.context.score = ScoreService(
            self.library, loader=self.loader
        )
        # Also lower the service constant path used at build time.
        try:
            status, payload = self.request(f"/api/v1/score/{self.song_id}")
        finally:
            score_module.MAX_SCORE_SPANS = original
        self.assertEqual(status, 413)
        self.assertEqual(payload["error"]["code"], "score_too_large")

    def test_missing_subsystem_is_typed_503(self):
        self.context.score = None
        status, payload = self.request(f"/api/v1/score/{self.song_id}")
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"]["code"], "score_unavailable")

    def test_no_traversal_or_extra_paths(self):
        status, payload = self.request("/api/v1/score/../etc/passwd")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "not_found")
        status, payload = self.request("/api/v1/score/a/b")
        self.assertEqual(status, 404)

    def test_serialization_is_deterministic(self):
        from piano_core.playback.score import build_score_projection

        projection = build_score_projection(self.timeline, self.song_id, "T")
        self.assertEqual(score_payload(projection), score_payload(projection))
        encoded = json.dumps(
            score_payload(projection), sort_keys=True, allow_nan=False
        )
        self.assertIn('"note_count"', encoded)

    def test_source_version_has_canonical_numbers_and_sensitive_fields(self):
        def projection(midi=60, onset=0.0, duration=1.0):
            return ScoreProjection(
                "song",
                "Title",
                onset + duration,
                (ScoreSpan(0, midi, 1, onset, duration, 80),),
            )

        positive = projection(onset=0.0)
        negative = projection(onset=-0.0)
        self.assertEqual(positive.source_version, negative.source_version)
        self.assertEqual(positive.source_version, projection().source_version)
        self.assertNotEqual(positive.source_version, projection(midi=61).source_version)
        self.assertNotEqual(positive.source_version, projection(onset=0.25).source_version)
        self.assertNotEqual(positive.source_version, projection(duration=1.25).source_version)
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    ScoreProjection("song", "Title", value, ())
                with self.assertRaises(ValueError):
                    ScoreSpan(0, 60, 1, value, 1, 80)


class ScoreTooLargeGuardTests(unittest.TestCase):
    def test_projection_builder_enforces_the_same_bound(self):
        # The builder raises before serialization can ever see an
        # unbounded payload (defense in depth with the handler's 413).
        from piano_core.playback import score as score_module

        events = []
        for index in range(score_module.MAX_SCORE_SPANS + 1):
            time = index * 0.001
            events.append(TimelineEvent(time, note_on(1, 60, 90)))
            events.append(TimelineEvent(time, note_off(1, 60, 0)))
        events.sort(key=lambda event: event.time)
        timeline = Timeline(events=tuple(events), duration=100.0, source="big.mid")
        with self.assertRaises(ScoreTooLargeError):
            score_module.build_score_projection(timeline, "s", "T")


if __name__ == "__main__":
    unittest.main()
