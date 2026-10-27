# Piano Core Local Control API (PIANO-P012)

> **Status:** Canonical operations contract for the `/api/v1` control API.
> **Architecture decision:** [`docs/decisions/ADR-CONTROL-API-V1.md`](../decisions/ADR-CONTROL-API-V1.md)
> **Owner:** Redion Cekrezi

> **PIANO-P013 note:** the packaged browser UI (see
> [`docs/operations/UI.md`](UI.md)) is served by the same HTTP server on
> the same bind (`GET /`, allowlisted `/ui/...` assets). The `/api/v1`
> route contract below is UNCHANGED by P013.

## 1. Purpose and authority boundaries

The API is an **external control surface over the already-canonical
subsystems**. It owns no business logic and never sends MIDI directly:

| Concern | Canonical authority (unchanged) |
|---|---|
| Application/runtime composition | `Application` (P001) |
| Physical input state | `StateStore` (P006) — read-only for the API |
| Catalog / search / lookup / resolve | `LibraryService` (P010/P011) |
| Playback mechanics | Playback Core (P009) via `PlaybackEngine` |
| Output ownership / STOP semantics | `MidiOutputAdapter` (P007) |
| Live note publication | `LiveNoteState` copies StateStore + output ownership; it owns no musical state |
| Learning targets / grading / progression | `PracticeManager` / `PracticeEngine` (PIANO-UI-004); the API only validates and delegates |

The service-side session manager (`piano_core.playback.manager`) is the
only new composition point: it chains `song_id → LibraryService lookup →
validated resolve → P009 timeline → PlaybackEngine`. A client-supplied
filesystem path is **never** accepted; the only path used is the one
`LibraryService.resolve` validated under the canonical root.

## 2. Network / security model

- **Bind address:** loopback (`127.0.0.1`) by default; LAN exposure
  requires an explicit `PIANO_CORE_API_HOST` production decision. There
  is **no** hidden `0.0.0.0` fallback — all-interface binding is always
  an explicit configuration value.
- **Port:** `8788` by default (`PIANO_CORE_API_PORT`, validated 1..65535,
  fail closed).
- **Production binding (appliance decision, PIANO-P015-HF2):** the
  production service binds `0.0.0.0:8788` (all interfaces, configured
  explicitly via `PIANO_CORE_API_HOST`). `0.0.0.0` is
  network-independent: the bind succeeds before any interface address
  exists at cold boot and follows DHCP address changes without
  reconfiguration — the appliance never depends on one specific
  DHCP-assigned address. Security review: on this single-interface
  home-LAN appliance the exposure is EQUIVALENT to the previous
  specific-LAN-address bind (the same unauthenticated trusted-LAN
  surface; earlier deployments bound `192.168.1.50:8788`). Residual
  (recorded as future productization, NOT part of HF2): authentication,
  TLS, and interface allow-listing before any non-LAN-facing interface
  ever exists.
- **Authentication: NONE — recorded limitation.** The API is a
  **trusted-LAN** surface only. It MUST NOT be treated as Internet-safe:
  no port forwarding, no TLS termination, no cloud exposure. Anyone on
  the LAN can read state and start/stop playback. Authentication is
  deliberately deferred to a later task.
- **PANIC is NOT exposed** through the API (no `/panic` route exists;
  global MIDI silence stays an operator-side action). STOP releases only
  Piano Core-owned notes/sustain and is never a global sweep.
- **API enabled:** `PIANO_CORE_API_ENABLED` (safe default `false`).
- **Bind failure is honestly DEGRADED (PIANO-P015-HF2):** if an ENABLED
  API fails to start, the application lifecycle becomes `DEGRADED`
  instead of a fully healthy READY — `/api/v1/health` answers
  `503 not_ready` ("application is DEGRADED") and the journal records
  it. The process keeps running (MIDI input/state stay live).

## 3. Configuration

| Env variable | Key | Default | Meaning |
|---|---|---|---|
| `PIANO_CORE_API_ENABLED` | `api_enabled` | `false` | Start the API server |
| `PIANO_CORE_API_HOST` | `api_host` | `127.0.0.1` | Bind address (IPv4 literal or `localhost`) |
| `PIANO_CORE_API_PORT` | `api_port` | `8788` | TCP port (1..65535) |

