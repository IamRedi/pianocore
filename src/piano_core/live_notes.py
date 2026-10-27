"""Bounded realtime publication of authoritative active-note snapshots.

PIANO-UI-003 adds no musical authority.  :class:`LiveNoteState` is a
thread-safe publication boundary that copies immutable observations from the
existing owners:

* ``StateStore`` publishes physical keys, pedal and input connection;
* ``MidiOutputAdapter`` publishes the notes/sustain that Piano Core owns.

Subscribers receive complete snapshots.  Events are only wake-ups, so a
missed event or reconnect is repaired by replacing the client replica with
the next full snapshot.  Publication performs no network I/O and never waits
for a subscriber.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, replace
from typing import Iterator

from piano_core.state import PianoState

__all__ = [
    "LiveNoteSnapshot",
    "LiveNoteState",
    "LiveNoteSubscriberLimitError",
    "LiveNotePublicationClosedError",
]


class LiveNoteSubscriberLimitError(RuntimeError):
    """The bounded realtime subscriber capacity is already in use."""


class LiveNotePublicationClosedError(RuntimeError):
    """Expected unavailable outcome once publication shutdown has begun."""


@dataclass(frozen=True, slots=True)
class LiveNoteSnapshot:
    """Immutable, coherent note-state publication for one runtime epoch."""

    schema_version: int
    runtime_epoch: str
    sequence: int
    physical_notes: tuple[int, ...]
    physical_sustain_value: int
    physical_sustain_pressed: bool
    midi_connection: str
    physical_revision: int
    playback_available: bool
    playback_voices: tuple[tuple[int, int], ...]
    playback_sustain_channels: tuple[int, ...]


class _Subscription:
    """One capacity-reserved snapshot iterator (``None`` is a heartbeat)."""

    def __init__(self, channel: "LiveNoteState") -> None:
        self._channel = channel
        self._closed = False
        self._last_sequence: int | None = None

    def __iter__(self) -> Iterator[LiveNoteSnapshot | None]:
        try:
            while True:
                item = self._channel._next(self._last_sequence, self)
                if item is False:
                    return
                if item is None:
                    yield None
                    continue
                self._last_sequence = item.sequence
                yield item
        finally:
            self.close()

    def close(self) -> None:
        # Cancellation, release and the iterator wait predicate share one lock.
        with self._channel._condition:
            if self._closed:
                return
            self._closed = True
            self._channel._subscribers -= 1
            self._channel._condition.notify_all()


class LiveNoteState:
    """Non-authoritative publication and bounded subscription boundary."""

    SCHEMA_VERSION = 1
    DEFAULT_MAX_SUBSCRIBERS = 8
    HEARTBEAT_SECONDS = 10.0

    def __init__(
        self,
        *,
        runtime_epoch: str | None = None,
        max_subscribers: int = DEFAULT_MAX_SUBSCRIBERS,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
    ) -> None:
        if not isinstance(max_subscribers, int) or isinstance(max_subscribers, bool):
            raise TypeError("max_subscribers must be an int")
        if max_subscribers < 1:
            raise ValueError("max_subscribers must be positive")
        if heartbeat_seconds <= 0:
            raise ValueError("heartbeat_seconds must be positive")
        epoch = runtime_epoch or uuid.uuid4().hex
        if not isinstance(epoch, str) or not epoch:
            raise ValueError("runtime_epoch must be a non-empty string")
        self._condition = threading.Condition()
        self._max_subscribers = max_subscribers
        self._heartbeat_seconds = float(heartbeat_seconds)
        self._subscribers = 0
        self._closed = False
        initial = PianoState.initial()
        self._snapshot = LiveNoteSnapshot(
            schema_version=self.SCHEMA_VERSION,
            runtime_epoch=epoch,
            sequence=0,
            physical_notes=(),
            physical_sustain_value=initial.sustain_value,
            physical_sustain_pressed=initial.sustain_pressed,
            midi_connection=initial.midi_connection.value,
            physical_revision=initial.revision,
            playback_available=False,
            playback_voices=(),
            playback_sustain_channels=(),
        )

    @property
    def runtime_epoch(self) -> str:
        return self.snapshot().runtime_epoch

    def snapshot(self) -> LiveNoteSnapshot:
        """Return the current immutable publication atomically."""
        with self._condition:
            return self._snapshot

    def publish_physical(self, state: PianoState) -> LiveNoteSnapshot:
        """Copy one authoritative physical ``StateStore`` snapshot."""
        if not isinstance(state, PianoState):
            raise TypeError("state must be a PianoState")
        with self._condition:
            current = self._snapshot
            values = (
                tuple(sorted(state.pressed_notes)),
                state.sustain_value,
                state.sustain_pressed,
                state.midi_connection.value,
                state.revision,
            )
            existing = (
                current.physical_notes,
                current.physical_sustain_value,
                current.physical_sustain_pressed,
                current.midi_connection,
                current.physical_revision,
            )
            if values == existing:
                return current
            self._snapshot = replace(
                current,
                sequence=current.sequence + 1,
                physical_notes=values[0],
                physical_sustain_value=values[1],
                physical_sustain_pressed=values[2],
                midi_connection=values[3],
                physical_revision=values[4],
            )
            self._condition.notify_all()
            return self._snapshot

    def publish_playback(
        self,
        *,
        available: bool,
        owned_notes=(),
        sustain_channels=(),
    ) -> LiveNoteSnapshot:
        """Copy actual canonical ownership after an output attempt."""
        if not isinstance(available, bool):
            raise TypeError("available must be a bool")
        voices = tuple(sorted((int(channel), int(note)) for channel, note in owned_notes))
        sustain = tuple(sorted(int(channel) for channel in sustain_channels))
        with self._condition:
            current = self._snapshot
            if (
                current.playback_available == available
                and current.playback_voices == voices
                and current.playback_sustain_channels == sustain
            ):
                return current
            self._snapshot = replace(
                current,
                sequence=current.sequence + 1,
                playback_available=available,
                playback_voices=voices,
                playback_sustain_channels=sustain,
            )
            self._condition.notify_all()
            return self._snapshot

    def subscribe(self) -> _Subscription:
        """Reserve one bounded subscriber and return its full-snapshot stream."""
        with self._condition:
            if self._closed:
                raise LiveNotePublicationClosedError("live-note publication is closed")
            if self._subscribers >= self._max_subscribers:
                raise LiveNoteSubscriberLimitError(
                    f"live-note subscriber limit reached ({self._max_subscribers})"
                )
            self._subscribers += 1
        return _Subscription(self)

    def close(self) -> None:
        """Wake and end all subscribers during application shutdown."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def _next(self, after: int | None, subscription: _Subscription):
        with self._condition:
            if self._closed or subscription._closed:
                return False
            if after is None or self._snapshot.sequence != after:
                return self._snapshot
            changed = self._condition.wait_for(
                lambda: self._closed or subscription._closed or self._snapshot.sequence != after,
                self._heartbeat_seconds,
            )
            if self._closed or subscription._closed:
                return False
            return self._snapshot if changed else None
