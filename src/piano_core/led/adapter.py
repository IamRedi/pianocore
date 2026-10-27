"""LED keyboard adapter (PIANO-P014, ADR-LED-KEYBOARD-V1).

Owns the transport and the canonical LED visualization state. All wire
frames are produced here and sent through :class:`LedTransport`; no raw
serial/protocol bytes are ever exposed to callers (P014 invariant).

Threading model (LED latency must never delay MIDI timing — ADR D4):

- callers enqueue bounded op tuples (never block: drop-oldest on
  overflow, counted) and setters mutate state under one lock;
- ONE worker thread drains the queue and performs the actual sends;
- the ESP32 owns reconnection (firmware 8 s retry); a dead client only
  degrades visualization (``LISTENING``), never playback or input.

Blending semantics (explicit, ADR D3 + ADR-PRACTICE-V1 D5): a key is lit
while EITHER source (``playback`` / ``physical``) holds it, when that
source is visible in the current mode; each source releases only its own
holds. Repeated note-ons are deduplicated per source; clearing is
deterministic.

Practice targets (PIANO-P015): ``practice`` is a third canonical source.
While a practice session is active the practice TARGET is the ONLY
visible source — the deployed single-color protocol cannot distinguish
sources, so physical mirroring is suppressed during WAITING to keep
"which lit key is my target?" unambiguous (playback is impossible then
anyway: the two activities are mutually exclusive). Outside practice the
adapter behaves exactly as P014 shipped it.
"""

from __future__ import annotations

import logging
import queue
import threading

from piano_core.led.model import (
    BRIGHTNESS_MAX,
    BRIGHTNESS_MIN,
    LedConnectionState,
    LedError,
    LedMode,
    LedState,
)
from piano_core.led.protocol import clear_frame, note_off_frame, note_on_frame
from piano_core.led.transport import LedTransport

__all__ = [
    "LedKeyboardAdapter",
    "SOURCE_PRACTICE",
    "SOURCE_PHYSICAL",
    "SOURCE_PLAYBACK",
    "TARGET_VELOCITY",
]

SOURCE_PLAYBACK = "playback"
SOURCE_PHYSICAL = "physical"
SOURCE_PRACTICE = "practice"
_SOURCES = (SOURCE_PLAYBACK, SOURCE_PHYSICAL, SOURCE_PRACTICE)

#: Fixed display intensity for practice target keys (scaled by the
#: existing brightness control; not a musical velocity).
TARGET_VELOCITY = 100

#: Ops buffered for the worker. The keyboard protocol is tiny; 256 ops is
#: generous headroom (a dense chord burst is a handful of ops).
_QUEUE_MAX = 256

#: Maximum ops processed per worker cycle (bounds burst latency).
_DRAIN_LIMIT = 64


def _validate_source(source: object) -> str:
    if source not in _SOURCES:
        raise LedError(f"unknown LED source: {source!r}")
    return source


def _validate_brightness(brightness: object) -> int:
    if not isinstance(brightness, int) or isinstance(brightness, bool):
        raise LedError(
            f"brightness must be an int, got {type(brightness).__name__}"
        )
    if not BRIGHTNESS_MIN <= brightness <= BRIGHTNESS_MAX:
        raise ValueError(f"brightness out of range: {brightness!r}")
    return brightness


