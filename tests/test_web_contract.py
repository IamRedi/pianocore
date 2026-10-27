"""V2 tests: frontend static contract and security analysis (PIANO-P013).

Durable contract checks over the packaged UI assets — the browser code
must stay a same-origin client of /api/v1 with no external assets, no
eval, no unsafe HTML construction, no hardcoded hosts, bounded search,
and canonical tempo bounds. These are invariant checks, not text
snapshots.
"""

import unittest
from pathlib import Path

from piano_core.web import UI_ASSETS

_STATIC = Path(__file__).resolve().parent.parent / "src" / "piano_core" / "web" / "static"
_TEXT_ASSETS = {
    name
    for name, content_type in UI_ASSETS.items()
    if content_type.startswith("text/")
    or "javascript" in content_type
    or "json" in content_type
    or "svg" in content_type
}


def _asset_text(name: str) -> str:
    return (_STATIC / name).read_text(encoding="utf-8")


def _app_js() -> str:
    return _asset_text("app.js")


class AssetInventoryTests(unittest.TestCase):
    def test_allowlist_matches_files_on_disk(self):
        # PIANO-UI-007: the inventory is recursive — vendored renderer
        # files live in static/vendor/ and are allowlisted with their
        # package-relative path (never a directory listing).
        on_disk = {
            p.relative_to(_STATIC).as_posix()
            for p in _STATIC.rglob("*")
            if p.is_file()
        }
        self.assertEqual(set(UI_ASSETS), on_disk)

    def test_index_references_only_packaged_assets(self):
        html = _asset_text("index.html")
        for ref in ("/ui/app.js", "/ui/styles.css", "/ui/favicon.svg",
                    "/ui/manifest.webmanifest"):
            self.assertIn(ref, html)

    def test_index_has_no_inline_script_or_handler(self):
        html = _asset_text("index.html")
        self.assertNotIn("<script>", html.replace(
            '<script type="module" src="/ui/app.js"></script>', ""))
        for attr in ("onclick=", "onload=", "onerror=", "javascript:"):
            self.assertNotIn(attr, html.lower())


class SameOriginContractTests(unittest.TestCase):
    def test_api_access_is_relative_and_versioned(self):
        js = _app_js()
        self.assertIn('const API_BASE = "/api/v1"', js)
        self.assertNotIn("http://localhost", js)
        self.assertNotIn("127.0.0.1", js)

    def test_no_hardcoded_production_host(self):
        for name in _TEXT_ASSETS:
            self.assertNotIn("192.168.", _asset_text(name), name)

    def test_no_external_urls_or_cdn(self):
        for name in ("app.js", "index.html", "styles.css", "manifest.webmanifest"):
            text = _asset_text(name)
            for marker in ("http://", "https://", "//cdn.", "url(http"):
                self.assertNotIn(marker, text.lower(), f"{name}: {marker}")

    def test_single_fetch_boundary(self):
        """All HTTP access flows through the ApiClient (exactly one fetch)."""
        js = _app_js()
        self.assertEqual(js.count("fetch("), 1)
        self.assertIn("this._fetch = fetchImpl || globalThis.fetch.bind(globalThis)", js)


class SecurityContractTests(unittest.TestCase):
    def test_no_eval_or_dynamic_code(self):
        js = _app_js()
        for marker in ("eval(", "new Function(", "Function(", "setTimeout(\"",
                       "setInterval(\"", "import("):
            self.assertNotIn(marker, js)

    def test_no_unsafe_html_construction(self):
        js = _app_js()
        for marker in ("innerHTML", "outerHTML", "insertAdjacentHTML",
                       "document.write", "document.writeln"):
            self.assertNotIn(marker, js)
        # Server-derived text must flow through textContent.
        self.assertIn("textContent", js)

    def test_no_dynamic_script_injection(self):
        js = _app_js()
        self.assertNotIn("createElement(\"script\")", js)
        self.assertNotIn("createElement('script')", js)

    def test_panic_not_referenced(self):
        for name in ("app.js", "index.html", "styles.css"):
            self.assertNotIn("panic", _asset_text(name).lower(), name)


