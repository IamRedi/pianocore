# ADR-PIANO-LED-DRIVER-V1: Raspberry Pi Direct LED Control Becomes Preferred Architecture

> **ID:** ADR-PIANO-LED-DRIVER-V1  
> **Title:** Piano Core LED Driver Architecture Amendment — Pi-Direct Preferred, ESP32 Fallback  
> **Status:** ACCEPTED (2026-09-04)  
> **Owner:** Redion Cekrezi  
> **Authorized by:** PIANO-A001  
> **Independent Review:** PASS — no material findings (`USER_REQUEST_PIANO-A001-REVIEW`)  
> **Accepted by:** Redion Cekrezi (Owner)  
> **Accepted at:** 2026-09-04T17:14:35+02:00  
> **Acceptance record:** `docs/work/evidence/PIANO-A001-ACCEPTANCE.md`  
> **Date:** 2026-09-04  
> **Supersedes:** ADR-004 ("ESP32 remains internal LED driver for v1", `docs/baselines/PIANO_CORE_V1_DECISIONS.md`) and the LED-driver preference statement in `docs/baselines/PIANO_CORE_V1_FROZEN_SPEC.md` §12  
> **Companion amendment record:** `docs/baselines/PIANO_CORE_V1_AMENDMENT_A001.md`  
> **Scope guard:** Architecture preference ONLY. This ADR claims NO hardware verification.

---

## 1. Context — Previous Frozen Direction

The accepted Piano Core v1 baseline froze the LED path as ESP32-driven:

- **`docs/baselines/PIANO_CORE_V1_DECISIONS.md`, ADR-004 — "ESP32 remains internal LED driver for v1"** (Status: ACCEPTED):
  > "retain `Pi → internal link → ESP32 → 196-LED strip` because it is proven; Pi-direct WS2812 is not the v1 architecture."
- **`docs/baselines/PIANO_CORE_V1_FROZEN_SPEC.md`, §12 LED architecture:**
  > "Frozen v1: **Pi Piano Core → internal link → ESP32 LED driver → WS2812 strip** — retained because it is the proven, user-verified path. Pi-direct WS2812 may be investigated later but is not the v1 architecture."
- Historical note: ADR-004 itself superseded the planning baseline's earlier "Pi direct-drive preferred" lean. This amendment returns to that lean — now as an explicit, gated Owner decision rather than an unexamined preference.
- The ESP32 choice was evidence-based: it is the proven, deployed, user-verified path (live LED keyboard path, final 88-row calibration 2026-05-24). That evidence remains valid and is explicitly preserved as fallback seed (§4).

## 2. Decision — New Owner Direction

**Raspberry Pi direct LED control is now the PREFERRED Piano Core architecture. The ESP32 LED driver remains a supported FALLBACK implementation only.**

Decision made by Owner Redion Cekrezi, authorized as task PIANO-A001 (reference `USER_REQUEST_PIANO-A001`).

### 2.1 Rationale (product simplification)

- The Raspberry Pi is dedicated solely to Piano Core — no competing load justifies offloading LED rendering;
- one less MCU to own (no ESP32 firmware lifecycle in the preferred path);
- one less WiFi client on the home network;
- one less firmware codebase to maintain, build, and version;
- one less reconnect/recovery path to design and test;
- one less internal network dependency (no Pi↔ESP32 link in the preferred path);
- LED power will be supplied separately and correctly (independent 5V PSU), removing the historical reason to keep power-hungry rendering off the Pi's attachments;
- the physical 88-key / 196-LED mapping is unchanged.

### 2.2 What this decision does NOT claim

- **No hardware verification is claimed.** Pi-direct LED control is NOT accepted as production-ready, NOT tested here, and NOT evidenced. No GPIO was touched; no hardware was accessed in PIANO-A001.
- **No implementation mechanism is selected.** The exact Pi LED output method is INTERNAL and NOT frozen. Candidate techniques (hardware-assisted PWM/DMA, SPI-style, or other Linux mechanisms) may be evaluated in the later hardware spike; A001 deliberately chooses none, because choosing without evidence would repeat the mistake this amendment corrects in reverse (preference presented as architecture without evidence).
- The public contract is unchanged (§5).

## 3. Preferred Architecture — Pi-Direct

```
Piano Core / Raspberry Pi
        │
        │ hardware-timed LED output
        ▼
3.3V → 5V level shifter
        │
        ▼
196-LED WS2812 keyboard strip
        │
independent 5V LED PSU
```

**Required electrical constraints (all mandatory in the preferred path):**

1. **Common ground** between Raspberry Pi, level shifter, and LED PSU.
2. **The LED strip is NOT powered from the Raspberry Pi.** The Pi supplies logic-level signals only; all LED current comes from the independent, correctly rated 5V PSU.
3. A **3.3V → 5V level shifter** sits between the Pi's GPIO-class output and the WS2812 data line.
4. The **canonical 88-key mapping CSV** (`hardware/led/keyboard_mapping.csv`, per ADR-006) remains the authoritative physical mapping; the verified mapping data is unchanged by this amendment.
5. The exact Pi LED output mechanism is INTERNAL and NOT frozen (§2.2).

## 4. Fallback Architecture — ESP32 Local-Wired

If Pi-direct fails its later hardware/stability gate (§6), the architecture reverts to the ESP32 driver:

```
Raspberry Pi
   │
USB/local link
   ▼
ESP32 LED Driver
   │
196 LEDs
```

