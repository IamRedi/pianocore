"""Canonical learning representation for the Learn Library.

Pure data model shared by every source adapter and the package builder.
No source or format knowledge lives here; adapters produce ``LearnItem``
values and the builder consumes them.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Optional

__all__ = [
    "LEARN_ID_NAMESPACE_V1",
    "Hand",
    "LearnNote",
    "LearnItem",
    "lesson_id_for",
    "item_duration",
]

#: Deterministic Learn identity namespace (separate from the Player
#: library namespace and from the practice-package namespace on purpose:
#: a lesson id must never collide with a Player song id or a raw package
#: id even if the underlying MIDI bytes were shared).
LEARN_ID_NAMESPACE_V1 = uuid.uuid5(uuid.NAMESPACE_URL, "piano-core:learn:v1")

#: Canonical hand labels used across the Learn subsystem. These match the
#: practice-package contract ("RH"/"LH"/None) rather than the Pianio
#: source labels ("right"/"left"/"together"), which adapters must map.
Hand = Optional[str]


@dataclass(frozen=True, slots=True)
class LearnNote:
    """One canonical note event (onsets/offsets in song seconds)."""

    onset: float
    offset: float
    midi: int
    velocity: int
    hand: Hand = None
    track: int = 0


@dataclass(frozen=True, slots=True)
class LearnItem:
    """One canonical learning item produced by a source adapter.

    ``lesson_key`` is the stable source identity (e.g.
    ``pianio:method-level-1a:m1a-001`` or ``mutopia:the-entertainer``);
    the published lesson id derives deterministically from it.

    ``problems`` carries UNSUPPORTED classifications. A non-empty
    ``problems`` tuple means the item must NOT be published; the exact
    reasons are recorded in the source-to-published ledger.
    """

    lesson_key: str
    title: str
    composer: str | None
    source: str  # "pianio" | "mutopia" | "pianovera"
    source_detail: dict = field(default_factory=dict)
    license_name: str = "Unknown"
    license_url: str | None = None
    attribution: str | None = None
    notes: tuple[LearnNote, ...] = ()
    tempo_bpm: float | None = None
    time_signature: tuple[int, int] | None = None
    key_signature: str | None = None
    swing: bool = False
    pedal_events: tuple[tuple[float, str], ...] = ()
    level_hint: int | None = None
    hands_availability: str = "unknown"  # available | partial | unknown
    summary: str | None = None
    problems: tuple[str, ...] = ()

    def with_problems(self, *reasons: str) -> "LearnItem":
        merged = tuple(dict.fromkeys(self.problems + tuple(reasons)))
        return LearnItem(
            lesson_key=self.lesson_key,
            title=self.title,
            composer=self.composer,
            source=self.source,
            source_detail=self.source_detail,
            license_name=self.license_name,
            license_url=self.license_url,
            attribution=self.attribution,
            notes=self.notes,
            tempo_bpm=self.tempo_bpm,
            time_signature=self.time_signature,
            key_signature=self.key_signature,
            swing=self.swing,
            pedal_events=self.pedal_events,
            level_hint=self.level_hint,
            hands_availability=self.hands_availability,
            summary=self.summary,
            problems=merged,
        )


def lesson_id_for(lesson_key: str) -> str:
    """Deterministic published lesson identity for a source item."""
    if not isinstance(lesson_key, str) or not lesson_key:
        raise ValueError("lesson_key must be a non-empty string")
    return str(uuid.uuid5(LEARN_ID_NAMESPACE_V1, lesson_key))


def item_duration(notes: tuple[LearnNote, ...]) -> float:
    """Total performance duration in seconds (end of the last note)."""
    if not notes:
        return 0.0
    end = max(note.offset for note in notes)
    return float(end) if math.isfinite(end) and end > 0 else 0.0
