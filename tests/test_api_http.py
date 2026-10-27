"""V2/V3 tests: HTTP layer of the local control API (PIANO-P012).

Server-level behavior on real loopback sockets with a fake context:
deterministic routing, bounded bodies, the stable JSON error envelope,
and zero information leakage on unexpected failures.
"""

import json
import unittest
import urllib.error
import urllib.request
from dataclasses import replace

from piano_core.api.server import MAX_BODY_BYTES, ApiServer
from piano_core.health import Health, HealthState
from piano_core.playback.manager import PlaybackSession
from piano_core.state import StateStore


class FakeManager:
    """Scripted playback manager (records control calls)."""

    def __init__(self):
        self.calls: list[tuple] = []
        self._session = PlaybackSession(
            state="IDLE",
            tempo_multiplier=1.0,
            position_seconds=0.0,
            duration_seconds=0.0,
            events_sent=0,
            error=None,
            song_id=None,
            title=None,
            available=True,
        )

    @property
    def available(self) -> bool:
        return True

    def snapshot(self) -> PlaybackSession:
        return self._session

    def play(self, song_id, tempo_multiplier=1.0):
        self.calls.append(("play", song_id, tempo_multiplier))
        self._session = replace(
            self._session, state="PLAYING", song_id=song_id, title="t.mid"
        )
        return self._session

    def pause(self):
        self.calls.append(("pause",))
        self._session = replace(self._session, state="PAUSED")
        return self._session

    def resume(self):
        self.calls.append(("resume",))
        self._session = replace(self._session, state="PLAYING")
        return self._session

    def stop(self):
        self.calls.append(("stop",))
        stopped = self._session.state in ("PLAYING", "PAUSED", "STOPPING")
        self._session = replace(
            self._session, state="IDLE", song_id=None, title=None
        )
        return self._session, stopped


class FakeContext:
    def __init__(self):
        self.health = Health()
        self.state_store = StateStore()
        self.library = None
        self.library_ready = False
        self.playback = None


class ApiHttpTestCase(unittest.TestCase):
    """Shared harness: one live server on an ephemeral loopback port."""

    def setUp(self):
        self.context = FakeContext()
        self.context.health.transition(HealthState.READY)
        self.server = ApiServer(self.context, "127.0.0.1", 0)
        self.server.start()
        self.addCleanup(self.server.shutdown)
        host, port = self.server.bound_address
        self.base = f"http://{host}:{port}"

    # -- helpers -------------------------------------------------------
    def request(self, method, path, body=None, content_type=None):
        """Issue one request; returns (status, payload_dict_or_bytes)."""
        url = self.base + path
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode() if isinstance(body, dict) else body
            headers["Content-Type"] = content_type or "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, raw


class RoutingTests(ApiHttpTestCase):
    def test_unknown_route_is_404(self):
        status, payload = self.request("GET", "/api/v1/nope")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "not_found")

    def test_unknown_prefix_is_404(self):
        status, payload = self.request("GET", "/health")
        self.assertEqual(status, 404)

    def test_wrong_method_is_405(self):
        status, payload = self.request("POST", "/api/v1/health", body={})
        self.assertEqual(status, 405)
        self.assertEqual(payload["error"]["code"], "method_not_allowed")

    def test_get_on_post_route_is_405(self):
        status, payload = self.request("GET", "/api/v1/playback/play")
        self.assertEqual(status, 405)

    def test_delete_method_is_handled_deterministically(self):
        status, payload = self.request("DELETE", "/api/v1/state")
        self.assertEqual(status, 405)
        self.assertEqual(payload["error"]["code"], "method_not_allowed")

    def test_unknown_query_parameter_is_400(self):
        status, payload = self.request("GET", "/api/v1/state?filter=x")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_parameter")

    def test_responses_are_json_with_content_type(self):
        url = self.base + "/api/v1/health"
        with urllib.request.urlopen(url, timeout=10) as response:
            self.assertEqual(response.status, 200)
            self.assertTrue(
                response.headers["Content-Type"].startswith("application/json")
            )
            json.loads(response.read().decode())


