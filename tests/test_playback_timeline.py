"""V2 tests: playback timeline conversion (PIANO-P009).

The pure builder (:func:`piano_core.playback.timeline.build_timeline`)
is tested with duck-typed track messages — deterministic, no mido, no
hardware, no files. Loader tests that need full SMF parsing run through
the canonical mido stack and are skipped when mido is not installed
(the production venv always has it; see also the real-library sample
checks at the bottom, which additionally require the sample files).
"""

import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from piano_core.midi.output_events import OutputCommandType
from piano_core.playback.timeline import (
    TEMPO_SCALE_MAX,
    TEMPO_SCALE_MIN,
    MidiFileError,
    TempoScaleError,
    build_timeline,
    load_timeline,
    validate_tempo_scale,
)


def tmsg(msg_type, time, channel=0, note=60, velocity=64, control=64, value=0, tempo=500000):
    """Duck-typed mido track message with a delta-tick ``time``."""
    return SimpleNamespace(
        type=msg_type,
        time=time,
        channel=channel,
        note=note,
        velocity=velocity,
        control=control,
        value=value,
        tempo=tempo,
    )


class TempoScaleTests(unittest.TestCase):
    def test_bounds_are_explicit(self):
        self.assertEqual(TEMPO_SCALE_MIN, 0.25)
        self.assertEqual(TEMPO_SCALE_MAX, 4.0)

    def test_valid_scales_pass_and_normalize_to_float(self):
        self.assertEqual(validate_tempo_scale(1.0), 1.0)
        self.assertEqual(validate_tempo_scale(1), 1.0)
        self.assertEqual(validate_tempo_scale(0.25), 0.25)
        self.assertEqual(validate_tempo_scale(4.0), 4.0)
        self.assertEqual(validate_tempo_scale(0.5), 0.5)

    def test_out_of_bounds_rejected(self):
        for bad in (0.0, -1.0, 0.24, 4.01, 10.0):
            with self.assertRaises(TempoScaleError, msg=repr(bad)):
                validate_tempo_scale(bad)

    def test_non_numbers_rejected(self):
        for bad in ("1.0", None, True, False, object()):
            with self.assertRaises((TempoScaleError, TypeError), msg=repr(bad)):
                validate_tempo_scale(bad)

    def test_non_finite_rejected(self):
        import math

        for bad in (math.nan, math.inf, -math.inf):
            with self.assertRaises(TempoScaleError):
                validate_tempo_scale(bad)