- **Preferred fallback transport: USB serial or another local wired link.** The old WiFi/WebSocket Pi↔ESP32 dependency must NOT be restored as the preferred product architecture; it was the single largest source of internal complexity (reconnect logic, network coupling).
- The existing ESP32 LED firmware remains valuable working evidence and the fallback seed (source map rows for `firmware/esp32-led/` COPY/ADAPT remain valid for the fallback path).
- Decisions ADR-005 (transport replaceable and not public API) and ADR-006 (canonical CSV mapping) continue to apply unchanged to the fallback path.

## 5. Public / Internal Boundary — Unchanged

- **No external Piano Core API change.** The public LED surface remains the semantic scene capability `piano.led.scene.set` (presentation intent, not hardware).
- HUGO, Home Assistant, and the UI must never know — and have no way to observe — whether LEDs are driven directly by the Raspberry Pi, through the ESP32, or through another future internal driver. This preserves ADR-001 (one logical device, internal hardware invisible) and ADR-005 (internal transport is never public API) without modification.
- The LED implementation, driver selection, and transport remain fully internal.
- The canonical LED mapping remains `hardware/led/keyboard_mapping.csv`; the verified mapping data must not be changed by this amendment.

## 6. Mandatory Hardware Gate (later, dedicated spike)

Pi-direct is NOT production-ready by virtue of this amendment. A later dedicated hardware spike must verify, at minimum:

1. exact Raspberry Pi hardware (board model/revision — currently unverified per G001 limitations);
2. a supported hardware-timed LED output method for that hardware;
3. 196-LED full-frame stability (no glitching, no partial frames);
4. correct level shifting (3.3V logic → 5V WS2812 data);
5. separate 5V PSU powering the strip (never from the Pi);
6. shared ground across Pi / level shifter / LED PSU;
7. exact 88-key mapping fidelity (all 88 keys, 178 lit LEDs, 18 gaps, reversed orientation per canonical CSV);
8. render rate;
9. CPU load during rendering;
10. MIDI timing interaction (no audible/timing degradation while rendering);
11. API responsiveness during LED rendering;
12. sustained stress run;
13. reboot/recovery behavior.

**Gate verdict rules:**

- **PASS:** Pi-direct remains the product implementation path.
- **FAIL:** switch to the ESP32 local-wired fallback (§4).
- The fallback decision must be made on **evidence, not preference** — in either direction.

## 7. Consequences

**Positive:**
- Simpler preferred product: one fewer MCU, firmware lifecycle, WiFi client, reconnect path, and internal network dependency.
- LED power architecture is designed correctly from the start (separate PSU, level shifter, common ground) instead of inheriting the historical attach-to-Pi constraints.
- The public contract is untouched; consumers are isolated from the driver swap by the existing internal boundary.
- The proven ESP32 path is preserved (not deleted) as an evidence-backed fallback, so a gate failure has a known-good retreat.
- The existing implementation-gate LED lines (public capability internal; CSV canonical; LED failure isolation) remain fully valid.

**Negative / costs:**
- Real-time WS2812 timing from a general-purpose Linux system is the central technical risk; it may not be achievable stably on the actual Pi hardware — hence the mandatory gate.
- A dedicated hardware spike task is now required before any product LED implementation; this adds a planning step that the ESP32-first plan did not need.
- Two LED paths must be kept conceptually alive (preferred + fallback) until the gate verdict.
- Pi GPIO capability and level-shifter wiring become new hardware prerequisites to verify.

**Neutral:**
- The physical mapping, calibration data, and semantic scene capability are unaffected.
- The frozen spec §24 non-goal "Pi-GPIO LED direct-drive as a v1 requirement" is read as amended: Pi-direct is no longer excluded as a preference, but it is still not an unconditional requirement — it is the preferred architecture contingent on gate PASS.

## 8. Precise Supersession Statement

This amendment supersedes **exactly**:

1. **ADR-004** — "ESP32 remains internal LED driver for v1" (`docs/baselines/PIANO_CORE_V1_DECISIONS.md`, Status ACCEPTED): its "ESP32 as preferred/only v1 LED driver" direction is replaced by "Pi-direct preferred, ESP32 local-wired fallback".
2. **`docs/baselines/PIANO_CORE_V1_FROZEN_SPEC.md` §12 (LED architecture)** — solely the driver-preference statement ("Frozen v1: Pi → internal link → ESP32 → WS2812 strip … Pi-direct WS2812 … is not the v1 architecture").

**NOT superseded (remain in force):** ADR-001, ADR-002, ADR-003, ADR-005, ADR-006, ADR-007, ADR-008, ADR-009, ADR-010, ADR-011, ADR-012, ADR-013; frozen spec §13 (canonical LED mapping asset), §2 (public/internal boundary) as applied to LEDs, and the implementation-gate LED-subsystem lines. The five original baseline files remain byte-for-byte unchanged; supersession is effected only through this ADR plus `docs/baselines/PIANO_CORE_V1_AMENDMENT_A001.md` once accepted.

## 9. Record Integrity

This ADR was **ACCEPTED** by Owner Redion Cekrezi on 2026-09-04 (independent review `PASS`, no material findings; formal record: `docs/work/evidence/PIANO-A001-ACCEPTANCE.md`). It is now part of the effective Piano Core architecture baseline together with the accepted `docs/baselines/PIANO_CORE_V1_AMENDMENT_A001.md`. The architecture preference it records is binding; the Pi-direct path it prefers remains subject to the §6 hardware gate before any production implementation decision. Modification or silent reinterpretation of this record after acceptance requires a new Owner-authorized amendment task.
