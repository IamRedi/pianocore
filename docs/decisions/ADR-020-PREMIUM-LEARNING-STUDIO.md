# ADR-020 — Premium Learning Studio (U8-P7)

Status: PROPOSED — implemented; independent architecture review PASS;
Owner acceptance pending. Review: `PIANO-UI-008-U8-P7-INDEPENDENT-REVIEW.md`.
Owner mandate: `PIANO-UI-008-U8-P7-AUTHORIZATION.txt` (2026-10-09).
Base: `81533264422795d2d38f74357bc6f098214bde16`, `feat/learn-library`.

## Authority and boundaries

`piano_core.practice.PracticeEngine` remains the single learning lifecycle,
with the existing PracticeManager owning the appliance activity flock, receiving
post-StateStore physical observations and publishing target LEDs. Wait semantics
remain unchanged. Flow is an additive strategy in that lifecycle, never Player
playback or a browser grading system. Core remains silent during both modes.

Canonical P009 seconds already incorporate MIDI tempo changes. Imported packages
are hash-validated against that canonical timeline by M5. Flow consumes that same
PracticePlan, grouping exact-onset notes as musical events. No new MIDI parser,
beat reconstruction, notation truth or human hand attribution is introduced.

This proposal explicitly extends ADR-015's deferred timing scope and the
self-paced-only timer restriction in ADR-PRACTICE-V1 for Flow. It does not alter
Wait correctness, the frozen architecture baseline or the Player scheduler.
Independent architecture review must assess this extension before release.

## Proposed timing contract (fixed before tests)

- Injectable Core monotonic clock, shared time basis with canonical MIDI input
  timestamps. Clock state is runtime-only and bound to the learning session.
- Two-second count-in; origin maps song time zero. A fixed start-time speed in
  [0.25, 1.0] scales the entire canonical seconds timeline, preserving tempo-map
  proportions. Live tempo mutation is not offered.
- Attack acceptance window: +/-180 ms wall time; on-time band +/-60 ms;
  earlier/later accepted attacks report EARLY/LATE. These are transparent product
  tolerances, not scientific or medical performance measures.
- Chords: each expected pitch needs its own fresh attack in the same event's
  window, with all chord keys concurrently held and attack spread <=180 ms.
  Release ends physical occupancy. Sustained sound/pedal never fabricates a
  physical attack. Duration/release grading is not claimed without a dedicated
  contract; source durations remain accurately rendered.
- Repeated and overlapping pitches: one fresh physical press matches at most one
  unresolved target, nearest eligible onset then canonical ordinal as the stable
  tie-break. A held key cannot satisfy another event; each target resolves once.
- Targets expire as missed after their late deadline. Musical time continues
  without student input; successful attacks do not seek or advance the clock.
- Completion waits for canonical endpoint AND the last late window. Results
  retain raw counts and signed timing errors; note/event accuracy uses explicitly
  named denominators. Wait never reports a timing score.
- Core pause freezes the timeline and ownership; resume realigns the monotonic
  anchor, discards pending chord attempts and requires fresh releases/presses.
  Input disconnect pauses Flow visibly. Reconnect never automatically resumes.
- Core stop/failure/completion clears practice LEDs and releases activity through
  the existing lifecycle hook. Core restart ends the session, no session replay.
- A bounded manager heartbeat resolves expiry/completion independently of HTTP
  clients. Physical observations use captured timestamps; clock tick/input order
  are serialized by the engine lock. A 50 ms ingress grace prevents clock expiry
  from racing an in-budget late captured attack. Greater attack delay fails the
  session visibly rather than rewriting published misses. Clock and DB workers
  are separate. Full-lesson Flow only: section requests are explicitly rejected
  until a section-relative clock contract is independently reviewed.

## Presentation and persistence

Academy consumes only Learn catalog and package progress. Course/level completion
and Continue Learning must derive from persisted package sessions mapped through
lesson/package identity. Generated catalog contents stay outside Git. Catalog
pagination must allow access to all 1,612 lessons with bounded DOM.

Studio uses the existing single scene and renderer instances. Fullscreen is a
presentation state, with a native user-gesture request and full-viewport fallback;
Escape, resize, view and range changes issue no musical command. Exiting an
active Studio requires an explicit safe Stop path; switching visual modes does
not. Hands renderer/GLB/rig/IK/MotionEngine are untouched.

Focus geometry is a bounded view of the accepted 88-key geometry. It is stable
for a passage, with Full 88 always available; no geometry affects grading.
Renderer interpolation follows fresh Core clock samples and freezes on stale
state. A visual frame never changes authoritative state or scores an input.

