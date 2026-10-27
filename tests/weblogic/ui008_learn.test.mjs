// PIANO-UI-008 U8-P4: deterministic node tests for the premium Learn
// presentation logic — honest capability map, consumer wording without
// enum leakage, authoritative target/progress/feedback models, mutex
// enablement and frozen future-mode absence.
import assert from "node:assert/strict";
import {
  LEARN_FEATURES,
  LEARN_MODES,
  learnModeLabel,
  learnStateChip,
  learnTargetChips,
  learnProgress,
  learnFeedback,
  learnRepetition,
  learnGuidance,
  learnControls,
  learnLessonModel,
} from "../../src/piano_core/web/static/ui008/learn.js";

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

const IDLE_SESSION = Object.freeze({
  available: true, state: "IDLE", song_id: "s1", title: "Nocturne",
  step_index: null, total_steps: null, completed_steps: 0, progress: 0,
  target: null, midi_connection: "CONNECTED", session_id: null,
  mode: "WAIT_FOR_NOTE", section: null, accepted_notes: [],
  latest_outcome: "NONE", repetition: { current: 0, required: 1, successful: 0, failed: 0 },
  error: null,
});
const WAITING_SINGLE = Object.freeze({
  ...IDLE_SESSION, state: "WAITING", session_id: "sess-1", version: 4,
  step_index: 11, total_steps: 84, completed_steps: 11, progress: 0.1309,
  target: Object.freeze({ notes: [67], names: ["G4"], chord: false, onset_seconds: 4, target_id: "target:11:67" }),
});
const WAITING_CHORD = Object.freeze({
  ...WAITING_SINGLE, step_index: 12, completed_steps: 12,
  target: Object.freeze({ notes: [60, 64, 67], names: ["C4", "E4", "G4"], chord: true, onset_seconds: 8, target_id: "target:12:60-64-67" }),
});

/* ---------------- honest capability map ---------------- */

test("LEARN_FEATURES: real Core capabilities are enabled", () => {
  for (const f of ["selectSong", "start", "stop", "restartTarget", "restartSection", "waitMode", "sectionMode", "repeatUntilCorrectMode", "ledIndicator"]) {
    assert.equal(LEARN_FEATURES[f], true, f);
  }
  assert.deepEqual([...LEARN_FEATURES.visualModes], ["sheet", "falling", "hybrid"]);
});

test("LEARN_FEATURES: future Learning-Engine-V2 capabilities stay absent", () => {
  for (const f of ["pauseResume", "tempoMutation", "handSelection", "watchListenFollow", "scoreMetrics"]) {
    assert.equal(LEARN_FEATURES[f], false, f);
  }
});

test("LEARN_FEATURES is frozen (no runtime capability inflation)", () => {
  assert.ok(Object.isFrozen(LEARN_FEATURES));
  assert.ok(Object.isFrozen(LEARN_FEATURES.visualModes));
  assert.ok(Object.isFrozen(LEARN_MODES));
});

test("LEARN_MODES preserves the three accepted strategies and adds governed Flow", () => {
  assert.deepEqual(
    LEARN_MODES.map((m) => m.value),
    ["WAIT_FOR_NOTE", "SECTION_PRACTICE", "REPEAT_UNTIL_CORRECT", "FLOW"]
  );
});

/* ---------------- consumer wording ---------------- */

test("mode label is consumer wording, never the raw enum", () => {
  assert.equal(learnModeLabel("WAIT_FOR_NOTE"), "Wait for me");
  assert.equal(learnModeLabel("SECTION_PRACTICE"), "Section practice");
  assert.equal(learnModeLabel("REPEAT_UNTIL_CORRECT"), "Repeat until correct");
  assert.equal(learnModeLabel("SOMETHING_ELSE"), "Guided practice");
});

