"""M5 practice-package contract tests (fail-closed validation matrix)."""

import hashlib
import io
import json
import tempfile
import unittest
import uuid
from pathlib import Path

from piano_core.packages.contract import (
    MAX_NOTE_COUNT,
    PACKAGE_SCHEMA_VERSION,
    PRACTICE_CONTRACT_VERSION,
    PackageError,
    package_digest_of,
    package_id_for,
    parse_package,
)
from tests.test_library import write_midi

# ---------------------------------------------------------------- helpers


def _package_id(source_version_id: int, midi_sha: str) -> str:
    return package_id_for(source_version_id, midi_sha)


def build_members(
    tmp: Path,
    *,
    notes=((60, 85, "RH"), (62, 85, "LH"), (64, 80, "RH")),
    source_version_id=11,
    tempo_µs=500_000,
    tpb=96,
    sections=2,
    hands_availability="available",
    force_event_hands=None,
    required_capabilities=(),
    corrupt_member=None,
    drop_member=None,
    extra_member=None,
    override_schema=None,
    override_contract=None,
    title="Bridge Piece",
) -> dict[str, bytes]:
    """Self-contained practice-package/v1 builder (mirrors the Studio)."""
    midi_path = tmp / "piece.mid"
    write_midi(
        midi_path,
        notes=tuple((n, v) for n, v, _ in notes),
        tempo_µs=tempo_µs,
        tpb=tpb,
    )
    midi_bytes = midi_path.read_bytes()
    midi_sha = hashlib.sha256(midi_bytes).hexdigest()
    package_id = _package_id(source_version_id, midi_sha)

    # write_midi layout: tempo meta at tick 0, first note at tick 48
    # (tpb=96, 500k µs/beat) => onsets 0.25 + i*0.5s, duration 0.25s each.
    events = []
    for index, (note, velocity, hand) in enumerate(notes):
        onset = round(0.25 + index * 0.5, 4)
        events.append(
            {
                "event_id": index,
                "note": note,
                "onset": onset,
                "offset": round(onset + 0.25, 4),
                "velocity": velocity,
                "hand": force_event_hands[index] if force_event_hands else (
                    hand if hands_availability != "unknown" else None
                ),
                "track": 0,
                "channel": 1,
                "bar": index,
                "beat": 1.0,
                "section": min(index * sections // max(1, len(notes)), sections - 1)
                if sections else 0,
                "group_id": index,
                "importance": 0.5,
                "source_note_id": index,
            }
        )
    total_events = len(events)
    per_section = max(1, total_events // max(1, sections))
    section_list = []
    for s_index in range(max(1, sections)):
        members_here = events[s_index * per_section:
                              (s_index + 1) * per_section if s_index < sections - 1
                              else total_events]
        if not members_here:
            continue
        start = members_here[0]["onset"]
        end = members_here[-1]["offset"]
        section_list.append(
            {
                "section_id": f"section:{s_index}",
                "label": f"Bars {s_index * per_section + 1}-{s_index * per_section + max(1, len(members_here))}",
                "start_bar": s_index * per_section,
                "end_bar": s_index * per_section + max(1, len(members_here)),
                "start_time": start,
                "end_time": end,
                "note_count": len(members_here),
                "rh_count": sum(1 for e in members_here if e["hand"] == "RH"),
                "lh_count": sum(1 for e in members_here if e["hand"] == "LH"),
                "difficulty": "medium",
                "suggested_loop": True,
                "evidence": "bar_windows_4",
            }
        )

    practice = {
        "schema": "practice-events/v1",
        "practice_event_schema_version": 1,
        "package_id": package_id,
        "event_count": len(events),
        "duration_seconds": round(0.25 + total_events * 0.5, 4),
        "hands_availability": hands_availability,
        "events": events,
    }
    sections_doc = {
        "schema": "practice-sections/v1",
        "section_schema_version": 1,
        "package_id": package_id,
        "section_count": len(section_list),
        "basis": "bar_windows_4",
        "sections": section_list,
    }
    provenance = {
        "schema": "practice-provenance/v1",
        "provenance_schema_version": 1,
        "source_system": "midi-curator",
        "source_version_id": source_version_id,
        "source_version_kind": "transform",
        "root_version_id": 1,
        "parent_version_id": None,
        "source_file_id": 42,
        "midi_sha256": midi_sha,
        "transformation_type": "beginner",
        "algo_versions": {"engine": 1},
        "analysis_version": 1,
        "lineage": None,
        "generator": None,
    }
    def _canon(payload):
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    practice_bytes = _canon(practice)
    sections_bytes = _canon(sections_doc)
    provenance_bytes = _canon(provenance)
    checksums = {
        "piece.mid": midi_sha,
        "practice.json": hashlib.sha256(practice_bytes).hexdigest(),
        "sections.json": hashlib.sha256(sections_bytes).hexdigest(),
        "provenance.json": hashlib.sha256(provenance_bytes).hexdigest(),
    }
    manifest = {
        "schema": override_schema or "practice-package/v1",
        "package_schema_version": PACKAGE_SCHEMA_VERSION,
        "practice_contract_version": override_contract or PRACTICE_CONTRACT_VERSION,
        "led_semantic_version": 1,
        "practice_event_schema_version": 1,
        "package_id": package_id,
        "source_system": "midi-curator",
        "source_version_id": source_version_id,
        "piece_version_id": source_version_id,
        "title": title,
        "artist": None,
        "midi_sha256": midi_sha,
        "midi_size_bytes": len(midi_bytes),
        "duration_seconds": round(0.25 + total_events * 0.5, 4),
        "tempo_summary": [{"second": 0.0, "bpm": 120.0}],
        "time_signature_summary": [{"second": 0.0, "numerator": 4, "denominator": 4}],
        "difficulty": "intermediate",
        "piano_suitability": 0.7,
        "hands": {
            "availability": hands_availability,
            "rh_count": sum(1 for e in events if e["hand"] == "RH"),
            "lh_count": sum(1 for e in events if e["hand"] == "LH"),
        },
        "sections_available": bool(section_list),
        "section_count": len(section_list),
        "event_count": len(events),
        "source_type": "midi_curator_version",
        "transformation_type": "beginner",
        "provenance_summary": {"source_version_id": source_version_id},
        "required_capabilities": list(required_capabilities),
        "checksums": checksums,
        "created_at": "2026-09-20T00:00:00Z",
        "package_digest": package_digest_of(checksums),
    }
    files = {
        "manifest.json": json.dumps(manifest, sort_keys=True).encode(),
        "piece.mid": midi_bytes,
        "practice.json": practice_bytes,
        "sections.json": sections_bytes,
        "provenance.json": provenance_bytes,
    }
    if drop_member:
        files.pop(drop_member)
    if extra_member:
        files[extra_member] = b"evil"
    if corrupt_member:
        files[corrupt_member] = files[corrupt_member] + b"tamper"
    return files


class ContractTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def test_valid_package_parses(self):
        package = parse_package(build_members(self.tmp))
        self.assertEqual(package.identity.hands_availability, "available")
        self.assertEqual(len(package.events), 3)
        self.assertEqual(len(package.sections), 2)
        self.assertTrue(all(event.hand in ("RH", "LH") for event in package.events))

    def test_package_identity_rederives_from_basis(self):
        files = build_members(self.tmp, source_version_id=11)
        midi_sha = hashlib.sha256(files["piece.mid"]).hexdigest()
        self.assertEqual(
            json.loads(files["manifest.json"])["package_id"],
            _package_id(11, midi_sha),
        )

    def test_missing_member_rejected(self):
        with self.assertRaises(PackageError):
            parse_package(build_members(self.tmp, drop_member="sections.json"))

    def test_extra_member_rejected(self):
        with self.assertRaises(PackageError):
            parse_package(build_members(self.tmp, extra_member="run.sh"))

    def test_tampered_member_rejected(self):
        with self.assertRaises(PackageError):
            parse_package(build_members(self.tmp, corrupt_member="practice.json"))

    def test_unknown_schema_rejected(self):
        with self.assertRaises(PackageError):
            parse_package(build_members(self.tmp, override_schema="practice-package/v9"))

    def test_unknown_contract_version_rejected(self):
        with self.assertRaises(PackageError):
            parse_package(build_members(self.tmp, override_contract=99))

    def test_unknown_capability_fails_closed(self):
        with self.assertRaises(PackageError):
            parse_package(
                build_members(self.tmp, required_capabilities=("hugo_coaching",))
            )

    def test_supported_capability_accepted(self):
        package = parse_package(
            build_members(self.tmp, required_capabilities=("hands", "sections"))
        )
        self.assertIn("hands", package.identity.required_capabilities)

    def test_hand_metadata_contracts_enforced(self):
        # "unknown" must mean NO hand evidence anywhere (no fabricated
        # certainty) — events carrying hands while claiming unknown fail.
        with self.assertRaises(PackageError):
            parse_package(
                build_members(
                    self.tmp,
                    hands_availability="unknown",
                    force_event_hands=("RH", "LH", "RH"),
                )
            )
        with self.assertRaises(PackageError):
            parse_package(build_members(self.tmp, hands_availability="nonsense"))
        # "unknown" with truly hand-less events is valid.
        package = parse_package(build_members(self.tmp, hands_availability="unknown"))
        self.assertEqual(package.identity.hands_availability, "unknown")

    def test_out_of_keyboard_note_rejected(self):
        with self.assertRaises(PackageError):
            parse_package(build_members(self.tmp, notes=((8, 90, "LH"),)))

    def test_identity_mismatch_rejected(self):
        files = build_members(self.tmp)
        manifest = json.loads(files["manifest.json"])
        manifest["source_version_id"] = 999
        files["manifest.json"] = json.dumps(manifest, sort_keys=True).encode()
        with self.assertRaises(PackageError):
            parse_package(files)

    def test_digest_mismatch_rejected(self):
        files = build_members(self.tmp)
        manifest = json.loads(files["manifest.json"])
        manifest["package_digest"] = "0" * 64
        files["manifest.json"] = json.dumps(manifest, sort_keys=True).encode()
        with self.assertRaises(PackageError):
            parse_package(files)

    def test_non_midi_piece_rejected(self):
        files = build_members(self.tmp)
        manifest = json.loads(files["manifest.json"])
        files["piece.mid"] = b"definitely not midi bytes" + b"\x00" * 32
        manifest["checksums"]["piece.mid"] = hashlib.sha256(
            files["piece.mid"]
        ).hexdigest()
        manifest["package_digest"] = package_digest_of(manifest["checksums"])
        files["manifest.json"] = json.dumps(manifest, sort_keys=True).encode()
        with self.assertRaises(PackageError):
            parse_package(files)

    def test_malformed_practice_json_rejected(self):
        files = build_members(self.tmp)
        files["practice.json"] = b"{not json"
        with self.assertRaises(PackageError):
            parse_package(files)

    def test_event_outside_sections_rejected(self):
        files = build_members(self.tmp)
        practice = json.loads(files["practice.json"])
        for event in practice["events"]:
            event["section"] = 7
        files["practice.json"] = json.dumps(practice, sort_keys=True).encode()
        with self.assertRaises(PackageError):
            parse_package(files)


if __name__ == "__main__":
    unittest.main()
