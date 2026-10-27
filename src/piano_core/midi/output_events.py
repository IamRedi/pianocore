"""Canonical typed MIDI output commands (PIANO-P007).

These frozen records are the only data shape business layers may hand to
the MIDI output adapter; raw mido messages are constructed exclusively
inside the backend. Commands are validated fail closed at construction.

PIANO-P007 proves only the minimal output primitives (note on/off,
sustain, stop, panic). There is deliberately NO timeline playback, no
MIDI-file interpretation, and no scheduling here.

Channels are canonical 1-based (1..16), matching the input event model;
the backend converts to mido's 0-based representation internally.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

#: CC number of the sustain (damper) pedal output.
SUSTAIN_CC = 64
#: MIDI channel-mode: all notes off.
ALL_NOTES_OFF_CC = 123
#: MIDI channel-mode: all sound off.
ALL_SOUND_OFF_CC = 120

#: Canonical channel range (1-based).
CHANNEL_MIN, CHANNEL_MAX = 1, 16
#: Canonical 7-bit MIDI value range.
_SEVEN_BIT_MIN, _SEVEN_BIT_MAX = 0, 127


def _validate_int(name: str, value: object, low: int, high: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if not low <= value <= high:
        raise ValueError(f"{name} out of range [{low}..{high}]: {value!r}")
    return value


class OutputCommandType(enum.Enum):
    """Canonical MIDI output command types."""

    NOTE_ON = "note_on"
    NOTE_OFF = "note_off"
    SUSTAIN = "sustain"
    STOP_AUTOMATED_NOTES = "stop_automated_notes"
    PANIC = "panic"


@dataclass(frozen=True, slots=True)
class OutputCommand:
    """One validated canonical output command.

    Payload fields are meaningful per type and must be left at defaults
    for the global commands (``STOP_AUTOMATED_NOTES`` / ``PANIC``), which
    carry no per-note data; providing one is rejected (fail closed).
    """

    type: OutputCommandType
    channel: int = 1
    note: int = 0
    velocity: int = 0
    release_velocity: int = 0
    value: int = 0

    def __post_init__(self) -> None:
        _validate_int("channel", self.channel, CHANNEL_MIN, CHANNEL_MAX)
        _validate_int("note", self.note, _SEVEN_BIT_MIN, _SEVEN_BIT_MAX)
        _validate_int("velocity", self.velocity, _SEVEN_BIT_MIN, _SEVEN_BIT_MAX)
        _validate_int(
            "release_velocity", self.release_velocity, _SEVEN_BIT_MIN, _SEVEN_BIT_MAX
        )
        _validate_int("value", self.value, _SEVEN_BIT_MIN, _SEVEN_BIT_MAX)
        if self.type is OutputCommandType.NOTE_ON:
            if self.velocity < 1:
                raise ValueError(
                    "NOTE_ON velocity must be 1..127 (0 is not a note-on)"
                )
        elif self.type is OutputCommandType.STOP_AUTOMATED_NOTES or (
            self.type is OutputCommandType.PANIC
        ):
            payload = (self.note, self.velocity, self.release_velocity, self.value)
            if payload != (0, 0, 0, 0) or self.channel != 1:
                raise ValueError(
                    f"{self.type.value} is a global command and carries no payload"
                )

    def describe(self) -> str:
        """Stable one-line description (numbers only; no secrets)."""
        if self.type is OutputCommandType.NOTE_ON:
            return (
                f"note_on channel={self.channel} note={self.note} "
                f"velocity={self.velocity}"
            )
        if self.type is OutputCommandType.NOTE_OFF:
            return (
                f"note_off channel={self.channel} note={self.note} "
                f"release_velocity={self.release_velocity}"
            )
        if self.type is OutputCommandType.SUSTAIN:
            return f"sustain channel={self.channel} value={self.value}"
        return self.type.value


def note_on(channel: int, note: int, velocity: int) -> OutputCommand:
    """Build a validated NOTE_ON output command (velocity 1..127)."""
    return OutputCommand(
        type=OutputCommandType.NOTE_ON, channel=channel, note=note, velocity=velocity
    )


def note_off(
    channel: int, note: int, release_velocity: int = 0
) -> OutputCommand:
    """Build a validated NOTE_OFF output command."""
    return OutputCommand(
        type=OutputCommandType.NOTE_OFF,
        channel=channel,
        note=note,
        release_velocity=release_velocity,
    )


def sustain(channel: int, value: int) -> OutputCommand:
    """Build a validated SUSTAIN (CC64) output command."""
    return OutputCommand(
        type=OutputCommandType.SUSTAIN, channel=channel, value=value
    )


def stop_automated_notes() -> OutputCommand:
    """Build the STOP command: releases only Piano Core-owned notes.

    STOP is NOT PANIC: never a global sweep, never silences unrelated
    human playing.
    """
    return OutputCommand(type=OutputCommandType.STOP_AUTOMATED_NOTES)


def panic() -> OutputCommand:
    """Build the PANIC command: global emergency MIDI silence.

    Explicitly broader than STOP; must never be silently substituted for
    ordinary stop.
    """
    return OutputCommand(type=OutputCommandType.PANIC)
