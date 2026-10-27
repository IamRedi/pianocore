"""Piano Core application: composition root and deterministic lifecycle.

This module is the composition root of the appliance (the clean rewrite of
the legacy ``app/main.py`` role, frozen spec §3/§17). PIANO-P001 implements
only the skeleton lifecycle:

1. load and validate configuration (fail closed);
2. initialize logging;
3. announce application identity and move health to READY;
4. enter a minimal running state that waits efficiently for a stop request;
5. shut down cleanly to STOPPED.

Functional subsystems (state, MIDI, playback, library, LED, API) attach in
later bounded tasks as ``piano_core.*`` subpackages. No hardware-specific or
protocol-specific logic may appear in this module.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from piano_core import PRODUCT_NAME, RUNTIME_IDENTIFIER, __version__
from piano_core.api.server import ApiServer
from piano_core.config import AppConfig, ConfigError
from piano_core.device_identity import DeviceIdentity, IdentityError, load_identity
from piano_core.health import Health, HealthState
from piano_core.fingering.service import FingeringService
from piano_core.led import LedIntegration
from piano_core.logging_setup import configure_logging
from piano_core.midi.backends import MidoBackend, MidoOutputBackend
from piano_core.midi.input import (
    AmbiguousEndpointError,
    ConnectionState,
    EndpointNotFoundError,
    MidiInputAdapter,
)
from piano_core.midi.output import MidiOutputAdapter
from piano_core.midi.output_events import stop_automated_notes
from piano_core.library.index import LibraryIndex
from piano_core.library.model import (
    LibraryConfigError,
    LibraryError,
    LibraryIndexError,
)
from piano_core.library.service import LibraryConfig, LibraryService
from piano_core.live_notes import LiveNoteState
from piano_core.playback.engine import PlaybackState
from piano_core.playback.manager import PlaybackManager
from piano_core.playback.score import ScoreService
from piano_core.practice.manager import PracticeManager
from piano_core.state import MidiConnectionState, StateStore

#: Mapping from adapter connection states to canonical store states (P006).
_ADAPTER_CONNECTION_MAP = {
    ConnectionState.DISCONNECTED: MidiConnectionState.DISCONNECTED,
    ConnectionState.CONNECTING: MidiConnectionState.CONNECTING,
    ConnectionState.CONNECTED: MidiConnectionState.CONNECTED,
}


class Application:
    """Deterministic application lifecycle.

    State machine (see :mod:`piano_core.health`):
    ``STARTING -> READY -> STOPPING -> STOPPED``.

    Usage::

        app = Application.from_env()
        app.start()                 # logging + identity + READY
        app.install_signal_handlers()
        app.run()                   # waits efficiently for a stop request
        app.shutdown()              # STOPPED
    """

    def __init__(
        self,
        config: AppConfig,
        midi_backend_factory: Callable[[], object] | None = None,
        midi_output_backend_factory: Callable[[], object] | None = None,
        playback_lock_path: str | None = None,
    ) -> None:
        if not isinstance(config, AppConfig):
            raise TypeError(
                f"config must be an AppConfig, got {type(config).__name__}"
            )
        self.config = config
        self.health = Health()
        self._stop_requested = threading.Event()
        self._log = logging.getLogger("piano_core.app")
        self._midi_backend_factory = midi_backend_factory or MidoBackend
        self._midi_output_backend_factory = (
            midi_output_backend_factory or MidoOutputBackend
        )
        self._playback_lock_path = playback_lock_path
        self.midi_adapter: MidiInputAdapter | None = None
        self.state_store = StateStore()
        self.live_notes = LiveNoteState()
        self.midi_output: MidiOutputAdapter | None = None
        self.library: LibraryService | None = None
        self.library_ready: bool = False
        # M5 (ADR-PACKAGE-PRACTICE-V1): imported practice packages.
        self.packages_service = None
        self.packages_ready: bool = False
        # PIANO-LEARN-001: Learn Library catalog service (auxiliary).
        self.learn_service = None
        self.learn_ready: bool = False
        self.score: ScoreService | None = None
        self.fingering: FingeringService | None = None
        self.led: LedIntegration | None = None
        self.playback: PlaybackManager | None = None
        self.practice: PracticeManager | None = None
        self.device_identity: DeviceIdentity | None = None
        self.device_identity_error: str | None = None
        self.api_server: ApiServer | None = None

    @classmethod
    def from_env(
        cls,
        env: object = None,
        midi_backend_factory: Callable[[], object] | None = None,
        midi_output_backend_factory: Callable[[], object] | None = None,
        playback_lock_path: str | None = None,
    ) -> "Application":
        """Build an application from environment configuration (validated).

        ``midi_backend_factory`` optionally overrides the MIDI input backend
        constructor used when MIDI input is enabled (default:
        :class:`~piano_core.midi.backends.MidoBackend`);
        ``midi_output_backend_factory`` does the same for the output backend
        (default: :class:`~piano_core.midi.backends.MidoOutputBackend`).
        ``playback_lock_path`` overrides the appliance playback advisory
        lock path (tests only; production uses the shared /run/lock path).
        """
        return cls(
            AppConfig.from_env(env),
            midi_backend_factory=midi_backend_factory,
            midi_output_backend_factory=midi_output_backend_factory,
            playback_lock_path=playback_lock_path,
        )

    def start(self) -> None:
        """Initialize logging, announce identity, and become READY.

        Starts the MIDI input adapter first when configuration enables it
        (``PIANO_CORE_MIDI_INPUT_ENABLED=true``). The adapter degrades to
        DISCONNECTED with bounded reconnect when the endpoint is absent —
        the application reaches READY regardless and never silently claims
        a connection (connection state is logged and exposed via the
        adapter). Raises
        :class:`piano_core.health.InvalidHealthTransition` if called when
        the application is not STARTING (start is a one-shot).
        """
        configure_logging(self.config.log_level)
        self._log.info(
            "startup product=%s runtime=%s version=%s mode=%s",
            PRODUCT_NAME,
            RUNTIME_IDENTIFIER,
            __version__,
            self.config.mode.value,
        )
        # PIANO-PROD-001: load (never generate) the persistent device
        # identity exactly once, after logging exists and BEFORE any
        # subsystem — in particular before the API starts. Identity
        # problems are auxiliary: they are logged and cached, and the
        # musical appliance starts unchanged.
        self._load_device_identity()
        if self.config.midi_input_enabled:
            self._start_midi_input()
        if self.config.midi_output_enabled:
            self._start_midi_output()
        if self.config.library_enabled:
            self._start_library()
        if self.config.packages_enabled:
            self._start_packages()
        self._start_learn()
        self._start_score()
        self._start_fingering()
        if self.config.led_enabled:
            self._start_led()
        self._start_playback()
        self._start_practice()
        if self.midi_output is not None and self.midi_output.is_open:
            self.midi_output.start_monitor(self._on_output_connection)
        if self.config.api_enabled:
            self._start_api()
        if self.config.api_enabled and self.api_server is None:
            # PIANO-P015-HF2: an ENABLED API that failed to start must
            # never be reported as a fully healthy appliance. The
            # lifecycle is honestly DEGRADED — the process keeps running
            # (MIDI input/state stay live) while the control surface is
            # down, and the journal says so. With the network-independent
            # production bind (0.0.0.0) this cannot happen at cold boot:
            # INADDR_ANY binds before any interface address exists.
            self.health.transition(HealthState.DEGRADED)
            self._log.error(
                "ready (DEGRADED: api enabled but unavailable — "
                "see 'api unavailable' above)"
            )
        else:
            self.health.transition(HealthState.READY)
            self._log.info("ready")

    def _load_device_identity(self) -> None:
        """Load the persistent device identity ONCE (PIANO-PROD-001).

        Load-only by contract: startup NEVER generates, repairs, or
        replaces an identity (creation happens only through
        ``piano-core-identity initialize``). Outcomes:

        - **not configured** (empty ``device_identity_dir``): the
          feature is unavailable, nothing is read, startup unchanged;
        - **valid identity**: cached for the whole process lifetime
          (the read-only ``GET /api/v1/device`` serves this cache);
        - **missing/corrupt/unreadable/unsafe**: the stable reason is
          logged and cached in ``device_identity_error``; the appliance
          continues its full musical startup — identity failure NEVER
          alters ``HealthState``, ``/api/v1/health``, MIDI, playback,
          practice, library, or LED. There is deliberately no retry
          thread, watcher, or reload.
        """
        directory = self.config.device_identity_dir
        if not directory:
            self.device_identity = None
            self.device_identity_error = "identity_unconfigured"
            return
        try:
            self.device_identity = load_identity(directory)
        except IdentityError as exc:
            self.device_identity = None
            self.device_identity_error = exc.reason
            self._log.error("device identity unavailable: %s", exc.reason)
            return
        except Exception as exc:  # auxiliary subsystem: never break startup
            self.device_identity = None
            self.device_identity_error = "identity_storage_unavailable"
            self._log.error("device identity unavailable: %s", exc)
            return
        self.device_identity_error = None
        self._log.info(
            "device identity loaded display_code=%s",
            self.device_identity.display_code,
        )

    def _start_midi_input(self) -> None:
        """Instantiate and start the input-only MIDI adapter (F2 closure).

        The adapter owns the ALSA/mido boundary. Its receive thread reduces
        canonical events into the StateStore and observes connection
        transitions, never touching the application lifecycle. Backend/
        connect failures degrade to DISCONNECTED with bounded reconnect —
        the application stays healthy and never silently claims a
        connection.
        """
        try:
            backend = self._midi_backend_factory()
        except Exception as exc:
            self._log.error("midi input unavailable (backend error): %s", exc)
            return
        self.midi_adapter = MidiInputAdapter(
            backend=backend,
            matcher=self.config.midi_input_port_matcher,
            event_callback=self._on_input_event,
            on_connection_state=self._on_midi_connection_state,
            reconnect_delay=2.0,
        )
        self.midi_adapter.start()
        self._log.info(
            "midi input enabled matcher=%s",
            self.config.midi_input_port_matcher,
        )

    def _on_input_event(self, event):
        """Reduce one input event into the store, then observe downstream.

        The StateStore remains THE physical-input authority (P006): the
        LED integration and the P015 practice observer are strictly
        read-only downstream observers and can never alter store state
        or application lifecycle. Both observers are bounded and fast
        (in-memory + bounded enqueue); none of them can delay the MIDI
        reader thread meaningfully.
        """
        state = self.state_store.apply_event(event)
        self.live_notes.publish_physical(state)
        led = self.led
        if led is not None:
            led.observe_input_event(event)
        practice = self.practice
        if practice is not None:
            practice.observe_input_event(event)
        return state

    def _on_midi_connection_state(self, state: ConnectionState) -> None:
        """Observe adapter connection transitions into the canonical store.

        Deliberately value-only and lifecycle-free: the MIDI reader thread
        must never be able to reach ``Application.request_stop`` (P001 race
        stays unreachable).
        """
        snapshot = self.state_store.set_midi_connection(_ADAPTER_CONNECTION_MAP[state])
        self.live_notes.publish_physical(snapshot)
        practice = self.practice
        if practice is not None:
            practice.observe_connection_state(snapshot.midi_connection)

    def _start_midi_output(self) -> None:
        """Open the canonical output adapter when configured (PIANO-P007).

        Startup opens the port and emits NOTHING: no note is ever sent
        here, and nothing connects human input to output. Endpoint or open
        failures degrade loudly to a healthy process without output (never
        silently pretend output is available).
        """
        if self.midi_output is not None:
            # Reconnect belongs to that adapter. Replacing it here could
            # orphan an unresolved close or reopen after its shutdown.
            self._log.error("midi output already has a lifecycle owner; replacement refused")
            return
        try:
            backend = self._midi_output_backend_factory()
        except Exception as exc:
            self._log.error("midi output unavailable (backend error): %s", exc)
            return
        adapter = MidiOutputAdapter(
            backend=backend,
            matcher=self.config.midi_output_port_matcher,
        )
        try:
            adapter.open()
        except EndpointNotFoundError as exc:
            self._log.error("midi output unavailable (endpoint): %s", exc)
            return
        except AmbiguousEndpointError as exc:
            self._log.error("midi output refused (ambiguous endpoint): %s", exc)
            return
        except Exception as exc:
            self._log.error("midi output unavailable (open failed): %s", exc)
            if adapter.close_pending:
                # Failed initialization still owns an uncertain native
                # output; retain it for explicit reconciliation/shutdown.
                self.midi_output = adapter
            return
        self.midi_output = adapter
        self._publish_output_state()
        self._log.info(
            "midi output enabled matcher=%s port=%s",
            self.config.midi_output_port_matcher,
            adapter.port_name,
        )

    def _start_library(self) -> None:
        """Construct the library subsystem (PIANO-P011) — degrade contract.

        The library is an AUXILIARY subsystem: on any validation or
        initialization failure the reason is logged, the library is marked
        unavailable (``library_ready = False``), and the application starts
        healthy — MIDI input and playback are unaffected. Readiness NEVER
        deep-scans or enriches: it validates the configuration, opens the
        index, and reports the committed entry count.

        The source library must be READ-ONLY from this process (PIANO-P011
        contract; enforced kernel-side by the service bind mount). The
        read-only probe attempts one harmless directory creation inside the
        root and requires it to FAIL; a successful creation is a contract
        violation and marks the library unavailable (the probe artifact is
        removed immediately).
        """
        try:
            if not self.config.library_root or not self.config.library_index_path:
                raise LibraryConfigError(
                    "library root/index path not configured"
                )
            config = LibraryConfig(
                root=self.config.library_root,
                index_path=self.config.library_index_path,
            )
            root_real = config.root_real
            index_real = Path(self.config.library_index_path).resolve()
            if index_real == root_real or root_real in index_real.parents:
                raise LibraryConfigError(
                    "index path must be outside the library root"
                )
            with LibraryIndex(self.config.library_index_path) as index:
                entries = index.stats()["total"]
            self._verify_source_read_only(root_real)
        except Exception as exc:  # auxiliary subsystem: never break startup
            self._log.error("library unavailable: %s", exc)
            return
        self.library = LibraryService(
            config, logger=logging.getLogger("piano_core.library")
        )
        self.library_ready = True
        self._log.info(
            "library ready root=%s index=%s entries=%d source_read_only=True",
            config.root,
            self.config.library_index_path,
            entries,
        )

    def _verify_source_read_only(self, root_real) -> None:
        probe = root_real / f".piano-core-readonly-probe-{os.getpid()}"
        try:
            probe.mkdir()
        except OSError:
            return  # expected: the source view is read-only
        try:
            probe.rmdir()
        except OSError:
            pass
        raise LibraryError(
            "library source is writable; read-only source contract violated"
        )

    def _start_score(self) -> None:
        """Compose the canonical score projection service (PIANO-UI-005).

        Exists whenever the canonical library exists (same auxiliary
        degrade contract). The service owns no MIDI surface: it derives
        read-only note-span projections from the canonical P009 timeline
        and exposes them through ``GET /api/v1/score/<song_id>``. Its
        ``timeline_loader`` wrapper is injected into playback/practice so
        a song's projection is warm exactly when the song becomes active
        — the accepted managers themselves are unchanged.
        """
        if self.library is None:
            return
        # PIANO-LEARN-001: when packages are ready, score/fingering
        # projections also resolve ``package:<id>`` (Learn lessons),
        # while every library id keeps its exact accepted behavior.
        score_library = self.library
        if self.packages_service is not None and self.packages_ready:
            from piano_core.packages.service import PackageAwareLibrary

            score_library = PackageAwareLibrary(self.library, self.packages_service)
        self.score = ScoreService(
            score_library,
            logger=logging.getLogger("piano_core.playback.score"),
        )
        self._log.info("score projection service ready")

    def _start_fingering(self) -> None:
        """Compose inference strictly downstream of canonical score truth."""
        if self.score is None:
            return
        self.fingering = FingeringService(self.score)
        self._log.info("deterministic fingering service ready")

    def _start_led(self) -> None:
        """Start the keyboard LED subsystem (PIANO-P014) — degrade contract.

        AUXILIARY like the library/API: any construction, bind, or start
        failure is logged loudly and the application starts healthy
        without LED visualization. READY never depends on LED availability.
        """
        try:
            led = LedIntegration.from_config(self.config)
            led.start()
        except Exception as exc:  # auxiliary subsystem: never break startup
            self._log.error("led subsystem unavailable: %s", exc)
            return
        self.led = led
        self._log.info(
            "led subsystem enabled mode=%s brightness=%d ws=%s:%d",
            self.config.led_mode.value,
            self.config.led_brightness,
            self.config.led_ws_host,
            self.config.led_ws_port,
        )

    def _start_playback(self) -> None:
        """Compose the canonical service-side playback manager (P012).

        Exists whenever the canonical output adapter is open (P007 open
        semantics: startup emits NOTHING). The manager owns no MIDI of
        its own: every session flows through the PlaybackEngine and the
        shared appliance advisory lock, so ``piano-core-play`` and API
        playback can never run simultaneously.
        """
        if self.midi_output is None or not self.midi_output.is_open:
            return
        self.playback = PlaybackManager(
            output_adapter=self.midi_output,
            library=self.library,
            lock_path=self._playback_lock_path,
            logger=logging.getLogger("piano_core.playback.manager"),
            timeline_loader=(
                self.score.timeline_loader() if self.score is not None else None
            ),
            event_observer=(
                self.led.observe_output_command if self.led is not None else None
            ),
            ownership_observer=self._publish_output_state,
            on_session_end=(
                self.led.session_ended if self.led is not None else None
            ),
        )
        self._log.info("playback manager ready")

    def _publish_output_state(self) -> None:
        """Copy current canonical output ownership into the UI publication."""
        output = self.midi_output
        self.live_notes.publish_playback(
            available=bool(output is not None and output.is_open),
            owned_notes=output.owned_notes if output is not None else (),
            sustain_channels=(
                output.owned_sustain_channels if output is not None else ()
            ),
        )

    def _on_output_connection(self, connected: bool) -> None:
        if not connected and self.playback is not None:
            self.playback.output_disconnected()
        self._publish_output_state()

    def _start_packages(self) -> None:
        """Compose the practice-package subsystem (M5) — degrade contract.

        Auxiliary like the library: any failure logs the reason, marks
        ``packages_ready = False`` and the application starts healthy.
        Startup only validates configuration and opens the store; the
        importer reconciles (removes) abandoned staging directories and
        never scans or promotes anything else.
        """
        try:
            if not self.config.packages_root:
                raise RuntimeError("packages root not configured")
            if not self.config.packages_db_path:
                raise RuntimeError("packages db path not configured")
            root_real = Path(self.config.packages_root).resolve()
            db_real = Path(self.config.packages_db_path).resolve()
            if db_real == root_real or root_real in db_real.parents:
                raise RuntimeError(
                    "packages db path must be outside the packages root"
                )
            from piano_core.packages.service import PackageService

            service = PackageService(
                root=self.config.packages_root,
                db_path=self.config.packages_db_path,
            )
        except Exception as exc:  # auxiliary subsystem: never break startup
            self._log.error("practice packages unavailable: %s", exc)
            return
        self.packages_service = service
        self.packages_ready = True
        self._log.info(
            "practice packages ready root=%s db=%s",
            self.config.packages_root,
            self.config.packages_db_path,
        )

    def _start_learn(self) -> None:
        """Compose the Learn Library catalog (PIANO-LEARN-001) — degrade.

        Auxiliary read-side over the deterministic learn catalog database.
        Progress truth stays in the package store; this service merges it
        per lesson. Any failure marks ``learn_ready = False`` and the
        application starts healthy; the Player is untouched either way.
        """
        try:
            if not self.config.learn_enabled:
                raise RuntimeError("learn subsystem disabled")
            if not self.config.learn_db_path:
                raise RuntimeError("learn db path not configured")
            from piano_core.learn.service import LearnService

            lookup = None
            if self.packages_service is not None and self.packages_ready:
                store = self.packages_service.store

                def lookup(package_id):
                    return store.progress_summary(package_id)

            service = LearnService(
                db_path=self.config.learn_db_path,
                progress_lookup=lookup,
                progress_all_lookup=store.learning_progress if lookup is not None else None,
                progress_many_lookup=store.progress_summaries if lookup is not None else None,
            )
        except Exception as exc:  # auxiliary subsystem: never break startup
            self._log.error("learn library unavailable: %s", exc)
            return
        self.learn_service = service
        self.learn_ready = True
        self._log.info(
            "learn library ready db=%s", self.config.learn_db_path
        )

    def _start_practice(self) -> None:
        """Compose the canonical guided-practice manager (PIANO-P015).

        Always constructed (an auxiliary subsystem with the documented
        degrade contract): availability derives honestly from the
        library; exclusivity reuses the SAME appliance-wide advisory
        lock as playback, so practice and playback can never run at the
        same time. Practice holds NO MIDI output reference: guided
        practice is silent by design (ADR-PRACTICE-V1 D7).

        M5: when the packages subsystem is ready, practice sees a
        PackageAwareLibrary wrapper, so ``package:<id>`` song ids resolve
        to imported packages while every library id keeps its exact
        accepted behavior (ADR-PACKAGE-PRACTICE-V1).
        """
        practice_library = self.library
        if self.packages_service is not None and self.library is not None:
            from piano_core.packages.service import PackageAwareLibrary

            practice_library = PackageAwareLibrary(
                self.library, self.packages_service
            )
        recorder = None
        if self.packages_service is not None:
            from piano_core.packages.sessions import record_engine_session
            store = self.packages_service.store
            recorder = lambda session: record_engine_session(store, session)
        self.practice = PracticeManager(
            session_recorder=recorder,
            library=practice_library,
            state_store=self.state_store,
            lock_path=self._playback_lock_path,
            led=self.led,
            conflict_probe=self._playback_activity_reason,
            logger=logging.getLogger("piano_core.practice.manager"),
            timeline_loader=(
                self.score.timeline_loader() if self.score is not None else None
            ),
        )
        self._log.info("practice manager ready")

    def _playback_activity_reason(self) -> str | None:
        """Human reason when the service-side playback session is active."""
        manager = self.playback
        if manager is None:
            return None
        state = manager.snapshot().state
        if state in (
            PlaybackState.PLAYING.value,
            PlaybackState.PAUSED.value,
            PlaybackState.STOPPING.value,
        ):
            return f"playback is {state}"
        return None

    def _start_api(self) -> None:
        """Bind and start the local control API (P012) — degrade contract.

        A bind failure (address in use, unbindable address, permission)
        is logged loudly and the application continues without an API —
        since PIANO-P015-HF2 with an honestly DEGRADED lifecycle instead
        of a fully healthy READY (an ENABLED API is the appliance's
        entire control surface). Binding uses the validated
        configuration (loopback by default; the production appliance
        binds 0.0.0.0 since HF2 so the bind succeeds before the network
        is up at cold boot and follows DHCP address changes without
        reconfiguration).
        """
        try:
            server = ApiServer(
                self,
                self.config.api_host,
                self.config.api_port,
                logger=logging.getLogger("piano_core.api"),
                serve_ui=self.config.ui_enabled,
            )
        except OSError as exc:
            self._log.error("api unavailable (bind failed): %s", exc)
            return
        self.api_server = server
        server.start()

    def request_stop(self, reason: str = "requested") -> None:
        """Ask the run loop to stop; idempotent and safe from any thread.

        Moves STARTING/READY/DEGRADED to STOPPING exactly once and wakes
        :meth:`run` (PIANO-P015-HF2: a DEGRADED appliance — API down —
        must remain fully stoppable through the same path).
        """
        if self.health.state in (
            HealthState.STARTING,
            HealthState.READY,
            HealthState.DEGRADED,
        ):
            self.health.transition(HealthState.STOPPING)
            self._log.info("shutdown request reason=%s", reason)
        # Stop SSE admission immediately on the STOPPING transition. This
        # lock-only operation wakes existing streams without network I/O.
        self.live_notes.close()
        self._stop_requested.set()

    def run(self, timeout: float | None = None) -> bool:
        """Enter the running state until a stop is requested.

        Blocks on an event (no polling, no busy loop). Returns ``True`` when
        a stop was requested, ``False`` when ``timeout`` elapsed first. A
        stop request delivered by a signal handler is turned into the
        STOPPING transition here, on the thread running :meth:`run`.
        """
        requested = self._stop_requested.wait(timeout)
        if requested and self.health.state is HealthState.READY:
            self.request_stop("signal")
        return requested

    def shutdown(self) -> None:
        """Move to STOPPED through the legal transition path. Idempotent.

        Shutdown ordering: (1) the API server stops accepting requests
        first, so no control operation races the teardown; (2) any active
        playback session is stopped through the canonical engine (owned
        notes/sustain released, advisory lock released); (3) any active
        guided-practice session is stopped (practice LEDs cleared,
        activity ownership released — no MIDI output is ever touched);
        (4) the MIDI input adapter is stopped; (5) the output adapter's
        existing owned-state safety net runs before the final STOPPED
        transition; (6) the LED subsystem tears down last.
        """
        api_server = self.api_server
        # Publication close is atomic with subscribe and gives late handlers
        # the typed unavailable outcome; the server then drains owned handlers.
        self.live_notes.close()
        if api_server is not None:
            try:
                api_server.shutdown()
            except Exception as exc:
                self._log.error("api shutdown failed: %s", exc)
        manager = self.playback
        if self.midi_output is not None:
            try:
                self.midi_output.stop_monitor(shutdown=True)
            except Exception as exc:
                self._log.error("midi output monitor shutdown failed: %s", exc)
        if manager is not None:
            try:
                manager.shutdown()
                self._log.info(
                    "playback manager stopped state=%s",
                    manager.snapshot().state,
                )
            except Exception as exc:
                self._log.error("playback shutdown failed: %s", exc)
        practice = self.practice
        if practice is not None:
            # Stop BEFORE the LED teardown so the practice-clear op still
            # flushes through the bounded LED worker queue; releases the
            # shared activity ownership (crash-safe flock semantics).
            try:
                practice.shutdown()
                self._log.info(
                    "practice manager stopped state=%s",
                    practice.snapshot().state.value,
                )
            except Exception as exc:
                self._log.error("practice shutdown failed: %s", exc)
        adapter = self.midi_adapter
        if adapter is not None:
            adapter.stop(2.0)
            self._log.info(
                "midi input stopped state=%s events: %s",
                adapter.state.value,
                self.state_store.event_counts_summary(),
            )
        output = self.midi_output
        if output is not None:
            # Safe shutdown: release only Piano Core-owned output state
            # (STOP, never PANIC), then close. Cleanup must never break
            # the shutdown path.
            try:
                try:
                    if output.is_open:
                        owned = output.owned_notes
                        sustain = output.owned_sustain_channels
                        if owned or sustain:
                            output.apply(stop_automated_notes())
                            self._log.info(
                                "midi output shutdown released notes=%d sustain_channels=%d",
                                len(owned),
                                len(sustain),
                            )
                finally:
                    output.close()
            except Exception as exc:
                self._log.error("midi output shutdown cleanup failed: %s", exc)
        led = self.led
        if led is not None:
            # LAST auxiliary teardown: best-effort deterministic clear,
            # transport close, worker join. Must never break shutdown.
            try:
                led.stop()
                self._log.info("led subsystem stopped")
            except Exception as exc:
                self._log.error("led shutdown failed: %s", exc)
        if self.health.state is HealthState.STOPPED:
            return
        if self.health.state is not HealthState.STOPPING:
            self.health.transition(HealthState.STOPPING)
        self.health.transition(HealthState.STOPPED)
        self._log.info("stopped")

    def install_signal_handlers(self) -> None:
        """Handle SIGINT/SIGTERM by requesting a clean stop.

        Installable only on the main thread; silently skipped elsewhere.
        The handler only sets the wake event — the STOPPING transition and
        its logging happen on the :meth:`run` thread.
        """
        if threading.current_thread() is not threading.main_thread():
            return

        def _handler(signum, frame):  # noqa: ANN001 (signal API)
            self._stop_requested.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, _handler)


def main(argv: list[str] | None = None) -> int:
    """Console entrypoint. Returns a process exit code.

    Exit codes: ``0`` clean run/shutdown, ``2`` invalid configuration
    (fail closed before logging is initialized).
    """
    parser = argparse.ArgumentParser(
        prog=RUNTIME_IDENTIFIER,
        description="Piano Core appliance runtime (application skeleton).",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="print product identity and exit",
    )
    parser.add_argument(
        "--run-for",
        type=float,
        default=None,
        metavar="SECONDS",
        help=(
            "run for at most SECONDS seconds, then shut down cleanly "
            "(ops/testing convenience; default: run until SIGINT/SIGTERM)"
        ),
    )
    args = parser.parse_args(argv)

    if args.version:
        print(f"{PRODUCT_NAME} {__version__} ({RUNTIME_IDENTIFIER})")
        return 0

    try:
        app = Application.from_env()
    except ConfigError as error:
        print(
            f"{RUNTIME_IDENTIFIER}: invalid configuration: {error}",
            file=sys.stderr,
        )
        return 2

    app.start()
    try:
        app.install_signal_handlers()
        app.run(timeout=args.run_for)
    except KeyboardInterrupt:
        pass
    finally:
        app.shutdown()
    return 0
