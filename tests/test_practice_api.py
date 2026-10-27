"""PIANO-P015: practice API, exclusivity, and input boundary tests.

HTTP-level behavior on real loopback sockets (P012 envelope discipline),
appliance activity exclusivity through the SHARED advisory lock
(ADR-PRACTICE-V1 D6), and the read-only input boundary (D4): only
physical MIDI input advances practice; automated playback output and LED
state can never advance it; StateStore stays authoritative and unmutated.
"""

import json
import logging
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from piano_core.api.server import ApiServer
from piano_core.health import Health, HealthState
from piano_core.midi.events import note_off as ie_off
from piano_core.midi.events import note_on as ie_on
from piano_core.library.service import LibraryConfig, LibraryService
from piano_core.midi.output import MidiOutputAdapter
from piano_core.playback.manager import PlaybackLockBusyError, PlaybackManager
from piano_core.practice.manager import PracticeManager
from piano_core.practice.model import (
    PracticeActivityConflictError,
    PracticeAlreadyActiveError,
    PracticeNotAvailableError,
    PracticePlanError,
)
from piano_core.state import MidiConnectionState, StateStore
from tests.test_led_adapter import FakeTransport
from tests.test_library import write_midi
from tests.test_midi_output import FakeOutputBackend


def _practice_timeline():
    from piano_core.midi.output_events import note_on
    from piano_core.playback.timeline import Timeline, TimelineEvent

    return Timeline(
        events=(
            TimelineEvent(0.0, note_on(1, 60, 80)),
            TimelineEvent(1.0, note_on(1, 62, 80)),
        ),
        duration=2.0,
        source="practice.mid",
    )


def _unplayable_timeline():
    from piano_core.playback.timeline import Timeline

    return Timeline(events=(), duration=0.0, source="empty.mid")


class ManagerHarness(unittest.TestCase):
    """Library + StateStore + practice manager over one temp appliance."""

    def setUp(self):
        logging.getLogger("piano_core.practice").setLevel(logging.CRITICAL)
        self.addCleanup(
            logging.getLogger("piano_core.practice").setLevel, logging.INFO
        )
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        root = self.tmp / "root"
        root.mkdir()
        write_midi(root / "practice.mid", notes=((60, 80), (62, 80)))
        write_midi(root / "empty.mid", notes=((60, 80),))  # loader overrides
        self.library = LibraryService(
            LibraryConfig(
                root=str(root),
                index_path=str(self.tmp / "index" / "library.sqlite3"),
            )
        )
        self.library.scan(enrich=False)
        self.songs = {
            record.filename: record.song_id
            for record in self.library.list_songs(limit=100)
        }
        self.store = StateStore()
        # Production reality (U4-F1): the canonical input path is
        # CONNECTED before any practice grading can occur; a DISCONNECTED
        # store must never advance a session.
        self.store.set_midi_connection(MidiConnectionState.CONNECTED)
        self.manager = PracticeManager(
            library=self.library,
            state_store=self.store,
            lock_path=str(self.tmp / "activity.lock"),
            led=None,
            timeline_loader=lambda path, tempo_scale=1.0: _practice_timeline(),
        )
        self.addCleanup(self.manager.shutdown)

    # mirrors Application._on_input_event (StateStore reduce -> observe)
    def play(self, note, velocity=90):
        event = ie_on(1, note, velocity, 0.0)
        self.store.apply_event(event)
        self.manager.observe_input_event(event)

    def release(self, note):
        event = ie_off(1, note, 0, 0.0)
        self.store.apply_event(event)
        self.manager.observe_input_event(event)


