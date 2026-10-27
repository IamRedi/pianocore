# PIANO CORE V1 SPECIFICATION — FROZEN v1.0

Status: `FROZEN FOR IMPLEMENTATION`
Scope: `Piano Core v1 only`
Supersedes: `PIANO CORE V1 ARCHITECTURE BASELINE`
Implementation authorization: `NOT YET AUTHORIZED`
Revision: final consistency patch (Pi-model wording; STOP/PANIC separation; Roland-disconnect semantics; LED scene public-capability distinction).

Authoritative inputs: Piano Ecosystem Audit; Raspberry Pi Piano Audit (+ user-verified evidence re-evaluation); the deployed implementation under `/home/dev/projects/RediOn/services/pluto-piano-server/` and the PlatformIO evidence tree. All "USER-VERIFIED / WORKING" classifications rest on repository evidence plus user-confirmed prior physical operation; current physical disconnection does not downgrade them.

---

## Header architecture

```text
        HUGO  ·  Home Assistant  ·  Manual / Dashboard UI
                          │
                          ▼  (one canonical semantic API)
              ┌───────────────────────────┐
              │         PIANO CORE        │
              │       Raspberry Pi        │
              │                           │
   Roland ◄──►│  MIDI Bridge              │
   (USB MIDI) │  StateStore               │
              │  Playback Engine          │
              │  MIDI Library             │
              │  Semantic API             │
              │  LED Policy / State       │
              └─────────────┬─────────────┘
                            │  internal replaceable link
                            │  current: WiFi + WebSocket JSON
                            ▼
                    ESP32 LED Driver
                            │
                    196-LED keyboard strip

   7" touchscreen  → Piano Core API client (kiosk)
   EQ accessory    → separate device (out of scope)
```

The Roland link is **bidirectional**: `Human → Roland → Pi` (keys/pedal input) and `Pi → Roland → sound` (automated output).

Exact Raspberry Pi board model is **hardware documentation/verification, not part of the Piano Core public contract** — the architecture claims only "Raspberry Pi"; the model (Pi 4 per old project intent) remains an open §23 verification item.

## 0. Authoritative evidence status (baseline facts)

USER-VERIFIED / WORKING (deployed at `RediOn/services/pluto-piano-server/`, Pi host `pianopi@192.168.1.50`, Roland USB `0582:01b1`, ALSA `28:0`): Pi↔Roland MIDI input; Pi→Roland output/playback; MIDI-file playback (`play_midi`); stop (deployed legacy form — see §11 note); panic/all-notes-off (used in daily `blackout.py` ritual); tempo/transpose/CC7 volume; key/pedal input reception; `StateStore` as source of truth; WebSocket server :8766 deployed; MIDI library list/search (169k archive, ~540 curated); LED keyboard path Pi→ESP32→WS2812 (live); 88-key/196-LED mapping finalized and exported (88-row CSV, 2026-05-24); 7″ 1024×600 touch kiosk deployed; systemd autostart; HA MQTT publishing/session integration; ALSA route-recovery logic (built for an observed re-enumeration failure).

**Not implemented today (no pretending otherwise):** single-note play, note release, chord play, sustain *output* (`sustain` is input-read only), program/instrument selection, canonical HTTP API, REST identity surface, canonical stop/panic separation.

---

## 1. Product definition

Piano Core is **one logical piano device**. Externally — to HUGO, Home Assistant, and any human UI — it is a single instrument endpoint with one identity, one state, and one semantic API.

The **Raspberry Pi hosts the authoritative `piano-core` runtime** and owns the canonical state (Roland connection, held notes, sustain input, playback, library, LED policy). No other component is a competing brain.

The **Roland is the instrument endpoint**, connected bidirectionally via MIDI: human input flows `Human → Roland → Pi`; automated sound flows `Pi → Roland`. Roland USB/ALSA details never appear in the public contract.

The **LED system is an internal peripheral**: Pi LED policy/state → internal replaceable link (today WiFi+WS JSON) → ESP32 LED driver → 196-LED strip. External clients never see an LED device. The one LED-related public surface is the semantic scene capability `piano.led.scene.set` (§12), which expresses Piano Core presentation intent, not hardware.

The **display is an API client** (deployed 7″ kiosk), never the source of truth; its visual design is freely replaceable.

External systems are **semantic API clients only**. Piano Core contains **no HUGO-specific logic**, no dashboard automation hooks, and no per-client control paths.

## 2. Public vs internal architecture boundary