class ErrorModelTests(ApiHttpTestCase):
    def test_error_payload_shape_is_stable(self):
        status, payload = self.request("GET", "/api/v1/nope")
        self.assertEqual(
            payload,
            {
                "error": {
                    "code": "not_found",
                    "message": payload["error"]["message"],
                    "status": 404,
                }
            },
        )

    def test_internal_failure_leaks_nothing(self):
        def _boom():
            raise RuntimeError("secret /srv/piano-core/midi internals leaked")

        self.context.state_store.snapshot = _boom  # type: ignore[method-assign]
        status, payload = self.request("GET", "/api/v1/state")
        self.assertEqual(status, 500)
        self.assertEqual(payload["error"]["code"], "internal_error")
        self.assertNotIn("Traceback", json.dumps(payload))
        self.assertNotIn("secret", json.dumps(payload))

    def test_oversized_body_is_413(self):
        status, payload = self.request(
            "POST", "/api/v1/playback/play", body=b"x" * (MAX_BODY_BYTES + 1)
        )
        self.assertEqual(status, 413)
        self.assertEqual(payload["error"]["code"], "payload_too_large")

    def test_non_json_body_is_415(self):
        status, payload = self.request(
            "POST",
            "/api/v1/playback/play",
            body=b"song_id=x",
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(status, 415)
        self.assertEqual(payload["error"]["code"], "unsupported_media_type")

    def test_invalid_json_is_400(self):
        status, payload = self.request(
            "POST", "/api/v1/playback/play", body=b"{not json"
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "malformed_request")

    def test_json_array_body_is_400(self):
        status, payload = self.request("POST", "/api/v1/playback/play", body=[])
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "malformed_request")


class PlaybackRequestFieldTests(ApiHttpTestCase):
    def setUp(self):
        super().setUp()
        self.context.playback = FakeManager()

    def test_play_unknown_field_is_rejected(self):
        status, payload = self.request(
            "POST",
            "/api/v1/playback/play",
            body={"song_id": "s", "path": "/etc/passwd"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "malformed_request")

    def test_play_requires_song_id(self):
        status, payload = self.request("POST", "/api/v1/playback/play", body={})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_song_id")

    def test_play_rejects_non_string_song_id(self):
        status, payload = self.request(
            "POST", "/api/v1/playback/play", body={"song_id": 5}
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_song_id")

    def test_play_rejects_out_of_bounds_tempo(self):
        for tempo in (0.2, 4.5, "fast", True):
            status, payload = self.request(
                "POST",
                "/api/v1/playback/play",
                body={"song_id": "s", "tempo_multiplier": tempo},
            )
            self.assertEqual(status, 400, f"tempo {tempo!r}")
            self.assertEqual(payload["error"]["code"], "invalid_tempo")

    def test_pause_rejects_non_empty_object(self):
        status, payload = self.request(
            "POST", "/api/v1/playback/pause", body={"force": True}
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "malformed_request")


class HealthShapeTests(ApiHttpTestCase):
    def test_health_ready_reports_ok_when_subsystems_available(self):
        self.context.library_ready = True
        self.context.playback = FakeManager()
        status, payload = self.request("GET", "/api/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["application"]["state"], "READY")
        self.assertEqual(payload["midi_input"]["connection"], "DISCONNECTED")

    def test_health_degraded_without_subsystems(self):
        status, payload = self.request("GET", "/api/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "degraded")
        self.assertFalse(payload["library"]["ready"])
        self.assertFalse(payload["playback"]["available"])

    def test_health_not_ready_is_503(self):
        context = FakeContext()  # stays STARTING
        server = ApiServer(context, "127.0.0.1", 0)
        server.start()
        self.addCleanup(server.shutdown)
        host, port = server.bound_address
        req = urllib.request.Request(f"http://{host}:{port}/api/v1/health")
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                status = response.status
                payload = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            status = exc.code
            payload = json.loads(exc.read().decode())
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"]["code"], "not_ready")


if __name__ == "__main__":
    unittest.main()