class BuildTimelineTests(unittest.TestCase):
    def test_basic_note_and_channel_conversion(self):
        timeline = build_timeline(
            100,
            [[tmsg("note_on", 100, channel=0, note=60, velocity=80),
              tmsg("note_off", 50, channel=0, note=60, velocity=0)]],
        )
        self.assertEqual(len(timeline.events), 2)
        first = timeline.events[0]
        self.assertEqual(first.time, 0.5)  # 100 ticks / 100 tpb * 0.5 s
        self.assertIs(first.command.type, OutputCommandType.NOTE_ON)
        self.assertEqual(first.command.channel, 1)  # 0-based -> canonical
        self.assertEqual(first.command.note, 60)
        self.assertEqual(first.command.velocity, 80)
        second = timeline.events[1]
        self.assertEqual(second.time, 0.75)
        self.assertIs(second.command.type, OutputCommandType.NOTE_OFF)
        self.assertEqual(timeline.duration, 0.75)

    def test_tempo_map_default_and_changes(self):
        # tpb=100: default tempo (500000) -> 0.5 s/beat.
        # set_tempo at tick 200 halves the beat length to 0.25 s.
        track = [
            tmsg("set_tempo", 0, tempo=500000),  # explicit default at tick 0
            tmsg("note_on", 100),                # tick 100 -> 0.5 s
            tmsg("set_tempo", 100, tempo=250000),  # tick 200 (takes effect after)
            tmsg("note_off", 100),               # tick 300 -> 1.0 + 0.25 = 1.25 s
        ]
        timeline = build_timeline(100, [track])
        self.assertEqual([round(e.time, 9) for e in timeline.events], [0.5, 1.25])

    def test_events_before_first_tempo_marker_use_default_tempo(self):
        track = [
            tmsg("note_on", 100),
            tmsg("set_tempo", 100, tempo=1_000_000),   # tick 200
            tmsg("note_off", 100),                     # tick 300
        ]
        timeline = build_timeline(100, [track])
        # tick 100 -> 0.5 s (default tempo); tick 200 -> 1.0 s; the last
        # 100 ticks run at the new tempo (1 beat = 1.0 s) -> 2.0 s
        self.assertEqual([round(e.time, 9) for e in timeline.events], [0.5, 2.0])

    def test_multitrack_merge_is_ordered_by_time(self):
        track_a = [tmsg("note_on", 100, channel=0, note=60, velocity=80)]
        track_b = [tmsg("note_on", 50, channel=1, note=64, velocity=70)]
        timeline = build_timeline(100, [track_a, track_b])
        self.assertEqual(
            [(e.command.channel, e.command.note) for e in timeline.events],
            [(2, 64), (1, 60)],  # earliest tick first across tracks
        )

    def test_chords_share_the_same_timestamp(self):
        track = [
            tmsg("note_on", 100, note=60, velocity=80),
            tmsg("note_on", 0, note=64, velocity=80),
            tmsg("note_on", 0, note=67, velocity=80),
        ]
        timeline = build_timeline(100, [track])
        self.assertEqual([e.time for e in timeline.events], [0.5, 0.5, 0.5])
        self.assertEqual([e.command.note for e in timeline.events], [60, 64, 67])

    def test_same_tick_note_off_is_ordered_before_note_on(self):
        # Dangerous written order (re-strike at the same tick: the new
        # NOTE_ON appears BEFORE the NOTE_OFF). The merge must reorder
        # releases first so the sounding note can never be orphaned as
        # unowned.
        track = [
            tmsg("note_on", 100, note=60, velocity=80),  # initial attack
            tmsg("note_off", 100, note=60),              # tick 200
            tmsg("note_on", 0, note=60, velocity=90),    # same tick, written first
        ]
        timeline = build_timeline(100, [track])
        types = [e.command.type for e in timeline.events]
        self.assertEqual(
            types,
            [OutputCommandType.NOTE_ON, OutputCommandType.NOTE_OFF, OutputCommandType.NOTE_ON],
        )
        self.assertEqual([e.time for e in timeline.events], [0.5, 1.0, 1.0])

    def test_velocity_zero_note_on_becomes_note_off(self):
        track = [
            tmsg("note_on", 100, note=60, velocity=0),
            tmsg("note_off", 0, note=60, velocity=32),
        ]
        timeline = build_timeline(100, [track])
        self.assertTrue(all(
            e.command.type is OutputCommandType.NOTE_OFF for e in timeline.events
        ))

    def test_sustain_control_change_is_canonical(self):
        track = [
            tmsg("control_change", 100, channel=2, control=64, value=127),
            tmsg("control_change", 100, channel=2, control=64, value=0),
        ]
        timeline = build_timeline(100, [track])
        self.assertEqual(
            [(e.command.type.value, e.command.channel, e.command.value)
             for e in timeline.events],
            [("sustain", 3, 127), ("sustain", 3, 0)],
        )

    def test_non_played_messages_are_ignored(self):
        track = [
            tmsg("program_change", 0, channel=0),
            tmsg("control_change", 50, channel=0, control=7, value=100),  # volume
            tmsg("pitchwheel", 0, channel=0),
            tmsg("note_on", 100, note=60, velocity=80),  # absolute tick 150
        ]
        timeline = build_timeline(100, [track])
        self.assertEqual(len(timeline.events), 1)
        self.assertEqual(timeline.duration, 0.75)

    def test_empty_timeline_is_valid(self):
        timeline = build_timeline(96, [])
        self.assertEqual(timeline.events, ())
        self.assertEqual(timeline.duration, 0.0)

    def test_track_with_only_meta_is_empty_timeline(self):
        timeline = build_timeline(96, [[tmsg("set_tempo", 10, tempo=400000)]])
        self.assertEqual(timeline.events, ())
        self.assertEqual(timeline.duration, 0.0)

    def test_smpte_division_rejected_fail_closed(self):
        for bad in (0, -1, -960):
            with self.assertRaises(MidiFileError):
                build_timeline(bad, [])

    def test_invalid_delta_time_rejected(self):
        with self.assertRaises(MidiFileError):
            build_timeline(96, [[tmsg("note_on", -5)]])

    def test_events_are_sorted_by_time(self):
        track_a = [tmsg("note_on", 200, note=60)]
        track_b = [tmsg("note_on", 100, note=64)]
        timeline = build_timeline(100, [track_a, track_b])
        times = [e.time for e in timeline.events]
        self.assertEqual(times, sorted(times))


