"""Route handlers for the local control API (PIANO-P012).

Each handler is a pure adapter: it validates request input, calls ONE
canonical subsystem (StateStore / LibraryService / PlaybackManager /
PracticeManager / Health), and returns a deterministic ``(status,
payload)``. No business logic lives here and no handler ever touches
MIDI, the filesystem, or any application-lifecycle control (stopping
the application is unreachable from the API).

Handlers may raise :class:`~piano_core.api.errors.ApiError` directly or
let canonical product exceptions bubble — the HTTP layer translates every
non-``ApiError`` exception through
:func:`~piano_core.api.errors.translate_exception`.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Callable

from piano_core.api.errors import ApiError
from piano_core.api.serialization import (
    device_identity_payload,
    fingering_payload,
    led_state_payload,
    library_stats_payload,
    live_note_payload,
    playback_session_payload,
    practice_session_payload,
    score_payload,
    song_record_payload,
    state_payload,
)
from piano_core.api.streaming import ApiStream
from piano_core.live_notes import LiveNotePublicationClosedError, LiveNoteSubscriberLimitError
from piano_core.led.model import BRIGHTNESS_MAX, BRIGHTNESS_MIN, LedMode
from piano_core.health import HealthState
from piano_core.library.model import SongStatus
from piano_core.playback.score import ScoreTooLargeError
from piano_core.fingering.engine import FingeringUnsupportedError
from piano_core.learn import LearnCatalogError
from piano_core.playback.timeline import validate_tempo_scale
import logging

from piano_core.packages.importer import PackageAlreadyPresentError, PackageImportError
from piano_core.packages.service import PackageService
from piano_core.packages.practice import (
    GUIDED_MODES,
    GuidedRun,
    PackagePracticeError,
    build_package_plan,
    hands_mode_available,
    plan_section,
)
from piano_core.packages.sessions import record_guidance_session, record_wait_session
from piano_core.practice.model import LearningMode, PracticeSection

__all__ = ["ROUTES", "Route"]


@dataclass(frozen=True)
class Route:
    """One API route: exact method + path pattern (+ allowed query keys)."""

    method: str
    pattern: str
    handler: Callable[..., tuple[int, dict]]
    query_keys: frozenset = field(default=frozenset())


def _require_library(context):
    if not getattr(context, "library_ready", False) or context.library is None:
        raise ApiError(503, "library_unavailable", "library is unavailable")
    return context.library


def _require_playback(context):
    if context.playback is None:
        raise ApiError(
            503,
            "playback_unavailable",
            "automated playback is unavailable (canonical MIDI output "
            "is not open)",
        )
    return context.playback


def _require_led(context):
    """The LED subsystem must exist (be configured on this appliance).

    A USER-DISABLED subsystem still exists and is re-enable-able here;
    this guard only rejects appliances without the subsystem at all.
    """
    led = getattr(context, "led", None)
    if led is None:
        raise ApiError(
            503,
            "led_unavailable",
            "LED subsystem is not configured on this appliance",
        )
    return led


def _parse_json_object(request, required: bool, allowed: frozenset) -> dict:
    """Parse a POST body as a strict JSON object (deterministic errors)."""
    if not request.body:
        if required:
            raise ApiError(
                400, "malformed_request", "a JSON object request body is required"
            )
        return {}
    try:
        parsed = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError(400, "malformed_request", f"invalid JSON body: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ApiError(400, "malformed_request", "request body must be a JSON object")
    unknown = sorted(str(key) for key in parsed if key not in allowed)
    if unknown:
        raise ApiError(
            400,
            "malformed_request",
            f"unknown request field(s): {', '.join(unknown)}",
        )
    return parsed


def _parse_int_param(
    query: dict, key: str, default: int, minimum: int, maximum: int
) -> int:
    raw = query.get(key)
    if raw is None:
        return default
    if not raw.strip().lstrip("+").isdigit():
        raise ApiError(
            400, "invalid_parameter", f"{key} must be an integer ({raw!r})"
        )
    value = int(raw)
    if not minimum <= value <= maximum:
        raise ApiError(
            400,
            "invalid_parameter",
            f"{key} out of range [{minimum}..{maximum}]: {value}",
        )
    return value


# ----------------------------------------------------------------------
# GET /api/v1/device
# ----------------------------------------------------------------------
def device(context, request) -> tuple[int, dict]:
    """Public device identity descriptor (PIANO-PROD-001).

    Purely cached: reads the identity loaded once during application
    startup — NEVER the filesystem, and no query parameters are
    accepted. When no valid identity is available (unconfigured,
    missing, corrupt, or unreadable storage) the deterministic 503
    envelope is returned; the internal reason stays internal.
    """
    identity = getattr(context, "device_identity", None)
    if identity is None:
        raise ApiError(
            503,
            "device_identity_unavailable",
            "device identity is unavailable",
        )
    return 200, device_identity_payload(identity)


# ----------------------------------------------------------------------
# GET /api/v1/health
# ----------------------------------------------------------------------
def health(context, request) -> tuple[int, dict]:
    """Readiness plus one-line subsystem states (never any secret)."""
    app_state = context.health.state
    if app_state is not HealthState.READY:
        raise ApiError(503, "not_ready", f"application is {app_state.value}")
    library_ready = bool(getattr(context, "library_ready", False))
    entries = None
    if library_ready and context.library is not None:
        try:
            entries = context.library.stats()["total"]
        except Exception:
            entries = None  # degraded at runtime; health stays honest
    playback = context.playback
    playback_payload = playback_session_payload(
        playback.snapshot() if playback is not None else None
    )
    status = (
        "ok"
        if library_ready and playback is not None and playback.available
        else "degraded"
    )
    return 200, {
        "status": status,
        "application": {
            "state": app_state.value,
            "since": context.health.updated_at.isoformat(),
        },
        "midi_input": {
            "connection": context.state_store.snapshot().midi_connection.value
        },
        "library": {"ready": library_ready, "entries": entries},
        "playback": playback_payload,
    }


# ----------------------------------------------------------------------
# GET /api/v1/state
# ----------------------------------------------------------------------
def state(context, request) -> tuple[int, dict]:
    """Canonical PHYSICAL piano state (StateStore snapshot; read-only)."""
    return 200, state_payload(context.state_store.snapshot())


# ----------------------------------------------------------------------
# GET /api/v1/live-notes + /api/v1/live-notes/stream
# ----------------------------------------------------------------------
def _require_live_notes(context):
    channel = getattr(context, "live_notes", None)
    if channel is None:
        raise ApiError(
            503,
            "live_notes_unavailable",
            "live note state is unavailable on this appliance",
        )
    return channel


def live_notes(context, request) -> tuple[int, dict]:
    """Full replacement snapshot of copied authoritative observations."""
    return 200, live_note_payload(_require_live_notes(context).snapshot())


def live_note_stream(context, request):
    """Semantic SSE stream of full replacement snapshots plus heartbeats."""
    channel = _require_live_notes(context)
    try:
        subscription = channel.subscribe()
    except LiveNotePublicationClosedError as exc:
        raise ApiError(503, "live_notes_unavailable", "live note publication is stopping") from exc
    except LiveNoteSubscriberLimitError as exc:
        raise ApiError(503, "live_note_capacity", str(exc)) from exc

    def frames():
        for snapshot in subscription:
            if snapshot is None:
                yield b": heartbeat\n\n"
                continue
            payload = json.dumps(
                live_note_payload(snapshot),
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            )
            yield (
                f"id: {snapshot.sequence}\n"
                f"event: snapshot\n"
                f"data: {payload}\n\n"
            ).encode("utf-8")

    return 200, ApiStream(
        frames(),
        "text/event-stream; charset=utf-8",
        on_close=subscription.close,
    )


# ----------------------------------------------------------------------
# GET /api/v1/library
# ----------------------------------------------------------------------
def library_stats(context, request) -> tuple[int, dict]:
    library = _require_library(context)
    return 200, library_stats_payload(library.stats())


# ----------------------------------------------------------------------
# GET /api/v1/library/songs
# ----------------------------------------------------------------------
def library_songs(context, request) -> tuple[int, dict]:
    library = _require_library(context)
    query = request.query
    limit = _parse_int_param(query, "limit", default=20, minimum=1, maximum=100)
    offset = _parse_int_param(
        query, "offset", default=0, minimum=0, maximum=1_000_000
    )
    q = query.get("q") or None
    status = None
    if query.get("status"):
        try:
            status = SongStatus.from_value(query["status"])
        except ValueError:
            raise ApiError(
                400,
                "invalid_parameter",
                "status must be one of pending, parsed, invalid, unsupported",
            ) from None
    midi_format = None
    if query.get("format") is not None:
        if query["format"] not in ("0", "1"):
            raise ApiError(
                400, "invalid_parameter", "format must be 0 or 1"
            )
        midi_format = int(query["format"])
    if q is not None and offset:
        raise ApiError(
            400,
            "invalid_parameter",
            "offset pagination applies to unfiltered listing only "
            "(search results are relpath-ordered and limited)",
        )
    if q is not None:
        records = library.search(q, limit=limit)
    else:
        records = library.list_songs(
            limit=limit, offset=offset, status=status, midi_format=midi_format
        )
    return 200, {
        "query": {
            "q": q,
            "limit": limit,
            "offset": offset,
            "status": status.value if status is not None else None,
            "format": midi_format,
        },
        "count": len(records),
        "songs": [song_record_payload(record) for record in records],
    }


# ----------------------------------------------------------------------
# GET /api/v1/library/songs/<song_id>
# ----------------------------------------------------------------------
def library_song(context, request, song_id: str = "") -> tuple[int, dict]:
    library = _require_library(context)
    record = library.get(song_id)
    if record is None:
        raise ApiError(
            404, "song_not_found", f"no catalog entry for song_id {song_id!r}"
        )
    return 200, song_record_payload(record)


# ----------------------------------------------------------------------
# GET /api/v1/playback
# ----------------------------------------------------------------------
def playback_state(context, request) -> tuple[int, dict]:
    manager = context.playback
    return 200, playback_session_payload(
        manager.snapshot() if manager is not None else None
    )


# ----------------------------------------------------------------------
# POST /api/v1/playback/play
# ----------------------------------------------------------------------
def playback_play(context, request) -> tuple[int, dict]:
    # Validate the request shape BEFORE subsystem availability so a
    # malformed request always wins deterministically (400 over 503).
    body = _parse_json_object(
        request, required=True, allowed=frozenset({"song_id", "tempo_multiplier"})
    )
    manager = _require_playback(context)
    song_id = body.get("song_id")
    if not isinstance(song_id, str) or not song_id.strip():
        raise ApiError(400, "invalid_song_id", "song_id must be a non-empty string")
    tempo = body.get("tempo_multiplier", 1.0)
    if isinstance(tempo, bool) or not isinstance(tempo, (int, float)):
        raise ApiError(
            400, "invalid_tempo", "tempo_multiplier must be a number"
        )
    try:
        scale = validate_tempo_scale(tempo)
    except ValueError as exc:
        raise ApiError(400, "invalid_tempo", str(exc)) from exc
    session = manager.play(song_id.strip(), tempo_multiplier=scale)
    return 200, playback_session_payload(session)


def _empty_body_or_object(request) -> None:
    """pause/resume/stop accept an empty body or exactly ``{}``."""
    _parse_json_object(request, required=False, allowed=frozenset())


# ----------------------------------------------------------------------
# POST /api/v1/playback/pause
# ----------------------------------------------------------------------
def playback_pause(context, request) -> tuple[int, dict]:
    _empty_body_or_object(request)
    manager = _require_playback(context)
    return 200, playback_session_payload(manager.pause())


# ----------------------------------------------------------------------
# POST /api/v1/playback/resume
# ----------------------------------------------------------------------
def playback_resume(context, request) -> tuple[int, dict]:
    _empty_body_or_object(request)
    manager = _require_playback(context)
    return 200, playback_session_payload(manager.resume())


# ----------------------------------------------------------------------
# POST /api/v1/playback/stop
# ----------------------------------------------------------------------
def playback_stop(context, request) -> tuple[int, dict]:
    _empty_body_or_object(request)
    manager = _require_playback(context)
    session, stopped = manager.stop()
    payload = playback_session_payload(session)
    payload["stopped"] = stopped
    return 200, payload


# ----------------------------------------------------------------------
# GET /api/v1/led
# ----------------------------------------------------------------------
def led_state(context, request) -> tuple[int, dict]:
    """LED subsystem state (200 even when disabled — honest snapshot)."""
    led = getattr(context, "led", None)
    if led is None:
        return 200, led_state_payload(None)
    return 200, led_state_payload(led.adapter.snapshot())


# ----------------------------------------------------------------------
# PUT /api/v1/led
# ----------------------------------------------------------------------
def led_update(context, request) -> tuple[int, dict]:
    """Bounded LED updates (enabled / mode / brightness) — nothing else."""
    body = _parse_json_object(
        request,
        required=False,
        allowed=frozenset({"enabled", "mode", "brightness"}),
    )
    led = _require_led(context)
    # Validate EVERYTHING first so a bad request never half-applies.
    enabled = body.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        raise ApiError(400, "invalid_parameter", "enabled must be a boolean")
    mode_raw = body.get("mode")
    mode = None
    if mode_raw is not None:
        try:
            mode = LedMode(mode_raw)
        except ValueError:
            allowed = ", ".join(m.value for m in LedMode)
            raise ApiError(
                400,
                "invalid_parameter",
                f"mode must be one of: {allowed}",
            ) from None
    brightness = body.get("brightness")
    if brightness is not None:
        if isinstance(brightness, bool) or not isinstance(brightness, int):
            raise ApiError(
                400, "invalid_parameter", "brightness must be an integer"
            )
        if not BRIGHTNESS_MIN <= brightness <= BRIGHTNESS_MAX:
            raise ApiError(
                400,
                "invalid_parameter",
                f"brightness out of range [{BRIGHTNESS_MIN}..{BRIGHTNESS_MAX}]: "
                f"{brightness}",
            )
    # Apply in a deterministic order.
    if enabled is not None:
        led.adapter.set_enabled(enabled)
    if mode is not None:
        led.adapter.set_mode(mode)
    if brightness is not None:
        led.adapter.set_brightness(brightness)
    return 200, led_state_payload(led.adapter.snapshot())


# ----------------------------------------------------------------------
# POST /api/v1/led/clear
# ----------------------------------------------------------------------
def led_clear(context, request) -> tuple[int, dict]:
    """Bounded diagnostic: deterministic all-keys clear."""
    _parse_json_object(request, required=False, allowed=frozenset())
    led = _require_led(context)
    led.adapter.clear()
    return 200, led_state_payload(led.adapter.snapshot())


# ----------------------------------------------------------------------
# GET /api/v1/practice
# ----------------------------------------------------------------------
def practice_state(context, request) -> tuple[int, dict]:
    """Guided practice snapshot (200 even when unavailable — honest)."""
    practice = getattr(context, "practice", None)
    return 200, practice_session_payload(
        practice.snapshot() if practice is not None else None
    )


# ----------------------------------------------------------------------
# POST /api/v1/practice/start
# ----------------------------------------------------------------------
def practice_start(context, request) -> tuple[int, dict]:
    """Start a guided practice session by song_id (never a raw path)."""
    body = _parse_json_object(request, required=True, allowed=frozenset({"song_id"}))
    practice = getattr(context, "practice", None)
    if practice is None:
        raise ApiError(
            503,
            "practice_unavailable",
            "guided practice is unavailable on this appliance",
        )
    song_id = body.get("song_id")
    if not isinstance(song_id, str) or not song_id.strip():
        raise ApiError(400, "invalid_song_id", "song_id must be a non-empty string")
    session = practice.start(song_id.strip())
    return 200, practice_session_payload(session)


# ----------------------------------------------------------------------
# POST /api/v1/practice/stop
# ----------------------------------------------------------------------
def practice_stop(context, request) -> tuple[int, dict]:
    """Stop/reset the guided practice session (idempotent, NOT a PANIC)."""
    _empty_body_or_object(request)
    practice = getattr(context, "practice", None)
    if practice is None:
        raise ApiError(
            503,
            "practice_unavailable",
            "guided practice is unavailable on this appliance",
        )
    session, stopped = practice.stop()
    payload = practice_session_payload(session)
    payload["stopped"] = stopped
    return 200, payload


# ----------------------------------------------------------------------
# GET /api/v1/score/<song_id>
# ----------------------------------------------------------------------
def score(context, request, song_id: str = "") -> tuple[int, dict]:
    """Canonical read-only note-span projection for visual learning.

    The projection is derived at the Core boundary from the canonical
    P009 timeline (never reparsed in the browser). Bounded: oversized
    material answers a typed 413 instead of an unbounded payload.
    """
    score_service = getattr(context, "score", None)
    if score_service is None:
        raise ApiError(
            503,
            "score_unavailable",
            "score projection is unavailable (canonical library is not ready)",
        )
    if not song_id or not song_id.strip():
        raise ApiError(400, "invalid_song_id", "song_id must be a non-empty string")
    try:
        projection = score_service.get(song_id.strip())
    except ScoreTooLargeError as exc:
        raise ApiError(413, "score_too_large", str(exc)) from exc
    return 200, score_payload(projection)


# ----------------------------------------------------------------------
# GET /api/v1/fingering/<song_id>
# ----------------------------------------------------------------------
def fingering(context, request, song_id: str = "") -> tuple[int, dict]:
    """Complete deterministic hand/finger plan for one exact score version."""
    service = getattr(context, "fingering", None)
    if service is None:
        raise ApiError(
            503,
            "fingering_unavailable",
            "fingering intelligence is unavailable (canonical score is not ready)",
        )
    if not song_id or not song_id.strip():
        raise ApiError(400, "invalid_song_id", "song_id must be a non-empty string")
    try:
        plan = service.get(song_id.strip())
    except ScoreTooLargeError as exc:
        raise ApiError(413, "score_too_large", str(exc)) from exc
    except FingeringUnsupportedError as exc:
        raise ApiError(422, "fingering_unsupported", str(exc)) from exc
    return 200, fingering_payload(plan)


def _learning_payload(context, practice) -> dict:
    payload = practice_session_payload(
        practice.snapshot() if practice is not None else None
    )
    payload["progress_pending_count"] = getattr(practice, "pending_results", 0) if practice else 0
    payload["progress_persistence_error"] = getattr(practice, "persistence_error", None) if practice else None
    if practice is not None and payload.get("song_id", "") and payload["song_id"].startswith("package:") and getattr(context, "learn_service", None) is not None:
        lesson = context.learn_service.lesson_by_package(payload["song_id"][8:])
        payload["lesson_id"] = lesson["lesson_id"] if lesson else None
    payload["capabilities"] = {
        "supported_modes": [mode.value for mode in LearningMode],
        "missed_outcome": payload.get("mode") == "FLOW",
        # Library songs carry no hand data, so plain-learning hand
        # selection stays False; imported packages with hand evidence
        # report their own flags below (M5).
        "hand_selection": False,
        "package_practice": True,
        "package_hand_selection": True,
        "package_session_recording": True,
        "rhythm_grading": payload.get("mode") == "FLOW",
        "flow_timing": {"window_ms": 180, "on_time_ms": 60, "count_in_seconds": 2, "pause_resume": True, "speed_range": [0.25, 1.0]},
        "persistence": "runtime_only",
        "section_basis": "song_time_seconds",
        "learn_library": bool(getattr(context, "learn_ready", False)),
    }
    return payload


def _require_practice(context):
    practice = getattr(context, "practice", None)
    if practice is None:
        raise ApiError(
            503,
            "practice_unavailable",
            "learning is unavailable on this appliance",
        )
    return practice


def _require_packages(context):
    service = getattr(context, "packages_service", None)
    if service is None:
        raise ApiError(
            503,
            "packages_unavailable",
            "practice packages are not enabled on this appliance",
        )
    return service


def _require_learn(context):
    service = getattr(context, "learn_service", None)
    if service is None:
        raise ApiError(
            503,
            "learn_unavailable",
            "the learn library is not enabled on this appliance",
        )
    return service


def learn_overview(context, request) -> tuple[int, dict]:
    """Learn Library overview (PIANO-LEARN-001): courses, levels, counts."""
    service = _require_learn(context)
    return 200, service.overview()


def learn_lessons(context, request) -> tuple[int, dict]:
    """Learn lesson listing with course/level filters and search."""
    service = _require_learn(context)
    course_id = request.query.get("course") or None
    text = request.query.get("q") or None
    level = _parse_int_param(request.query, "level", default=-1, minimum=0, maximum=4)
    limit = _parse_int_param(request.query, "limit", default=50, minimum=1, maximum=100)
    offset = _parse_int_param(request.query, "offset", default=0, minimum=0, maximum=10**9)
    payload = service.lessons(
        course_id=course_id,
        level=level if level >= 0 else None,
        text=text,
        limit=limit,
        offset=offset,
    )
    return 200, payload


def learn_lesson_detail(context, request, lesson_id: str) -> tuple[int, dict]:
    """One lesson: detail, license, curriculum neighbors, merged progress."""
    service = _require_learn(context)
    try:
        return 200, service.lesson_detail(lesson_id)
    except LearnCatalogError as exc:
        raise ApiError(404, "lesson_not_found", str(exc))


def _learning_guard(body: dict, *, required: bool) -> tuple[str | None, int | None]:
    session_id = body.get("expected_session_id")
    version = body.get("expected_version")
    if required and (session_id is None or version is None):
        raise ApiError(
            400,
            "stale_learning_command",
            "expected_session_id and expected_version are required",
        )
    if session_id is not None and (
        not isinstance(session_id, str) or not session_id.strip()
    ):
        raise ApiError(400, "invalid_parameter", "expected_session_id is invalid")
    if version is not None and (
        not isinstance(version, int) or isinstance(version, bool) or version < 0
    ):
        raise ApiError(400, "invalid_parameter", "expected_version is invalid")
    return session_id.strip() if isinstance(session_id, str) else None, version


def learning_state(context, request) -> tuple[int, dict]:
    """Learning V2 snapshot and explicit current capability contract."""
    return 200, _learning_payload(context, getattr(context, "practice", None))


def learning_start(context, request) -> tuple[int, dict]:
    """Start one validated Core-owned Learning V2 strategy."""
    body = _parse_json_object(
        request,
        required=True,
        allowed=frozenset({
            "song_id", "mode", "section", "required_repetitions",
            "package_id", "hand", "section_id", "speed",
        }),
    )
    raw_package_id = body.get("package_id")
    if raw_package_id is not None and (
        not isinstance(raw_package_id, str) or not raw_package_id.strip()
    ):
        raise ApiError(400, "invalid_parameter", "package_id must be a string")
    raw_hand = body.get("hand")
    if raw_hand is not None and raw_hand not in ("RH", "LH"):
        raise ApiError(400, "invalid_parameter", "hand must be 'RH' or 'LH'")
    raw_section_id = body.get("section_id")
    if raw_section_id is not None and (
        not isinstance(raw_section_id, str) or not raw_section_id.strip()
    ):
        raise ApiError(400, "invalid_parameter", "section_id must be a string")
    song_id = body.get("song_id")
    if raw_package_id is None and (
        not isinstance(song_id, str) or not song_id.strip()
    ):
        raise ApiError(400, "invalid_song_id", "song_id must be a non-empty string")
    if raw_package_id is not None and "song_id" in body:
        raise ApiError(
            400, "invalid_parameter", "package_id and song_id are mutually exclusive"
        )
    raw_mode = body.get("mode", LearningMode.WAIT_FOR_NOTE.value)
    if not isinstance(raw_mode, str):
        raise ApiError(400, "invalid_learning_mode", "mode must be a string")
    try:
        mode = LearningMode(raw_mode)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in LearningMode)
        raise ApiError(
            400, "invalid_learning_mode", f"mode must be one of: {allowed}"
        ) from exc
    if mode is LearningMode.FLOW and (body.get("section") is not None or raw_section_id is not None):
        raise ApiError(400, "invalid_learning_configuration", "Flow currently uses the full lesson. Use Section practice for a selected section.")
    section = None
    raw_section = body.get("section")
    if raw_section is not None:
        if not isinstance(raw_section, dict) or set(raw_section) != {
            "start_seconds",
            "end_seconds",
        }:
            raise ApiError(
                400,
                "invalid_section",
                "section must contain only start_seconds and end_seconds",
            )
        start = raw_section["start_seconds"]
        end = raw_section["end_seconds"]
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in (start, end)
        ):
            raise ApiError(400, "invalid_section", "section bounds must be finite numbers")
        try:
            section = PracticeSection(start, end)
        except ValueError as exc:
            raise ApiError(400, "invalid_section", str(exc)) from exc
    repetitions = body.get("required_repetitions", 1)
    if (
        not isinstance(repetitions, int)
        or isinstance(repetitions, bool)
        or not 1 <= repetitions <= 16
    ):
        raise ApiError(
            400,
            "invalid_parameter",
            "required_repetitions must be an integer in [1..16]",
        )
    speed = body.get("speed", 1.0)
    if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed) or not 0.25 <= speed <= 1.0 or (mode is not LearningMode.FLOW and speed != 1.0):
        raise ApiError(400, "invalid_parameter", "speed is only configurable for Flow in [0.25, 1.0]")
    practice = _require_practice(context)
    try:
        if raw_package_id is not None:
            service = _require_packages(context)
            # the API body carries the BARE package id; the "package:" prefix
            # exists only on the PracticeManager song-id surface
            plan_override, package = service.build_plan(
                raw_package_id.strip(),
                hand=raw_hand,
                section_id=raw_section_id,
            )
            session = practice.start(
                f"package:{raw_package_id.strip()}",
                mode=mode,
                section=section,
                required_repetitions=repetitions,
                plan_override=plan_override,
                hand_selection=raw_hand,
                **({"scope_section_id": raw_section_id, "full_lesson": raw_hand is None and raw_section_id is None and section is None and mode in (LearningMode.WAIT_FOR_NOTE, LearningMode.FLOW)} if getattr(practice, "records_sessions", False) else {}),
                **({"speed": speed} if mode is LearningMode.FLOW else {}),
            )
        else:
            session = practice.start(
                song_id.strip(),
                mode=mode,
                section=section,
                required_repetitions=repetitions,
                **({"speed": speed} if mode is LearningMode.FLOW else {}),
            )
    except ValueError as exc:
        raise ApiError(400, "invalid_learning_configuration", str(exc)) from exc
    return 200, _learning_payload(context, practice)


def learning_retry_progress(context, request) -> tuple[int, dict]:
    _parse_json_object(request, required=False, allowed=frozenset())
    practice = _require_practice(context)
    if not getattr(practice, "records_sessions", False):
        raise ApiError(503, "practice_unavailable", "progress persistence unavailable")
    practice.retry_failed_results()
    return 200, _learning_payload(context, practice)


def learning_stop(context, request) -> tuple[int, dict]:
    body = _parse_json_object(
        request,
        required=False,
        allowed=frozenset({"expected_session_id", "expected_version",
                           "elapsed_seconds"}),
    )
    session_id, version = _learning_guard(body, required=False)
    raw_elapsed = body.get("elapsed_seconds")
    if raw_elapsed is not None and (
        isinstance(raw_elapsed, bool)
        or not isinstance(raw_elapsed, (int, float))
        or raw_elapsed < 0
    ):
        raise ApiError(400, "invalid_parameter", "elapsed_seconds must be >= 0")
    practice = _require_practice(context)
    # the PRE-stop snapshot carries the observable session facts (a
    # post-stop snapshot is already reset to IDLE)
    before = practice.snapshot()
    session, stopped = practice.stop(
        expected_session_id=session_id, expected_version=version
    )
    packages_service = getattr(context, "packages_service", None)
    if (
        stopped
        and not getattr(practice, "records_sessions", False)
        and packages_service is not None
        and before.song_id
        and PackageService.is_package_id(before.song_id)
    ):
        try:
            record_wait_session(
                packages_service.store,
                session_id=before.session_id or "",
                package_id=PackageService.strip_prefix(before.song_id),
                mode=before.mode.value,
                hand_selection=before.hand_selection,
                section_id=None,
                tempo_scale=1.0,
                started_at=before.started_at or "",
                ended_at=before.ended_at,
                total_steps=before.total_steps or 0,
                completed_steps=before.completed_steps,
                incorrect_attempts=before.incorrect_attempts,
                completed=before.state.value == "COMPLETED",
                best_streak=0,
                elapsed_seconds=float(raw_elapsed or 0.0),
            )
        except Exception:  # noqa: BLE001 - recording must never break practice
            logging.getLogger("piano_core.api").exception(
                "package session recording failed"
            )
    payload = _learning_payload(context, practice)
    payload["stopped"] = stopped
    return 200, payload


def _learning_restart(context, request, *, section: bool) -> tuple[int, dict]:
    body = _parse_json_object(
        request,
        required=True,
        allowed=frozenset({"expected_session_id", "expected_version"}),
    )
    session_id, version = _learning_guard(body, required=True)
    practice = _require_practice(context)
    if section:
        practice.restart_section(
            expected_session_id=session_id, expected_version=version
        )
    else:
        practice.restart_target(
            expected_session_id=session_id, expected_version=version
        )
    return 200, _learning_payload(context, practice)


def learning_pause(context, request) -> tuple[int, dict]:
    body = _parse_json_object(request, required=True, allowed=frozenset({"expected_session_id", "expected_version", "paused"}))
    session_id, version = _learning_guard(body, required=True)
    if not isinstance(body.get("paused"), bool):
        raise ApiError(400, "invalid_parameter", "paused must be boolean")
    practice = _require_practice(context)
    practice.flow_pause(body["paused"], expected_session_id=session_id, expected_version=version)
    return 200, _learning_payload(context, practice)


def learning_restart_target(context, request) -> tuple[int, dict]:
    return _learning_restart(context, request, section=False)


def learning_restart_section(context, request) -> tuple[int, dict]:
    return _learning_restart(context, request, section=True)


# ----------------------------------------------------------------- M5 packages


def packages_capabilities(context, request) -> tuple[int, dict]:
    """Practice Package capability document (Studio compatibility check)."""
    service = _require_packages(context)
    return 200, {"capabilities": service.capabilities()}


def packages_list(context, request) -> tuple[int, dict]:
    service = _require_packages(context)
    rows = service.list_packages()
    return 200, {"packages": rows, "count": len(rows)}


def packages_get(context, request, package_id: str) -> tuple[int, dict]:
    service = _require_packages(context)
    row = service.get_package_row(package_id)
    try:
        package = service.load_package(package_id)
        sections = [
            {
                "section_id": sec.section_id,
                "label": sec.label,
                "start_bar": sec.start_bar,
                "end_bar": sec.end_bar,
                "start_time": sec.start_time,
                "end_time": sec.end_time,
                "note_count": sec.note_count,
                "rh_count": sec.rh_count,
                "lh_count": sec.lh_count,
                "difficulty": sec.difficulty,
                "suggested_loop": sec.suggested_loop,
                "evidence": sec.evidence,
            }
            for sec in package.sections
        ]
        provenance = package.provenance
    except (PackageImportError, RuntimeError) as exc:
        raise ApiError(410, "package_broken", str(exc)) from exc
    payload = row.to_dict()
    payload["sections"] = sections
    payload["provenance"] = provenance
    payload["hand_modes_available"] = hands_mode_available(package)
    return 200, payload


def packages_upload(context, request) -> tuple[int, dict]:
    """Single defined package upload endpoint (tar envelope, bounded).

    The body is NOT JSON: the server special-cases this route's content
    type and size cap. Everything else (validation, staging, hashing,
    activation) is the importer's fail-closed pipeline. Idempotent: the
    identical package returns ``already_present``.
    """
    service = _require_packages(context)
    if not request.body:
        raise ApiError(400, "malformed_request", "package tar body is required")
    try:
        result = service.importer.import_from_tar(request.body)
    except PackageAlreadyPresentError as exc:
        return 200, {"status": "already_present", "detail": str(exc)}
    except PackageImportError as exc:
        raise ApiError(422, "package_rejected", str(exc)) from exc
    if result["status"] == "already_present":
        return 200, {"status": "already_present", "package_id": result["package_id"]}
    ack = {
        "package_id": result["package_id"],
        "midi_sha256": result["midi_sha256"],
        "status": result["status"],
        "title": result.get("title"),
    }
    return 201, {"ack": ack}


def packages_progress(context, request, package_id: str) -> tuple[int, dict]:
    service = _require_packages(context)
    service.get_package_row(package_id)  # 404-shape when unknown
    return 200, service.store.progress_summary(package_id)


def packages_record_session(context, request, package_id: str) -> tuple[int, dict]:
    """Record one GUIDANCE (Listen/Follow) run explicitly.

    Wait-mode sessions are recorded automatically by learning/stop; this
    endpoint exists for passive runs (no input grading is invented).
    """
    service = _require_packages(context)
    service.get_package_row(package_id)
    body = _parse_json_object(
        request,
        required=True,
        allowed=frozenset({
            "session_id", "mode", "section_id", "tempo_scale",
            "started_at", "note_groups", "completed", "elapsed_seconds",
            "loop_count", "hand",
        }),
    )
    mode = body.get("mode")
    if mode not in ("LISTEN", "FOLLOW"):
        raise ApiError(400, "invalid_parameter", "mode must be LISTEN or FOLLOW")
    session_id = body.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ApiError(400, "invalid_parameter", "session_id is required")
    completed = body.get("completed")
    if not isinstance(completed, bool):
        raise ApiError(400, "invalid_parameter", "completed must be a boolean")
    tempo_scale = body.get("tempo_scale", 1.0)
    if (
        isinstance(tempo_scale, bool)
        or not isinstance(tempo_scale, (int, float))
        or not 0.25 <= float(tempo_scale) <= 4.0
    ):
        raise ApiError(400, "invalid_parameter", "tempo_scale out of 0.25..4.0")
    note_groups = body.get("note_groups", 0)
    if not isinstance(note_groups, int) or isinstance(note_groups, bool) or note_groups < 0:
        raise ApiError(400, "invalid_parameter", "note_groups must be a count")
    result = record_guidance_session(
        service.store,
        session_id=session_id.strip(),
        package_id=package_id,
        mode=str(mode),
        section_id=body.get("section_id"),
        tempo_scale=float(tempo_scale),
        started_at=str(body.get("started_at") or ""),
        note_groups=note_groups,
        completed=completed,
        elapsed_seconds=float(body.get("elapsed_seconds") or 0.0),
        loop_count=int(body.get("loop_count") or 0),
        hand_selection=body.get("hand"),
    )
    return 201, {"recorded": True, "session": result.to_dict()}


def packages_guidance_frame(context, request) -> tuple[int, dict]:
    """One semantic LED frame for a guided run (deterministic probe).

    Body: {package_id, mode, position_seconds, lookahead_ms, tempo_scale,
    section_id?, loop_start?, loop_end?}. Pure: no session state is
    mutated; the frame is computed and returned (and optionally recorded
    by the caller). This is the software-side LED semantic evidence.
    """
    service = _require_packages(context)
    body = _parse_json_object(
        request,
        required=True,
        allowed=frozenset({
            "package_id", "mode", "position_seconds", "lookahead_ms",
            "tempo_scale", "section_id", "loop_start", "loop_end", "hand",
        }),
    )
    package_id = body.get("package_id")
    if not isinstance(package_id, str) or not package_id.strip():
        raise ApiError(400, "invalid_parameter", "package_id is required")
    mode = body.get("mode")
    if mode not in GUIDED_MODES:
        raise ApiError(400, "invalid_parameter", "mode must be one of LISTEN/FOLLOW/WAIT")
    position = body.get("position_seconds", 0.0)
    if (
        isinstance(position, bool)
        or not isinstance(position, (int, float))
        or position < 0
    ):
        raise ApiError(400, "invalid_parameter", "position_seconds must be >= 0")
    lookahead_ms = body.get("lookahead_ms", 500)
    if not isinstance(lookahead_ms, int) or isinstance(lookahead_ms, bool) or lookahead_ms <= 0:
        raise ApiError(400, "invalid_parameter", "lookahead_ms must be a positive int")
    tempo_scale = body.get("tempo_scale", 1.0)
    if (
        isinstance(tempo_scale, bool)
        or not isinstance(tempo_scale, (int, float))
        or not 0.25 <= float(tempo_scale) <= 4.0
    ):
        raise ApiError(400, "invalid_parameter", "tempo_scale out of 0.25..4.0")
    hand = body.get("hand")
    if hand is not None and hand not in ("RH", "LH"):
        raise ApiError(400, "invalid_parameter", "hand must be 'RH' or 'LH'")
    package = service.load_package(package_id.strip())
    section = plan_section(package, body.get("section_id"))
    run = GuidedRun(
        package=package,
        tempo_scale=float(tempo_scale),
        loop_start=body.get("loop_start"),
        loop_end=body.get("loop_end"),
    )
    del section  # section scoping affects plans; the frame uses raw events
    run.seek(float(position))
    from piano_core.packages.led_frames import LedNoteState, build_frame

    current, upcoming = run.window(lookahead_ms / 1000.0)
    current_notes = [
        (LedNoteState.ACTIVE, event) for event in current
    ]
    upcoming_notes = [(LedNoteState.UPCOMING, event) for event in upcoming]
    if hand is not None:
        current_notes = [x for x in current_notes if x[1].hand == hand]
        upcoming_notes = [x for x in upcoming_notes if x[1].hand == hand]
    frame = build_frame(
        practice_state=str(mode),
        song_time=run.position,
        current=[_led_note(x[1], x[0]) for x in current_notes],
        upcoming=[_led_note(x[1], x[0]) for x in upcoming_notes],
        lookahead_ms=lookahead_ms,
        hand=hand,
        section=body.get("section_id"),
        tempo_scale=float(tempo_scale),
    )
    return 200, {"frame": frame.to_dict()}


def _led_note(event, state):
    """Adapter: package event -> led_frames LedNote with the given state."""
    from piano_core.packages.led_frames import LedNote

    return LedNote(
        note=event.note,
        state=state,
        hand=event.hand,
        onset=event.onset,
        offset=event.offset,
        section=event.section,
    )


ROUTES = (
    Route("GET", "/api/v1/health", health),
    Route("GET", "/api/v1/device", device),
    Route("GET", "/api/v1/state", state),
    Route("GET", "/api/v1/live-notes", live_notes),
    Route("GET", "/api/v1/live-notes/stream", live_note_stream),
    Route("GET", "/api/v1/led", led_state),
    Route("PUT", "/api/v1/led", led_update),
    Route("POST", "/api/v1/led/clear", led_clear),
    Route("GET", "/api/v1/library", library_stats),
    Route(
        "GET",
        "/api/v1/library/songs",
        library_songs,
        query_keys=frozenset({"q", "limit", "offset", "status", "format"}),
    ),
    Route("GET", "/api/v1/library/songs/<song_id>", library_song),
    Route("GET", "/api/v1/score/<song_id>", score),
    Route("GET", "/api/v1/fingering/<song_id>", fingering),
    Route("GET", "/api/v1/playback", playback_state),
    Route("POST", "/api/v1/playback/play", playback_play),
    Route("POST", "/api/v1/playback/pause", playback_pause),
    Route("POST", "/api/v1/playback/resume", playback_resume),
    Route("POST", "/api/v1/playback/stop", playback_stop),
    Route("GET", "/api/v1/practice", practice_state),
    Route("POST", "/api/v1/practice/start", practice_start),
    Route("POST", "/api/v1/practice/stop", practice_stop),
    Route("GET", "/api/v1/learning", learning_state),
    Route("POST", "/api/v1/learning/start", learning_start),
    Route("POST", "/api/v1/learning/pause", learning_pause),
    Route("POST", "/api/v1/learning/progress/retry", learning_retry_progress),
    Route("POST", "/api/v1/learning/stop", learning_stop),
    Route("POST", "/api/v1/learning/restart-target", learning_restart_target),
    Route("POST", "/api/v1/learning/restart-section", learning_restart_section),
    Route("GET", "/api/v1/packages/capabilities", packages_capabilities),
    Route("GET", "/api/v1/packages", packages_list),
    Route("POST", "/api/v1/packages/upload", packages_upload),
    Route("GET", "/api/v1/packages/<package_id>", packages_get),
    Route("GET", "/api/v1/packages/<package_id>/progress", packages_progress),
    Route(
        "POST",
        "/api/v1/packages/<package_id>/sessions",
        packages_record_session,
    ),
    Route("POST", "/api/v1/packages/guidance-frame", packages_guidance_frame),
    Route("GET", "/api/v1/learn", learn_overview),
    Route(
        "GET",
        "/api/v1/learn/lessons",
        learn_lessons,
        query_keys=frozenset({"course", "level", "q", "limit", "offset"}),
    ),
    Route("GET", "/api/v1/learn/lessons/<lesson_id>", learn_lesson_detail),
)