test("state chip maps real states without enum leakage", () => {
  assert.deepEqual(learnStateChip({ session: WAITING_SINGLE, stale: false, selectedSong: null, startBlocked: false }), { label: "Your turn", tone: "active" });
  assert.deepEqual(learnStateChip({ session: { ...IDLE_SESSION, state: "COMPLETED" }, stale: false, selectedSong: null, startBlocked: false }), { label: "Complete", tone: "ok" });
  assert.deepEqual(learnStateChip({ session: IDLE_SESSION, stale: false, selectedSong: null, startBlocked: false }), { label: "No song", tone: "idle" });
  assert.deepEqual(learnStateChip({ session: IDLE_SESSION, stale: false, selectedSong: { song_id: "s1" }, startBlocked: false }), { label: "Ready", tone: "ready" });
  assert.deepEqual(learnStateChip({ session: IDLE_SESSION, stale: false, selectedSong: { song_id: "s1" }, startBlocked: true }), { label: "Piano in use", tone: "wait" });
  assert.deepEqual(learnStateChip({ session: IDLE_SESSION, stale: true, selectedSong: null, startBlocked: false }), { label: "Reconnecting", tone: "wait" });
  assert.deepEqual(learnStateChip({ session: { available: false, state: "IDLE" }, stale: false, selectedSong: null, startBlocked: false }), { label: "Unavailable", tone: "bad" });
});

/* ---------------- authoritative target chips ---------------- */

test("single-note target yields one remaining chip", () => {
  assert.deepEqual(learnTargetChips(WAITING_SINGLE.target, []), [
    { name: "G4", midi: 67, accepted: false },
  ]);
});

test("chord chips split accepted vs remaining from CORE accepted_notes only", () => {
  assert.deepEqual(learnTargetChips(WAITING_CHORD.target, [60]), [
    { name: "C4", midi: 60, accepted: true },
    { name: "E4", midi: 64, accepted: false },
    { name: "G4", midi: 67, accepted: false },
  ]);
  assert.deepEqual(learnTargetChips(WAITING_CHORD.target, [60, 64, 67]).map((c) => c.accepted), [true, true, true]);
});

test("no target yields no chips (never a stale chip)", () => {
  assert.deepEqual(learnTargetChips(null, []), []);
  assert.deepEqual(learnTargetChips({ names: [] }, []), []);
});

/* ---------------- progress (authoritative counters only) ---------------- */

test("progress derives from step_index/total_steps only", () => {
  assert.deepEqual(learnProgress(WAITING_SINGLE), { text: "Step 12 of 84", pct: 13, barAllowed: true });
  assert.equal(learnProgress({ ...WAITING_SINGLE, state: "COMPLETED" }).text, "11 of 84");
});

test("progress omits percentage when total_steps unavailable (never faked)", () => {
  assert.deepEqual(learnProgress(IDLE_SESSION), { text: "", pct: null, barAllowed: false });
  assert.deepEqual(learnProgress(null), { text: "", pct: null, barAllowed: false });
});

/* ---------------- feedback (authoritative outcome only) ---------------- */

test("feedback maps Core latest_outcome values, browser never grades", () => {
  assert.equal(learnFeedback({ latest_outcome: "CORRECT" }).tone, "ok");
  assert.ok(learnFeedback({ latest_outcome: "INCORRECT" }).text.includes("stays put"));
  assert.equal(learnFeedback({ latest_outcome: "INPUT_UNAVAILABLE" }).tone, "wait");
  assert.equal(learnFeedback({ latest_outcome: "NONE" }).text, "");
  assert.equal(learnFeedback(null).text, "");
});

test("repetition line only for REPEAT_UNTIL_CORRECT from counters", () => {
  assert.equal(learnRepetition({ mode: "WAIT_FOR_NOTE" }), "");
  assert.equal(
    learnRepetition({ mode: "REPEAT_UNTIL_CORRECT", repetition: { current: 2, required: 3, successful: 5, failed: 1 } }),
    "In a row 2 of 3 · clean 5"
  );
});

/* ---------------- guidance ---------------- */

