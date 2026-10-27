"""Deterministic JSON serialization for the local control API (PIANO-P012).

Pure converters from canonical product objects to JSON-safe dictionaries.
These functions never invent data: fields are taken from the canonical
snapshots or ``None``. Catalog payloads deliberately omit diagnostic
internals (parse ``error`` text, raw ``mtime_ns``) and absolute host
paths (only the root-relative ``relpath`` is exposed).
"""

from __future__ import annotations

from piano_core import PRODUCT_NAME
from piano_core.device_identity import DeviceIdentity
from piano_core.led.model import LedConnectionState, LedState
from piano_core.library.model import SongRecord
from piano_core.live_notes import LiveNoteSnapshot
from piano_core.playback.manager import PlaybackSession
from piano_core.playback.score import ScoreProjection
from piano_core.fingering.model import FingeringPlan
from piano_core.practice.model import PracticeSession
from piano_core.state import PianoState

__all__ = [
    "device_identity_payload",
    "fingering_payload",
    "led_state_payload",
    "library_stats_payload",
    "live_note_payload",
    "playback_session_payload",
    "practice_session_payload",
    "score_payload",
    "song_record_payload",
    "state_payload",
]


_PIANO_NOTE_MIN = 21
_PIANO_NOTE_MAX = 108


def live_note_payload(snapshot: LiveNoteSnapshot) -> dict:
    """Serialize one immutable full active-note snapshot (PIANO-UI-003)."""
    physical = tuple(
        note for note in snapshot.physical_notes
        if _PIANO_NOTE_MIN <= note <= _PIANO_NOTE_MAX
    )
    voices = tuple(
        (channel, note) for channel, note in snapshot.playback_voices
        if _PIANO_NOTE_MIN <= note <= _PIANO_NOTE_MAX
    )
    playback_notes = sorted({note for _channel, note in voices})
    return {
        "schema_version": snapshot.schema_version,
        "runtime_epoch": snapshot.runtime_epoch,
        "sequence": snapshot.sequence,
        "physical": {
            "notes": list(physical),
            "outside_keyboard_count": len(snapshot.physical_notes) - len(physical),
            "sustain": {
                "value": snapshot.physical_sustain_value,
                "pressed": snapshot.physical_sustain_pressed,
            },
            "midi_connection": snapshot.midi_connection,
            "revision": snapshot.physical_revision,
        },
        "playback": {
            "available": snapshot.playback_available,
            "notes": playback_notes,
            "voices": [
                {"channel": channel, "note": note} for channel, note in voices
            ],
            "outside_keyboard_count": len(snapshot.playback_voices) - len(voices),
            "sustain_channels": list(snapshot.playback_sustain_channels),
        },
    }


def device_identity_payload(identity: DeviceIdentity) -> dict:
    """Serialize the public device identity descriptor (PIANO-PROD-001).

    Exactly four fields, nothing else: the derived ``display_code`` is a
    presentation convenience; the full UUID remains the device identity.
    The UUID is a public device identifier, NOT an authentication
    secret, and this payload never carries hardware/network/path/
    environment details or raw internal error reasons.
    """
    return {
        "product": PRODUCT_NAME,
        "device_id": identity.device_id,
        "display_code": identity.display_code,
        "identity_schema": identity.identity_schema,
    }


def state_payload(state: PianoState) -> dict:
    """Serialize the canonical physical StateStore snapshot.

    ``pressed_notes`` are PHYSICAL keys only (playback never appears
    here), sorted ascending for deterministic payloads.
    """
    last_input = None
    if state.last_input is not None:
        last_input = {
            "type": state.last_input.event_type,
            "channel": state.last_input.channel,
            "timestamp": state.last_input.timestamp,
            "note": state.last_input.note,
            "value": state.last_input.value,
        }
    return {
        "pressed_notes": sorted(state.pressed_notes),
        "sustain": {
            "value": state.sustain_value,
            "pressed": state.sustain_pressed,
        },
        "last_input": last_input,
        "midi_connection": state.midi_connection.value,
        "revision": state.revision,
    }


