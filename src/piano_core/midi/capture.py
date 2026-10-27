"""Bounded diagnostic capture of canonical MIDI input events (INPUT ONLY).

Runs the input adapter for a bounded time and emits each canonical event as
one JSON line. Used for appliance evidence capture (PIANO-P004). Contains no
output capability: it opens input ports only.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from piano_core.logging_setup import configure_logging
from piano_core.midi.backends import MidoBackend
from piano_core.midi.input import MidiInputAdapter

_SUMMARY = {"note_on": 0, "note_off": 0, "sustain": 0}


def event_matches_filter(event, type_filter: str) -> bool:
    """Return True if ``event`` passes the ``--type`` filter."""
    return type_filter == "all" or event.type.value == type_filter


def _event_line(event) -> str:
    payload = {
        "type": event.type.value,
        "channel": event.channel,
        "note": event.note,
        "velocity": event.velocity,
        "value": event.value,
        "pressed": event.pressed,
        "t": round(event.timestamp, 6),
    }
    _SUMMARY[event.type.value] = _SUMMARY.get(event.type.value, 0) + 1
    return json.dumps(payload, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="piano-core-midi-capture",
        description=(
            "Bounded diagnostic capture of canonical MIDI input events "
            "(input only; the adapter cannot send MIDI)."
        ),
    )
    parser.add_argument(
        "--matcher",
        default="roland",
        help="case-insensitive input port name matcher (default: roland)",
    )
    parser.add_argument(
        "--run-for",
        type=float,
        default=60.0,
        metavar="SECONDS",
        help="capture for at most SECONDS seconds (default: 60)",
    )
    parser.add_argument(
        "--type",
        choices=["all", "note_on", "note_off", "sustain"],
        default="all",
        help="capture only events of this type (default: all)",
    )
    parser.add_argument(
        "--output",
        default="-",
        metavar="PATH",
        help="JSON-lines output path ('-' = stdout, default)",
    )
    args = parser.parse_args(argv)

    stream = sys.stdout if args.output == "-" else open(args.output, "w", encoding="utf-8")
    # DEBUG: evidence capture wants per-message detail from the adapter.
    configure_logging(level=logging.DEBUG, stream=sys.stderr)

    received: list[str] = []

    def on_event(event) -> None:
        if not event_matches_filter(event, args.type):
            return
        line = _event_line(event)
        received.append(line)
        print(line, file=stream, flush=True)

    adapter = MidiInputAdapter(
        MidoBackend(),
        matcher=args.matcher,
        event_callback=on_event,
        reconnect_delay=1.0,
    )
    print(
        f"capturing midi input (matcher={args.matcher!r}) for {args.run_for}s; "
        "input only - the adapter cannot send midi",
        file=sys.stderr,
        flush=True,
    )
    adapter.start()
    try:
        time.sleep(args.run_for)
    except KeyboardInterrupt:
        pass
    finally:
        adapter.stop()

    summary = {
        "state": adapter.state.value,
        "connected_port": adapter.connected_port,
        "events_received": adapter.events_received,
        "note_on": _SUMMARY.get("note_on", 0),
        "note_off": _SUMMARY.get("note_off", 0),
        "sustain": _SUMMARY.get("sustain", 0),
        "last_error": adapter.last_error,
    }
    print(json.dumps({"summary": summary}, sort_keys=True), file=stream, flush=True)
    print(f"capture summary: {summary}", file=sys.stderr, flush=True)
    if stream is not sys.stdout:
        stream.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
