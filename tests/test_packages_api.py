"""M5 packages API tests over a real loopback HTTP server.

Composes the same wiring as Application._start_packages/_start_practice
(PackageAwareLibrary wrapper, PracticeManager, packages service) with
the stdlib API server, then exercises the M5 endpoints end-to-end:
capability discovery, upload (happy / duplicate / corrupted), library
listing, guidance frames, and Wait-mode practice on a package through
the ACCEPTED learning endpoints (including automatic session
recording at stop).
"""

import json
import logging
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from piano_core.api.server import ApiServer
from piano_core.health import Health, HealthState
from piano_core.midi.events import note_off as ie_off
from piano_core.midi.events import note_on as ie_on
from piano_core.midi.output_events import note_on as build_note_on
from piano_core.playback.timeline import Timeline, TimelineEvent
from piano_core.practice.manager import PracticeManager
from piano_core.packages.importer import PackageImporter
from piano_core.packages.service import PackageAwareLibrary, PackageService
from piano_core.packages.store import PackageStore
from piano_core.state import MidiConnectionState, StateStore
from tests.test_packages_contract import build_members


def _timeline_for_path(path, tempo_scale=1.0):
    """Canonical timeline loader over the ACTUAL file (no stubs)."""
    from piano_core.playback.timeline import load_timeline

    return load_timeline(path, tempo_scale)


