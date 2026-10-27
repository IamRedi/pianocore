# Piano Core Guided Practice (PIANO-P015)

> Architecture: `docs/decisions/ADR-PRACTICE-V1.md` — normative for the
> decisions summarized here.

## 1. What it is

Guided practice turns Piano Core into a basic REAL learning instrument:
choose a song in the UI, press **Practice**, and the keyboard LEDs light
the next expected note or chord. The learner plays it on the real
Roland; Piano Core observes the physical keys through the canonical
StateStore path and advances only on a correct play. The song NEVER
plays itself in guided mode — practice is silent from Piano Core (no
automated MIDI output is ever sent; the learner hears the Roland's own
key sound).

## 2. User workflow

1. Library search → select a song.
2. Press **Play** (automated performance) **or** **Practice** (guided
   learning). The two are mutually exclusive appliance-wide.
3. Follow the lit keys; the Practice panel shows the current step
   (`Step 14 / 820`), the expected note names (`C4` or `C4 + E4 + G4`),
   and progress.
4. **Stop Practice** ends the session (LEDs clear, playback becomes
   possible again). Completing the last step shows "Practice complete".

## 3. Lesson rules (exactly as implemented)

- **Single note:** the target advances when the required key is
  physically held.
- **Chord:** the target advances only when ALL required notes are
  simultaneously held; press order never matters; extra held notes are
  tolerated (they never advance a step and never block completion).
- **Wrong notes:** never advance the step; they only increment the
  observational `incorrect_attempts` counter.
- **Repeated notes:** a target note that is already physically held when
  a step becomes active is DISARMED — it must be released and pressed
  again. Holding through between repeated notes never advances.
- **Self-paced:** rests and tempo are irrelevant; the source onset is
  metadata only.

## 4. Targets come from the canonical timeline

Practice targets are a NOTE-ON projection of the canonical P009
timeline (no second MIDI parser): GM percussion channel (10) and notes
outside the verified 88-key range (MIDI 21..108) are never targets;
notes at the same onset form one chord (multi-track merges,
duplicate same-note attacks deduplicate); a song with zero keyboard
targets is rejected as `song_unplayable`.

## 5. LEDs during practice

While a session is `WAITING`, ONLY the practice target is displayed —
the single-color keyboard hardware cannot distinguish sources, so
physical mirroring is suppressed during practice to keep the target
unambiguous. Playback mirroring is impossible then anyway (mutual
exclusion). Target keys light at a fixed display intensity scaled by
the existing LED brightness control. Stop, completion, failure, and
shutdown clear the practice display deterministically. LED transport
problems degrade only the visualization — the session keeps progressing
and targets stay readable in the UI/API. Outside practice the LED
subsystem behaves exactly as P014-HF1 shipped (modes, playback and
physical mirroring, HF1 re-enable contract unchanged).

## 6. Exclusivity and safety

- Guided practice and automated playback share the existing
  appliance-wide advisory lock (`/run/lock/piano-core-playback.lock`;
  documented meaning generalized to "appliance activity ownership").
  API playback, CLI `piano-core-play`, and practice exclude each other
  in both directions with deterministic 409s; a crashed process can
  never permanently hold the `flock`.
- Practice observes physical input ONLY through the canonical
  input → StateStore path (read-only; no second MIDI input, no raw
  ALSA/mido, no StateStore mutation). Automated playback output and LED
  state cannot advance a lesson.
- The observation path is bounded and fast (in-memory transitions; LED
  display changes go through the bounded drop-oldest LED queue); an
  internal error fails the session VISIBLY (`FAILED`) instead of
  silently pretending a note was processed.
- Practice never creates or uses RtMidiOut; `POST /api/v1/practice/stop`
  is not a STOP/PANIC — nothing is sounding from Piano Core.

## 7. Operations notes

- No new configuration, dependencies, listeners, or threads. Practice
  availability derives from the library: without the library the API
  reports `available: false` and `start` returns `503
  practice_unavailable`.
- Journal loggers: `piano_core.practice.manager` (session
  start/stop/failure) and `piano_core.practice.engine` (step
  completions at INFO). Per-note detail is never logged at INFO.
- Useful checks: `GET /api/v1/practice` (state/target/progress),
  `GET /api/v1/led` (`practice_active` flag), StateStore revision
  unchanged by practice (physical-only authority).

## 9. Learning Engine V2 (PIANO-UI-004)

UI-004 evolves this subsystem in place. There is still one Practice/Learning
progression engine and one activity owner; `/api/v1/practice` remains the P015
compatibility surface. The richer runtime-only snapshot is available at
`GET /api/v1/learning`.

Supported modes:

- `WAIT_FOR_NOTE` — the exact accepted P015 self-paced behavior;
- `SECTION_PRACTICE` — target onsets inside a validated half-open song-time
  range `[start_seconds, end_seconds)`; these are time sections, not claimed
  measures or bars;
- `REPEAT_UNTIL_CORRECT` — 1..16 consecutive correct fresh-press completions
  per target. An incorrect NOTE_ON resets the current repetition streak and is
  recorded; it never advances.

The current canonical timeline has no hand, fingering, track-retention,
measure/beat or timing-opportunity contract. LEFT/RIGHT hand modes, claimed
hand-aware BOTH mode, FOLLOW/LISTEN/WATCH orchestration, slow/progressive tempo
and early/on-time/late/missed rhythm grading are therefore not exposed.

Snapshots add session/target identity, mode, optional section, accepted current
notes, latest Core outcome, repetition totals, generation/version and update
time. They are recoverable after page reload but are deliberately not persisted
across Core restart. Restart-target and restart-section commands require exact
session/version guards, and every guarded command (including guarded stop)
validates the guard and applies its mutation inside ONE atomic engine critical
section, so a physical input transition can never slip between validation and
mutation.

Input availability is a durable grading barrier (U4-F1): while the
authoritative physical input is unavailable, NO observation can grade — stale
NOTE_ON/NOTE_OFF cannot complete, re-arm, advance `accepted_notes` or
repetition state, or count an incorrect attempt. MIDI disconnect clears the
current attempt, disarms the target, reports `INPUT_UNAVAILABLE`, and preserves
completed progress; reconnect derives re-arm state from authoritative
StateStore truth, so target notes already held at reconnect require a physical
release and a fresh press before they can satisfy the target.

`accepted_notes` always describes the current attempt's accepted contribution
(U4-F4): releasing a required note removes it immediately and advances the
Learning version only when serialized truth changes.

Target identity is deterministic and collision-free (U4-F5): plan targets
carry their canonical onset-group ordinal in the full song projection
(`target:<ordinal>:<notes>`); section filtering never renumbers identity.
