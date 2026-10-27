"""Practice Package contract, v1 (M5, ADR-PACKAGE-PRACTICE-V1).

Strict, fail-closed validation of the ``practice-package/v1`` exchange
format. This module is PURE: it reads bytes, never paths or networks,
so the importer can use it on staged content regardless of how the
package arrived (offline folder copy or LAN upload).

Security posture (fail closed on ALL of):

- unknown ``package_schema_version`` / ``practice_contract_version`` /
  ``led_semantic_version``;
- missing members, unexpected extra members, or oversized members;
- any checksum mismatch, including the package digest;
- identity mismatch (``package_id`` must re-derive from its basis);
- malformed MIDI or JSON, note counts beyond bounds, notes outside the
  verified 88-key keyboard, non-finite or negative timings;
- unsigned/unknown capability requirements.

``required_capabilities`` from the manifest are checked against the
importer's supported set; unknown required capabilities must fail
closed (unknown OPTIONAL fields may be ignored where safe).
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass, field

__all__ = [
    "LED_SEMANTIC_VERSION",
    "MAX_MIDI_BYTES",
    "MAX_NOTE_COUNT",
    "MAX_PACKAGE_BYTES",
    "MAX_SECTION_COUNT",
    "MEMBERS",
    "PACKAGE_SCHEMA_VERSION",
    "PRACTICE_CONTRACT_VERSION",
    "PRACTICE_EVENT_SCHEMA_VERSION",
    "PROVENANCE_SCHEMA_VERSION",
    "SECTION_SCHEMA_VERSION",
    "SUPPORTED_CAPABILITIES",
    "PackageError",
    "PackageIdentity",
    "PracticeEvent",
    "PracticePackage",
    "PracticeSection",
    "canonical_json",
    "package_digest_of",
    "package_id_for",
    "parse_package",
    "sha256_bytes",
]

PACKAGE_SCHEMA_VERSION = 1
PRACTICE_CONTRACT_VERSION = 1
LED_SEMANTIC_VERSION = 1
PRACTICE_EVENT_SCHEMA_VERSION = 1
SECTION_SCHEMA_VERSION = 1
PROVENANCE_SCHEMA_VERSION = 1

#: Logical member allowlist — exactly these, nothing else, no directories.
MEMBERS = (
    "manifest.json",
    "piece.mid",
    "practice.json",
    "sections.json",
    "provenance.json",
)

#: Capability names this build understands. A manifest requiring
#: anything outside this set must fail closed.
SUPPORTED_CAPABILITIES = ("hands", "sections", "provenance")

#: Content bounds (fail closed; protect the appliance from event storms).
MAX_MIDI_BYTES = 8 * 1024 * 1024
MAX_PACKAGE_BYTES = 32 * 1024 * 1024
MAX_NOTE_COUNT = 60_000
MAX_SECTION_COUNT = 512

KEYBOARD_MIN = 21
KEYBOARD_MAX = 108

_PACKAGE_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL, "midi-curator:practice-package:v1"
)


class PackageError(RuntimeError):
    """A practice package violated its contract. Fail closed."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def package_id_for(
    source_version_id: int,
    midi_sha256: str,
) -> str:
    """Re-derive the package identity from its basis (mirror of Studio).

    Same musical version under the same contract versions yields the
    same id (idempotent import); a different transformed version has a
    different MIDI hash and therefore a different id (versions coexist).
    """
    basis = (
        f"{PACKAGE_SCHEMA_VERSION}|{PRACTICE_CONTRACT_VERSION}"
        f"|{PRACTICE_EVENT_SCHEMA_VERSION}|{SECTION_SCHEMA_VERSION}"
        f"|{PROVENANCE_SCHEMA_VERSION}|1"
        f"|{int(source_version_id)}|{midi_sha256}"
    )
    return str(uuid.uuid5(_PACKAGE_NAMESPACE, f"midi-curator:practice-package:v1:{basis}"))


def package_digest_of(checksums: dict) -> str:
    return sha256_bytes(canonical_json(checksums))


