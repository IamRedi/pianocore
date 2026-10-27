"""Piano Core Playback Core (PIANO-P009).

Deterministic Standard MIDI File playback on top of the verified P007
MIDI output foundation:

- :mod:`~piano_core.playback.timeline` — pure SMF -> canonical output
  timeline conversion (tempo map, ordering, sustain; mido touched only
  here, lazily);
- :mod:`~piano_core.playback.engine` — typed playback lifecycle
  (IDLE/PLAYING/PAUSED/STOPPING/COMPLETED/FAILED) with monotonic,
  bounded-cancellable scheduling and owned-only cleanup (STOP, never
  PANIC);
- :mod:`~piano_core.playback.cli` — the bounded ``piano-core-play``
  evidence surface (explicit ``--file`` path; no API, no library
  ownership, no service integration).

Safety boundaries: playback notes flow exclusively through
:class:`~piano_core.midi.output.MidiOutputAdapter` (ownership-tracked),
there is no input->output path, and the P006 StateStore is never
touched by playback.
"""

from piano_core.playback.engine import (
    MonotonicClock,
    PlaybackAlreadyActiveError,
    PlaybackEngine,
    PlaybackError,
    PlaybackState,
)
from piano_core.playback.timeline import (
    MidiFileError,
    TEMPO_SCALE_MAX,
    TEMPO_SCALE_MIN,
    TempoScaleError,
    Timeline,
    TimelineEvent,
    build_timeline,
    load_timeline,
    validate_tempo_scale,
)

__all__ = [
    "MonotonicClock",
    "MidiFileError",
    "PlaybackAlreadyActiveError",
    "PlaybackEngine",
    "PlaybackError",
    "PlaybackState",
    "TEMPO_SCALE_MAX",
    "TEMPO_SCALE_MIN",
    "TempoScaleError",
    "Timeline",
    "TimelineEvent",
    "build_timeline",
    "load_timeline",
    "validate_tempo_scale",
]
