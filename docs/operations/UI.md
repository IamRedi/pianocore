# Piano Core Web UI (PIANO-P013)

> **Status:** Canonical operations contract for the packaged browser UI.
> **Architecture decision:** [`docs/decisions/ADR-WEB-UI-V1.md`](../decisions/ADR-WEB-UI-V1.md)
> **Owner:** Redion Cekrezi

## 1. Purpose and authority boundaries

The UI is a **first-party static frontend served by the same HTTP
server as the `/api/v1` control API**. The browser is a client of the
P012 API only:

| Concern | Authority |
|---|---|
| All data and commands | `/api/v1` (P012 contract, unchanged) |
| Canonical playback state | `PlaybackManager` / Playback Engine (P009/P012) |
| Physical piano state | `StateStore` (P006) — read-only for the UI |
| Catalog / search | `LibraryService` (P010/P011) |
| Learning targets, outcomes and progression | `PracticeManager` / `PracticeEngine` through `/api/v1/learning` |
| UI assets | `piano_core.web` package (allowlisted, in-wheel) |

The UI owns no business logic. It holds replaceable presentation replicas only:
after every command it reconciles with the API's returned snapshot, and
control-button enablement is derived exclusively from canonical playback state.

## 2. Serving contract

| Route | Behavior |
|---|---|
| `GET /` | UI document (`index.html`) |
| `GET /ui`, `/ui/`, `/ui/index.html` | UI document |
| `GET /ui/app.js`, `/ui/styles.css`, `/ui/favicon.svg`, `/ui/manifest.webmanifest` | Allowlisted asset, explicit content type |
| any other non-`/api` path | deterministic JSON `404 not_found` |
| non-GET on a UI path | JSON `405 method_not_allowed` |

- Assets are resolved through an ALLOWLIST (`piano_core.web.UI_ASSETS`)
  via `importlib.resources` inside the installed wheel. There is no
  filesystem-path serving; traversal/percent-encoded escapes never
  resolve. Tests prove this on real sockets.
- Every UI response carries `Cache-Control: no-cache` (early appliance
  phase: correctness over caching; a deployment can never strand a
  browser on stale JS/CSS).
- `/api/v1/...` routing and the error model are unchanged (see
  `API.md`).

## 3. Configuration

| Env variable | Key | Default | Meaning |
|---|---|---|---|
| `PIANO_CORE_UI_ENABLED` | `ui_enabled` | `true` | Serve the packaged UI on the API bind |

The gate only controls static asset responses; it adds no listener and
no network surface. `false` restores the bare P012 API-only surface.

## 4. Browser data flow (documented cadence)

- **Polling:** `/api/v1/health` + `/api/v1/state` + `/api/v1/learning`
  every **1 s** while the tab is visible (`/api/v1/led` best-effort in
  the same poll); overlap-guarded; a failed poll shows an
  "unreachable" state and keeps the last rendered data (no retry
  storm; timers are bounded by the page lifecycle).
- **Live keyboard (PIANO-UI-003):** one same-origin `EventSource` consumes
  complete `/api/v1/live-notes/stream` snapshots. A low-frequency authoritative
  `GET /api/v1/live-notes` runs immediately on start/resume and then every
  **2 s** after the prior request settles, so a silently missed SSE release
  converges even while the connection and comment heartbeats remain open. A
  separate **5 s** freshness deadline clears all authoritative-looking
  highlights if neither delivery path succeeds. Every new connection is a
  client generation; older callbacks, lower same-epoch sequences, and delayed
  HTTP responses overtaken by a newer accepted snapshot are rejected, including
  across runtime epochs. Stream loss or a hidden tab also clears highlights;
  reconnect/page resume immediately requests a full snapshot. The browser
  parses no MIDI and runs no scheduler.
- **Search:** debounced **300 ms** after typing; bounded `limit=50`;
  stale responses (monotonic token) are discarded; empty query = the
  deterministic unfiltered listing with offset "Load more" paging
  (offset pagination is API-supported for unfiltered listing only).
- **Commands:** play (song_id + tempo_multiplier, clamped 0.25..4.0 in
  the client), pause/resume/stop — each reconciled with the API's
  returned snapshot; enablement strictly per canonical state
  (IDLE/COMPLETED/FAILED → Play; PLAYING → Pause+Stop; PAUSED →
  Resume+Stop; else disabled).
- **Guided practice (PIANO-P015):** a distinct **Practice** action on
  the selected-song panel starts a session (`POST
  /api/v1/practice/start`); the Practice panel shows the live state,
  `Step x / y`, the expected note names, progress, and Stop Practice.
  While a session is WAITING, Play is disabled (mutual exclusion), and
  Practice start is disabled while playback is active. LED-unavailable
  and MIDI-input-degraded states surface as honest notes in the panel.
- **Learning presentation authority (U4-F3):** the Learning panel, expected
  key overlay and Learning controls are driven by a dedicated
  `LearningCoordinator` lifecycle, isolated from Player/Core/LiveNote
  freshness. Every Learning GET carries a request ticket; every mutating
  Learning command (start/stop/restart-target/restart-section) is an
  ordering boundary, so a poll that started before an accepted command can
  never overwrite the command result (a stopped session cannot resurrect).
  Valid snapshots refresh a bounded 5 s freshness window (about five 1 s
  polls); beyond it Learning presentation de-authorizes — explicit
  "Learning state is stale" status, no target or expected overlay, and
  disabled Learning mutating controls — while Player, Core health and the
  live keyboard stay independent. One valid snapshot restores the current
  session/target authority.
