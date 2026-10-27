# ADR-015 — Learning Engine V2

- **Status:** IMPLEMENTED LOCALLY / PENDING INDEPENDENT REVIEW
- **Task:** PIANO-UI-004
- **Owner authorization:** `USER_REQUEST_PIANO-UI-004-LEARNING-ENGINE-V2`
- **Builds on:** ADR-PRACTICE-V1, ADR-014-PREMIUM-UI-V2

## 1. Context

P015 already supplies the authoritative, silent and self-paced Practice path:
canonical P009 timeline targets, post-reduction StateStore physical input,
deterministic note/chord/re-arm grading, activity-lock exclusion and target LED
output. Creating another learning state machine would split authority.

The current timeline retains ordered song-time seconds and canonical MIDI
commands. It does not retain track identity, meter, measures, beats, authored
sections, human hand assignment or fingering. Playback and Practice remain
mutually exclusive. These facts bound the modes that UI-004 can claim.

## 2. Decision

### D1 — Evolve Practice; one progression authority

`piano_core.practice` is the Learning Engine V2 implementation. The existing
engine remains the sole target/grading/progression state machine. Legacy
`/api/v1/practice` behavior remains compatible; `/api/v1/learning` exposes the
richer contract. StateStore remains physical authority, P009 remains the only
MIDI/timeline source, and the browser remains presentation/control only.

### D2 — Supported strategies

- `WAIT_FOR_NOTE`: the accepted P015 behavior, unchanged.
- `SECTION_PRACTICE`: the same behavior over a validated half-open
  `[start_seconds, end_seconds)` projection of canonical target onsets.
- `REPEAT_UNTIL_CORRECT`: each target needs 1..16 configured consecutive
  correct completions. Each success needs a release/fresh press. An unexpected
  NOTE_ON increments incorrect attempts and resets the current success streak;
  it never advances. The loop is bounded by the repetition count and finite
  target plan.

`WATCH`, `LISTEN`, `FOLLOW`, `SLOW_PRACTICE` and `PROGRESSIVE_TEMPO` require a
separately governed learning/playback interaction and are deferred.
`LEFT_HAND`, `RIGHT_HAND` and a claimed hand-aware `BOTH_HANDS` require canonical
hand assignment and are `REQUIRES FUTURE HAND-ASSIGNMENT CONTRACT`. Pitch is
never used as a hand heuristic.

### D3 — Outcomes and timing

`CORRECT` means the P015 target is satisfied from authoritative held-note truth.
`INCORRECT` means an unexpected physical NOTE_ON occurred while a target was
active. Extra notes remain learner-friendly: they are recorded but do not
prevent later completion when all required notes are held. `MISSED` is not
defined for these self-paced modes because musical time does not advance an
attempt. Early/on-time/late scoring is deferred until Core owns a verified
timing-opportunity and meter/beat contract.

### D4 — Recoverable runtime session

Every observable snapshot carries a runtime-only `session_id`, strategy,
optional section, stable content-derived target ID, accepted current notes,
latest outcome, progress, repetition counts, timestamps, generation and
monotonic session version. Page reload recovers from `GET /api/v1/learning`.
Core restart intentionally returns inactive state; no active session or
physical held-note state is persisted.

### D5 — Commands and stale protection

Start validates song, supported mode, section bounds and repetition bounds.
Stop, restart-target and restart-section are Core operations. Restart commands
require the expected session ID and version; mismatches return a typed 409
`stale_learning_command`. The UI cannot mutate progress directly.

### D6 — Disconnect and reset

The Application forwards the canonical StateStore connection transition after
reduction. Disconnect clears accepted attempt state, disarms the current target
and publishes `INPUT_UNAVAILABLE`; no event can advance stale truth. Reconnect
re-arms only target notes not already held in StateStore, preserving the fresh
press rule.

### D7 — Publication and presentation

One-second bounded HTTP polling is sufficient for low-frequency learning
snapshots and reload recovery; UI-003 LiveNoteState is not widened into a
learning event bus. The 88-key renderer may outline expected notes from the
Learning snapshot. Physical/playback sources remain separate, and the outline
has no feedback path into grading.

## 3. Consequences and limits

- Sections are authoritative song-time ranges, not claimed measures/bars.
- No hand-aware mode, fingering, notation, falling notes or advanced rhythm
  percentage is exposed.
- Session history and analytics are runtime-only and disappear on restart.
- Practice remains silent and mutually exclusive with playback.
- Independent review and Owner acceptance are still required before this ADR
  or UI-004 can become accepted.
