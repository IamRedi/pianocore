"""MIDI input adapter.

Owns the ALSA/mido boundary for Piano Core: endpoint discovery/selection,
connection lifecycle, message normalization. Produces canonical
:class:`~piano_core.midi.events.MidiInputEvent` records through a caller
supplied callback.

Hard boundaries (PIANO-P004):

- INPUT ONLY: the adapter opens input ports and exposes no send/output
  surface; MIDI bytes can never be transmitted to the Roland from here.
- The receive thread produces events and must NEVER call application
  lifecycle APIs such as ``Application.request_stop`` (P001 accepted
  limitation stays unreachable).
- Raw ALSA/mido structures do not leave this package.

Endpoint selection never hard-codes ALSA client numbers: ports are matched
against the configured case-insensitive name matcher (with optional exact
preference). Multiple distinct matches fail closed.
"""

from __future__ import annotations

import enum
import logging
import math
import threading
import time
from typing import Callable

from piano_core.midi.events import (
    SUSTAIN_CC,
    MidiEventType,
    MidiInputEvent,
    note_off,
    note_on,
    sustain,
)

_LOGGER = logging.getLogger("piano_core.midi.input")

#: System/realtime message types that are filtered out (proven legacy behavior).
_IGNORED_MESSAGE_TYPES = frozenset(
    {"active_sensing", "clock", "start", "continue", "stop"}
)


class ConnectionState(enum.Enum):
    """MIDI input connection lifecycle states."""

    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"


class AmbiguousEndpointError(RuntimeError):
    """More than one configured-matcher endpoint exists: fail closed."""


class EndpointNotFoundError(RuntimeError):
    """No endpoint matching the configured matcher exists (retryable)."""


def normalize_message(msg: object, now: float) -> MidiInputEvent | None:
    """Normalize a raw backend message into a canonical event.

    ``msg`` is duck-typed to the proven mido message surface (``type``,
    ``channel``, ``note``, ``velocity``, ``control``, ``value``). Returns
    ``None`` for filtered/invalid input. Canonical rules:

    - NOTE_ON with velocity 0 is normalized to NOTE_OFF;
    - control change 64 becomes SUSTAIN (``pressed`` = value >= 64);
    - channels are 1-based;
    - system/realtime messages are dropped;
    - structurally invalid data (out-of-range values, wrong types) is
      dropped rather than raised - a malformed MIDI stream must never take
      down the input thread.
    """
    msg_type = getattr(msg, "type", None)
    if msg_type in _IGNORED_MESSAGE_TYPES:
        return None
    try:
        channel = int(getattr(msg, "channel", 0)) + 1
        if msg_type == "note_on":
            velocity = int(getattr(msg, "velocity"))
            note = int(getattr(msg, "note"))
            if velocity == 0:
                return note_off(channel, note, 0, now)
            return note_on(channel, note, velocity, now)
        if msg_type == "note_off":
            return note_off(
                channel,
                int(getattr(msg, "note")),
                int(getattr(msg, "velocity")),
                now,
            )
        if msg_type == "control_change":
            control = int(getattr(msg, "control"))
            value = int(getattr(msg, "value"))
            if control == SUSTAIN_CC:
                return sustain(channel, value, now)
        return None
    except (TypeError, ValueError) as exc:
        _LOGGER.debug("dropping invalid midi message: %s", exc)
        return None


def select_input_port(
    candidates: list[str], matcher: str, preferred: str = ""
) -> str:
    """Select the Roland input endpoint from ``candidates``.

    Exact ``preferred`` match wins. Otherwise the single case-insensitive
    ``matcher`` substring match is returned. Zero matches raise
    :class:`EndpointNotFoundError` (retryable); more than one raise
    :class:`AmbiguousEndpointError` (fail closed).
    """
    if preferred:
        for candidate in candidates:
            if candidate == preferred:
                return candidate
    matches = [c for c in candidates if matcher.casefold() in c.casefold()]
    if not matches:
        raise EndpointNotFoundError(
            f"no MIDI input port matching {matcher!r} "
            f"(candidates: {candidates!r})"
        )
    if len(matches) > 1:
        raise AmbiguousEndpointError(
            f"ambiguous MIDI input endpoints for {matcher!r}: {matches!r} "
            "- refusing to guess"
        )
    return matches[0]


