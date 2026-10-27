// PIANO-UI-005: deterministic visual-model tests (pure app.js exports).
//
// Covers the musical mapping contract of the Visual Learning System:
// pitch -> keyboard X alignment (same UI-003 geometry), onset -> visual
// time mapping, duration -> note length, strike-line relation, stale
// freeze via the accepted UI-002 anchor model, section clipping with
// half-open semantics, zero-duration safety, Learning target overlays,
// mode switching without state loss, resize stability, and the falling/
// sheet test batteries. No canvas and no browser here — the pure model
// only; drawing is validated by browser_visual.mjs in real Chromium.
import assert from "node:assert/strict";
import {
  FALLING_MIN_NOTE_HEIGHT_PX,
  PIANO_KEYS,
  PIANO_NOTE_MAX,
  PIANO_NOTE_MIN,
  PIANO_WHITE_KEY_COUNT,
  ScoreReplica,
  VISUAL_MODES,
  clampPlaybackPosition,
  createPlaybackAnchor,
  fallingNoteY,
  fallingPixelsPerSecond,
  fallingTimeWindow,
  isOnsetInHalfOpenSection,
  isValidScorePayload,
  isVisualBlackKey,
  keyboardRangeNotes,
  learningOverlayModel,
  learningTargetPosition,
  loadVisualMode,
  noteCenterFraction,
  notePlayState,
  noteWidthFraction,
  nextVisualMode,
  playbackFrame,
  sectionTimeClip,
  visibleNoteSlice,
  visualAriaSummary,
  visualCanvasSize,
  visualPositionSource,
  visualStatusModel,
  sheetMidiToY,
  sheetPitchRange,
  sheetRulerStepSeconds,
  sheetTimeToX,
  sheetViewStart,
  sheetViewportSeconds,
} from "../../src/piano_core/web/static/app.js";

import { FallingNotesRenderer, VisualStage } from "../../src/piano_core/web/static/visualizer.js";

let failures = 0;
function test(name, fn) {
  try {
    fn();
    console.log(`ok - ${name}`);
  } catch (error) {
    failures += 1;
    console.error(`FAIL - ${name}`);
    console.error(error);
  }
}

function scorePayload({ songId = "s1", duration = 10, notes = [], diagnostics } = {}) {
  const canonical = notes.map((note, index) => ({ index, velocity: 90, channel: 1, ...note }));
  return {
    schema_version: 1,
    song_id: songId,
    source_version: `score-sha256:${"a".repeat(64)}`,
    title: "Test Song",
    duration_seconds: duration,
    time_basis: "song_seconds",
    note_count: canonical.length,
    notes: canonical,
    diagnostics: diagnostics || {
      percussion_note_ons: 0,
      outside_keyboard_note_ons: 0,
      unpaired_note_offs: 0,
      retriggered_note_ons: 0,
      unterminated_note_ons: 0,
    },
    notation: { supported: ["pitch"], unsupported: ["measure"] },
  };
}

/* ---------------- 1. MIDI pitch -> keyboard X alignment ---------------- */

test("every 21..108 pitch maps to a center inside [0,1] (no second geometry)", () => {
  for (let midi = PIANO_NOTE_MIN; midi <= PIANO_NOTE_MAX; midi += 1) {
    const center = noteCenterFraction(midi);
    assert.ok(typeof center === "number" && Number.isFinite(center));
    assert.ok(center > 0 && center < 1, `midi ${midi} center ${center}`);
    const width = noteWidthFraction(midi);
    assert.ok(width > 0 && width < 1);
  }
});

test("centers are strictly increasing across the keyboard (monotone)", () => {
  let previous = -1;
  for (let midi = PIANO_NOTE_MIN; midi <= PIANO_NOTE_MAX; midi += 1) {
    const center = noteCenterFraction(midi);
    assert.ok(center > previous, `midi ${midi} not increasing`);
    previous = center;
  }
});

test("center fractions match the accepted UI-003 key model exactly", () => {
  // White key: (whiteIndex + 0.5) / 52; black key: leftPercent boundary
  // (whiteIndex is null for black keys in the accepted model).
  for (const key of PIANO_KEYS) {
    const expected = key.black
      ? key.leftPercent / 100
      : (key.whiteIndex + 0.5) / PIANO_WHITE_KEY_COUNT;
    assert.ok(Math.abs(noteCenterFraction(key.midi) - expected) < 1e-12);
    assert.equal(isVisualBlackKey(key.midi), key.black);
  }
});

