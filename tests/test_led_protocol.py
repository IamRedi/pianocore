"""PIANO-P014: ESP32 LED wire protocol tests.

The frames must be EXACTLY what the deployed firmware parses; the
inbound parser must accept get_state and nothing else.
"""

import unittest

from piano_core.led.model import LedError
from piano_core.led.protocol import (
    CLIENT_MAX_MESSAGE_BYTES,
    ClientMessage,
    clear_frame,
    note_off_frame,
    note_on_frame,
    parse_client_message,
)


class NoteFrameTests(unittest.TestCase):
    def test_note_on_frame_exact_shape(self):
        self.assertEqual(
            note_on_frame(60, 80),
            '{"type":"midi","event":"note_on","note":60,"velocity":80}',
        )

    def test_note_off_frame_exact_shape(self):
        self.assertEqual(
            note_off_frame(60),
            '{"type":"midi","event":"note_off","note":60}',
        )

    def test_clear_frame_exact_shape(self):
        self.assertEqual(
            clear_frame(),
            '{"type":"playback","status":"all_notes_off"}',
        )

    def test_bounds(self):
        self.assertEqual(note_on_frame(0, 1).count("note"), 2)
        self.assertEqual(note_on_frame(108, 127).count("note"), 2)
        with self.assertRaises(LedError):
            note_on_frame(-1, 64)
        with self.assertRaises(LedError):
            note_on_frame(128, 64)
        with self.assertRaises(LedError):
            note_on_frame(60, 0)  # velocity 0 is not a note-on here
        with self.assertRaises(LedError):
            note_on_frame(60, 128)
        with self.assertRaises(LedError):
            note_on_frame("60", 64)
        with self.assertRaises(LedError):
            note_on_frame(True, 64)
        with self.assertRaises(LedError):
            note_off_frame(128)


class ClientMessageParsingTests(unittest.TestCase):
    def test_get_state_accepted(self):
        self.assertEqual(
            parse_client_message('{"type":"get_state"}'),
            ClientMessage("get_state"),
        )
        self.assertEqual(
            parse_client_message(b'{"type":"get_state"}'),
            ClientMessage("get_state"),
        )

    def test_everything_else_dropped(self):
        self.assertIsNone(parse_client_message(""))
        self.assertIsNone(parse_client_message("not json"))
        self.assertIsNone(parse_client_message(b"\xff\xfe"))
        self.assertIsNone(parse_client_message("[]"))
        self.assertIsNone(parse_client_message("42"))
        self.assertIsNone(
            parse_client_message('{"type":"midi","event":"note_on","note":1}')
        )
        self.assertIsNone(
            parse_client_message('{"type":"playback","status":"all_notes_off"}')
        )
        # Injective payloads (commands, unknown types) never parse.
        self.assertIsNone(parse_client_message('{"type":"command"}'))
        # Oversized input is the TRANSPORT's boundary (frames beyond
        # CLIENT_MAX_MESSAGE_BYTES are dropped before parsing).
        self.assertGreater(CLIENT_MAX_MESSAGE_BYTES, 0)


if __name__ == "__main__":
    unittest.main()