`PIANO_CORE_MIDI_OUTPUT_ENABLED=true` is required for playback through
the API (the canonical output adapter must be open; opening emits
nothing — P007 semantics). Playback endpoints return
`503 playback_unavailable` when the output adapter is not open.

## 4. Runtime lifecycle

- The server is owned by the `Application` lifecycle: started during
  `start()` (after MIDI/library/playback), stopped FIRST during
  `shutdown()` so no control request races the teardown; any active
  playback session is then stopped through the canonical engine
  (owned-note release, advisory-lock release) before the output adapter
  closes. There is no orphan server thread (daemon thread + bounded
  join) and no separate daemon.
- One automated playback per appliance: the service's PlaybackManager
  and the `piano-core-play` CLI share ONE advisory lock
  (`/run/lock/piano-core-playback.lock`). While the service plays, CLI
  plays are refused and vice versa (API surfaces this as
  `409 playback_already_active`).

## 5. Route contract

Base namespace: `/api/v1`. All responses are `application/json`.
Unknown query parameters are rejected (`400 invalid_parameter`).
POST bodies must be JSON (`application/json`), bounded to 64 KiB.

### GET /api/v1/health → 200 | 503

```json
{
  "status": "ok",
  "application": {"state": "READY", "since": "2026-09-13T07:00:00+00:00"},
  "midi_input": {"connection": "CONNECTED"},
  "library": {"ready": true, "entries": 169454},
  "playback": {"available": true, "state": "IDLE", "...": "..."}
}
```

`status` is `degraded` when the library is unavailable or playback is
unavailable. Non-READY application lifecycle → `503 {"error": {"code":
"not_ready", ...}}`. No secrets or environment dumps are exposed.

### GET /api/v1/device → 200 | 503 (PIANO-PROD-001)

Public device identity descriptor, served purely from the identity
loaded once at application startup (no filesystem access from request
handling, no query parameters):

```json
{
  "product": "Piano Core",
  "device_id": "a7f32c91-d2e4-4abc-9def-0123456789ab",
  "display_code": "PC-A7F32C91D2E4",
  "identity_schema": 1
}
```

Exactly these four fields. The `display_code` is derived only
(`"PC-" + device_id.hex[:12].upper()`); the full UUID is the device
identity. **The UUID is a public device identity, NOT an
authentication secret.** The endpoint never exposes MAC, hardware
serial, IP, hostname, filesystem paths, Wi-Fi details, setup secrets,
bootstrap credentials, environment, or raw internal error reasons.

When the identity feature is unconfigured, or the identity is missing,
corrupt, or unreadable: `503 {"error": {"code":
"device_identity_unavailable", "message": "device identity is
unavailable", "status": 503}}`. Identity failures never alter
`/api/v1/health` or any musical subsystem. See
[`DEVICE_IDENTITY.md`](DEVICE_IDENTITY.md).

### GET /api/v1/state → 200

Canonical **physical** piano state from the StateStore (read-only
snapshot; playback NEVER appears in `pressed_notes`):

```json
{
  "pressed_notes": [60, 64],
  "sustain": {"value": 0, "pressed": false},
  "last_input": {"type": "note_on", "channel": 1, "timestamp": 12.5,
                 "note": 64, "value": 90},
  "midi_connection": "CONNECTED",
  "revision": 42
}
```

### GET /api/v1/live-notes → 200 (PIANO-UI-003)

Full authoritative-replacement snapshot for the standard A0–C8 display range.
It composes, without merging authority, physical state copied after StateStore
reduction and automated state copied from actual adapter ownership after every
canonical output attempt. A multi-send STOP that partly fails publishes the
remaining ownership after each successful release; it never republishes an
already released note or claims that the failed STOP completed:

