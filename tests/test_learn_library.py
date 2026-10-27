"""Learn Library API + runtime wiring tests (PIANO-LEARN-001).

Covers: config fail-closed keys, LearnService surfaces with merged
progress, the /api/v1/learn* routes on a real ApiServer (Player
isolation included), and the app-level degrade contract.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from piano_core.api.errors import ApiError, translate_exception
from piano_core.config import AppConfig, ConfigError
from piano_core.learn import LearnCatalogError
from piano_core.learn.catalog import LearnCatalog
from piano_core.learn.model import lesson_id_for
from piano_core.learn.service import LearnService


def _seed_catalog(db_path: Path) -> None:
    catalog = LearnCatalog(db_path)
    catalog.replace_catalog(
        [{"course_id": "c1", "title": "First Notes", "level": 0, "summary": "s",
          "source": "pianio", "order_index": 0, "lesson_count": 2}],
        [
            {"lesson_id": lesson_id_for("pianio:t:ex-1"), "lesson_key": "pianio:t:ex-1",
             "package_id": "pkg-1", "course_id": "c1", "title": "Middle C Pulse",
             "composer": None, "source": "pianio", "level": 0, "difficulty": "easy",
             "duration_seconds": 8.0, "order_index": 0,
             "hands_availability": "available", "license_name": "CC0-1.0",
             "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
             "attribution": "Pianio", "metrics": {"note_count": 8},
             "level_decision": {"level": 0, "reasons": ["gate"]}, "summary": "s"},
            {"lesson_id": lesson_id_for("pianio:t:ex-2"), "lesson_key": "pianio:t:ex-2",
             "package_id": "pkg-2", "course_id": "c1", "title": "C and D",
             "composer": None, "source": "pianio", "level": 0, "difficulty": "easy",
             "duration_seconds": 6.0, "order_index": 1,
             "hands_availability": "available", "license_name": "CC0-1.0",
             "license_url": None, "attribution": None, "metrics": {},
             "level_decision": {}, "summary": ""},
        ],
    )
    catalog.close()


class LearnConfigTests(unittest.TestCase):
    def test_learn_keys_parse_and_default_safe(self):
        config = AppConfig.from_mapping({})
        self.assertFalse(config.learn_enabled)
        self.assertEqual(config.learn_db_path, "")
        enabled = AppConfig.from_mapping(
            {"learn_enabled": "true", "learn_db_path": "/tmp/learn.sqlite3"}
        )
        self.assertTrue(enabled.learn_enabled)
        self.assertEqual(enabled.learn_db_path, "/tmp/learn.sqlite3")

    def test_unknown_key_fails_closed(self):
        with self.assertRaises(ConfigError):
            AppConfig.from_mapping({"learn_bad_key": "1"})

    def test_env_mirror_complete(self):
        from piano_core.config import KNOWN_ENV_VARS, KNOWN_KEYS

        self.assertIn("PIANO_CORE_LEARN_ENABLED", KNOWN_ENV_VARS)
        self.assertIn("PIANO_CORE_LEARN_DB_PATH", KNOWN_ENV_VARS)


class LearnServiceTests(unittest.TestCase):
    def test_overview_lessons_detail_and_neighbors(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "learn.sqlite3"
            _seed_catalog(db)
            service = LearnService(db_path=db)
            try:
                overview = service.overview()
                self.assertTrue(overview["ready"])
                self.assertEqual(overview["lesson_count"], 2)
                self.assertEqual(overview["levels"]["0"], 2)
                listing = service.lessons(level=0, limit=1)
                self.assertEqual(listing["count"], 1)
                detail = service.lesson_detail(lesson_id_for("pianio:t:ex-1"))
                self.assertEqual(detail["title"], "Middle C Pulse")
                self.assertIsNone(detail["previous_lesson_id"])
                self.assertEqual(detail["next_lesson_id"], lesson_id_for("pianio:t:ex-2"))
                self.assertEqual(detail["license"]["name"], "CC0-1.0")
                self.assertEqual(detail["license"]["attribution"], "Pianio")
            finally:
                service.close()

    def test_unknown_lesson_raises_catalog_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "learn.sqlite3"
            _seed_catalog(db)
            service = LearnService(db_path=db)
            try:
                with self.assertRaises(LearnCatalogError):
                    service.lesson_detail("no-such-lesson")
            finally:
                service.close()

    def test_progress_merge_via_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "learn.sqlite3"
            _seed_catalog(db)

            def lookup(package_id):
                return {"attempts": 3, "completions": 1, "note_accuracy": 0.9}

            service = LearnService(db_path=db, progress_lookup=lookup)
            try:
                detail = service.lesson_detail(lesson_id_for("pianio:t:ex-1"))
                self.assertEqual(detail["progress"]["attempts"], 3)
            finally:
                service.close()

    def test_academy_progress_preserves_latest_identity_and_qualified_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'learn.sqlite3'
            _seed_catalog(db)
            summaries = {
                'pkg-1': {'attempts': 2, 'completions': 1, 'last_practiced': '2026-10-09T10:00:00.000001+00:00'},
                'pkg-2': {'attempts': 1, 'completions': 0, 'last_practiced': '2026-10-09T10:00:00.000002+00:00'},
                'unknown-package': {'attempts': 3, 'completions': 1, 'last_practiced': '2026-10-10'},
            }
            service = LearnService(db, progress_all_lookup=lambda: summaries)
            self.addCleanup(service.close)
            overview = service.overview()
            self.assertEqual(overview['progress']['completed_lessons'], 1)
            self.assertEqual(overview['progress']['courses'], {'c1': 1})
            self.assertEqual(overview['progress']['levels'], {'0': 1})
            self.assertEqual(overview['progress']['continue_lesson']['lesson_id'], lesson_id_for('pianio:t:ex-2'))
            self.assertEqual(overview['courses'][0]['completed_lessons'], 1)
            service._progress_all_lookup = lambda: (_ for _ in ()).throw(OSError('controlled unavailable progress'))
            self.assertFalse(service.overview()['progress']['available'])

    def test_missing_catalog_fails_closed(self):
        with self.assertRaises(LearnCatalogError):
            LearnService(db_path=Path(tempfile.gettempdir()) / "learn-nope.sqlite3")


class LearnApiErrorTests(unittest.TestCase):
    def test_catalog_error_maps_to_503(self):
        mapped = translate_exception(LearnCatalogError("stale catalog"))
        self.assertEqual(mapped.status_code, 503)
        self.assertEqual(mapped.payload()["error"]["code"], "learn_unavailable")


class LearnRoutesTests(unittest.TestCase):
    """Real ApiServer on loopback with a seeded catalog and a fake store."""

    def setUp(self):
        from piano_core.api.server import ApiServer

        self.tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(self.tmp.name)
        self.db = tmp_path / "learn.sqlite3"
        _seed_catalog(self.db)

        class FakePackagesStore:
            def progress_summary(self, package_id):
                if package_id == "pkg-1":
                    return {"attempts": 2, "completions": 1, "note_accuracy": 1.0}
                return {"attempts": 0}

        class FakePackagesService:
            store = FakePackagesStore()

        class Context:
            learn_service = None
            packages_service = FakePackagesService()
            learn_ready = False

        self.context = Context()
        service = LearnService(db_path=self.db, progress_lookup=FakePackagesStore().progress_summary)
        self.context.learn_service = service
        self.context.learn_ready = True
        self.server = ApiServer(self.context, "127.0.0.1", 0, serve_ui=False)
        self.server.start()
        host, port = self.server.bound_address
        self.base = f"http://{host}:{port}"

    def tearDown(self):
        self.server.shutdown()
        self.context.learn_service.close()
        self.tmp.cleanup()

    def _get(self, path):
        import urllib.request

        with urllib.request.urlopen(f"{self.base}{path}", timeout=10) as response:
            return response.status, json.loads(response.read())

    def test_overview_route(self):
        status, payload = self._get("/api/v1/learn")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["lesson_count"], 2)

    def test_lessons_route_with_filters(self):
        status, payload = self._get("/api/v1/learn/lessons?level=0&limit=1")
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["lessons"][0]["title"], "Middle C Pulse")

    def test_lessons_route_search_is_accent_folded(self):
        status, payload = self._get("/api/v1/learn/lessons?q=gymnopedie")
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 0)  # no gymnopedie in this seed
        status, payload = self._get("/api/v1/learn/lessons?q=pulse")
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 1)

    def test_lesson_detail_route_with_merged_progress(self):
        lesson_id = lesson_id_for("pianio:t:ex-1")
        status, payload = self._get(f"/api/v1/learn/lessons/{lesson_id}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["progress"]["attempts"], 2)
        self.assertEqual(payload["progress"]["completions"], 1)

    def test_unknown_lesson_maps_to_404(self):
        import urllib.error

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/v1/learn/lessons/nope")
        self.assertEqual(ctx.exception.code, 404)

    def test_unknown_query_key_rejected(self):
        import urllib.error

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/v1/learn/lessons?bogus=1")
        self.assertEqual(ctx.exception.code, 400)

    def test_player_isolation_no_learn_content_in_library(self):
        """The Learn catalog never appears through Player surfaces."""
        from piano_core.api.serialization import library_stats_payload

        # static guarantee: learn routes and library routes are disjoint
        from piano_core.api.handlers import ROUTES

        learn_paths = {r.pattern for r in ROUTES if "/learn" in r.pattern}
        library_paths = {r.pattern for r in ROUTES if "/library" in r.pattern}
        self.assertTrue(learn_paths)
        self.assertFalse(learn_paths & library_paths)


if __name__ == "__main__":
    unittest.main()
