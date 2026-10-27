"""Keyboard note -> LED index mapping (PIANO-P014).

Mirrors the DEPLOYED ESP32 firmware table exactly (project
"PLUTO PIANO BRAIN", ``src/led_test/led_key_map.cpp::computeFromStripEnd()``,
calibration magic ``PLM10p``, Owner calibration 2026-05-24). Mapping values
are RECOVERED, never invented (P014 invariant).

Physical strip facts (firmware ``include/led_test_config.h``):

- 196 WS2812 LEDs, 0-based firmware indices ``0..195``;
- firmware index = ``196 - human`` where "human #1" is the physical strip
  END on the A0 (leftmost) side;
- key 0 (A0, MIDI 21) uses 4 LEDs (human 1..4 = indices 192..195);
- keys 1..87 use 2 LEDs each;
- 18 gap LEDs belong to NO key (compensation for black-key spacing).

The canonical conversion chain is::

    MIDI note 21..108 -> key index 0..87 -> [start, start + count)

Out-of-range notes are rejected by contract (the firmware ignores them
too).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "KEY_LED_COUNTS",
    "KEY_LED_STARTS",
    "KEY_RANGE",
    "LED_STRIP_COUNT",
    "MIDI_NOTE_MAX",
    "MIDI_NOTE_MIN",
    "PianoKeyLeds",
    "key_led_range",
    "midi_note_to_key",
    "midi_to_led_indexes",
]

#: Physical WS2812 LEDs on the keyboard strip (firmware LED_STRIP_COUNT).
LED_STRIP_COUNT = 196

#: Inclusive MIDI range of the standard 88-key piano (A0 .. C8).
MIDI_NOTE_MIN = 21
MIDI_NOTE_MAX = 108

#: Inclusive key-index range (0-based, A0=0 .. C8=87).
KEY_RANGE = range(0, 88)


def _start_from_human_led(human_led: int) -> int:
    """Human LED # (1 = physical strip end / A0 side) -> 0-based index."""
    if human_led < 1:
        return 0
    return LED_STRIP_COUNT - human_led