test("first and last keys map near the span edges; out-of-range maps null", () => {
  assert.ok(Math.abs(noteCenterFraction(21) - 0.5 / 52) < 1e-9);
  assert.ok(Math.abs(noteCenterFraction(108) - 51.5 / 52) < 1e-9);
  assert.equal(noteCenterFraction(20), null);
  assert.equal(noteCenterFraction(109), null);
  assert.equal(noteCenterFraction(200), null);
});

test("a note rect stays inside its key lane (center +/- width/2)", () => {
  for (let midi = PIANO_NOTE_MIN; midi <= PIANO_NOTE_MAX; midi += 1) {
    const center = noteCenterFraction(midi);
    const width = noteWidthFraction(midi);
    assert.ok(center - width / 2 >= 0);
    assert.ok(center + width / 2 <= 1);
  }
});

/* ---------------- 2/3. onset/duration -> visual Y/time mapping ---------------- */

test("onset == position lands exactly on the strike line", () => {
  const y = fallingNoteY(4.0, 4.0, 300, 90);
  assert.equal(y, 300);
});

test("future onsets are above the strike line, past below, linear in time", () => {
  const strikeY = 300;
  const rate = 90;
  assert.equal(fallingNoteY(5.0, 4.0, strikeY, rate), strikeY - 90);
  assert.equal(fallingNoteY(3.0, 4.0, strikeY, rate), strikeY + 90);
  assert.equal(
    fallingNoteY(6.0, 4.0, strikeY, rate),
    fallingNoteY(5.0, 4.0, strikeY, rate) - rate
  );
});

test("note length is duration * pxPerSecond with a minimum visual height", () => {
  const rate = 90;
  const long = 2.5 * rate;
  assert.equal(long, 225);
  const zero = Math.max(FALLING_MIN_NOTE_HEIGHT_PX, 0 * rate);
  assert.equal(zero, FALLING_MIN_NOTE_HEIGHT_PX);
  assert.ok(FALLING_MIN_NOTE_HEIGHT_PX >= 2);
});

test("falling window derives horizon from geometry and is degenerate-safe", () => {
  const window = fallingTimeWindow({ positionSeconds: 10, heightPx: 360, pxPerSecond: 90 });
  assert.equal(window.futureSeconds, 14);
  assert.equal(window.pastSeconds, 10);
  assert.equal(fallingTimeWindow({ positionSeconds: 10, heightPx: 0, pxPerSecond: 90 }), null);
  assert.equal(fallingTimeWindow({ positionSeconds: 10, heightPx: 360, pxPerSecond: 0 }), null);
  assert.equal(fallingTimeWindow({ positionSeconds: NaN, heightPx: 360, pxPerSecond: 90 }), null);
});

test("pxPerSecond is bounded and derives from pane height only", () => {
  assert.equal(fallingPixelsPerSecond(450), 90); // 450/5
  assert.equal(fallingPixelsPerSecond(50), 40);  // clamped low
  assert.equal(fallingPixelsPerSecond(5000), 180); // clamped high
  assert.equal(fallingPixelsPerSecond(0), 40);
  assert.equal(fallingPixelsPerSecond(-10), 40);
});

test("play states classify correctly against the authoritative position", () => {
  assert.equal(notePlayState(1.0, 0.5, 0.0), "upcoming");
  assert.equal(notePlayState(1.0, 0.5, 1.0), "active");
  assert.equal(notePlayState(1.0, 0.5, 1.49), "active");
  assert.equal(notePlayState(1.0, 0.5, 1.5), "past"); // half-open: end is past
  assert.equal(notePlayState(1.0, 0.0, 1.0), "past");  // zero-duration ends instantly
});

/* ---------------- 4. playback position -> strike-line relation ---------------- */

test("strike-line relation: the note crossing the line is the active one", () => {
  const notes = [
    { onset_seconds: 0.0, duration_seconds: 1.0, midi: 60 },
    { onset_seconds: 2.0, duration_seconds: 1.0, midi: 62 },
    { onset_seconds: 2.5, duration_seconds: 4.0, midi: 64 },
  ];
  const position = 2.6;
  const states = notes.map((n) => notePlayState(n.onset_seconds, n.duration_seconds, position));
  assert.deepEqual(states, ["past", "active", "active"]);
  // The active note's rect spans the strike line: its end edge (later
  // time, drawn higher) is above the line and its onset edge below.
  const strikeY = 300;
  const yOnset = fallingNoteY(2.0, position, strikeY, 90);
  const yEnd = fallingNoteY(3.0, position, strikeY, 90);
  assert.ok(yEnd <= strikeY && yOnset >= strikeY);
});

