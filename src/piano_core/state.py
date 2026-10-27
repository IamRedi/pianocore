"""Canonical in-process Piano Core runtime state (PIANO-P006).

:class:`StateStore` is the single source of truth for current runtime state:
canonical :class:`~piano_core.midi.events.MidiInputEvent` records are reduced
deterministically into an immutable :class:`PianoState` snapshot that future
API / LED / HUGO / UI consumers read instead of touching raw MIDI.

Authority boundary: the StateStore is authoritative for *current* runtime
state. Raw MIDI messages are authoritative only inside the MIDI adapter;
nothing outside ``piano_core.midi`` may re-derive state from raw events.
The store is in-memory only: on process restart, pressed notes are empty,
sustain is released, connection starts DISCONNECTED, and the revision
resets to 0. A runtime disconnect also clears keys and sustain atomically so
lost release events cannot survive reconnect. Nothing here persists, sends
MIDI, or calls application lifecycle APIs (the P001 ``request_stop`` race
stays unreachable).
"""

from __future__ import annotations

import enum
import logging
import threading
from dataclasses import dataclass, replace

from piano_core.midi.events import MidiEventType, MidiInputEvent

#: Inclusive MIDI range for notes and 7-bit control values.
_MIDI_7BIT_MAX = 127


class MidiConnectionState(enum.Enum):
    """Canonical MIDI input connection lifecycle (store-facing view).

    Mirrors the adapter's internal :class:`~piano_core.midi.input.ConnectionState`
    values one-to-one; the mapping lives in the composition root so this
    canonical state contract stays free of adapter imports.
    """

    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"


@dataclass(frozen=True, slots=True)
class LastInput:
    """Observational metadata for the most recently accepted input event.

    This is *not* part of the material state revision (see StateStore
    revision policy): it changes with every accepted event so consumers can
    see the latest input, while ``revision`` tracks material state only.
    """

    event_type: str
    channel: int
    timestamp: float
    note: int = 0
    value: int = 0


@dataclass(frozen=True, slots=True)
class PianoState:
    """Immutable snapshot of canonical Piano Core runtime state.

    ``pressed_notes`` are keys *physically held* per NOTE_ON/NOTE_OFF input.
    The sustain pedal does NOT retain released notes here — sustained/
    sounding-note semantics are deliberately not modelled yet. There is no
    playback/output state by design (PIANO-P006 is input-only).

    ``revision`` is the monotonic material-state version: it increments by
    exactly one whenever pressed notes, sustain (raw value or pressed), or
    the MIDI connection materially changes, and never for no-ops or
    ``last_input``-only updates.
    """

    pressed_notes: frozenset[int] = frozenset()
    sustain_value: int = 0
    sustain_pressed: bool = False
    last_input: LastInput | None = None
    midi_connection: MidiConnectionState = MidiConnectionState.DISCONNECTED
    revision: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.pressed_notes, frozenset):
            raise TypeError(
                "pressed_notes must be a frozenset, "
                f"got {type(self.pressed_notes).__name__}"
            )
        for note in self.pressed_notes:
            if not isinstance(note, int) or isinstance(note, bool):
                raise TypeError(f"pressed note must be an int, got {note!r}")
            if not 0 <= note <= _MIDI_7BIT_MAX:
                raise ValueError(f"pressed note out of range: {note!r}")
        if not isinstance(self.sustain_value, int) or isinstance(
            self.sustain_value, bool
        ):
            raise TypeError(
                f"sustain_value must be an int, got {type(self.sustain_value).__name__}"
            )
        if not 0 <= self.sustain_value <= _MIDI_7BIT_MAX:
            raise ValueError(f"sustain_value out of range: {self.sustain_value!r}")
        if not isinstance(self.sustain_pressed, bool):
            raise TypeError("sustain_pressed must be a bool")
        if self.last_input is not None and not isinstance(
            self.last_input, LastInput
        ):
            raise TypeError(
                "last_input must be a LastInput or None, "
                f"got {type(self.last_input).__name__}"
            )
        if not isinstance(self.midi_connection, MidiConnectionState):
            raise TypeError(
                "midi_connection must be a MidiConnectionState, "
                f"got {type(self.midi_connection).__name__}"
            )
        if not isinstance(self.revision, int) or isinstance(self.revision, bool):
            raise TypeError(
                f"revision must be an int, got {type(self.revision).__name__}"
            )
        if self.revision < 0:
            raise ValueError(f"revision must be non-negative: {self.revision!r}")

    @classmethod
    def initial(cls) -> "PianoState":
        """Canonical empty state (process start / after restart)."""
        return cls()


