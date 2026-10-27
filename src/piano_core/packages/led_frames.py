"""LED semantic practice contract, v1 (M5, ADR-PACKAGE-PRACTICE-V1).

The practice engine decides MUSICAL MEANING; the LED renderer decides
COLORS and ANIMATION. This module is the versioned boundary between
the two: :class:`LedPracticeFrame` carries semantic state only —
never pixel colors, never hardware addressing, never raw MIDI packets.

Note states (v1):

- ``UPCOMING`` — inside the guidance lookahead window;
- ``ACTIVE``  — sounding at the current position (Listen/Follow);
- ``WAITING`` — the expected target in Wait mode;
- ``MATCHED`` — user input satisfied the expectation;
- ``MISSED``  — the expectation passed unmatched;
- ``EXTRA``   — user input that was not expected.

Hands are semantic hints (``RH``/``LH``/``None``), not physical
mappings: MIDI-note-to-pixel mapping lives exclusively in the LED
adapter configuration. This contract makes NO claim about physical
pixel correctness — hardware verification is a separate campaign.
"""

from __future__ import annotations

import enum
import threading
from dataclasses import dataclass, field
from typing import Protocol

from piano_core.packages.contract import LED_SEMANTIC_VERSION

__all__ = [
    "LED_SEMANTIC_VERSION",
    "LedNoteState",
    "LedPracticeFrame",
    "RecordingLedRenderer",
    "SemanticLedRenderer",
    "build_frame",
]


class LedNoteState(enum.Enum):
    """Semantic note states (v1)."""

    UPCOMING = "UPCOMING"
    ACTIVE = "ACTIVE"
    WAITING = "WAITING"
    MATCHED = "MATCHED"
    MISSED = "MISSED"
    EXTRA = "EXTRA"


@dataclass(frozen=True, slots=True)
class LedNote:
    """One semantically annotated note (no hardware meaning)."""

    note: int
    state: LedNoteState
    hand: str | None
    onset: float
    offset: float
    section: int | None = None


@dataclass(frozen=True, slots=True)
class LedPracticeFrame:
    """One versioned semantic practice frame."""

    practice_state: str          # LISTEN | FOLLOW | WAIT | COMPLETED | FAILED
    song_time: float             # song-time seconds (scaled reference)
    current_notes: tuple[LedNote, ...]
    upcoming_notes: tuple[LedNote, ...]
    hand: str | None             # active hand-mode filter or None
    section: str | None          # section id or None
    beat: float | None           # current beat-in-bar if known
    lookahead_ms: int            # guidance window actually applied
    extra_notes: tuple[int, ...] = ()   # unexpected user notes (semantic)
    loop_count: int = 0
    tempo_scale: float = 1.0
    schema: str = "led-practice-frame/v1"
    version: int = LED_SEMANTIC_VERSION

    def to_dict(self) -> dict:
        def note_dict(note: LedNote) -> dict:
            return {
                "note": note.note,
                "state": note.state.value,
                "hand": note.hand,
                "onset": round(note.onset, 4),
                "offset": round(note.offset, 4),
                "section": note.section,
            }

        return {
            "schema": self.schema,
            "led_semantic_version": self.version,
            "practice_state": self.practice_state,
            "song_time": round(self.song_time, 4),
            "current_notes": [note_dict(note) for note in self.current_notes],
            "upcoming_notes": [note_dict(note) for note in self.upcoming_notes],
            "extra_notes": list(self.extra_notes),
            "hand": self.hand,
            "section": self.section,
            "beat": self.beat,
            "lookahead_ms": self.lookahead_ms,
            "loop_count": self.loop_count,
            "tempo_scale": self.tempo_scale,
        }


class SemanticLedRenderer(Protocol):
    """Renderer boundary: consumes semantic frames, owns all visuals."""

    def render(self, frame: LedPracticeFrame) -> None:  # pragma: no cover
        ...


class RecordingLedRenderer:
    """Deterministic frame recorder (tests + future hardware evidence).

    Stores every frame it is given so a later hardware campaign can
    replay and compare against observed LED output. Thread-safe enough
    for the single-renderer use case.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frames: list[LedPracticeFrame] = []

    def render(self, frame: LedPracticeFrame) -> None:
        with self._lock:
            self._frames.append(frame)

    @property
    def frames(self) -> tuple[LedPracticeFrame, ...]:
        with self._lock:
            return tuple(self._frames)

    def last(self) -> LedPracticeFrame | None:
        with self._lock:
            return self._frames[-1] if self._frames else None

    def clear(self) -> None:
        with self._lock:
            self._frames.clear()


def build_frame(
    *,
    practice_state: str,
    song_time: float,
    current,
    upcoming,
    lookahead_ms: int,
    hand: str | None = None,
    section: str | None = None,
    beat: float | None = None,
    extra_notes: tuple[int, ...] = (),
    loop_count: int = 0,
    tempo_scale: float = 1.0,
) -> LedPracticeFrame:
    """Assemble a frame from raw event tuples (state defaults applied)."""
    current_notes = tuple(
        note
        if isinstance(note, LedNote)
        else LedNote(
            note=getattr(note, "note"),
            state=LedNoteState.ACTIVE,
            hand=getattr(note, "hand", None),
            onset=getattr(note, "onset", 0.0),
            offset=getattr(note, "offset", 0.0),
            section=getattr(note, "section", None),
        )
        for note in current
    )
    upcoming_notes = tuple(
        note
        if isinstance(note, LedNote)
        else LedNote(
            note=getattr(note, "note"),
            state=LedNoteState.UPCOMING,
            hand=getattr(note, "hand", None),
            onset=getattr(note, "onset", 0.0),
            offset=getattr(note, "offset", 0.0),
            section=getattr(note, "section", None),
        )
        for note in upcoming
    )
    return LedPracticeFrame(
        practice_state=practice_state,
        song_time=song_time,
        current_notes=current_notes,
        upcoming_notes=upcoming_notes,
        hand=hand,
        section=section,
        beat=beat,
        lookahead_ms=int(lookahead_ms),
        extra_notes=tuple(extra_notes),
        loop_count=loop_count,
        tempo_scale=tempo_scale,
    )
