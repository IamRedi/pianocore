"""Package service facade + PracticeManager bridge (M5).

``PackageService`` composes the store and importer and exposes the
package practice operations (plans, sections, guidance) plus the
capability document used for Studio compatibility checks.

``PackageAwareLibrary`` wraps the canonical LibraryService so the
ACCEPTED :class:`~piano_core.practice.manager.PracticeManager` can
practice imported packages without modification: ids prefixed
``package:`` resolve against the package store (title/path), everything
else falls through to the real library. The package piece is a managed
activated copy, so ``resolve`` may return a WRITABLE path (unlike the
read-only source tree).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from piano_core.packages.contract import (
    LED_SEMANTIC_VERSION,
    MAX_PACKAGE_BYTES,
    PACKAGE_SCHEMA_VERSION,
    PRACTICE_CONTRACT_VERSION,
    SUPPORTED_CAPABILITIES,
)
from piano_core.packages.importer import PackageImporter
from piano_core.packages.practice import (
    GUIDED_MODES,
    build_package_plan,
    hands_mode_available,
    plan_section,
)
from piano_core.practice.model import LearningMode
from piano_core.packages.store import PackageStore

logger = logging.getLogger("piano_core.packages.service")

_PACKAGE_ID_PREFIX = "package:"


class PackageService:
    """Composition of the package subsystem (auxiliary degrade contract)."""

    def __init__(self, root: str | Path, db_path: str | Path) -> None:
        self.root = Path(root).resolve()
        self.store = PackageStore(db_path)
        self.importer = PackageImporter(self.root, self.store)
        self.importer.reconcile_staging()

    # ------------------------------------------------------------- packages

    def list_packages(self) -> list[dict]:
        return [row.to_dict() for row in self.store.list_packages()]

    def get_package_row(self, package_id: str):
        row = self.store.get_package(package_id)
        if row is None:
            from piano_core.packages.importer import PackageImportError

            raise PackageImportError(f"unknown package: {package_id!r}")
        return row

    def load_package(self, package_id: str):
        """Parse the activated package from its managed directory."""
        row = self.store.get_package(package_id)
        if row is None:
            from piano_core.packages.importer import PackageImportError

            raise PackageImportError(f"unknown package: {package_id!r}")
        from piano_core.packages.contract import MEMBERS, parse_package

        files = {}
        for member in MEMBERS:
            path = Path(row.package_path) / member
            if not path.is_file():
                raise RuntimeError(
                    f"activated package {package_id} is missing {member}; "
                    "library integrity violated"
                )
            files[member] = path.read_bytes()
        return parse_package(files)

    def capabilities(self) -> dict:
        return {
            "system": "piano-core",
            "package_schema_versions": [PACKAGE_SCHEMA_VERSION],
            "practice_contract_versions": [PRACTICE_CONTRACT_VERSION],
            "led_semantic_versions": [LED_SEMANTIC_VERSION],
            "supported_capabilities": list(SUPPORTED_CAPABILITIES),
            "max_package_bytes": MAX_PACKAGE_BYTES,
            "hands_support": True,
            "sections_support": True,
            "practice_modes": list(GUIDED_MODES)
            + ["RH", "LH", "BOTH_HANDS", "LOOP_SECTION", "TEMPO_SCALE"],
            "learning_modes": [mode.value for mode in LearningMode],
        }

    # ------------------------------------------------------------- practice

    def build_plan(self, package_id: str, *, hand: str | None = None,
                   section_id: str | None = None):
        """Package-derived plan for the accepted PracticeEngine/Manager."""
        package = self.load_package(package_id)
        section = plan_section(package, section_id)
        return build_package_plan(package, hand=hand, section=section), package

    def package_duration(self, package_id: str) -> float:
        return self.load_package(package_id).identity.duration_seconds

    def hand_modes_available(self, package_id: str) -> bool:
        return hands_mode_available(self.load_package(package_id))

    @staticmethod
    def is_package_id(song_id: str) -> bool:
        return isinstance(song_id, str) and song_id.startswith(_PACKAGE_ID_PREFIX)

    @staticmethod
    def strip_prefix(song_id: str) -> str:
        return song_id[len(_PACKAGE_ID_PREFIX):]


class _PackageRecord:
    """Minimal record surface PracticeManager.start expects."""

    __slots__ = ("title", "filename")

    def __init__(self, title: str) -> None:
        self.title = title
        self.filename = title


class PackageAwareLibrary:
    """Library fall-through so the accepted PracticeManager is untouched.

    ``package:<id>`` ids resolve to the activated package copy; any other
    id delegates to the canonical LibraryService.
    """

    def __init__(self, library, service: PackageService) -> None:
        self._library = library
        self._service = service
        self._lock = threading.Lock()

    @property
    def wrapped(self):
        return self._library

    def get(self, song_id: str):
        if PackageService.is_package_id(song_id):
            row = self._service.store.get_package(
                PackageService.strip_prefix(song_id)
            )
            if row is None:
                return None
            return _PackageRecord(title=row.title)
        return self._library.get(song_id)

    def resolve(self, song_id: str) -> Path:
        if PackageService.is_package_id(song_id):
            package_id = PackageService.strip_prefix(song_id)
            path = self._service.store.package_path(package_id)
            if path is None or not (path / "piece.mid").is_file():
                from piano_core.library.model import LibraryEntryGoneError

                raise LibraryEntryGoneError(
                    f"package copy missing on disk: {package_id}"
                )
            return path / "piece.mid"
        return self._library.resolve(song_id)

    # pass-throughs used elsewhere in the app (handlers call the library
    # directly); never expose package rows through these.
    def list_songs(self, *args, **kwargs):
        return self._library.list_songs(*args, **kwargs)

    def search(self, *args, **kwargs):
        return self._library.search(*args, **kwargs)

    def stats(self, *args, **kwargs):
        return self._library.stats(*args, **kwargs)
