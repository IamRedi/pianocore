# PIANO CORE V1 — FROZEN-BASELINE AMENDMENT A001

> **ID:** PIANO_CORE_V1_AMENDMENT_A001  
> **Title:** LED Driver Preference Amendment — Raspberry Pi Direct Preferred, ESP32 Fallback  
> **Authority:** FROZEN-BASELINE AMENDMENT — ACCEPTED (2026-09-04)  
> **Authorized by:** PIANO-A001 (Owner Redion Cekrezi, reference `USER_REQUEST_PIANO-A001`)  
> **Date:** 2026-09-04  
> **Decision record:** `docs/decisions/ADR-PIANO-LED-DRIVER-V1.md`  
> **Status:** ACCEPTED. Independently reviewed (verdict `PASS`, no material findings) and accepted by Owner Redion Cekrezi on 2026-09-04 at 2026-09-04T17:14:35+02:00. Acceptance record: `docs/work/evidence/PIANO-A001-ACCEPTANCE.md`. This amendment is effective as part of the architecture baseline per §5.

---

## 1. Integrity of the Original Frozen Specification

The original document:

```
docs/baselines/PIANO_CORE_V1_FROZEN_SPEC.md
```

**MUST remain byte-for-byte unchanged.** It is historical/frozen evidence and is never edited in place. Its file hash at the time of this amendment is recorded in `docs/work/handoffs/PIANO-A001.md` and must remain identical after acceptance.

Likewise, these companion baseline files remain byte-for-byte unchanged:

- `docs/baselines/PIANO_CORE_V1_IMPLEMENTATION_GATE.md`
- `docs/baselines/PIANO_CORE_V1_SOURCE_MAP.md`
- `docs/baselines/PIANO_CORE_V1_DECISIONS.md`
- `docs/baselines/PIANO_CORE_V1_AUDIT_SUMMARY.md`

## 2. Supersession Scope — LED Driver Preference ONLY

Upon acceptance, this amendment supersedes **only** the LED-driver preference where the frozen record states that the ESP32 is the preferred v1 LED driver:

1. **`PIANO_CORE_V1_FROZEN_SPEC.md`, §12 (LED architecture)** — the statement:
   > "Frozen v1: **Pi Piano Core → internal link → ESP32 LED driver → WS2812 strip** — retained because it is the proven, user-verified path. Pi-direct WS2812 may be investigated later but is not the v1 architecture."
   is superseded by the Pi-direct preferred topology defined in `ADR-PIANO-LED-DRIVER-V1.md` §3.
2. **`PIANO_CORE_V1_DECISIONS.md`, ADR-004** — "ESP32 remains internal LED driver for v1": superseded by Pi-direct preferred / ESP32 fallback.
3. **`PIANO_CORE_V1_FROZEN_SPEC.md`, §24 (Frozen non-goals)** — the entry "Pi-GPIO LED direct-drive as a v1 requirement" is read as amended: Pi-direct is the preferred architecture (contingent on the mandatory hardware gate in `ADR-PIANO-LED-DRIVER-V1.md` §6), but it is still not an unconditional v1 requirement.

## 3. Amended LED Direction (Summary)

Upon acceptance, the effective LED architecture becomes:

- **PREFERRED:** Raspberry Pi direct LED control — Piano Core on the Pi produces hardware-timed LED output → 3.3V→5V level shifter → 196-LED WS2812 keyboard strip → independent 5V LED PSU. Mandatory: common ground between Pi / level shifter / LED PSU; the strip is never powered from the Raspberry Pi; canonical mapping `hardware/led/keyboard_mapping.csv` remains authoritative.
- **FALLBACK (evidence-gated):** Raspberry Pi → USB/local wired link → ESP32 LED Driver → 196 LEDs. The WiFi/WebSocket Pi↔ESP32 dependency is not restored as preferred architecture.
- **Unchanged boundary:** no external API change; `piano.led.scene.set` remains the only public LED surface; driver, transport, and mechanism stay internal.
- **Gate:** Pi-direct is production-ready only after the dedicated hardware spike passes (`ADR-PIANO-LED-DRIVER-V1.md` §6). No hardware verification is claimed by this amendment.

## 4. Everything Else Remains Frozen

All other Piano Core v1 frozen architecture remains unchanged — including but not limited to: Pi as authoritative controller (ADR-002), Roland MIDI endpoint (ADR-003), transport replaceability and non-public status (ADR-005), canonical CSV mapping (ADR-006), API shape (ADR-008), legacy compatibility (ADR-009), playback semantics (ADR-010), HUGO/HA boundaries (ADR-011), and all non-LED sections of the frozen spec and implementation gate. Any further deviation requires its own explicit Owner-authorized amendment.

## 5. Effective Baseline Composition Rule

Future agents must compute the effective Piano Core architecture baseline as:

```
docs/baselines/PIANO_CORE_V1_FROZEN_SPEC.md
+ accepted docs/baselines/PIANO_CORE_V1_AMENDMENT_A001.md
= effective Piano Core architecture baseline
```

The frozen spec is read as-is except where an **accepted** amendment explicitly supersedes a statement. This amendment was **accepted on 2026-09-04** (Owner Redion Cekrezi; acceptance record `docs/work/evidence/PIANO-A001-ACCEPTANCE.md`) and is now part of the effective architecture baseline.

## 6. Record Integrity

This amendment must not be silently modified or silently accepted. Acceptance required independent review and explicit Owner acceptance recorded per RWS §14.3 — satisfied on 2026-09-04 via `docs/work/evidence/PIANO-A001-ACCEPTANCE.md`. Supersession of this amendment requires a new Owner-authorized amendment task.