class LedKeyboardAdapter:
    """Canonical keyboard LED adapter (single writer to the strip)."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        enabled: bool = False,
        mode: LedMode = LedMode.PLAYBACK,
        brightness: int = 100,
        logger: logging.Logger | None = None,
        transport_factory=None,
    ) -> None:
        self._log = logger or logging.getLogger("piano_core.led")
        self._host = host
        self._port = port
        self._transport_factory = transport_factory
        self._lock = threading.Lock()
        self._enabled = bool(enabled)
        self._mode = mode if isinstance(mode, LedMode) else LedMode.PLAYBACK
        self._brightness = _validate_brightness(brightness)
        self._held: dict[str, dict[int, int]] = {s: {} for s in _SOURCES}
        self._practice_active = False
        self._connection = LedConnectionState.DISABLED
        self._bind_failed = False
        self._last_error: str | None = None
        self._frames_sent = 0
        self._dropped_events = 0

        self._queue: queue.Queue = queue.Queue(maxsize=_QUEUE_MAX)
        self._worker: threading.Thread | None = None
        self._stop_worker = threading.Event()
        self._transport: LedTransport | None = None
        if enabled:
            # Construct the transport eagerly so `start()` binds it; a
            # bind failure degrades (UNAVAILABLE), never raises out of
            # the auxiliary-subsystem path.
            self._transport = self._build_transport()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def _build_transport(self) -> LedTransport:
        if self._transport_factory is not None:
            transport = self._transport_factory(self._host, self._port)
        else:
            transport = LedTransport(
                self._host,
                self._port,
                logger=logging.getLogger("piano_core.led.transport"),
            )
        transport.on_message = self._on_client_message
        transport.on_connect = self._on_client_connect
        transport.on_disconnect = self._on_client_disconnect
        return transport

    def start(self) -> None:
        """Start the worker and, when enabled, bind the transport."""
        if self._worker is not None:
            raise RuntimeError("led adapter already started")
        self._stop_worker.clear()
        self._worker = threading.Thread(
            target=self._work, name="piano-core-led", daemon=True
        )
        self._worker.start()
        if self._enabled:
            self._open_transport()

    def stop(self) -> None:
        """Best-effort clear, then close transport and worker. Idempotent."""
        self._stop_worker.set()
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.join(2.0)
        if self._transport is not None:
            try:
                # Deterministic clear is attempted while the client may
                # still be present; failure is logged, never raised.
                self._transport.send_text(clear_frame())
            except Exception:  # pragma: no cover - defensive
                self._log.exception("led shutdown clear failed")
            self._transport.stop()
            self._transport = None
        with self._lock:
            self._connection = LedConnectionState.DISABLED
            self._held = {s: {} for s in _SOURCES}
            self._practice_active = False
        self._log.info("led adapter stopped")

    def _open_transport(self) -> None:
        """Bind the listening transport (degrade contract, never raise)."""
        transport = self._transport
        if transport is None:
            self._transport = transport = self._build_transport()
        if transport.bound_address is not None:
            return  # already listening
        try:
            transport.start()
        except OSError as exc:
            with self._lock:
                self._bind_failed = True
                self._connection = LedConnectionState.UNAVAILABLE
                self._last_error = f"bind failed: {exc}"
            self._log.error("led transport unavailable: %s", exc)
            return
        with self._lock:
            self._bind_failed = False
            self._connection = LedConnectionState.LISTENING
            self._last_error = None

    # ------------------------------------------------------------------
    # control surface (API-visible)
    # ------------------------------------------------------------------
    def set_enabled(self, enabled: bool) -> None:
        """Enable/disable the subsystem. Disable clears deterministically."""
        if not isinstance(enabled, bool):
            raise LedError("enabled must be a bool")
        with self._lock:
            if enabled == self._enabled:
                return
            self._enabled = enabled
            if enabled:
                self._bind_failed = False
        if enabled:
            if self._transport is None:
                self._transport = self._build_transport()
            self._open_transport()
            self._enqueue(("refresh",))
        else:
            # Clear SYNCHRONOUSLY before closing the listener so the
            # frame can flush while the client is still connected.
            self._transport.send_text(clear_frame())
            with self._lock:
                self._held = {s: {} for s in _SOURCES}
                self._practice_active = False
            self._transport.stop()
            with self._lock:
                self._connection = LedConnectionState.DISABLED

    def set_mode(self, mode: LedMode) -> None:
        """Switch the bounded visualization mode (refreshes the display)."""
        if not isinstance(mode, LedMode):
            raise LedError(f"mode must be a LedMode, got {type(mode).__name__}")
        with self._lock:
            if mode is self._mode:
                return
            self._mode = mode
        self._enqueue(("refresh",))

    def set_brightness(self, brightness: int) -> None:
        """Set the bounded brightness scaling (0..100) and refresh."""
        brightness = _validate_brightness(brightness)
        with self._lock:
            if brightness == self._brightness:
                return
            self._brightness = brightness
        self._enqueue(("refresh",))

    def clear(self) -> None:
        """Deterministic full clear (API diagnostic): blank firmware state."""
        self._enqueue(("hard_clear",))

    def snapshot(self) -> LedState:
        """Current immutable LED state (atomic, honest)."""
        with self._lock:
            connection = self._connection
            if self._enabled:
                if self._transport is not None and self._transport.client_connected:
                    connection = LedConnectionState.CONNECTED
                elif not self._bind_failed:
                    connection = LedConnectionState.LISTENING
            visible = self._visible_notes_locked()
            address = self._transport.bound_address if self._transport else None
            host = address[0] if address else None
            port = address[1] if address else None
            return LedState(
                enabled=self._enabled,
                connection=connection,
                mode=self._mode,
                brightness=self._brightness,
                active_note_count=len(visible),
                practice_active=self._practice_active,
                last_error=self._last_error,
                host=host,
                port=port,
                dropped_events=self._dropped_events,
                frames_sent=self._frames_sent,
            )

    # ------------------------------------------------------------------
    # observation entrypoints (non-blocking by contract)
    # ------------------------------------------------------------------
    def observe_note(self, note: int, velocity: int, source: str, on: bool) -> None:
        """Queue one note lifecycle event (playback OR physical source)."""
        _validate_source(source)
        if not isinstance(note, int) or isinstance(note, bool):
            return
        if not 0 <= note <= 127:
            return
        if on:
            if not isinstance(velocity, int) or isinstance(velocity, bool):
                return
            if not 1 <= velocity <= 127:
                return
            self._enqueue(("note_on", note, velocity, source))
        else:
            self._enqueue(("note_off", note, source))

    def playback_ended(self) -> None:
        """The canonical playback session reached a terminal state."""
        self._enqueue(("clear_source", SOURCE_PLAYBACK))

    def practice_target(self, notes) -> None:
        """Replace the practice target display (PIANO-P015).

        ``notes`` is the expected key set of the CURRENT step; the
        previous target is released deterministically (per-key diffs, no
        full clear) and the new keys are lit.
        """
        if isinstance(notes, (str, bytes)) or not hasattr(notes, "__iter__"):
            raise LedError(
                f"practice target notes must be iterable, got "
                f"{type(notes).__name__}"
            )
        clean: list[int] = []
        for note in notes:
            if not isinstance(note, int) or isinstance(note, bool):
                raise LedError(f"practice target note must be an int: {note!r}")
            if not 0 <= note <= 127:
                raise LedError(f"practice target note out of range: {note!r}")
            if note not in clean:
                clean.append(note)
        self._enqueue(("practice_target", tuple(sorted(clean))))

    def practice_cleared(self) -> None:
        """The practice session released its target display."""
        self._enqueue(("practice_clear",))

    # ------------------------------------------------------------------
    # client callbacks (transport thread)
    # ------------------------------------------------------------------
    def _on_client_message(self, text: str) -> None:
        from piano_core.led.protocol import parse_client_message

        # The deployed firmware sends get_state on connect and nothing
        # else matters; the current display is pushed on connect anyway.
        parse_client_message(text)

    def _on_client_connect(self) -> None:
        with self._lock:
            if not self._bind_failed:
                self._connection = LedConnectionState.CONNECTED
        self._log.info("led keyboard client connected")
        self._enqueue(("refresh",))

    def _on_client_disconnect(self) -> None:
        with self._lock:
            if self._enabled and not self._bind_failed:
                self._connection = LedConnectionState.LISTENING
        self._log.info("led keyboard client disconnected (degraded)")

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _enqueue(self, op: tuple) -> None:
        while True:
            try:
                self._queue.put_nowait(op)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()  # drop oldest
                except queue.Empty:
                    pass
                with self._lock:
                    self._dropped_events += 1

    def _visible_sources_locked(self) -> tuple[str, ...]:
        if not self._enabled:
            return ()
        if self._practice_active:
            # ADR-PRACTICE-V1 D5: during an active practice session the
            # TARGET is the only visible source (single-color hardware —
            # physical mirroring would make the target ambiguous).
            return (SOURCE_PRACTICE,)
        if self._mode is LedMode.OFF:
            return ()
        if self._mode is LedMode.PLAYBACK:
            return (SOURCE_PLAYBACK,)
        if self._mode is LedMode.PHYSICAL:
            return (SOURCE_PHYSICAL,)
        return (SOURCE_PLAYBACK, SOURCE_PHYSICAL)

    def _visible_notes_locked(self) -> dict[int, int]:
        """note -> displayed velocity across visible sources (max wins)."""
        visible: dict[int, int] = {}
        for source in self._visible_sources_locked():
            for note, velocity in self._held[source].items():
                visible[note] = max(visible.get(note, 0), velocity)
        return visible

    def _scaled(self, velocity: int) -> int:
        with self._lock:
            brightness = self._brightness
        if brightness <= 0:
            return 0
        return max(1, round(velocity * brightness / 100))

    def _send(self, frame: str) -> bool:
        transport = self._transport
        if transport is None:
            return False
        sent = transport.send_text(frame)
        if sent:
            with self._lock:
                self._frames_sent += 1
        return sent

    def _work(self) -> None:
        while True:
            try:
                op = self._queue.get(timeout=0.5)
            except queue.Empty:
                if self._stop_worker.is_set():
                    return
                continue
            try:
                self._apply_op(op)
            except Exception:  # the worker must never die on one op
                self._log.exception("led op failed: %r", op)
                with self._lock:
                    self._last_error = "led op failed"
            if self._stop_worker.is_set() and self._queue.empty():
                return

    def _apply_op(self, op: tuple) -> None:
        kind = op[0]
        if kind == "note_on":
            _, note, velocity, source = op
            self._apply_note_on(note, velocity, source)
        elif kind == "note_off":
            _, note, source = op
            self._apply_note_off(note, source)
        elif kind == "clear_source":
            _, source = op
            self._apply_clear_source(source)
        elif kind == "practice_target":
            _, notes = op
            self._apply_practice_target(notes)
        elif kind == "practice_clear":
            self._apply_practice_clear()
        elif kind == "hard_clear":
            with self._lock:
                self._held = {s: {} for s in _SOURCES}
            self._send(clear_frame())
        elif kind == "refresh":
            self._apply_refresh()
        else:  # pragma: no cover - closed op set
            raise LedError(f"unknown led op: {op!r}")

    def _apply_note_on(self, note: int, velocity: int, source: str) -> None:
        with self._lock:
            sources = self._visible_sources_locked()
            visible = source in sources
            already = note in self._held[source]
            if not already:
                self._held[source][note] = velocity
            others_held = any(
                note in self._held[s] for s in _SOURCES if s != source
            )
        if not visible or already:
            return  # state tracked; nothing to display (idempotent)
        scaled = self._scaled(velocity)
        if scaled < 1:
            return  # brightness 0: keep state, suppress display
        if not self._send(note_on_frame(note, scaled)):
            # No client (or dead): visualization degrades, state persists
            # so a reconnect refresh restores the display.
            return

    def _apply_note_off(self, note: int, source: str) -> None:
        with self._lock:
            if note not in self._held[source]:
                return
            del self._held[source][note]
            still_held = any(note in self._held[s] for s in _SOURCES)
            visible = source in self._visible_sources_locked()
        if still_held or not visible:
            return  # union semantics: the other source keeps it lit
        self._send(note_off_frame(note))

    def _apply_clear_source(self, source: str) -> None:
        with self._lock:
            held = sorted(self._held[source])
            self._held[source] = {}
            visible = source in self._visible_sources_locked()
        if not visible:
            return
        for note in held:
            with self._lock:
                still_held = any(
                    note in self._held[s] for s in _SOURCES if s != source
                )
            if not still_held:
                self._send(note_off_frame(note))

    def _still_displayed_locked(self, note: int, skip: str | None = None) -> bool:
        """True when a VISIBLE source still holds ``note`` (post-change).

        ``skip`` excludes one source from the check (the one being
        cleared/replaced). Callers hold ``self._lock``.
        """
        visible = self._visible_sources_locked()
        return any(
            note in self._held[source]
            for source in visible
            if source != skip
        )

    def _apply_practice_target(self, notes: tuple[int, ...]) -> None:
        """Replace the practice target display (per-key diffs, no flicker)."""
        with self._lock:
            old = self._held[SOURCE_PRACTICE]
            removed = [note for note in sorted(old) if note not in notes]
            added = [note for note in notes if note not in old]
            self._held[SOURCE_PRACTICE] = {
                note: TARGET_VELOCITY for note in notes
            }
            self._practice_active = True
            visible = SOURCE_PRACTICE in self._visible_sources_locked()
        if not visible:
            return  # state tracked; a reconnect refresh restores it
        for note in removed:
            with self._lock:
                still_held = self._still_displayed_locked(
                    note, skip=SOURCE_PRACTICE
                )
            if not still_held:
                self._send(note_off_frame(note))
        for note in added:
            scaled = self._scaled(TARGET_VELOCITY)
            if scaled >= 1:
                self._send(note_on_frame(note, scaled))

    def _apply_practice_clear(self) -> None:
        with self._lock:
            self._practice_active = False
            held = sorted(self._held[SOURCE_PRACTICE])
            self._held[SOURCE_PRACTICE] = {}
        for note in held:
            with self._lock:
                still_held = self._still_displayed_locked(
                    note, skip=SOURCE_PRACTICE
                )
            if not still_held:
                self._send(note_off_frame(note))

    def _apply_refresh(self) -> None:
        """Recompute the full display (mode/brightness/reconnect changes)."""
        with self._lock:
            visible = self._visible_notes_locked()
        # One deterministic blank + one frame per visible note: the
        # firmware re-renders atomically per frame anyway, and this
        # guarantees stale keys can never survive a refresh.
        self._send(clear_frame())
        if not visible:
            return
        for note in sorted(visible):
            scaled = self._scaled(visible[note])
            if scaled >= 1:
                self._send(note_on_frame(note, scaled))
