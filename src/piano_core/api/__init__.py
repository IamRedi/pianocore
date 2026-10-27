"""Local control API for Piano Core (PIANO-P012).

A production-quality LOCAL control surface over the canonical subsystems:
Application health, the physical StateStore, the LibraryService, and the
Playback Core. The API owns NO business logic and never sends MIDI
directly — playback flows exclusively through the canonical playback
engine and the canonical owned output adapter (via the playback manager).

Packages/modules:

- :mod:`piano_core.api.errors` — deterministic error model;
- :mod:`piano_core.api.serialization` — canonical objects -> JSON;
- :mod:`piano_core.api.handlers` — route handlers + route table;
- :mod:`piano_core.api.server` — threaded HTTP server facade.

Contract and security model: ``docs/operations/API.md``.
"""

from __future__ import annotations

from piano_core.api.server import ApiServer

__all__ = ["ApiServer"]
