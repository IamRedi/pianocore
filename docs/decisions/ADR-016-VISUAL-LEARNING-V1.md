# ADR-016 — Visual Learning System V1

- **Status:** IMPLEMENTED LOCALLY / PENDING INDEPENDENT REVIEW
- **Task:** PIANO-UI-005
- **Owner authorization:** `USER_REQUEST_PIANO-UI-005-VISUAL-LEARNING-SYSTEM` (2026-09-18)
- **Builds on:** ADR-014-PREMIUM-UI-V2 (canonical timeline §4, synchronization §6,
  renderer boundaries §9), ADR-015-LEARNING-ENGINE-V2

## 1. Context

UI-002/003/004 established the professional player (authoritative anchor model
with bounded local interpolation), the live 88-key presentation (one canonical
key geometry, physical + playback + expected sources) and Learning Engine V2
(Core-owned targets, sections, repetitions, outcomes). No visual score surface
existed: the browser had no access to note onsets/durations at all, and the P009
timeline itself deliberately retains only ordered song-time output commands
(no track IDs, tempo map, meter, measures, beats, hands or fingering — recorded
in ADR-014 §1.1 and ADR-015 §1).

## 2. Decision

### D1 — One canonical score projection at the Core boundary

`piano_core.playback.score.build_score_projection` pairs the canonical P009
timeline into immutable note spans (onset, duration, pitch, channel, velocity)
with deterministic pairing rules (same-key re-strike clips at the new onset;
unpaired offs are counted and dropped; unterminated ons close at song duration).
`ScoreService` caches projections per resolved source path (bounded LRU) and
warms them through an injected `load_timeline` wrapper used by playback and
practice — the accepted managers are unchanged. `GET /api/v1/score/{song_id}`
exposes the projection read-only (bounded: typed 413 beyond 20,000 spans).
No renderer parses raw MIDI; chunked transfer stays a future authorized
extension.

### D2 — Honest notation scope

Full engraved notation requires meter/beat/voice data that does not
canonically exist. V1 therefore renders the strongest truthful notation: a
sheet-oriented view with pitch rows (highest on top), a song-SECONDS ruler,
note spans, playhead, Learning target highlight and section bands. The payload
carries a machine-readable `notation` capability contract (supported vs
unsupported dimensions); measures, beats, staves, hands, fingering, voices,
phrasing and enharmonic spelling are explicitly deferred and never fabricated.

### D3 — Falling notes on the accepted presentation clock

Falling notes reuse the accepted UI-002 anchor model verbatim
(`createPlaybackAnchor` / `playbackFrame` / `reconcilePlaybackAnchor`):
authoritative position + bounded local interpolation, rAF-driven presentation
that never creates musical time. Pause/stop/completion freeze; stale anchors
(age > 3.5 s) freeze on labeled stale truth — the position source keeps the
known song identity and reports `stale` instead of pretending no song exists.
While Learning WAITING owns the appliance activity, the visual position is the
current target's canonical onset (self-paced truth; the stream never advances
musical time on its own).

### D4 — One keyboard geometry, exact strike-line alignment

All visual X coordinates derive from the accepted UI-003 `buildPianoKeyModel`
via `noteCenterFraction`/`noteWidthFraction` (white centers at
`(whiteIndex + 0.5)/52`; black keys at their `leftPercent` boundary). The
falling renderer draws its 88-key strip with the same fractions, so a note
crossing the strike line maps exactly to its canonical MIDI 21..108 key. The
DOM live keyboard (UI-003) is untouched; the canvas strip consumes the same
authoritative LiveNote sources (physical/playback/both) plus the Learning
expected-target overlay from Learning snapshots only.

### D5 — Mode switching is visual state only

Sheet / Falling / Hybrid toggle pane visibility (localStorage preference).
Switching performs no fetch, no command, no re-anchor and never resets
playback or Learning state. Hybrid shows both panes sharing one frame.

### D6 — Rendering technology: Canvas 2D behind one boundary

Canvas 2D (HiDPI-aware, quality-scale aware, ResizeObserver-stable) is
sufficient for the measured note counts (viewport-culled binary-search slices;
measured p95 frame preparation ≤ 1 ms at 2,100 notes in real Chromium). DOM/SVG
per-note nodes were rejected for scale; GPU (WebGL/WebGPU/Three.js) was
rejected as unjustified novelty for this milestone (ADR-014 §8.1 priority
order). Renderers live in `visualizer.js` behind an injected-mapper boundary
(one module direction; no second geometry); a future GPU renderer replaces
them without touching musical schemas. Quality degradation is plumbed
(resolution scale 1/0.75/0.5, effects flag) without a profile subsystem — full
profiles remain UI-009 scope.

### D7 — Recovery and honest states

Page reload, network interruption, browser sleep and reconciliation
reconstruct the visual from authoritative song identity, playback position and
Learning snapshot only (no visual history). Explicit states: no-song, loading,
score-unavailable (playback continues unaffected), empty (zero playable
keyboard notes), stale (frozen, labeled) and ready. Zero-duration spans keep a
minimum visual height; degenerate geometry (zero height/rate/duration) yields
empty windows, never divide-by-zero or infinite scroll.

## 3. Consequences and limits

- Sheet/falling/hybrid are presentation-only; no renderer grades, schedules,
  derives targets or advances Learning.
- Percussion-channel and out-of-keyboard notes remain in the projection
  (they are real output events) and are counted; presentation filters to
  21..108 exactly like the accepted live-note payload.
- Cold score fetches for never-played songs parse on demand (honest bounded
  latency); active songs are cache-warm via the loader wrapper.
- No seek, loop, live tempo, track/hand selection, fingering, 3D hands or
  UI-006 work is introduced.
- Independent review and Owner acceptance are required before this ADR or
  UI-005 can become accepted.

## 4. Bounded U5-F1–F9 remediation clarification

The independent review verdict FAIL is preserved; these implementation
clarifications do not establish acceptance. Span publication sorts by
(onset_seconds, channel, pitch, original close index), then assigns contiguous
onset-order indices. Playback timeline ordering is unchanged. Construction
refuses span 20,001 before allocation. A successful canonical loader call
unconditionally invalidates the previous same-path projection; a runtime
generation prevents an in-flight older projection from being published.
This adds no parser or Playback/Practice semantics.
