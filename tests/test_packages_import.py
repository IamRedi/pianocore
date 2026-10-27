"""M5 importer tests: fail-closed, atomic, idempotent, offline + tar."""

import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from piano_core.packages.importer import (
    PackageAlreadyPresentError,
    PackageImportError,
    PackageImporter,
)
from piano_core.packages.store import PackageStore
from tests.test_packages_contract import build_members


class ImporterHarness(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root = self.tmp / "packages"
        self.store = PackageStore(self.tmp / "packages.db")
        self.importer = PackageImporter(self.root, self.store)

    def _write_folder(self, name="pkg", **kwargs) -> Path:
        folder = self.tmp / name
        folder.mkdir(parents=True, exist_ok=True)
        for member, payload in build_members(self.tmp / "_stage", **kwargs).items():
            (folder / member).write_bytes(payload)
        return folder

    def _tar_bytes(self, **kwargs) -> bytes:
        files = build_members(self.tmp / "_stage2", **kwargs)
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            for member, payload in files.items():
                info = tarfile.TarInfo(name=member)
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
        return buffer.getvalue()


class OfflineImportTests(ImporterHarness):
    def test_offline_import_activates_and_registers(self):
        folder = self._write_folder()
        result = self.importer.import_from_directory(folder)
        self.assertEqual(result["status"], "activated")
        package_id = result["package_id"]
        row = self.store.get_package(package_id)
        self.assertIsNotNone(row)
        self.assertEqual(row.midi_sha256, result["midi_sha256"])
        self.assertTrue((Path(row.package_path) / "piece.mid").is_file())
        # exactly the five contract members were copied
        self.assertEqual(
            sorted(p.name for p in Path(row.package_path).iterdir()),
            sorted(["manifest.json", "piece.mid", "practice.json",
                    "sections.json", "provenance.json"]),
        )
        # the SOURCE folder is untouched (read + copy, never move)
        self.assertTrue((folder / "manifest.json").is_file())

    def test_duplicate_import_is_idempotent(self):
        folder = self._write_folder()
        first = self.importer.import_from_directory(folder)
        second = self.importer.import_from_directory(folder)
        self.assertEqual(first["status"], "activated")
        self.assertEqual(second["status"], "already_present")
        self.assertEqual(first["package_id"], second["package_id"])

    def test_different_version_coexists(self):
        folder_a = self._write_folder("a", source_version_id=11)
        folder_b = self._write_folder("b", source_version_id=12)
        ra = self.importer.import_from_directory(folder_a)
        rb = self.importer.import_from_directory(folder_b)
        self.assertNotEqual(ra["package_id"], rb["package_id"])
        self.assertEqual(len(self.store.list_packages()), 2)

    def test_missing_file_rejected_before_anything_activates(self):
        folder = self._write_folder()
        (folder / "sections.json").unlink()
        with self.assertRaises(PackageImportError):
            self.importer.import_from_directory(folder)
        self.assertEqual(self.store.list_packages(), [])
        self.assertEqual(list(self.root.iterdir()), [self.root / "incoming"])

    def test_tampered_package_rejected(self):
        folder = self._write_folder(corrupt_member="piece.mid")
        with self.assertRaises(PackageImportError):
            self.importer.import_from_directory(folder)
        self.assertEqual(self.store.list_packages(), [])

    def test_unknown_top_level_file_rejected(self):
        folder = self._write_folder()
        (folder / "payload.sh").write_bytes(b"#!/bin/sh\n")
        with self.assertRaises(PackageImportError):
            self.importer.import_from_directory(folder)
        self.assertEqual(self.store.list_packages(), [])

    def test_failed_import_leaves_no_staging_behind(self):
        folder = self._write_folder(corrupt_member="sections.json")
        with self.assertRaises(PackageImportError):
            self.importer.import_from_directory(folder)
        self.assertEqual(list(self.importer._incoming.iterdir()), [])


class TarImportTests(ImporterHarness):
    def test_tar_upload_activates(self):
        result = self.importer.import_from_tar(self._tar_bytes())
        self.assertEqual(result["status"], "activated")

    def test_tar_extra_member_rejected(self):
        files = build_members(self.tmp / "_s3")
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            for member, payload in files.items():
                info = tarfile.TarInfo(name=member)
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
            evil = tarfile.TarInfo(name="evil.sh")
            evil.size = 4
            tar.addfile(evil, io.BytesIO(b"exec"))
        with self.assertRaises(PackageImportError):
            self.importer.import_from_tar(buffer.getvalue())

    def test_tar_traversal_member_rejected(self):
        files = build_members(self.tmp / "_s4")
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            for member, payload in files.items():
                info = tarfile.TarInfo(name=member)
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
            evil = tarfile.TarInfo(name="../../etc/passwd")
            evil.size = 4
            tar.addfile(evil, io.BytesIO(b"root"))
        with self.assertRaises(PackageImportError):
            self.importer.import_from_tar(buffer.getvalue())

    def test_corrupted_tar_rejected(self):
        good = self._tar_bytes()
        corrupted = bytearray(good)
        corrupted[len(corrupted) // 2] ^= 0xFF
        with self.assertRaises(PackageImportError):
            self.importer.import_from_tar(bytes(corrupted))

    def test_directory_member_in_tar_rejected(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            info = tarfile.TarInfo(name="subdir")
            info.type = tarfile.DIRTYPE
            tar.addfile(info)
        with self.assertRaises(PackageImportError):
            self.importer.import_from_tar(buffer.getvalue())

    def test_tar_with_duplicate_member_rejected(self):
        files = build_members(self.tmp / "_s5")
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            for member, payload in list(files.items()) + [
                ("piece.mid", files["piece.mid"])
            ]:
                info = tarfile.TarInfo(name=member)
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
        with self.assertRaises(PackageImportError):
            self.importer.import_from_tar(buffer.getvalue())


class AtomicityTests(ImporterHarness):
    def test_interrupted_import_leaves_no_active_partial(self):
        """Simulate a crash between DB commit and promote."""
        folder = self._write_folder()
        files = {
            member: (folder / member).read_bytes() for member in
            ["manifest.json", "piece.mid", "practice.json",
             "sections.json", "provenance.json"]
        }
        package_id = json.loads(files["manifest.json"])["package_id"]
        # stage the files, insert the DB row, but never promote
        staging = self.root / "incoming" / "staging_deadbeef"
        staging.mkdir(parents=True)
        for member, payload in files.items():
            (staging / member).write_bytes(payload)
        from piano_core.packages.contract import parse_package

        package = parse_package(files)
        from piano_core.packages.importer import _summary_of

        self.store.insert_package(
            _summary_of(package, str(self.root / package_id), "2026-01-01T00:00:00Z"),
            {},
        )
        # startup reconciliation removes the abandoned staging...
        removed = self.importer.reconcile_staging()
        self.assertEqual(removed, 1)
        # ...but the DB row exists while the promoted dir does not:
        # the store must not serve a package whose files are absent.
        row = self.store.get_package(package_id)
        self.assertIsNotNone(row)
        self.assertFalse(Path(row.package_path).exists())
        service_check = self.store.package_path(package_id)
        self.assertIsNotNone(service_check)
        # a re-import of the same package heals the appliance (idempotent
        # identity with absent content re-activates a fresh copy)
        result = self.importer.import_from_directory(folder)
        self.assertEqual(result["status"], "activated")
        self.assertTrue((Path(result["package_path"]) / "piece.mid").is_file())


if __name__ == "__main__":
    unittest.main()