def led_state_payload(state: LedState | None) -> dict:
    """Serialize the LED subsystem snapshot (``None`` = not constructed).

    ``configured`` distinguishes "the subsystem exists on this appliance
    (it can be enabled/re-enabled through the API)" from "the subsystem
    is not configured at all". Disabling from the UI must never make the
    subsystem unreachable: configured-but-disabled remains toggleable.
    """
    if state is None:
        return {
            "available": False,
            "configured": False,
            "enabled": False,
            "connection": "DISABLED",
            "mode": None,
            "brightness": None,
            "active_note_count": 0,
            "practice_active": False,
            "last_error": None,
            "transport": None,
            "dropped_events": 0,
            "frames_sent": 0,
        }
    transport = None
    if state.host is not None and state.port is not None:
        transport = {"host": state.host, "port": state.port}
    return {
        "available": bool(
            state.enabled
            and state.connection is not LedConnectionState.UNAVAILABLE
        ),
        "configured": True,
        "enabled": state.enabled,
        "connection": state.connection.value,
        "mode": state.mode.value,
        "brightness": state.brightness,
        "active_note_count": state.active_note_count,
        "practice_active": state.practice_active,
        "last_error": state.last_error,
        "transport": transport,
        "dropped_events": state.dropped_events,
        "frames_sent": state.frames_sent,
    }


def library_stats_payload(stats: dict[str, int]) -> dict:
    """Serialize :meth:`LibraryService.stats` output."""
    return {
        "ready": True,
        "total": stats.get("total", 0),
        "parsed": stats.get("parsed", 0),
        "pending": stats.get("pending", 0),
        "invalid": stats.get("invalid", 0),
        "unsupported": stats.get("unsupported", 0),
    }


def song_record_payload(record: SongRecord) -> dict:
    """Serialize one catalog entry (safe metadata only)."""
    return {
        "song_id": record.song_id,
        "filename": record.filename,
        "relpath": record.relpath,
        "extension": record.extension,
        "size_bytes": record.size_bytes,
        "status": record.status.value,
        "midi_format": record.midi_format,
        "track_count": record.track_count,
        "ticks_per_beat": record.ticks_per_beat,
        "duration_seconds": record.duration_seconds,
        "tempo_bpm_first": record.tempo_bpm_first,
        "title": record.title,
        "indexed_at_utc": record.indexed_at_utc,
    }


def playback_session_payload(session: PlaybackSession | None) -> dict:
    """Serialize the playback snapshot (``None`` = subsystem unavailable)."""
    if session is None:
        return {
            "available": False,
            "state": None,
            "tempo_multiplier": None,
            "position_seconds": None,
            "duration_seconds": None,
            "events_sent": None,
            "error": None,
            "song_id": None,
            "title": None,
        }
    return {
        "available": session.available,
        "state": session.state,
        "tempo_multiplier": session.tempo_multiplier,
        "position_seconds": session.position_seconds,
        "duration_seconds": session.duration_seconds,
        "events_sent": session.events_sent,
        "error": session.error,
        "song_id": session.song_id,
        "title": session.title,
    }


def score_payload(projection: ScoreProjection) -> dict:
    """Serialize one canonical score projection (PIANO-UI-005).

    Bounded and honest: note spans carry only canonical fields
    (index/pitch/channel/onset/duration/velocity in song-time seconds).
    The ``notation`` block states machine-readably which notation
    capabilities the current canonical timeline can and cannot support —
    unsupported items must never be fabricated by any renderer.
    """
    return {
        "schema_version": 1,
        "song_id": projection.song_id,
        "source_version": projection.source_version,
        "title": projection.title,
        "duration_seconds": projection.duration_seconds,
        "time_basis": "song_seconds",
        "note_count": projection.span_count,
        "notes": [
            {
                "index": span.index,
                "midi": span.note,
                "channel": span.channel,
                "velocity": span.velocity,
                "onset_seconds": span.onset_seconds,
                "duration_seconds": span.duration_seconds,
            }
            for span in projection.spans
        ],
        "diagnostics": {
            "percussion_note_ons": projection.percussion_note_ons,
            "outside_keyboard_note_ons": projection.outside_keyboard_note_ons,
            "unpaired_note_offs": projection.unpaired_note_offs,
            "retriggered_note_ons": projection.retriggered_note_ons,
            "unterminated_note_ons": projection.unterminated_note_ons,
        },
        "notation": {
            "supported": ["pitch", "onset", "duration", "channel", "velocity"],
            "unsupported": [
                "track",
                "tempo_map",
                "time_signature",
                "measure",
                "beat",
                "hand",
                "fingering",
                "voice",
                "phrasing",
                "enharmonic_spelling",
            ],
        },
    }


