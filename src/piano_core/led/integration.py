"""LED subsystem integration (PIANO-P014, ADR-LED-KEYBOARD-V1).

Composition glue between the canonical subsystems and the LED adapter:

- ``observe_output_command`` — the optional :class:`PlaybackEngine`
  observer. Never raises, never blocks: LED failures can never fail,
  stall, or deschedule playback (ADR D4).
- ``observe_input_event`` — a read-only observer of canonical MIDI input
  events (the StateStore remains the physical-input authority and is
  never touched; the LED merely follows).
- ``session_ended`` — invoked when the playback session reaches a
  terminal state (stop/completion/failure/cancellation): the
  playback-held visualization is cleared.

SUSTAIN / STOP / PANIC commands are deliberately ignored by the LED
observer; only NOTE_ON/NOTE_OFF drive key visuals.
"""

from __future__ import annotations

import logging

from piano_core.led.adapter import SOURCE_PLAYBACK, SOURCE_PHYSICAL, LedKeyboardAdapter
from piano_core.midi.events import MidiEventType
from piano_core.midi.output_events import OutputCommand, OutputCommandType

__all__ = ["LedIntegration"]


class LedIntegration:
    """Owns the LED adapter and the canonical event observation surface."""

    def __init__(self, adapter: LedKeyboardAdapter) -> None:
        if not isinstance(adapter, LedKeyboardAdapter):
            raise TypeError(
                f"adapter must be a LedKeyboardAdapter, "
                f"got {type(adapter).__name__}"
            )
        self.adapter = adapter

    @classmethod
    def from_config(cls, config) -> "LedIntegration":
        """Build the adapter from validated LED configuration.

        The ``AppConfig`` import is deliberately local: ``config`` imports
        :class:`piano_core.led.model.LedMode`, so a module-level import
        here would create an import cycle (composition root vs subsystem).
        """
        from piano_core.led.model import LedMode

        return cls(
            LedKeyboardAdapter(
                config.led_ws_host,
                config.led_ws_port,
                enabled=config.led_enabled,
                mode=LedMode(config.led_mode.value),
                brightness=config.led_brightness,
            )
        )

    def start(self) -> None:
        """Start the adapter (bind + worker) — auxiliary/degrade contract."""
        self.adapter.start()

    def stop(self) -> None:
        """Stop the adapter (best-effort clear, clean close)."""
        self.adapter.stop()

    # ------------------------------------------------------------------
    # observers (safe by contract: they never raise)
    # ------------------------------------------------------------------
    def observe_output_command(self, command: OutputCommand) -> None:
        """PlaybackEngine observer: scheduled note lifecycle → LED."""
        try:
            if command.type is OutputCommandType.NOTE_ON:
                self.adapter.observe_note(
                    command.note, command.velocity, SOURCE_PLAYBACK, on=True
                )
            elif command.type is OutputCommandType.NOTE_OFF:
                self.adapter.observe_note(
                    command.note, 0, SOURCE_PLAYBACK, on=False
                )
            # SUSTAIN / STOP_AUTOMATED_NOTES / PANIC: ignored by design.
        except Exception:  # observers never break playback
            logging.getLogger("piano_core.led").exception(
                "led output observer failed"
            )

    def observe_input_event(self, event) -> None:
        """MIDI input observer: physical key lifecycle → LED (read-only)."""
        try:
            if event.type is MidiEventType.NOTE_ON:
                self.adapter.observe_note(
                    event.note, event.velocity, SOURCE_PHYSICAL, on=True
                )
            elif event.type is MidiEventType.NOTE_OFF:
                self.adapter.observe_note(
                    event.note, 0, SOURCE_PHYSICAL, on=False
                )
        except Exception:  # observers never break input capture
            logging.getLogger("piano_core.led").exception(
                "led input observer failed"
            )

    def session_ended(self) -> None:
        """PlaybackManager callback: terminal session clears playback LEDs."""
        try:
            self.adapter.playback_ended()
        except Exception:  # observers never break playback teardown
            logging.getLogger("piano_core.led").exception(
                "led session-end observer failed"
            )

    # ------------------------------------------------------------------
    # practice display hooks (PIANO-P015; same safe-by-contract discipline)
    # ------------------------------------------------------------------
    def practice_target(self, notes) -> None:
        """PracticeEngine hook: light the current expected target keys."""
        try:
            self.adapter.practice_target(notes)
        except Exception:  # observers never break the practice engine
            logging.getLogger("piano_core.led").exception(
                "led practice target display failed"
            )

    def practice_cleared(self) -> None:
        """PracticeEngine hook: clear the practice target display."""
        try:
            self.adapter.practice_cleared()
        except Exception:  # observers never break the practice engine
            logging.getLogger("piano_core.led").exception(
                "led practice clear failed"
            )
