"""Entry point for ``python -m piano_core``.

The console-script entrypoint (``piano-core``, defined in pyproject.toml)
targets :func:`piano_core.app.main` directly; this module exists for the
``-m`` invocation form.
"""

from __future__ import annotations

import sys

from piano_core.app import main

if __name__ == "__main__":
    sys.exit(main())