class BoundedBehaviorContractTests(unittest.TestCase):
    def test_search_page_size_is_bounded(self):
        js = _app_js()
        self.assertIn("const PAGE_SIZE = ", js)
        page_size = int(js.split("const PAGE_SIZE = ")[1].split(";")[0])
        self.assertGreaterEqual(page_size, 1)
        self.assertLessEqual(page_size, 100)

    def test_search_url_clamps_limit(self):
        js = _app_js()
        self.assertIn("Math.min(100", js)

    def test_tempo_bounds_match_canonical_range(self):
        js = _app_js()
        self.assertIn("export const TEMPO_MIN = 0.25;", js)
        self.assertIn("export const TEMPO_MAX = 4.0;", js)

    def test_play_sends_song_id_only(self):
        js = _app_js()
        self.assertIn("song_id: songId", js)
        self.assertNotIn("path", js.split("play(songId")[1].split("}")[0])


class ProfessionalPlayerContractTests(unittest.TestCase):
    """PIANO-UI-002: presentation remains a bounded Core API client."""

    def test_timeline_is_read_only_and_accessible(self):
        html = _asset_text("index.html")
        self.assertIn('id="player-progress"', html)
        self.assertIn('role="progressbar"', html)
        self.assertIn('aria-readonly="true"', html)
        self.assertNotIn('id="seek"', html)
        self.assertNotIn('type="range" id="player-progress"', html)

    def test_visual_clock_is_bounded_and_never_a_scheduler(self):
        js = _app_js()
        self.assertIn("export const PLAYBACK_FRESHNESS_MS = 3500;", js)
        self.assertIn("requestAnimationFrame(tickVisualClock)", js)
        self.assertIn("const POLL_INTERVAL_MS = 1000;", js)
        self.assertNotIn("setInterval(tickVisualClock", js)
        for marker in ("MIDIOutput", "requestMIDIAccess", "send([", "performance.now() +"):
            self.assertNotIn(marker, js)

    def test_playback_authority_and_metadata_rendering_are_separate(self):
        js = _app_js()
        hydration = js.split("async function hydrateActiveSong", 1)[1].split(
            "function playerStateLabel", 1
        )[0]
        self.assertIn("renderPlaybackMetadata()", hydration)
        self.assertNotIn("renderPlayback()", hydration)
        self.assertIn("isPollEligible(ticket)", js)
        self.assertIn('status: this.isPollEligible(ticket) ? "failed" : "stale"', js)
        self.assertIn("frozenPlaybackFrame", js)

    def test_unsupported_advanced_controls_are_not_rendered(self):
        html = _asset_text("index.html")
        for control_id in (
            "seek", "loop-a", "loop-b", "hand-selection", "track-selection",
            "mute", "solo", "metronome", "count-in", "bookmark", "sections",
        ):
            self.assertNotIn(f'id="{control_id}"', html)

    def test_touch_and_keyboard_contract(self):
        html = _asset_text("index.html")
        css = _asset_text("styles.css")
        js = _app_js()
        self.assertIn('aria-keyshortcuts="Space"', html)
        self.assertIn('aria-keyshortcuts="Escape"', html)
        self.assertIn("min-height: 64px", css)
        self.assertIn("transportShortcutAction(event", js)
        self.assertIn('event.code !== "Space"', js)
        self.assertIn('event.code === "Escape"', js)
        self.assertIn("summary", js)


if __name__ == "__main__":
    unittest.main()

class LedUiContractTests(unittest.TestCase):
    """PIANO-P014: the LED section stays a bounded API client."""

    def test_led_section_present_and_bounded(self):
        html = _asset_text("index.html")
        self.assertIn('id="led-heading"', html)
        self.assertIn('id="led-mode"', html)
        # Mode choices are exactly the canonical bounded set.
        for mode in ("off", "playback", "physical", "all"):
            self.assertIn(f'value="{mode}"', html)

    def test_led_api_access_is_bounded(self):
        js = _app_js()
        for route in ("${API_BASE}/led", "${API_BASE}/led/clear"):
            self.assertIn(route, js)
        # No LED sub-routes exist beyond the bounded clear diagnostic.
        self.assertNotIn("${API_BASE}/led/", js.replace("${API_BASE}/led/clear", ""))
        # No raw LED index or protocol surface is ever exposed.
        self.assertNotIn("note_on_frame", js)
        self.assertNotIn('"type":"midi"', js)