def fingering_payload(plan: FingeringPlan) -> dict:
    """Serialize one complete, version-bound inferred fingering plan."""
    return {
        "schema_version": plan.schema_version,
        "engine_version": plan.engine_version,
        "plan_id": plan.plan_id,
        "song_id": plan.song_id,
        "source_version": plan.source_version,
        "version": plan.version,
        "generation": plan.generation,
        "strategy": plan.strategy,
        "provenance": plan.provenance,
        "targets": [
            {
                "score_index": target.score_index,
                "target_id": target.target_id,
                "midi": target.midi,
                "channel": target.channel,
                "onset_seconds": target.onset_seconds,
                "duration_seconds": target.duration_seconds,
                "hand": target.hand,
                "finger": target.finger,
                "alternatives": list(target.alternatives),
            }
            for target in plan.targets
        ],
        "warnings": list(plan.warnings),
        "excluded_note_count": plan.excluded_note_count,
        "config": plan.config.identity(),
        "capabilities": {
            "authored_metadata": False,
            "manual_overrides": False,
            "manual_override_persistence": "deferred",
            "alternatives": True,
            "finger_sensitive_grading": False,
            "hand_pose_or_ik": False,
        },
    }


def practice_session_payload(session: PracticeSession | None) -> dict:
    """Serialize the practice snapshot (``None`` = subsystem unavailable).

    Bounded and honest: no raw MIDI internals and no filesystem paths —
    the target carries note numbers, human names, chord semantics and
    the source onset (observational metadata only; practice is
    self-paced).
    """
    if session is None:
        return {
            "available": False,
            "state": None,
            "song_id": None,
            "title": None,
            "step_index": None,
            "total_steps": None,
            "completed_steps": 0,
            "incorrect_attempts": 0,
            "progress": 0.0,
            "target": None,
            "started_at": None,
            "ended_at": None,
            "error": None,
            "midi_connection": None,
            "session_id": None,
            "mode": None,
            "hand_selection": None,
            "section": None,
            "target_tempo": None,
            "accepted_notes": [],
            "latest_outcome": "NONE",
            "repetition": {
                "current": 0,
                "required": 1,
                "successful": 0,
                "failed": 0,
            },
            "generation": 0,
            "version": 0,
            "updated_at": None,
        }
    target = None
    if session.current_target is not None:
        target = {
            "notes": list(session.current_target.notes),
            "names": list(session.current_target.names),
            "chord": session.current_target.chord,
            "onset_seconds": session.current_target.onset_seconds,
            "target_id": session.current_target.target_id,
        }
    section = None
    if session.section is not None:
        section = {
            "start_seconds": session.section.start_seconds,
            "end_seconds": session.section.end_seconds,
        }
    return {
        "flow": session.flow,
        "available": session.available,
        "state": session.state.value,
        "song_id": session.song_id,
        "title": session.title,
        "step_index": session.step_index,
        "total_steps": session.total_steps,
        "completed_steps": session.completed_steps,
        "incorrect_attempts": session.incorrect_attempts,
        "progress": session.progress,
        "target": target,
        "started_at": session.started_at,
        "ended_at": session.ended_at,
        "error": session.error,
        "midi_connection": session.midi_connection,
        "session_id": session.session_id,
        "mode": session.mode.value,
        "hand_selection": session.hand_selection,
        "section": section,
        "target_tempo": session.target_tempo,
        "accepted_notes": list(session.accepted_notes),
        "latest_outcome": session.latest_outcome.value,
        "repetition": {
            "current": session.current_repetition,
            "required": session.required_repetitions,
            "successful": session.successful_repetitions,
            "failed": session.failed_repetitions,
        },
        "generation": session.generation,
        "version": session.version,
        "updated_at": session.updated_at,
    }