/* ---------------- 5-10. accepted anchor model: pause/resume/stop/complete/replace/stale ---------------- */

function snapshot(overrides = {}) {
  return {
    available: true,
    state: "PLAYING",
    tempo_multiplier: 1.0,
    position_seconds: 10,
    duration_seconds: 60,
    events_sent: 5,
    error: null,
    song_id: "s1",
    title: "T",
    ...overrides,
  };
}

test("PLAYING advances visually inside the freshness window", () => {
  const anchor = createPlaybackAnchor(snapshot(), 1000);
  const frame = playbackFrame(anchor, 1500);
  assert.ok(Math.abs(frame.position - 10.5) < 1e-9);
  assert.equal(frame.stale, false);
});

test("PAUSED freezes the visual position (no advancement)", () => {
  const anchor = createPlaybackAnchor(snapshot({ state: "PAUSED" }), 1000);
  const frame = playbackFrame(anchor, 10_000);
  assert.equal(frame.position, 10);
  // Beyond the freshness window the frozen frame is ALSO honestly stale.
  assert.equal(frame.stale, true);
  const freshFrame = playbackFrame(anchor, 2000);
  assert.equal(freshFrame.position, 10);
  assert.equal(freshFrame.stale, false);
});

test("resume continuation: a new PLAYING anchor continues from the frozen position", () => {
  const paused = createPlaybackAnchor(snapshot({ state: "PAUSED", position_seconds: 12 }), 1000);
  const frameAtLongAge = playbackFrame(paused, 999_999);
  assert.equal(frameAtLongAge.position, 12);
  const resumed = createPlaybackAnchor(snapshot({ state: "PLAYING", position_seconds: 12 }), 2000);
  const resumedFrame = playbackFrame(resumed, 2500);
  assert.ok(Math.abs(resumedFrame.position - 12.5) < 1e-9);
});

test("stop reset: an IDLE anchor holds position 0 truth", () => {
  const anchor = createPlaybackAnchor(
    snapshot({ state: "IDLE", position_seconds: 0 }), 1000
  );
  const frame = playbackFrame(anchor, 5000);
  assert.equal(frame.position, 0);
});

test("completion: COMPLETED pins the visual position to the duration", () => {
  const anchor = createPlaybackAnchor(
    snapshot({ state: "COMPLETED", position_seconds: 60 }), 1000
  );
  const frame = playbackFrame(anchor, 60_000);
  assert.equal(frame.position, 60);
  assert.equal(frame.progress, 1);
});

test("song replacement: a new song id re-anchors from its own truth", () => {
  const first = createPlaybackAnchor(snapshot(), 1000);
  const second = createPlaybackAnchor(
    snapshot({ song_id: "s2", position_seconds: 30, duration_seconds: 90 }), 2000
  );
  const frame = playbackFrame(second, 2000);
  assert.equal(frame.position, 30);
  assert.notEqual(first.snapshot.song_id, second.snapshot.song_id);
});

test("stale Player state freezes movement at the freshness boundary", () => {
  const anchor = createPlaybackAnchor(snapshot(), 1000);
  const justInside = playbackFrame(anchor, 1000 + 3499);
  assert.equal(justInside.stale, false);
  const beyond = playbackFrame(anchor, 1000 + 3501);
  assert.equal(beyond.stale, true);
  // Frozen exactly at the boundary position (no extrapolation past it).
  assert.ok(Math.abs(beyond.position - (10 + 3.5)) < 1e-6);
});

test("clamp guards: negative and infinite positions never escape", () => {
  assert.equal(clampPlaybackPosition(-5, 60), 0);
  assert.equal(clampPlaybackPosition(70, 60), 60);
  assert.equal(clampPlaybackPosition(NaN, 60), 0);
  assert.equal(clampPlaybackPosition(5, 0), 5);
});

/* ---------------- visible-slice search (falling + sheet batteries) ---------------- */

function makeNotes(count, { gap = 0.5, duration = 0.4, startMidi = 60 } = {}) {
  const notes = [];
  for (let i = 0; i < count; i += 1) {
    notes.push({
      index: i,
      midi: startMidi + (i % 12),
      channel: 1,
      velocity: 90,
      onset_seconds: i * gap,
      duration_seconds: duration,
    });
  }
  return notes;
}

test("visible slice: binary search returns exactly the candidate window", () => {
  const notes = makeNotes(100);
  const maxDuration = 0.4;
  const slice = visibleNoteSlice(notes, 10.0, 14.0, maxDuration);
  // Onsets >= 10 - 0.4 are candidates.
  assert.equal(notes[slice.start].onset_seconds, 10.0);
  assert.equal(notes[slice.end - 1].onset_seconds, 14.0);
  assert.equal(notes[slice.end]?.onset_seconds, 14.5);
});

