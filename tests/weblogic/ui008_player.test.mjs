// PIANO-UI-008 U8-P3: deterministic node tests for the Player workspace
// presentation logic — honest capability map, performance state machine,
// secondary placement, LED payload, timeline aria and header wording.
import assert from "node:assert/strict";
import {
  PLAYER_FEATURES,
  SECONDARY_BREAKPOINT,
  performanceHeaderModel,
  createPerformanceState,
  secondaryPlacement,
  ledTogglePayload,
  timelineAria,
} from "../../src/piano_core/web/static/ui008/player.js";

let passes = 0;
let failures = 0;
function test(name, fn) {
  try {
    fn();
    passes += 1;
    console.log(`ok - ${name}`);
  } catch (error) {
    failures += 1;
    console.error(`FAIL - ${name}`);
    console.error(error);
  }
}

/* ---------------- honest capability map ---------------- */

test("PLAYER_FEATURES: only real Core capabilities are enabled", () => {
  assert.equal(PLAYER_FEATURES.play, true);
  assert.equal(PLAYER_FEATURES.pause, true);
  assert.equal(PLAYER_FEATURES.resume, true);
  assert.equal(PLAYER_FEATURES.stop, true);
});

test("PLAYER_FEATURES: seek stays unsupported (no Core command)", () => {
  assert.equal(PLAYER_FEATURES.seek, false);
});

test("PLAYER_FEATURES: unsupported future vision features stay absent", () => {
  for (const feature of [
    "liveTempo", "transpose", "abLoop", "handSelection", "metronome",
    "countIn", "sections", "queue", "favorites",
  ]) {
    assert.equal(PLAYER_FEATURES[feature], false, feature);
  }
});

test("PLAYER_FEATURES: presentation-only capabilities are marked", () => {
  assert.equal(PLAYER_FEATURES.ledControls, true); // existing semantic PUT /led
  assert.equal(PLAYER_FEATURES.fullscreen, true); // presentation only
  assert.deepEqual([...PLAYER_FEATURES.visualModes], []);
  assert.deepEqual([...PLAYER_FEATURES.handsCameraModes], []);
});

test("PLAYER_FEATURES is frozen (no runtime capability inflation)", () => {
  assert.ok(Object.isFrozen(PLAYER_FEATURES));
  assert.ok(Object.isFrozen(PLAYER_FEATURES.visualModes));
});

/* ---------------- performance state machine ---------------- */

test("performance state: inactive -> engaging -> active -> inactive", () => {
  const p = createPerformanceState();
  assert.equal(p.current, "inactive");
  assert.equal(p.request(), "engaging");
  assert.equal(p.confirm(), "active");
  assert.equal(p.exit(), "inactive");
});

test("performance state: request is idempotent while engaging/active", () => {
  const p = createPerformanceState();
  p.request();
  assert.equal(p.request(), "engaging");
  p.confirm();
  assert.equal(p.request(), "active");
});

test("performance state: confirm before request is a no-op", () => {
  const p = createPerformanceState();
  assert.equal(p.confirm(), "inactive");
});

test("performance state: exit from inactive is safe", () => {
  const p = createPerformanceState();
  assert.equal(p.exit(), "inactive");
});

/* ---------------- secondary placement ---------------- */

test("secondary placement: wide viewport keeps controls inline", () => {
  assert.equal(secondaryPlacement(true), "inline");
});

test("secondary placement: phone moves the SAME node into the sheet", () => {
  assert.equal(secondaryPlacement(false), "sheet");
});

test("secondary breakpoint matches the P1 compact boundary", () => {
  assert.equal(SECONDARY_BREAKPOINT, "(min-width: 600px)");
});

/* ---------------- LED quick toggle ---------------- */

test("led toggle payload flips enabled using only the semantic field", () => {
  assert.deepEqual(ledTogglePayload({ enabled: true }), { enabled: false });
  assert.deepEqual(ledTogglePayload({ enabled: false }), { enabled: true });
  assert.deepEqual(ledTogglePayload(null), { enabled: true });
});

/* ---------------- timeline aria ---------------- */

test("timeline aria: read-only progress maps to 0..100 integer values", () => {
  assert.deepEqual(timelineAria(0), { valuemin: 0, valuemax: 100, valuenow: 0 });
  assert.deepEqual(timelineAria(0.427), { valuemin: 0, valuemax: 100, valuenow: 43 });
  assert.deepEqual(timelineAria(1), { valuemin: 0, valuemax: 100, valuenow: 100 });
});

test("timeline aria: non-finite input clamps safely", () => {
  assert.deepEqual(timelineAria(Number.NaN), { valuemin: 0, valuemax: 100, valuenow: 0 });
  assert.deepEqual(timelineAria(7), { valuemin: 0, valuemax: 100, valuenow: 100 });
  assert.deepEqual(timelineAria(-3), { valuemin: 0, valuemax: 100, valuenow: 0 });
});

/* ---------------- performance header wording ---------------- */

test("performance header: honest heading and status wording", () => {
  assert.deepEqual(
    performanceHeaderModel({ title: "Nocturne", stateLabel: "Playing", fresh: true }),
    { heading: "Nocturne", status: "Playing" }
  );
});

test("performance header: no song yields consumer wording, never technical", () => {
  assert.deepEqual(
    performanceHeaderModel({ title: null, stateLabel: "No song" }),
    { heading: "Nothing playing", status: "No song" }
  );
});

test("performance header: stale authority reads as Reconnecting", () => {
  assert.deepEqual(
    performanceHeaderModel({ title: "Nocturne", stateLabel: "Playing", fresh: false }),
    { heading: "Nocturne", status: "Reconnecting…" }
  );
});

if (failures) {
  console.error(`FAIL ${failures}; PASS ${passes}`);
  process.exit(1);
}
console.log(`PASS ${passes}/${passes}`);