```json
{
  "schema_version": 1,
  "runtime_epoch": "9a78...",
  "sequence": 42,
  "physical": {
    "notes": [60, 64],
    "outside_keyboard_count": 0,
    "sustain": {"value": 100, "pressed": true},
    "midi_connection": "CONNECTED",
    "revision": 17
  },
  "playback": {
    "available": true,
    "notes": [60, 67],
    "voices": [{"channel": 1, "note": 60}, {"channel": 2, "note": 67}],
    "outside_keyboard_count": 0,
    "sustain_channels": [1]
  }
}
```

`runtime_epoch` is process-local and intentionally changes on Core restart;
`sequence` orders publications only inside that epoch. `notes` contains MIDI
21–108; the count fields disclose filtered out-of-keyboard activity without
pretending it can be rendered. Playback `voices` preserves channel ownership,
while `notes` is the deduplicated visual projection. Velocity and
pedal-extended physical sounding-note state are absent because the current
authorities do not retain them.

### GET /api/v1/live-notes/stream → 200 text/event-stream (PIANO-UI-003)

A same-origin semantic SSE channel. Every `snapshot` event carries the complete
payload above and an SSE `id` equal to `sequence`; heartbeats are comments.
Reconnect, reload, a lost event, a sequence gap, or a new runtime epoch is
recovered by replacing the browser replica from the next full snapshot—events
are wake-ups, never authority or an irreplaceable delta log. The packaged UI
also reads `GET /api/v1/live-notes` immediately on start/resume and at a bounded
two-second cadence; this repairs a silently missed SSE update even if heartbeats
continue. A response overtaken by a newer accepted SSE/HTTP snapshot is ignored,
including when the runtime epoch differs.

The channel is bounded to eight concurrent subscribers. Publication is an
in-memory condition notification after canonical processing: MIDI input and
playback scheduler threads never serialize or write network data. Each HTTP
connection has the server's bounded socket timeout; slow/broken clients are
dropped and cannot backpressure musical work. Capacity exhaustion returns
`503 live_note_capacity` before streaming begins.

### GET /api/v1/library → 200 | 503

```json
{"ready": true, "total": 169454, "parsed": 3400, "pending": 166054,
 "invalid": 125, "unsupported": 0}
```

### GET /api/v1/library/songs → 200

Query: `q` (search), `limit` (1..100, default 20), `offset` (≥0;
**unfiltered listing only**), `status` (`pending|parsed|invalid|
unsupported`), `format` (`0|1`). Ordering is deterministic
(`relpath` ascending). Search without offset pagination in P012.

```json
{"query": {"q": null, "limit": 20, "offset": 0, "status": null,
           "format": null},
 "count": 2,
 "songs": [{"song_id": "...", "filename": "a.mid", "relpath": "a.mid",
            "extension": ".mid", "size_bytes": 1121, "status": "parsed",
            "midi_format": 1, "track_count": 1, "ticks_per_beat": 96,
            "duration_seconds": 2.0, "tempo_bpm_first": 120.0,
            "title": null, "indexed_at_utc": "..."}]}
```

Catalog payloads deliberately omit parse `error` text and raw
`mtime_ns`; no absolute host paths are exposed.

### GET /api/v1/library/songs/{song_id} → 200 | 404

One catalog record (same shape) or `404 song_not_found`.

### GET /api/v1/playback → 200

```json
{"available": true, "state": "PLAYING", "tempo_multiplier": 1.0,
 "position_seconds": 12.3, "duration_seconds": 150.6, "events_sent": 900,
 "error": null, "song_id": "...", "title": "..."}
```

`state` is the canonical engine state (`IDLE | PLAYING | PAUSED |
STOPPING | COMPLETED | FAILED`). When playback is unavailable, the
payload reports `"available": false` with `null` fields. Natural
`COMPLETED` and `FAILED` outcomes retain the canonical `song_id` and
safe title until another session starts or an explicit stop clears the
session. This lets reconnecting clients render the terminal result
without creating a second identity store.