def _compute_key_starts() -> tuple[int, ...]:
    """Replicate the firmware ``computeFromStripEnd()`` table verbatim.

    Kept line-for-line faithful to ``src/led_test/led_key_map.cpp``
    (including its explicit corrections after the physical calibration,
    e.g. key 28/29 share exactly ONE gap LED, #64) so this mapping and
    the flashed firmware can never drift apart silently. Golden-tested
    against ``tests/fixtures/keyboard_led_mapping.csv``.
    """
    starts = [0] * 88
    starts[0] = _start_from_human_led(4)   # tast 1: human 1-4
    starts[1] = _start_from_human_led(6)   # tast 2: 5-6
    starts[2] = _start_from_human_led(8)   # tast 3: 7-8
    starts[3] = _start_from_human_led(11)  # tast 4: 10-11 (gap 9)

    human = 13
    for k in range(4, 8):  # tast 5..8
        starts[k] = _start_from_human_led(human)
        human += 2

    human = 22  # tast 9 (gap 20)
    for k in range(8, 13):  # tast 9..13
        starts[k] = _start_from_human_led(human)
        human += 2

    human = 33  # tast 14 (gap 31)
    for k in range(13, 18):  # tast 14..18
        starts[k] = _start_from_human_led(human)
        human += 2

    human = 44  # tast 19 (gap 42)
    for k in range(18, 23):  # tast 19..23
        starts[k] = _start_from_human_led(human)
        human += 2

    human = 55  # tast 24 (gap 53)
    for k in range(23, 27):  # tast 24..27
        starts[k] = _start_from_human_led(human)
        human += 2

    # Explicit after tast 28: ONE empty LED (#64), tast 29 = #65-66.
    starts[27] = _start_from_human_led(63)  # tast 28 -> human 62-63
    starts[28] = _start_from_human_led(66)  # tast 29 -> human 65-66

    human = 68  # tast 30..32
    for k in range(29, 32):
        starts[k] = _start_from_human_led(human)
        human += 2

    starts[31] = _start_from_human_led(72)  # tast 32 -> human 71-72
    starts[32] = _start_from_human_led(75)  # tast 33 -> human 74-75 (gap #73)

    human = 77  # tast 34..39
    for k in range(33, 39):
        starts[k] = _start_from_human_led(human)
        human += 2

    starts[38] = _start_from_human_led(87)  # tast 39 -> human 86-87
    starts[39] = _start_from_human_led(90)  # tast 40 -> human 89-90 (gap #88)

    human = 92  # tast 41..44
    for k in range(40, 44):
        starts[k] = _start_from_human_led(human)
        human += 2

    starts[43] = _start_from_human_led(98)   # tast 44 -> human 97-98
    starts[44] = _start_from_human_led(101)  # tast 45 -> human 100-101 (gap #99)

    human = 103  # tast 46..48
    for k in range(45, 48):
        starts[k] = _start_from_human_led(human)
        human += 2

    starts[47] = _start_from_human_led(107)  # tast 48 -> human 106-107
    starts[48] = _start_from_human_led(110)  # tast 49 -> human 109-110 (gap #108)

    human = 112  # tast 50..53
    for k in range(49, 53):
        starts[k] = _start_from_human_led(human)
        human += 2

    starts[52] = _start_from_human_led(118)  # tast 53 -> human 117-118
    starts[53] = _start_from_human_led(121)  # tast 54 -> human 120-121 (gap #119)

    human = 123  # tast 55..59
    for k in range(54, 59):
        starts[k] = _start_from_human_led(human)
        human += 2

    starts[58] = _start_from_human_led(131)  # tast 59 -> human 130-131
    starts[59] = _start_from_human_led(134)  # tast 60 -> human 133-134 (gap #132)

    human = 136  # tast 61..63
    for k in range(60, 63):
        starts[k] = _start_from_human_led(human)
        human += 2

    starts[62] = _start_from_human_led(140)  # tast 63 -> human 139-140
    starts[63] = _start_from_human_led(143)  # tast 64 -> human 142-143 (gap #141)

    human = 145  # tast 65..67
    for k in range(64, 67):
        starts[k] = _start_from_human_led(human)
        human += 2

    starts[66] = _start_from_human_led(149)  # tast 67 -> human 148-149
    starts[67] = _start_from_human_led(152)  # tast 68 -> human 151-152 (gap #150)

    human = 154  # tast 69..74
    for k in range(68, 74):
        starts[k] = _start_from_human_led(human)
        human += 2

    starts[73] = _start_from_human_led(164)  # tast 74 -> human 163-164
    starts[74] = _start_from_human_led(167)  # tast 75 -> human 166-167 (gap #165)

    human = 169  # tast 76..77
    for k in range(75, 77):
        starts[k] = _start_from_human_led(human)
        human += 2

    starts[76] = _start_from_human_led(171)  # tast 77 -> human 170-171
    starts[77] = _start_from_human_led(174)  # tast 78 -> human 173-174 (gap #172)

    human = 176  # tast 79..81
    for k in range(78, 81):
        starts[k] = _start_from_human_led(human)
        human += 2

    starts[80] = _start_from_human_led(180)  # tast 81 -> human 179-180
    starts[81] = _start_from_human_led(183)  # tast 82 -> human 182-183 (gap #181)

    human = 185  # tast 83..86
    for k in range(82, 86):
        starts[k] = _start_from_human_led(human)
        human += 2

    starts[86] = _start_from_human_led(193)  # tast 87 -> human 192-193
    starts[87] = _start_from_human_led(196)  # tast 88 (end) -> human 195-196 (gap #194)

    return tuple(starts)


def _led_count(key_index: int) -> int:
    """LEDs used by one key: 4 for the leftmost key (A0), otherwise 2."""
    return 4 if key_index == 0 else 2


#: Per-key first LED index (0-based), identical to the firmware table.
KEY_LED_STARTS: tuple[int, ...] = _compute_key_starts()

#: Per-key LED count (4 for A0, 2 for all other keys).
KEY_LED_COUNTS: tuple[int, ...] = tuple(_led_count(k) for k in KEY_RANGE)


@dataclass(frozen=True, slots=True)
class PianoKeyLeds:
    """The physical LED range of one piano key (0-based, half-open)."""

    key_index: int
    start: int
    count: int

    @property
    def indexes(self) -> tuple[int, ...]:
        """The concrete firmware LED indexes lit for this key."""
        return tuple(range(self.start, self.start + self.count))


def midi_note_to_key(note: int) -> int | None:
    """MIDI note -> 0-based key index; ``None`` outside 21..108."""
    if not isinstance(note, int) or isinstance(note, bool):
        return None
    if not MIDI_NOTE_MIN <= note <= MIDI_NOTE_MAX:
        return None
    return note - MIDI_NOTE_MIN


def key_led_range(key_index: int) -> PianoKeyLeds | None:
    """Key index -> calibrated LED range; ``None`` outside 0..87."""
    if not isinstance(key_index, int) or isinstance(key_index, bool):
        return None
    if key_index not in KEY_RANGE:
        return None
    return PianoKeyLeds(
        key_index=key_index,
        start=KEY_LED_STARTS[key_index],
        count=KEY_LED_COUNTS[key_index],
    )


def midi_to_led_indexes(note: int) -> tuple[int, ...] | None:
    """MIDI note -> concrete LED indexes; ``None`` outside the piano."""
    key = midi_note_to_key(note)
    if key is None:
        return None
    return key_led_range(key).indexes
