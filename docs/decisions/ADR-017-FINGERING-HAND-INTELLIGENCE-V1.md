# ADR-017 — Fingering & Hand Intelligence V1

- **Status:** IMPLEMENTED LOCALLY / PENDING INDEPENDENT REVIEW
- **Task:** PIANO-UI-006
- **Owner authorization:** `USER_REQUEST_PIANO-UI-006-FINGERING-HAND-INTELLIGENCE` (2026-09-19)
- **Builds on:** ADR-014, ADR-015, ADR-016

## 1. Source facts

The canonical P009 timeline retains song-time commands with pitch, channel and
velocity. UI-005 deterministically derives onset-ordered note spans and now
publishes a `score-sha256` source version. Track identity, staff, voice,
authored hand, authored fingering, meter and beat do not survive into this
canonical representation. Channel therefore remains event metadata and is not
treated as hand authority.

## 2. Decision

### D1 — Core owns one version-bound advisory plan

`piano_core.fingering` consumes `ScoreProjection`; it never parses MIDI. A plan
is identified by SHA-256 over explicit schema, plan, generation and engine
versions; strategy; provenance; the exact score source version; complete
validated configuration; and every canonical target field including
alternatives. The same input and configuration therefore produce the same
`plan_id`, target order and alternatives without randomness, process hashes,
wall-clock data, LLMs or network services. A bounded eight-plan service cache
is keyed by song, source version and configuration.

Every V1 assignment has provenance `DETERMINISTIC_INFERENCE`. The API also
states that authored metadata and manual overrides are unavailable. The result
must never be described as source-authored truth.

### D2 — Explainable bounded hand inference

At each exact onset, duplicate pitches share one key assignment. The solver
enumerates contiguous low-to-high splits across LEFT and RIGHT. Each hand is
limited to five distinct simultaneous pitches and the validated default span
is 12 semitones (`5..24` accepted by the model). Ranking combines continuity
from prior hand centers, movement, register comfort and balance. Pitch is one
cost input, not a source label or a sole fixed threshold. A shape with more
than ten pitches or no valid split raises `FingeringUnsupportedError`.

Assignments remain occupied for the canonical half-open lifetime
`[onset, onset + duration)`. Before each attack the solver expires assignments
whose end is at or before the new onset. Retained fingers cannot hold a
different physical key, and retained pitches participate in hand-span
feasibility. Duplicate aliases of one physical key share one assignment and
the longest end; zero-duration attacks receive advice but create no occupancy.

### D3 — Ordered finger candidates with ergonomic costs

Both hands use thumb=1, index=2, middle=3, ring=4, little=5. The engine
enumerates physically ordered finger combinations for each hand. Ranking uses
interval/finger-spacing fit, a black-key thumb penalty, repeated-note
continuity and next-target motion direction. Stable tuple ordering breaks exact
ties. The chosen finger and up to the configured number of distinct ranked
alternatives, excluding the primary, are public. Candidate scanning continues
past the primary and removes duplicates; internal costs are not exposed as
subjective scores.

### D4 — Presentation and Learning remain consumers

`GET /api/v1/fingering/{song_id}` returns a complete plan. The browser treats
it as untrusted and accepts it atomically only when schema/engine/config,
song/source identity, unique target IDs and indices, exact playable-target
coverage, target MIDI/channel/timing/duration, hand/finger enums and distinct
bounded alternatives all match its current validated score replica.

The visible-tab one-second authoritative poll also performs at most one
overlap-safe score revalidation. This makes same-song source replacement
discoverable without animation-frame fetching or page reload. A newly observed
source clears old advice before replacement installation, and generation
tokens reject delayed old responses. Sheet and Falling render `L1..L5` /
`R1..R5` labels when geometry permits; Hybrid shares the same map. Renderers
cannot modify or regenerate the plan.

Learning and Practice code paths are unchanged. The current modes continue to
grade expected pitches only. Fingering is advice and cannot reject a musically
correct input.

### D5 — Manual override and 3D data are deferred

The repository has no suitable persisted, version-checked override store.
Manual override is explicitly deferred instead of accepting browser mutation
or runtime-only pseudo-authority. Targets carry score identity, pitch, channel,
onset, duration, hand and finger; they contain no pose, wrist, joint, IK,
collision or animation values. UI-007 remains unauthorized.

## 3. Failure and regeneration semantics

- Missing subsystem: typed `503 fingering_unavailable`.
- Unknown/unplayable source: existing typed library errors.
- Oversized score: typed `413 score_too_large`.
- Impossible material: typed `422 fingering_unsupported`.
- Malformed configuration: rejected before generation.
- Mismatched/stale browser plan: rejected and no labels installed.

A new song, changed score source version or changed configuration uses a new
cache key and plan identity. A simple reload refetches and reconstructs the
same plan. V1 exposes generation/version `1`; a future algorithm or schema
change must increment the relevant public version rather than silently change
meaning.

## 4. Limits

This is a deterministic first ergonomic model, not a claim of conservatory,
professional, medical or physiological fingering quality. It has no authored
hand preservation path because the canonical source has no authored hand data.
The configured `max_hand_span_semitones` range 5..24 is a software heuristic,
not a physical guarantee; larger values may permit ergonomically unrealistic
recommendations. Personal hand size, skill, technique profiles, manual
overrides, finger sensing, fingering grading and 3D hands remain deferred.
