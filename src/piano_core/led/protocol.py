"""ESP32 keyboard LED wire protocol (PIANO-P014).

Exactly the message subset the DEPLOYED firmware parses
(``PLUTO PIANO BRAIN`` / ``src/led_test/pluto_pi_link.cpp``), nothing
more:

- ``{"type":"midi","event":"note_on","note":N,"velocity":V}`` — V>0 lights
  key N; V==0 clears it;
- ``{"type":"midi","event":"note_off","note":N,...}`` — clears key N;
- ``{"type":"playback","status":"all_notes_off"}`` — clears ALL keys.

The firmware additionally accepts ``playback`` status ``idle``/``finished``
(same effect); this module canonicalizes on ``all_notes_off`` only. The
client sends ``{"type":"get_state"}`` on connect (the deployed firmware
ignores any response); this parser accepts it and nothing else. Malformed
input is NEVER forwarded anywhere — it is dropped (protocol silence).
"""

from __future__ import annotations

import json

from piano_core.led.model import LedError

__all__ = [
    "CLIENT_MAX_MESSAGE_BYTES",
    "ClientMessage",
    "clear_frame",
    "note_off_frame",
    "note_on_frame",
    "parse_client_message",
]

#: One protocol text frame. Tiny by construction (single MIDI note).
_MAX_NOTE = 127
_MAX_VELOCITY = 127

#: Upper bound for a client message (the ESP32 sends ~24 bytes). Anything
#: larger is dropped unread-by-protocol (still bounded at transport level).
CLIENT_MAX_MESSAGE_BYTES = 1024


class ClientMessage:
    """A recognized inbound client message (closed set)."""

    __slots__ = ("kind",)

    def __init__(self, kind: str) -> None:
        if kind not in ("get_state",):
            raise LedError(f"unknown client message kind: {kind!r}")
        self.kind = kind

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, ClientMessage) and other.kind == self.kind
        )

    def __hash__(self) -> int:
        return hash(("ClientMessage", self.kind))

    def __repr__(self) -> str:
        return f"ClientMessage({self.kind!r})"


def _validate_note(note: object) -> int:
    if not isinstance(note, int) or isinstance(note, bool):
        raise LedError(f"note must be an int, got {type(note).__name__}")
    if not 0 <= note <= _MAX_NOTE:
        raise LedError(f"note out of range [0..{_MAX_NOTE}]: {note!r}")
    return note


def _validate_velocity(velocity: object) -> int:
    if not isinstance(velocity, int) or isinstance(velocity, bool):
        raise LedError(
            f"velocity must be an int, got {type(velocity).__name__}"
        )
    if not 1 <= velocity <= _MAX_VELOCITY:
        raise LedError(
            f"velocity out of range [1..{_MAX_VELOCITY}]: {velocity!r}"
        )
    return velocity


def note_on_frame(note: int, velocity: int) -> str:
    """Build the exact wire frame that lights one key on the firmware."""
    note = _validate_note(note)
    velocity = _validate_velocity(velocity)
    return (
        '{"type":"midi","event":"note_on",'
        f'"note":{note},"velocity":{velocity}}}'
    )


def note_off_frame(note: int) -> str:
    """Build the exact wire frame that clears one key."""
    note = _validate_note(note)
    return '{"type":"midi","event":"note_off",' f'"note":{note}}}'


def clear_frame() -> str:
    """Build the deterministic all-keys-clear frame."""
    return '{"type":"playback","status":"all_notes_off"}'


def parse_client_message(raw: str | bytes) -> ClientMessage | None:
    """Parse one inbound client message; ``None`` for anything untracked.

    The deployed firmware sends exactly one message kind
    (``{"type":"get_state"}``). Every other well-formed or malformed
    payload maps to ``None`` — unknown input is dropped, never forwarded,
    never executed (bounded protocol surface, ADR D1).
    """
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("type") == "get_state":
        return ClientMessage("get_state")
    return None