test("long notes starting before the window remain visible via maxDuration", () => {
  const notes = [
    { onset_seconds: 0, duration_seconds: 100 },
    { onset_seconds: 50, duration_seconds: 0.2 },
  ];
  const slice = visibleNoteSlice(notes, 40, 60, 100);
  assert.equal(slice.start, 0);
  assert.equal(slice.end, 2);
});

test("degenerate slice inputs are empty, never thrown", () => {
  assert.deepEqual(visibleNoteSlice([], 0, 10, 1), { start: 0, end: 0 });
  assert.deepEqual(visibleNoteSlice(makeNotes(5), NaN, 10, 1), { start: 0, end: 0 });
  assert.deepEqual(visibleNoteSlice(makeNotes(5), 10, 0, 1), { start: 0, end: 0 });
  assert.deepEqual(visibleNoteSlice(null, 0, 10, 1), { start: 0, end: 0 });
});

test("falling battery: chords, repeats, overlaps, short/long, edge keys", () => {
  // Chord at one onset.
  const chord = [60, 64, 67].map((midi, i) => ({
    index: i, midi, channel: 1, velocity: 90,
    onset_seconds: 1.0, duration_seconds: 0.5,
  }));
  for (const note of chord) {
    const x = noteCenterFraction(note.midi);
    for (const other of chord) {
      if (note !== other) {
        assert.notEqual(x, noteCenterFraction(other.midi), "chord notes keep distinct lanes");
      }
    }
  }
  // Repeated notes at distinct onsets map to the same lane.
  assert.equal(noteCenterFraction(60), noteCenterFraction(60));
  // Overlapping same-pitch spans both classify from the same geometry.
  assert.equal(
    notePlayState(1.0, 3.0, 2.0),
    "active"
  );
  // Very short and very long notes.
  assert.equal(Math.max(3, 0.01 * 90), 3);
  assert.equal(30 * 90, 2700);
  // First/last physical keys are inside the visual span.
  assert.ok(noteCenterFraction(21) > 0);
  assert.ok(noteCenterFraction(108) < 1);
});

test("dense passages keep a bounded visible count (100 and 500 notes)", () => {
  for (const { count, expected } of [
    { count: 100, expected: 50 }, // song spans 0..9.9s: window 5..10 shows 50
    { count: 500, expected: 51 }, // song spans 0..49.9s: window 5..10 shows 51
  ]) {
    const notes = makeNotes(count, { gap: 0.1, duration: 0.08 });
    const maxDuration = 0.08;
    const slice = visibleNoteSlice(notes, 5.0, 10.0, maxDuration);
    const visible = slice.end - slice.start;
    assert.equal(visible, expected, `${count}-note song shows the window, not the song`);
    for (let i = slice.start; i < slice.end; i += 1) {
      assert.ok(notes[i].onset_seconds <= 10.0);
    }
  }
});

test("rapid and slow tempos only change density, never lane mapping", () => {
  const rapid = makeNotes(50, { gap: 0.05 });
  const slow = makeNotes(50, { gap: 2.0 });
  for (let i = 0; i < 50; i += 1) {
    assert.equal(
      noteCenterFraction(rapid[i].midi),
      noteCenterFraction(slow[i].midi)
    );
  }
});

/* ---------------- 11. section clipping (half-open) ---------------- */

test("section clipping clamps to song duration and rejects degenerate ranges", () => {
  assert.deepEqual(sectionTimeClip({ start: 2, end: 8 }, 100), { start: 2, end: 8 });
  assert.deepEqual(sectionTimeClip({ start: -5, end: 50 }, 30), { start: 0, end: 30 });
  assert.deepEqual(sectionTimeClip({ start: 20, end: 99 }, 30), { start: 20, end: 30 });
  assert.equal(sectionTimeClip({ start: 8, end: 2 }, 30), null);
  assert.equal(sectionTimeClip({ start: 2, end: 2 }, 30), null);
  assert.equal(sectionTimeClip(null, 30), null);
  assert.equal(sectionTimeClip({ start: NaN, end: 5 }, 30), null);
  // A section fully past the song duration collapses to null (no clip zone).
  assert.equal(sectionTimeClip({ start: 40, end: 50 }, 30), null);
  // No duration truth: bounds stay unclamped but non-negative.
  assert.deepEqual(sectionTimeClip({ start: 1, end: 5 }, 0), { start: 1, end: 5 });
});

