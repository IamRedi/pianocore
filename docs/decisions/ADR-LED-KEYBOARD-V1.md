# ADR-LED-KEYBOARD-V1: ESP32 Keyboard LED Integration (PIANO-P014)

**Status:** Accepted for implementation (PIANO-P014)
**Date:** 2026-09-13
**Owner:** Redion Cekrezi

## Context

The existing, Owner-tested keyboard LED system is:

```
ESP32-D DevKit (WiFi STA, firmware "esp32_led_test", project
"PLUTO PIANO BRAIN", ~/Documents/PlatformIO — OUTSIDE the RediOn monorepo)
  --WebSocket client--> ws://192.168.1.50:8766/  (legacy pluto-piano-server)
  --FastLED/GPIO13-->  196 x WS2812 across 88 keys (calibrated mapping)
```

There is NO serial transport: the ESP32 is a WebSocket CLIENT that dials
the legacy service, reconnecting every 8 s (no heartbeat). Discovery of
the real system (read-only) established:

- **Protocol accepted by the deployed firmware** (`src/led_test/pluto_pi_link.cpp`):
  - `{"type":"midi","event":"note_on","note":N,"velocity":V}` — V>0 lights
    key N; V==0 or `"note_off"` clears it (0..127 note, else ignored);
  - `{"type":"playback","status":"all_notes_off"|"idle"|"finished"}` —
    clears ALL keys;
  - on connect the ESP32 sends `{"type":"get_state"}` (response ignored
    unless it is `midi`/`playback`); nothing else is parsed.
- **Mapping:** 196 physical LEDs, explicit calibrated `keyStarts` table
  (`src/led_test/led_key_map.cpp::computeFromStripEnd()`, magic `PLM10p`):
  key 0 (A0, MIDI 21) = 4 LEDs at firmware indices 192..195 (counted from
  the physical strip END), keys 1..87 = 2 LEDs each, 18 gap LEDs belong to
  no key. Full 88-key table calibrated 2026-05-24 and documented in
  `RediOn/docs/pluto-piano/PLUTO_KEYBOARD_LED_MAPPING.{md,csv}`.
- **Fixed firmware behavior:** colors are decided IN FIRMWARE (white keys
  warm `CRGB(120,98,62)`, black keys blue `CRGB(24,50,95)`, scaled by
  velocity `map(20..100 → 80..170)`); global `FastLED` brightness is a
  compiled constant (28); frame rendering is firmware-throttled (40 ms).
  There is NO remote brightness, color, or scene control in the deployed
  firmware (the old monorepo skeleton `hardware/pluto-piano-keyboard-led/`
  had scenes but is explicitly obsolete and NOT the deployed source).
- **Ownership today:** legacy `pluto-piano-server.service` (active since
  2026-09-06, untouched) listens on `0.0.0.0:8766`. At discovery time NO
  ESP32 client was connected (the LED system is dormant/powered-off) and
  no serial device exists on the Pi or the mini PC.

## Decision

### D1 — Transport: Piano Core hosts the WebSocket endpoint (stdlib RFC6455)

The ESP32 firmware is the client; Piano Core becomes the WS **server** it
dials. A minimal RFC 6455 server (handshake, masked-frame decode,
text-frame send, close) is implemented in `piano_core/led/transport.py`
using ONLY the standard library (`socket`, `hashlib`, `base64`, `struct`)
— ZERO new runtime dependencies, preserving the offline deployment model.

The endpoint binds per configuration (`PIANO_CORE_LED_WS_HOST/WS_PORT`,
safe defaults loopback:8787, disabled by default). It accepts EXACTLY ONE
client (a second concurrent connection is refused): the keyboard strip is
one physical device with one writer. The client speaks the bounded
protocol above; anything else is ignored or disconnects the client. No
arbitrary serial/index/packet surface exists anywhere in the stack.

### D2 — One-time ownership migration: repoint the ESP32 (config-only reflash)

Per-key LED control requires the ESP32 to be connected to a server Piano
Core controls. The legacy service owns 8766 and must not be stopped,
moved, or proxied (no DNAT/socket tricks — one explicit writer, no silent
fighting). The existing PROTOCOL is sufficient; what cannot work as-is is
the connection TARGET (legacy:8766).

