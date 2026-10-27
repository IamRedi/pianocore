"""Pianio source adapter (Source A, PIANO-LEARN-001).

Converts the Pianio open curriculum (CC0-1.0, github.com/ismaelponce/
pianio) into canonical Learn items.

The token grammar and tie semantics are a faithful port of the upstream
reference parser (``scripts/generate-content.mjs`` at the pinned commit
recorded in the provenance evidence):

- single notes ``C4:q`` — optional ``#``/``b`` accidental, durations
  ``w``/``h``/``q``/``e`` plus dotted ``dh``/``dq``/``de``, optional
  trailing ``~`` tie marker;
- chords ``[C4,E4,G4]:h`` — a chord token flushes any pending tie chain
  (chords are never tied upstream);
- rests ``R:q`` advance time and flush tie chains;
- ties: a ``~``-marked token starts or continues a chain on the same
  pitch; the accumulated duration is emitted when the chain ends;
- grand staff: ``measures`` is the right-hand staff, ``measuresLeft``
  the optional left-hand staff (its presence implies per-note hand
  evidence regardless of the exercise ``hands`` label);
- dynamics (measure/beat -> marking) map to deterministic velocities;
  pedal events and the swing flag are preserved as provenance metadata
  (practice-package/v1 has no pedal/swing event surface; nothing is
  silently dropped or fabricated).

Anything the grammar cannot represent exactly classifies the exercise
UNSUPPORTED via ``LearnItem.problems`` with the exact reason.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator

from piano_core.learn.model import LearnItem, LearnNote

__all__ = ["iter_pianio_items", "PIANIO_SOURCE"]

PIANIO_SOURCE = "pianio"

_TOKEN_RE = re.compile(r"^([A-GR])([#b]?)(\d)?:(w|h|q|e|dh|dq|de)(~?)$")
_CHORD_RE = re.compile(r"^\[([^\]]+)\]:(w|h|q|e|dh|dq|de)(~?)$")
_NOTE_RE = re.compile(r"^([A-G])([#b]?)(\d)$")

#: Beats (quarter = 1) per duration token.
_DUR_BEATS = {"w": 4.0, "h": 2.0, "q": 1.0, "e": 0.5, "dh": 3.0, "dq": 1.5, "de": 0.75}

_PITCH = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

#: Deterministic dynamic-marking velocities.
_DYNAMIC_VELOCITY = {"pp": 33, "p": 49, "mp": 64, "mf": 80, "f": 96, "ff": 112}
_DEFAULT_VELOCITY = 80

KEYBOARD_MIN = 21
KEYBOARD_MAX = 108

#: Pianio course levels (1..6) mapped onto the Learn 0..4 curriculum.
_PIANIO_LEVEL_MAP = {1: 0, 2: 1, 3: 2, 4: 3, 5: 3, 6: 4}

_KEY_NAMES = {
    -7: "Cb", -6: "Gb", -5: "Db", -4: "Ab", -3: "Eb", -2: "Bb", -1: "F",
    0: "C", 1: "G", 2: "D", 3: "A", 4: "E", 5: "B", 6: "F#", 7: "C#",
}


def _midi_number(step: str, accidental: str, octave: int) -> int:
    return (
        (octave + 1) * 12
        + _PITCH[step]
        + (1 if accidental == "#" else -1 if accidental == "b" else 0)
    )


def _parse_staff(
    measures: list,
    hand: str | None,
) -> tuple[list[tuple[float, float, int]], float, tuple[str, ...]]:
    """Parse one staff into beat-unit notes ``[(onset, offset, midi)]``.

    Returns ``(notes, total_beats, problems)``. The tie semantics follow
    the upstream reference parser exactly.
    """
    notes: list[tuple[float, float, int]] = []
    problems: list[str] = []
    start_beat = 0.0
    tie: dict | None = None

    def flush_tie() -> None:
        nonlocal tie
        if tie is not None:
            notes.append((tie["onset"], tie["onset"] + tie["beats"], tie["midi"]))
            tie = None

    for measure in measures:
        if not isinstance(measure, list):
            raise ValueError("measure must be a list of tokens")
        for raw in measure:
            token = str(raw)

            chord = _CHORD_RE.match(token)
            if chord:
                note_list, duration, tilde = chord.groups()
                beats = _DUR_BEATS[duration]
                if tilde:
                    problems.append(f"tied chord token {token!r} is not representable")
                flush_tie()
                for part in note_list.split(","):
                    m = _NOTE_RE.match(part.strip())
                    if not m:
                        problems.append(f"unparseable chord tone {part!r} in {token!r}")
                        continue
                    midi = _midi_number(m.group(1), m.group(2), int(m.group(3)))
                    if not (KEYBOARD_MIN <= midi <= KEYBOARD_MAX):
                        problems.append(
                            f"chord tone {midi} outside 88-key range in {token!r}"
                        )
                        continue
                    notes.append((start_beat, start_beat + beats, midi))
                start_beat += beats
                continue

            single = _TOKEN_RE.match(token)
            if not single:
                problems.append(f"unparseable token {token!r}")
                continue
            step, accidental, octave_s, duration, tilde = single.groups()
            beats = _DUR_BEATS[duration]

            if step == "R":
                flush_tie()
                start_beat += beats
                continue

            if octave_s is None:
                problems.append(f"token {token!r} has no octave")
                start_beat += beats
                continue
            midi = _midi_number(step, accidental, int(octave_s))
            if not (KEYBOARD_MIN <= midi <= KEYBOARD_MAX):
                problems.append(f"note {midi} outside 88-key range in {token!r}")
                start_beat += beats
                continue

            if tilde:
                if tie is not None and tie["midi"] == midi:
                    tie["beats"] += beats
                elif tie is not None:
                    flush_tie()
                    tie = {"onset": start_beat, "beats": beats, "midi": midi}
                else:
                    tie = {"onset": start_beat, "beats": beats, "midi": midi}
            else:
                if tie is not None:
                    if tie["midi"] == midi:
                        tie["beats"] += beats
                        flush_tie()
                    else:
                        flush_tie()
                        notes.append((start_beat, start_beat + beats, midi))
                else:
                    notes.append((start_beat, start_beat + beats, midi))
            start_beat += beats

    flush_tie()
    return notes, start_beat, tuple(problems)


def _dynamic_velocity(
    dynamics: list, measure_units: float, beat_unit: float, onset_beat: float
) -> int:
    """Active dynamic marking at ``onset_beat`` -> deterministic velocity."""
    active = None
    for d in dynamics:
        if not isinstance(d, dict):
            continue
        measure = d.get("measure")
        if not isinstance(measure, int) or isinstance(measure, bool) or measure < 1:
            continue
        beat = d.get("beat", 1)
        if not isinstance(beat, (int, float)) or isinstance(beat, bool):
            continue
        absolute = (measure - 1) * measure_units + (float(beat) - 1) * beat_unit
        if absolute <= onset_beat + 1e-9:
            marking = d.get("marking")
            if marking in _DYNAMIC_VELOCITY:
                active = _DYNAMIC_VELOCITY[marking]
    return active if active is not None else _DEFAULT_VELOCITY


def _collect_pedal(
    pedal: list, measure_units: float, beat_unit: float
) -> tuple[tuple[float, str], ...]:
    if not pedal:
        return ()
    events: list[tuple[float, str]] = []
    for p in pedal:
        if not isinstance(p, dict):
            continue
        measure = p.get("measure")
        beat = p.get("beat")
        if not isinstance(measure, int) or isinstance(measure, bool):
            continue
        if not isinstance(beat, (int, float)) or isinstance(beat, bool):
            continue
        kind = p.get("type")
        if kind not in ("down", "up"):
            continue
        absolute = (measure - 1) * measure_units + (float(beat) - 1) * beat_unit
        events.append((absolute, str(kind)))
    events.sort()
    return tuple(events)


def _key_signature_name(value) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool) and value in _KEY_NAMES:
        return _KEY_NAMES[value]
    return None


def iter_pianio_items(
    content_dir: str | Path,
    upstream_commit: str | None = None,
) -> Iterator[LearnItem]:
    """Yield one canonical LearnItem per Pianio exercise.

    Parse failures, range violations, measure-math mismatches and any
    non-CC0 license classify the exercise UNSUPPORTED (never repaired).
    """
    root = Path(content_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"Pianio content directory not found: {root}")
    course_files = sorted(root.glob("*/*/course.json"))
    if not course_files:
        raise FileNotFoundError(f"no course.json files under {root}")

    for course_path in course_files:
        raw = json.loads(course_path.read_text(encoding="utf-8"))
        course_id = str(raw.get("courseId") or course_path.parent.name)
        course_level = raw.get("level")
        for exercise in raw.get("exercises", []):
            if not isinstance(exercise, dict):
                continue
            exercise_id = str(exercise.get("id") or "")
            if not exercise_id:
                continue
            lesson_key = f"pianio:{course_id}:{exercise_id}"
            problems: list[str] = []

            tempo = exercise.get("tempoBpm")
            if (
                not isinstance(tempo, (int, float))
                or isinstance(tempo, bool)
                or tempo <= 0
            ):
                problems.append(f"invalid tempoBpm {tempo!r}")
                tempo = None
            time_sig = exercise.get("timeSignature")
            if (
                not isinstance(time_sig, list)
                or len(time_sig) != 2
                or not all(
                    isinstance(v, int) and not isinstance(v, bool) and v > 0
                    for v in time_sig
                )
            ):
                problems.append(f"invalid timeSignature {time_sig!r}")
                time_sig = None
            measure_units = time_sig[0] * (4.0 / time_sig[1]) if time_sig else 4.0
            beat_unit = 4.0 / time_sig[1] if time_sig else 1.0

            measures = exercise.get("measures")
            measures_left = exercise.get("measuresLeft")
            hands_label = exercise.get("hands")
            hands_availability = "unknown"

            try:
                if measures_left:
                    rh_notes, rh_total, rh_problems = _parse_staff(measures or [], "RH")
                    lh_notes, lh_total, lh_problems = _parse_staff(measures_left, "LH")
                    beat_notes: list[tuple[float, float, int, str | None]] = [
                        (n[0], n[1], n[2], "RH") for n in rh_notes
                    ] + [(n[0], n[1], n[2], "LH") for n in lh_notes]
                    beat_notes.sort(key=lambda n: (n[0], 0 if n[3] == "RH" else 1, n[2]))
                    total_beats = max(rh_total, lh_total)
                    problems.extend(rh_problems)
                    problems.extend(lh_problems)
                    hands_availability = "available"
                    if abs(rh_total - len(measures or []) * measure_units) > 1e-6:
                        problems.append(
                            f"right-hand measures sum to {rh_total} beats, "
                            f"expected {len(measures or []) * measure_units}"
                        )
                    if abs(lh_total - len(measures_left) * measure_units) > 1e-6:
                        problems.append(
                            f"left-hand measures sum to {lh_total} beats, "
                            f"expected {len(measures_left) * measure_units}"
                        )
                else:
                    if hands_label == "left":
                        hand: str | None = "LH"
                    elif hands_label == "right":
                        hand = "RH"
                    else:
                        # "together" on a single staff cannot prove per-note
                        # hand truth: leave hands unassigned (honesty rule).
                        hand = None
                    parsed, total_beats, staff_problems = _parse_staff(
                        measures or [], hand
                    )
                    beat_notes = [(n[0], n[1], n[2], hand) for n in parsed]
                    problems.extend(staff_problems)
                    if hand is None:
                        hands_availability = (
                            "partial" if hands_label == "together" else "unknown"
                        )
                    else:
                        hands_availability = "available"
                        if abs(total_beats - len(measures or []) * measure_units) > 1e-6:
                            problems.append(
                                f"measures sum to {total_beats} beats, expected "
                                f"{len(measures or []) * measure_units}"
                            )
            except ValueError as exc:
                problems.append(f"staff parse failure: {exc}")
                beat_notes = []
                total_beats = 0.0

            if tempo:
                seconds_per_beat = 60.0 / float(tempo)
                dynamics = exercise.get("dynamics") or []
                notes = tuple(
                    LearnNote(
                        onset=round(n[0] * seconds_per_beat, 6),
                        offset=round(n[1] * seconds_per_beat, 6),
                        midi=n[2],
                        velocity=(
                            _dynamic_velocity(dynamics, measure_units, beat_unit, n[0])
                            if dynamics
                            else _DEFAULT_VELOCITY
                        ),
                        hand=n[3],
                        track=0,
                    )
                    for n in beat_notes
                )
                pedal = tuple(
                    (round(t * seconds_per_beat, 6), kind)
                    for t, kind in _collect_pedal(
                        exercise.get("pedal"), measure_units, beat_unit
                    )
                )
            else:
                notes = tuple(
                    LearnNote(
                        onset=n[0], offset=n[1], midi=n[2], velocity=0, hand=n[3], track=0
                    )
                    for n in beat_notes
                )
                pedal = ()

            if not notes:
                problems.append("exercise produced no notes")

            level_hint = (
                _PIANIO_LEVEL_MAP.get(course_level)
                if isinstance(course_level, int) and not isinstance(course_level, bool)
                else None
            )
            license_block = exercise.get("license") or {}
            license_type = str(license_block.get("type") or "unknown")
            if license_type != "CC0-1.0":
                problems.append(
                    f"unexpected source license {license_type!r} (CC0-1.0 required)"
                )

            yield LearnItem(
                lesson_key=lesson_key,
                title=str(exercise.get("title") or exercise_id),
                composer=None,
                source=PIANIO_SOURCE,
                source_detail={
                    "course_id": course_id,
                    "course_title": str(raw.get("title") or course_id),
                    "course_level": (
                        course_level
                        if isinstance(course_level, int) and not isinstance(course_level, bool)
                        else None
                    ),
                    "course_summary": str(raw.get("summary") or ""),
                    "exercise_id": exercise_id,
                    "slug": str(exercise.get("slug") or ""),
                    "clef": str(exercise.get("clef") or ""),
                    "source_kind": str(exercise.get("sourceKind") or "unknown"),
                    "upstream_commit": upstream_commit or "unknown",
                    "goal": str(exercise.get("goal") or ""),
                    "pedal_events": [[t, kind] for t, kind in pedal],
                },
                license_name=license_type,
                license_url="https://creativecommons.org/publicdomain/zero/1.0/",
                attribution=str(
                    license_block.get("attribution")
                    or "Pianio open curriculum (CC0-1.0)"
                ),
                notes=notes,
                tempo_bpm=float(tempo) if tempo else None,
                time_signature=tuple(time_sig) if time_sig else None,
                key_signature=_key_signature_name(exercise.get("keySignature")),
                swing=bool(exercise.get("swing", False)),
                pedal_events=pedal,
                level_hint=level_hint,
                hands_availability=hands_availability,
                summary=str(exercise.get("summary") or ""),
                problems=tuple(problems),
            )