| PUBLIC / STABLE V1 | INTERNAL / REPLACEABLE |
|---|---|
| Device identity (`deviceId`, `deviceType`, role), API/protocol version, software version | ALSA/RtMidi implementation, mido library, port selection heuristics (`'roland'`/`'digital piano'` matching) |
| Semantic capability names (§6) and their parameters/results | Roland ALSA port names, USB VID/PID, `aconnect` route management |
| Canonical state model fields (§4) | Pi OS, **exact Pi board model**, systemd internal arrangement, venv layout |
| Command/query semantics (HTTP) and event semantics (WS) | Pi↔ESP32 LED transport (WiFi+WS JSON today; USB serial or other later) |
| Error vocabulary (§9) | ESP32 firmware internals, FastLED, NVS storage choice |
| Event subscription model; playback/session semantics (§10) | WS2812 timing, GPIO 13, brightness handling, key-map application, physical LED rendering |
| Panic semantic intent: "global MIDI silence/reset of MIDI-controlled output state" (§11) | Exact panic MIDI sequence (CC64/CC120/CC123/sweep), ALSA watchdog mechanics |
| Stop semantic: "cancel automated playback, release automated-tracked notes only" (§5/§10) | Stop's internal release mechanism (targeted note-offs etc.) |
| `piano.led.scene.set` as semantic presentation intent (§12) | LED driver hardware, WS2812 details, key-map application |
| `piano.midi.play` semantics incl. tempo/transpose/volume meaning | MIDI file parsing details, curated-library symlink mechanics |
| LED mapping **data** as a versioned asset (§13) | Library/framework choices (Python vs other), dashboard technology |

Rule: swapping anything in the right column must never require a public contract change.

## 3. Existing runtime components to inherit

From `RediOn/services/pluto-piano-server/` (layout not sacred; classification by future role):

| Component (evidence path) | Current role | Verified | Future disposition |
|---|---|---|---|
| `app/state/store.py` — `StateStore` | single-writer state + JSON snapshot | yes | **Core** — COPY/ADAPT as canonical state model seed; split public snapshot vs internal fields (§4); extend with per-source held-note bookkeeping (§5) |
| `app/midi/bridge.py` — `MidiBridge` | mido input loop, Roland port match, normalization, in-loop ALSA route keep-alive | yes | **Core** — COPY/ADAPT (route logic preserved; port names move to internal config) |
| `app/player/midi_player.py` — `MidiPlayer` | MIDI-file playback, tempo/transpose/CC7 volume, stop (currently full-panic form), panic sweep, non-blocking emit | yes | **Core** — COPY/ADAPT; **stop rewritten to canonical semantics (§5/§11); panic retained as `all_notes_off`; extends to note/chord/sustain-out (§6)** |
| `app/library/midi_library.py` — `MidiLibrary` | scan/list/search, sha1 ids, thread-offloaded rescans | yes | **Core** — COPY/ADAPT (path config externalized) |
| `app/api/websocket_server.py` — `WebSocketServer` | WS :8766, hello/state/library/commands, hardened broadcast | yes | **Compatibility layer** — retained temporarily as legacy surface behind the canonical API (§7); legacy `stop` full-panic behavior documented, not canonical |
| `app/led/router.py` — `LedRouter` | LED scene state + pulse events (deliberately virtual) | yes | **Core LED policy** — COPY/ADAPT as the Pi-side LED policy/state service (§12) |
| `app/ha/mqtt.py` — `HomeAssistantPublisher` | MQTT discovery + state publishing, availability LWT | yes | **HA adapter** — moves out of core into the compatibility/adapter area (§15) |
| `scripts/pluto_alsa_watchdog.py` + `systemd/pluto-alsa-watchdog.service` | standalone ALSA route repair | yes | **Operational helper** — keep subordinate; optional later consolidation into core (bridge already self-checks routes) |
| `systemd/pluto-piano-server.service`, `pluto-piano-kiosk.{service,desktop}` | autostart core + kiosk | yes | **Deployment assets** — COPY/ADAPT into `deploy/`; kiosk is UI-client tooling |
| `scripts/{sync-to-pi.sh, pluto-piano-on/off.sh, pluto-piano-blackout.py, pluto-piano-power-sync.sh, pluto-kiosk.sh, curate_midi_library.py, list_midi_ports.py, pluto_ws_client.py}` | deploy/ops/diagnostics | yes | **Operational tooling** — keep; `sync-to-pi.sh` superseded by §18 policy |
| `tests/test_player_queue.py` | non-blocking emit regression | yes | **Tests** — COPY/ADAPT; pattern for new core tests |
| `app/main.py` | composition root | yes | **Core** — REWRITE CLEANLY around canonical API + compat adapters |

## 4. Canonical state model

### PUBLIC AUTHORITATIVE STATE
`available`; `deviceId`, `deviceType`, `apiVersion`, `swVersion`; `midi {inputAvailable, outputAvailable}` (booleans only); `activeNotes {notes[], count}` — union of human+automated held notes; `sustain` (pedal input state); `playback {mode: idle|playing|error, source: human|automated, kind: midi_file|note|chord, nowPlayingId, nowPlayingTitle}`; `library {available, trackCount}`; `led {health: ok|degraded|unavailable, scene}`; `clients {count}`; `warnings[]`; `uptimeSeconds`.