class StateStore:
    """Thread-safe single-writer store reducing canonical events to state.

    One internal lock guards the current :class:`PianoState`. Writers are
    the MIDI reader thread (via :meth:`apply_event`) and connection
    observers (via :meth:`set_midi_connection`); any thread may call
    :meth:`snapshot`. Snapshots are frozen dataclasses, so a reader can
    never mutate store internals and every snapshot is coherent/atomic.

    Revision policy (explicit, Option B — ``last_input`` is observational):

    - material state = ``pressed_notes`` + ``sustain_value`` +
      ``sustain_pressed`` + ``midi_connection``;
    - an update bumps ``revision`` by exactly 1 **iff** material state
      differs afterwards; no-ops (repeated NOTE_ON for a held note,
      NOTE_OFF for an absent note, same-value SUSTAIN, connection set to
      the current value) leave the revision unchanged;
    - ``last_input`` is updated on every accepted event and is excluded
      from materiality, so a snapshot may differ from the previous one
      while sharing its revision.
    """

    def __init__(
        self,
        initial: PianoState | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if initial is not None and not isinstance(initial, PianoState):
            raise TypeError(
                f"initial must be a PianoState, got {type(initial).__name__}"
            )
        self._state = initial if initial is not None else PianoState.initial()
        self._lock = threading.Lock()
        self._log = logger or logging.getLogger("piano_core.state")
        self._counts: dict[str, int] = {}

    def snapshot(self) -> PianoState:
        """Return the current immutable state (atomic, coherent)."""
        with self._lock:
            return self._state

    def apply_event(self, event: MidiInputEvent) -> PianoState:
        """Reduce one canonical input event; returns the new state.

        Raises :class:`TypeError` for non-canonical input: only validated
        :class:`~piano_core.midi.events.MidiInputEvent` records enter the
        store. Velocity-0 normalization already happened upstream (the
        canonical constructors and the adapter enforce it).
        """
        if not isinstance(event, MidiInputEvent):
            raise TypeError(
                f"apply_event requires a canonical MidiInputEvent, "
                f"got {type(event).__name__}"
            )
        with self._lock:
            current = self._state
            pressed = current.pressed_notes
            sustain_value = current.sustain_value
            sustain_pressed = current.sustain_pressed
            if event.type is MidiEventType.NOTE_ON:
                pressed = pressed | {event.note}
            elif event.type is MidiEventType.NOTE_OFF:
                pressed = pressed - {event.note}
            elif event.type is MidiEventType.SUSTAIN:
                sustain_value = event.value
                sustain_pressed = event.pressed
            else:  # pragma: no cover - the canonical enum is closed
                raise ValueError(f"unsupported canonical event type: {event.type!r}")
            material = (
                pressed != current.pressed_notes
                or sustain_value != current.sustain_value
                or sustain_pressed != current.sustain_pressed
            )
            updated = PianoState(
                pressed_notes=pressed,
                sustain_value=sustain_value,
                sustain_pressed=sustain_pressed,
                last_input=LastInput(
                    event_type=event.type.value,
                    channel=event.channel,
                    timestamp=event.timestamp,
                    note=event.note,
                    value=event.value,
                ),
                midi_connection=current.midi_connection,
                revision=current.revision + (1 if material else 0),
            )
            self._state = updated
            self._counts[event.type.value] = self._counts.get(event.type.value, 0) + 1
        if material:
            # Per-note detail is DEBUG only: production INFO stays note-free.
            self._log.debug(
                "state revision=%d notes=%d sustain=%d/%s",
                updated.revision,
                len(updated.pressed_notes),
                updated.sustain_value,
                updated.sustain_pressed,
            )
        return updated

    def set_midi_connection(self, connection: MidiConnectionState) -> PianoState:
        """Update the canonical MIDI connection state; returns the new state.

        Disconnect is also the authoritative reset boundary: held keys and
        pedal state are cleared because they can no longer be observed.  This
        prevents a lost NOTE_OFF from surviving a cable loss/reconnect.  The
        whole connection/reset transition bumps the revision exactly once.
        """
        if not isinstance(connection, MidiConnectionState):
            raise TypeError(
                f"connection must be a MidiConnectionState, "
                f"got {type(connection).__name__}"
            )
        with self._lock:
            current = self._state
            reset = connection is MidiConnectionState.DISCONNECTED and (
                bool(current.pressed_notes)
                or current.sustain_value != 0
                or current.sustain_pressed
            )
            if current.midi_connection is connection and not reset:
                return current
            updated = replace(
                current,
                pressed_notes=frozenset() if reset else current.pressed_notes,
                sustain_value=0 if reset else current.sustain_value,
                sustain_pressed=False if reset else current.sustain_pressed,
                midi_connection=connection,
                revision=current.revision + 1,
            )
            self._state = updated
        self._log.info("midi connection state=%s", connection.value)
        return updated

    def event_counts_summary(self) -> str:
        """Diagnostic one-line summary of accepted events (for shutdown logs)."""
        with self._lock:
            counts = dict(self._counts)
        return " ".join(
            f"{name}={count}" for name, count in sorted(counts.items())
        ) or "no events"

    def events_received(self) -> int:
        """Total count of accepted canonical events."""
        with self._lock:
            return sum(self._counts.values())