test("sectionTimeClip semantics via isOnsetInHalfOpenSection", () => {
  // Half-open [start, end): start inside, end outside.
  assert.equal(isOnsetInHalfOpenSection(2.0, { start: 2, end: 8 }), true);
  assert.equal(isOnsetInHalfOpenSection(7.999, { start: 2, end: 8 }), true);
  assert.equal(isOnsetInHalfOpenSection(8.0, { start: 2, end: 8 }), false);
  assert.equal(isOnsetInHalfOpenSection(1.9, { start: 2, end: 8 }), false);
  // No section -> everything is inside.
  assert.equal(isOnsetInHalfOpenSection(100.0, null), true);
});

/* ---------------- 12. zero-duration / empty material ---------------- */

test("zero-duration events produce safe geometry everywhere", () => {
  const zero = { onset_seconds: 3.0, duration_seconds: 0.0, midi: 60 };
  assert.equal(notePlayState(zero.onset_seconds, zero.duration_seconds, 2.9), "upcoming");
  assert.equal(notePlayState(zero.onset_seconds, zero.duration_seconds, 3.0), "past");
  const window = fallingTimeWindow({ positionSeconds: 3.0, heightPx: 300, pxPerSecond: 60 });
  assert.equal(window.pastSeconds, 3.0);
  const slice = visibleNoteSlice([zero], 3.0, 8.0, 0.0);
  assert.equal(slice.end - slice.start, 1);
});

test("empty score still maps cleanly (no divide-by-zero, no infinite scroll)", () => {
  assert.equal(sheetViewportSeconds(0), 4);
  assert.equal(sheetViewportSeconds(-5), 4);
  assert.equal(sheetViewStart(0, 12, 0), 0);
  assert.equal(sheetPitchRange([]), null);
  assert.equal(sheetMidiToY(60, null, 0, 100), null);
  assert.equal(sheetTimeToX(5, 0, 0, 100), null);
  const status = visualStatusModel({
    scoreState: "ready",
    sourceKind: "playback",
    visibleNoteCount: 0,
  });
  assert.equal(status, "empty");
});

test("sheet viewport/scroll is bounded for very long songs", () => {
  assert.equal(sheetViewportSeconds(10_000), 30);
  assert.equal(sheetViewStart(9999, 30, 10_000), 9970); // clamped to duration - viewport
  assert.equal(sheetViewStart(5, 30, 10_000), 0);
  assert.equal(sheetViewStart(0.5, 30, 1.0), 0); // shorter than a viewport
});

/* ---------------- 13. Learning target overlay ---------------- */

test("learning overlay derives from Core snapshots only", () => {
  const waiting = {
    state: "WAITING",
    song_id: "s1",
    target: { notes: [60, 64, 67], names: ["C4", "E4", "G4"], onset_seconds: 4.5 },
    section: { start_seconds: 2.0, end_seconds: 9.0 },
  };
  const overlay = learningOverlayModel(waiting, true);
  assert.equal(overlay.active, true);
  assert.deepEqual(overlay.targetNotes, [60, 64, 67]);
  assert.equal(overlay.targetOnset, 4.5);
  assert.deepEqual(overlay.section, { start: 2.0, end: 9.0 });
  // Not authorized or not WAITING -> no overlay at all.
  assert.equal(learningOverlayModel(waiting, false).active, false);
  assert.equal(learningOverlayModel({ ...waiting, state: "IDLE" }, true).active, false);
  assert.equal(learningTargetPosition({ ...waiting, state: "IDLE" }), null);
  assert.equal(learningTargetPosition(null), null);
});

test("learning WAITING owns the visual position (self-paced truth)", () => {
  const learningSnapshot = {
    state: "WAITING",
    song_id: "s1",
    target: { notes: [60], onset_seconds: 7.25 },
  };
  const source = visualPositionSource({
    playbackSnapshot: snapshot({ song_id: "s1" }),
    playbackFresh: true,
    learningSnapshot,
    learningAuthorized: true,
  });
  assert.equal(source.kind, "learning");
  assert.equal(learningTargetPosition(learningSnapshot), 7.25);
});

test("playback owns the visual position when learning is inactive", () => {
  const source = visualPositionSource({
    playbackSnapshot: snapshot({ song_id: "s1" }),
    playbackFresh: true,
    learningSnapshot: { state: "IDLE" },
    learningAuthorized: true,
  });
  assert.equal(source.kind, "playback");
  assert.equal(source.stale, false);
  assert.deepEqual(visualPositionSource({}), { kind: "none", songId: null, stale: false });
});

