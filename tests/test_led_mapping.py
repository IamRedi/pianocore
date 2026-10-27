"""PIANO-P014: keyboard LED mapping tests (golden, firmware-derived).

The mapping table in :mod:`piano_core.led.mapping` must stay identical to
the DEPLOYED ESP32 firmware table (``computeFromStripEnd``, calibration
``PLM10p``, Owner calibration 2026-05-24). The golden fixture is the
Owner's 88-key calibration CSV from the RediOn monorepo docs.
"""

import csv
import unittest
from pathlib import Path

from piano_core.led.mapping import (
    KEY_LED_COUNTS,
    KEY_LED_STARTS,
    LED_STRIP_COUNT,
    MIDI_NOTE_MAX,
    MIDI_NOTE_MIN,
    key_led_range,
    midi_note_to_key,
    midi_to_led_indexes,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "keyboard_led_mapping.csv"

GAPS = {9, 20, 31, 42, 53, 64, 73, 88, 99, 108, 119, 132, 141, 150, 165, 172, 181, 194}


class KeyboardLedMappingTests(unittest.TestCase):
    def _golden_rows(self):
        with FIXTURE.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def test_golden_table_all_88_keys(self):
        """Every one of the 88 rows matches the recovered mapping."""
        rows = self._golden_rows()
        self.assertEqual(len(rows), 88)
        for row in rows:
            key = int(row["tast"]) - 1
            note = int(row["midi"])
            fw_min = int(row["fw_index_min"])
            fw_max = int(row["fw_index_max"])
            key_range = key_led_range(key)
            self.assertIsNotNone(key_range, f"key {key}")
            self.assertEqual(key_range.start, fw_min, f"key {key} start")
            self.assertEqual(key_range.indexes[-1], fw_max, f"key {key} end")
            self.assertEqual(MIDI_NOTE_MIN + key, note, f"key {key} midi")

    def test_lowest_and_highest_key(self):
        a0 = key_led_range(0)
        self.assertEqual((a0.start, a0.count), (192, 4))
        self.assertEqual(a0.indexes, (192, 193, 194, 195))
        c8 = key_led_range(87)
        self.assertEqual((c8.start, c8.count), (0, 2))
        self.assertEqual(c8.indexes, (0, 1))

    def test_middle_c(self):
        c4 = midi_to_led_indexes(60)
        self.assertEqual(c4, (106, 107))

    def test_representative_black_and_white_keys(self):
        # C#4 (black, key 41) and E4 (white, key 43) per the golden table.
        self.assertEqual(midi_to_led_indexes(61), (104, 105))
        self.assertEqual(midi_to_led_indexes(64), (98, 99))

    def test_counts_and_strip_bounds(self):
        self.assertEqual(len(KEY_LED_STARTS), 88)
        self.assertEqual(KEY_LED_COUNTS[0], 4)
        self.assertEqual(sum(KEY_LED_COUNTS), 178)  # 4 + 87*2
        for key in range(88):
            start = KEY_LED_STARTS[key]
            count = KEY_LED_COUNTS[key]
            self.assertGreaterEqual(start, 0)
            self.assertLessEqual(start + count, LED_STRIP_COUNT)

    def test_ranges_never_overlap_and_respect_gaps(self):
        covered: set[int] = set()
        for key in range(88):
            indexes = set(key_led_range(key).indexes)
            self.assertFalse(covered & indexes, f"key {key} overlaps")
            covered |= indexes
        self.assertEqual(len(covered), 178)
        for gap in GAPS:
            self.assertNotIn(LED_STRIP_COUNT - gap, covered, f"gap {gap}")
        self.assertEqual(len(covered) + len(GAPS), LED_STRIP_COUNT)

    def test_out_of_range_notes_rejected(self):
        self.assertIsNone(midi_note_to_key(MIDI_NOTE_MIN - 1))
        self.assertIsNone(midi_note_to_key(MIDI_NOTE_MAX + 1))
        self.assertIsNone(midi_to_led_indexes(20))
        self.assertIsNone(midi_to_led_indexes(109))
        self.assertIsNone(midi_note_to_key(-1))
        self.assertIsNone(midi_note_to_key(128))
        self.assertIsNone(midi_note_to_key("60"))
        self.assertIsNone(midi_note_to_key(True))
        self.assertIsNone(midi_note_to_key(None))

    def test_out_of_range_key_indexes_rejected(self):
        self.assertIsNone(key_led_range(-1))
        self.assertIsNone(key_led_range(88))
        self.assertIsNone(key_led_range("0"))


if __name__ == "__main__":
    unittest.main()
