"""Canonical MIDI output adapter (PIANO-P007).

Owns the Roland output boundary for Piano Core: stable fail-closed
endpoint discovery/selection, output-port lifecycle, serialization of
typed :mod:`~piano_core.midi.output_events` commands through the
injected backend, and **output ownership** — the set of notes Piano Core
itself started and has not yet released.

HARD RULE — input/output separation: nothing in this module (or anywhere
in Piano Core) connects :class:`~piano_core.midi.events.MidiInputEvent`
to output commands. There is no MIDI thru, echo, loopback, mirroring, or
automatic replay of played keys; human playing can never cause Piano
Core to send MIDI back to the Roland. The P006 StateStore physical-input
state (``pressed_notes``) is never touched by output commands.

STOP vs PANIC (canonical, frozen spec §5/§11 heritage):

- ``STOP_AUTOMATED_NOTES`` releases **only** notes Piano Core owns, and
  releases Piano Core-owned sustain if the adapter itself set it. It is
  a no-op when nothing is owned. It never sends a global sweep.
- ``PANIC`` is the global emergency silence (per channel: sustain off,
  all-notes-off CC123, all-sound-off CC120 — the deployed legacy
  heritage's control strategy, §11). It is broader than STOP, is never
  invoked automatically by the Application, and is never silently
  substituted for ordinary stop.

Commands execute synchronously and serially under one lock (no playback
thread, scheduler, or queue — those belong to a later playback task).
The output connection observer only marks playback faults and publishes
ownership; it never invokes application stop/start APIs or receives input.
"""

from __future__ import annotations

import logging
import math
import threading

from piano_core.midi.backends import OutputOpenCleanupError
from piano_core.midi.input import (
    AmbiguousEndpointError,
    EndpointNotFoundError,
    select_input_port,
    _is_selected_port_invalidation,
)
from piano_core.midi.output_events import (
    ALL_NOTES_OFF_CC,
    ALL_SOUND_OFF_CC,
    CHANNEL_MAX,
    CHANNEL_MIN,
    SUSTAIN_CC,
    OutputCommand,
    OutputCommandType,
)

__all__ = [
    "AmbiguousEndpointError",
    "EndpointNotFoundError",
    "MidiOutputAdapter",
    "OutputNotOpenError",
]

#: Sustain threshold mirrored from the canonical input model (value >= 64).
_SUSTAIN_PRESSED_THRESHOLD = 64


class OutputNotOpenError(RuntimeError):
    """A command arrived before the output port was opened: fail closed."""


class _SelectedOutputPortInvalidated(RuntimeError):
    """The real RtMidi stale-index error at the selected-output open only."""


class _OutputCloseFailed(RuntimeError):
    """Retained output must be safely closed before any replacement opens."""