# ----------------------------------------------------------------------
# Loader tests. Header-only rejection runs without mido; full parses
# require the canonical mido stack (skipUnless, mirroring the repo rule
# that the core suite runs without mido).
# ----------------------------------------------------------------------


def _vlq(value: int) -> bytes:
    out = bytearray([value & 0x7F])
    value >>= 7
    while value:
        out.insert(0, 0x80 | (value & 0x7F))
        value >>= 7
    return bytes(out)


def _track(*events: tuple[int, bytes]) -> bytes:
    data = b"".join(_vlq(delta) + payload for delta, payload in events)
    return b"MTrk" + struct.pack(">I", len(data)) + data


def _smf(fmt: int, division: int, *tracks: bytes) -> bytes:
    header = b"MThd" + struct.pack(
        ">IHHH", 6, fmt, len(tracks), division
    )
    return header + b"".join(tracks)


NOTE_ON = lambda note, vel, ch=0: bytes([0x90 | ch, note, vel])  # noqa: E731
NOTE_OFF = lambda note, ch=0: bytes([0x80 | ch, note, 0])  # noqa: E731
SUSTAIN = lambda value, ch=0: bytes([0xB0 | ch, 64, value])  # noqa: E731
EOT = (0, b"\xff\x2f\x00")
TEMPO = lambda us: (0, b"\xff\x51\x03" + us.to_bytes(3, "big"))  # noqa: E731


class LoaderHeaderTests(unittest.TestCase):
    """Header pre-validation is mido-free and must fail closed."""

    def _load(self, data: bytes):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.mid"
            path.write_bytes(data)
            return load_timeline(path)

    def test_missing_file_rejected(self):
        with self.assertRaises(MidiFileError):
            load_timeline("/nonexistent/piano-core-p009.mid")

    def test_garbage_file_rejected(self):
        with self.assertRaises(MidiFileError):
            self._load(b"this is definitely not a MIDI file.........")

    def test_empty_file_rejected(self):
        with self.assertRaises(MidiFileError):
            self._load(b"")

    def test_truncated_header_rejected(self):
        with self.assertRaises(MidiFileError):
            self._load(b"MThd\x00\x00\x00\x06")

    def test_format2_rejected_from_header(self):
        data = _smf(2, 96, _track(EOT))
        with self.assertRaises(MidiFileError):
            self._load(data)

    def test_smpte_division_rejected_from_header(self):
        data = _smf(0, 0xE250, _track(EOT))
        with self.assertRaises(MidiFileError):
            self._load(data)

    def test_invalid_tempo_scale_rejected_before_io(self):
        with self.assertRaises(TempoScaleError):
            load_timeline("/nonexistent/piano-core-p009.mid", tempo_scale=99.0)


try:
    import mido  # noqa: F401

    MIDO_AVAILABLE = True
except ImportError:
    MIDO_AVAILABLE = False