class PracticeManagerTests(ManagerHarness):
    def test_start_stop_snapshot_lifecycle(self):
        self.assertEqual(self.manager.snapshot().state.value, "IDLE")
        self.assertTrue(self.manager.available)
        session = self.manager.start(self.songs["practice.mid"])
        self.assertEqual(session.state.value, "WAITING")
        self.assertEqual(session.step_index, 0)
        self.assertEqual(session.total_steps, 2)
        self.assertEqual(session.current_target.notes, (60,))
        # The harness models a CONNECTED canonical input path (U4-F1):
        # practice only ever grades over a connected input.
        self.assertEqual(session.midi_connection, "CONNECTED")
        stopped_session, stopped = self.manager.stop()
        self.assertTrue(stopped)
        self.assertEqual(stopped_session.state.value, "IDLE")
        _, stopped_again = self.manager.stop()
        self.assertFalse(stopped_again)

    def test_duplicate_start_is_typed_conflict(self):
        self.manager.start(self.songs["practice.mid"])
        with self.assertRaises(PracticeAlreadyActiveError):
            self.manager.start(self.songs["practice.mid"])

    def test_unknown_song_maps_to_library_error(self):
        from piano_core.library.model import LibraryPathError

        with self.assertRaises(LibraryPathError):
            self.manager.start("does-not-exist")

    def test_zero_target_song_is_typed_plan_error(self):
        manager = PracticeManager(
            library=self.library,
            state_store=self.store,
            lock_path=str(self.tmp / "activity.lock"),
            timeline_loader=lambda path, tempo_scale=1.0: _unplayable_timeline(),
        )
        self.addCleanup(manager.shutdown)
        with self.assertRaises(PracticePlanError):
            manager.start(self.songs["empty.mid"])

    def test_unavailable_library_is_typed_error(self):
        manager = PracticeManager(
            library=None,
            state_store=self.store,
            lock_path=str(self.tmp / "activity.lock"),
        )
        self.addCleanup(manager.shutdown)
        self.assertFalse(manager.available)
        with self.assertRaises(PracticeNotAvailableError):
            manager.start("whatever")
        # an unavailable library must not break an existing session's report
        self.assertEqual(manager.snapshot().state.value, "IDLE")

    def test_timeline_failure_maps_to_midi_file_error(self):
        from piano_core.playback.timeline import MidiFileError

        manager = PracticeManager(
            library=self.library,
            state_store=self.store,
            lock_path=str(self.tmp / "activity.lock"),
            timeline_loader=lambda path, tempo_scale=1.0: (_ for _ in ()).throw(
                MidiFileError("garbage")
            ),
        )
        self.addCleanup(manager.shutdown)
        with self.assertRaises(MidiFileError):
            manager.start(self.songs["practice.mid"])


class ExclusivityTests(ManagerHarness):
    def _playback_manager(self):
        backend = FakeOutputBackend(["Roland Digital Piano MIDI 1"])
        output = MidiOutputAdapter(backend, matcher="roland")
        output.open()
        self.addCleanup(output.close)
        return PlaybackManager(
            output_adapter=output,
            library=self.library,
            timeline_loader=lambda path, tempo_scale=1.0: _practice_timeline(),
            lock_path=str(self.tmp / "activity.lock"),
        )

    def test_playback_active_service_session_conflicts_practice(self):
        manager = PracticeManager(
            library=self.library,
            state_store=self.store,
            lock_path=str(self.tmp / "activity.lock"),
            conflict_probe=lambda: "playback is PLAYING",
        )
        self.addCleanup(manager.shutdown)
        with self.assertRaises(PracticeActivityConflictError):
            manager.start(self.songs["practice.mid"])

    def test_practice_conflicts_api_playback_through_shared_lock(self):
        self.manager.start(self.songs["practice.mid"])
        playback = self._playback_manager()
        with self.assertRaises(PlaybackLockBusyError):
            playback.play(self.songs["practice.mid"])

    def test_cli_lock_held_conflicts_practice(self):
        from piano_core.playback.advisory import acquire_playback_lock

        lock = acquire_playback_lock(str(self.tmp / "activity.lock"))
        try:
            with self.assertRaises(PracticeActivityConflictError):
                self.manager.start(self.songs["practice.mid"])
        finally:
            lock.release()

    def test_practice_stop_releases_shared_activity_ownership(self):
        self.manager.start(self.songs["practice.mid"])
        playback = self._playback_manager()
        with self.assertRaises(PlaybackLockBusyError):
            playback.play(self.songs["practice.mid"])
        self.manager.stop()
        # Ownership released: API playback can start after practice stops.
        session = playback.play(self.songs["practice.mid"])
        self.assertEqual(session.state, "PLAYING")
        playback.stop()


