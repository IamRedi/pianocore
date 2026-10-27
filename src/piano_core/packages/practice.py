"""Package-derived practice plans and the guidance timeline (M5).

Plan derivation for imported packages mirrors the accepted
``practice.plan`` rules (NOTE_ON only, percussion excluded, exact-onset
chord grouping, keyboard bounds) but sources its notes from the
package's hash-validated PracticeEvents, which carry the hand, section
and importance metadata the Studio exported. Hand filtering is
therefore evidence-based: RH/LH modes only exist when the package
actually carries hand data.

The guidance timeline is a PURE, deterministic projection for the
Listen/Follow modes: seek, section windows, looping and tempo scaling
are arithmetic on song-time seconds (the tempo map is already baked
into the package event onsets by the exporter). Wall-clock mapping is
the caller's job: ``song_time = elapsed_wall_time * tempo_scale`` —
so 50% practice speed makes the piece take twice as long while the
*relative* tempo changes inside the piece remain meaningful. The
package MIDI is never modified.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from piano_core.packages.contract import (
    KEYBOARD_MAX,
    KEYBOARD_MIN,
    PracticePackage,
)
from piano_core.practice.model import (
    PracticePlan,
    PracticeSection,
    PracticeTarget,
)

__all__ = [
    "GUIDED_MODES",
    "GuidedRun",
    "PackagePracticeError",
    "build_package_plan",
    "hands_mode_available",
]


class PackagePracticeError(ValueError):
    """A guided-practice request against a package is invalid."""


#: Guided modes beyond the accepted Learning Engine modes. WAIT mode is
#: served by the accepted PracticeEngine (WAIT_FOR_NOTE); LISTEN/FOLLOW
#: advance on the guidance timeline without gating on user input.
GUIDED_MODES = ("LISTEN", "FOLLOW", "WAIT")


def hands_mode_available(package: PracticePackage) -> bool:
    """RH/LH modes require real hand evidence (never fabricated)."""
    return package.identity.hands_availability in ("available", "partial") and any(
        event.hand in ("RH", "LH") for event in package.events
    )


def build_package_plan(
    package: PracticePackage,
    *,
    hand: str | None = None,
    section: PracticeSection | None = None,
) -> PracticePlan:
    """Derive the deterministic PracticePlan from package events.

    ``hand`` must be ``None`` (both hands) or ``"RH"``/``"LH"``; a hand
    filter on a package without hand evidence raises (fail closed —
    Piano Core must not offer authoritative hand modes without data).
    ``section`` scopes the plan to one section's time range while
    keeping full-song ordinals stable.
    """
    if hand not in (None, "RH", "LH"):
        raise PackagePracticeError(f"invalid hand selection: {hand!r}")
    if hand is not None and not hands_mode_available(package):
        raise PackagePracticeError(
            "this package carries no hand metadata; hand-specific modes "
            "are unavailable (fail closed)"
        )
    events = package.events
    if hand is not None:
        events = tuple(event for event in events if event.hand == hand)
    if section is not None:
        # accepts the contract section (start_time/end_time) and the
        # canonical practice-model section (start_seconds/end_seconds)
        start = getattr(section, "start_time", None)
        if start is None:
            start = section.start_seconds
        end = getattr(section, "end_time", None)
        if end is None:
            end = section.end_seconds
        events = tuple(
            event for event in events if start <= event.onset < end
        )
    if not events:
        raise PackagePracticeError(
            "no practice events in scope (check hand/section selection)"
        )

    # same-onset grouping -> one human step per onset group (chords)
    groups: dict[float, list[int]] = {}
    ordinals: dict[float, int] = {}
    for event in sorted(events, key=lambda e: (e.onset, e.note, e.event_id)):
        groups.setdefault(event.onset, []).append(event.note)
    for ordinal, onset in enumerate(sorted(groups)):
        ordinals[onset] = ordinal

    targets: list[PracticeTarget] = []
    skipped = 0
    for onset in sorted(groups):
        notes = tuple(sorted(set(groups[onset])))
        in_range = tuple(
            note for note in notes if KEYBOARD_MIN <= note <= KEYBOARD_MAX
        )
        skipped += len(notes) - len(in_range)
        if not in_range:
            continue
        targets.append(
            PracticeTarget(notes=in_range, onset_seconds=onset,
                           ordinal=ordinals[onset])
        )
    if not targets:
        raise PackagePracticeError("package yields no playable keyboard targets")
    duration = max(event.offset for event in package.events)
    return PracticePlan(
        targets=tuple(targets),
        source=f"practice-package:{package.package_id}",
        skipped_note_ons=skipped,
        duration_seconds=duration,
    )


def plan_section(package: PracticePackage, section_id: str | None) -> PracticeSection | None:
    """Resolve a package section id (or None for the whole piece)."""
    if section_id is None:
        return None
    contract_section = package.section(section_id)
    if contract_section is None:
        raise PackagePracticeError(f"unknown section: {section_id!r}")
    return PracticeSection(
        contract_section.start_time, contract_section.end_time
    )


# ------------------------------------------------------------------ guidance


@dataclass
class GuidedRun:
    """Deterministic guidance timeline runner (Listen/Follow modes).

    Pure state machine over song-time seconds: the caller feeds wall
    deltas and reads the current+upcoming window. Looping wraps the
    section range deterministically; seeking is clamped into the active
    range. No wall clock, no threads — trivially testable.
    """

    package: PracticePackage
    tempo_scale: float = 1.0
    loop_start: float | None = None
    loop_end: float | None = None
    position: float = 0.0
    finished: bool = False
    loop_count: int = 0
    _history: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.tempo_scale, (int, float))
            or isinstance(self.tempo_scale, bool)
            or not 0.25 <= float(self.tempo_scale) <= 4.0
        ):
            raise PackagePracticeError(
                "tempo_scale must be within 0.25..4.0 (accepted bounds)"
            )
        self.tempo_scale = float(self.tempo_scale)
        duration = self._duration()
        if self.loop_start is not None or self.loop_end is not None:
            if self.loop_start is None or self.loop_end is None:
                raise PackagePracticeError("loop requires both start and end")
            if not 0 <= self.loop_start < self.loop_end <= duration + 1e-6:
                raise PackagePracticeError("loop range out of bounds")
        self._history = sorted({event.onset for event in self.package.events})

    def _duration(self) -> float:
        if self.loop_end is not None:
            return self.loop_end
        return max(
            (event.offset for event in self.package.events), default=0.0
        )

    def seek(self, song_seconds: float) -> None:
        if (
            not isinstance(song_seconds, (int, float))
            or isinstance(song_seconds, bool)
            or song_seconds < 0
        ):
            raise PackagePracticeError("seek must be a non-negative number")
        duration = self._duration()
        self.position = min(float(song_seconds), duration)
        self.finished = False

    def tick(self, wall_delta_seconds: float) -> None:
        """Advance by a wall-clock delta (scaled by the tempo scale)."""
        if self.finished:
            return
        if (
            not isinstance(wall_delta_seconds, (int, float))
            or isinstance(wall_delta_seconds, bool)
            or wall_delta_seconds < 0
        ):
            raise PackagePracticeError("tick delta must be a non-negative number")
        self.position += float(wall_delta_seconds) * self.tempo_scale
        end = self.loop_end if self.loop_end is not None else self._duration()
        if self.loop_start is not None and self.position >= end:
            self.position = self.loop_start + (
                (self.position - end) % max(1e-9, end - self.loop_start)
            )
            self.loop_count += 1
        elif self.position >= end and self.loop_end is None:
            self.position = end
            self.finished = True

    def window(self, lookahead_seconds: float = 0.5) -> tuple[tuple, tuple]:
        """Return ``(current, upcoming)`` event groups at the position.

        ``current``: events whose onset <= position < offset (sounding)
        or the nearest group at/behind the position within the window.
        ``upcoming``: onset groups within the lookahead window, scaled
        with the practice tempo (the lookahead is song-time; wall-clock
        lookahead grows as the tempo scale drops, which is exactly what
        a slow practitioner needs).
        """
        if (
            not isinstance(lookahead_seconds, (int, float))
            or isinstance(lookahead_seconds, bool)
            or lookahead_seconds <= 0
        ):
            raise PackagePracticeError("lookahead must be a positive number")
        horizon = self.position + float(lookahead_seconds) * self.tempo_scale
        current: list = []
        upcoming: list = []
        for event in self.package.events:
            if event.onset <= self.position < event.offset:
                current.append(event)
            elif self.position < event.onset <= horizon:
                upcoming.append(event)
        return tuple(current), tuple(upcoming)