class MidiOutputAdapter:
    """Synchronous, ownership-tracking canonical MIDI output adapter.

    Commands are validated :class:`OutputCommand` records; raw backend
    messages are constructed exclusively by the injected backend. One
    internal lock serializes commands so future concurrent callers remain
    deterministic.
    """

    def __init__(
        self,
        backend,
        matcher: str = "roland",
        preferred_port: str = "",
        logger: logging.Logger | None = None,
    ) -> None:
        if not matcher or not matcher.strip():
            raise ValueError("matcher must be a non-empty string")
        self._backend = backend
        self._matcher = matcher
        self._preferred_port = preferred_port
        self._log = logger or logging.getLogger("piano_core.midi.output")
        self._lock = threading.RLock()
        self._output = None
        self._retired_output = None
        self._close_error: str | None = None
        self._shutdown = threading.Event()
        self._port_name: str | None = None
        self._owned: set[tuple[int, int]] = set()
        self._sustain_pressed_channels: set[int] = set()
        self._commands_sent = 0
        self._generation = 0
        self._monitor_stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._monitor_lifecycle = threading.Lock()
        self.last_error: str | None = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._output is not None

    @property
    def close_pending(self) -> bool:
        """Unavailable output retained until backend release is confirmed."""
        with self._lock:
            return self._retired_output is not None

    @property
    def port_name(self) -> str | None:
        """Selected output port name while open (derived evidence only)."""
        with self._lock:
            return self._port_name

    @property
    def owned_notes(self) -> tuple[tuple[int, int], ...]:
        """Sorted (channel, note) pairs Piano Core currently owns."""
        with self._lock:
            return tuple(sorted(self._owned))

    @property
    def owned_sustain_channels(self) -> tuple[int, ...]:
        """Sorted channels on which the adapter itself set sustain pressed."""
        with self._lock:
            return tuple(sorted(self._sustain_pressed_channels))

    @property
    def commands_sent(self) -> int:
        """Count of backend sends performed through this adapter."""
        with self._lock:
            return self._commands_sent

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def apply_for_generation(self, command: OutputCommand, generation: int) -> None:
        """Never transmit a command belonging to a disconnected session."""
        with self._lock:
            if generation != self._generation:
                raise OutputNotOpenError("MIDI output connection changed during playback")
            self.apply(command)

    def open(self) -> str:
        """Discover and open the output port. Idempotent; returns the name.

        Selection reuses the proven fail-closed semantics: exact
        ``preferred_port`` wins, otherwise the single case-insensitive
        ``matcher`` match; zero matches raise
        :class:`EndpointNotFoundError`, multiple matches raise
        :class:`AmbiguousEndpointError` — never an arbitrary choice.
        """
        with self._lock:
            if self._shutdown.is_set():
                raise OutputNotOpenError("MIDI output shutdown forbids reopening")
            if self._output is not None:
                return self._port_name
            self._close_retired_locked()
            if self._shutdown.is_set():
                raise OutputNotOpenError("MIDI output shutdown during close reconciliation")
            ports = self._backend.list_output_ports()
            port = select_input_port(ports, self._matcher, self._preferred_port)
            try:
                output = self._backend.open_output(port)
            except OutputOpenCleanupError as exc:
                # Backend initialization acquired a handle but could not
                # release it. Preserve it even though open never returned.
                self._retired_output = exc.output
                raise
            except ValueError as exc:
                if not _is_selected_port_invalidation(exc):
                    raise
                raise _SelectedOutputPortInvalidated(str(exc)) from exc
            self._retired_output = output
            try:
                if self._shutdown.is_set():
                    raise OutputNotOpenError("MIDI output shutdown during open")
                # Disconnection retains ownership. Release only that state
                # on the fresh connection BEFORE making it available.
                self._release_owned_locked(output)
                if self._shutdown.is_set():
                    raise OutputNotOpenError("MIDI output shutdown during cleanup")
            except Exception:
                self._close_retired_locked()
                raise
            self._retired_output = None
            self._output = output
            self._port_name = port
        self._log.info("midi output open matcher=%s port=%s", self._matcher, port)
        return port

    def close(self) -> None:
        """Close the output port. Idempotent; pure resource cleanup.

        Callers must release owned notes (``STOP_AUTOMATED_NOTES``) before
        closing; ``close()`` never sends MIDI itself.
        """
        with self._lock:
            output = self._output
            self._output = None
            self._port_name = None
            if output is not None:
                self._retired_output = output
                self._generation += 1
                # Serialize the physical close with open(): even a slow
                # close must not overlap a second live output handle.
            self._close_retired_locked()
        if output is not None:
            self._log.info("midi output closed")

    def _close_retired_locked(self) -> None:
        output = self._retired_output
        if output is None:
            return
        try:
            self._backend.close_output(output)
        except Exception as exc:
            self._close_error = f"MIDI output close unresolved: {exc!r}"
            self.last_error = self._close_error
            raise _OutputCloseFailed(self._close_error) from exc
        self._retired_output = None
        if self.last_error == self._close_error:
            self.last_error = None
        self._close_error = None

    def start_monitor(self, on_connection_change, interval: float = 0.2) -> None:
        """One bounded output-only health/reconnect worker; sends no attacks."""
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("output monitor interval must be positive and finite")
        with self._monitor_lifecycle:
            if self._shutdown.is_set():
                raise OutputNotOpenError("MIDI output shutdown forbids monitoring")
            if self._monitor_thread is not None and self._monitor_thread.is_alive():
                raise RuntimeError("MIDI output monitor is already running")
            self._monitor_stop.clear()
            self.last_error = None
            self._monitor_thread = threading.Thread(
                target=self._monitor, args=(on_connection_change, interval),
                name="piano-core-midi-output", daemon=True,
            )
            self._monitor_thread.start()

    def stop_monitor(self, timeout: float = 2.0, *, shutdown: bool = False) -> None:
        with self._monitor_lifecycle:
            if shutdown:
                self._shutdown.set()
            self._monitor_stop.set()
            thread = self._monitor_thread
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                raise RuntimeError("MIDI output monitor did not stop")

    def _monitor(self, notify, interval: float) -> None:
        notifying = False
        loss_notified = False
        try:
            while not self._monitor_stop.wait(interval):
                with self._lock:
                    was_open = self._output is not None
                    if was_open:
                        ports = self._backend.list_output_ports()
                        probe = getattr(self._backend, "is_output_connected", None)
                        healthy = self._port_name in ports and (
                            probe is None or probe(self._output)
                        )
                        if healthy:
                            continue
                        try:
                            self.close()
                        except _OutputCloseFailed:
                            # Retained, unavailable, never a replacement yet.
                            pass
                if was_open:
                    self._log.warning("MIDI output disconnected; stale session cannot replay")
                    # Outside the output lock: consumers only mark fault and
                    # publish state; they never receive physical input here.
                    notifying = True
                    notify(False)
                    notifying = False
                    loss_notified = True
                    continue
                if self.close_pending and not loss_notified:
                    notifying = True
                    notify(False)
                    notifying = False
                    loss_notified = True
                try:
                    self.open()
                except (EndpointNotFoundError, AmbiguousEndpointError, OSError,
                        _SelectedOutputPortInvalidated, _OutputCloseFailed) as exc:
                    self._log.debug("MIDI output reconnect unavailable: %s", exc)
                    continue
                except OutputNotOpenError:
                    if self._monitor_stop.is_set():
                        return
                    raise
                if self._monitor_stop.is_set():
                    self.close()
                    return
                notifying = True
                notify(True)
                notifying = False
                loss_notified = False
        except Exception as exc:
            # Configuration, discovery, probe and observer failures are not
            # relabelled as reconnect races or hidden behind retry loops.
            failure = repr(exc)
            try:
                self.close()
            except _OutputCloseFailed as close_error:
                failure += f"; retained close failure: {close_error}"
            self.last_error = failure
            self._log.error("MIDI output monitor failed: %s", exc)
            if not notifying:
                try:
                    notify(False)
                except Exception as observer_error:
                    self._log.error("MIDI output fault observer failed: %s", observer_error)

    # ------------------------------------------------------------------
    # commands
    # ------------------------------------------------------------------
    def apply(self, command: OutputCommand) -> None:
        """Apply one canonical output command (fail closed if not open)."""
        if not isinstance(command, OutputCommand):
            raise TypeError(
                "apply requires a canonical OutputCommand, "
                f"got {type(command).__name__}"
            )
        with self._lock:
            output = self._output
            if output is None:
                raise OutputNotOpenError(
                    "midi output port is not open; call open() first"
                )
            if command.type is OutputCommandType.NOTE_ON:
                self._backend.send_note_on(
                    output, command.channel, command.note, command.velocity
                )
                # Duplicate NOTE_ON re-sends the command (retrigger) while
                # ownership stays a single (channel, note) entry.
                self._owned.add((command.channel, command.note))
                self._commands_sent += 1
            elif command.type is OutputCommandType.NOTE_OFF:
                # NOTE_OFF is always forwarded; ownership is discarded when
                # present and unaffected for unowned notes (explicit policy).
                self._backend.send_note_off(
                    output,
                    command.channel,
                    command.note,
                    command.release_velocity,
                )
                self._owned.discard((command.channel, command.note))
                self._commands_sent += 1
            elif command.type is OutputCommandType.SUSTAIN:
                self._backend.send_control_change(
                    output, command.channel, SUSTAIN_CC, command.value
                )
                if command.value >= _SUSTAIN_PRESSED_THRESHOLD:
                    self._sustain_pressed_channels.add(command.channel)
                else:
                    self._sustain_pressed_channels.discard(command.channel)
                self._commands_sent += 1
            elif command.type is OutputCommandType.STOP_AUTOMATED_NOTES:
                sustain_count = len(self._sustain_pressed_channels)
                released = self._release_owned_locked(output)
                self._log.info(
                    "midi output stop released_notes=%d sustain_released=%d",
                    len(released),
                    sustain_count,
                )
            elif command.type is OutputCommandType.PANIC:
                self._panic_locked(output)
            else:  # pragma: no cover - the canonical enum is closed
                raise ValueError(f"unsupported output command: {command.type!r}")

    def stop_automated_notes(self) -> int:
        """Convenience wrapper: apply STOP; returns the note count released."""
        before = len(self.owned_notes)
        self.apply(
            OutputCommand(type=OutputCommandType.STOP_AUTOMATED_NOTES)
        )
        return before

    # ------------------------------------------------------------------
    # internals (callers hold self._lock)
    # ------------------------------------------------------------------
    def _release_owned_locked(self, output) -> list[tuple[int, int]]:
        """STOP: release only Piano Core-owned notes (+ owned sustain).

        Ownership is reconciled after each successful backend send. If a
        later release raises, the remaining sets therefore describe only
        output that may still be sounding.
        """
        released = sorted(self._owned)
        for channel, note in released:
            self._backend.send_note_off(output, channel, note, 0)
            self._owned.discard((channel, note))
            self._commands_sent += 1
        for channel in sorted(self._sustain_pressed_channels):
            self._backend.send_control_change(output, channel, SUSTAIN_CC, 0)
            self._sustain_pressed_channels.discard(channel)
            self._commands_sent += 1
        return released

    def _panic_locked(self, output) -> None:
        """PANIC: global emergency silence (legacy §11 control heritage)."""
        for channel in range(CHANNEL_MIN, CHANNEL_MAX + 1):
            self._backend.send_control_change(output, channel, SUSTAIN_CC, 0)
            self._backend.send_control_change(
                output, channel, ALL_NOTES_OFF_CC, 0
            )
            self._backend.send_control_change(
                output, channel, ALL_SOUND_OFF_CC, 0
            )
        self._commands_sent += (CHANNEL_MAX - CHANNEL_MIN + 1) * 3
        self._owned.clear()
        self._sustain_pressed_channels.clear()
        self._log.warning("midi output PANIC applied (global silence)")