- **Learning V2 (PIANO-UI-004):** the same panel now starts only the three
  Core-advertised modes (`WAIT_FOR_NOTE`, time-based `SECTION_PRACTICE`, and
  bounded `REPEAT_UNTIL_CORRECT`). It renders the authoritative target ID,
  expected/accepted notes, latest outcome, target progress, section bounds,
  repetition state and input availability. Restart-target, restart-section and
  stop commands carry the last observed session ID/version; the server rejects
  stale mutations. A reload reconstructs the active runtime session from the
  next Learning snapshot; the browser stores no authoritative progress.
- Errors surface as human-readable messages keyed by the stable P012
  error codes; no tracebacks or internals are ever shown.

## 5. Live 88-key renderer (PIANO-UI-003)

- One immutable frontend model spans exactly MIDI 21–108 (A0–C8): stable MIDI
  identity, pitch class, octave, white/black classification and geometry.
- The renderer creates 52 white and 36 black DOM elements once. Snapshot updates
  touch only keys whose derived source changed; no per-frame full-keyboard
  rerender, Canvas readback, GPU dependency or layout-driven musical clock exists.
- `physical`, `playback`, and `both` use distinct color plus visible `P`, `▶`,
  and `P+▶` markers; the legend and live summary carry the same semantics.
- The keyboard is read-only (`role=img`), adds no key tab stops, and announces a
  compact active-count/connection summary rather than 88 noisy controls.
- Sustain is shown as pedal state only. Physical keys remain key-down truth;
  pedal-held sounding voices are not invented.
- Learning expected notes may add an outline layer sourced only from the
  authoritative Learning snapshot. It does not replace the physical/playback
  source marker and cannot advance or grade the session.

## 5A. Learning view limits (PIANO-UI-004)

- The browser does not infer hands, bars, beats, fingering, rhythm accuracy, or
  a `MISSED` result. Corresponding controls are intentionally absent.
- Sections are canonical song-time ranges in seconds, not claimed notation
  measures. The section fields are shown only for `SECTION_PRACTICE`;
  repetitions are configurable only for `REPEAT_UNTIL_CORRECT`.
- Learning snapshots use the existing bounded one-second polling path rather
  than widening the UI-003 live-note stream into a domain event bus.
- An input disconnect clears authoritative-looking accepted-note feedback while
  leaving the expected target visible and reporting input unavailable. Core,
  not the page, controls reconnect re-arming and subsequent progression.

## 6. Security model (unchanged from P012)

- Trusted LAN, no authentication, NOT Internet-safe. The UI does not
  change the exposure: same bind, same port, same absence of auth.
- No CDN/external fonts/scripts; no eval; no dynamic script injection;
  no hardcoded hostnames (all requests are same-origin relative
  `/api/v1/...`).
- Song filenames/titles and error strings are UNTRUSTED display
  strings: rendered via `textContent`/safe DOM construction only
  (enforced by `tests/test_web_contract.py`); search queries are
  URL-encoded; hostile filenames therefore render as inert text.

## 7. Operations

- Service unit unchanged; the UI ships inside the wheel
  (`piano_core/web/static/`), so deployment is the standard wheel
  deploy — no asset sync step exists.
- Post-deploy expectations: `GET /` returns the UI document on the API
  bind; `/api/v1/health` unchanged; exactly the same listener set as
  before (legacy 8766, SSH 22, rpcbind 111, API 192.168.1.50:8788).
- Known behavior (P012 semantics, not a UI defect): library search
  matches filenames/relpaths, not enriched titles.

## 8. 3D hands pane (PIANO-UI-007)

- A "3D hands" card renders a minimal scene (camera, lighting, the 88-key
  keyboard, two hands) driven ONLY by the validated UI-006 fingering plan
  replica and the same authoritative presentation clock as the UI-005
  views. The MotionEngine (`handmotion.js`) and the analytical IK are
  pure, deterministic and Node-tested; the renderer (`handrender.js`)
  consumes MotionState only.
- Authority is unchanged: hands never parse MIDI, never select or change
  fingering, never grade Learning, never move musical time. A plan
  conflict (same hand+finger overlapping different keys) hides THAT hand
  (`plan_occupancy_violation`, honest "Plan conflict" status) while the
  other hand, playback, Learning and the 2D views continue.
- Renderer dependency: Three.js r186 vendored in-wheel under
  `/ui/vendor/` (pinned exact files, recorded SHA-256, MIT license text
  shipped; see ADR-018). Loaded lazily through an import map; no CDN.
  Backend ladder: WebGPU when genuinely available → WebGL2 →
  COMPATIBILITY. On failure the pane degrades honestly ("3D unavailable
  on this browser") and the rest of the UI is unaffected.
- Controls: FULL/FOLLOW camera, quality AUTO/PREMIUM/BALANCED/BATTERY/
  COMPATIBILITY, and an enable toggle. All are presentation-only local
  preferences; quality changes pixels/shadows only, never musical truth.
- Recovery: reload, song change, same-song source replacement, stale
  plan, renderer init failure, asset failure and WebGL context loss all
  flow through one identity gate (old pose never survives an identity
  change) and one recovery path; an old pose is never replayed.
- Tier-A ships the Fal-based `/ui/assets/piano-hand-tier-a.glb` in the wheel.
  `handasset.js` maps the Piano Core-owned left/right armatures into the same
  renderer contract. The GLB is presentation data only and never decides
  fingering. If it is absent, malformed, non-finite or fails during recovery,
  both sides return to the permanent procedural Tier-R rigs and the renderer
  stays usable. The rejected CGTrader evaluation asset is not a runtime or
  packaging dependency and no CGTrader binary data belongs in deployment.
- The current Fal material carries one embedded 2048x2048 base-color texture.
  A future material/4K asset may replace it without changing MotionEngine,
  HandRenderer, fingering or the rig contract. Exact Fal output-rights terms
  were not recovered locally; operational publication/deployment must respect
  the separately recorded terms-review item and requires its own authority.