### INTERNAL STATE
ALSA/mido port names and preferred-port config; route-watchdog state; playback task handles, tempo/transpose/volume session settings; **per-source held-note bookkeeping (automated vs human)**; emit/background task sets; ESP32 link connection state; raw event counters; library root path and scan cache.

### DERIVED/UI-ONLY STATE
LED rendered frame (ESP32-side); kiosk progress visuals (elapsed/duration event-derived); `ledLastNote`/`ledReactivity` pulse decor (legacy snapshot fields — reclassified derived); HA discovery payloads.

Not exposed: port names, exception strings, USB details, ESP32 internals. Runtime state never persisted (§19).

## 5. Human vs automated MIDI model

**Human path:** `Roland → MIDI Bridge → StateStore → LED policy + API events`. Never ownership-gated; flows regardless of automated playback.
**Automated path:** `API command → Playback Engine → Roland + StateStore → LED policy + API events`.

- **Source labels:** every note event carries `source: "human" | "automated"` (deployed evidence: `'roland'` vs `'midi_player'` in `midi_note_event()`; canonical names frozen as `human`/`automated`).
- **Held notes:** one `activeNotes` set in StateStore, both sources write it; **per-source split is required internal bookkeeping** so that stop and panic can be distinguished (§10/§11).
- **`piano.playback.stop` (canonical):** cancels automated playback/scheduling; releases **only** notes generated/tracked by automated playback (internal mechanism free); transitions playback state to `idle`; **must not intentionally issue a global all-notes-off sweep affecting human-played MIDI state**. Human-held notes and human pedal state are untouched.
- **`piano.all_notes_off`:** the explicit global operation (§11) — the only surface that clears MIDI-controlled held-note state wholesale.
- **Loop/echo handling:** implementation concern only — if the Roland is ever observed echoing outbound notes into the input stream, dedup is added inside the bridge (currently unverified, no evidence it occurs).

## 6. Playback capability baseline

### INHERITED V1 (USER-VERIFIED — preserve behavior)
- `piano.midi.play {trackId, tempo, transpose, volume}` — MIDI-file playback with tempo 0.25–2.0, transpose ±24, CC7 volume
- `piano.playback.stop` — **canonical semantics per §5**: cancel automated playback, release automated-tracked notes only, playback→idle *(behavior correction; the deployed full-panic form survives only as legacy compat, §11)*
- `piano.all_notes_off` — global panic (§11)
- `piano.volume.set {volume}` — CC7 to all channels
- `piano.library.list {limit, offset}` / `piano.library.search {query}`
- `piano.state.read` + playback/now-playing state and events
- `piano.led.scene.set {scene, intensity?}` — **public semantic presentation capability** (§12); deployed and used by the kiosk

### NEW V1 (to implement; not claimed existing)
- `piano.note.play {note, velocity, durationMs?}`
- `piano.note.release {note}`
- `piano.chord.play {notes[], velocity, durationMs?}`
- `piano.sustain.set {on}` — sustain *output* (input reading is verified; output is new)

### OPTIONAL / DEFERRED
- pause/resume; sequence API beyond MIDI files; further public LED effect surfaces beyond scenes; multi-library profiles.

### REQUIRES ADDITIONAL VERIFICATION
- `piano.sustain.set` on the Roland (CC64 out) — verify after implementation
- program/instrument selection — no code path, no Roland evidence; deferred until verified
- master-volume semantics beyond CC7 (none evidenced)

## 7. Canonical semantic API v1

**Canonical: HTTP for commands/queries + WebSocket for real-time events/state.**

```text
        Canonical API v1  (HTTP + WS events)
                  │
           Core semantics
                  │
   Legacy compatibility adapters / internal links
   (WS :8766 legacy surface · ESP32 LED internal stream)
```

- **Commands:** `POST` semantic operations (§6 set) with typed results/errors (§9).
- **Queries:** `GET /state`, `GET /identity` (§8).
- **Events (WS):** `note {note, velocity, source}`, `state.changed`, `playback.changed {status, id, title, elapsedSeconds?, durationSeconds?}`, `midi.availability.changed`, `led.health.changed`. Bounded broadcast with slow-client drop (deployed hardening preserved).
- **Legacy compatibility surface:** the existing WS :8766 JSON behavior (`hello`, `get_state`, `library`, `command{play_midi|stop|all_notes_off|set_volume|set_led_scene}`) is retained **temporarily** behind an adapter. Documented legacy deviation: deployed `stop` currently performs a full panic (calls `all_notes_off`, clearing human-played state too) — this is preserved **only as documented legacy compatibility behavior** and must not dictate canonical `piano.playback.stop` semantics. The ESP32 LED path may keep consuming its existing internal message stream unchanged. Compatibility logic must not dictate canonical naming, and **HUGO never depends on legacy PLUTO messages**. Second documented compat difference: legacy `play_midi` implicitly stops current playback; canonical play follows §10 arbitration.