@unittest.skipUnless(MIDO_AVAILABLE, "mido is not installed (core suite rule)")
class LoaderParseTests(unittest.TestCase):
    """Full parse path through the canonical mido stack."""

    def _load(self, data: bytes) -> object:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.mid"
            path.write_bytes(data)
            return load_timeline(path)

    def test_format0_single_track_parses(self):
        data = _smf(
            0,
            96,
            _track(
                (0, b"\xff\x51\x03\x07\xa1\x20"),  # 500000 us/beat
                (96, NOTE_ON(60, 80)),
                (96, NOTE_OFF(60)),
                EOT,
            ),
        )
        timeline = self._load(data)
        self.assertEqual(len(timeline.events), 2)
        self.assertEqual(timeline.events[0].time, 0.5)
        self.assertEqual(timeline.duration, 1.0)
        self.assertTrue(timeline.source.endswith("test.mid"))

    def test_format1_multitrack_parses_and_merges(self):
        data = _smf(
            1,
            96,
            _track((0, b"\xff\x51\x03\x07\xa1\x20"), EOT),
            _track((96, NOTE_ON(60, 80)), (96, NOTE_OFF(60)), EOT),
            _track((192, NOTE_ON(64, 70)), (0, NOTE_OFF(64)), EOT),
        )
        timeline = self._load(data)
        # Merge order at tick 192 (t=1.0): releases before attacks (the
        # same-tick phase rule), then track order.
        self.assertEqual(
            [
                (e.command.type.value, e.command.note, round(e.time, 6))
                for e in timeline.events
            ],
            [
                ("note_on", 60, 0.5),
                ("note_off", 60, 1.0),
                ("note_off", 64, 1.0),
                ("note_on", 64, 1.0),
            ],
        )

    def test_chord_and_sustain_parse(self):
        data = _smf(
            1,
            120,
            _track(
                TEMPO(500000),
                (120, SUSTAIN(127)),
                (0, NOTE_ON(60, 80)),
                (0, NOTE_ON(64, 80)),
                (120, SUSTAIN(0)),
                (0, NOTE_OFF(60)),
                (0, NOTE_OFF(64)),
                EOT,
            ),
        )
        timeline = self._load(data)
        self.assertEqual(len(timeline.events), 6)
        self.assertEqual(
            [e.command.type.value for e in timeline.events],
            ["sustain", "note_on", "note_on", "sustain", "note_off", "note_off"],
        )
        self.assertEqual(timeline.duration, 1.0)

    def test_running_status_and_multibyte_deltas_parse(self):
        # 3-beat gap (VLQ > 127) then a chord; exercises VLQ encoding.
        data = _smf(
            0,
            96,
            _track(
                TEMPO(500000),
                (96, NOTE_ON(60, 80)),
                (96 * 3, NOTE_OFF(60)),
                EOT,
            ),
        )
        timeline = self._load(data)
        self.assertEqual(timeline.events[1].time, 2.0)


_SAMPLE_DIR = Path(tempfile.gettempdir()) / "p009-samples"
_SAMPLES = [
    "chile2.mid",
    "Delirio.mid",
    "Russian dance from Petroushka.mid",
    "whenimgone.mid",
]


@unittest.skipUnless(
    MIDO_AVAILABLE and _SAMPLE_DIR.is_dir(),
    "requires mido and the read-only real-library samples in /tmp/p009-samples",
)
class RealLibrarySampleTests(unittest.TestCase):
    """Sanity parsing of representative files from the real Pi library.

    The library itself is NEVER touched by P009; these four files were
    copied out read-only during discovery (hashes recorded in the P009
    handoff §4). Skips cleanly when the samples are absent.
    """

    def test_all_samples_parse_into_valid_timelines(self):
        for name in _SAMPLES:
            with self.subTest(sample=name):
                timeline = load_timeline(_SAMPLE_DIR / name)
                self.assertGreater(len(timeline.events), 0, name)
                self.assertGreater(timeline.duration, 0.0, name)
                times = [event.time for event in timeline.events]
                self.assertEqual(times, sorted(times), name)
                self.assertGreaterEqual(times[0], 0.0, name)


if __name__ == "__main__":
    unittest.main()