Package sessions remain the persisted progress authority. Terminal results must
be recorded idempotently without browser dependence; no DB I/O on the MIDI
callback. Failed results remain retained in memory, block new practice, and have
an explicit idempotent retry action. Pending writes are exposed; no save-success
claim precedes a successful database transaction. A storage failure does not
promise survival across a process crash; it is a visible release blocker.

Only explicitly scoped full-lesson Wait/Flow completions qualify for Academy
completion. Flow completion means reaching the full endpoint, even with misses;
it is not mastery or a passing grade. Partial hands, sections and other modes
remain attempts. Historical
sessions without scope proof are preserved and never inferred as full completion.
Core monotonic elapsed time replaces browser-dependent duration reporting.
Existing sessions and catalog rows must survive wheel rollback.

## Release evidence

Required: deterministic clock and matching controls, real API/StateStore
integration, Wait and Player regressions, zero new failures, browser interactions
and inspected screenshots, tablet-size geometry and frame measurements,
independent code/architecture review, wheel byte identity, appliance safe-state,
governed helper deployment with rollback, real MIDI/LED machine evidence and
separate Owner physical keypress/tablet confirmation.

Targets: controls >=44 px, no horizontal overflow at declared tablet/portrait
viewports, no presentation reset, bounded DOM/animation/listeners. Frame pacing
and latency are measurements scoped to the tested browser/device; physical
latency and 60 FPS are never inferred from headless synthetic results.


## R1 release reliability note — 2026-10-10

ADR status and Owner acceptance remain unchanged. The R1 catalog correction owns complete SQLite operations with a reentrant lock; progress callbacks stay outside it. Native catalog-only controls and full local tests passed, but the corrected real Pi release failed with process descriptor exhaustion and was rolled back. Transaction context ownership is insufficient for PackageStore connection lifetime; explicit closure and full progress-fanout resource tests are a proposed separate bounded remedy. Do not infer appliance readiness from catalog-only concurrency PASS. See R1 evidence.


## R7 source ownership correction — 2026-10-13

This clarification preserves the accepted product architecture. The full identity
`package:<id>` denotes a Learn/Practice package; the unprefixed Gold song identity
belongs to Player. Content provenance (`pianio`, `mutopia`) is a different field.
The UI's explicit discriminated boundary validates this distinction before Gold
metadata or Play requests. Learn metadata comes from the existing canonical lesson
contract, never Gold detail or an additional library service.

The existing `PlayerCoordinator.selectedSong` remains the single explicit next
selection. A shared cancellable generation orders Academy, Recent, reload and
route intents. Learn detail also checks its local request token, requested lesson
ID and package identity before committing. Pending work is canceled when a newer
selection, clear, or incompatible route wins. Accepted Core playback metadata is
separate: Gold A may still be playing while the user selects package B next.
Hydration follows Core's full Gold identity and its own token, without replacing
that explicit next selection. Typed metadata errors remain visible and latched
across routine polls; a successful explicit same-Gold selection or genuine fresh
reconnect permits bounded revalidation. Score/fingering cache keys retain their
existing full song identity and canonical source version.

A tab-local sessionStorage hint contains only selected lesson/package identity,
never authoritative metadata, progress, or musical state. Reload validates it
against canonical Learn detail and two valid, corresponding Core Learning
snapshots. A Learning command generation change or rejected/stale verification
cannot authorize a commit. An active WAITING Core package has precedence; an
inactive terminal result does not overwrite a newer explicit lesson hint.
Home Continue can restore an active package after initial Home boot without
issuing another Start. Reconnect restoration preserves a newer explicit selection.

R7 verification and independent review are recorded separately from appliance
release and Owner acceptance. Historical R6 wrong-namespace 404 failures remain
unchanged; this note does not itself establish release PASS.

R7 independent D04 remediation keeps typed selection/recovery feedback on the
visible Academy surface as well as Studio's existing Practice surface. A valid
new selection clears that selection failure; unrelated command errors retain
their existing handling. Actual browser checks require rendered geometry and
visibility through all ancestors, rather than only the element's hidden flag.

## R7 release evidence update — 2026-10-14

The implementation/source isolation, exact artifact/procedure, real Pi engineering release and final publication/recovery audits PASS. Premium is installed according to the final read-only checkpoint2026-10-14T09:48:39.998730Z; product5e46288/wheelee394f14. Release checkpointb6c4edb and later documentation-sync publication remain distinct from installed product identity. See live handoff and independent publication/recovery closure.

This evidence update does not accept or amend architectural decisions. Owner acceptance remains pending; physical playing/listening/tablet NOT_RUN, CI NOT_VERIFIED; Core authority and existing Hands behavior unchanged, paused3DHands work not reopened. No successor or main merge.
