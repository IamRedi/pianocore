"""``piano-core-play``: bounded CLI surface for the P009 Playback Core.

Plays ONE Standard MIDI File from an explicitly supplied path through
the canonical output foundation. This is the minimal evidence surface
authorized for PIANO-P009 — there is deliberately no library browsing,
no API, and no service integration (the production service's output
stays at its disabled safe default; this CLI opens its own dedicated
adapter as an explicit operator action, exactly like
``piano-core-midi-capture`` mirrors the input side).

Behavior:

- configuration via ``PIANO_CORE_*`` env (output port matcher, log
  level) through :class:`~piano_core.config.AppConfig.from_env`;
- SIGINT/SIGTERM are the designed STOP path: owned notes/sustain are
  released and the process exits 0 (intentional stop);
- ``--stop-after SECONDS`` is a wall-clock safety cap for bounded
  verification (intentional stop, exit 0);
- a non-blocking cross-process advisory lock guarantees at most ONE
  automated playback per appliance (in-process single-active is
  enforced by the engine itself). PIANO-P012: the CLI and the service's
  PlaybackManager share ONE lock in the common ``/run/lock`` directory
  (never per-process ``/tmp`` — the systemd unit sets ``PrivateTmp``),
  so a CLI playback and an API playback can never run concurrently.

Exit codes: ``0`` ended without product failure (COMPLETED or
intentional stop), ``1`` failure (file, lock, endpoint, playback
error), ``2`` usage/configuration error.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from collections.abc import Callable

from piano_core import PRODUCT_NAME, RUNTIME_IDENTIFIER, __version__
from piano_core.config import AppConfig, ConfigError
from piano_core.logging_setup import configure_logging
from piano_core.midi.backends import MidoOutputBackend
from piano_core.midi.input import AmbiguousEndpointError, EndpointNotFoundError
from piano_core.midi.output import MidiOutputAdapter
from piano_core.playback.advisory import (
    LOCK_FILENAME,
    acquire_playback_lock,
    default_lock_path,
)
from piano_core.playback.engine import PlaybackEngine, PlaybackState
from piano_core.playback.timeline import (
    MidiFileError,
    TempoScaleError,
    Timeline,
    load_timeline,
    validate_tempo_scale,
)

__all__ = ["LOCK_FILENAME", "main"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="piano-core-play",
        description=(
            "Play one Standard MIDI File on the Roland through the Piano Core "
            "output foundation (PIANO-P009 bounded CLI)."
        ),
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="print product identity and exit",
    )
    parser.add_argument(
        "--file",
        type=str,
        default=None,
        metavar="PATH",
        help="path of the Standard MIDI File to play (read-only; required)",
    )
    parser.add_argument(
        "--tempo",
        type=float,
        default=1.0,
        metavar="SCALE",
        help=(
            "playback tempo multiplier (0.25..4.0; 1.0 = original file tempo; "
            "the source file is never modified)"
        ),
    )
    parser.add_argument(
        "--stop-after",
        type=float,
        default=None,
        metavar="SECONDS",
        help="wall-clock safety cap: stop playback after SECONDS (optional)",
    )
    return parser


def main(
    argv: list[str] | None = None,
    backend_factory: Callable[[], object] | None = None,
    clock: object | None = None,
    env: object = None,
    lock_path: str | None = None,
) -> int:
    """Console entrypoint. Returns a process exit code (see module docstring)."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f"{PRODUCT_NAME} {__version__} ({RUNTIME_IDENTIFIER})")
        return 0

    if args.file is None:
        print("piano-core-play: --file PATH is required", file=sys.stderr)
        return 2
    if args.stop_after is not None and args.stop_after <= 0:
        print("piano-core-play: --stop-after must be > 0", file=sys.stderr)
        return 2
    try:
        config = AppConfig.from_env(env)
        validate_tempo_scale(args.tempo)
    except (ConfigError, TempoScaleError) as error:
        print(f"piano-core-play: invalid configuration: {error}", file=sys.stderr)
        return 2

    configure_logging(config.log_level)
    log = logging.getLogger("piano_core.playback.cli")

    lock = None
    adapter: MidiOutputAdapter | None = None
    exit_code = 1
    try:
        # Cross-process single-playback lock first: a second playback is
        # refused before any file is touched.
        resolved_lock_path = lock_path or default_lock_path()
        try:
            lock = acquire_playback_lock(resolved_lock_path, log)
        except OSError as error:
            log.error("playback refused: %s", error)
            return 1

        try:
            timeline: Timeline = load_timeline(args.file, tempo_scale=args.tempo)
        except MidiFileError as error:
            log.error("playback refused: %s", error)
            return 1
        log.info(
            "timeline loaded source=%s events=%d duration=%.3fs",
            timeline.source,
            len(timeline.events),
            timeline.duration,
        )

        try:
            backend = backend_factory() if backend_factory else MidoOutputBackend()
        except Exception as exc:
            log.error("midi output unavailable (backend error): %s", exc)
            return 1
        adapter = MidiOutputAdapter(
            backend=backend, matcher=config.midi_output_port_matcher
        )
        try:
            adapter.open()
        except EndpointNotFoundError as exc:
            log.error("midi output unavailable (endpoint): %s", exc)
            return 1
        except AmbiguousEndpointError as exc:
            log.error("midi output refused (ambiguous endpoint): %s", exc)
            return 1
        except Exception as exc:
            log.error("midi output unavailable (open failed): %s", exc)
            return 1

        engine = PlaybackEngine(adapter, clock=clock)

        stop_signal = threading.Event()

        def _handler(signum, frame):  # noqa: ANN001 (signal API)
            stop_signal.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            if threading.current_thread() is threading.main_thread():
                signal.signal(sig, _handler)

        started = _now(clock)
        engine.play(timeline, tempo_scale=args.tempo)

        deadline = (
            _now(clock) + args.stop_after if args.stop_after is not None else None
        )
        stop_issued = False
        while not engine.wait(0.2):
            should_stop = False
            if stop_signal.is_set():
                should_stop = True
            elif deadline is not None and _now(clock) >= deadline:
                should_stop = True
            if should_stop and not stop_issued:
                stop_issued = True
                log.info("playback stop requested (signal or safety cap)")
                engine.stop()
        wall = _now(clock) - started
        engine.stop()  # no-op safety net (returns False when already terminal)

        outcome = engine.state
        if outcome is PlaybackState.COMPLETED or outcome is PlaybackState.IDLE:
            exit_code = 0
        else:
            exit_code = 1
        log.info(
            "playback summary outcome=%s events_sent=%d wall=%.3fs error=%s",
            outcome.value,
            engine.events_sent,
            wall,
            engine.error,
        )
        print(
            f"piano-core-play: outcome={outcome.value} "
            f"events_sent={engine.events_sent} wall={wall:.3f}s"
        )
    finally:
        if adapter is not None and adapter.is_open:
            adapter.close()
            log.info("midi output closed")
        if lock is not None:
            lock.release()
            log.info("playback lock released")
    return exit_code


def _now(clock: object) -> float:
    """Read the injected clock (or the wall monotonic clock)."""
    if clock is not None:
        return clock.monotonic()
    return time.monotonic()