test("guidance never leaks internal state names", () => {
  const banned = ["WAITING", "IDLE", "COMPLETED", "FAILED", "StateStore", "API", "WebSocket", "snapshot"];
  const inputs = [
    { session: null, stale: false, selectedSong: null, startBlocked: false },
    { session: null, stale: false, selectedSong: { song_id: "s" }, startBlocked: false },
    { session: IDLE_SESSION, stale: false, selectedSong: { song_id: "s" }, startBlocked: false },
    { session: IDLE_SESSION, stale: false, selectedSong: null, startBlocked: true },
    { session: WAITING_SINGLE, stale: false, selectedSong: null, startBlocked: false },
    { session: { ...IDLE_SESSION, state: "COMPLETED", total_steps: 84 }, stale: false, selectedSong: null, startBlocked: false },
    { session: null, stale: true, selectedSong: null, startBlocked: false },
    { session: { available: false, state: "IDLE" }, stale: false, selectedSong: null, startBlocked: false },
  ];
  for (const input of inputs) {
    const text = learnGuidance(input);
    for (const word of banned) {
      assert.ok(!text.includes(word), `guidance leaks "${word}": ${text}`);
    }
  }
});

test("waiting guidance tells the learner to play the highlighted keys", () => {
  assert.equal(learnGuidance({ session: WAITING_SINGLE, stale: false, selectedSong: null, startBlocked: false }), "Play the highlighted key(s) on your piano.");
});

/* ---------------- controls + mutex ---------------- */

test("controls: start needs selection + availability + no session + no playback", () => {
  assert.deepEqual(learnControls({ session: IDLE_SESSION, selectedSong: { song_id: "s" }, playbackSnapshot: { available: false, state: null } }), {
    start: true, stop: false, restartTarget: false, restartSection: false, startBlockedByPlayback: false,
  });
  assert.equal(learnControls({ session: IDLE_SESSION, selectedSong: null, playbackSnapshot: null }).start, false);
  assert.equal(learnControls({ session: WAITING_SINGLE, selectedSong: { song_id: "s" }, playbackSnapshot: null }).stop, true);
  assert.equal(learnControls({ session: WAITING_SINGLE, selectedSong: { song_id: "s" }, playbackSnapshot: null }).restartTarget, true);
  assert.equal(learnControls({ session: IDLE_SESSION, selectedSong: { song_id: "s" }, playbackSnapshot: { available: true, state: "PLAYING" } }).start, false);
  assert.equal(learnControls({ session: IDLE_SESSION, selectedSong: { song_id: "s" }, playbackSnapshot: { available: true, state: "PAUSED" } }).startBlockedByPlayback, true);
  assert.equal(learnControls({ session: IDLE_SESSION, selectedSong: { song_id: "s" }, playbackSnapshot: { available: true, state: "IDLE" } }).start, true);
});

/* ---------------- full model ---------------- */

test("model: waiting single-note lesson (guidance, chips, progress)", () => {
  const m = learnLessonModel({ session: WAITING_SINGLE, stale: false, selectedSong: null, playbackSnapshot: null, led: null });
  assert.equal(m.stage, "waiting");
  assert.equal(m.chip.label, "Your turn");
  assert.equal(m.guidance, "Play the highlighted key(s) on your piano.");
  assert.equal(m.title, "Nocturne");
  assert.equal(m.targetText, "G4");
  assert.equal(m.chips.length, 1);
  assert.equal(m.progress.text, "Step 12 of 84");
  assert.equal(m.modeLabel, "Wait for me");
  assert.equal(m.controls.stop, true);
});

test("model: chord target exposes chord flag and authoritative partials", () => {
  const m = learnLessonModel({ session: { ...WAITING_CHORD, accepted_notes: [60] }, stale: false, selectedSong: null, playbackSnapshot: null, led: null });
  assert.equal(m.chord, true);
  assert.deepEqual(m.chips.map((c) => c.accepted), [true, false, false]);
});