## 8. Identity and capability contract

- `deviceId`: stable string `piano-core-<suffix>`, provisioned once, stable across reboots/reinstalls.
- `deviceType`: `piano_instrument_v1`; `role`: `instrument`.
- `apiVersion`: 1 (canonical); legacy surface advertises its own `protocol: 1` shape unchanged.
- `swVersion`: semver, observable via `GET /identity` and the state snapshot — the deployed-runtime-version observability required by §18.
- `capabilities[]`: exactly the §6 set the build supports.
- Health/state: `GET /state`; event subscription via the canonical WS endpoint.
- No automatic discovery in v1 — configured endpoint is acceptable. Preserved verbatim: **Discovery does not imply authorization.** Authentication/authorization: none in v1 (LAN-trust), matching deployed reality; revisited only if evidence demands.

## 9. Error/result model

Typed public errors: `rolandUnavailable`, `playbackBusy`, `trackNotFound`, `invalidParameter`, `unsupportedCapability`, `libraryUnavailable`, `ledUnavailable` (only where an LED-specific internal operation must surface, e.g. scene application failing), `internalError`.

`rolandUnavailable` additionally covers the disconnect state of §11-R: reported when MIDI input and/or output transport is gone; commands requiring the Roland fail with it while observation/state queries continue.

Legacy-string mapping (compat adapter responsibility): `midi_output_not_found`→`rolandUnavailable`; `track_not_found`→`trackNotFound`; `mido_not_installed`→`internalError`; `unknown_command`→`unsupportedCapability`; `unknown_led_scene`→`invalidParameter`; `invalid_json`/`unknown_message_type`→`invalidParameter` (protocol level). ALSA/USB exception strings are never public error contracts — they may appear in logs/internal `detail` only.

## 10. Client/arbitration model

Robot v1 ownership machinery is **not imported**. Frozen model:

- Multiple clients may connect, observe, and command; commands are serialized centrally by the single core process.
- **One automated playback activity at a time.** A new conflicting automated playback request returns `playbackBusy` (canonical); the legacy surface keeps its implicit stop-then-play behavior as documented compat semantics.
- `piano.playback.stop` and `piano.all_notes_off` are accepted from **any** client. They remain distinct operations (§5/§11): stop is channel-scoped to automated output; panic is global.
- Human playing is never ownership-gated (§5), and canonical stop never disturbs it.
- **Client disconnect does NOT automatically stop playback** — classified as **PLAYBACK / SESSION SEMANTIC** (deployed and intended: the kiosk may close/refresh while music continues), not a physical safety invariant.

## 11. Panic, stop, and disconnect semantics

Three distinct operation classes, frozen:

### `piano.all_notes_off` — global panic/reset
Semantic promise: "silence/reset **all** MIDI-controlled output state." May use sustain-off, note-off sweep, CC123, CC120 or equivalent internal strategy (deployed `_panic_midi_output()` heritage: CC64=0 + 128 note_offs × 16 channels + CC123 + CC120 — verified operational heritage, exact sequence internal). Clears MIDI-controlled held-note state (including state bookkeeping for human-played notes, since the instrument itself is silenced). Callable from any client.

### `piano.playback.stop` — automated-channel cancel
Cancels automated playback/scheduling; releases only automated-tracked notes; playback state → `idle`. **No global sweep.** (Legacy :8766 `stop` full-panic form = documented compat only, §7.)

### Disconnect / shutdown classes — honesty rules
- **Manual panic command:** full sequence as above, on demand.
- **Graceful shutdown:** best-effort panic before closing MIDI (SIGINT/SIGTERM path; the daily `piano-off`/`blackout` ritual demonstrates this flow).
- **Unexpected crash:** **cannot guarantee** any MIDI cleanup at the instant of death — no promise made.
- **Roland/MIDI output disconnect:** the core **cannot perform physical MIDI cleanup after the transport has disappeared**. Required behavior: abort automated scheduling/playback; clear/transition internal automated playback state; attempt MIDI cleanup **only when the output transport remains available**; report `rolandUnavailable`; reconnect normally.
- **Restart / reconnect reset:** runtime state starts clean (held notes never restored, §19). After the Roland output port is (re-)established, a **best-effort startup silent reset** (sustain-off + CC123) is recommended NEW behavior — clearly distinguished from a guaranteed disconnect-time panic, and verified during bring-up before being promised.

## 12. LED architecture

Frozen v1: **Pi Piano Core → internal link → ESP32 LED driver → WS2812 strip** — retained because it is the proven, user-verified path. Pi-direct WS2812 may be investigated later but is not the v1 architecture.