The browser treats each payload as an authoritative presentation
anchor. While state is `PLAYING`, it may interpolate the displayed
position using a monotonic client clock and the reported start tempo,
bounded to 3.5 seconds after receipt and clamped to the reported
duration. A stale anchor freezes and disables transport controls until
Core responds again. `PAUSED`, `STOPPING`, `COMPLETED`, `FAILED`, and
`IDLE` never advance locally. This display clock neither schedules nor
emits MIDI; there is no seek endpoint or browser playback engine.

### POST /api/v1/playback/play → 200

Body: `{"song_id": "<uuid>", "tempo_multiplier": 1.0}` (tempo optional,
bounded 0.25..4.0, applied at session start only — there is no
mid-play tempo endpoint in P012). Returns the playback snapshot.

### POST /api/v1/playback/pause | /resume | /stop → 200

Empty body or `{}`. `stop` is idempotent and additionally returns
`"stopped": true|false`. STOP releases ONLY Piano Core-owned
notes/sustain (never PANIC, never a global sweep) and leaves the
physical StateStore untouched.

### GET /api/v1/led → 200 (PIANO-P014)

LED subsystem state (ALWAYS 200 — the LED is auxiliary; an absent
subsystem reports an honest disabled snapshot rather than an error):

```json
{"available": false, "configured": false, "enabled": false,
 "connection": "DISABLED", "mode": null, "brightness": null,
 "active_note_count": 0, "last_error": null, "transport": null,
 "dropped_events": 0, "frames_sent": 0}
```

With the subsystem enabled: `connection` is `LISTENING` (waiting for the
ESP32 keyboard client), `CONNECTED` (client present), or `UNAVAILABLE`
(bind failure; `last_error` carries the reason). `transport` reports the
bound `{"host", "port"}`. `available` is true only when enabled and not
unavailable.

`configured` (PIANO-P014-HF1) distinguishes the four states explicitly:
**configured** (the subsystem exists on this appliance — it can be
enabled/re-enabled through this API even when currently disabled or
unavailable), **enabled** (user turn-on state), **available** (enabled
and transport operational), **connected** (the ESP32 client is present).
Disabling via `PUT` keeps `configured: true` and the re-enable control
offered; only a truly unconfigured appliance reports
`configured: false`.

### PUT /api/v1/led → 200 | 400 | 503

Bounded partial update: `{"enabled": bool, "mode":
"off|playback|physical|all", "brightness": 0..100}` — any combination,
fields optional, everything validated BEFORE anything applies (a bad
request never half-applies). `503 led_unavailable` when the subsystem is
not enabled in configuration (enabling from scratch requires production
configuration via the helper, not this route).

### POST /api/v1/led/clear → 200 | 503

Bounded diagnostic: deterministic all-keys clear (firmware
`all_notes_off`). Empty body or `{}`.

The LED surface is deliberately bounded: NO arbitrary serial commands,
LED indexes, protocol packets, colors, or scenes are exposed. LED
visualization is driven internally by canonical playback events
(P009/P012), physical input (StateStore observed, read-only), and
practice targets (PIANO-P015) — see
`docs/decisions/ADR-LED-KEYBOARD-V1.md` and
`docs/decisions/ADR-PRACTICE-V1.md`.

### GET /api/v1/practice → 200 (PIANO-P015)

Guided practice snapshot (ALWAYS 200 — the practice manager reports
honestly even when unavailable):

```json
{"available": false, "state": null, "song_id": null, "title": null,
 "step_index": null, "total_steps": null, "completed_steps": 0,
 "incorrect_attempts": 0, "progress": 0.0, "target": null,
 "started_at": null, "ended_at": null, "error": null,
 "midi_connection": null}
```

With a session: `state` is `IDLE | WAITING | COMPLETED | FAILED`;
`step_index` is the 0-based current step (only while `WAITING`);
`target` is `{"notes": [60], "names": ["C4"], "chord": false,
"onset_seconds": 1.5}` (chords carry every required note; the onset is
observational source metadata — practice is self-paced); `progress` is
the completed fraction (0..1). `midi_connection` mirrors the canonical
StateStore input state so input degradation is visible. A `COMPLETED`
session keeps its summary observable until the next `start` or `stop`.

### POST /api/v1/practice/start → 200 | 4xx | 5xx

