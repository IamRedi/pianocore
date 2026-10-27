# Practice Packages — Studio → Piano Core guided practice (M5)

Practice Packages are the only sanctioned way Studio material enters
Piano Core. Contract: `practice-package/v1` (see
`docs/decisions/ADR-PACKAGE-PRACTICE-V1.md`). This document describes
implemented behavior only.

## Package layout (exactly five members)

```
<package>/
    manifest.json     identity, versions, checksums, capabilities
    piece.mid         the exact MIDI of the Studio version
    practice.json     PracticeEvent timeline (note/onset/offset/velocity/
                      hand/track/bar/beat/section/group/importance)
    sections.json     practice sections (phrase-based or honest bar windows)
    provenance.json   lineage summary back to the Studio version / M3 audio
```

Versions are independent: `package_schema_version`,
`practice_contract_version`, `led_semantic_version` (all currently 1).
Identity (`package_id`) is a deterministic uuid5 over the contract
versions + Studio source version id + MIDI SHA-256 — never over
filenames or paths.

## Security model (fail closed)

The importer is the only entry path and rejects: unknown schema/contract
versions; missing or extra members; checksum or digest mismatches;
identity mismatches; malformed MIDI/JSON; notes outside the verified
88-key keyboard; event storms (bounded counts/sizes); traversal or
non-file members in tar envelopes; unknown `required_capabilities`.
Tar envelopes are never extracted to arbitrary paths and never
executed. Activation is atomic (stage → verify → DB commit → rename);
import is idempotent for identical packages; distinct versions coexist.

## Offline workflow (PRIMARY)

```
Studio: export the version  → data/piano_core_exports/<package_id>/
operator: copy the folder (USB/SCP) to the appliance
Piano Core: piano-core-packages import <folder>
```

Configuration: `PIANO_CORE_PACKAGES_ENABLED`, `PIANO_CORE_PACKAGES_ROOT`
(managed activated copies; staging lives under `<root>/incoming`),
`PIANO_CORE_PACKAGES_DB_PATH` (separate SQLite store). The subsystem is
disabled by default and auxiliary: failures degrade it without
affecting appliance startup.

## LAN workflow (secondary convenience)

1. Studio fetches `GET /api/v1/packages/capabilities`.
2. Studio checks schema/contract/LED-semantic compatibility (fail
   closed) and its size bound.
3. Studio uploads the tar envelope: `POST /api/v1/packages/upload`
   (`application/x-tar`, bounded).
4. Piano Core validates, stages, activates atomically and returns an
   explicit acknowledgement: package id + accepted MIDI SHA-256 +
   `activated`. The Studio reports success ONLY on that acknowledgement
   — an HTTP return alone never counts as delivered. Duplicate sends
   answer `already_present` (idempotent).

## Guided practice

- **Wait** — the accepted Learning Engine V2 (`WAIT_FOR_NOTE`) fed by
  package-derived plans; sessions recorded automatically at stop with
  objective counts (expected/correct/missed/extra/completion).
- **Listen / Follow** — pure `GuidedRun` guidance timeline (seek,
  section loop, tempo-scaled windows); passive, no invented grading.
- **RH / LH / Both** — hand filtering only when the package carries
  hand evidence; unknown-hands packages refuse hand modes.
- **Loop Section / Slow practice** — section-scoped plans and loops;
  tempo scale 0.25..4.0 applied proportionally over the tempo map (the
  package MIDI is never modified).

## LED semantic contract (v1)

`piano_core.packages.led_frames` emits `led-practice-frame/v1`
semantic frames: current/upcoming notes with
UPCOMING/ACTIVE/WAITING/MATCHED/MISSED/EXTRA states, hand hints,
section, beat, lookahead ms, tempo scale, loop count. The renderer owns
colors, animation, and note→pixel mapping. A recording renderer ships
for deterministic tests and future hardware-campaign replay.

**Hardware boundary:** semantic-frame PASS makes NO claim about
physical pixel correctness, orientation, color order, power, thermal
behavior, or end-to-end hardware latency — all PENDING a dedicated
hardware campaign.

## Sessions & progress

Sessions persist per package (mode, hand, section, tempo scale, times,
objective counts, completion). Progress summaries are deterministic
statistics: attempts, completions, note accuracy (correct/expected),
best/recent tempo, last practiced, and trouble sections ranked by miss
rate. No opaque scores; nothing trains on this data.
