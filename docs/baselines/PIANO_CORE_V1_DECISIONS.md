# PIANO CORE V1 DECISIONS RECORD

Concise record of the final architecture decisions and why they were made.
Status of every entry: **ACCEPTED**. Alternatives are not reopened here.

---

## ADR-001 — One logical Piano Core device
- **Decision:** the product is one logical device, `Piano Core`; external systems (HUGO, Home Assistant, dashboards) see a single endpoint with one identity, one state, one API.
- **Evidence/rationale:** locked product decision; eliminates the multi-brain ambiguity found in the old topology (panel WS server :8765 + Pi server :8766 + autonomous EQ); the audited system already converges on the Pi as hub.
- **Consequence:** internal hardware (ESP32 LED driver, display, Roland link) is invisible externally; no peripheral may expose its own provider surface.
- **Status: ACCEPTED**

## ADR-002 — Raspberry Pi is the authoritative controller
- **Decision:** the Pi hosts the single authoritative `piano-core` runtime and canonical state.
- **Evidence/rationale:** deployed, user-verified runtime (`services/pluto-piano-server/`: StateStore, MidiBridge, MidiPlayer, WebSocketServer) plus ESP32-S3 USB-host to Roland physically blocked (`docs/project_status.md`: Roland never enumerated); an MCU cannot host the semantic API scope.
- **Consequence:** no competing brains; all other components are clients or internal peripherals; exact Pi board model stays hardware documentation, not contract.
- **Status: ACCEPTED**

## ADR-003 — Roland is the bidirectional MIDI instrument endpoint
- **Decision:** `Human → Roland → Pi` (keys/pedal) and `Pi → Roland → sound` (automated), via USB MIDI.
- **Evidence/rationale:** both directions verified on hardware (2026-05-16 log: `0582:01b1`, `aconnect 28:0`, `aseqdump` events; playback verified) plus user confirmation.
- **Consequence:** Roland USB/ALSA details never enter the public contract; source labels `human`/`automated` required on all note events.
- **Status: ACCEPTED**

## ADR-004 — ESP32 remains internal LED driver for v1
- **Decision:** retain `Pi → internal link → ESP32 → 196-LED strip` because it is proven; Pi-direct WS2812 is not the v1 architecture.
- **Evidence/rationale:** deployed and user-verified (TASK-109 live; final calibration 2026-05-24); operator handoff doc explicitly forbids Pi GPIO for the strip.
- **Consequence:** supersedes the baseline's earlier "Pi direct-drive preferred" lean; LED failure degrades only visualization.
- **Status: ACCEPTED**

## ADR-005 — Pi↔ESP32 transport is replaceable and not public API
- **Decision:** current WiFi + WebSocket JSON is an internal detail; USB serial or another local transport may replace it with no public API change.
- **Evidence/rationale:** the ESP32 consumer is protocol-thin and reconnect-tolerant; freezing the transport would couple the public contract to implementation.
- **Consequence:** the ESP32 never becomes a HUGO/HA-visible device.
- **Status: ACCEPTED**

## ADR-006 — 88-key/196-LED CSV is canonical physical mapping evidence
- **Decision:** the finalized 88-row calibration export is the canonical mapping record at `hardware/led/keyboard_mapping.csv`; firmware derives from it.
- **Evidence/rationale:** physical calibration finalized 2026-05-24 (magic `PLM10p`, 178 lit LEDs, 18 gaps, reversed orientation); mapping must never live only in firmware source.
- **Consequence:** firmware rebuilds/rewiring re-derive from data; `led_key_map.cpp`/`led_test_config.h` demoted to historical records.
- **Status: ACCEPTED**

## ADR-007 — Old ESP32-S3/LVGL UI discarded as product architecture
- **Decision:** the Waveshare/LVGL stack is LEGACY/REFERENCE ONLY; the future UI is rebuilt as an API client (kiosk/browser/direct display), visual design unconstrained.
- **Evidence/rationale:** locked decision; the proven 7″ touch kiosk already demonstrates the client pattern; panel hardware value is display bring-up knowledge, not architecture.
- **Consequence:** UI redesign can never require a Piano Core API change.
- **Status: ACCEPTED**

## ADR-008 — Canonical API = semantic HTTP commands/queries + WebSocket events
- **Decision:** one canonical API: HTTP for commands/queries, WebSocket for events/state.
- **Evidence/rationale:** curl-debuggable, Home-Assistant-friendly, trivially consumable by HUGO adapters and dashboards; the baseline comparison (A vs WS-only vs MQTT-first) favored A.
- **Consequence:** identity/capability/error surfaces are defined once for all client classes (spec §7–§9).
- **Status: ACCEPTED**

## ADR-009 — Legacy :8766 remains temporary compatibility only
- **Decision:** the deployed WS :8766 JSON protocol survives only behind a compatibility adapter; it never becomes the canonical contract.
- **Evidence/rationale:** ESP32 LED path and existing tools depend on it today; preserving it avoids breaking a working system while the canonical API lands.
- **Consequence:** documented compat deviations (implicit stop-then-play; legacy `stop` full panic) stay confined to the adapter; HUGO never uses legacy PLUTO messages.
- **Status: ACCEPTED**

## ADR-010 — Canonical `playback.stop` is separate from global `all_notes_off`
- **Decision:** `piano.playback.stop` cancels automated playback and releases only automated-tracked notes (no global sweep); `piano.all_notes_off` is the explicit global panic (sustain-off/sweep/CC123/CC120 heritage).
- **Evidence/rationale:** deployed `stop()` currently performs a full panic, which would wrongly clear human-played state under a canonical stop; the correction was locked in the final consistency patch.
- **Consequence:** per-source held-note bookkeeping becomes required internal state; legacy behavior documented in the compat adapter only.
- **Status: ACCEPTED**

## ADR-011 — Home Assistant and HUGO are API clients/adapters, not core logic
- **Decision:** HA integrates via an external MQTT (existing, verified) and/or REST adapter; HUGO integrates via a `DeviceAdapter` on the canonical API; neither's logic lives in Piano Core.
- **Evidence/rationale:** `ha/mqtt.py` already implements discovery/state publishing outside the realtime path; the operator runbook (smart plugs + SSH ritual) is operational, not architectural.
- **Consequence:** core contains zero HA/HUGO-specific code; both see only semantic state/actions; HUGO never automates the dashboard.
- **Status: ACCEPTED**

## ADR-012 — EQ accessory remains separate/out of scope
- **Decision:** `PLUTO EQ ACCESSORY` (and the unbuilt "EQ wall") are outside Piano Core v1.
- **Evidence/rationale:** independent device with its own firmware; roadmap explicitly lists it as Phase 5 (not built); locked product decision 6.
- **Consequence:** no EQ requirements inside Piano Core; it may later consume Piano Core events as an external client.
- **Status: ACCEPTED**

## ADR-013 — One canonical source tree/version defines deployment
- **Decision:** a single canonical repository/source tree must define the deployable Piano Core version; partial-rsync drift (current `sync-to-pi.sh`) is not acceptable.
- **Evidence/rationale:** audit found Pi deployment via partial rsync into a non-git `~/RediON` tree covering only 4 app files — silent drift risk; `PI_DEPLOY_SYNC.md` confirms the Pi tree is not a repo.
- **Consequence:** whole-version deploys, locked dependencies, config/source separation, deployed version observable via API; CI/CD tooling deliberately deferred.
- **Status: ACCEPTED**

---

Supersedes all interim architecture deliberations. Do not reopen these decisions during
implementation; changes require a new revision of `PIANO_CORE_V1_FROZEN_SPEC.md`.
