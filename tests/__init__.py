"""Test package bootstrap.

PIANO-P001 runs on the stdlib only, without installing the project, so the
test package prepends ``src/`` to ``sys.path`` once at import time. With a
real installation this is harmless (the installed package wins nothing here;
explicit local sources keep tests aligned with the working tree).
"""

import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
