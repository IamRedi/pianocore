// PIANO-UI-002 remediation: application-level coordination tests.
// These exercise the same PlayerCoordinator used by the browser entrypoint,
// with controlled async responses and a controllable monotonic clock.
import assert from "node:assert/strict";
import {
  ApiError,
  PlayerCoordinator,
  PLAYBACK_PAYLOAD_CLASS,
  classifyPlaybackPayload,
  playbackFrame,
  reconcilePlaybackAnchor,
  transportShortcutAction,
} from "../../src/piano_core/web/static/app.js";

let failures = 0;
async function test(name, fn) {
  try {
    await fn();
    console.log(`ok - ${name}`);
  } catch (error) {
    failures += 1;
    console.error(`FAIL - ${name}`);
    console.error(error);
  }
}

function snapshot(state, songId = "song-a", position = 10, duration = 60) {
  return {
    available: true,
    state,
    song_id: songId,
    title: songId,
    position_seconds: position,
    duration_seconds: duration,
    tempo_multiplier: 1,
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((ok, bad) => {
    resolve = ok;
    reject = bad;
  });
  return { promise, resolve, reject };
}

function seed(player, value, receivedAt = 0) {
  const ticket = player.beginPoll();
  assert.ok(ticket);
  assert.equal(player.acceptPoll(value, ticket, receivedAt), true);
}

async function settle() {
  await Promise.resolve();
  await Promise.resolve();
}

// F1: freshness expires independently of requestAnimationFrame/state.
for (const [label, value] of [
  ["PAUSED", snapshot("PAUSED")],
  ["PLAYING before duration", snapshot("PLAYING", "song-a", 10, 60)],
  ["PLAYING at duration", snapshot("PLAYING", "song-a", 60, 60)],
  ["COMPLETED", snapshot("COMPLETED", "song-a", 60, 60)],
  ["FAILED", snapshot("FAILED", "song-a", 14, 60)],
]) {
  await test(`F1 expires ${label} without animation frames`, () => {
    const player = new PlayerCoordinator({ freshnessMs: 3500 });
    seed(player, value);
    assert.equal(player.evaluateFreshness(3500), true);
    assert.equal(player.evaluateFreshness(3501), false);
    assert.equal(player.fresh, false);
  });
}

await test("F1 tab/time jump expires state and blocks command invocation", async () => {
  const player = new PlayerCoordinator({ freshnessMs: 3500 });
  seed(player, snapshot("PAUSED"));
  let calls = 0;
  const result = await player.runCommand({
    action: "resume",
    invoke: async () => { calls += 1; return snapshot("PLAYING"); },
    reconcile: async () => snapshot("PLAYING"),
    now: () => 9000,
  });
  assert.equal(result.status, "stale");
  assert.equal(calls, 0);
  assert.equal(player.fresh, false);
});

async function commandVsOldPoll({ before, after, action, songId = "song-a" }) {
  let now = 0;
  const player = new PlayerCoordinator();
  seed(player, before, now);
  const old = deferred();
  const oldPoll = player.runPoll(() => old.promise, (x) => x, () => now);
  const command = deferred();
  const reconcile = deferred();
  now = 100;
  const commandRun = player.runCommand({
    action,
    requestedSongId: action === "play" ? songId : null,
    invoke: () => command.promise,
    reconcile: () => reconcile.promise,
    now: () => now,
  });
  return { player, old, oldPoll, command, reconcile, commandRun, setNow: (n) => { now = n; } };
}

await test("F2 poll starts before command; command result wins delayed poll", async () => {
  const flow = await commandVsOldPoll({
    before: snapshot("PLAYING"),
    after: snapshot("PAUSED"),
    action: "pause",
  });
  flow.command.resolve(snapshot("PAUSED"));
  await settle();
  assert.equal(flow.player.snapshot.state, "PAUSED");
  flow.old.resolve(snapshot("PLAYING"));
  assert.equal((await flow.oldPoll).status, "stale");
  assert.equal(flow.player.snapshot.state, "PAUSED");
  flow.reconcile.resolve(snapshot("PAUSED"));
  assert.equal((await flow.commandRun).status, "success");
});

await test("F2 delayed poll returning before command response is still rejected", async () => {
  const flow = await commandVsOldPoll({
    before: snapshot("PAUSED"),
    after: snapshot("PLAYING"),
    action: "resume",
  });
  flow.old.resolve(snapshot("PAUSED"));
  assert.equal((await flow.oldPoll).status, "stale");
  flow.command.resolve(snapshot("PLAYING"));
  await settle();
  flow.reconcile.resolve(snapshot("PLAYING"));
  assert.equal((await flow.commandRun).status, "success");
  assert.equal(flow.player.snapshot.state, "PLAYING");
});

await test("F2 poll beginning during command is suppressed", async () => {
  const player = new PlayerCoordinator();
  seed(player, snapshot("PLAYING"));
  const command = deferred();
  const reconcile = deferred();
  const run = player.runCommand({
    action: "pause",
    invoke: () => command.promise,
    reconcile: () => reconcile.promise,
    now: () => 100,
  });
  const poll = await player.runPoll(
    async () => snapshot("PLAYING"),
    (x) => x,
    () => 100
  );
  assert.equal(poll.status, "suppressed");
  command.resolve(snapshot("PAUSED"));
  await settle();
  reconcile.resolve(snapshot("PAUSED"));
  await run;
});

for (const [action, before, stale, after] of [
  ["pause", snapshot("PLAYING"), snapshot("PLAYING"), snapshot("PAUSED")],
  ["resume", snapshot("PAUSED"), snapshot("PAUSED"), snapshot("PLAYING")],
  ["stop", snapshot("PLAYING"), snapshot("PLAYING"), snapshot("IDLE", null, 0, 0)],
]) {
  await test(`F2 ${action} rejects stale ${stale.state} poll`, async () => {
    const flow = await commandVsOldPoll({ before, after, action });
    flow.command.resolve(after);
    await settle();
    flow.old.resolve(stale);
    assert.equal((await flow.oldPoll).accepted, false);
    flow.reconcile.resolve(after);
    await flow.commandRun;
    assert.equal(flow.player.snapshot.state, after.state);
  });
}

await test("F2 song A poll cannot replace accepted song B session", async () => {
  const player = new PlayerCoordinator();
  seed(player, snapshot("COMPLETED", "song-a", 60, 60));
  player.selectSong({ song_id: "song-b", title: "B" });
  const old = deferred();
  const oldPoll = player.runPoll(() => old.promise, (x) => x, () => 100);
  const run = player.runCommand({
    action: "play",
    requestedSongId: "song-b",
    invoke: async () => snapshot("PLAYING", "song-b", 0, 80),
    reconcile: async () => snapshot("PLAYING", "song-b", 0.1, 80),
    now: () => 200,
  });
  old.resolve(snapshot("COMPLETED", "song-a", 60, 60));
  assert.equal((await oldPoll).accepted, false);
  assert.equal((await run).status, "success");
  assert.equal(player.snapshot.song_id, "song-b");
});

// F3: active metadata and explicit next selection are separate.
await test("F3 terminal A hydration does not overwrite explicit B or C selection", () => {
  const player = new PlayerCoordinator();
  seed(player, snapshot("COMPLETED", "song-a", 60, 60));
  player.selectSong({ song_id: "song-b", title: "B" });
  const hydration = player.beginHydration("song-a");
  assert.equal(player.acceptHydration({ song_id: "song-a", title: "A" }, hydration), true);
  assert.equal(player.selectedSong.song_id, "song-b");
  player.selectSong({ song_id: "song-c", title: "C" });
  assert.equal(player.selectedSong.song_id, "song-c");
  assert.equal(player.activeSongMetadata.song_id, "song-a");
});

await test("F3 accepted B play binds active B without losing canonical selection", async () => {
  const player = new PlayerCoordinator();
  seed(player, snapshot("COMPLETED", "song-a", 60, 60));
  player.selectSong({ song_id: "song-b", title: "B" });
  const result = await player.runCommand({
    action: "play",
    requestedSongId: "song-b",
    invoke: async () => snapshot("PLAYING", "song-b", 0, 80),
    reconcile: async () => snapshot("PLAYING", "song-b", 0.1, 80),
    now: () => 100,
  });
  assert.equal(result.status, "success");
  assert.equal(player.activeSongMetadata.song_id, "song-b");
  assert.equal(player.selectedSong.song_id, "song-b");
  assert.equal(player.selectionPending, false);
});

await test("F3 active song hydrates when no explicit next selection exists", () => {
  const player = new PlayerCoordinator();
  seed(player, snapshot("PAUSED", "song-a"));
  const hydration = player.beginHydration("song-a");
  assert.equal(player.acceptHydration({ song_id: "song-a", track_count: 2 }, hydration), true);
  assert.equal(player.activeSongMetadata.song_id, "song-a");
  assert.equal(player.selectedSong, null);
});

await test("F3 invalid selected song is cleared after authoritative rejection", async () => {
  const player = new PlayerCoordinator();
  seed(player, snapshot("IDLE", null, 0, 0));
  player.selectSong({ song_id: "missing", title: "Missing" });
  const result = await player.runCommand({
    action: "play",
    requestedSongId: "missing",
    invoke: async () => { throw new ApiError(404, "song_not_found", "missing"); },
    reconcile: async () => { throw new Error("must not reconcile rejection"); },
    now: () => 100,
  });
  assert.equal(result.status, "rejected");
  assert.equal(player.selectedSong, null);
});

// F4: transport failure is outcome-unknown, typed response is authoritative.
for (const [action, before] of [
  ["play", snapshot("IDLE", null, 0, 0)],
  ["pause", snapshot("PLAYING")],
  ["resume", snapshot("PAUSED")],
  ["stop", snapshot("PLAYING")],
]) {
  await test(`F4 ${action} network failure invalidates freshness until recovery`, async () => {
    const player = new PlayerCoordinator();
    seed(player, before);
    if (action === "play") player.selectSong({ song_id: "song-a" });
    const result = await player.runCommand({
      action,
      requestedSongId: action === "play" ? "song-a" : null,
      invoke: async () => { throw new ApiError(0, "network_error", "offline"); },
      reconcile: async () => snapshot("PAUSED"),
      now: () => 100,
    });
    assert.equal(result.status, "unknown");
    assert.equal(player.fresh, false);
    const recovered = await player.runPoll(
      async () => snapshot("PAUSED"),
      (x) => x,
      () => 200
    );
    assert.equal(recovered.status, "accepted");
    assert.equal(player.fresh, true);
  });
}

await test("F4 typed 409 rejection preserves fresh authoritative prior state", async () => {
  const player = new PlayerCoordinator();
  seed(player, snapshot("PLAYING"));
  const result = await player.runCommand({
    action: "pause",
    invoke: async () => { throw new ApiError(409, "invalid_state_transition", "rejected"); },
    reconcile: async () => snapshot("PAUSED"),
    now: () => 100,
  });
  assert.equal(result.status, "rejected");
  assert.equal(player.fresh, true);
  assert.equal(player.snapshot.state, "PLAYING");
});

await test("F4 malformed 200 success body is outcome unknown rather than confirmed rejection", async () => {
  const player = new PlayerCoordinator();
  seed(player, snapshot("PLAYING"));
  const result = await player.runCommand({
    action: "pause",
    invoke: async () => { throw new ApiError(200, "invalid_response", "malformed success body"); },
    reconcile: async () => snapshot("PAUSED"),
    now: () => 100,
  });
  assert.equal(result.status, "unknown");
  assert.equal(player.fresh, false);
  assert.equal(player.snapshot.state, "PLAYING");
});

await test("F4 stale overlapping poll cannot end outcome-unknown recovery", async () => {
  const player = new PlayerCoordinator();
  seed(player, snapshot("PLAYING"));
  const old = deferred();
  const oldRun = player.runPoll(() => old.promise, (x) => x, () => 200);
  const command = await player.runCommand({
    action: "stop",
    invoke: async () => { throw new ApiError(0, "network_error", "offline"); },
    reconcile: async () => snapshot("IDLE", null, 0, 0),
    now: () => 100,
  });
  assert.equal(command.status, "unknown");
  old.resolve(snapshot("PLAYING"));
  assert.equal((await oldRun).accepted, false);
  assert.equal(player.fresh, false);
});

// RR-1: failure eligibility uses the same generation/sequence boundary as
// successful poll application.
for (const [action, before, after, songId] of [
  ["pause", snapshot("PLAYING"), snapshot("PAUSED"), null],
  ["resume", snapshot("PAUSED"), snapshot("PLAYING"), null],
  ["stop", snapshot("PLAYING"), snapshot("IDLE", null, 0, 0), null],
  ["play", snapshot("COMPLETED", "song-a", 60, 60), snapshot("PLAYING", "song-b", 0, 80), "song-b"],
]) {
  await test(`RR-1 obsolete pre-${action} poll failure cannot invalidate reconciled state`, async () => {
    const player = new PlayerCoordinator();
    seed(player, before);
    if (songId) player.selectSong({ song_id: songId, title: songId });
    const old = deferred();
    const oldRun = player.runPoll(() => old.promise, (x) => x, () => 300);
    const command = await player.runCommand({
      action,
      requestedSongId: songId,
      invoke: async () => after,
      reconcile: async () => after,
      now: () => 100,
    });
    assert.equal(command.status, "success");
    old.reject(new ApiError(503, "not_ready", "old failure"));
    const obsolete = await oldRun;
    assert.equal(obsolete.status, "stale");
    assert.equal(player.snapshot.state, after.state);
    assert.equal(player.snapshot.song_id, after.song_id);
    assert.equal(player.fresh, true);
  });
}

await test("RR-1 older recovery failure is ignored after newer recovery succeeds", async () => {
  const player = new PlayerCoordinator();
  seed(player, snapshot("PAUSED"));
  player.invalidateFreshness();
  const old = deferred();
  const newer = deferred();
  const oldRun = player.runPoll(() => old.promise, (x) => x, () => 300);
  const newerRun = player.runPoll(() => newer.promise, (x) => x, () => 200);
  newer.resolve(snapshot("PLAYING"));
  assert.equal((await newerRun).status, "accepted");
  old.reject(new ApiError(0, "network_error", "obsolete"));
  assert.equal((await oldRun).status, "stale");
  assert.equal(player.snapshot.state, "PLAYING");
  assert.equal(player.fresh, true);
});

await test("RR-1 current poll failure remains eligible for offline handling", async () => {
  const player = new PlayerCoordinator();
  seed(player, snapshot("PAUSED"));
  const current = await player.runPoll(
    async () => { throw new ApiError(503, "not_ready", "current failure"); },
    (x) => x,
    () => 100
  );
  assert.equal(current.status, "failed");
  assert.equal(current.error.status, 503);
  assert.equal(player.snapshot.state, "PAUSED");
});

// RR-2: metadata is descriptive only; it cannot mutate playback authority.
// COMPLETED fixtures carry Core's canonical terminal position (duration).
for (const state of ["PAUSED", "PLAYING", "COMPLETED", "FAILED"]) {
  await test(`RR-2 ${state} metadata hydration cannot restore invalidated authority`, () => {
    const player = new PlayerCoordinator();
    seed(player, state === "COMPLETED" ? snapshot(state, "song-a", 60, 60) : snapshot(state));
    const anchor = player.anchor;
    player.invalidateFreshness();
    const hydration = player.beginHydration("song-a");
    assert.equal(player.acceptHydration({ song_id: "song-a", track_count: 4 }, hydration), true);
    assert.equal(player.fresh, false);
    assert.equal(player.hasCurrentAuthority(100), false);
    assert.equal(player.anchor, anchor);
  });
}

await test("RR-2 metadata cannot escape command-outcome uncertainty; fresh snapshot can", async () => {
  const player = new PlayerCoordinator();
  seed(player, snapshot("PLAYING"));
  const result = await player.runCommand({
    action: "pause",
    invoke: async () => { throw new ApiError(0, "network_error", "unknown"); },
    reconcile: async () => snapshot("PAUSED"),
    now: () => 100,
  });
  assert.equal(result.status, "unknown");
  const anchor = player.anchor;
  const hydration = player.beginHydration("song-a");
  assert.equal(player.acceptHydration({ song_id: "song-a", track_count: 2 }, hydration), true);
  assert.equal(player.fresh, false);
  assert.equal(player.anchor, anchor);
  const recovery = await player.runPoll(
    async () => snapshot("PLAYING", "song-a", 12),
    (x) => x,
    () => 200
  );
  assert.equal(recovery.status, "accepted");
  assert.equal(player.hasCurrentAuthority(200), true);
});

// F5: bounded presentation correction, exact authority on discontinuities.
await test("F5 small backward drift never steps backward and converges", () => {
  const first = { ...snapshot("PLAYING"), position_seconds: 10 };
  const previous = reconcilePlaybackAnchor(null, first, 0);
  const next = { ...first, position_seconds: 10.8 };
  const corrected = reconcilePlaybackAnchor(previous, next, 1000);
  assert.equal(playbackFrame(corrected, 1000).position, 11);
  assert.equal(playbackFrame(corrected, 3000).position, 12.8);
});

await test("F5 small forward drift accelerates smoothly and converges", () => {
  const first = reconcilePlaybackAnchor(null, snapshot("PLAYING"), 0);
  const next = { ...snapshot("PLAYING"), position_seconds: 11.2 };
  const corrected = reconcilePlaybackAnchor(first, next, 1000);
  assert.equal(playbackFrame(corrected, 1000).position, 11);
  assert.equal(playbackFrame(corrected, 3000).position, 13.2);
});

await test("F5 large jump, pause, resume, song and terminal transitions re-anchor", () => {
  const playing = reconcilePlaybackAnchor(null, snapshot("PLAYING"), 0);
  assert.equal(reconcilePlaybackAnchor(playing, snapshot("PLAYING", "song-a", 20), 1000).positionSeconds, 20);
  const paused = reconcilePlaybackAnchor(playing, snapshot("PAUSED", "song-a", 11), 1000);
  assert.equal(paused.positionSeconds, 11);
  const resumed = reconcilePlaybackAnchor(paused, snapshot("PLAYING", "song-a", 11), 1100);
  assert.equal(resumed.positionSeconds, 11);
  assert.equal(reconcilePlaybackAnchor(playing, snapshot("PLAYING", "song-b", 1), 1000).positionSeconds, 1);
  assert.equal(reconcilePlaybackAnchor(playing, snapshot("COMPLETED", "song-a", 60), 1000).positionSeconds, 60);
});

// F6: native interactive contexts own their keys.
function targetMatching(token = null) {
  return {
    closest(selector) {
      return token && selector.includes(token) ? this : null;
    },
  };
}

function key(code, token = null) {
  return {
    code,
    target: targetMatching(token),
    defaultPrevented: false,
    repeat: false,
    metaKey: false,
    ctrlKey: false,
    altKey: false,
  };
}

await test("F6 Space works on document background", () => {
  assert.equal(transportShortcutAction(key("Space"), { play: true }), "play");
});

for (const token of ["summary", "button", "input", "textarea", "select", "a", "contenteditable", "role='button'"]) {
  await test(`F6 Space ignores interactive ${token}`, () => {
    assert.equal(transportShortcutAction(key("Space", token), { play: true }), null);
  });
}

await test("F6 Escape stops only from non-interactive context", () => {
  assert.equal(transportShortcutAction(key("Escape"), { stop: true }), "stop");
  assert.equal(transportShortcutAction(key("Escape", "input"), { stop: true }), null);
});

// ---- playback-contract remediation: classifier-driven coordinator -------

// Exact live proofs from the Core serializer (see app.test.mjs fixtures).
const unavailableTruth = {
  available: false,
  state: "COMPLETED",
  tempo_multiplier: 1.0,
  position_seconds: 0.0,
  duration_seconds: 0.0,
  events_sent: 0,
  error: null,
  song_id: null,
  title: null,
};
const zeroDurationCompleted = snapshot("COMPLETED", "song-a", 0, 0);

// Table-driven invalid-numeric matrix: no coerced value may cross any of
// the three authority entry points (command success, reconciliation,
// normal poll).
const invalidNumericPayloads = [
  ["null position", { position_seconds: null }],
  ["boolean position", { position_seconds: true }],
  ["array position", { position_seconds: [] }],
  ["string position", { position_seconds: "1" }],
  ["negative position", { position_seconds: -1 }],
  ["null duration", { duration_seconds: null }],
  ["boolean duration", { duration_seconds: false }],
  ["string duration", { duration_seconds: "60" }],
  ["negative duration", { duration_seconds: -5 }],
  ["null tempo", { tempo_multiplier: null }],
  ["boolean tempo", { tempo_multiplier: true }],
  ["string tempo", { tempo_multiplier: "1" }],
  ["zero tempo", { tempo_multiplier: 0 }],
  ["sub-bound tempo", { tempo_multiplier: 0.1 }],
  ["tempo 100", { tempo_multiplier: 100 }],
  ["boolean events", { events_sent: true }],
  ["string events", { events_sent: "1" }],
  ["fractional events", { events_sent: 1.5 }],
  ["negative events", { events_sent: -1 }],
];

for (const [label, overrides] of invalidNumericPayloads) {
  const payload = { ...snapshot("PAUSED"), ...overrides };
  assert.equal(classifyPlaybackPayload(payload), PLAYBACK_PAYLOAD_CLASS.INVALID, label);

  await test(`matrix ${label}: invalid command success never becomes authority`, async () => {
    const player = new PlayerCoordinator();
    seed(player, snapshot("PLAYING"));
    const result = await player.runCommand({
      action: "pause",
      invoke: async () => payload,
      reconcile: async () => snapshot("PAUSED"),
      now: () => 100,
    });
    assert.equal(result.status, "unknown");
    assert.equal(player.fresh, false);
    assert.equal(player.snapshot.state, "PLAYING");
    const recovered = await player.runPoll(async () => snapshot("PAUSED"), (x) => x, () => 200);
    assert.equal(recovered.status, "accepted");
    assert.equal(player.snapshot.state, "PAUSED");
  });

  await test(`matrix ${label}: invalid reconciliation cannot clear UNKNOWN`, async () => {
    const player = new PlayerCoordinator();
    seed(player, snapshot("PLAYING"));
    const result = await player.runCommand({
      action: "pause",
      invoke: async () => { throw new ApiError(0, "network_error", "offline"); },
      reconcile: async () => payload,
      now: () => 100,
    });
    assert.equal(result.status, "unknown");
    assert.equal(player.fresh, false);
    assert.notEqual(player.snapshot.state, "PAUSED");
  });

  await test(`matrix ${label}: invalid normal poll never overwrites authority`, async () => {
    const player = new PlayerCoordinator();
    seed(player, snapshot("PLAYING"));
    const poll = await player.runPoll(async () => payload, (x) => x, () => 200);
    assert.notEqual(poll.status, "accepted");
    assert.equal(player.snapshot.state, "PLAYING");
    assert.equal(player.fresh, true);
  });
}

await test("zero-duration COMPLETED 0/0 becomes valid fresh authority", async () => {
  const player = new PlayerCoordinator();
  seed(player, snapshot("PLAYING"));
  const poll = await player.runPoll(async () => zeroDurationCompleted, (x) => x, () => 100);
  assert.equal(poll.status, "accepted");
  assert.equal(player.fresh, true);
  assert.equal(player.snapshot.state, "COMPLETED");
  assert.equal(player.snapshot.position_seconds, 0);
  const frame = player.currentFrame(200);
  assert.equal(frame.position, 0);
  assert.equal(frame.progress, 0);
});

await test("valid unavailable truth installs synced no-anchor authority", async () => {
  const player = new PlayerCoordinator();
  seed(player, snapshot("PLAYING"));
  const poll = await player.runPoll(async () => unavailableTruth, (x) => x, () => 100);
  assert.equal(poll.status, "accepted");
  assert.equal(player.fresh, true, "unavailable truth is authoritative");
  assert.equal(player.anchor, null, "no fake playback anchor");
  assert.equal(player.snapshot.available, false);
  assert.equal(player.evaluateFreshness(200), true);
  assert.equal(player.hasCurrentAuthority(200), true);
  // No transport control may be offered from unavailable truth.
  const enablement = controlEnablementLike(player.snapshot);
  assert.deepEqual(enablement, { play: false, pause: false, resume: false, stop: false });
  // A later available poll restores normal authority.
  const restored = await player.runPoll(async () => snapshot("PLAYING"), (x) => x, () => 300);
  assert.equal(restored.status, "accepted");
  assert.notEqual(player.anchor, null);
  assert.equal(player.snapshot.state, "PLAYING");
});

function controlEnablementLike(snapshot) {
  if (!snapshot || snapshot.available !== true) {
    return { play: false, pause: false, resume: false, stop: false };
  }
  switch (snapshot.state) {
    case "IDLE":
    case "COMPLETED":
    case "FAILED":
      return { play: true, pause: false, resume: false, stop: false };
    case "PLAYING":
      return { play: false, pause: true, resume: false, stop: true };
    case "PAUSED":
      return { play: false, pause: false, resume: true, stop: true };
    default:
      return { play: false, pause: false, resume: false, stop: false };
  }
}

await test("2xx valid-unavailable command response is outcome unknown, not success", async () => {
  const player = new PlayerCoordinator();
  seed(player, snapshot("PLAYING"));
  const result = await player.runCommand({
    action: "pause",
    invoke: async () => unavailableTruth,
    reconcile: async () => snapshot("PAUSED"),
    now: () => 100,
  });
  assert.equal(result.status, "unknown");
  assert.equal(player.fresh, false);
  assert.equal(player.snapshot.state, "PLAYING");
});

await test("unknown outcome + valid-unavailable reconciliation poll establishes synced unavailable authority", async () => {
  const player = new PlayerCoordinator();
  seed(player, snapshot("PLAYING"));
  const result = await player.runCommand({
    action: "pause",
    invoke: async () => { throw new ApiError(0, "network_error", "offline"); },
    reconcile: async () => snapshot("PAUSED"),
    now: () => 100,
  });
  assert.equal(result.status, "unknown");
  assert.equal(player.fresh, false);
  // The follow-up reconciliation poll is authoritative: valid unavailable
  // truth clears uncertainty with an honest unavailable state.
  const reconciled = await player.runPoll(async () => unavailableTruth, (x) => x, () => 200);
  assert.equal(reconciled.status, "accepted");
  assert.equal(player.snapshot.available, false);
  assert.equal(player.anchor, null);
  assert.equal(player.fresh, true);
});

await test("valid-unavailable reconciliation GET after a proven command installs unavailable authority", async () => {
  const player = new PlayerCoordinator();
  seed(player, snapshot("PLAYING"));
  const result = await player.runCommand({
    action: "pause",
    invoke: async () => snapshot("PAUSED"),
    reconcile: async () => unavailableTruth,
    now: () => 100,
  });
  assert.equal(result.status, "success");
  assert.equal(player.snapshot.available, false);
  assert.equal(player.anchor, null);
  assert.equal(player.fresh, true);
});

console.log(failures ? "PLAYER APPLICATION TESTS FAILED" : "ALL PLAYER APPLICATION TESTS PASSED");
if (failures) process.exitCode = 1;