Body: `{"song_id": "<uuid>"}` — never a raw path. Starts a SILENT,
self-paced guided session: the canonical P009 timeline is projected into
play targets, the first target lights on the LED keyboard, and only
physically played correct notes advance the lesson (repeated notes
require a release + fresh press; chords complete when all required notes
are simultaneously held; wrong notes never advance). Guided practice
NEVER sends automated MIDI output.

Deterministic failures: `400 invalid_song_id`/`malformed_request`;
`404 song_not_found`; `410 song_source_missing`; `422 song_unplayable`
(unparseable OR zero keyboard targets in MIDI 21..108 — the message
distinguishes); `503 practice_unavailable` (library not ready);
`409 practice_already_active` (a session is already `WAITING`);
`409 activity_conflict` (automated playback is active appliance-wide,
including a CLI `piano-core-play` session).

### POST /api/v1/practice/stop → 200

Empty body or `{}`. Idempotent; returns the (reset) session payload plus
`"stopped": true|false`. Stop clears the practice LED target display and
releases the appliance activity ownership. It is NOT a P009
STOP/PANIC: practice owns no sounding notes and no MIDI output is ever
touched.

## 6. Error model

Every error is `{"error": {"code": "...", "message": "...", "status":
n}}`. Stable codes:

| Code | HTTP | Raised by |
|---|---|---|
| `malformed_request` | 400 | invalid JSON body / unknown body field / bad framing |
| `invalid_parameter` | 400 | bad query parameter (limit/offset/status/format/unknown) |
| `invalid_song_id` | 400 | missing or non-string `song_id` |
| `invalid_tempo` | 400 | tempo outside 0.25..4.0 or non-numeric |
| `not_found` | 404 | unknown route |
| `song_not_found` | 404 | song_id not in catalog (incl. resolve miss) |
| `song_source_missing` | 410 | indexed but the source file is gone (rescan reconciles) |
| `method_not_allowed` | 405 | wrong method on a known route |
| `payload_too_large` | 413 | body above 64 KiB |
| `unsupported_media_type` | 415 | non-JSON body content type |
| `playback_already_active` | 409 | session active OR appliance lock held (CLI) |
| `practice_already_active` | 409 | a guided practice session is already WAITING |
| `activity_conflict` | 409 | another appliance activity (playback) holds ownership |
| `invalid_state_transition` | 409 | pause/resume outside their required state |
| `song_unplayable` | 422 | source is not a playable SMF OR has zero keyboard targets |
| `library_unavailable` | 503 | library subsystem not ready/failed |
| `playback_unavailable` | 503 | canonical output not open |
| `practice_unavailable` | 503 | guided practice unavailable (library not ready / no subsystem) |
| `invalid_learning_mode` | 400 | mode is not one of the explicitly supported Learning V2 strategies |
| `invalid_learning_configuration` | 400 | mode, section, or repetition options are mutually inconsistent |
| `invalid_section` | 400 | section shape or song-time bounds are invalid |
| `stale_learning_command` | 409 | guarded command does not match current session ID/version |
| `not_ready` | 503 | application lifecycle not READY |
| `internal_error` | 500 | unexpected failure (traceback logged server-side ONLY) |

## 7. Concurrency model

- One thread per connection (`ThreadingHTTPServer`, daemon threads,
  bounded queue); handler threads never touch the application lifecycle.
- The PlaybackManager serializes control operations under one lock:
  concurrent plays resolve deterministically (exactly one 200, the rest
  409); play/stop races end with the engine terminal and the advisory
  lock free; repeated stops are idempotent (exactly one reports
  `stopped: true`).
- Resolve + timeline load happen under the control lock (bounded local
  I/O) so a duplicate play can never slip through.
- The engine keeps its P009 semantics unchanged: bounded cancellable
  waits, release-on-pause, owned-only STOP cleanup.
- Guided practice (PIANO-P015) participates in the SAME appliance-wide
  advisory lock as playback (path/name unchanged; the documented meaning
  is now "appliance activity ownership"): at most ONE of {automated
  playback, guided practice} may be active at a time, across API and
  CLI. Practice holds no thread of its own; input observation rides the
  canonical MIDI input path and is bounded/fast.

