"""PIANO-UI-006 HTTP contract and failure semantics."""

from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from piano_core.api.server import ApiServer
from piano_core.fingering import FingeringService
from piano_core.fingering import FingeringUnsupportedError
from piano_core.health import Health, HealthState
from piano_core.library.service import LibraryConfig, LibraryService
from piano_core.midi.output_events import note_off, note_on
from piano_core.playback.score import ScoreService
from piano_core.playback.timeline import Timeline, TimelineEvent
from tests.test_library import write_midi


class FingeringApiTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "root"
        root.mkdir()
        write_midi(root / "song.mid", notes=((48, 80), (60, 80)))
        library = LibraryService(
            LibraryConfig(root=str(root), index_path=str(Path(tmp.name) / "index.sqlite3"))
        )
        library.scan(enrich=False)
        self.song_id = library.list_songs(limit=1)[0].song_id
        events = (
            TimelineEvent(0, note_on(1, 48, 80)),
            TimelineEvent(0, note_on(1, 60, 80)),
            TimelineEvent(1, note_off(1, 48, 0)),
            TimelineEvent(1, note_off(1, 60, 0)),
        )
        timeline = Timeline(events=events, duration=1, source="song.mid")
        score = ScoreService(library, loader=lambda path: timeline)

        class Context:
            health = Health()
            state_store = None
            library_ready = True
            playback = None
            practice = None

        self.context = Context()
        self.context.fingering = FingeringService(score)
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

    def test_complete_version_bound_payload(self):
        status, payload = self.request(f"/api/v1/fingering/{self.song_id}")
        self.assertEqual(200, status)
        self.assertEqual(self.song_id, payload["song_id"])
        self.assertRegex(payload["plan_id"], r"^fingering-sha256:[0-9a-f]{64}$")
        self.assertEqual("balanced-engine-v1", payload["engine_version"])
        self.assertRegex(payload["source_version"], r"^score-sha256:[0-9a-f]{64}$")
        self.assertEqual("DETERMINISTIC_INFERENCE", payload["provenance"])
        self.assertEqual(2, len(payload["targets"]))
        self.assertFalse(payload["capabilities"]["manual_overrides"])
        self.assertFalse(payload["capabilities"]["finger_sensitive_grading"])
        self.assertTrue(all(t["hand"] in {"LEFT", "RIGHT"} for t in payload["targets"]))

    def test_repeat_response_is_identical(self):
        first = self.request(f"/api/v1/fingering/{self.song_id}")
        second = self.request(f"/api/v1/fingering/{self.song_id}")
        self.assertEqual(first, second)

    def test_missing_subsystem_and_unknown_song_are_typed(self):
        self.context.fingering = None
        status, payload = self.request(f"/api/v1/fingering/{self.song_id}")
        self.assertEqual(503, status)
        self.assertEqual("fingering_unavailable", payload["error"]["code"])

    def test_no_path_traversal(self):
        status, payload = self.request("/api/v1/fingering/a/b")
        self.assertEqual(404, status)
        self.assertEqual("not_found", payload["error"]["code"])

    def test_unsupported_material_is_typed_422(self):
        class Unsupported:
            def get(self, song_id):
                raise FingeringUnsupportedError("impossible chord")

        self.context.fingering = Unsupported()
        status, payload = self.request(f"/api/v1/fingering/{self.song_id}")
        self.assertEqual(422, status)
        self.assertEqual("fingering_unsupported", payload["error"]["code"])


if __name__ == "__main__":
    unittest.main()
