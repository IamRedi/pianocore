"""Practice package importer (M5): the ONLY package-entry path.

Pipeline (fail closed, atomic, idempotent):

    incoming package (folder or tar envelope)
      -> file allowlist validation (exact member set, no traversal)
      -> bounded-size read
      -> contract validation (schema/identity/checksums/bounds)
      -> musical validation (canonical timeline loads from piece.mid)
      -> duplicate identity check (idempotent "already present")
      -> stage under ``<root>/incoming/<staging-id>/``
      -> DB commit (packages row)
      -> atomic promote (rename into the library root)

If anything fails at ANY stage before promotion, the staging directory
is removed and nothing is registered: no partial package can ever
become active. Abandoned staging directories from crashed runs are
reconciled (removed) on startup and never imported.
"""

from __future__ import annotations

import io
import logging
import shutil
import tarfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from piano_core.packages.contract import (
    MEMBERS,
    MAX_MIDI_BYTES,
    MAX_PACKAGE_BYTES,
    PackageError,
    PracticePackage,
    parse_package,
    sha256_bytes,
)
from piano_core.packages.store import PackageStore, utc_now

logger = logging.getLogger("piano_core.packages.importer")


class PackageImportError(RuntimeError):
    """A package import was rejected or failed. Nothing was activated."""