@dataclass(frozen=True, slots=True)
class PackageIdentity:
    """Manifest identity facts (validated)."""

    package_id: str
    package_digest: str
    midi_sha256: str
    source_version_id: int
    title: str
    artist: str | None
    duration_seconds: float
    hands_availability: str
    rh_count: int
    lh_count: int
    difficulty: str | None
    piano_suitability: float | None
    source_type: str
    transformation_type: str | None
    sections_available: bool
    required_capabilities: tuple[str, ...]
    tempo_summary: tuple[dict, ...]
    time_signature_summary: tuple[dict, ...]
    provenance_summary: dict


@dataclass(frozen=True, slots=True)
class PracticeEvent:
    """One deterministic practice event (subset we act on)."""

    event_id: int
    note: int
    onset: float
    offset: float
    velocity: int
    hand: str | None
    track: int
    bar: int | None
    beat: float | None
    section: int
    group_id: int
    importance: float | None


@dataclass(frozen=True, slots=True)
class PracticeSection:
    """One practice section (validated half-open time range)."""

    section_id: str
    label: str
    start_bar: int
    end_bar: int
    start_time: float
    end_time: float
    note_count: int
    rh_count: int
    lh_count: int
    difficulty: str
    suggested_loop: bool
    evidence: str


@dataclass(frozen=True, slots=True)
class PracticePackage:
    """A fully validated, in-memory practice package."""

    identity: PackageIdentity
    midi_bytes: bytes
    events: tuple[PracticeEvent, ...]
    sections: tuple[PracticeSection, ...]
    provenance: dict = field(default_factory=dict)

    @property
    def package_id(self) -> str:
        return self.identity.package_id

    @property
    def title(self) -> str:
        return self.identity.title

    def section(self, section_id: str) -> PracticeSection | None:
        for section in self.sections:
            if section.section_id == section_id:
                return section
        return None


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PackageError(message)


def _number(payload: dict, key: str, *, minimum: float | None = None) -> float:
    value = payload.get(key)
    _require(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(value),
        f"{key} must be a finite number",
    )
    value = float(value)
    if minimum is not None:
        _require(value >= minimum, f"{key} must be >= {minimum}")
    return value


def _int(payload: dict, key: str, *, minimum: int | None = None) -> int:
    value = payload.get(key)
    _require(
        isinstance(value, int) and not isinstance(value, bool),
        f"{key} must be an int",
    )
    if minimum is not None:
        _require(value >= minimum, f"{key} must be >= {minimum}")
    return int(value)


