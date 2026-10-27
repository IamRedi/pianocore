"""V2 tests: canonical MIDI input event model."""

import unittest
from datetime import datetime, timezone

from piano_core.midi.events import (
    SUSTAIN_CC,
    SUSTAIN_PRESSED_THRESHOLD,
    MidiEventType,
    note_off,
    note_on,
    sustain,
)

_T = 12.5  # injected monotonic timestamp


class NoteOnTests(unittest.TestCase):
    def test_builds_valid_event(self):
        event = note_on(1, 60, 99, _T)
        self.assertIs(event.type, MidiEventType.NOTE_ON)
        self.assertEqual(event.channel, 1)
        self.assertEqual(event.note, 60)
        self.assertEqual(event.velocity, 99)
        self.assertEqual(event.timestamp, _T)

    def test_velocity_zero_is_rejected(self):
        """NOTE_ON velocity 0 must be normalized to NOTE_OFF by the parser."""
        with self.assertRaises(ValueError):
            note_on(1, 60, 0, _T)

    def test_channel_bounds(self):
        with self.assertRaises(ValueError):
            note_on(0, 60, 99, _T)
        with self.assertRaises(ValueError):
            note_on(17, 60, 99, _T)

    def test_note_bounds(self):
        with self.assertRaises(ValueError):
            note_on(1, -1, 99, _T)
        with self.assertRaises(ValueError):
            note_on(1, 128, 99, _T)

    def test_velocity_bounds(self):
        with self.assertRaises(ValueError):
            note_on(1, 60, 128, _T)


class NoteOffTests(unittest.TestCase):
    def test_builds_valid_event_with_release_velocity(self):
        event = note_off(16, 21, 5, _T)
        self.assertIs(event.type, MidiEventType.NOTE_OFF)
        self.assertEqual(event.channel, 16)
        self.assertEqual(event.note, 21)
        self.assertEqual(event.velocity, 5)

    def test_zero_release_velocity_allowed(self):
        event = note_off(1, 60, 0, _T)
        self.assertEqual(event.velocity, 0)

    def test_frozen(self):
        event = note_off(1, 60, 0, _T)
        with self.assertRaises(Exception):
            event.note = 61  # type: ignore[misc]


class SustainTests(unittest.TestCase):
    def test_pressed_at_threshold(self):
        event = sustain(1, SUSTAIN_PRESSED_THRESHOLD, _T)
        self.assertTrue(event.pressed)
        self.assertEqual(event.value, SUSTAIN_CC)

    def test_released_below_threshold(self):
        self.assertFalse(sustain(1, 0, _T).pressed)
        self.assertFalse(sustain(1, SUSTAIN_PRESSED_THRESHOLD - 1, _T).pressed)

    def test_value_bounds(self):
        with self.assertRaises(ValueError):
            sustain(1, -1, _T)
        with self.assertRaises(ValueError):
            sustain(1, 128, _T)

    def test_sustain_cc_constant(self):
        self.assertEqual(SUSTAIN_CC, 64)


class TimestampTests(unittest.TestCase):
    def test_injected_monotonic_timestamp_preserved(self):
        self.assertEqual(note_on(1, 60, 1, 1000.25).timestamp, 1000.25)

    def test_non_numeric_timestamp_rejected(self):
        with self.assertRaises(TypeError):
            note_on(1, 60, 1, "now")  # type: ignore[arg-type]

    def test_describe_contains_numeric_metadata(self):
        text = note_on(1, 60, 99, _T).describe()
        self.assertIn("note_on", text)
        self.assertIn("60", text)
        self.assertIn("velocity=99", text)


if __name__ == "__main__":
    unittest.main()