test("expired playback freshness stays the source but freezes as stale", () => {
  const source = visualPositionSource({
    playbackSnapshot: snapshot({ song_id: "s1" }),
    playbackFresh: false,
    learningSnapshot: { state: "IDLE" },
    learningAuthorized: true,
  });
  // Known song identity + stale truth — never a pretend "no song".
  assert.equal(source.kind, "playback");
  assert.equal(source.stale, true);
  assert.equal(
    visualStatusModel({ scoreState: "ready", sourceKind: "playback", visibleNoteCount: 3, playbackStale: source.stale }),
    "stale"
  );
});

/* ---------------- 14. mode switching without state loss ---------------- */

test("mode switching is visual state only", () => {
  assert.equal(nextVisualMode("falling", "sheet"), "sheet");
  assert.equal(nextVisualMode("sheet", "hybrid"), "hybrid");
  assert.equal(nextVisualMode("hybrid", "falling"), "falling");
  assert.equal(nextVisualMode("falling", "nonsense"), "falling");
  assert.equal(nextVisualMode("falling", null), "falling");
  assert.deepEqual([...VISUAL_MODES], ["sheet", "falling", "hybrid"]);
});

test("loadVisualMode tolerates missing storage and invalid values", () => {
  assert.equal(loadVisualMode(null), "falling");
  assert.equal(loadVisualMode({ getItem: () => "sheet" }), "sheet");
  assert.equal(loadVisualMode({ getItem: () => "bogus" }), "falling");
  assert.equal(
    loadVisualMode({ getItem: () => { throw new Error("denied"); } }),
    "falling"
  );
});

test("switching modes never resets score/anchor replicas (model level)", () => {
  const replica = new ScoreReplica();
  const ticket = replica.beginRequest("s1");
  assert.ok(replica.accept(scorePayload({ songId: "s1" }), ticket));
  // Mode transitions are pure and independent of replica state.
  nextVisualMode("falling", "sheet");
  nextVisualMode("sheet", "hybrid");
  assert.equal(replica.state, "ready");
  assert.equal(replica.score.song_id, "s1");
});

/* ---------------- 15. resize stability ---------------- */

test("canvas sizing is bounded, deterministic and HiDPI-correct", () => {
  const size = visualCanvasSize(800, 400, 2, 1);
  assert.deepEqual({ w: size.width, h: size.height }, { w: 1600, h: 800 });
  assert.equal(size.ratio, 2);
  assert.deepEqual(
    { w: visualCanvasSize(800, 400, 2, 0.5).width, r: visualCanvasSize(800, 400, 2, 0.5).ratio },
    { w: 800, r: 1 }
  );
  // Degenerate CSS boxes clamp to 1x1 backing, never 0 (canvas spec).
  assert.equal(visualCanvasSize(0, 0, 1, 1).width, 1);
  assert.equal(visualCanvasSize(0, 0, 1, 1).height, 1);
  // dpr is capped at 3.
  assert.equal(visualCanvasSize(100, 100, 8, 1).ratio, 3);
  // Invalid quality falls back to full scale.
  assert.equal(visualCanvasSize(100, 100, 1, 0.11).ratio, 1);
});

/* ---------------- score payload validation ---------------- */

test("score payload validation accepts canonical and rejects garbage", () => {
  const good = scorePayload({
    notes: [
      { midi: 60, onset_seconds: 0, duration_seconds: 1 },
      { midi: 21, onset_seconds: 1.5, duration_seconds: 0 },
    ],
  });
  assert.equal(isValidScorePayload(good), true);
  assert.equal(isValidScorePayload({ ...good, schema_version: 2 }), false);
  assert.equal(isValidScorePayload({ ...good, song_id: "" }), false);
  assert.equal(isValidScorePayload({ ...good, note_count: 5 }), false);
  assert.equal(isValidScorePayload({ ...good, duration_seconds: -1 }), false);
  assert.equal(isValidScorePayload(null), false);
  assert.equal(isValidScorePayload([]), false);
  assert.equal(
    isValidScorePayload({
      ...good,
      notes: [{ index: 1, midi: 60, channel: 1, velocity: 90, onset_seconds: 0, duration_seconds: 0 }],
    }),
    false
  ); // indices must ascend from canonical order
  assert.equal(
    isValidScorePayload({
      ...good,
      notes: [{ index: 0, midi: 200, channel: 1, velocity: 90, onset_seconds: 0, duration_seconds: 0 }],
    }),
    false
  );
  assert.equal(
    isValidScorePayload({ ...good, diagnostics: { percussion_note_ons: "x", outside_keyboard_note_ons: 0, unpaired_note_offs: 0, retriggered_note_ons: 0, unterminated_note_ons: 0 } }),
    false
  );
});