class PackageAlreadyPresentError(PackageImportError):
    """The identical package already exists (idempotent re-import)."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PackageImporter:
    """Fail-closed, atomic, idempotent practice package importer."""

    def __init__(self, root: str | Path, store: PackageStore) -> None:
        self._root = Path(root).resolve()
        self._incoming = self._root / "incoming"
        self._store = store
        self._root.mkdir(parents=True, exist_ok=True)
        self._incoming.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ API

    def import_from_directory(self, source: str | Path) -> dict:
        """Offline path: import a plain package folder (primary path)."""
        directory = Path(source)
        if not directory.is_dir():
            raise PackageImportError(f"package folder not found: {directory}")
        resolved = directory.resolve()
        present = {entry.name for entry in resolved.iterdir()}
        unknown = sorted(present - set(MEMBERS))
        if unknown:
            raise PackageImportError(
                f"package folder contains non-contract entries: {unknown}"
            )
        files: dict[str, bytes] = {}
        for member in MEMBERS:
            member_path = resolved / member
            if not member_path.is_file():
                raise PackageImportError(f"package folder is missing {member}")
            if member_path.stat().st_size > MAX_PACKAGE_BYTES:
                raise PackageImportError(f"member {member} exceeds the size bound")
            files[member] = member_path.read_bytes()
        return self._import_files(files, origin=str(resolved))

    def import_from_tar(self, tar_bytes: bytes) -> dict:
        """LAN path: import a normalized tar envelope of the five members.

        Extraction is bounded and traversal-proof: only flat regular-file
        members whose name is exactly in the allowlist are read; nothing
        is ever extracted to arbitrary paths and nothing is executed.
        """
        if not tar_bytes:
            raise PackageImportError("upload body is empty")
        if len(tar_bytes) > MAX_PACKAGE_BYTES:
            raise PackageImportError(
                f"upload exceeds the {MAX_PACKAGE_BYTES} byte bound"
            )
        files = self._read_tar(io.BytesIO(tar_bytes))
        return self._import_files(files, origin="lan-upload")

    def reconcile_staging(self) -> int:
        """Remove abandoned staging directories (crash recovery)."""
        removed = 0
        if not self._incoming.is_dir():
            return 0
        for entry in self._incoming.iterdir():
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        if removed:
            logger.info("reconciled %d abandoned staging directories", removed)
        return removed

    # ------------------------------------------------------------- internals

    @staticmethod
    def _read_tar(stream: io.BytesIO) -> dict[str, bytes]:
        files: dict[str, bytes] = {}
        try:
            with tarfile.open(fileobj=stream, mode="r:") as tar:
                for member in tar.getmembers():
                    if not member.isfile():
                        raise PackageImportError(
                            f"package tar contains non-file member {member.name!r}"
                        )
                    name = member.name
                    if name.startswith("./"):
                        name = name[2:]
                    if name not in MEMBERS:
                        raise PackageImportError(
                            f"package tar member {member.name!r} is not in the "
                            "contract allowlist"
                        )
                    if name in files:
                        raise PackageImportError(f"duplicate member {name!r}")
                    if member.size > MAX_MIDI_BYTES * 4:
                        raise PackageImportError(
                            f"member {name!r} exceeds the size bound"
                        )
                    handle = tar.extractfile(member)
                    if handle is None:
                        raise PackageImportError(f"member {name!r} is unreadable")
                    data = handle.read(member.size + 1)
                    if len(data) != member.size:
                        raise PackageImportError(f"member {name!r} truncated")
                    files[name] = data
        except tarfile.TarError as exc:
            raise PackageImportError(f"unreadable package tar: {exc}") from exc
        missing = sorted(set(MEMBERS) - set(files))
        if missing:
            raise PackageImportError(f"package tar is missing members: {missing}")
        return files

    def _import_files(self, files: dict[str, bytes], *, origin: str) -> dict:
        # 1+2) allowlist + bounds were applied by the caller/reader; the
        # contract layer re-validates everything fail closed.
        try:
            package = parse_package(files)
        except PackageError as exc:
            raise PackageImportError(str(exc)) from exc

        # 3) musical validation: the canonical timeline must load from the
        # package MIDI (this also catches malformed mid-file content).
        self._validate_musically(files["piece.mid"], package)

        # 4) idempotency: identical identity with INTACT content ->
        # already present. A row whose promoted files are missing (crash
        # between DB commit and atomic promote) is repaired by importing.
        existing = self._store.get_package(package.package_id)
        if existing is not None:
            if existing.package_digest != package.identity.package_digest:
                raise PackageImportError(
                    "conflicting package: same id but different digest"
                )
            if (Path(existing.package_path) / "piece.mid").is_file():
                return {
                    "status": "already_present",
                    "package_id": package.package_id,
                    "package_path": existing.package_path,
                }
            logger.warning(
                "repairing package %s: DB row exists but promoted files "
                "are missing",
                package.package_id,
            )

        # 5) stage -> DB commit -> atomic promote.
        staging = self._incoming / f"staging_{uuid.uuid4().hex}"
        staging.mkdir(parents=True, exist_ok=True)
        try:
            final = self._root / package.package_id
            if final.exists():
                raise PackageImportError(
                    f"package directory already exists: {package.package_id}"
                )
            for member, payload in files.items():
                (staging / member).write_bytes(payload)
            self._store.replace_package(
                _summary_of(package, str(final), _utc_now()),
                package.provenance,
            )
            staging.rename(final)  # atomic within the filesystem
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        logger.info(
            "practice package activated: %s (%s, origin=%s)",
            package.package_id,
            package.title,
            origin,
        )
        return {
            "status": "activated",
            "package_id": package.package_id,
            "title": package.title,
            "midi_sha256": package.identity.midi_sha256,
            "package_digest": package.identity.package_digest,
            "package_path": str(final),
        }

    @staticmethod
    def _validate_musically(midi_bytes: bytes, package: PracticePackage) -> None:
        """The canonical timeline must load from the package MIDI.

        Interpretation stays inside the sanctioned playback package
        (containment invariant): the importer only asks whether the
        material is playable and whether the practice events match it.
        """
        from piano_core.playback.timeline import load_timeline_if_playable

        import tempfile

        with tempfile.TemporaryDirectory(prefix="pc_pkg_") as tmp:
            probe = Path(tmp) / "piece.mid"
            probe.write_bytes(midi_bytes)
            timeline = load_timeline_if_playable(probe)
        if timeline is None:
            raise PackageImportError(
                "package MIDI is not playable (failed canonical "
                "timeline validation)"
            )
        # every practice event must exist in the canonical timeline
        timeline_ons = [
            (round(event.time, 3), event.command)
            for event in timeline.events
            if getattr(event.command, "type", None) is not None
            and event.command.type.value == "note_on"
        ]
        timeline_index: dict[tuple[float, int], int] = {}
        for time_s, command in timeline_ons:
            key = (time_s, int(command.note))
            timeline_index[key] = timeline_index.get(key, 0) + 1
        for event in package.events:
            key = (round(event.onset, 3), event.note)
            remaining = timeline_index.get(key, 0)
            if remaining <= 0:
                # tolerate small float drift by scanning neighbours
                if not _drift_match(timeline_index, key):
                    raise PackageImportError(
                        f"practice event {event.event_id} (note {event.note} "
                        f"@ {event.onset:.3f}s) does not exist in the package "
                        "MIDI; package is inconsistent"
                    )
            else:
                timeline_index[key] = remaining - 1


def _drift_match(index: dict[tuple[float, int], int], key: tuple[float, int]) -> bool:
    """Allow <=10 ms float drift between practice.json and the timeline."""
    onset, note = key
    for delta in (0.001, -0.001, 0.002, -0.002, 0.005, -0.005, 0.01, -0.01):
        candidate = (round(onset + delta, 3), note)
        if index.get(candidate, 0) > 0:
            index[candidate] -= 1
            return True
    return False


def _summary_of(package: PracticePackage, path: str, activated_at: str):
    from piano_core.packages.store import PackageRow

    return PackageRow(
        package_id=package.package_id,
        title=package.title,
        source_version_id=package.identity.source_version_id,
        midi_sha256=package.identity.midi_sha256,
        package_digest=package.identity.package_digest,
        difficulty=package.identity.difficulty,
        hands_availability=package.identity.hands_availability,
        rh_count=package.identity.rh_count,
        lh_count=package.identity.lh_count,
        duration_seconds=package.identity.duration_seconds,
        section_count=len(package.sections),
        event_count=len(package.events),
        source_type=package.identity.source_type,
        transformation_type=package.identity.transformation_type,
        provenance=package.identity.provenance_summary,
        package_path=path,
        activated_at=activated_at,
        status="active",
    )
