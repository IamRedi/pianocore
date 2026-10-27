"""Practice plan derivation (PIANO-P015, ADR-PRACTICE-V1 D2).

Projects the canonical P009 :class:`~piano_core.playback.timeline.Timeline`
into a deterministic sequence of human play targets. This module is the
ONLY place practice touches MIDI semantics, and it invents NO parsing of
its own: the input is an already-built canonical timeline.

Projection rules (ADR D2, all tested):

- only NOTE_ON commands (velocity >= 1 by construction) create targets;
  NOTE_OFF / sustain / tempo markers never do;
- the GM percussion channel (canonical channel 10) is NEVER a learning
  target — drum hits must not become keyboard-learning steps;
- notes outside the verified physical keyboard (MIDI 21..108) are
  skipped;
- notes at the SAME canonical onset form ONE target (a chord): events at
  the same absolute tick always carry the identical accumulated timeline
  time, so multi-track simultaneous piano notes merge and duplicate
  same-note attacks at one onset deduplicate (a physical key can never
  be required twice at once);
- targets are ordered by onset ascending; notes within a target are
  sorted ascending. Exact-onset grouping is used deliberately — no
  fuzzy/tolerance heuristics (ADR D2).

Timing never schedules anything: practice is self-paced and the source
onset is metadata only.
"""

from __future__ import annotations

from piano_core.midi.output_events import OutputCommandType
from piano_core.playback.timeline import Timeline
from piano_core.practice.model import (
    KEYBOARD_MAX,
    KEYBOARD_MIN,
    PERCUSSION_CHANNEL,
    PracticePlan,
    PracticeSection,
    PracticeTarget,
)

__all__ = ["build_practice_plan", "select_practice_section"]


def build_practice_plan(timeline: Timeline) -> PracticePlan:
    """Derive the human NOTE-ON projection of one canonical timeline.

    Deterministic for a given timeline: same input -> same plan, same
    step count, same ordering. The source timeline is read-only and is
    never mutated.
    """
    if not isinstance(timeline, Timeline):
        raise TypeError(
            f"build_practice_plan requires a Timeline, "
            f"got {type(timeline).__name__}"
        )
    groups: dict[float, set[int]] = {}
    skipped = 0
    for event in timeline.events:
        command = event.command
        if command.type is not OutputCommandType.NOTE_ON:
            continue
        if command.channel == PERCUSSION_CHANNEL:
            skipped += 1
            continue
        if not KEYBOARD_MIN <= command.note <= KEYBOARD_MAX:
            skipped += 1
            continue
        groups.setdefault(event.time, set()).add(command.note)
    targets = tuple(
        PracticeTarget(
            notes=tuple(sorted(notes)), onset_seconds=onset, ordinal=ordinal
        )
        for ordinal, (onset, notes) in enumerate(sorted(groups.items()))
    )
    return PracticePlan(
        targets=targets,
        source=timeline.source,
        skipped_note_ons=skipped,
        duration_seconds=timeline.duration,
    )


def select_practice_section(
    plan: PracticePlan, section: PracticeSection
) -> PracticePlan:
    """Return the canonical targets inside one half-open song-time section.

    Sectioning is deliberately time-based because the current P009 timeline
    retains authoritative song seconds but not meter, measure, beat, track or
    hand metadata. Targets keep their FULL-song canonical ordinals here
    (filtering never renumbers), so target identity is stable and
    collision-free across equivalent reconstructions of the same section.
    """
    if not isinstance(plan, PracticePlan):
        raise TypeError("plan must be a PracticePlan")
    if not isinstance(section, PracticeSection):
        raise TypeError("section must be a PracticeSection")
    targets = tuple(
        target for target in plan.targets if section.contains(target.onset_seconds)
    )
    return PracticePlan(
        targets=targets,
        source=plan.source,
        skipped_note_ons=plan.skipped_note_ons,
        duration_seconds=max(0.0, section.end_seconds - section.start_seconds),
    )