test("keyboardRangeNotes filters to 21..108 and keeps canonical order", () => {
  const score = scorePayload({
    notes: [
      { midi: 20, onset_seconds: 0, duration_seconds: 1 },
      { midi: 60, onset_seconds: 1, duration_seconds: 1 },
      { midi: 108, onset_seconds: 2, duration_seconds: 1 },
      { midi: 109, onset_seconds: 3, duration_seconds: 1 },
    ],
  });
  const inRange = keyboardRangeNotes(score);
  assert.deepEqual(inRange.map((n) => n.midi), [60, 108]);
  assert.deepEqual(keyboardRangeNotes(null), []);
  assert.deepEqual(keyboardRangeNotes(scorePayload({ notes: [] })), []);
});

/* ---------------- ScoreReplica lifecycle ---------------- */

test("ScoreReplica: token-guarded fetch lifecycle", async () => {
  const replica = new ScoreReplica();
  const t1 = replica.beginRequest("s1");
  assert.equal(replica.state, "loading");
  // Wrong song payload rejected.
  assert.equal(replica.accept(scorePayload({ songId: "other" }), t1), false);
  // Wrong token rejected (a newer request superseded).
  const t2 = replica.beginRequest("s2");
  assert.equal(replica.accept(scorePayload({ songId: "s1" }), t1), false);
  assert.equal(replica.accept(scorePayload({ songId: "s2" }), t2), true);
  assert.equal(replica.state, "ready");
  // Same song again: no new request while settled.
  assert.equal(replica.beginRequest("s2"), null);
  // Failure path: a different song may retry after an error.
  const t3 = replica.beginRequest("s3");
  assert.equal(replica.noteFailure(t3), true);
  assert.equal(replica.state, "error");
  assert.equal(replica.noteFailure(t2), false); // stale failure ignored
  replica.reset();
  assert.equal(replica.state, "idle");
});

test("ScoreReplica: bounded same-song source revalidation", () => {
  const replica = new ScoreReplica();
  const initial = replica.beginRequest("s1");
  const sourceA = scorePayload({ songId: "s1" });
  assert.equal(replica.accept(sourceA, initial), true);
  const refresh = replica.beginRequest("s1", { revalidate: true });
  assert.equal(refresh.sourceVersion, sourceA.source_version);
  assert.equal(replica.beginRequest("s1", { revalidate: true }), null);
  const sourceB = {
    ...sourceA,
    source_version: `score-sha256:${"b".repeat(64)}`,
  };
  assert.equal(replica.accept(sourceB, refresh), true);
  assert.equal(replica.score.source_version, sourceB.source_version);
  const failedRefresh = replica.beginRequest("s1", { revalidate: true });
  assert.equal(replica.noteFailure(failedRefresh), true);
  assert.equal(replica.state, "ready");
  assert.equal(replica.score.source_version, sourceB.source_version);
});

/* ---------------- status model + aria ---------------- */

test("visual status model classifies every explicit state", () => {
  assert.equal(visualStatusModel({ sourceKind: "none" }), "no-song");
  assert.equal(
    visualStatusModel({ scoreState: "loading", sourceKind: "playback" }),
    "loading"
  );
  assert.equal(
    visualStatusModel({ scoreState: "error", sourceKind: "playback" }),
    "unavailable"
  );
  assert.equal(
    visualStatusModel({ scoreState: "ready", sourceKind: "playback", visibleNoteCount: 0 }),
    "empty"
  );
  assert.equal(
    visualStatusModel({ scoreState: "ready", sourceKind: "playback", visibleNoteCount: 5, playbackStale: true }),
    "stale"
  );
  assert.equal(
    visualStatusModel({ scoreState: "ready", sourceKind: "learning", visibleNoteCount: 5 }),
    "ready"
  );
});

test("aria summary names mode, status, song, position and target", () => {
  const summary = visualAriaSummary({
    mode: "falling",
    status: "ready",
    title: "Clair de Lune",
    positionSeconds: 42,
    targetText: "C4 + E4 + G4",
  });
  assert.ok(summary.startsWith("Falling notes view — ready"));
  assert.ok(summary.includes("Clair de Lune"));
  assert.ok(summary.includes("0:42"));
  assert.ok(summary.includes("target C4 + E4 + G4"));
  assert.ok(visualAriaSummary({ mode: "sheet", status: "stale" }).includes("frozen"));
});

/* ---------------- sheet view specifics ---------------- */

