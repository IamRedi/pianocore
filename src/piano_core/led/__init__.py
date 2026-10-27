"""LED keyboard subsystem (PIANO-P014) — visual output only.

Canonical entry points:

- :class:`piano_core.led.integration.LedIntegration` — composition glue
  (observers for playback/input, lifecycle);
- :class:`piano_core.led.adapter.LedKeyboardAdapter` — state + transport
  owner;
- :mod:`piano_core.led.mapping` — calibrated MIDI note -> LED indexes.

The LED subsystem is never authoritative for MIDI state, playback state,
physical key state, timing, or song identity (ADR-LED-KEYBOARD-V1).
"""

from piano_core.led.adapter import LedKeyboardAdapter
from piano_core.led.integration import LedIntegration
from piano_core.led.mapping import midi_to_led_indexes, midi_note_to_key
from piano_core.led.model import (
    LedConfigError,
    LedConnectionState,
    LedError,
    LedMode,
    LedState,
)

__all__ = [
    "LedConfigError",
    "LedConnectionState",
    "LedError",
    "LedIntegration",
    "LedKeyboardAdapter",
    "LedMode",
    "LedState",
    "midi_note_to_key",
    "midi_to_led_indexes",
]
