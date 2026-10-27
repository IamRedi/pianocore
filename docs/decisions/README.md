# Architecture Decision Records (ADRs)

> **Standard:** Redion Work Standard (RWS) v0.1.0-draft  
> **Repository:** Piano Core (`piano-core`)

> **Public snapshot note:** links in these ADRs to `docs/work/...` (handoffs, evidence, status) point to the private development repository and are not part of this public copy. See [EVIDENCE.md](../../EVIDENCE.md).

All initial architecture decisions for Piano Core v1 were established during planning and are recorded in the frozen baseline:

- **Canonical Architecture Decisions:** [`docs/baselines/PIANO_CORE_V1_DECISIONS.md`](../baselines/PIANO_CORE_V1_DECISIONS.md)

### Summary of Accepted Decisions

- **ADR-001:** One logical Piano Core device endpoint
- **ADR-002:** Raspberry Pi is the authoritative controller and state owner
- **ADR-003:** Roland is the bidirectional MIDI instrument endpoint
- **ADR-004:** ESP32 remains the internal LED driver for v1
- **ADR-005:** Pi↔ESP32 transport is replaceable and internal (not public API)
- **ADR-006:** 88-key/196-LED CSV is canonical physical mapping evidence
- **ADR-007:** Old ESP32-S3/LVGL UI discarded as product architecture
- **ADR-008:** Canonical API = semantic HTTP commands/queries + WebSocket events
- **ADR-009:** Legacy :8766 WS protocol remains temporary compatibility adapter only
- **ADR-010:** Canonical `playback.stop` is separate from global `all_notes_off` panic
- **ADR-011:** Home Assistant and HUGO are API clients/adapters, not core logic
- **ADR-012:** EQ accessory remains separate and out of v1 scope
- **ADR-013:** One canonical source tree and version defines deployment

Any future architectural changes or amendments require an authorized ADR task and explicit Owner approval. New sequential ADRs continue after `ADR-014` (next available: `ADR-015`).

## Premium UI V2

- [ADR-014: Premium UI V2 — Architecture & Premium Foundation](ADR-014-PREMIUM-UI-V2.md)
  — **ACCEPTED**, PIANO-UI-001; independent review `PASS` and Owner acceptance
  recorded 2026-09-15. Canonical architecture source for the separate UI lineage.
  Supersedes no accepted ADR or frozen baseline; UI-002 remains not authorized.
- [ADR-018: Realistic 3D Hands V1](ADR-018-REALISTIC-3D-HANDS-V1.md)
  — ACCEPTED: TIER-1 + TIER-A + FINAL ENGINEERING MILESTONE,
  LEGAL_RELEASE_GATE_PENDING, PIANO-UI-007
  (architecture recorded 2026-09-20; final engineering acceptance 2026-09-25).
  Presentation-only MotionEngine/IK/renderer consuming the UI-006 plan;
  vendored pinned Three.js.
- [ADR-019: Premium Tablet Experience](ADR-019-PREMIUM-TABLET-EXPERIENCE.md)
  — SPECIFICATION OWNER-APPROVED / U8-P1 IMPLEMENTATION AUTHORIZED / U8-P2–P6
  NOT AUTHORIZED, PIANO-UI-008 (Owner decision recorded 2026-09-28).
  Tablet-landscape-first
  product shell, responsive/design-system/interaction contracts, capability
  gates and deterministic acceptance matrix; Core remains musical authority.

The initial-decision summary above is historical. Its ADR-004 preference is
subject to the accepted [A001 overlay](../baselines/PIANO_CORE_V1_AMENDMENT_A001.md).

## Premium Learning Studio — U8-P7

- [ADR-020: Premium Learning Studio](ADR-020-PREMIUM-LEARNING-STUDIO.md)
  — PROPOSED / implemented under the hash-bound Owner U8-P7 mandate;
  independent code and architecture review PASS; Owner acceptance pending.
  Additive Core-owned Flow strategy, persisted Academy progress and
  presentation-only immersive Studio. Earlier acceptance records remain historical.