class TerminalReleaseTests(ManagerHarness):
    """F-R1 regression: natural terminal transitions release ownership.

    The shared advisory/activity lock must be released AT the
    WAITING -> COMPLETED and WAITING -> FAILED transitions themselves
    (ADR-PRACTICE-V1 D6), so playback and a fresh practice session work
    immediately — with no Stop call — and stop()/shutdown() remain
    idempotent no-ops afterwards.
    """

    def _run_to_completion(self):
        self.manager.start(self.songs["practice.mid"])
        self.play(60)
        self.release(60)
        self.play(62)  # final step -> natural COMPLETED
        snapshot = self.manager.snapshot()
        self.assertEqual(snapshot.state.value, "COMPLETED")
        return snapshot

    def _force_visible_failure(self):
        self.manager.start(self.songs["practice.mid"])

        def _boom(pressed_now):
            raise RuntimeError("injected internal failure")

        self.manager._engine._advance_locked = _boom
        self.play(60)  # correct note -> advance path -> visible FAILED
        snapshot = self.manager.snapshot()
        self.assertEqual(snapshot.state.value, "FAILED")
        # Leave the StateStore clean so the helper is safely repeatable
        # (a still-held 60 would be disarmed at the next session start).
        self.release(60)
        return snapshot

    def _playback_manager(self):
        backend = FakeOutputBackend(["Roland Digital Piano MIDI 1"])
        output = MidiOutputAdapter(backend, matcher="roland")
        output.open()
        self.addCleanup(output.close)
        return PlaybackManager(
            output_adapter=output,
            library=self.library,
            timeline_loader=lambda path, tempo_scale=1.0: _practice_timeline(),
            lock_path=str(self.tmp / "activity.lock"),
        )

    def test_natural_completion_releases_advisory_lock(self):
        self._run_to_completion()
        self.assertIsNone(self.manager._advisory)

    def test_playback_acquires_lock_immediately_after_completion(self):
        self._run_to_completion()
        # No Stop call: playback must acquire the shared lock right away.
        playback = self._playback_manager()
        session = playback.play(self.songs["practice.mid"])
        self.assertEqual(session.state, "PLAYING")
        playback.stop()

    def test_new_practice_session_starts_immediately_after_completion(self):
        self._run_to_completion()
        session = self.manager.start(self.songs["practice.mid"])
        self.assertEqual(session.state.value, "WAITING")
        self.assertEqual(session.completed_steps, 0)
        self.assertIsNotNone(self.manager._advisory)
        self.manager.stop()

    def test_failure_releases_advisory_lock(self):
        self._force_visible_failure()
        self.assertIsNone(self.manager._advisory)

    def test_new_practice_session_starts_after_failure(self):
        self._force_visible_failure()
        session = self.manager.start(self.songs["practice.mid"])
        self.assertEqual(session.state.value, "WAITING")
        self.manager.stop()

    def test_completion_then_stop_is_safe_and_idempotent(self):
        self._run_to_completion()
        _, stopped = self.manager.stop()
        self.assertTrue(stopped)
        self.assertEqual(self.manager.snapshot().state.value, "IDLE")
        _, stopped_again = self.manager.stop()
        self.assertFalse(stopped_again)
        self.assertIsNone(self.manager._advisory)

    def test_completion_then_shutdown_is_safe(self):
        self._run_to_completion()
        self.manager.shutdown()  # must not raise
        self.assertEqual(self.manager.snapshot().state.value, "IDLE")
        self.assertIsNone(self.manager._advisory)

    def test_failure_then_stop_and_shutdown_are_safe(self):
        self._force_visible_failure()
        _, stopped = self.manager.stop()
        self.assertTrue(stopped)
        self.assertEqual(self.manager.snapshot().state.value, "IDLE")
        # Fail again, then shut down straight from FAILED.
        self._force_visible_failure()
        self.manager.shutdown()  # must not raise
        self.assertEqual(self.manager.snapshot().state.value, "IDLE")
        self.assertIsNone(self.manager._advisory)

    def test_waiting_exclusivity_unchanged(self):
        self.manager.start(self.songs["practice.mid"])
        self.assertIsNotNone(self.manager._advisory)
        playback = self._playback_manager()
        with self.assertRaises(PlaybackLockBusyError):
            playback.play(self.songs["practice.mid"])
        self.manager.stop()
        self.assertIsNone(self.manager._advisory)

    # -- F-R1b hardening: serialized terminal release -------------------

    def test_stale_terminal_notification_never_releases_waiting_session_lock(self):
        """F-R1b guard, deterministic: a terminal notification arriving
        while a session is WAITING (i.e. a NEW session already owns the
        appliance) must be a no-op — it must never release the live
        session's advisory handle."""
        self.manager.start(self.songs["practice.mid"])
        self.manager._on_engine_release()  # stale/late notification
        self.assertIsNotNone(self.manager._advisory)  # handle preserved
        playback = self._playback_manager()
        with self.assertRaises(PlaybackLockBusyError):
            playback.play(self.songs["practice.mid"])
        self.manager.stop()
        self.assertIsNone(self.manager._advisory)

    def test_terminal_release_is_serialized_and_idempotent(self):
        self.manager.start(self.songs["practice.mid"])
        self.play(60)
        self.release(60)
        self.play(62)
        self.assertEqual(self.manager.snapshot().state.value, "COMPLETED")
        self.manager._on_engine_release()  # releases exactly once
        self.assertIsNone(self.manager._advisory)
        self.manager._on_engine_release()  # idempotent no-op
        self.assertIsNone(self.manager._advisory)
        _, stopped = self.manager.stop()  # stop after terminal: safe
        self.assertTrue(stopped)
        self.assertEqual(self.manager.snapshot().state.value, "IDLE")
        self.assertIsNone(self.manager._advisory)