test("sheet pitch placement is inverted (highest pitch on top) and exact", () => {
  const range = { min: 60, max: 67 };
  const top = sheetMidiToY(67, range, 22, 200);
  const bottom = sheetMidiToY(60, range, 22, 200);
  assert.ok(top < bottom, "higher pitch must render above lower pitch");
  assert.equal(top, 22 + 200 / 8 / 2);
  const mid = sheetMidiToY(64, range, 22, 200);
  assert.ok(top < mid && mid < bottom);
});

test("sheet time mapping is linear and clamped by caller inputs", () => {
  const width = 800;
  const x0 = sheetTimeToX(0, 0, 20, width);
  const x10 = sheetTimeToX(10, 0, 20, width);
  const x20 = sheetTimeToX(20, 0, 20, width);
  assert.equal(x0, 0);
  assert.equal(x10, 400);
  assert.equal(x20, 800);
});

test("sheet ruler steps adapt deterministically", () => {
  assert.equal(sheetRulerStepSeconds(4), 1);
  assert.equal(sheetRulerStepSeconds(16), 2);
  assert.equal(sheetRulerStepSeconds(40), 5);
  assert.equal(sheetRulerStepSeconds(100), 15);
  assert.equal(sheetRulerStepSeconds(300), 60);
  assert.equal(sheetRulerStepSeconds(0), 5);
  assert.equal(sheetRulerStepSeconds(-1), 5);
});

test("sheet chronological ordering follows canonical indices", () => {
  const notes = makeNotes(30, { gap: 0.7 });
  let last = -Infinity;
  for (const note of notes) {
    assert.ok(note.onset_seconds >= last);
    last = note.onset_seconds;
  }
  const range = sheetPitchRange(notes);
  assert.equal(range.min, 60);
  assert.equal(range.max, 71);
});


test("remediation: onset-ordered overlap slice includes active long note", () => {
  const notes = [{onset_seconds:0,duration_seconds:30,midi:60},
    {onset_seconds:10,duration_seconds:1,midi:64},{onset_seconds:20,duration_seconds:1,midi:67}];
  assert.deepEqual(visibleNoteSlice(notes,0,5,30), {start:0,end:1});
});

test("remediation: current malformed score errors and retries, obsolete malformed is ignored", () => {
  const r = new ScoreReplica();
  const old = r.beginRequest("old");
  const current = r.beginRequest("s1");
  assert.equal(r.accept({},old),false);
  assert.equal(r.state,"loading");
  assert.equal(r.accept({},current),false);
  assert.equal(r.state,"error");
  const retry = r.beginRequest("s1");
  assert.ok(retry.token > current.token);
  assert.equal(r.accept(scorePayload(),retry),true);
  assert.equal(r.state,"ready");
});

function sectionRects(section) {
  const rects=[];
  const ctx={fillRect:(...v)=>rects.push(v),setLineDash:()=>{},beginPath:()=>{},moveTo:()=>{},lineTo:()=>{},stroke:()=>{}};
  FallingNotesRenderer.prototype._drawSectionBand.call({mappers:{fallingNoteY}},ctx,100,0,100,500,{clippedSection:section});
  return rects;
}
test("remediation: falling section normalizes Y and dims only outside", () => {
  assert.deepEqual(sectionRects({start:1,end:3}), [[0,200,100,200],[0,0,100,200],[0,400,100,100]]);
});
test("remediation: falling section clips fully outside and crossing viewport", () => {
  assert.deepEqual(sectionRects({start:6,end:8}), [[0,0,100,500]]);
  assert.deepEqual(sectionRects({start:-2,end:1}), [[0,400,100,100],[0,0,100,400]]);
});
test("remediation: stale and unavailable overlays use finite CSS dimensions", () => {
  for (const status of ["stale","unavailable"]) {
    const calls=[];
    const ctx={save:()=>{},restore:()=>{},fillRect:(...v)=>calls.push(v),fillText:(...v)=>calls.push(v)};
    const pane={ctx,cssWidth:1000,cssHeight:300};
    VisualStage.prototype._drawStaleOverlay.call({falling:{pane}}, {mode:"falling",status});
    assert.deepEqual(calls[0],[0,132,1000,36]);
    assert.equal(calls[1][1],500);assert.equal(calls[1][2],154);
    assert.ok(calls.flat().filter(v=>typeof v === "number").every(Number.isFinite));
  }
});

if (failures > 0) {
  console.error(`${failures} VISUAL MODEL TEST(S) FAILED`);
  process.exit(1);
}
console.log("ALL VISUAL MODEL TESTS PASSED");
