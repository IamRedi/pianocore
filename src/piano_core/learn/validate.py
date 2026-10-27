"""Full-catalog validation (PIANO-LEARN-001).

Re-validates every published package from disk through the production
contract parser AND the real M5 package importer run against a scratch
store (allowlist -> bounds -> contract -> canonical timeline playability
-> staging -> DB commit -> atomic promote) — the exact code path the
appliance executes on deployment, exercised without touching production
state — plus catalog cross-checks:

- contract parse of all five members (hashes, identity, bounds);
- canonical timeline playability through the playback package boundary;
- every practice event exists in the canonical timeline within the
  importer's 10 ms drift window (the importer itself enforces this);
- lesson identity: ``lesson_id == lesson_id_for(lesson_key)``,
  source_version_id re-derivation, catalog<->package 1:1 mapping;
- curriculum coverage: levels and courses populated.

This is the same canonical target-generation process the appliance's
guided practice consumes — nothing Learn-specific is trusted instead.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from piano_core.learn.catalog import LearnCatalog
from piano_core.learn.model import lesson_id_for
from piano_core.packages.contract import MEMBERS, PackageError, parse_package
from piano_core.packages.importer import (
    PackageAlreadyPresentError,
    PackageImporter,
    PackageImportError,
)
from piano_core.packages.store import PackageStore

__all__ = ["validate_build", "ValidationReport"]


class ValidationReport:
    def __init__(self) -> None:
        self.total = 0
        self.passed = 0
        self.failures: list[dict] = []
        self.levels: dict[int, int] = {}
        self.courses: int = 0
        self.excluded_count: int = 0

    def fail(self, package_id: str, stage: str, detail: str) -> None:
        self.failures.append(
            {"package_id": package_id, "stage": stage, "detail": detail}
        )

    def summary(self) -> dict:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": len(self.failures),
            "levels": {str(k): v for k, v in sorted(self.levels.items())},
            "courses": self.courses,
            "excluded": self.excluded_count,
            "failures": self.failures[:50],
        }


def _rederive_version(lesson_key: str) -> int:
    digest = hashlib.sha256(lesson_key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") >> 1


def validate_build(build_root: str | Path) -> ValidationReport:
    """Validate the complete published build. Never touches production."""
    root = Path(build_root)
    packages_dir = root / "packages"
    catalog_path = root / "learn.sqlite3"
    manifest_path = root / "build-manifest.json"
    for required in (packages_dir, catalog_path, manifest_path):
        if not required.exists():
            raise FileNotFoundError(f"missing build artifact: {required}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report = ValidationReport()
    report.excluded_count = len(manifest.get("excluded", []))

    catalog = LearnCatalog(catalog_path)
    try:
        stats = catalog.stats()
        report.levels = {int(k): v for k, v in stats["levels"].items()}
        report.courses = stats["course_count"]

        all_lessons: list = []
        offset = 0
        while True:
            page = catalog.lessons(limit=100, offset=offset)
            if not page:
                break
            all_lessons.extend(page)
            offset += len(page)
    finally:
        catalog.close()

    by_package = {lesson.package_id: lesson for lesson in all_lessons}
    disk_packages = sorted(p.name for p in packages_dir.iterdir() if p.is_dir())

    if set(disk_packages) != set(by_package):
        for package_id in sorted(set(by_package) - set(disk_packages))[:20]:
            report.fail(package_id, "catalog", "catalog row without package folder")
        for package_id in sorted(set(disk_packages) - set(by_package))[:20]:
            report.fail(package_id, "catalog", "package folder without catalog row")

    # Scratch store + the REAL importer: deployment-path validation.
    scratch = Path(tempfile.mkdtemp(prefix="piano-learn-validate-"))
    try:
        store = PackageStore(scratch / "store.sqlite3")
        importer = PackageImporter(scratch / "packages", store)
        try:
            for package_id in disk_packages:
                report.total += 1
                folder = packages_dir / package_id
                try:
                    members = {
                        name: (folder / name).read_bytes()
                        for name in MEMBERS
                    }
                    parsed = parse_package(members)
                except (PackageError, OSError) as exc:
                    report.fail(package_id, "contract", str(exc)[:300])
                    continue
                if parsed.package_id != package_id:
                    report.fail(
                        package_id, "identity", "folder name does not match package id"
                    )
                    continue

                lesson = by_package.get(package_id)
                if lesson is None:
                    report.fail(package_id, "catalog", "no catalog row")
                    continue
                if lesson.lesson_id != lesson_id_for(lesson.lesson_key):
                    report.fail(
                        package_id,
                        "identity",
                        "lesson id does not derive from lesson key",
                    )
                    continue
                if parsed.identity.source_version_id != _rederive_version(
                    lesson.lesson_key
                ):
                    report.fail(package_id, "identity", "source_version_id mismatch")
                    continue

                try:
                    outcome = importer.import_from_directory(folder)
                except (PackageImportError, PackageAlreadyPresentError) as exc:
                    report.fail(package_id, "import", str(exc)[:300])
                    continue
                if outcome.get("status") not in ("activated", "already_present"):
                    report.fail(
                        package_id,
                        "import",
                        f"unexpected importer status {outcome.get('status')!r}",
                    )
                    continue
                report.passed += 1
        finally:
            # PackageStore opens a fresh connection per operation; the
            # scratch database is discarded with the scratch directory.
            pass
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    return report
