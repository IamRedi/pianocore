"""Build practice-package/v1 packages from canonical Learn items.

Deterministic converter between the Learn canonical representation and
the M5 ``practice-package/v1`` exchange format (ADR-PACKAGE-PRACTICE-V1).
Every produced package is validated with the production contract parser
(``piano_core.packages.contract.parse_package``) before it is returned —
a builder output that would fail import can never leave this module.

Musical fidelity rules (PIANO-LEARN-001):

- onsets/offsets are preserved in seconds; the MIDI render uses one
  tempo track so seconds round-trip far inside the importer's 10 ms
  matching window;
- chords stay exact-onset groups (group_id mirrors practice-plan
  chord semantics);
- hand labels pass through only where the source proved them; an item
  claiming ``available`` hands that still carries unassigned notes is
  downgraded to ``partial`` (never fabricated);
- bar/beat positions derive from the item's own time signature;
- pedal/swing metadata lives in provenance (the v1 contract has no
  pedal event surface).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from piano_core.learn.difficulty import DifficultyMetrics, LevelDecision, compute_metrics
from piano_core.learn.model import LearnItem, LearnNote, item_duration, lesson_id_for
from piano_core.packages.contract import (
    LED_SEMANTIC_VERSION,
    MEMBERS,
    PACKAGE_SCHEMA_VERSION,
    PRACTICE_CONTRACT_VERSION,
    SUPPORTED_CAPABILITIES,
    PracticePackage,
    canonical_json,
    package_digest_of,
    package_id_for,
    parse_package,
    sha256_bytes,
)

__all__ = ["PackageBuild", "build_package", "render_midi"]

_TICKS_PER_BEAT = 480
_DEFAULT_BPM = 120.0


@dataclass(frozen=True, slots=True)
class PackageBuild:
    """A fully built and contract-validated practice package."""

    members: dict[str, bytes]
    package_id: str
    midi_sha256: str
    item: LearnItem
    level: LevelDecision
    metrics: DifficultyMetrics
    lesson_id: str


def render_midi(notes: tuple[LearnNote, ...], tempo_bpm: float) -> bytes:
    """Render canonical notes as a deterministic format-0 MIDI file.

    Same notes + same tempo always produce byte-identical output. At an
    equal tick, note_off sorts before note_on so re-pressed repeated
    notes never merge in FIFO pairing.
    """
    import struct

    seconds_per_beat = 60.0 / tempo_bpm

    def tick(seconds: float) -> int:
        return int(round(seconds / seconds_per_beat * _TICKS_PER_BEAT))

    events: list[tuple[int, int, int]] = []  # (tick, kind, note) kind 0=off 1=on
    for note in notes:
        on = tick(note.onset)
        off = tick(note.offset)
        if off <= on:
            off = on + 1
        events.append((on, 1, note.midi))
        events.append((off, 0, note.midi))
    events.sort(key=lambda e: (e[0], e[1], e[2]))

    micros = int(round(60_000_000 / tempo_bpm))
    track = bytearray()
    track += b"\x00\xff\x51\x03" + micros.to_bytes(3, "big")
    track += b"\x00\xff\x58\x04\x04\x02\x18\x08"  # 4/4 (informational)

    last = 0
    for ev_tick, kind, note in events:
        delta = ev_tick - last
        last = ev_tick
        _append_varint(track, delta)
        status = 0x90 if kind else 0x80
        track.append(status | 0)
        track.append(note & 0x7F)
        track.append(80 if kind else 0)
    track += b"\x00\xff\x2f\x00"

    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, _TICKS_PER_BEAT)
    chunk = b"MTrk" + struct.pack(">I", len(track)) + bytes(track)
    return header + chunk


def _append_varint(buffer: bytearray, value: int) -> None:
    if value < 0:
        raise ValueError("negative delta time")
    encoded = value & 0x7F
    value >>= 7
    while value:
        buffer.append((value & 0x7F) | 0x80)
        value >>= 7
    buffer.append(encoded)


def _derive_sections(
    notes: tuple[LearnNote, ...],
    duration: float,
    time_signature: tuple[int, int] | None,
    tempo_bpm: float | None,
) -> list[dict]:
    """Deterministic practice sections (bar windows when tempo+meter known)."""
    windows: list[tuple[int, int, float, float]] = []
    if (
        time_signature is not None
        and tempo_bpm is not None
        and duration > 0
    ):
        beats_per_bar = time_signature[0] * (4.0 / time_signature[1])
        seconds_per_bar = beats_per_bar * 60.0 / tempo_bpm
        total_bars = int(duration / seconds_per_bar) + 1
        start_bar = 1
        while start_bar <= total_bars:
            end_bar = min(start_bar + 7, total_bars)
            start_time = (start_bar - 1) * seconds_per_bar
            end_time = end_bar * seconds_per_bar
            if end_time <= start_time:
                break
            windows.append((start_bar, end_bar, start_time, min(end_time, duration + 1e-6)))
            start_bar = end_bar + 1
    if not windows:
        if duration <= 0:
            return []
        windows.append((1, 1, 0.0, duration + 1e-6))

    sections: list[dict] = []
    for index, (start_bar, end_bar, start_time, end_time) in enumerate(windows):
        inside = [n for n in notes if start_time <= n.onset < end_time]
        note_count = len(inside)
        rh = sum(1 for n in inside if n.hand == "RH")
        lh = sum(1 for n in inside if n.hand == "LH")
        if note_count == 0:
            density = "empty"
        else:
            span = max(end_time, 1e-9) - start_time
            nps = note_count / span if span > 0 else float(note_count)
            density = "easy" if nps < 2.0 else "medium" if nps < 5.0 else "hard"
        sections.append(
            {
                "section_id": f"section:{index}",
                "label": f"Bars {start_bar}-{end_bar}" if len(windows) > 1 else "Full piece",
                "start_bar": start_bar,
                "end_bar": end_bar,
                "start_time": round(start_time, 6),
                "end_time": round(end_time, 6),
                "note_count": note_count,
                "rh_count": rh,
                "lh_count": lh,
                "difficulty": density,
                "suggested_loop": density != "empty",
                "evidence": "bar_window" if len(windows) > 1 else "single_span",
            }
        )
    return sections


def _assign_sections(
    notes: tuple[LearnNote, ...], sections: list[dict]
) -> list[int]:
    assignment = []
    for note in notes:
        assigned = len(sections) - 1
        for index, section in enumerate(sections):
            if section["start_time"] <= note.onset < section["end_time"]:
                assigned = index
                break
        assignment.append(assigned)
    return assignment


def _sanitize_provenance(value):
    """Fail closed on machine-local paths anywhere in provenance."""
    if isinstance(value, str):
        if "/home/" in value or value.startswith("/"):
            raise ValueError(f"machine-local path in provenance: {value!r}")
        return value
    if isinstance(value, dict):
        return {str(k): _sanitize_provenance(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_sanitize_provenance(v) for v in value]
    return value


def build_package(
    item: LearnItem,
    source_version_id: int,
    level: LevelDecision | None = None,
    metrics: DifficultyMetrics | None = None,
) -> PackageBuild:
    """Build and contract-validate one practice package.

    ``source_version_id`` must be a stable non-negative int for the
    lesson (the ingest assigns them deterministically in catalog order).
    """
    if item.problems:
        raise ValueError(f"cannot build UNSUPPORTED item {item.lesson_key}: {item.problems}")
    if not item.notes:
        raise ValueError(f"cannot build empty item {item.lesson_key}")

    duration = item_duration(item.notes)
    if duration <= 0:
        raise ValueError(f"non-positive duration for {item.lesson_key}")

    tempo = float(item.tempo_bpm) if item.tempo_bpm else _DEFAULT_BPM
    metrics = metrics or compute_metrics(item.notes, tempo_bpm=item.tempo_bpm)
    level = level or evaluate_level_safe(metrics, item.level_hint)

    midi_bytes = render_midi(item.notes, tempo)
    midi_sha = sha256_bytes(midi_bytes)

    sections = _derive_sections(item.notes, duration, item.time_signature, item.tempo_bpm)
    assignment = _assign_sections(item.notes, sections)

    # Exact-onset groups (chords) mirror practice-plan semantics.
    group_ids: dict[float, int] = {}
    events: list[dict] = []
    rh_count = 0
    lh_count = 0
    ordered = sorted(item.notes, key=lambda n: (n.onset, n.midi))
    for note, section_index in zip(ordered, assignment):
        key = round(note.onset, 9)
        if key not in group_ids:
            group_ids[key] = len(group_ids)
        if note.hand not in ("RH", "LH"):
            hand = None
        else:
            hand = note.hand
            if hand == "RH":
                rh_count += 1
            else:
                lh_count += 1
        onset = round(note.onset, 6)
        offset = round(note.offset, 6)
        if offset <= onset:
            offset = onset + 0.001
        bar = None
        beat = None
        if item.time_signature and item.tempo_bpm:
            beats_per_bar = item.time_signature[0] * (4.0 / item.time_signature[1])
            onset_beat = onset * tempo / 60.0
            bar = int(onset_beat / beats_per_bar) + 1
            beat = round(onset_beat % beats_per_bar + 1.0, 4)
        events.append(
            {
                "event_id": len(events),
                "note": int(note.midi),
                "onset": onset,
                "offset": offset,
                "velocity": int(note.velocity),
                "hand": hand,
                "track": int(note.track),
                "bar": bar,
                "beat": beat,
                "section": section_index,
                "group_id": group_ids[key],
                "importance": None,
            }
        )

    hands_availability = item.hands_availability
    if hands_availability not in ("available", "partial", "unknown"):
        hands_availability = "unknown"
    if hands_availability == "available" and (rh_count + lh_count) != len(events):
        # Fail-safe honesty: unassigned notes exist, so hands are partial.
        hands_availability = "partial"
    if hands_availability == "unknown" and (rh_count + lh_count):
        hands_availability = "partial"

    practice = {
        "schema": "practice-events/v1",
        "practice_event_schema_version": 1,
        "package_id": None,  # filled below (needs midi sha first)
        "event_count": len(events),
        "hands_availability": hands_availability,
        "events": events,
    }
    sections_doc = {
        "schema": "practice-sections/v1",
        "section_schema_version": 1,
        "package_id": None,
        "sections": sections,
    }

    required = ["sections", "provenance"]
    if hands_availability in ("available", "partial"):
        required.append("hands")
    required = [c for c in required if c in SUPPORTED_CAPABILITIES]

    lesson_id = lesson_id_for(item.lesson_key)
    provenance = _sanitize_provenance(
        {
            "schema": "practice-provenance/v1",
            "source_system": "midi-curator",
            "producer": "piano-core-learn",
            "lesson_key": item.lesson_key,
            "lesson_id": lesson_id,
            "source": item.source,
            "source_detail": item.source_detail,
            "license": {
                "name": item.license_name,
                "url": item.license_url,
                "attribution": item.attribution,
            },
            "level": level.as_dict(),
            "metrics": metrics.as_dict(),
            "summary": item.summary or "",
        }
    )

    checksums = {
        "piece.mid": midi_sha,
        "practice.json": sha256_bytes(canonical_json(practice)),
        "sections.json": sha256_bytes(canonical_json(sections_doc)),
        "provenance.json": sha256_bytes(canonical_json(provenance)),
    }
    package_id = package_id_for(source_version_id, midi_sha)
    package_digest = package_digest_of(checksums)

    practice["package_id"] = package_id
    sections_doc["package_id"] = package_id
    # checksums for the docs change once package_id is embedded
    checksums["practice.json"] = sha256_bytes(canonical_json(practice))
    checksums["sections.json"] = sha256_bytes(canonical_json(sections_doc))
    package_digest = package_digest_of(checksums)

    manifest = {
        "schema": "practice-package/v1",
        "package_schema_version": PACKAGE_SCHEMA_VERSION,
        "practice_contract_version": PRACTICE_CONTRACT_VERSION,
        "led_semantic_version": LED_SEMANTIC_VERSION,
        "checksums": checksums,
        "package_digest": package_digest,
        "midi_sha256": midi_sha,
        "source_version_id": int(source_version_id),
        "package_id": package_id,
        "title": item.title[:200],
        "artist": item.composer,
        "duration_seconds": round(duration, 6),
        "hands": {
            "availability": hands_availability,
            "rh_count": rh_count,
            "lh_count": lh_count,
        },
        "required_capabilities": required,
        "difficulty": level.difficulty,
        "piano_suitability": 1.0,
        "source_type": item.source,
        "transformation_type": (
            "notation-tokens-to-practice-package"
            if item.source == "pianio"
            else "midi-engraving-to-practice-package"
        ),
        "sections_available": bool(sections),
        "tempo_summary": ([{"bpm": tempo}] if item.tempo_bpm else []),
        "time_signature_summary": (
            [{"beats": item.time_signature[0], "beat_unit": item.time_signature[1]}]
            if item.time_signature
            else []
        ),
        "provenance_summary": {
            "lesson_key": item.lesson_key,
            "lesson_id": lesson_id,
            "license": item.license_name,
            "level": level.level,
        },
    }

    members = {
        "manifest.json": canonical_json(manifest),
        "piece.mid": midi_bytes,
        "practice.json": canonical_json(practice),
        "sections.json": canonical_json(sections_doc),
        "provenance.json": canonical_json(provenance),
    }
    if set(members) != set(MEMBERS):
        raise ValueError("package member set drifted")

    # The production contract parser is the gate: a package that would
    # fail import can never leave the builder.
    parsed: PracticePackage = parse_package(dict(members))
    if parsed.package_id != package_id:
        raise ValueError("package identity drift after parse")

    return PackageBuild(
        members=members,
        package_id=package_id,
        midi_sha256=midi_sha,
        item=item,
        level=level,
        metrics=metrics,
        lesson_id=lesson_id,
    )


def evaluate_level_safe(metrics, level_hint):
    from piano_core.learn.difficulty import evaluate_level

    return evaluate_level(metrics, source_level=level_hint)