## 8. Operations

- Service unit unchanged (`piano-core.service`); production
  configuration lives in `/etc/piano-core/piano-core.env`
  (`api-config` helper subcommand).
- Post-deploy expectations: exactly ONE service-owned RtMidiOut (the
  sanctioned playback surface; opening emits nothing), API listening on
  the configured address only, legacy `pluto-piano-server.service`
  untouched.
- Observability: `api listening host=… port=…` / `api server stopped` /
  `playback session started|ended` journal lines (logger
  `piano_core.api`, `piano_core.playback.manager`).

## 9. Learning Engine V2 (PIANO-UI-004)

Learning V2 is a richer contract over the existing Practice authority. It does
not introduce a second progression path and does not derive correctness in the
HTTP layer.

| Method and route | Request | Result |
|---|---|---|
| `GET /api/v1/learning` | none | Authoritative session snapshot plus explicit capability metadata; inactive is represented explicitly |
| `POST /api/v1/learning/start` | `song_id`; optional `mode`, `section`, `required_repetitions` | Starts one validated Core-owned learning session |
| `POST /api/v1/learning/stop` | optional `expected_session_id`, `expected_version` | Idempotently stops the current learning session; supplied guards must be current |
| `POST /api/v1/learning/restart-target` | required `expected_session_id`, `expected_version` | Resets the current target attempt without advancing it |
| `POST /api/v1/learning/restart-section` | required `expected_session_id`, `expected_version` | Resets progress to the first target in the active plan/section |

Guard validation and mutation are atomic for every guarded Learning command
(U4-F2): a command is evaluated against the session/version truth of the same
critical section that applies it, so a stale command can never reset newer
progress even when physical input advances concurrently. Target identities are
deterministic and collision-free: `target:<canonical-ordinal>:<notes>` for
plan-derived targets (full-song ordinals; section filtering never renumbers).

Supported `mode` values are exactly `WAIT_FOR_NOTE`, `SECTION_PRACTICE`, and
`REPEAT_UNTIL_CORRECT`. A section is exactly
`{"start_seconds": number, "end_seconds": number}` and selects canonical
target onsets in the half-open range `[start_seconds, end_seconds)`. It is
required only for `SECTION_PRACTICE`. `required_repetitions` is 1..16 and may
exceed 1 only for `REPEAT_UNTIL_CORRECT`.

The snapshot includes session identity/version, mode, current target and its
stable content-derived identity, expected and currently accepted notes, latest
Core outcome, target/repetition progress, optional section, timestamps and
input availability. Capability metadata states the deliberate limits:
runtime-only persistence, song-time sections, and no hand assignment, rhythm
grading, or `MISSED` outcome.

Learning commands never accept a filesystem path, expected notes, progress, or
outcome from the client. Restart commands are guarded so a delayed browser
command cannot mutate a replacement session. Practice/playback continue to
share the appliance activity lock, and learning itself emits no MIDI output.

## 10. Score projection (PIANO-UI-005)

The score endpoint exposes ONE canonical note-span projection of a song for the
Visual Learning System. It is derived at the Core boundary from the canonical
P009 timeline (the only MIDI parser); no renderer ever receives raw MIDI or
parses it in the browser.

| Method and route | Request | Result |
|---|---|---|
| `GET /api/v1/score/{song_id}` | none | Canonical read-only note-span projection for visualization |
| | | `200` projection; `400` empty `song_id`; `404` unknown song; `413` `score_too_large` (> 20,000 spans; chunked transfer is a future authorized extension); `422` unplayable source; `503` subsystem/library unavailable |

