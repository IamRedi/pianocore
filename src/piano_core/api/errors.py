"""Local control API error model (PIANO-P012).

Deterministic HTTP error surface: stable status codes, stable
machine-readable ``code`` values, stable payload shape, and no leakage
(Python tracebacks and filesystem internals never reach the client).
Product-layer exceptions are translated here so handlers stay declarative.
"""

from __future__ import annotations

from piano_core.learn import LearnCatalogError
from piano_core.packages.importer import (
    PackageAlreadyPresentError,
    PackageImportError,
)
from piano_core.packages.practice import PackagePracticeError
from piano_core.library.model import (
    LibraryConfigError,
    LibraryEntryGoneError,
    LibraryError,
    LibraryIndexError,
    LibraryPathError,
)
from piano_core.playback.engine import (
    PlaybackAlreadyActiveError,
    PlaybackError,
)
from piano_core.playback.manager import (
    PlaybackLockBusyError,
    PlaybackNotAvailableError,
)
from piano_core.playback.timeline import MidiFileError
from piano_core.practice.model import (
    PracticeActivityConflictError,
    PracticeAlreadyActiveError,
    PracticeError,
    PracticeInvalidStateError,
    PracticeNotAvailableError,
    PracticePlanError,
    PracticeStaleCommandError,
)

__all__ = ["ApiError", "translate_exception"]


class ApiError(Exception):
    """One deterministic API error (status code + machine-readable code)."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    def payload(self) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "status": self.status_code,
            }
        }


def translate_exception(exc: Exception) -> ApiError:
    """Map a product-layer exception to its deterministic API error."""
    if isinstance(exc, LibraryEntryGoneError):
        # Indexed but the source file is gone (rescan reconciles).
        return ApiError(
            410,
            "song_source_missing",
            "song is indexed but its source file is missing under the "
            "library root",
        )
    if isinstance(exc, LibraryPathError):
        return ApiError(404, "song_not_found", str(exc))
    if isinstance(exc, (LibraryConfigError, LibraryIndexError)):
        return ApiError(503, "library_unavailable", "library is unavailable")
    if isinstance(exc, LibraryError):
        return ApiError(503, "library_unavailable", "library is unavailable")
    if isinstance(exc, (PlaybackAlreadyActiveError, PlaybackLockBusyError)):
        return ApiError(409, "playback_already_active", str(exc))
    if isinstance(exc, PlaybackNotAvailableError):
        return ApiError(503, "playback_unavailable", str(exc))
    if isinstance(exc, PlaybackError):
        # Invalid state transitions (pause/resume outside their required
        # state) keep the engine's typed message.
        return ApiError(409, "invalid_state_transition", str(exc))
    if isinstance(exc, MidiFileError):
        return ApiError(
            422,
            "song_unplayable",
            "song source could not be interpreted as a playable "
            "Standard MIDI File",
        )
    if isinstance(exc, PackageAlreadyPresentError):
        # Idempotent import: explicit, client-usable answer.
        return ApiError(200, "package_already_present", str(exc))
    if isinstance(exc, LearnCatalogError):
        return ApiError(503, "learn_unavailable", str(exc))
    if isinstance(exc, PackageImportError):
        return ApiError(422, "package_rejected", str(exc))
    if isinstance(exc, PackagePracticeError):
        return ApiError(400, "invalid_package_practice", str(exc))
    if isinstance(exc, PracticeNotAvailableError):
        return ApiError(503, "practice_unavailable", str(exc))
    if isinstance(exc, PracticeAlreadyActiveError):
        return ApiError(409, "practice_already_active", str(exc))
    if isinstance(exc, PracticeActivityConflictError):
        return ApiError(409, "activity_conflict", str(exc))
    if isinstance(exc, PracticePlanError):
        return ApiError(422, "song_unplayable", str(exc))
    if isinstance(exc, PracticeStaleCommandError):
        return ApiError(409, "stale_learning_command", str(exc))
    if isinstance(exc, (PracticeInvalidStateError, PracticeError)):
        return ApiError(409, "invalid_state_transition", str(exc))
    return ApiError(500, "internal_error", "internal error")
