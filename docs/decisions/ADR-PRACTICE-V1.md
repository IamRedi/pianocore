# ADR-PRACTICE-V1: Guided Practice / Learning Mode Foundation (PIANO-P015)

- **Status:** ACCEPTED FOR IMPLEMENTATION (Owner authorization `USER_REQUEST_PIANO-P015`, 2026-09-13)
- **Context tasks:** builds on P009 (Playback Core / canonical timeline), P006/P008 (StateStore), P012 (Control API), P013 (Web UI), P014 (LED keyboard adapter)
- **Supersedes:** nothing. Resolves the P014-deferred "GUIDANCE/upcoming" LED functionality as a bounded first mode (silent, self-paced guided practice).

## 1. Context

Piano Core can play songs (P009), show what is physically played (P006 StateStore + P014 LED
mirroring), and expose everything through a bounded local API/UI (P012/P013). The Owner wants the
appliance to become a basic REAL learning instrument: pick a song, choose PRACTICE, and the
keyboard's LEDs show the next expected note/chord; the learner plays it on the real Roland; only a
correct play advances the lesson; wrong notes never advance. This must coexist safely with playback,
StateStore, LED, API and UI — and must NEVER sound the song by itself.

## 2. Decision

### D1 — One new canonical subsystem, no duplicated foundations

`piano_core.practice` adds four modules:

- `model.py` — typed practice model and typed errors;
- `plan.py` — deterministic practice-plan derivation FROM the canonical P009 timeline
  (`piano_core.playback.timeline.build_timeline` / `load_timeline`). P009 remains the ONLY MIDI
  parsing surface; practice never opens a MIDI file itself and never invents a second parser;
- `engine.py` — the pure in-memory practice state machine (lesson progress only);
- `manager.py` — composition: song_id resolution through the canonical LibraryService, appliance
  activity ownership (D6), LED display hooks (D5), and the read-only input observation entry point
  (D4).

The subsystem owns NO MIDI hardware, NO threads of its own, NO timers, and NO persistence.

### D2 — Practice plan = human NOTE-ON projection (exact-onset grouping)

A practice plan is derived from a loaded `Timeline` as follows (all deterministic, all tested):

- only canonical NOTE_ON commands create targets; NOTE_OFF, sustain, tempo markers never do;
- NOTE_ONs on the GM percussion channel (canonical channel 10) are NEVER learning targets
  (deliberate, documented, tested — drum hits must not become keyboard targets);
- NOTE_ONs outside the verified physical keyboard range (MIDI 21..108 inclusive) are skipped;
- notes sharing the exact canonical onset (the same timeline `time` value; events at the same
  absolute tick always receive the identical accumulated float) form ONE target — a chord;
  multi-track simultaneous piano notes therefore merge; duplicate same-note attacks at one onset
  are deduplicated (impossible duplicate physical keys cannot exist);
- targets are ordered by onset ascending; notes within a target are sorted ascending. Rests and
  timing gaps are preserved ONLY as observational metadata (`onset_seconds`); practice is
  self-paced — the learner never waits for the song's clock;
- a song whose projection has zero targets fails deterministically (`song_unplayable` envelope
  family, message distinguishes the zero-target case).

No fuzzy chord heuristics, no tolerance windows, and no adaptive classification are used. If a real
file ever proves exact-onset grouping insufficient, a single bounded deterministic tolerance would
require new evidence, tests, and an ADR amendment first.

### D3 — Practice state machine

Typed states: `IDLE`, `WAITING`, `COMPLETED`, `FAILED`. A separate `PREPARING` state is
deliberately omitted: session start (library lookup → validated resolve → canonical timeline load →
plan derivation → ownership acquisition) is synchronous within one API request, exactly like
playback start since P012; there is no observable preparing phase to report honestly.

- `start` is legal from `IDLE`, `COMPLETED` and `FAILED`; from `WAITING` it is a typed
  `practice_already_active` conflict;
- **Step completion (single note):** the required note must be physically held in the authoritative
  post-event StateStore pressed set.
