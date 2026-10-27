"""PIANO-PROD-001 tests: the read-only GET /api/v1/device endpoint.

Exact 200 payload, exact 503 envelope, no query parameters, read-only
repeated GETs from cached state, the privacy allowlist, and no internal
reason leakage. A final integration test serves the endpoint from a
real Application with a real persisted identity.
"""

import json
import os
import socket
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from piano_core.api.server import ApiServer
from piano_core.app import Application
from piano_core.config import AppConfig
from piano_core.device_identity import DeviceIdentity, initialize_identity
from piano_core.health import HealthState


def _http_get(base: str, path: str) -> tuple[int, dict, bytes]:
    try:
        with urllib.request.urlopen(base + path, timeout=10) as response:
            raw = response.read()
            return response.status, json.loads(raw.decode()), raw
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, json.loads(raw.decode()), raw


class _IdentityContext:
    """Duck-typed API context carrying only the identity surface."""

    def __init__(self, identity=None, error=None):
        self.health_state = HealthState.READY
        self.device_identity = identity
        self.device_identity_error = error


def _free_context_server(context) -> ApiServer:
    server = ApiServer(context, "127.0.0.1", 0)
    server.start()
    return server


class DeviceEndpointTests(unittest.TestCase):
    def setUp(self):
        self.identity = DeviceIdentity(
            identity_schema=1, device_id="a7f32c91-d2e4-4abc-9def-0123456789ab"
        )

    def _server(self, context) -> str:
        self.server = _free_context_server(context)
        self.addCleanup(self.server.shutdown)
        host, port = self.server.bound_address
        return f"http://{host}:{port}"

    def test_exact_success_payload(self):
        base = self._server(_IdentityContext(identity=self.identity))
        status, payload, _ = _http_get(base, "/api/v1/device")
        self.assertEqual(status, 200)
        self.assertEqual(
            payload,
            {
                "product": "Piano Core",
                "device_id": "a7f32c91-d2e4-4abc-9def-0123456789ab",
                "display_code": "PC-A7F32C91D2E4",
                "identity_schema": 1,
            },
        )

    def test_exact_unavailable_envelope(self):
        base = self._server(_IdentityContext(identity=None, error="identity_missing"))
        status, payload, _ = _http_get(base, "/api/v1/device")
        self.assertEqual(status, 503)
        self.assertEqual(
            payload,
            {
                "error": {
                    "code": "device_identity_unavailable",
                    "message": "device identity is unavailable",
                    "status": 503,
                }
            },
        )

    def test_query_parameters_rejected(self):
        base = self._server(_IdentityContext(identity=self.identity))
        status, payload, _ = _http_get(base, "/api/v1/device?verbose=1")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_parameter")

    def test_repeated_gets_are_identical(self):
        base = self._server(_IdentityContext(identity=self.identity))
        first = _http_get(base, "/api/v1/device")
        second = _http_get(base, "/api/v1/device")
        self.assertEqual(first, second)
        self.assertEqual(first[0], 200)

    def test_repeated_gets_are_read_only_cached(self):
        """No filesystem access happens while serving the endpoint."""
        real_open = os.open
        real_stat = os.stat
        real_listdir = os.listdir

        def deny(name, *args, **kwargs):  # noqa: ARG001
            raise AssertionError(f"filesystem access during request: {name}")

        base = self._server(_IdentityContext(identity=self.identity))
        with mock.patch("os.open", side_effect=deny), mock.patch(
            "os.stat", side_effect=deny
        ), mock.patch("os.listdir", side_effect=deny):
            status, _, _ = _http_get(base, "/api/v1/device")
            status2, _, _ = _http_get(base, "/api/v1/device")
        self.assertEqual(status, 200)
        self.assertEqual(status2, 200)
        self.assertIs(os.open, real_open)
        self.assertIs(os.stat, real_stat)
        self.assertIs(os.listdir, real_listdir)

    def test_privacy_allowlist_exact_fields_only(self):
        base = self._server(_IdentityContext(identity=self.identity))
        status, payload, raw = _http_get(base, "/api/v1/device")
        self.assertEqual(status, 200)
        self.assertEqual(
            set(payload),
            {"product", "device_id", "display_code", "identity_schema"},
        )
        hostname = socket.gethostname().encode()
        self.assertNotIn(hostname, raw)

    def test_no_internal_reason_leakage(self):
        for reason in (
            "identity_unsafe_storage",
            "identity_invalid_record",
            "identity_directory_missing",
            "identity_storage_unavailable",
        ):
            base = self._server(
                _IdentityContext(identity=None, error=reason)
            )
            status, payload, raw = _http_get(base, "/api/v1/device")
            self.assertEqual(status, 503)
            self.assertNotIn(reason.encode(), raw)
            self.assertNotIn(b"unsafe", raw)
            self.server.shutdown()
            self.server = None


class DeviceEndpointIntegrationTests(unittest.TestCase):
    def test_real_application_serves_initialized_identity(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        directory = str(Path(tmp.name) / "identity")
        os.mkdir(directory)
        os.chmod(directory, 0o700)
        expected = initialize_identity(directory)

        app = Application(
            AppConfig(device_identity_dir=directory, api_enabled=True, api_port=0)
        )
        app.start()
        self.addCleanup(app.shutdown)
        self.assertIs(app.health.state, HealthState.READY)
        host, port = app.api_server.bound_address
        status, payload, _ = _http_get(
            f"http://{host}:{port}", "/api/v1/device"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["device_id"], expected.device_id)
        self.assertEqual(payload["display_code"], expected.display_code)

    def test_unconfigured_application_serves_503(self):
        app = Application(AppConfig(api_enabled=True, api_port=0))
        app.start()
        self.addCleanup(app.shutdown)
        host, port = app.api_server.bound_address
        status, payload, _ = _http_get(
            f"http://{host}:{port}", "/api/v1/device"
        )
        self.assertEqual(status, 503)
        self.assertEqual(
            payload["error"]["code"], "device_identity_unavailable"
        )
        # Health is untouched by the unconfigured identity feature.
        status, payload, _ = _http_get(
            f"http://{host}:{port}", "/api/v1/health"
        )
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