**Public/internal distinction (frozen):**
- **INTERNAL:** the ESP32 LED driver as a component, WS2812 hardware, key-map rendering, brightness/timing, and the Pi↔ESP32 transport (current `WiFi + WebSocket JSON`; future `USB serial` or other local transport — no public API change either way). HUGO, HA, and the dashboard never see an "LED device".
- **PUBLIC:** `piano.led.scene.set {scene, intensity?}` is a legitimate **semantic presentation capability** — it describes Piano Core presentation *intent* (scene/mood), not hardware implementation. HUGO, HA, and UI may use it freely without knowing whether LEDs are MCU-driven, Pi-driven, or absent.

**Piano Core LED Policy/State owns:** desired illuminated notes (from both sources), LED mode/scene policy, clear/panic intent (panic ⇒ clear), LED subsystem health. **ESP32 LED driver owns:** physical rendering, WS2812 timing, key-map application, brightness/output details, local driver health.

Normal piano functionality (notes, playback, state) **must never depend on LED availability** — LED failure degrades only visualization (`led.health`, `ledUnavailable` where an LED-scoped operation must fail).

## 13. Canonical LED mapping asset

Canonical future path: `hardware/led/keyboard_mapping.csv`, seeded from the verified 88-row export (`RediOn/docs/pluto-piano/PLUTO_KEYBOARD_LED_MAPPING.csv`, calibration final 2026-05-24). Required content: 88 rows; MIDI 21–108; physical 196-LED layout; reversed orientation (human LED #1 = firmware index 195); calibrated gap positions (18 gaps); physical LED range per key.

**The repository mapping file is the canonical calibration record.** ESP32 firmware/build may derive its internal structures from it; the physical mapping must never exist only inside firmware source.

## 14. Display/UI boundary

- The 7″ 1024×600 touchscreen is a valid, proven client display; kiosk form (browser/Chromium) is a valid continuing pattern.
- The ESP32-S3/LVGL UI architecture is **LEGACY / REFERENCE ONLY**; its visual design is discarded and does not constrain the future UI.
- Future UI may be browser kiosk, direct Pi display app, or client web app — **visual redesign must never alter the Piano Core API**.
- Minimum functional surfaces only (no style): connection/health; Roland/MIDI availability; held/played notes; playback controls (play/stop/panic); now-playing; library search; LED scene status (via the public scene capability); diagnostics/settings as needed.

## 15. Home Assistant boundary

Existing MQTT publishing (discovery payloads, `pluto/piano/*` topics, availability LWT) is verified heritage — but it is an **adapter**, not the core API. Freeze:

- **Canonical Piano Core API** = HTTP+WS (§7); core state/playback modules contain zero HA-specific logic.
- **HA integration** continues via the **MQTT adapter** for v1 (already working), exposed to the same semantic state/actions — including the `piano.led.scene.set` semantic where useful; a REST-based integration may be added later — both are external adapters, and supporting both is allowed. HA sees only semantic device state/actions: never MIDI packets, GPIO, LED driver internals, Pi process details, or Roland USB details.

## 16. HUGO boundary

Piano Core is a future HUGO `DeviceAdapter` target. HUGO: connects to the **canonical** API; verifies identity/version/capabilities; reads authoritative state/events; issues semantic music/playback commands (including scene presentation via the public LED-scene capability); may stop/panic from any session per §10; never handles raw MIDI; never sees Pi↔ESP32 messages or any LED device; never automates the dashboard UI. Piano Core contains no HUGO-specific reasoning. HUGO must not depend on legacy PLUTO messages.

## 17. Runtime/process model

**Core service:** one authoritative `piano-core` service owning all state (the deployed `pluto-piano-server` pattern: single asyncio process, `Restart=always`). **Operational helpers** (subordinate, no state ownership): ALSA route watchdog (keep; optional later consolidation since the bridge self-checks routes), systemd units, deploy/config tooling. **UI client:** the kiosk launcher (`pluto-piano-kiosk.service`) — a client convenience, never a state owner. State ownership is never split across Linux services.

## 18. Deployment / source-of-truth policy

Problem (evidence): dev tree in RediON repo; Pi deployment via partial rsync (`sync-to-pi.sh` covers 4 app files + scripts); Pi tree is not a git repo — **silent drift is possible and unobserved**.

Frozen rule: **one canonical repository/source tree defines the deployable Piano Core version.** Requirements: reproducible deployment from that tree; explicit version; dependencies locked (deployed `requirements.txt` pinning preserved); configuration separated from source; deployed runtime version observable through the API (§8 `swVersion`); no silent partial-rsync drift — deployment moves whole-version artifacts/manifests. CI/CD tooling choice is deliberately not made now.

## 19. Persistence model

Persist: `deviceId`; configuration (single config file, example committed); canonical LED mapping CSV (in-repo asset); MIDI library path (+ optional scan-index cache); optional local UI preferences (client-side); logs (journald). **Never persisted:** held notes, sustain runtime state, active playback process state, ephemeral client sessions.

## 20. Clean future repository structure (proposal — do not create)

```text
piano-core/
  core/          # state store, lifecycle, event bus (seed: store.py, main.py rewritten)
  midi/          # Roland bridge, normalization, route recovery (seed: bridge.py)
  playback/      # playback engine + MIDI library (seed: midi_player.py, midi_library.py)
  api/           # canonical HTTP + WS (new; patterns from websocket_server.py)
  led/           # LED policy/state (seed: router.py)
  compat/        # legacy :8766 WS adapter + HA MQTT adapter + internal LED link adapter
  firmware/esp32-led/   # ESP32 LED driver firmware (internal peripheral; mapping derived from CSV)
  ui/            # future UI client (placeholder)
  hardware/      # keyboard_mapping.csv (canonical), wiring/PSU notes, Pi model verification
  deploy/        # systemd units, config templates, deploy tooling
  docs/          # CONTRACT.md, RELIABILITY.md (§22), DECISIONS.md, runbooks (adapted)
  tests/         # seed: test_player_queue.py pattern
```

No micro-packages; every directory maps to a §3 component or a frozen boundary.

## 21. Legacy/current extraction map

`CURRENT/OLD PATH → ASSET → FUTURE ROLE → ACTION`

| Current path | Asset | Future role | Action |
|---|---|---|---|
| RediOn `app/main.py` | composition root | `core/` assembly | REWRITE CLEANLY |
| RediOn `app/midi/bridge.py` | Roland bridge + route recovery | `midi/` | COPY/ADAPT |
| RediOn `app/player/midi_player.py` | playback engine + panic heritage | `playback/` | COPY/ADAPT + REWRITE stop to canonical (§5/§11); extend note/chord/sustain-out |
| RediOn `app/state/store.py` | state store | `core/` | COPY/ADAPT (public/internal split + per-source notes) |
| RediOn `app/api/websocket_server.py` | legacy WS :8766 (incl. legacy full-panic stop) | `compat/` legacy adapter | COMPATIBILITY LAYER |
| RediOn `app/library/midi_library.py` | library scan/list/search | `playback/` | COPY/ADAPT |
| RediOn `app/led/router.py` | LED policy/state | `led/` | COPY/ADAPT |
| RediOn `app/ha/mqtt.py` | HA MQTT publishing | `compat/` HA adapter | COMPATIBILITY LAYER |
| RediOn `systemd/*` | service definitions | `deploy/` | COPY/ADAPT |
| RediOn `scripts/sync-to-pi.sh` | partial rsync deploy | superseded by §18 | ARCHIVE (then DISCARD once whole-version deploy exists) |
| RediOn `scripts/pluto-piano-{on,off}.sh`, `blackout.py`, `power-sync.sh`, `pluto-kiosk.sh`, `curate_midi_library.py`, `list_midi_ports.py`, `pluto_ws_client.py`, `pluto_alsa_watchdog.py` | ops tooling | `deploy/`/ops | COPY/ADAPT (watchdog optional consolidation) |
| RediOn `tests/test_player_queue.py` | non-blocking emit test | `tests/` | COPY/ADAPT |
| PlatformIO `led_test_config.h` + `led_key_map.cpp` | mapping constants/algorithms | superseded by CSV | PRESERVE DATA (CSV canonical) + firmware derives |
| PlatformIO `src/led_test/{main.cpp, pluto_pi_link.cpp}` + `pluto_midi_queue.h` | ESP32 LED firmware | `firmware/esp32-led/` | COPY/ADAPT |
| RediOn `docs/pluto-piano/PLUTO_KEYBOARD_LED_MAPPING.csv` | final calibration export | `hardware/led/keyboard_mapping.csv` | PRESERVE DATA |
| RediOn `docs/pluto-piano/*.md`, `docs/runbooks/PLUTO_*.md` | verification logs, runbooks | `docs/` (adapted) | REFERENCE ONLY / COPY/ADAPT |
| PlatformIO ESP32-S3 panel + LVGL UI (`src/display`, `src/ui`, `src/input`) | display stack | — | ARCHIVE (LEGACY/REFERENCE ONLY) |
| PlatformIO `PianoController/PianoTransport` | mocked transport commands | superseded by real playback | ARCHIVE |
| PlatformIO `MidiEngine.*` | USB-host blocker history | `docs/DECISIONS.md` lesson | REFERENCE ONLY |
| PlatformIO `PLUTO EQ ACCESSORY/` | separate accessory | out of scope | ARCHIVE |
| PlatformIO `tools/pluto_ws_server.py` | obsolete mock | — | DISCARD |

## 22. Reliability semantics

### MUST NOT REGRESS
1. Single source of truth (one core service; no second writer).
2. `piano.playback.stop` always available while the core runs — and always scoped to automated output (never a disguised global sweep).
3. `piano.all_notes_off` (global panic) always callable from any client.
4. Stop and panic remain distinct operations (§5/§11); canonical stop never clears human-played MIDI state.
5. Human MIDI input never ownership-blocked.
6. Client disconnect does not implicitly stop playback (session semantic, preserved deliberately).
7. Held-note runtime state never restored from persistence.
8. **Roland/MIDI output disconnect:** automated scheduling aborted, internal automated playback state cleared, MIDI cleanup attempted **only if the output transport remains available**, state reports `rolandUnavailable`, normal reconnection; no guaranteed disconnect-time physical cleanup is claimed (§11).
9. LED failure does not break piano functionality (degradation only); scene capability fails with `ledUnavailable` without affecting notes/playback.

### OPERATIONAL RELIABILITY
ALSA re-enumeration recovery (in-bridge route keep-alive + watchdog helper); service restart cleanliness incl. best-effort startup reset (§11); structured logs; API availability; library scanning off the real-time path (thread-offload preserved); bounded event broadcasting with slow-client drop (deployed hardening preserved).

### HARDWARE VERIFICATION
Roland behavior set (§23), LED electrical system, display, network. No Robot-style ESTOP terminology anywhere.

## 23. Physical/operational verification gates

### ALREADY VERIFIED / USER-VERIFIED
Every item in §0 (Roland I/O, playback, legacy stop, panic, tempo/transpose/volume, library, WS :8766, LED path, mapping, kiosk, systemd, HA MQTT, ALSA recovery). No re-proving required for architecture freeze. Additionally still open from evidence: **exact Raspberry Pi board model** (claimed only as "Raspberry Pi" at architecture level; confirm model at hardware documentation time).

### NEW V1 FEATURES TO VERIFY
Semantic single-note play; note release; chord play; sustain output (CC64 out) if implemented; **canonical stop semantics (automated-only release, no global sweep)**; **Roland-disconnect abort/`rolandUnavailable` behavior**; **best-effort startup reset** (§11) if adopted; canonical API layer (HTTP+WS alongside legacy); any changed Pi↔ESP32 transport; redesigned UI.

### PRODUCTION RE-VALIDATION
After hardware reconnection: one full smoke pass — playback, canonical stop, panic, LED live path incl. scene capability, kiosk, HA state — plus deployment-manifest correctness (§18) and version observability.

## 24. Frozen non-goals

ESP32-S3 as central brain; old LVGL UI; multiple HUGO providers for internal piano peripherals; EQ inside Piano Core; raw MIDI public API; MIDI hardware details exposed publicly; Pi-GPIO LED direct-drive as a v1 requirement; automatic discovery; cloud dependency; fleet orchestration; AI reasoning inside Piano Core; database-heavy architecture; dashboard as control contract; robot code; unrelated RediON services.

## 25. Implementation baseline checklist

**Core runtime**
- [ ] Single `piano-core` service owns all state; helpers stateless — PASS/FAIL
- [ ] State model == §4; public/internal/derived separation enforced; per-source held-note bookkeeping present — PASS/FAIL
- [ ] Runtime state never persisted; restart yields clean state — PASS/FAIL
- [ ] Deployed runtime version observable via API — PASS/FAIL *(NEW)*

**MIDI/playback**
- [ ] Roland input path incl. route-recovery preserved — PASS/FAIL *(INHERITED-PRESERVE)*
- [ ] MIDI-file playback: tempo/transpose/CC7 volume behavior preserved — PASS/FAIL *(INHERITED-PRESERVE)*
- [ ] **Canonical `piano.playback.stop`: cancels automated playback, releases automated-tracked notes only, playback→idle, no global sweep, human state untouched** — PASS/FAIL *(NEW semantics; legacy full-panic stop confined to compat surface)*
- [ ] **`piano.all_notes_off`: global panic per §11 intent; distinct from stop** — PASS/FAIL *(INHERITED-PRESERVE, renamed canonical)*
- [ ] **Roland disconnect: abort scheduling, clear automated state, cleanup only if transport available, report `rolandUnavailable`, reconnect; best-effort startup reset distinguished from guaranteed cleanup** — PASS/FAIL *(NEW)*
- [ ] `piano.note.play` / `piano.note.release` / `piano.chord.play` implemented + verified — PASS/FAIL *(NEW)*
- [ ] `piano.sustain.set` implemented + Roland-verified — PASS/FAIL *(NEW + VERIFY)*
- [ ] Source labels `human`/`automated` on all note events — PASS/FAIL *(INHERITED, renamed canonical)*
- [ ] Legacy implicit stop-then-play and legacy full-panic stop confined to compat surface; canonical returns `playbackBusy`/scoped stop — PASS/FAIL *(COMPAT)*

**API**
- [ ] Canonical HTTP commands/queries + WS events per §7; identity per §8 — PASS/FAIL *(NEW)*
- [ ] Typed error vocabulary == §9 incl. `rolandUnavailable` disconnect usage; no ALSA/USB strings in public errors — PASS/FAIL *(NEW)*
- [ ] Legacy :8766 adapter preserves deployed behavior with documented deviations; HUGO uses canonical only — PASS/FAIL *(COMPAT)*
- [ ] Bounded broadcast / slow-client drop preserved — PASS/FAIL *(INHERITED-PRESERVE)*

**LED subsystem**
- [ ] Policy/driver split per §12; transport replaceable, no public dependency — PASS/FAIL
- [ ] **`piano.led.scene.set` exposed as public semantic presentation capability; driver/WS2812/key-map/transport fully internal** — PASS/FAIL
- [ ] `hardware/led/keyboard_mapping.csv` is the canonical 88-row mapping; firmware derives from it — PASS/FAIL
- [ ] Panic/clear intent clears LEDs; LED failure/`ledUnavailable` never affects notes/playback — PASS/FAIL

**UI**
- [ ] UI is API client only; no state ownership; redesign needs no API change — PASS/FAIL
- [ ] Minimum functional surfaces present (§14); old LVGL design absent — PASS/FAIL *(DISCARD-LEGACY: LVGL UI)*

**HA/HUGO boundaries**
- [ ] HA via external MQTT/REST adapter on semantic state only (incl. scene capability); zero HA logic in core — PASS/FAIL
- [ ] HUGO boundary == §16; no HUGO-specific code in core — PASS/FAIL

**Deployment**
- [ ] One canonical source tree defines deployable version; whole-version deploys; no partial rsync — PASS/FAIL *(NEW; supersedes `sync-to-pi.sh`)*
- [ ] Dependencies pinned; config separated; version observable — PASS/FAIL

**Docs/tests**
- [ ] CONTRACT.md (canonical + legacy compat incl. stop/panic deviation), RELIABILITY.md (§22), DECISIONS.md, adapted runbooks — PASS/FAIL
- [ ] Non-blocking emit regression test + canonical API tests + stop/panic distinction tests — PASS/FAIL

**Hardware verification**
- [ ] Exact Pi board model recorded as hardware documentation — PASS/FAIL *(VERIFY)*
- [ ] §23 NEW-V1 items each verified on the reconnected rig — PASS/FAIL
- [ ] Production re-validation smoke pass executed — PASS/FAIL

## 26. Consistency notes

Downstream review after the four corrections:

1. **Pi model wording** — header diagram and all architecture-level claims now say "Raspberry Pi"; exact model demoted to §2 internal column, §23 verification item, and `hardware/` docs; no public-contract dependency. ✔
2. **STOP vs PANIC separation** — §5 defines scoped stop; §6 splits the two inherited capabilities with corrected descriptions; §7 documents the legacy full-panic `stop` as a compat deviation; §10 keeps both any-client-callable but distinct; §11 restructured into three operation classes (panic / stop / disconnect-shutdown) with the deployed `_panic_midi_output()` heritage attached to panic only; §22 invariants 2–4 encode the distinction; §25 checklist lines updated; extraction map §21 marks `midi_player.py` stop for rewrite. No remaining wording implies canonical stop performs a global sweep. ✔
3. **Roland disconnect semantics** — §11-R and §22 item 8 now state the honest rule: abort + internal state clear + cleanup only if transport available + `rolandUnavailable` + normal reconnect; §9 defines the error's disconnect usage; §23 lists disconnect behavior and best-effort startup reset as NEW-V1 verification; crash-time guarantee language remains explicitly absent. ✔
4. **LED scene capability** — §1, §2 (public column row), §6, §12, §14, §15, §16, §22 item 9, and §25 consistently present `piano.led.scene.set` as a public semantic presentation capability while driver/WS2812/key-map/transport stay internal, with the never-depends-on-LED rule restated. ✔
5. **Unchanged-by-design (verified untouched):** one logical device; single source of truth; Pi↔Roland bidirectionality; human/automated coexistence; client/arbitration model incl. disconnect-does-not-stop-playback session semantic; canonical-vs-legacy layering; LED mapping CSV canonicality; UI client role; HA/HUGO boundaries; deployment source-of-truth rule; inherited/new/verify feature separation. All previously frozen sections retain their meaning except where the four corrections necessarily touch them, as itemized above. No unresolved alternatives remain.

`SPECIFICATION CONSISTENCY CHECK: PASS`

NO IMPLEMENTATION AUTHORIZED BY THIS DOCUMENT