- **Step completion (chord):** ALL required notes must be simultaneously held. Press order never
  matters. Extra/wrong held notes are tolerated (learner-friendly deterministic policy): they never
  advance the step by themselves, and they do not prevent completion once every required note is
  held. A NOTE_ON for a note that is not part of the current target increments the observational
  `incorrect_attempts` counter and never advances anything.
- **Repeated notes / re-arm (critical):** at the moment a step becomes active, every required note
  that is ALREADY physically held is disarmed; it must be physically released (a NOTE_OFF observed
  on the canonical input path) before it can satisfy the new step. A fresh press is then required.
  Source `C4, C4` therefore requires two distinct physical presses — holding through can never
  satisfy the second target. The same semantics apply per-note inside chords (a repeated chord with
  overlapping held notes requires re-arming exactly the overlapping notes).
- **Completion of the last target:** state `COMPLETED`; practice LEDs clear; ownership releases. The
  completed-session summary (song, step totals, attempts) stays observable via `GET /api/v1/practice`
  until the next `start` or `stop`. `stop` is a defined reset: it always returns to `IDLE` and
  discards the summary (idempotent, including from `IDLE`).
- **Failure:** any unexpected internal error during observation fails the session VISIBLY
  (`FAILED` + typed error, LEDs cleared, ownership released) — a dropped note is never silently
  treated as processed.

### D4 — Input observation: read-only observer on the canonical path, StateStore authoritative

Practice never opens a second MIDI input and never parses raw ALSA/mido. The Application's existing
canonical input callback (MIDI reader thread → `StateStore.apply_event` → LED observer) gains one
additional read-only observer: `PracticeManager.observe_input_event(event)`. The manager reads the
StateStore snapshot AFTER the event was reduced (the StateStore remains THE physical-input
authority; practice derives nothing from raw events and never mutates the store) and hands both to
the engine.

Bounded-and-fast contract (same discipline as the P014 LED observer): the observation path performs
zero filesystem I/O, zero network I/O, and zero blocking LED hardware calls — the engine transition
is pure in-memory set arithmetic under one small lock, and target display changes enqueue into the
LED adapter's bounded drop-oldest worker queue (non-blocking by contract). A canonical event can
therefore never be delayed by practice. Because correctness-critical note lifecycle events must
never be silently dropped, ANY internal error in this path fails the practice session visibly (D3)
instead of pretending the note was processed. A dedicated worker queue was evaluated and rejected as
unnecessary indirection: nothing on this path blocks.

Automated playback output cannot advance practice (the playback engine has no path into the
practice manager; asserted by dedicated boundary tests). LED state cannot advance practice (LEDs
are write-only visualization; no feedback path exists).

### D5 — LED practice visualization: one canonical practice source

`SOURCE_PRACTICE = "practice"` becomes a third canonical source inside the P014
`LedKeyboardAdapter` (playback/physical remain unchanged; the calibrated note→LED mapping and the
ESP32 firmware/protocol are untouched — the adapter's three-frame protocol cannot distinguish
colors or sources, so no RGB semantics are faked).

Display semantics (deterministic, documented to the learner):

- while a practice session is `WAITING`, ONLY the practice source is visible: the current target's
  keys are lit (fixed target intensity, scaled by the existing brightness control). This is the
  simplest unambiguous target representation on single-color hardware — physical-key mirroring
  during practice would make "which lit key is my target?" ambiguous, so it is suppressed while
  WAITING; playback mirroring is impossible anyway because playback and practice are mutually
  exclusive (D6);
- a correct step clears the old target and lights the next immediately, in-process — LED updates do
  not wait for browser polling;
- practice stop, completion, failure, and application shutdown clear the practice source
  deterministically;
- LED transport unavailability degrades ONLY the visualization: the logical session continues,
  targets remain readable through API/UI, and practice never crashes or loses state because LEDs
  disconnected;
- outside an active practice session the adapter behaves exactly as P014-HF1 shipped it (mode
  blending, physical mirroring, playback visualization, HF1 re-enable contract all unchanged).

### D6 — Activity exclusivity: reuse the appliance-wide advisory lock