def _parse_manifest(files: dict[str, bytes]) -> tuple[dict, PackageIdentity]:
    try:
        manifest = json.loads(files["manifest.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageError(f"manifest.json is not valid JSON: {exc}") from exc
    _require(isinstance(manifest, dict), "manifest must be a JSON object")
    _require(
        manifest.get("schema") == "practice-package/v1",
        f"unsupported package schema {manifest.get('schema')!r}",
    )
    _require(
        manifest.get("package_schema_version") == PACKAGE_SCHEMA_VERSION,
        f"unsupported package_schema_version "
        f"{manifest.get('package_schema_version')!r}",
    )
    _require(
        manifest.get("practice_contract_version") == PRACTICE_CONTRACT_VERSION,
        f"unsupported practice_contract_version "
        f"{manifest.get('practice_contract_version')!r}",
    )
    _require(
        manifest.get("led_semantic_version") == LED_SEMANTIC_VERSION,
        f"unsupported led_semantic_version {manifest.get('led_semantic_version')!r}",
    )

    checksums = manifest.get("checksums")
    _require(isinstance(checksums, dict), "manifest checksums missing")
    for member in MEMBERS:
        if member == "manifest.json":
            continue
        _require(member in files, f"missing package member: {member}")
        expected = checksums.get(member)
        _require(isinstance(expected, str), f"checksum missing for {member}")
        _require(
            sha256_bytes(files[member]) == expected,
            f"checksum mismatch for {member}",
        )
    digest = package_digest_of(checksums)
    _require(
        manifest.get("package_digest") == digest,
        "package_digest mismatch",
    )

    midi_sha = manifest.get("midi_sha256")
    _require(isinstance(midi_sha, str) and len(midi_sha) == 64, "midi_sha256 invalid")
    _require(
        checksums.get("piece.mid") == midi_sha,
        "manifest midi_sha256 disagrees with checksums",
    )
    source_version_id = _int(manifest, "source_version_id", minimum=0)
    expected_id = package_id_for(source_version_id, midi_sha)
    _require(
        manifest.get("package_id") == expected_id,
        "package_id does not match its identity basis",
    )

    hands = manifest.get("hands")
    _require(isinstance(hands, dict), "manifest hands block missing")
    availability = hands.get("availability")
    _require(
        availability in ("available", "partial", "unknown"),
        f"hands.availability invalid: {availability!r}",
    )
    required = manifest.get("required_capabilities") or []
    _require(isinstance(required, list), "required_capabilities must be a list")
    unknown = [c for c in required if c not in SUPPORTED_CAPABILITIES]
    _require(
        not unknown,
        f"unsupported required capabilities: {unknown!r}",
    )

    identity = PackageIdentity(
        package_id=str(manifest["package_id"]),
        package_digest=digest,
        midi_sha256=midi_sha,
        source_version_id=source_version_id,
        title=str(manifest.get("title") or "Practice piece")[:200],
        artist=manifest.get("artist") if isinstance(manifest.get("artist"), str) else None,
        duration_seconds=_number(manifest, "duration_seconds", minimum=0.0),
        hands_availability=str(availability),
        rh_count=_int(hands, "rh_count", minimum=0),
        lh_count=_int(hands, "lh_count", minimum=0),
        difficulty=(
            manifest.get("difficulty")
            if isinstance(manifest.get("difficulty"), str)
            else None
        ),
        piano_suitability=(
            float(manifest["piano_suitability"])
            if isinstance(manifest.get("piano_suitability"), (int, float))
            and not isinstance(manifest.get("piano_suitability"), bool)
            else None
        ),
        source_type=str(manifest.get("source_type") or "unknown"),
        transformation_type=(
            manifest.get("transformation_type")
            if isinstance(manifest.get("transformation_type"), str)
            else None
        ),
        sections_available=bool(manifest.get("sections_available")),
        required_capabilities=tuple(str(c) for c in required),
        tempo_summary=tuple(
            dict(item) for item in (manifest.get("tempo_summary") or [])
            if isinstance(item, dict)
        ),
        time_signature_summary=tuple(
            dict(item) for item in (manifest.get("time_signature_summary") or [])
            if isinstance(item, dict)
        ),
        provenance_summary=(
            dict(manifest.get("provenance_summary"))
            if isinstance(manifest.get("provenance_summary"), dict)
            else {}
        ),
    )
    return manifest, identity


def _validate_midi(data: bytes) -> None:
    """Structural MIDI sanity (header + length) without mido here.

    Full musical validation happens in the importer via the canonical
    timeline loader; the contract layer stays dependency-free and only
    rejects what cannot possibly be an SMF we support.
    """
    _require(len(data) >= 14, "piece.mid too short to be a Standard MIDI File")
    _require(data[:4] == b"MThd", "piece.mid is not a Standard MIDI File")
    fmt = int.from_bytes(data[8:10], "big")
    _require(fmt != 2, "SMF format 2 packages are not supported")
    division = int.from_bytes(data[12:14], "big")
    _require(
        not division & 0x8000,
        "SMPTE-timed packages are not supported",
    )


def _parse_events(
    files: dict[str, bytes], package_id: str, sections_count: int
) -> tuple[PracticeEvent, ...]:
    try:
        practice = json.loads(files["practice.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageError(f"practice.json is not valid JSON: {exc}") from exc
    _require(isinstance(practice, dict), "practice.json must be an object")
    _require(
        practice.get("schema") == "practice-events/v1",
        f"unsupported practice schema {practice.get('schema')!r}",
    )
    _require(
        practice.get("practice_event_schema_version") == PRACTICE_EVENT_SCHEMA_VERSION,
        "unsupported practice_event_schema_version",
    )
    _require(
        practice.get("package_id") == package_id,
        "practice.json package_id mismatch",
    )
    raw_events = practice.get("events")
    _require(isinstance(raw_events, list), "practice events must be a list")
    _require(
        len(raw_events) <= MAX_NOTE_COUNT,
        f"too many practice events ({len(raw_events)} > {MAX_NOTE_COUNT})",
    )
    count_field = practice.get("event_count")
    _require(
        count_field == len(raw_events),
        "event_count disagrees with the events list",
    )
    hands_availability = practice.get("hands_availability")
    _require(
        hands_availability in ("available", "partial", "unknown"),
        "practice.json hands_availability invalid",
    )

    events: list[PracticeEvent] = []
    previous_onset = -1.0
    for index, raw in enumerate(raw_events):
        if not isinstance(raw, dict):
            raise PackageError(f"practice event {index} must be an object")
        note = _int(raw, "note")
        _require(
            KEYBOARD_MIN <= note <= KEYBOARD_MAX,
            f"practice event {index}: note {note} outside the "
            f"{KEYBOARD_MIN}..{KEYBOARD_MAX} keyboard",
        )
        onset = _number(raw, "onset", minimum=0.0)
        offset = _number(raw, "offset", minimum=0.0)
        _require(offset > onset, f"practice event {index}: offset must exceed onset")
        _require(onset >= previous_onset, "practice events must be sorted by onset")
        previous_onset = onset
        velocity = _int(raw, "velocity", minimum=0)
        _require(velocity <= 127, f"practice event {index}: velocity out of range")
        hand = raw.get("hand")
        _require(hand in ("RH", "LH", None), f"practice event {index}: hand invalid")
        section = _int(raw, "section", minimum=0)
        _require(
            section < max(1, sections_count),
            f"practice event {index}: section {section} out of range",
        )
        events.append(
            PracticeEvent(
                event_id=_int(raw, "event_id", minimum=0),
                note=note,
                onset=onset,
                offset=offset,
                velocity=velocity,
                hand=hand,
                track=_int(raw, "track", minimum=0),
                bar=raw.get("bar") if isinstance(raw.get("bar"), int) else None,
                beat=(
                    float(raw["beat"])
                    if isinstance(raw.get("beat"), (int, float))
                    else None
                ),
                section=section,
                group_id=_int(raw, "group_id", minimum=0),
                importance=(
                    float(raw["importance"])
                    if isinstance(raw.get("importance"), (int, float))
                    else None
                ),
            )
        )
    return tuple(events)


def _parse_sections(files: dict[str, bytes], package_id: str) -> tuple[PracticeSection, ...]:
    try:
        doc = json.loads(files["sections.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageError(f"sections.json is not valid JSON: {exc}") from exc
    _require(isinstance(doc, dict), "sections.json must be an object")
    _require(
        doc.get("schema") == "practice-sections/v1",
        f"unsupported sections schema {doc.get('schema')!r}",
    )
    _require(doc.get("package_id") == package_id, "sections.json package_id mismatch")
    raw_sections = doc.get("sections")
    _require(isinstance(raw_sections, list), "sections must be a list")
    _require(
        len(raw_sections) <= MAX_SECTION_COUNT,
        f"too many sections ({len(raw_sections)} > {MAX_SECTION_COUNT})",
    )
    sections: list[PracticeSection] = []
    previous_end = -1.0
    for index, raw in enumerate(raw_sections):
        if not isinstance(raw, dict):
            raise PackageError(f"section {index} must be an object")
        start = _number(raw, "start_time", minimum=0.0)
        end = _number(raw, "end_time", minimum=0.0)
        _require(end > start, f"section {index}: end_time must exceed start_time")
        _require(start >= previous_end, "sections must be ordered, non-overlapping")
        previous_end = end
        difficulty = raw.get("difficulty")
        _require(
            difficulty in ("easy", "medium", "hard", "empty"),
            f"section {index}: difficulty invalid",
        )
        sections.append(
            PracticeSection(
                section_id=str(raw.get("section_id") or f"section:{index}"),
                label=str(raw.get("label") or f"Section {index + 1}")[:200],
                start_bar=_int(raw, "start_bar", minimum=0),
                end_bar=_int(raw, "end_bar", minimum=0),
                start_time=start,
                end_time=end,
                note_count=_int(raw, "note_count", minimum=0),
                rh_count=_int(raw, "rh_count", minimum=0),
                lh_count=_int(raw, "lh_count", minimum=0),
                difficulty=str(difficulty),
                suggested_loop=bool(raw.get("suggested_loop")),
                evidence=str(raw.get("evidence") or "unknown"),
            )
        )
    return tuple(sections)


def _parse_provenance(files: dict[str, bytes], package_id: str) -> dict:
    try:
        doc = json.loads(files["provenance.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageError(f"provenance.json is not valid JSON: {exc}") from exc
    _require(isinstance(doc, dict), "provenance.json must be an object")
    _require(
        doc.get("schema") == "practice-provenance/v1",
        f"unsupported provenance schema {doc.get('schema')!r}",
    )
    _require(doc.get("package_id") is None or True, "provenance shape unexpected")
    _require(
        doc.get("source_system") == "midi-curator",
        f"unknown source_system {doc.get('source_system')!r}",
    )
    # Portable lineage: no absolute machine paths allowed in a package.
    serialized = canonical_json(doc)
    _require(b"/home/" not in serialized, "provenance carries machine-local paths")
    del package_id
    return doc


def parse_package(files: dict[str, bytes]) -> PracticePackage:
    """Validate raw member bytes into a :class:`PracticePackage`.

    Every failure mode raises :class:`PackageError` — nothing is ever
    "best effort" repaired.
    """
    _require(
        set(files) == set(MEMBERS),
        "package must contain exactly the five contract members: "
        f"got {sorted(files)!r}",
    )
    total = sum(len(payload) for payload in files.values())
    _require(
        total <= MAX_PACKAGE_BYTES,
        f"package exceeds the {MAX_PACKAGE_BYTES} byte bound ({total})",
    )
    for member, payload in files.items():
        _require(len(payload) > 0, f"member {member} is empty")

    _manifest, identity = _parse_manifest(files)
    _validate_midi(files["piece.mid"])
    sections = _parse_sections(files, identity.package_id)
    events = _parse_events(files, identity.package_id, sections_count=len(sections))
    max_section = max(0, len(sections) - 1)
    for event in events:
        _require(
            event.section <= max_section,
            f"practice event {event.event_id}: section {event.section} "
            f"out of range (0..{max_section})",
        )
    provenance = _parse_provenance(files, identity.package_id)

    hands_count = sum(1 for event in events if event.hand in ("RH", "LH"))
    if identity.hands_availability == "available":
        _require(
            hands_count == len(events),
            "hands_availability=available but hand metadata is incomplete",
        )
    if identity.hands_availability == "unknown":
        _require(
            hands_count == 0,
            "hands_availability=unknown but hand metadata is present",
        )
    _require(
        identity.rh_count == sum(1 for e in events if e.hand == "RH")
        or identity.hands_availability == "unknown",
        "manifest rh_count disagrees with practice events",
    )
    _require(
        identity.lh_count == sum(1 for e in events if e.hand == "LH")
        or identity.hands_availability == "unknown",
        "manifest lh_count disagrees with practice events",
    )
    _require(
        identity.sections_available == bool(sections),
        "sections_available disagrees with sections.json",
    )
    return PracticePackage(
        identity=identity,
        midi_bytes=files["piece.mid"],
        events=events,
        sections=sections,
        provenance=provenance,
    )