class InputBoundaryTests(ManagerHarness):
    def test_only_canonical_input_path_advances_practice(self):
        self.manager.start(self.songs["practice.mid"])
        # LED writes and store-only changes never advance anything.
        before = self.manager.snapshot()
        self.play(60)
        after = self.manager.snapshot()
        self.assertEqual(after.completed_steps, 1)
        self.assertEqual(before.completed_steps, 0)
        # The observation path NEVER mutates the StateStore: revisions
        # come only from the explicit apply_event calls in play()/release().
        revision = self.store.snapshot().revision
        self.release(60)
        self.assertEqual(self.store.snapshot().revision, revision + 1)

    def test_practice_manager_never_touches_midi_output(self):
        # Structural honesty: the manager holds no output adapter and no
        # input adapter (it opens no second MIDI input by construction).
        self.assertFalse(hasattr(self.manager, "_output"))
        self.assertFalse(hasattr(self.manager, "midi_adapter"))
        self.assertFalse(hasattr(self.manager, "output_adapter"))

    def test_automated_playback_output_cannot_advance_practice(self):
        self.manager.start(self.songs["practice.mid"])
        # Automated playback of ANOTHER instance (its own output adapter)
        # must have no effect on the practice session: only the canonical
        # input callback reaches observe_input_event.
        backend = FakeOutputBackend(["Roland Digital Piano MIDI 1"])
        output = MidiOutputAdapter(backend, matcher="roland")
        output.open()
        self.addCleanup(output.close)
        from piano_core.playback.engine import PlaybackEngine, PlaybackState

        engine = PlaybackEngine(output)
        engine.play(_practice_timeline())
        self.assertTrue(engine.wait(5.0))
        self.assertIs(engine.state, PlaybackState.COMPLETED)
        snapshot = self.manager.snapshot()
        self.assertEqual(snapshot.state.value, "WAITING")
        self.assertEqual(snapshot.completed_steps, 0)


