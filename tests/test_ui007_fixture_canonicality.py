"""PIANO-UI-007 canonical fixture canonicality guard.

The committed fixtures under ``tests/weblogic/fixtures/`` must remain
byte-identical to what the accepted canonical pipeline produces in this
repository (P009 timeline -> UI-005 score projection -> UI-006 fingering
engine -> API serializers). Any canonical change that would alter the
fixtures must regenerate them consciously — never silently.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests.weblogic.fixtures.generate_fixtures import (
    fixture_bass_melody,
    fixture_chords_held_moving,
    fixture_thumb_under_scale,
)

FIXTURES = {
    "thumb_under_scale.json": fixture_thumb_under_scale,
    "chords_held_moving.json": fixture_chords_held_moving,
    "bass_melody.json": fixture_bass_melody,
}


class FixtureCanonicalityTests(unittest.TestCase):
    def test_committed_fixtures_match_canonical_pipeline(self):
        for name, builder in FIXTURES.items():
            with self.subTest(fixture=name):
                path = Path(__file__).resolve().parent / "weblogic" / "fixtures" / name
                committed = json.loads(path.read_text(encoding="utf-8"))
                regenerated = builder()
                self.assertEqual(committed, regenerated)


if __name__ == "__main__":
    unittest.main()
