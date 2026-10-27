"""V2 tests: canonical runtime dependency pins (PIANO-P005 F1, review F5.3).

The appliance runtime must declare exactly the pinned canonical MIDI
dependencies — no more, no fewer. tomllib is stdlib on the supported
Python floor (>=3.11).
"""

import tomllib
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


class DependencyPinTests(unittest.TestCase):
    def test_runtime_dependencies_exactly_pinned(self):
        with open(_REPO_ROOT / "pyproject.toml", "rb") as handle:
            data = tomllib.load(handle)
        self.assertEqual(
            sorted(data["project"]["dependencies"]),
            ["mido==1.3.3", "python-rtmidi==1.5.8"],
        )


if __name__ == "__main__":
    unittest.main()