Response fields: `schema_version` (1), `song_id`, `title`,
`source_version` (`score-sha256:<64 hex>`, deterministic over canonical
field/span order with signed zero normalized to `0.0`; non-finite values are
rejected before hashing), `duration_seconds`, `time_basis` (`song_seconds`),
`note_count`, `notes`
(`index`, `midi` 0..127, `channel` 1..16, `velocity`, `onset_seconds`,
`duration_seconds` — zero duration is legal and preserved), `diagnostics`
(pairing counters: percussion/out-of-keyboard attacks, unpaired offs,
retriggered and unterminated ons), and `notation` — the machine-readable
capability contract listing which notation dimensions are supported
(pitch/onset/duration/channel/velocity) and which are deliberately unsupported
(track, tempo map, time signature, measure, beat, hand, fingering, voice,
phrasing, enharmonic spelling). Renderers must not fabricate the unsupported
dimensions.

Pairing semantics (deterministic): NOTE_ON opens one span per
`(channel, note)`; a same-key re-strike closes the previous span at the new
onset; an unpaired NOTE_OFF is counted and dropped; spans open at song end
close at the duration (they sound until the engine's completion cleanup).
Percussion-channel and out-of-keyboard attacks remain included as real output
events and are counted for honest presentation filtering.

The projection is cached per resolved source path (bounded LRU, 8 songs).
Playback and practice timeline loading warms the cache through an injected
loader wrapper, so an active song's projection is served without a second
parse. The endpoint owns no MIDI surface, schedules nothing, and grades
nothing: it is read-only observability for presentation.

UI-005 remediation clarification: span publication is deterministically sorted
by onset, channel, pitch and original close index, with indices reassigned in
that order. Span 20,001 is refused before allocation. Canonical timeline loads
invalidate the previous same-path score; an in-flight older projection cannot
be published across a new canonical load. No Playback/Practice semantics change.

## 11. Fingering and hand intelligence (PIANO-UI-006)

The fingering endpoint exposes one complete deterministic advisory plan derived
from the exact canonical score projection. It does not parse MIDI, schedule
output, grade Learning input or contain renderer/IK data.

| Method and route | Request | Result |
|---|---|---|
| `GET /api/v1/fingering/{song_id}` | none | Current deterministic plan; generates and caches it on first read |
| | | `200` plan; `400` empty `song_id`; `404` unknown song; `413` score too large; `422` unplayable source or `fingering_unsupported`; `503` score/fingering subsystem unavailable |

Response fields include `schema_version`, explicit `engine_version`,
content-derived `plan_id`, `song_id`,
`source_version`, plan `version` and `generation`, `strategy`, provenance
`DETERMINISTIC_INFERENCE`, `warnings`, excluded-note count, validated config,
capabilities and ordered `targets`. Each target carries `score_index`, stable
`target_id`, MIDI pitch, channel, song-time onset/duration, explicit `hand`,
selected `finger` and deterministic alternative fingers. Finger numbers are
1 thumb, 2 index, 3 middle, 4 ring, 5 little for both hands.
`alternative_count=N` means up to N distinct alternatives excluding the
primary recommendation.

V1 excludes percussion channel 10 and pitches outside MIDI 21..108 with an
explicit count/warning. A hand may receive at most five simultaneous distinct
pitches and defaults to a 12-semitone maximum span. Unsupported simultaneous
material fails explicitly; it is never assigned plausible-looking fake
fingers. Held assignments occupy `[onset, onset + duration)`: release at the
next onset frees the finger, retained pitches constrain hand span,
zero-duration attacks create no continuing occupancy, and duplicate aliases of
one physical key consume one assignment. Authored hand data, manual overrides, personalization, finger-sensitive
grading, pose/IK and 3D rendering are unavailable and stated in capabilities.

Plans are cached in a bounded LRU by song, score source version and config.
The plan identity also binds schema/plan/generation/engine versions, strategy,
provenance and complete canonical targets. Changing any identity dimension
regenerates a new content identity; reload of unchanged truth reconstructs the
same plan. Browser consumers validate exact playable-score coverage and every
target field atomically. They clear advice on failure and revalidate score once
per visible authoritative poll so same-song source replacement is discovered
without animation-frame polling or reload.

`max_hand_span_semitones` remains a configurable software heuristic in the
authorized 5..24 range. It is not a physiological, professional or medical
guarantee; larger configured values may permit ergonomically unrealistic
recommendations.