# ----------------------------------------------------------------------
# HTTP-level API tests (P012 envelope discipline)
# ----------------------------------------------------------------------
class FakePracticeManager:
    """Scripted practice manager for handler/HTTP tests."""

    def __init__(self, payload_state="WAITING", available=True):
        self.calls: list[tuple] = []
        self._available = available
        self._state = payload_state

    def snapshot(self):
        from piano_core.practice.model import (
            PracticeSession,
            PracticeState,
            PracticeTarget,
        )

        waiting = self._state == "WAITING"
        return PracticeSession(
            PracticeState(self._state),
            available=self._available,
            song_id="s-1" if self._state != "IDLE" else None,
            title="practice.mid" if self._state != "IDLE" else None,
            step_index=0 if waiting else None,
            total_steps=2 if self._state != "IDLE" else None,
            completed_steps=1 if self._state == "COMPLETED" else 0,
            current_target=PracticeTarget((60,), 0.0) if waiting else None,
            midi_connection="CONNECTED",
        )

    def start(self, song_id, **kwargs):
        self.calls.append(("start", song_id, kwargs))
        self._state = "WAITING"
        return self.snapshot()

    def stop(self, **kwargs):
        self.calls.append(("stop", kwargs))
        stopped = self._state == "WAITING"
        self._state = "IDLE"
        return self.snapshot(), stopped

    def restart_target(self, **kwargs):
        self.calls.append(("restart_target", kwargs))
        return self.snapshot()

    def restart_section(self, **kwargs):
        self.calls.append(("restart_section", kwargs))
        return self.snapshot()


class FakeApiContext:
    def __init__(self):
        self.health = Health()
        self.state_store = StateStore()
        self.library = None
        self.library_ready = False
        self.playback = None
        self.practice = None


