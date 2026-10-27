"""MIDI input adapter package for Piano Core.

Canonical input path: Roland USB MIDI -> ALSA -> this adapter -> typed
canonical events (:mod:`piano_core.midi.events`). INPUT ONLY: the adapter
opens input ports and can never send MIDI. Application logic must not
import mido/ALSA directly.
"""

from piano_core.midi.events import (
    SUSTAIN_CC,
    SUSTAIN_PRESSED_THRESHOLD,
    MidiEventType,
    MidiInputEvent,
    note_off,
    note_on,
    sustain,
)
from piano_core.midi.input import (
    AmbiguousEndpointError,
    ConnectionState,
    EndpointNotFoundError,
    MidiInputAdapter,
    normalize_message,
    select_input_port,
)

__all__ = [
    "SUSTAIN_CC",
    "SUSTAIN_PRESSED_THRESHOLD",
    "AmbiguousEndpointError",
    "ConnectionState",
    "EndpointNotFoundError",
    "MidiEventType",
    "MidiInputAdapter",
    "MidiInputEvent",
    "normalize_message",
    "note_off",
    "note_on",
    "select_input_port",
    "sustain",
]
