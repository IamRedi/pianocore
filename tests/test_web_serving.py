"""V2/V3 tests: packaged UI static serving (PIANO-P013).

Server-level behavior on real loopback sockets: allowlisted asset
serving with correct content types, deterministic 404s, traversal
rejection, no-cache headers, unchanged API routing, and the
``serve_ui`` disable semantics. No arbitrary filesystem access can
exist — the allowlist in ``piano_core.web`` is the only path source.
"""

import json
import unittest
import urllib.error
import urllib.request

from piano_core.api.server import ApiServer
from piano_core.health import HealthState
from piano_core.web import UI_ASSETS, read_ui_asset, resolve_ui_asset, resolve_ui_path
from tests.test_api_http import FakeContext


def _make_server(serve_ui: bool = True):
    context = FakeContext()
    context.health.transition(HealthState.READY)
    server = ApiServer(context, "127.0.0.1", 0, serve_ui=serve_ui)
    return server


class UiServingTestCase(unittest.TestCase):
    """Shared harness: one live server on an ephemeral loopback port."""

    SERVE_UI = True

    def setUp(self):
        self.server = _make_server(serve_ui=self.SERVE_UI)
        self.server.start()
        self.addCleanup(self.server.shutdown)
        host, port = self.server.bound_address
        self.base = f"http://{host}:{port}"

    def request(self, method, path):
        """Issue one request; returns (status, headers, raw bytes)."""
        req = urllib.request.Request(self.base + path, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()


class DocumentServingTests(UiServingTestCase):
    def test_root_serves_ui_document(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        self.assertIn(b"<!DOCTYPE html>", body)
        self.assertIn(b"Piano", body)

    def test_ui_paths_serve_the_document(self):
        for path in ("/ui", "/ui/", "/ui/index.html"):
            status, headers, body = self.request("GET", path)
            self.assertEqual(status, 200, path)
            self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")

    def test_document_has_no_cache_header(self):
        for path in ("/", "/ui/app.js", "/ui/styles.css"):
            _, headers, _ = self.request("GET", path)
            self.assertEqual(headers.get("Cache-Control"), "no-cache", path)

    def test_content_length_is_exact(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertEqual(int(headers["Content-Length"]), len(body))


class AssetServingTests(UiServingTestCase):
    def test_all_allowlisted_assets_are_served(self):
        for name, content_type in UI_ASSETS.items():
            with self.subTest(asset=name):
                status, headers, body = self.request("GET", f"/ui/{name}")
                self.assertEqual(status, 200)
                self.assertEqual(headers["Content-Type"], content_type)
                self.assertGreater(len(body), 0)

    def test_packaged_bytes_equal_served_bytes(self):
        for name in UI_ASSETS:
            with self.subTest(asset=name):
                _, _, body = self.request("GET", f"/ui/{name}")
                self.assertEqual(body, read_ui_asset(name))


class RejectTests(UiServingTestCase):
    def _assert_not_found(self, path):
        status, headers, body = self.request("GET", path)
        self.assertEqual(status, 404, path)
        payload = json.loads(body.decode())
        self.assertEqual(payload["error"]["code"], "not_found")

    def test_unknown_asset_is_deterministic_404(self):
        self._assert_not_found("/ui/missing.js")
        self._assert_not_found("/ui/secret.txt")
        self._assert_not_found("/ui/sub/dir/app.js")

    def test_traversal_attempts_rejected(self):
        for path in (
            "/ui/../config.py",
            "/ui/..%2fconfig.py",
            "/ui/%2e%2e/config.py",
            "/ui/%2e%2e%2fconfig.py",
            "/ui/....//config.py",
            "/ui/app.js/../../state.py",
            "/ui/static/__init__.py",
        ):
            with self.subTest(path=path):
                self._assert_not_found(path)

    def test_non_ui_paths_still_404_via_api_error_model(self):
        self._assert_not_found("/nope")
        self._assert_not_found("/health")

    def test_wrong_method_on_ui_path_is_405(self):
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            with self.subTest(method=method):
                status, _, body = self.request(method, "/")
                self.assertEqual(status, 405)
                payload = json.loads(body.decode())
                self.assertEqual(payload["error"]["code"], "method_not_allowed")
        status, _, _ = self.request("HEAD", "/")
        self.assertNotEqual(status, 200)

    def test_api_routes_unchanged_behind_ui_serving(self):
        status, headers, body = self.request("GET", "/api/v1/health")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        payload = json.loads(body.decode())
        self.assertIn("status", payload)
        status, _, body = self.request("GET", "/api/v1/nope")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body.decode())["error"]["code"], "not_found")


class UiDisabledTests(UiServingTestCase):
    SERVE_UI = False

    def test_root_is_404_when_ui_disabled(self):
        status, _, body = self.request("GET", "/")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body.decode())["error"]["code"], "not_found")

    def test_ui_assets_are_404_when_ui_disabled(self):
        for name in UI_ASSETS:
            with self.subTest(asset=name):
                status, _, _ = self.request("GET", f"/ui/{name}")
                self.assertEqual(status, 404)

    def test_api_still_served_when_ui_disabled(self):
        status, _, _ = self.request("GET", "/api/v1/health")
        self.assertEqual(status, 200)


class ResolverUnitTests(unittest.TestCase):
    """Pure allowlist resolution (no server, no sockets)."""

    def test_document_paths_resolve_to_index(self):
        for path in ("/", "/ui", "/ui/", "/ui/index.html"):
            self.assertEqual(resolve_ui_path(path), "index.html", path)

    def test_encoded_asset_name_resolves(self):
        self.assertEqual(resolve_ui_path("/ui/app%2Ejs"), "app.js")

    def test_encoded_traversal_does_not_resolve(self):
        for path in (
            "/ui/%2e%2e/config.py",
            "/ui/..%2Fconfig.py",
            "/ui/app.js%00.txt",
            "/ui//etc/passwd",
            "/ui/../web/__init__.py",
        ):
            self.assertIsNone(resolve_ui_path(path), path)

    def test_resolve_ui_asset_returns_content_type(self):
        self.assertEqual(
            resolve_ui_asset("/ui/styles.css"),
            ("styles.css", "text/css; charset=utf-8"),
        )
        self.assertIsNone(resolve_ui_asset("/ui/../x"))

    def test_read_ui_asset_fails_closed_on_non_allowlisted_name(self):
        with self.assertRaises(ValueError):
            read_ui_asset("../../../etc/passwd")
        with self.assertRaises(ValueError):
            read_ui_asset("index.html\x00.txt")


if __name__ == "__main__":
    unittest.main()