class PracticeApiHttpTests(unittest.TestCase):
    """Shared harness: one live server on an ephemeral loopback port."""

    def setUp(self):
        self.context = FakeApiContext()
        self.context.health.transition(HealthState.READY)
        self.server = ApiServer(self.context, "127.0.0.1", 0)
        self.server.start()
        self.addCleanup(self.server.shutdown)
        host, port = self.server.bound_address
        self.base = f"http://{host}:{port}"

    def request(self, path, payload=None, method=None):
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            self.base + path,
            data=body,
            method=method or ("POST" if body is not None else "GET"),
            headers={"Content-Type": "application/json"} if body else {},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_get_practice_without_subsystem_reports_unavailable(self):
        status, payload = self.request("/api/v1/practice")
        self.assertEqual(status, 200)
        self.assertFalse(payload["available"])
        self.assertIsNone(payload["state"])

    def test_get_practice_snapshot_payload_shape(self):
        self.context.practice = FakePracticeManager()
        status, payload = self.request("/api/v1/practice")
        self.assertEqual(status, 200)
        self.assertTrue(payload["available"])
        self.assertEqual(payload["state"], "WAITING")
        self.assertEqual(payload["step_index"], 0)
        self.assertEqual(payload["total_steps"], 2)
        self.assertEqual(payload["target"]["notes"], [60])
        self.assertEqual(payload["target"]["names"], ["C4"])
        self.assertFalse(payload["target"]["chord"])
        self.assertEqual(payload["progress"], 0.0)
        self.assertNotIn("relpath", json.dumps(payload))

    def test_start_valid_song(self):
        manager = FakePracticeManager()
        manager._state = "IDLE"
        self.context.practice = manager
        status, payload = self.request(
            "/api/v1/practice/start", {"song_id": "abc"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["state"], "WAITING")
        self.assertEqual(manager.calls, [("start", "abc", {})])

    def test_start_invalid_bodies(self):
        self.context.practice = FakePracticeManager()
        status, payload = self.request("/api/v1/practice/start", {})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_song_id")
        status, payload = self.request("/api/v1/practice/start", {"song_id": "  "})
        self.assertEqual(status, 400)
        status, payload = self.request(
            "/api/v1/practice/start", {"song_id": "x", "extra": 1}
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "malformed_request")

    def test_start_without_subsystem_is_503(self):
        status, payload = self.request(
            "/api/v1/practice/start", {"song_id": "abc"}
        )
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"]["code"], "practice_unavailable")

    def test_stop_is_idempotent_with_stopped_flag(self):
        manager = FakePracticeManager()
        self.context.practice = manager
        status, payload = self.request("/api/v1/practice/stop", {})
        self.assertEqual(status, 200)
        self.assertTrue(payload["stopped"])
        status, payload = self.request("/api/v1/practice/stop", {})
        self.assertEqual(status, 200)
        self.assertFalse(payload["stopped"])
        self.assertEqual(manager.calls, [("stop", {}), ("stop", {})])

    def test_stop_rejects_non_object_body(self):
        self.context.practice = FakePracticeManager()
        request = urllib.request.Request(
            self.base + "/api/v1/practice/stop",
            data=b"[1,2]",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
        self.assertEqual(status, 400)

    def test_error_envelopes_for_conflicts(self):
        class ConflictingManager(FakePracticeManager):
            def start(self, song_id):
                raise PracticeAlreadyActiveError("already active")

        self.context.practice = ConflictingManager()
        status, payload = self.request(
            "/api/v1/practice/start", {"song_id": "abc"}
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "practice_already_active")

        class ActivityManager(FakePracticeManager):
            def start(self, song_id):
                raise PracticeActivityConflictError("playback is PLAYING")

        self.context.practice = ActivityManager()
        status, payload = self.request(
            "/api/v1/practice/start", {"song_id": "abc"}
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "activity_conflict")

    def test_existing_routes_unchanged(self):
        status, _ = self.request("/api/v1/health")
        self.assertEqual(status, 200)
        status, _ = self.request("/api/v1/playback")
        self.assertEqual(status, 200)
        status, _ = self.request("/api/v1/led")
        self.assertEqual(status, 200)
        status, _ = self.request("/api/v1/nope")
        self.assertEqual(status, 404)

    def test_learning_contract_reports_capabilities_when_inactive(self):
        status, payload = self.request("/api/v1/learning")
        self.assertEqual(status, 200)
        self.assertFalse(payload["available"])
        self.assertEqual(
            payload["capabilities"]["supported_modes"],
            ["WAIT_FOR_NOTE", "SECTION_PRACTICE", "REPEAT_UNTIL_CORRECT", "FLOW"],
        )
        self.assertFalse(payload["capabilities"]["hand_selection"])
        self.assertFalse(payload["capabilities"]["rhythm_grading"])

    def test_learning_start_validates_and_delegates_configuration(self):
        manager = FakePracticeManager(payload_state="IDLE")
        self.context.practice = manager
        status, payload = self.request(
            "/api/v1/learning/start",
            {
                "song_id": "abc",
                "mode": "REPEAT_UNTIL_CORRECT",
                "required_repetitions": 3,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["state"], "WAITING")
        self.assertEqual(manager.calls[0][0:2], ("start", "abc"))
        self.assertEqual(
            manager.calls[0][2]["mode"].value, "REPEAT_UNTIL_CORRECT"
        )
        self.assertEqual(manager.calls[0][2]["required_repetitions"], 3)

    def test_learning_start_rejects_fake_or_malformed_modes_and_sections(self):
        self.context.practice = FakePracticeManager(payload_state="IDLE")
        status, payload = self.request(
            "/api/v1/learning/start", {"song_id": "abc", "mode": "FOLLOW"}
        )
        self.assertEqual((status, payload["error"]["code"]), (400, "invalid_learning_mode"))
        status, payload = self.request(
            "/api/v1/learning/start",
            {
                "song_id": "abc",
                "mode": "SECTION_PRACTICE",
                "section": {"start_seconds": 3, "end_seconds": 2},
            },
        )
        self.assertEqual((status, payload["error"]["code"]), (400, "invalid_section"))

    def test_learning_restart_requires_and_forwards_stale_guards(self):
        manager = FakePracticeManager()
        self.context.practice = manager
        status, payload = self.request("/api/v1/learning/restart-target", {})
        self.assertEqual((status, payload["error"]["code"]), (400, "stale_learning_command"))
        status, _ = self.request(
            "/api/v1/learning/restart-target",
            {"expected_session_id": "session-1", "expected_version": 4},
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            manager.calls[-1],
            (
                "restart_target",
                {"expected_session_id": "session-1", "expected_version": 4},
            ),
        )

    def test_learning_stop_forwards_guard_and_remains_idempotent(self):
        manager = FakePracticeManager()
        self.context.practice = manager
        status, payload = self.request(
            "/api/v1/learning/stop",
            {"expected_session_id": "session-1", "expected_version": 4},
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["stopped"])
        self.assertEqual(
            manager.calls[-1],
            (
                "stop",
                {"expected_session_id": "session-1", "expected_version": 4},
            ),
        )


if __name__ == "__main__":
    unittest.main()
