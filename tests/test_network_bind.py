"""PIANO-P015-HF2: network-independent bind and honest degraded startup.

Regression coverage for the cold-boot production defect: the appliance
bound the API (:8788) and the LED endpoint (:8787) to one specific
DHCP-assigned address, so a boot that raced the network raised
``[Errno 99] Cannot assign requested address`` and the auxiliary degrade
contract swallowed it — the application reached a fully healthy READY
with no control surface until a manual restart.

The HF2 architecture: production binds ``0.0.0.0`` (INADDR_ANY —
succeeds before any interface address exists and follows DHCP changes
without reconfiguration), and an ENABLED-but-failed API now yields an
honestly ``DEGRADED`` lifecycle instead of a misleading READY.
"""

import json
import unittest
import urllib.request
from pathlib import Path

from piano_core.api.server import ApiServer
from piano_core.app import Application
from piano_core.health import HealthState
from piano_core.led.transport import LedTransport


class _MinimalContext:
    """The attributes the /api/v1/health handler touches."""

    def __init__(self):
        from piano_core.health import Health, HealthState
        from piano_core.state import StateStore

        self.health = Health()
        self.health.transition(HealthState.READY)
        self.state_store = StateStore()
        self.library = None
        self.library_ready = False
        self.playback = None
        self.practice = None


class ApiServerAllInterfacesTests(unittest.TestCase):
    def test_api_server_binds_0_0_0_0_and_serves_on_loopback(self):
        """INADDR_ANY binds without any specific interface address and is
        reachable locally — the property that makes cold boot safe."""
        context = _MinimalContext()
        server = ApiServer(context, "0.0.0.0", 0)
        server.start()
        self.addCleanup(server.shutdown)
        host, port = server.bound_address
        self.assertEqual(host, "0.0.0.0")
        self.assertGreater(port, 0)
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/v1/health", timeout=5
        ) as response:
            payload = json.loads(response.read())
        self.assertEqual(payload["application"]["state"], "READY")

    def test_led_transport_binds_0_0_0_0(self):
        """The LED endpoint is network-independent the same way."""
        transport = LedTransport("0.0.0.0", 0)
        transport.start()
        self.addCleanup(transport.stop)
        host, port = transport.bound_address
        self.assertEqual(host, "0.0.0.0")
        self.assertGreater(port, 0)


class DegradedStartupTests(unittest.TestCase):
    """An ENABLED API that irrecoverably fails to start must not be
    reported as a fully healthy appliance (HF2 honesty semantics)."""

    UNBINDABLE_HOST = "203.0.113.1"  # TEST-NET-3: never assigned locally

    def _env(self):
        return {
            "PIANO_CORE_MODE": "test",
            "PIANO_CORE_API_ENABLED": "true",
            "PIANO_CORE_API_HOST": self.UNBINDABLE_HOST,
            "PIANO_CORE_API_PORT": "8788",
        }

    def test_unbindable_api_host_yields_degraded_not_ready(self):
        app = Application.from_env(self._env())
        self.addCleanup(app.request_stop, "test end")
        app.start()  # must NOT raise: the failure is honest, not fatal
        self.assertIsNone(app.api_server)
        self.assertIs(app.health.state, HealthState.DEGRADED)

    def test_degraded_application_shuts_down_cleanly(self):
        app = Application.from_env(self._env())
        app.start()
        self.assertIs(app.health.state, HealthState.DEGRADED)
        app.request_stop("test")
        self.assertIs(app.health.state, HealthState.STOPPING)
        app.shutdown()
        self.assertIs(app.health.state, HealthState.STOPPED)


class NoHardcodedLanAddressTests(unittest.TestCase):
    def test_production_sources_contain_no_hardcoded_lan_address(self):
        """No production default or code may depend on the appliance's
        home LAN address (HF2 portability direction)."""
        src = Path(__file__).resolve().parent.parent / "src"
        offenders = [
            str(path)
            for path in sorted(src.rglob("*"))
            if path.is_file()
            and "192.168." in path.read_text(encoding="utf-8", errors="replace")
        ]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