test("model: ready stage with selected song and idle engine", () => {
  const m = learnLessonModel({ session: IDLE_SESSION, stale: false, selectedSong: { song_id: "s1", title: "Nocturne" }, playbackSnapshot: null, led: null });
  assert.equal(m.stage, "ready");
  assert.equal(m.chip.label, "Ready");
  assert.equal(m.controls.start, true);
  assert.equal(m.targetText, "");
  assert.equal(m.chips.length, 0);
});

test("model: no-song and unavailable and stale stages", () => {
  assert.equal(learnLessonModel({ session: null, stale: false, selectedSong: null }).stage, "no-song");
  assert.equal(learnLessonModel({ session: { available: false, state: "IDLE" }, stale: false, selectedSong: { song_id: "s" } }).stage, "unavailable");
  assert.equal(learnLessonModel({ session: WAITING_SINGLE, stale: true, selectedSong: null }).stage, "stale");
  const stale = learnLessonModel({ session: WAITING_SINGLE, stale: true, selectedSong: null });
  assert.equal(stale.targetText, "");
  assert.equal(stale.chips.length, 0);
});

test("model: completion carries real counts only, never fabricated metrics", () => {
  const m = learnLessonModel({ session: { ...WAITING_SINGLE, state: "COMPLETED", completed_steps: 84 }, stale: false, selectedSong: null, playbackSnapshot: null, led: null });
  assert.equal(m.stage, "completed");
  assert.equal(m.completedText, "All 84 steps played.");
  const json = JSON.stringify(m);
  for (const banned of ["accuracy", "stars", "streak", "mastery", "score"]) {
    assert.ok(!json.toLowerCase().includes(banned), `model fabricates ${banned}`);
  }
});

test("model: failed stage surfaces Core error honestly", () => {
  const m = learnLessonModel({ session: { ...IDLE_SESSION, state: "FAILED", error: "library vanished" }, stale: false, selectedSong: { song_id: "s" }, playbackSnapshot: null, led: null });
  assert.equal(m.stage, "failed");
  assert.equal(m.errorText, "library vanished");
});

test("model: mutex conflict reads as piano-in-use with honest guidance", () => {
  const m = learnLessonModel({ session: IDLE_SESSION, stale: false, selectedSong: { song_id: "s" }, playbackSnapshot: { available: true, state: "PLAYING" }, led: null });
  assert.equal(m.stage, "conflict");
  assert.equal(m.chip.label, "Piano in use");
  assert.ok(m.guidance.includes("Playback is using the piano"));
});

test("model: LED guidance indicator only from real configured+available state", () => {
  const waiting = { session: WAITING_SINGLE, stale: false, selectedSong: null, playbackSnapshot: null };
  assert.equal(learnLessonModel({ ...waiting, led: { configured: true, available: true, mode: "physical" } }).ledGuidance, "Keyboard lights are guiding you.");
  assert.equal(learnLessonModel({ ...waiting, led: { configured: true, available: true, mode: "off" } }).ledGuidance, "");
  assert.equal(learnLessonModel({ ...waiting, led: { configured: true, available: false, mode: "physical" } }).ledGuidance, "");
  assert.equal(learnLessonModel({ ...waiting, led: { configured: false, available: false, mode: null } }).ledGuidance, "");
  assert.equal(learnLessonModel({ ...waiting, led: null }).ledGuidance, "");
});

test("model: degraded MIDI surfaces while waiting", () => {
  const m = learnLessonModel({ session: { ...WAITING_SINGLE, midi_connection: "DISCONNECTED" }, stale: false, selectedSong: null, playbackSnapshot: null, led: null });
  assert.deepEqual(m.notes, ["Your piano input is not connected, so practice cannot progress."]);
});

if (failures) {
  console.error(`FAIL ${failures}; PASS ${passes}`);
  process.exit(1);
}
console.log(`PASS ${passes}/${passes}`);