The existing `/run/lock/piano-core-playback.lock` `flock` (P012) is GENERALIZED in meaning to
appliance ACTIVITY ownership: at most one of {automated playback, guided practice} may be active
appliance-wide, cross-process. The lock path and file name are deliberately unchanged (no
migration risk); the documented meaning is broader.

- starting practice while the service-side PlaybackManager session is non-terminal → typed
  `activity_conflict` (409);
- starting practice while the CLI (`piano-core-play`) or any other process holds the flock → typed
  `activity_conflict` (409);
- starting playback (API or CLI) while practice holds the lock → the EXISTING deterministic
  playback conflicts (`playback_already_active` 409 from the manager path / the CLI's existing
  lock error). The P012 playback API surface is not modified;
- practice releases ownership on stop, completion, failure, and application shutdown. The lock is
  an `flock` on an open descriptor: a crashed process can never permanently lock the appliance.

### D7 — Silent practice (audio policy)

Guided practice NEVER sends automated MIDI output. The practice subsystem holds no reference to the
MIDI output adapter, never constructs `RtMidiOut`, never emits NOTE_ON/OFF/SUSTAIN/STOP/PANIC, and
`POST /api/v1/practice/stop` is NOT a P009 STOP/PANIC — there is nothing to release because
practice owns no sounding notes. The learner hears the Roland's own key sound only. PANIC is not
exposed through practice.

### D8 — API surface (bounded, versioned, P012 envelope discipline)

- `GET /api/v1/practice` → honest snapshot: `available`, `state`, `song_id`, `title`,
  `step_index` (0-based), `total_steps`, `completed_steps`, `incorrect_attempts`, `progress`,
  `target` (`notes`, `names`, `chord`, `onset_seconds`) or `null`, `started_at`, `ended_at`,
  `error`, `midi_connection`. `None`-session payloads report `available:false` honestly.
- `POST /api/v1/practice/start` `{"song_id": "..."}` → 200 session payload. Deterministic errors:
  400 `invalid_song_id`/`malformed_request`; 404 `song_not_found`; 410 `song_source_missing`;
  422 `song_unplayable` (unparseable OR zero keyboard targets — the message distinguishes);
  503 `practice_unavailable` (library unavailable); 409 `practice_already_active`;
  409 `activity_conflict` (playback active / appliance lock held).
- `POST /api/v1/practice/stop` → 200 payload + `stopped:boolean`; idempotent.
- NOT added: skip-step, scoring, grading, target injection, raw paths, arbitrary MIDI input,
  direct LED control through practice, restart (a fresh `start` with the same song_id already
  covers it; a restart endpoint would duplicate that surface without materially improving UX).

### D9 — UI workflow

The P013 UI gains a clearly distinct PRACTICE action on the selected-song panel and a bounded
Practice panel: song name, live state, `Step x / y`, expected note names (chords joined with `+`),
progress, Stop Practice, completion message, and degraded feedback (LED unavailable / MIDI input
disconnected). While practice is `WAITING`, normal PLAY is disabled (conflict-safe); Practice
start is disabled while playback is active. Raw filesystem paths are never shown; the physical LED
keyboard remains the primary visual target surface; no sheet music, no piano roll, no virtual
keyboard. State arrives through the existing 1 s bounded poll (no WebSockets).

## 3. Consequences

- **Positive:** the appliance becomes a real self-paced learning instrument with zero new runtime
  dependencies, zero new threads, zero duplicated MIDI parsing, and unchanged safety surfaces.
- **Positive:** exclusivity is enforced appliance-wide (CLI + API + practice) by one mechanism that
  already has cross-process crash semantics.
- **Limitation (accepted):** exact-onset grouping trusts the canonical timeline's float onsets;
  notes at distinct ticks with coincidentally equal computed times would merge deterministically.
  Evidence-driven tolerance is future work with new tests + ADR amendment.
- **Limitation (accepted):** single-color LEDs cannot distinguish target keys from physical
  mirroring; during practice only targets are shown (D5).
- **Limitation (accepted):** no timing scoring, fingering, hand assignment, sections, history —
  out of scope by Owner directive; later authorized tasks only.
- **Neutral:** the advisory lock's documented meaning is broader than its filename suggests; the
  filename is kept to avoid a migration.