The minimal backward-compatible change is a CONFIG-ONLY firmware rebuild:
`-D PLUTO_WS_PORT=8787` in the `esp32_led_test` build environment (same
protocol, same logic, same host). It still requires a physical flash, so
per the P014 ESP32 FIRMWARE RULE this is recorded here and executed ONLY
after explicit Owner authorization. Until flashed, Piano Core runs
DEGRADED: the LED subsystem reports `connection=LISTENING` (no client),
playback/UI are unaffected, and no double-writer situation can arise (the
dormant/legacy-pointed ESP32 is legacy's client or nobody's).

### D3 — LED is visual output only; bounded modes with explicit blending

`piano_core/led` NEVER sends MIDI and is never authoritative for MIDI
state, playback state, physical key state, timing, or song identity.

Modes (bounded enum, `PIANO_CORE_LED_MODE`):

- `off` — cleared, no visualization;
- `playback` — only canonical P009 playback events (service-side);
- `physical` — only physically pressed keys (StateStore observed;
  replaces the legacy input-visualization behavior the ESP32 loses in
  the D2 migration);
- `all` — union (a key is lit while EITHER source holds it; each source
  releases only its own holds; playback end never clears physically
  held keys).

Default production mode: `all` (preserves the familiar physical-press
lighting AND adds playback visualization).

Brightness (`PIANO_CORE_LED_BRIGHTNESS`, 0..100 %) scales the velocity
Piano Core reports (0 suppresses). It is honest software scaling only —
the firmware's global brightness (28) is NOT remotely controllable.

GUIDANCE/upcoming-note mode is DEFERRED (not implemented in P014): the
deployed firmware has no color/source distinction, and a second visual
class would be ambiguous. Revisit only with an authorized firmware change.

### D4 — Playback integration: observer, never a second scheduler

`PlaybackEngine` gains an optional `event_observer` invoked (best-effort,
exception-swallowed, after the canonical adapter apply SUCCEEDED) for
each scheduled timeline command; `PlaybackManager` gains an optional
`on_session_end` invoked when the engine reaches a terminal state. The
LED integration receives NOTE_ON/NOTE_OFF through a bounded queue into a
dedicated worker thread (drop-oldest on overflow, counted) — the engine
hot path never blocks on LED transport and LED failure can never fail,
stall, or deschedule playback. SUSTAIN/STOP/PANIC commands are ignored by
the LED observer; pause freezes the current visuals (defined behavior);
stop/completion/failure/cancellation clear the playback-held set
(physically held keys unaffected in `all` mode). CLI playback
(`piano-core-play`) runs in a separate process and does NOT drive LEDs
(known, recorded limitation; the appliance-wide advisory lock prevents
concurrent playback anyway).

### D5 — Degraded is normal

Connection states: `LISTENING` (no client), `CONNECTED` (ESP32 present),
`DISABLED`, `UNAVAILABLE` (bind failed). Reconnection is entirely the
ESP32's policy (8 s retry, firmware-owned); Piano Core just keeps the
socket open. A dead/absent ESP32 degrades LED visualization only: READY
never depends on LED availability, and the API/UI report the truth.

### D6 — Clear semantics

Deterministic clear (`all_notes_off` frame) on: LED disable, playback
stop/completion/failure/cancellation (playback-held set only), shutdown
(best-effort before transport close), and the bounded API diagnostic
route `POST /api/v1/led/clear`. `PIANO_CORE_LED_MODE=off` transitions
clear as well.

## Consequences

- Zero new runtime dependencies; one additional bounded listener on the
  LAN address in production (explicit config, same trusted-LAN/no-auth
  model as the P012 API — recorded limitation).
- `piano_core/led` mirrors the firmware's calibrated mapping as data +
  code, golden-tested against the Owner's 88-key calibration CSV.
- The legacy service is untouched; legacy keeps all its other functions.
  After the D2 migration the ESP32 is Piano Core's client and legacy
  playback no longer lights the keyboard (recorded behavioral change of
  the migration; Piano Core playback and physical-press visualization
  replace it).
- ESP32 firmware: NOT flashed during P014 without explicit Owner
  authorization (see D2).
