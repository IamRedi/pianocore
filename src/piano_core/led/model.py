"""Typed LED subsystem model (PIANO-P014).

The LED subsystem is VISUAL OUTPUT ONLY (ADR-LED-KEYBOARD-V1): it is never
authoritative for MIDI state, playback state, physical key state, timing,
or song identity. Everything here is a frozen, observable snapshot.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

__all__ = [
    "BRIGHTNESS_MAX",
    "BRIGHTNESS_MIN",
    "LedConfigError",
    "LedConnectionState",
    "LedError",
    "LedMode",
    "LedState",
]


class LedError(RuntimeError):
    """A LED subsystem request violated its contract. Fail closed."""


class LedConfigError(LedError):
    """Invalid LED configuration (fail closed at construction)."""


class LedMode(enum.Enum):
    """Bounded LED visualization modes (ADR D3)."""

    OFF = "off"
    PLAYBACK = "playback"
    PHYSICAL = "physical"
    ALL = "all"


class LedConnectionState(enum.Enum):
    """LED transport connection lifecycle (honest, never assumed)."""

    DISABLED = "DISABLED"  # subsystem exists but enable=false
    LISTENING = "LISTENING"  # enabled, waiting for the ESP32 client
    CONNECTED = "CONNECTED"  # the ESP32 keyboard client is present
    UNAVAILABLE = "UNAVAILABLE"  # bind/start failure; degraded


#: Brightness bounds (percent). Brightness is honest SOFTWARE scaling of
#: the reported velocity only: the firmware's global LED brightness is a
#: compiled constant and is not remotely controllable.
BRIGHTNESS_MIN = 0
BRIGHTNESS_MAX = 100


def _validate_mode(mode: object) -> LedMode:
    if not isinstance(mode, LedMode):
        raise TypeError(f"mode must be a LedMode, got {type(mode).__name__}")
    return mode


def _validate_connection(connection: object) -> LedConnectionState:
    if not isinstance(connection, LedConnectionState):
        raise TypeError(
            f"connection must be a LedConnectionState, "
            f"got {type(connection).__name__}"
        )
    return connection


def _validate_brightness(brightness: object) -> int:
    if not isinstance(brightness, int) or isinstance(brightness, bool):
        raise TypeError(
            f"brightness must be an int, got {type(brightness).__name__}"
        )
    if not BRIGHTNESS_MIN <= brightness <= BRIGHTNESS_MAX:
        raise ValueError(
            f"brightness out of range [{BRIGHTNESS_MIN}..{BRIGHTNESS_MAX}]: "
            f"{brightness!r}"
        )
    return brightness


@dataclass(frozen=True, slots=True)
class LedState:
    """Immutable LED subsystem snapshot for observability surfaces."""

    enabled: bool
    connection: LedConnectionState
    mode: LedMode
    brightness: int
    active_note_count: int = 0
    practice_active: bool = False
    last_error: str | None = None
    host: str | None = None
    port: int | None = None
    dropped_events: int = 0
    frames_sent: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be a bool")
        if not isinstance(self.practice_active, bool):
            raise TypeError("practice_active must be a bool")
        _validate_connection(self.connection)
        _validate_mode(self.mode)
        _validate_brightness(self.brightness)
        if not isinstance(self.active_note_count, int) or isinstance(
            self.active_note_count, bool
        ):
            raise TypeError("active_note_count must be an int")
        if self.active_note_count < 0:
            raise ValueError("active_note_count must be non-negative")
        if self.last_error is not None and not isinstance(self.last_error, str):
            raise TypeError("last_error must be a string or None")
