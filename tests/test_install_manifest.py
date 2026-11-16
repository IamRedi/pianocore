"""Installation identity and navigation must survive a manifest update."""
import json
import unittest
from urllib.parse import urljoin, urlparse
from piano_core.web import UI_ASSETS, read_ui_asset


class InstallManifestTests(unittest.TestCase):
    def test_legacy_implicit_identity_survives_the_new_start_route(self):
        manifest = json.loads(read_ui_asset("manifest.webmanifest"))
        origin = "https://piano.example"
        # The previous manifest omitted id and started at '/', its implicit id.
        self.assertEqual(urljoin(origin, manifest["id"]), urljoin(origin, "/"))
        start = urlparse(urljoin(origin, manifest["start_url"]))
        self.assertEqual(start.path, "/ui/")
        self.assertEqual(start.fragment, "/home")
        self.assertTrue(start.path.startswith(manifest["scope"]))
        self.assertEqual(start.netloc, "piano.example")

    def test_install_icons_are_packaged_at_the_declared_sizes(self):
        import struct
        manifest = json.loads(read_ui_asset("manifest.webmanifest"))
        purposes = set()
        sizes = set()
        for icon in manifest["icons"]:
            name = icon["src"].removeprefix("/ui/")
            self.assertIn(name, UI_ASSETS)
            data = read_ui_asset(name)
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
            width, height = struct.unpack(">II", data[16:24])
            self.assertEqual(icon["sizes"], f"{width}x{height}")
            sizes.add(width)
            purposes.add(icon.get("purpose", "any"))
        self.assertTrue({192, 512}.issubset(sizes))
        self.assertIn("maskable", purposes)
