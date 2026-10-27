"""Practice Package subsystem (M5, ADR-PACKAGE-PRACTICE-V1).

Imports the versioned ``practice-package/v1`` exchange format produced
by the MIDI Curator's Piano Core export adapter and runs guided
practice from it fully offline. Piano Core NEVER talks back to the
Studio once a package is imported: the package is standalone (manifest,
MIDI, practice events, sections, provenance) and hash-bound.

Layers:

- :mod:`piano_core.packages.contract` — strict, fail-closed package
  validation (schema, identity, checksums, bounds); no I/O.
- :mod:`piano_core.packages.store` — additive SQLite persistence for
  imported packages, practice sessions and progress (separate database;
  the canonical library index is untouched).
- :mod:`piano_core.packages.importer` — the only package-entry path:
  staging -> validate -> verify -> commit -> atomic promote; idempotent
  for identical packages, separate versions coexist.
- :mod:`piano_core.packages.practice` — package-derived practice plans
  (hand filtering, sections, loops) plus the deterministic guidance
  timeline (seek / loop / tempo-scaled current+upcoming windows) behind
  Listen/Follow modes.
- :mod:`piano_core.packages.led_frames` — the versioned LED semantic
  contract (v1): musical meaning only; colors/animations belong to the
  renderer/hardware adapter.
- :mod:`piano_core.packages.sessions` — objective session scoring and
  deterministic progress summaries (no opaque scores, no learning
  science overclaims).
"""

from __future__ import annotations