def _is_selected_port_invalidation(error: ValueError) -> bool:
    """Recognize RtMidi's stale port-index error after a selected-port open.

    RtMidi is optional for injected/non-RtMidi backends, so resolve its real
    exception class only at this failure boundary. Ordinary ValueError stays
    fatal; neither class names nor error text are used for classification.
    """
    try:
        from rtmidi import InvalidPortError
    except ImportError:
        return False
    return isinstance(error, InvalidPortError)


class MidiInputAdapter:
    """Deterministic input-only MIDI adapter.

    Runs one daemon receive thread that discovers/selects the configured
    endpoint, polls messages, normalizes them, and delivers canonical events
    via ``event_callback``. The thread produces events only; it never calls
    application lifecycle APIs. Endpoint loss is detected by re-listing
    ports at a bounded cadence and transitions the adapter to DISCONNECTED
    with bounded reconnect delays.

    ``on_connection_state`` (optional, PIANO-P006) observes actual connection
    transitions from the reader thread. It is a notification surface only:
    observer exceptions are logged and swallowed so they can never kill the
    reader thread or reach application lifecycle APIs.
    """

    def __init__(
        self,
        backend: object,
        matcher: str = "roland",
        preferred_port: str = "",
        event_callback: Callable[[MidiInputEvent], None] = lambda event: None,
        poll_interval: float = 0.005,
        reconnect_delay: float = 2.0,
        port_check_interval: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        on_connection_state: Callable[[ConnectionState], None] | None = None,
    ) -> None:
        if not matcher or not matcher.strip():
            raise ValueError("matcher must be a non-empty string")
        if any(
            not math.isfinite(value) or value <= 0
            for value in (poll_interval, reconnect_delay, port_check_interval)
        ):
            raise ValueError("poll/reconnect intervals must be positive")
        self._backend = backend
        self._matcher = matcher
        self._preferred_port = preferred_port
        self._event_callback = event_callback
        self._poll_interval = poll_interval
        self._reconnect_delay = reconnect_delay
        self._port_check_interval = port_check_interval
        self._clock = clock
        self._on_connection_state = on_connection_state
        self._stop_requested = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = ConnectionState.DISCONNECTED
        self._state_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._last_error: str | None = None
        self._connected_port: str | None = None
        self._events_received = 0

    @property
    def state(self) -> ConnectionState:
        """Current connection state."""
        with self._state_lock:
            return self._state

    @property
    def last_error(self) -> str | None:
        """Last fatal (non-retryable) error, if any."""
        return self._last_error

    @property
    def connected_port(self) -> str | None:
        """Port name the adapter is connected to, if connected."""
        return self._connected_port

    @property
    def events_received(self) -> int:
        """Count of canonical events delivered to the callback."""
        return self._events_received

    def start(self) -> None:
        """Start the receive thread. One-shot while running."""
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("MIDI input adapter is already running")
            self._stop_requested.clear()
            self._last_error = None
            self._thread = threading.Thread(
                target=self._run, name="piano-core-midi-input", daemon=True
            )
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Request stop, interrupt retry waits and join without holding locks.

        A timed-out backend operation retains its worker identity: start()
        refuses a replacement until that worker has actually exited.
        """
        with self._lifecycle_lock:
            self._stop_requested.set()
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)

    def _set_state(self, state: ConnectionState) -> None:
        """Transition the connection state (change-only) and notify the observer.

        The observer fires outside the lock, only on an actual change, and
        is exception-guarded: a failing observer must never take down the
        reader thread.
        """
        with self._state_lock:
            if state is self._state:
                return
            self._state = state
        observer = self._on_connection_state
        if observer is not None:
            try:
                observer(state)
            except Exception as exc:  # noqa: BLE001 - observer must not kill the thread
                _LOGGER.warning("midi connection observer failed: %s", exc)

    def _run(self) -> None:
        next_port_check = 0.0
        handle = None
        try:
            while not self._stop_requested.is_set():
                now = self._clock()
                if now >= next_port_check:
                    next_port_check = now + self._port_check_interval
                    try:
                        ports = self._backend.list_input_ports()
                    except OSError as exc:
                        self._recover(handle, "port listing", exc)
                        handle = None
                        continue
                    connected_port = self._connected_port
                    if (
                        handle is not None
                        and connected_port is not None
                        and connected_port not in ports
                    ):
                        self._disconnect(handle)
                        handle = None
                        self._set_state(ConnectionState.DISCONNECTED)
                        _LOGGER.warning(
                            "midi endpoint disappeared: %s", connected_port
                        )
                        self._stop_requested.wait(self._reconnect_delay)
                        continue

                if handle is None:
                    self._set_state(ConnectionState.CONNECTING)
                    try:
                        port = select_input_port(
                            self._backend.list_input_ports(),
                            self._matcher,
                            self._preferred_port,
                        )
                    except AmbiguousEndpointError as exc:
                        # Fail closed: refuse to guess; surface and stop retrying.
                        self._last_error = str(exc)
                        self._set_state(ConnectionState.DISCONNECTED)
                        _LOGGER.error("%s", exc)
                        return
                    except OSError as exc:
                        self._recover(None, "port discovery", exc)
                        continue
                    except EndpointNotFoundError:
                        self._set_state(ConnectionState.DISCONNECTED)
                        self._stop_requested.wait(self._reconnect_delay)
                        continue
                    if self._stop_requested.is_set():
                        break
                    _LOGGER.info("selected midi input endpoint: %s", port)
                    try:
                        handle = self._backend.open_input(port)
                    except OSError as exc:
                        self._recover(None, "open", exc)
                        continue
                    except ValueError as exc:
                        if not _is_selected_port_invalidation(exc):
                            raise
                        self._recover(None, "open", exc)
                        continue
                    if self._stop_requested.is_set():
                        break  # finally closes a handle returned during stop.
                    self._connected_port = port
                    self._set_state(ConnectionState.CONNECTED)

                # Catch transport faults only while obtaining the iterator or
                # advancing it. Normalization and consumer callbacks are outside
                # this boundary: their bugs must not become reconnect loops.
                try:
                    pending = iter(self._backend.iter_pending(handle))
                except OSError as exc:
                    self._recover(handle, "read", exc)
                    handle = None
                    continue
                while not self._stop_requested.is_set():
                    try:
                        msg = next(pending)
                    except StopIteration:
                        break
                    except OSError as exc:
                        self._recover(handle, "read", exc)
                        handle = None
                        break
                    if self._stop_requested.is_set():
                        break
                    try:
                        event = normalize_message(msg, self._clock())
                    except (TypeError, ValueError) as exc:
                        _LOGGER.debug("dropping invalid midi message: %s", exc)
                        continue
                    if event is None:
                        continue
                    self._events_received += 1
                    self._event_callback(event)
                if handle is not None:
                    self._stop_requested.wait(self._poll_interval)
        except Exception as exc:
            # Fatal diagnostics, not recovery: programming/configuration errors
            # and consumer failures remain distinct and stop this worker.
            self._last_error = f"{type(exc).__name__}: {exc}"
            _LOGGER.exception("midi input worker failed (non-retryable): %s", exc)
        finally:
            if handle is not None:
                self._disconnect(handle)
            self._set_state(ConnectionState.DISCONNECTED)

    def _recover(self, handle: object, operation: str, error: OSError | ValueError) -> None:
        """Existing disconnect/cleanup/backoff path for verified transport faults.

        Includes python-rtmidi SystemError/NoDevicesError (OSError subclasses).
        Selected-port open may also raise its InvalidPortError (ValueError) if
        the discovered index disappears before open. Never retries arbitrary
        RuntimeError/ValueError or callback exceptions.
        The positive configured delay bounds both retries and fault logging.
        """
        _LOGGER.warning("midi %s failed: %s", operation, error)
        self._set_state(ConnectionState.DISCONNECTED)
        self._disconnect(handle)
        self._stop_requested.wait(self._reconnect_delay)

    def _disconnect(self, handle: object) -> None:
        """Release a backend handle (never raises)."""
        self._connected_port = None
        close = getattr(handle, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:
                _LOGGER.warning("midi input close failed: %s", exc)
