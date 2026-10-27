// PIANO-UI-006 browser-boundary validation and race tests.
import assert from "node:assert/strict";
import {
  FingeringReplica,
  isValidFingeringPayload,
} from "../../src/piano_core/web/static/app.js";

const SOURCE_A = `score-sha256:${"a".repeat(64)}`;
const SOURCE_B = `score-sha256:${"b".repeat(64)}`;

function score({ songId = "s1", sourceVersion = SOURCE_A, notes = null } = {}) {
  const canonicalNotes = notes || [{
    index: 0, midi: 60, channel: 1, velocity: 80,
    onset_seconds: 0, duration_seconds: 1,
  }];
  return {
    schema_version: 1,
    song_id: songId,
    source_version: sourceVersion,
    title: "Test",
    duration_seconds: 1,
    note_count: canonicalNotes.length,
    notes: canonicalNotes,
    diagnostics: {
      percussion_note_ons: 0, outside_keyboard_note_ons: 0,
      unpaired_note_offs: 0, retriggered_note_ons: 0,
      unterminated_note_ons: 0,
    },
    notation: { supported: [], unsupported: [] },
  };
}

function plan({ songId = "s1", sourceVersion = SOURCE_A } = {}) {
  return {
    schema_version: 1,
    engine_version: "balanced-engine-v1",
    plan_id: `fingering-sha256:${"c".repeat(64)}`,
    song_id: songId,
    source_version: sourceVersion,
    version: 1,
    generation: 1,
    strategy: "balanced_v1",
    provenance: "DETERMINISTIC_INFERENCE",
    targets: [{
      score_index: 0,
      target_id: "score:0",
      midi: 60,
      channel: 1,
      onset_seconds: 0,
      duration_seconds: 1,
      hand: "RIGHT",
      finger: 2,
      alternatives: [1, 3],
    }],
    warnings: ["source_contains_no_authored_hand_or_fingering_metadata"],
    excluded_note_count: 0,
    config: {
      strategy: "balanced_v1",
      max_hand_span_semitones: 12,
      alternative_count: 3,
    },
    capabilities: { manual_overrides: false, finger_sensitive_grading: false },
  };
}

assert.equal(isValidFingeringPayload(plan()), true);
assert.equal(isValidFingeringPayload(plan(), score()), true);
assert.equal(isValidFingeringPayload({ ...plan(), provenance: "SOURCE" }), false);
assert.equal(isValidFingeringPayload({ ...plan(), plan_id: "random" }), false);
assert.equal(isValidFingeringPayload({ ...plan(), targets: [{ ...plan().targets[0], finger: 6 }] }), false);
assert.equal(isValidFingeringPayload({ ...plan(), targets: [plan().targets[0], plan().targets[0]] }), false);

const mutateTarget = (fields) => ({
  ...plan(),
  targets: [{ ...plan().targets[0], ...fields }],
});
for (const [name, payload] of [
  ["mismatched MIDI", mutateTarget({ midi: 61 })],
  ["mismatched channel", mutateTarget({ channel: 2 })],
  ["missing target_id", mutateTarget({ target_id: undefined })],
  ["non-finite onset", mutateTarget({ onset_seconds: Number.NaN })],
  ["negative duration", mutateTarget({ duration_seconds: -1 })],
  ["duplicate alternatives", mutateTarget({ alternatives: [1, 1] })],
  ["primary in alternatives", mutateTarget({ finger: 2, alternatives: [1, 2] })],
  ["incorrect score_index", mutateTarget({ score_index: 1, target_id: "score:1" })],
]) {
  assert.equal(isValidFingeringPayload(payload, score()), false, name);
}
assert.equal(
  isValidFingeringPayload({ ...plan(), targets: [] }, score()),
  false,
  "missing playable target"
);
assert.equal(
  isValidFingeringPayload(plan({ sourceVersion: SOURCE_B }), score()),
  false,
  "stale source version"
);
const twoNotes = score({ notes: [
  score().notes[0],
  { ...score().notes[0], index: 1, midi: 64 },
] });
const duplicateId = {
  ...plan(),
  targets: [
    plan().targets[0],
    { ...plan().targets[0], score_index: 1, midi: 64 },
  ],
};
assert.equal(isValidFingeringPayload(duplicateId, twoNotes), false, "duplicate target_id");

const replica = new FingeringReplica();
const oldTicket = replica.beginRequest("s1", SOURCE_A);
const currentTicket = replica.beginRequest("s1", SOURCE_B);
assert.equal(replica.accept(plan({ sourceVersion: SOURCE_A }), oldTicket, score()), false);
assert.equal(replica.accept(
  plan({ sourceVersion: SOURCE_A }),
  currentTicket,
  score({ sourceVersion: SOURCE_B })
), false);
assert.equal(replica.state, "error");
const retry = replica.beginRequest("s1", SOURCE_B);
assert.equal(replica.accept(
  plan({ sourceVersion: SOURCE_B }),
  retry,
  score({ sourceVersion: SOURCE_B })
), true);
assert.equal(replica.state, "ready");
assert.equal(replica.beginRequest("s1", SOURCE_B), null);
replica.reset();
assert.equal(replica.state, "idle");

console.log("PIANO-UI-006 fingering web logic: 24 assertions passed");
