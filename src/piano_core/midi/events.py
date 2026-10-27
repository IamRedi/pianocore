"""Canonical typed MIDI input events.

These frozen records are the only MIDI data shape that leaves the MIDI
adapter boundary; raw ALSA/mido structures never escape it (PIANO-P004).

Channels are represented 1-based (human convention, matching the proven
legacy bridge normalization). Timestamps are monotonic seconds supplied by
the adapter's injectable clock. All fields are range-validated fail closed.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class MidiEventType(enum.Enum):
    """Canonical MIDI input event types."""

    NOTE_ON = "note_on"
    NOTE_OFF = "note_off"
    SUSTAIN = "sustain"


def _validate_int(name: str, value: object, low: int, high: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if not low <= value <= high:
        raise ValueError(f"{name} out of range [{low}..{high}]: {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class MidiInputEvent:
    """One canonical MIDI input event.

    ``note``/``velocity`` are meaningful for NOTE_ON/NOTE_OFF, ``value``/
    ``pressed`` for SUSTAIN; the unused fields are zero/False so instances
    are always fully defined and comparable.
    """

    type: MidiEventType
    channel: int
    timestamp: float
    note: int = 0
    velocity: int = 0
    value: int = 0
    pressed: bool = False

    def __post_init__(self) -> None:
        _validate_int("channel", self.channel, 1, 16)
        _validate_int("note", self.note, 0, 127)
        _validate_int("velocity", self.velocity, 0, 127)
        _validate_int("value", self.value, 0, 127)
        if not isinstance(self.timestamp, (int, float)) or isinstance(
            self.timestamp, bool
        ):
            raise TypeError(
                f"timestamp must be a number, got {type(self.timestamp).__name__}"
            )
        if not isinstance(self.pressed, bool):
            raise TypeError("pressed must be a bool")

    def describe(self) -> str:
        """Stable one-line description (numbers only; no secrets)."""
        if self.type is MidiEventType.SUSTAIN:
            return (
                f"{self.type.value} channel={self.channel} "
                f"value={self.value} pressed={self.pressed} "
                f"t={self.timestamp:.6f}"
            )
        return (
            f"{self.type.value} channel={self.channel} note={self.note} "
            f"velocity={self.velocity} t={self.timestamp:.6f}"
        )


def note_on(
    channel: int, note: int, velocity: int, timestamp: float
) -> MidiInputEvent:
    """Build a validated NOTE_ON event (velocity must be non-zero)."""
    if velocity == 0:
        raise ValueError(
            "NOTE_ON with velocity 0 must be normalized to NOTE_OFF"
        )
    return MidiInputEvent(
        type=MidiEventType.NOTE_ON,
        channel=channel,
        timestamp=timestamp,
        note=note,
        velocity=velocity,
    )


def note_off(
    channel: int, note: int, release_velocity: int, timestamp: float
) -> MidiInputEvent:
    """Build a validated NOTE_OFF event."""
    return MidiInputEvent(
        type=MidiEventType.NOTE_OFF,
        channel=channel,
        timestamp=timestamp,
        note=note,
        velocity=release_velocity,
    )


#: CC number of the sustain (damper) pedal.
SUSTAIN_CC = 64

#: Sustain values at or above this threshold count as pressed (MIDI convention).
SUSTAIN_PRESSED_THRESHOLD = 64


def sustain(channel: int, value: int, timestamp: float) -> MidiInputEvent:
    """Build a validated SUSTAIN event (``pressed`` derives from the value)."""
    return MidiInputEvent(
        type=MidiEventType.SUSTAIN,
        channel=channel,
        timestamp=timestamp,
        value=value,
        pressed=value >= SUSTAIN_PRESSED_THRESHOLD,
    )
