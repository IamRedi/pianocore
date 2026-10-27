"""PIANO-UI-007 canonical fixture generator.

Regenerates the JSON fixtures under tests/weblogic/fixtures/ from the
ACCEPTED canonical pipeline exactly as shipped in this repository:

    P009 canonical timeline (build_timeline order)
      -> UI-005 build_score_projection
      -> UI-006 fingering engine (balanced_v1, default configuration)
      -> the exact API payload serializers (score_payload/fingering_payload)

The fixtures are committed artifacts; tests/weblogic/handmotion.test.mjs
consumes them so the MotionEngine is exercised against real canonical
score/fingering outputs, never a second JS fingering implementation.

Run: PYTHONPATH=src python3 tests/weblogic/fixtures/generate_fixtures.py

Determinism: the same repository revision always regenerates
byte-identical files (canonical engines are deterministic and the JSON
dump uses sort_keys with a fixed trailing newline). This is asserted by
tests/test_ui007_fixture_canonicality.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from piano_core.fingering import generate_plan
from piano_core.fingering.model import FingeringConfig
from piano_core.midi.output_events import note_off, note_on
from piano_core.playback.score import build_score_projection
from piano_core.playback.timeline import Timeline, TimelineEvent
from piano_core.api.serialization import score_payload, fingering_payload

FIXTURE_DIR = Path(__file__).resolve().parent


def build(events, duration):
    # Canonical timelines are time-ordered (stable sort keeps release
    # before attack at equal times where the caller already arranged it).
    ordered = sorted(events, key=lambda event: event.time)
    return Timeline(events=tuple(ordered), duration=duration, source="fixture")


def serialize(timeline, song_id, title):
    projection = build_score_projection(timeline, song_id, title)
    plan = generate_plan(projection, FingeringConfig())
    return {
        "score": score_payload(projection),
        "fingering": fingering_payload(plan),
    }


def fixture_thumb_under_scale():
    """RH two-octave C-major run C4..C6, LH counter-voice.

    The climbing hand center forces the accepted canonical engine to
    re-anchor the thumb (finger 1) at the top of the run — the engine's
    own crossing material (see ADR-017 heuristic; the fixture pins the
    engine's real output, the MotionEngine never refingered anything).
    """
    rh = [60, 62, 64, 65, 67, 69, 71, 72, 74, 76, 77, 79, 81, 84]
    lh = [48, 43, 40, 36]
    events = []
    time = 0.0
    for midi in rh:
        events.append(TimelineEvent(time, note_on(1, midi, 84)))
        events.append(TimelineEvent(time + 0.45, note_off(1, midi, 0)))
        time += 0.5
    time = 0.0
    for midi in lh:
        events.append(TimelineEvent(time, note_on(2, midi, 70)))
        events.append(TimelineEvent(time + 1.4, note_off(2, midi, 0)))
        time += 2.0
    timeline = build(events, 7.5)
    return serialize(timeline, "fixture-thumb-under", "Thumb Under Scale")


def fixture_chords_held_moving():
    """3/4/5-note chords with a held chord plus a moving top finger."""
    events = []
    # 5-note chord, 1s
    for midi in (60, 64, 67, 72, 76):
        events.append(TimelineEvent(0.0, note_on(1, midi, 90)))
    for midi in (60, 64, 67, 72, 76):
        events.append(TimelineEvent(1.0, note_off(1, midi, 0)))
    # Held bass chord with a moving top line (held chord + moving finger)
    for midi in (48, 55):
        events.append(TimelineEvent(1.5, note_on(2, midi, 70)))
    for index, midi in enumerate((76, 79, 81)):
        start = 1.5 + index * 0.5
        events.append(TimelineEvent(start, note_on(1, midi, 84)))
        events.append(TimelineEvent(start + 0.4, note_off(1, midi, 0)))
    for midi in (48, 55):
        events.append(TimelineEvent(3.2, note_off(2, midi, 0)))
    # 3-note and 4-note chords
    for midi in (62, 65, 69):
        events.append(TimelineEvent(3.5, note_on(1, midi, 88)))
    for midi in (62, 65, 69):
        events.append(TimelineEvent(4.3, note_off(1, midi, 0)))
    for midi in (57, 60, 64, 67):
        events.append(TimelineEvent(4.5, note_on(1, midi, 88)))
    for midi in (57, 60, 64, 67):
        events.append(TimelineEvent(5.4, note_off(1, midi, 0)))
    timeline = build(events, 6.0)
    return serialize(timeline, "fixture-chords", "Chords Held Moving")


def fixture_bass_melody():
    """LH bass with RH melody, a large jump, and fast repetition."""
    events = []
    bass = [(36, 0.0, 1.6), (43, 2.0, 1.6), (36, 4.0, 1.6), (45, 6.0, 1.6)]
    for midi, start, length in bass:
        events.append(TimelineEvent(start, note_on(2, midi, 76)))
        events.append(TimelineEvent(start + length, note_off(2, midi, 0)))
    melody = [(72, 8.0), (76, 8.5), (79, 9.0), (72, 9.5), (84, 10.0), (79, 10.5)]
    for midi, start in melody:
        events.append(TimelineEvent(start, note_on(1, midi, 88)))
        events.append(TimelineEvent(start + 0.4, note_off(1, midi, 0)))
    # Fast repetition on one key (release + fresh attack, canonical order)
    for index in range(6):
        start = 11.0 + index * 0.2
        events.append(TimelineEvent(start, note_on(1, 76, 80)))
        events.append(TimelineEvent(start + 0.15, note_off(1, 76, 0)))
    timeline = build(events, 13.0)
    return serialize(timeline, "fixture-bass-melody", "Bass Melody Jump Repeat")


def main():
    fixtures = {
        "thumb_under_scale.json": fixture_thumb_under_scale(),
        "chords_held_moving.json": fixture_chords_held_moving(),
        "bass_melody.json": fixture_bass_melody(),
    }
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, payload in fixtures.items():
        path = FIXTURE_DIR / name
        text = json.dumps(payload, indent=1, sort_keys=True) + "\n"
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path} ({len(text)} bytes)")


if __name__ == "__main__":
    main()