class PackagesApiHarness(unittest.TestCase):
    def setUp(self):
        logging.getLogger("piano_core").setLevel(logging.CRITICAL)
        self.addCleanup(
            logging.getLogger("piano_core").setLevel, logging.INFO
        )
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

        self.library_root = self.tmp / "library"
        self.library_root.mkdir()
        self.store = PackageStore(self.tmp / "packages.db")
        self.service = PackageService(
            root=self.tmp / "packages", db_path=self.tmp / "packages.db"
        )
        # real LibraryService over an empty read-only tree (fall-through)
        from piano_core.library.service import LibraryConfig, LibraryService

        self.library = LibraryService(
            LibraryConfig(
                root=str(self.library_root),
                index_path=str(self.tmp / "library.sqlite3"),
            )
        )
        self.library.scan(enrich=False)

        self.state_store = StateStore()
        self.state_store.set_midi_connection(MidiConnectionState.CONNECTED)
        practice_library = PackageAwareLibrary(self.library, self.service)
        self.practice = PracticeManager(
            library=practice_library,
            state_store=self.state_store,
            lock_path=str(self.tmp / "activity.lock"),
            led=None,
            timeline_loader=_timeline_for_path,
        )

        # the context IS this test object (same attribute surface the
        # Application exposes to handlers)
        self.health = Health(HealthState.READY)
        self.packages_service = self.service

        self.server = ApiServer(
            self, "127.0.0.1", 0, serve_ui=False,
            logger=logging.getLogger("piano_core.api.test"),
        )
        self.server.start()
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.bound_address[1]}/api/v1"
        self.addCleanup(self.practice.shutdown)

    # Application context surface used by handlers
    @property
    def library_ready(self):
        return True

    def _request(self, method, path, body=None, content_type="application/json"):
        data = None
        headers = {}
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            headers["Content-Type"] = content_type
        request = urllib.request.Request(
            self.base + path, data=data, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode()
            try:
                return exc.code, json.loads(payload)
            except json.JSONDecodeError:
                return exc.code, {"raw": payload}

    def _package_folder(self, **kwargs) -> tuple[Path, str]:
        folder = self.tmp / f"src_{abs(hash((id(self), str(kwargs))))}"
        folder.mkdir(parents=True, exist_ok=True)
        for member, payload in build_members(self.tmp / "_stage", **kwargs).items():
            (folder / member).write_bytes(payload)
        manifest = json.loads((folder / "manifest.json").read_bytes())
        return folder, manifest["package_id"]

    # Application surface used by health/state handlers
    @property
    def library_stats(self):
        return self.library.stats()


class PackagesApiTests(PackagesApiHarness):
    def test_capabilities_and_empty_library(self):
        status, payload = self._request("GET", "/packages/capabilities")
        self.assertEqual(status, 200)
        caps = payload["capabilities"]
        self.assertEqual(caps["package_schema_versions"], [1])
        self.assertEqual(caps["practice_contract_versions"], [1])
        self.assertIn("hands", caps["supported_capabilities"])
        status, payload = self._request("GET", "/packages")
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 0)

    def test_upload_activate_list_get_progress(self):
        folder, package_id = self._package_folder()
        tar_bytes = self._tar_of(folder)
        status, payload = self._request(
            "POST", "/packages/upload", body=tar_bytes,
            content_type="application/x-tar",
        )
        self.assertEqual(status, 201, payload)
        ack = payload["ack"]
        self.assertEqual(ack["status"], "activated")
        self.assertEqual(ack["package_id"], package_id)
        self.assertTrue(ack["midi_sha256"])

        # duplicate send: idempotent answer, NOT a second activation
        status, payload = self._request(
            "POST", "/packages/upload", body=tar_bytes,
            content_type="application/x-tar",
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "already_present")

        status, payload = self._request("GET", "/packages")
        self.assertEqual(payload["count"], 1)
        status, payload = self._request("GET", f"/packages/{package_id}")
        self.assertEqual(status, 200)
        self.assertTrue(payload["hand_modes_available"])
        self.assertTrue(payload["sections"])
        self.assertEqual(
            payload["provenance"]["source_system"], "midi-curator"
        )
        status, payload = self._request("GET", f"/packages/{package_id}/progress")
        self.assertEqual(status, 200)
        self.assertEqual(payload["attempts"], 0)

    def _tar_of(self, folder: Path) -> bytes:
        import tarfile

        buffer = __import__("io").BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            for member in sorted(folder.iterdir()):
                info = tarfile.TarInfo(name=member.name)
                info.size = member.stat().st_size
                tar.addfile(info, member.open("rb"))
        return buffer.getvalue()

    def test_corrupted_upload_rejected_nothing_activated(self):
        folder, _ = self._package_folder()
        tar_bytes = bytearray(self._tar_of(folder))
        tar_bytes[len(tar_bytes) // 2] ^= 0xFF
        status, payload = self._request(
            "POST", "/packages/upload", body=bytes(tar_bytes),
            content_type="application/x-tar",
        )
        self.assertEqual(status, 422)
        self.assertEqual(payload["error"]["code"], "package_rejected")
        status, payload = self._request("GET", "/packages")
        self.assertEqual(payload["count"], 0)

    def test_guidance_frame_endpoint(self):
        folder, package_id = self._package_folder()
        self.service.importer.import_from_directory(folder)
        status, payload = self._request(
            "POST",
            "/packages/guidance-frame",
            body={
                "package_id": package_id,
                "mode": "FOLLOW",
                "position_seconds": 0.25,
                "lookahead_ms": 600,
            },
        )
        self.assertEqual(status, 200, payload)
        frame = payload["frame"]
        self.assertEqual(frame["led_semantic_version"], 1)
        self.assertEqual(frame["practice_state"], "FOLLOW")
        self.assertTrue(frame["current_notes"])
        self.assertEqual(frame["current_notes"][0]["state"], "ACTIVE")

    def test_wait_mode_on_package_through_learning_endpoints(self):
        folder, package_id = self._package_folder()
        self.service.importer.import_from_directory(folder)
        status, payload = self._request(
            "POST",
            "/learning/start",
            body={"package_id": package_id, "mode": "WAIT_FOR_NOTE"},
        )
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["state"], "WAITING")
        self.assertEqual(payload["target"]["notes"], [60])
        # play the expected first note (60) via the canonical input path
        event = ie_on(1, 60, 90, 0.0)
        self.state_store.apply_event(event)
        self.practice.observe_input_event(event)
        event = ie_off(1, 60, 0, 0.1)
        self.state_store.apply_event(event)
        self.practice.observe_input_event(event)
        # stop and confirm the package session was recorded automatically
        status, payload = self._request(
            "POST", "/learning/stop", body={"elapsed_seconds": 12.5}
        )
        self.assertEqual(status, 200)
        progress = self.service.store.progress_summary(package_id)
        self.assertEqual(progress["attempts"], 1)
        self.assertEqual(progress["correct_notes"], 1)
        self.assertEqual(progress["recent_tempo"], 1.0)

    def test_hand_mode_on_package(self):
        folder, package_id = self._package_folder()
        self.service.importer.import_from_directory(folder)
        status, payload = self._request(
            "POST",
            "/learning/start",
            body={"package_id": package_id, "mode": "WAIT_FOR_NOTE",
                  "hand": "LH"},
        )
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["target"]["notes"], [62])
        self.assertEqual(payload["hand_selection"], "LH")
        self._request("POST", "/learning/stop", body={})

    def test_unknown_package_start_rejected(self):
        status, payload = self._request(
            "POST",
            "/learning/start",
            body={"package_id": "does-not-exist"},
        )
        self.assertIn(status, (404, 422, 400))


if __name__ == "__main__":
    unittest.main()
