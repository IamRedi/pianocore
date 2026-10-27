"""PIANO-P014: /api/v1/led endpoint tests (real server, real sockets).

Uses the same harness shape as the P012 endpoint tests: real ApiServer,
real canonical subsystems, a real LedKeyboardAdapter on a fake transport
(no hardware).
"""

import json
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from piano_core.api.server import ApiServer
from piano_core.health import HealthState
from piano_core.led.adapter import LedKeyboardAdapter
from piano_core.led.integration import LedIntegration
from piano_core.led.model import LedMode
from piano_core.state import StateStore
from tests.test_led_adapter import FakeTransport


def _request(method: str, url: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


class _Context:
    def __init__(self, led=None):
        self.health_state = HealthState.READY
        self.state_store = StateStore()
        self.led = led


class _Harness:
    """Live API server on an ephemeral port."""

    def __init__(self, context):
        self.server = ApiServer(context, "127.0.0.1", 0)
        self.server.start()
        host, port = self.server.bound_address
        self.base = f"http://{host}:{port}"
        self.addCleanup = None

    def close(self):
        self.server.shutdown()


class LedEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.transport = FakeTransport()
        self.adapter = LedKeyboardAdapter(
            "127.0.0.1",
            8787,
            enabled=True,
            mode=LedMode.ALL,
            transport_factory=lambda host, port: self.transport,
        )
        self.adapter.start()
        self.addCleanup(self.adapter.stop)

    def _server(self, led_integration=None) -> str:
        harness = _Harness(_Context(led=led_integration))
        self.addCleanup(harness.close)
        return harness.base

    def test_get_led_without_subsystem(self):
        base = self._server(None)
        status, payload = _request("GET", f"{base}/api/v1/led")
        self.assertEqual(status, 200)
        self.assertFalse(payload["available"])
        self.assertFalse(payload["enabled"])
        self.assertIsNone(payload["mode"])

    def test_get_led_snapshot(self):
        base = self._server(LedIntegration(self.adapter))
        status, payload = _request("GET", f"{base}/api/v1/led")
        self.assertEqual(status, 200)
        self.assertTrue(payload["enabled"])
        self.assertTrue(payload["available"])
        self.assertEqual(payload["mode"], "all")
        self.assertEqual(payload["brightness"], 100)
        self.assertEqual(payload["connection"], "LISTENING")
        self.assertIn("transport", payload)

    def test_put_updates_mode_and_brightness(self):
        base = self._server(LedIntegration(self.adapter))
        status, payload = _request(
            "PUT", f"{base}/api/v1/led", {"mode": "playback", "brightness": 40}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["mode"], "playback")
        self.assertEqual(payload["brightness"], 40)
        status, payload = _request(
            "PUT", f"{base}/api/v1/led", {"brightness": 0}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["brightness"], 0)

    def test_put_enable_disable(self):
        base = self._server(LedIntegration(self.adapter))
        status, payload = _request(
            "PUT", f"{base}/api/v1/led", {"enabled": False}
        )
        self.assertEqual(status, 200)
        self.assertFalse(payload["enabled"])
        status, payload = _request("PUT", f"{base}/api/v1/led", {"enabled": True})
        self.assertEqual(status, 200)
        self.assertTrue(payload["enabled"])

    def test_put_invalid_values_rejected(self):
        base = self._server(LedIntegration(self.adapter))
        for body, expected_code in (
            ({"mode": "disco"}, "invalid_parameter"),
            ({"brightness": 101}, "invalid_parameter"),
            ({"brightness": -1}, "invalid_parameter"),
            ({"brightness": True}, "invalid_parameter"),
            ({"brightness": "50"}, "invalid_parameter"),
            ({"enabled": "yes"}, "invalid_parameter"),
            ({"unknown_field": 1}, "malformed_request"),
        ):
            status, payload = _request("PUT", f"{base}/api/v1/led", body)
            self.assertEqual(status, 400, body)
            self.assertEqual(payload["error"]["code"], expected_code, body)
        # Bad requests must not have mutated the subsystem.
        snapshot = self.adapter.snapshot()
        self.assertIs(snapshot.mode, LedMode.ALL)
        self.assertEqual(snapshot.brightness, 100)

    def test_put_without_subsystem_is_503(self):
        base = self._server(None)
        status, payload = _request("PUT", f"{base}/api/v1/led", {"mode": "off"})
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"]["code"], "led_unavailable")

    def test_not_configured_reports_configured_false(self):
        """P014-HF1: 'not configured' is distinguishable from 'disabled'."""
        base = self._server(None)
        status, payload = _request("GET", f"{base}/api/v1/led")
        self.assertEqual(status, 200)
        self.assertFalse(payload["configured"])
        self.assertFalse(payload["enabled"])
        self.assertIsNone(payload["mode"])

    def test_disable_unavailable_reenable_cycle(self):
        """P014-HF1 regression: disabling from the UI must never make the
        subsystem unreachable. Disable -> still configured (toggle stays
        offered) -> re-enable via the SAME API -> transport reopens ->
        ESP32 client reconnects -> CONNECTED."""
        base = self._server(LedIntegration(self.adapter))
        self.transport.connected = True
        self.transport.on_connect()
        time.sleep(0.05)

        # 1. Disable from the "UI" (same PUT the toggle issues).
        status, payload = _request(
            "PUT", f"{base}/api/v1/led", {"enabled": False}
        )
        self.assertEqual(status, 200)
        self.assertFalse(payload["enabled"])
        self.assertFalse(payload["available"])
        # The subsystem still EXISTS: configured=true, mode retained.
        self.assertTrue(payload["configured"])
        self.assertEqual(payload["mode"], "all")
        self.assertEqual(payload["brightness"], 100)

        # 2. GET keeps reporting a configured-but-disabled subsystem.
        status, payload = _request("GET", f"{base}/api/v1/led")
        self.assertEqual(status, 200)
        self.assertTrue(payload["configured"])
        self.assertFalse(payload["enabled"])

        # 3. Re-enable through the SAME control: transport must reopen.
        status, payload = _request(
            "PUT", f"{base}/api/v1/led", {"enabled": True}
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["enabled"])
        self.assertTrue(payload["available"])
        self.assertEqual(payload["connection"], "LISTENING")
        self.assertTrue(self.transport.started)

        # 4. The ESP32's firmware reconnects within seconds: simulate it.
        self.transport.connected = True
        self.transport.on_connect()
        time.sleep(0.1)
        status, payload = _request("GET", f"{base}/api/v1/led")
        self.assertEqual(status, 200)
        self.assertEqual(payload["connection"], "CONNECTED")
        self.assertTrue(payload["available"])

    def test_post_clear(self):
        base = self._server(LedIntegration(self.adapter))
        self.transport.connected = True
        self.transport.on_connect()
        time.sleep(0.05)
        self.transport.frames.clear()
        status, payload = _request("POST", f"{base}/api/v1/led/clear", {})
        self.assertEqual(status, 200)
        self.assertEqual(payload["active_note_count"], 0)
        self._drain()
        self.assertIn(
            '{"type":"playback","status":"all_notes_off"}', self.transport.frames
        )

    def test_post_clear_without_subsystem_is_503(self):
        base = self._server(None)
        status, payload = _request("POST", f"{base}/api/v1/led/clear", {})
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"]["code"], "led_unavailable")

    def test_led_routes_bounded_methods(self):
        base = self._server(LedIntegration(self.adapter))
        status, payload = _request("DELETE", f"{base}/api/v1/led")
        self.assertEqual(status, 405)
        status, payload = _request("GET", f"{base}/api/v1/led/unknown")
        self.assertEqual(status, 404)

    def _drain(self):
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if self.adapter._queue.empty():
                return
            time.sleep(0.01)


if __name__ == "__main__":
    unittest.main()
