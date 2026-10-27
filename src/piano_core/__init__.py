"""Piano Core application package.

Piano Core is one logical piano device: the Raspberry Pi hosts the
authoritative ``piano-core`` runtime (frozen spec §1). This package currently
contains only the application skeleton established by PIANO-P001 — identity,
configuration, health, lifecycle, logging, and the entrypoint. Functional
subsystems (state, MIDI, playback, library, LED, API) are added by later
bounded tasks as ``piano_core.*`` subpackages; none of their concerns belong
in the entrypoint or composition root.
"""

from __future__ import annotations

__all__ = ["PRODUCT_NAME", "RUNTIME_IDENTIFIER", "__version__"]

#: Human-readable product identity.
PRODUCT_NAME = "Piano Core"

#: Stable runtime/machine identifier.
RUNTIME_IDENTIFIER = "piano-core"

# Single canonical version source for the whole repository; pyproject.toml
# reads this attribute dynamically (see [tool.setuptools.dynamic]).
__version__ = "0.1.0-dev"
