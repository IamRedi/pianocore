# ADR-PACKAGE-PRACTICE-V1: Practice Package import & guided practice (M5)

Status: ACCEPTED (M5 implementation baseline; independent review pending)
Date: 2026-09-20
Task: M5 — Piano Core Delivery & Guided Practice Bridge

## Context

The MIDI Curator / AI MIDI Studio (separate repository) produces curated,
transformed, hand-annotated piano material. Piano Core needs that material
as the input for guided practice and semantic LED guidance — without
sharing databases, without importing Studio internals, and without any
runtime dependency on the Studio once material has arrived.

## Decision

1. **One exchange format.** Studio material crosses the boundary ONLY as
   a versioned, standalone, hash-bound **practice package**
   (`practice-package/v1`): exactly five members (manifest.json,
   piece.mid, practice.json, sections.json, provenance.json), per-member
   SHA-256 checksums, a package digest, and a deterministic identity
   (`uuid5` over contract versions + Studio source version + MIDI
   SHA-256). Filenames and machine-local paths are never identity.
2. **One entry path.** Packages enter exclusively through the fail-closed
   importer (`piano_core.packages.importer`): allowlist -> bounds ->
   contract validation -> canonical timeline playability -> staging ->
   DB commit -> atomic promote. No partial package can become active;
   abandoned staging is reconciled at startup; re-importing an identical
   package is idempotent ("already present"); distinct transformed
   versions coexist. Offline folder import is the PRIMARY path
   (`piano-core-packages import`); the LAN upload endpoint accepts only
   the bounded tar envelope of the same members.
3. **One practice state machine.** Wait-mode practice on packages REUSES
   the accepted PracticeEngine/Learning Engine V2 (ADR-PRACTICE-V1) —
   package-derived plans feed `PracticeManager.start(plan_override=...)`;
   no second lesson state machine is created. Listen/Follow are NOT
   lesson modes: they run the pure `GuidedRun` guidance timeline (seek /
   section loop / tempo-scaled song-time windows) which emits guidance
   only and never gates on user input.
4. **Hands are evidence-bound.** RH/LH modes exist only for packages
   whose hand metadata is complete/partial evidence from the Studio's
   deterministic hand assignment; packages without hand evidence
   (`hands_availability: unknown`) can never enter a hand-specific mode
   (fail closed, never fabricated).
5. **LED semantics are versioned and hardware-free.**
   `piano_core.packages.led_frames` (led-practice-frame/v1) carries
   musical meaning only (UPCOMING/ACTIVE/WAITING/MATCHED/MISSED/EXTRA,
   hand hints, section, beat, lookahead). Renderers own colors,
   animation and physical note->pixel mapping. A recording renderer is
   provided so a later hardware campaign can replay semantic evidence;
   semantic-frame PASS makes NO physical LED claim.
6. **Persistence is additive and separate.** `packages` get their own
   SQLite store (packages, sessions, progress) with an in-place versioned
   schema; the canonical library index and all accepted M2..UI-006 data
   are untouched. Sessions record objective counts only (expected,
   correct, missed, extra, streak, completion); progress summaries are
   deterministic statistics; there is no opaque score and no automatic
   adaptation.

## Consequences

- The subsystem is auxiliary (same degrade contract as the library):
  disabled by default (`PIANO_CORE_PACKAGES_ENABLED=false` default);
  configuration failures mark it unavailable without affecting startup.
- MIDI file interpretation remains confined to the playback package
  (containment invariant honored via `load_timeline_if_playable`).
- The accepted learning API capability flags stay truthful:
  `hand_selection` remains False on the library path; package hand
  modes are reported through `package_*` capability keys.
- Hardware concerns (pixel mapping, orientation, color order, power,
  thermal, latency) are explicitly OUT of scope and PENDING until a
  dedicated physical campaign.
