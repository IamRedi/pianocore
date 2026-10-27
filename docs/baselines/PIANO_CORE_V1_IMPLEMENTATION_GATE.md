# PIANO CORE V1 IMPLEMENTATION GATE

Implementation status: NOT STARTED

Implementation may begin only against `PIANO_CORE_V1_FROZEN_SPEC.md`
(PIANO CORE V1 SPECIFICATION — FROZEN v1.0, consistency check: PASS).

Status fields:

- Planning: COMPLETE
- Architecture: FROZEN v1.0
- Implementation: NOT STARTED
- Hardware currently connected: NOT REQUIRED FOR ARCHIVE
- Production validation: PENDING

Rules of use:

- Every line is PASS/FAIL at implementation time; nothing may be skipped, merged, or reinterpreted.
- Labels: *(INHERITED-PRESERVE)* = proven deployed behavior to preserve verbatim;
  *(NEW)* = to implement; *(COMPAT)* = compatibility-surface obligations;
  *(VERIFY)* = requires physical verification; *(DISCARD-LEGACY)* = legacy intentionally not carried.
- No line may be marked PASS without actual implementation evidence (code + test/verification output).
- No line may be marked PASS by virtue of the old PLUTO/RediON system having worked, unless the line is explicitly labeled INHERITED-PRESERVE.

## Core runtime

- [ ] Single `piano-core` service owns all state; helpers stateless — PASS/FAIL
- [ ] State model == spec §4; public/internal/derived separation enforced; per-source held-note bookkeeping present — PASS/FAIL *(NEW)*
- [ ] Runtime state never persisted; restart yields clean state — PASS/FAIL
- [ ] Deployed runtime version observable via API — PASS/FAIL *(NEW)*

## MIDI/playback

- [ ] Roland input path incl. route-recovery preserved — PASS/FAIL *(INHERITED-PRESERVE)*
- [ ] MIDI-file playback: tempo/transpose/CC7 volume behavior preserved — PASS/FAIL *(INHERITED-PRESERVE)*
- [ ] Canonical `piano.playback.stop`: cancels automated playback, releases automated-tracked notes only, playback→idle, no global sweep, human state untouched — PASS/FAIL *(NEW semantics; legacy full-panic stop confined to compat surface)*
- [ ] `piano.all_notes_off`: global panic per §11 intent; distinct from stop — PASS/FAIL *(INHERITED-PRESERVE, renamed canonical)*
- [ ] Roland disconnect: abort scheduling, clear automated state, cleanup only if transport available, report `rolandUnavailable`, reconnect; best-effort startup reset distinguished from guaranteed cleanup — PASS/FAIL *(NEW)*
- [ ] `piano.note.play` / `piano.note.release` / `piano.chord.play` implemented + verified — PASS/FAIL *(NEW)*
- [ ] `piano.sustain.set` implemented + Roland-verified — PASS/FAIL *(NEW + VERIFY)*
- [ ] Source labels `human`/`automated` on all note events — PASS/FAIL *(INHERITED, renamed canonical)*
- [ ] Legacy implicit stop-then-play and legacy full-panic stop confined to compat surface; canonical returns `playbackBusy`/scoped stop — PASS/FAIL *(COMPAT)*

## API

- [ ] Canonical HTTP commands/queries + WS events per §7; identity per §8 — PASS/FAIL *(NEW)*
- [ ] Typed error vocabulary == §9 incl. `rolandUnavailable` disconnect usage; no ALSA/USB strings in public errors — PASS/FAIL *(NEW)*
- [ ] Legacy :8766 adapter preserves deployed behavior with documented deviations; HUGO uses canonical only — PASS/FAIL *(COMPAT)*
- [ ] Bounded broadcast / slow-client drop preserved — PASS/FAIL *(INHERITED-PRESERVE)*

## LED subsystem

- [ ] Policy/driver split per §12; transport replaceable, no public dependency — PASS/FAIL
- [ ] `piano.led.scene.set` exposed as public semantic presentation capability; driver/WS2812/key-map/transport fully internal — PASS/FAIL
- [ ] `hardware/led/keyboard_mapping.csv` is the canonical 88-row mapping; firmware derives from it — PASS/FAIL
- [ ] Panic/clear intent clears LEDs; LED failure/`ledUnavailable` never affects notes/playback — PASS/FAIL

## UI

- [ ] UI is API client only; no state ownership; redesign needs no API change — PASS/FAIL
- [ ] Minimum functional surfaces present (§14); old LVGL design absent — PASS/FAIL *(DISCARD-LEGACY: LVGL UI)*

## HA/HUGO boundaries

- [ ] HA via external MQTT/REST adapter on semantic state only (incl. scene capability); zero HA logic in core — PASS/FAIL
- [ ] HUGO boundary == §16; no HUGO-specific code in core — PASS/FAIL

## Deployment

- [ ] One canonical source tree defines deployable version; whole-version deploys; no partial rsync — PASS/FAIL *(NEW; supersedes `sync-to-pi.sh`)*
- [ ] Dependencies pinned; config separated; version observable — PASS/FAIL

## Docs/tests

- [ ] CONTRACT.md (canonical + legacy compat incl. stop/panic deviation), RELIABILITY.md (§22), DECISIONS.md, adapted runbooks — PASS/FAIL
- [ ] Non-blocking emit regression test + canonical API tests + stop/panic distinction tests — PASS/FAIL

## Hardware verification

- [ ] Exact Pi board model recorded as hardware documentation — PASS/FAIL *(VERIFY)*
- [ ] §23 NEW-V1 items each verified on the reconnected rig — PASS/FAIL
- [ ] Production re-validation smoke pass executed — PASS/FAIL
