// PIANO-UI-003 U3-R3: real HTTP command-outcome integration harness.
//
// Exercises the COMPLETE command path end-to-end — a real loopback HTTP
// server that APPLIES each mutating command before answering, the real
// packaged ApiClient (Node fetch/undici) and the real PlayerCoordinator —
// for the five required PAUSE outcomes plus shared Play/Resume/Stop
// coverage, bad reconciliation snapshots and bad normal polls:
//
//   CASE 1 valid success            -> confirmed success
//   CASE 2 typed 409                -> confirmed rejection (Core unchanged)
//   CASE 3 truncated 200 body       -> network_error -> OUTCOME UNKNOWN
//   CASE 4 complete malformed 200   -> invalid_response -> OUTCOME UNKNOWN
//   CASE 5 complete unusable JSON   -> unusable payload -> OUTCOME UNKNOWN
//
// Classification is asserted against the request PHASE (`error.phase`),
// never against exception message text: Node undici raises
// "TypeError: terminated" for a truncated body while Chromium raises
// "TypeError: Failed to fetch"; both must classify identically.
// The retained real-Chromium counterpart is
// tests/weblogic/browser_command_outcome.mjs.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { performance } from "node:perf_hooks";
import {
  ApiClient,
  ApiError,
  PlayerCoordinator,
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

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

// ---- controlled Core stub ------------------------------------------------

const core = {
  // Canonical truth: the server applies commands BEFORE responding.
  playback: {
    available: true,
    state: "IDLE",
    song_id: null,
    title: null,
    position_seconds: 0,
    duration_seconds: 0,
    tempo_multiplier: 1,
    events_sent: 0,
    error: null,
  },
  // How the NEXT mutating command answers (default: honest success).
  commandMode: "ok",
  applyCommand: true,
  // Ordered payloads for GET /api/v1/playback; falls back to core truth.
  getQueue: [],
  overridePayload: null,
  getOverride: null,
};

function snapshot(state, songId = "song-a", position = 10, duration = 60) {
  return {
    available: true,
    state,
    song_id: state === "IDLE" ? null : songId,
    title: state === "IDLE" ? null : songId,
    position_seconds: position,
    duration_seconds: duration,
    // Core always publishes a real tempo number (engine.tempo_scale).
    tempo_multiplier: 1,
    events_sent: 0,
    error: null,
  };
}

function resetCore(playback) {
  core.playback = playback;
  core.commandMode = "ok";
  core.applyCommand = true;
  core.getQueue = [];
  core.overridePayload = null;
  core.getOverride = null;
}

function writeJson(res, payload, status = 200) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "content-type": "application/json",
    "content-length": Buffer.byteLength(body),
    "cache-control": "no-store",
  });
  res.end(body);
}

const GARBAGE = { available: true, state: "GARBAGE", position_seconds: 0, duration_seconds: 0 };
// Legitimate authoritative unavailable truth, exactly as the Core
// serializer publishes it after MidiOutputAdapter.close() (live proof).
const CORE_UNAVAILABLE = {
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
// Invalid-numeric variants that must never cross the authority boundary.
const NULL_NUMERICS = (base) => ({
  ...base,
  position_seconds: null,
  duration_seconds: null,
  tempo_multiplier: null,
});
const TEMPO_100 = (base) => ({ ...base, tempo_multiplier: 100 });

const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://127.0.0.1");
  if (req.method === "GET" && url.pathname === "/api/v1/playback") {
    const override = core.getOverride;
    if (override !== null && override !== undefined) {
      core.getOverride = null;
      writeJson(res, override);
      return;
    }
    const next = core.getQueue.shift();
    writeJson(res, next !== undefined ? next : core.playback);
    return;
  }
  if (req.method === "POST" && url.pathname.startsWith("/api/v1/playback/")) {
    const action = url.pathname.split("/").pop();
    for await (const chunk of req) void chunk; // drain request body
    const mode = core.commandMode;
    if (mode === "typed409Truncate") {
      // A typed 409 whose envelope never completes: the status arrives but
      // the proof body does not, so nothing can be confirmed.
      core.applyCommand = false;
      const body = JSON.stringify({
        error: { code: "invalid_state_transition", message: "controlled rejection" },
      });
      res.writeHead(409, {
        "content-type": "application/json",
        "content-length": Buffer.byteLength(body),
        "cache-control": "no-store",
      });
      res.flushHeaders();
      res.write(body.slice(0, Math.floor(body.length / 2)));
      await sleep(20);
      res.socket.destroy();
      return;
    }
    if (mode === "typed409") {
      core.applyCommand = false;
      writeJson(
        res,
        { error: { code: "invalid_state_transition", message: "controlled rejection" } },
        409
      );
      return;
    }
    // The server applies the command FIRST (Core may have applied it even
    // when the response later fails to prove the outcome).
    core.applyCommand = true;
    if (action === "play") core.playback = snapshot("PLAYING", "song-a", 0, 60);
    else if (action === "pause") core.playback = { ...core.playback, state: "PAUSED", position_seconds: 10 };
    else if (action === "resume") core.playback = { ...core.playback, state: "PLAYING", position_seconds: 11 };
    else if (action === "stop") core.playback = snapshot("IDLE", null, 0, 0);
    const body = JSON.stringify(core.playback);
    if (mode === "ok") {
      writeJson(res, core.playback);
      return;
    }
    if (mode === "truncate") {
      // 200 headers promise the full body; the socket dies mid-body.
      res.writeHead(200, {
        "content-type": "application/json",
        "content-length": Buffer.byteLength(body),
        "cache-control": "no-store",
      });
      res.flushHeaders();
      res.write(body.slice(0, Math.max(1, Math.floor(body.length / 3))));
      await sleep(20);
      res.socket.destroy();
      return;
    }
    if (mode === "malformed") {
      res.writeHead(200, {
        "content-type": "application/json",
        "content-length": 13,
        "cache-control": "no-store",
      });
      res.end("{invalid-json");
      return;
    }
    if (mode === "garbage") {
      writeJson(res, GARBAGE);
      return;
    }
    if (mode === "payload") {
      // Serve an exact caller-provided payload as a 200 success response.
      writeJson(res, core.overridePayload);
      return;
    }
    if (mode === "unavailable") {
      // Legitimate authoritative unavailable truth (closed-adapter shape).
      writeJson(res, CORE_UNAVAILABLE);
      return;
    }
    if (mode === "getPayload") {
      // Next GET /playback answers with an exact caller-provided payload.
      writeJson(res, core.getOverride);
      return;
    }
    if (mode === "empty") {
      res.writeHead(200, {
        "content-type": "application/json",
        "content-length": 0,
        "cache-control": "no-store",
      });
      res.end();
      return;
    }
    throw new Error(`unknown command mode ${mode}`);
  }
  writeJson(res, { error: { code: "not_found", message: "not found" } }, 404);
});

await new Promise((done) => server.listen(0, "127.0.0.1", done));
const base = `http://127.0.0.1:${server.address().port}`;
const client = new ApiClient(
  (url, options) => fetch(new URL(url, base), options),
  2000
);

/** Seed one authoritative snapshot through the REAL GET path. */
async function seedAuthority(playback) {
  resetCore(playback);
  const player = new PlayerCoordinator();
  const poll = await player.runPoll(
    () => client.playback(),
    (value) => value,
    () => performance.now()
  );
  assert.equal(poll.status, "accepted");
  assert.equal(player.fresh, true);
  return player;
}

/** Run one real mutating command through the coordinator. */
function command(player, action, requestedSongId = null) {
  return player.runCommand({
    action,
    requestedSongId,
    invoke: () => {
      if (action === "play") return client.play(requestedSongId, 1);
      return client[action]();
    },
    reconcile: () => client.playback(),
    now: () => performance.now(),
  });
}

/** Authoritative reconciliation through the REAL GET path. */
async function reconcile(player) {
  const poll = await player.runPoll(
    () => client.playback(),
    (value) => value,
    () => performance.now()
  );
  return poll;
}

await test("CASE 1: real PAUSE success confirms PAUSED authority", async () => {
  const player = await seedAuthority(snapshot("PLAYING"));
  const result = await command(player, "pause");
  assert.equal(result.status, "success");
  assert.equal(player.fresh, true);
  assert.equal(player.snapshot.state, "PAUSED");
  const recovery = await reconcile(player);
  assert.equal(recovery.status, "accepted");
  assert.equal(player.snapshot.state, "PAUSED");
});

await test("CASE 2: real typed 409 rejection proves Core unchanged", async () => {
  const player = await seedAuthority(snapshot("PLAYING"));
  core.commandMode = "typed409";
  const result = await command(player, "pause");
  assert.equal(result.status, "rejected");
  assert.equal(result.error.status, 409);
  assert.equal(result.error.code, "invalid_state_transition");
  assert.equal(player.fresh, true);
  assert.equal(player.snapshot.state, "PLAYING");
  assert.equal(core.playback.state, "PLAYING");
});

await test("CASE 3: real truncated 200 body is network_error then UNKNOWN then reconciled PAUSED", async () => {
  const player = await seedAuthority(snapshot("PLAYING"));
  core.commandMode = "truncate"; // server applies PAUSE, then the body dies
  await assert.rejects(
    client.pause(),
    (error) =>
      error instanceof ApiError &&
      error.status === 0 &&
      error.code === "network_error" &&
      error.phase === "body"
  );
  const result = await command(player, "pause");
  assert.equal(result.status, "unknown");
  assert.equal(player.fresh, false, "old freshness must be invalidated");
  assert.equal(player.snapshot.state, "PLAYING", "no unusable snapshot installed");
  assert.equal(core.playback.state, "PAUSED", "Core actually applied the command");
  const recovery = await reconcile(player);
  assert.equal(recovery.status, "accepted");
  assert.equal(player.fresh, true);
  assert.equal(player.snapshot.state, "PAUSED", "final state converges to Core");
});

await test("CASE 4: real complete malformed 200 body is invalid_response then UNKNOWN then reconciled PAUSED", async () => {
  const player = await seedAuthority(snapshot("PLAYING"));
  core.commandMode = "malformed";
  await assert.rejects(
    client.pause(),
    (error) =>
      error instanceof ApiError &&
      error.status === 200 &&
      error.code === "invalid_response" &&
      error.phase === "classify"
  );
  const result = await command(player, "pause");
  assert.equal(result.status, "unknown");
  assert.equal(player.fresh, false);
  assert.equal(player.snapshot.state, "PLAYING");
  const recovery = await reconcile(player);
  assert.equal(recovery.status, "accepted");
  assert.equal(player.snapshot.state, "PAUSED");
});

await test("CASE 5: real complete unusable success JSON never becomes authority", async () => {
  const player = await seedAuthority(snapshot("PLAYING"));
  core.commandMode = "garbage"; // {"available":true,"state":"GARBAGE"}
  const payload = await client.pause(); // ApiClient parses fine; payload is unusable
  assert.deepEqual(payload, GARBAGE);
  const result = await command(player, "pause");
  assert.equal(result.status, "unknown");
  assert.equal(result.error.code, "invalid_response");
  assert.equal(player.fresh, false, "invalid success payload invalidates freshness");
  assert.equal(player.snapshot.state, "PLAYING", "GARBAGE was not installed");
  assert.notEqual(player.snapshot.state, "GARBAGE");
  const recovery = await reconcile(player);
  assert.equal(recovery.status, "accepted");
  assert.equal(player.snapshot.state, "PAUSED", "authoritative GET determines truth");
});

await test("truncated 409 body cannot fake a confirmed rejection", async () => {
  const player = await seedAuthority(snapshot("PLAYING"));
  core.commandMode = "typed409Truncate";
  await assert.rejects(
    client.pause(),
    (error) =>
      error instanceof ApiError &&
      error.status === 0 &&
      error.code === "network_error" &&
      error.phase === "body"
  );
  const result = await command(player, "pause");
  assert.equal(result.status, "unknown", "an unproven 409 is outcome unknown, not rejected");
  assert.equal(player.fresh, false);
  assert.equal(player.snapshot.state, "PLAYING");
  const recovery = await reconcile(player);
  assert.equal(recovery.status, "accepted");
  assert.equal(player.snapshot.state, "PLAYING", "Core never applied the command");
});

await test("empty 200 success body is invalid_response and outcome unknown", async () => {
  const player = await seedAuthority(snapshot("PLAYING"));
  core.commandMode = "empty";
  await assert.rejects(
    client.pause(),
    (error) =>
      error instanceof ApiError &&
      error.status === 200 &&
      error.code === "invalid_response" &&
      error.phase === "classify"
  );
  const result = await command(player, "pause");
  assert.equal(result.status, "unknown");
  assert.equal(player.fresh, false);
  const recovery = await reconcile(player);
  assert.equal(player.snapshot.state, "PAUSED");
  void recovery;
});

for (const action of ["play", "resume", "stop"]) {
  await test(`${action} unusable success payload shares the safe unknown semantics`, async () => {
    const initial =
      action === "play"
        ? snapshot("COMPLETED", "song-a", 60, 60)
        : action === "resume"
          ? snapshot("PAUSED")
          : snapshot("PLAYING");
    const player = await seedAuthority(initial);
    if (action === "play") player.selectSong({ song_id: "song-a", title: "song-a" });
    core.commandMode = "garbage";
    const result = await command(player, action, action === "play" ? "song-a" : null);
    assert.equal(result.status, "unknown");
    assert.equal(player.fresh, false);
    assert.notEqual(player.snapshot.state, "GARBAGE");
    const recovery = await reconcile(player);
    assert.equal(recovery.status, "accepted");
    assert.equal(player.fresh, true);
    assert.equal(player.snapshot.state, core.playback.state);
  });
}

await test("unusable reconciliation payload cannot clear UNKNOWN; the next good GET does", async () => {
  const player = await seedAuthority(snapshot("PLAYING"));
  core.commandMode = "truncate";
  const result = await command(player, "pause");
  assert.equal(result.status, "unknown");
  // Reconciliation GET 1: valid JSON, unusable playback payload.
  core.getQueue = [GARBAGE];
  const bad = await reconcile(player);
  assert.notEqual(bad.status, "accepted");
  assert.equal(player.fresh, false, "uncertainty survives a bad reconciliation GET");
  assert.equal(player.snapshot.state, "PLAYING", "no GARBAGE state installed");
  assert.notEqual(player.snapshot.state, "GARBAGE");
  // Reconciliation GET 2: valid authoritative PAUSED.
  core.getQueue = [];
  const good = await reconcile(player);
  assert.equal(good.status, "accepted");
  assert.equal(player.fresh, true);
  assert.equal(player.snapshot.state, "PAUSED");
});

await test("unusable normal poll never overwrites fresh PLAYING authority", async () => {
  const player = await seedAuthority(snapshot("PLAYING", "song-a", 10, 60));
  core.playback = { ...snapshot("PLAYING", "song-a", 14, 60) };
  core.getQueue = [GARBAGE];
  const bad = await reconcile(player);
  assert.notEqual(bad.status, "accepted");
  assert.equal(player.snapshot.state, "PLAYING", "GARBAGE never rendered as authoritative");
  assert.equal(player.snapshot.position_seconds, 10, "prior authority untouched");
  assert.equal(player.fresh, true, "freshness follows the existing recovery policy");
  core.getQueue = [];
  const good = await reconcile(player);
  assert.equal(good.status, "accepted");
  assert.equal(player.snapshot.position_seconds, 14, "later good poll restores Core truth");
});

await test("A: real PAUSE with null numeric fields is UNKNOWN then reconciled PAUSED", async () => {
  const player = await seedAuthority(snapshot("PLAYING"));
  core.commandMode = "payload"; // server applies PAUSE, then answers 200 with null numerics
  core.overridePayload = NULL_NUMERICS({ ...snapshot("PAUSED") });
  const payload = await client.pause(); // parses fine; numerics are not numbers
  assert.equal(payload.position_seconds, null);
  const result = await command(player, "pause");
  assert.equal(result.status, "unknown");
  assert.equal(player.fresh, false);
  assert.equal(player.snapshot.state, "PLAYING", "null-numeric payload not installed");
  const recovery = await reconcile(player);
  assert.equal(recovery.status, "accepted");
  assert.equal(player.snapshot.state, "PAUSED");
});

await test("B: real PAUSE with tempo_multiplier 100 is UNKNOWN then reconciled PAUSED", async () => {
  const player = await seedAuthority(snapshot("PLAYING"));
  core.commandMode = "payload";
  core.overridePayload = TEMPO_100({ ...snapshot("PAUSED") });
  const result = await command(player, "pause");
  assert.equal(result.status, "unknown");
  assert.equal(player.fresh, false);
  assert.equal(player.snapshot.state, "PLAYING");
  const recovery = await reconcile(player);
  assert.equal(recovery.status, "accepted");
  assert.equal(player.snapshot.state, "PAUSED");
});

await test("C: real reconciliation with invalid numeric payload keeps uncertainty", async () => {
  const player = await seedAuthority(snapshot("PLAYING"));
  core.commandMode = "truncate";
  const result = await command(player, "pause");
  assert.equal(result.status, "unknown");
  core.getQueue = [TEMPO_100({ ...snapshot("PAUSED") })];
  const bad = await reconcile(player);
  assert.notEqual(bad.status, "accepted");
  assert.equal(player.fresh, false, "uncertainty survives the invalid reconciliation");
  assert.equal(player.snapshot.state, "PLAYING");
  core.getQueue = [];
  const good = await reconcile(player);
  assert.equal(good.status, "accepted");
  assert.equal(player.snapshot.state, "PAUSED");
});

await test("D: real normal poll with invalid numeric payload is not installed", async () => {
  const player = await seedAuthority(snapshot("PLAYING", "song-a", 10, 60));
  core.playback = { ...snapshot("PLAYING", "song-a", 14, 60) };
  core.getQueue = [NULL_NUMERICS({ ...snapshot("PLAYING", "song-a", 14, 60) })];
  const bad = await reconcile(player);
  assert.notEqual(bad.status, "accepted");
  assert.equal(player.snapshot.position_seconds, 10, "prior authority untouched");
  core.getQueue = [];
  const good = await reconcile(player);
  assert.equal(good.status, "accepted");
  assert.equal(player.snapshot.position_seconds, 14);
});

await test("real PAUSE answering 2xx valid-unavailable is UNKNOWN then reconciled PAUSED", async () => {
  const player = await seedAuthority(snapshot("PLAYING"));
  core.commandMode = "unavailable"; // 2xx + available:false cannot prove success
  const result = await command(player, "pause");
  assert.equal(result.status, "unknown");
  assert.equal(player.fresh, false);
  assert.equal(player.snapshot.state, "PLAYING");
  // The authoritative GET serves Core's real (available) truth.
  const recovery = await reconcile(player);
  assert.equal(recovery.status, "accepted");
  assert.equal(player.fresh, true);
  assert.equal(player.snapshot.state, "PAUSED");
});

await test("real unavailable reconciliation installs honest synced unavailable authority", async () => {
  const player = await seedAuthority(snapshot("PLAYING"));
  core.commandMode = "truncate";
  const result = await command(player, "pause");
  assert.equal(result.status, "unknown");
  core.getOverride = CORE_UNAVAILABLE;
  const recovery = await reconcile(player);
  assert.equal(recovery.status, "accepted");
  assert.equal(player.fresh, true, "unavailable truth is authoritative");
  assert.equal(player.snapshot.available, false);
  assert.equal(player.anchor, null, "no fake playback anchor");
  // A later available poll restores normal authority.
  const restored = await reconcile(player);
  assert.equal(restored.status, "accepted");
  assert.notEqual(player.anchor, null);
  assert.equal(player.snapshot.state, "PAUSED");
});

await test("real zero-duration COMPLETED 0/0 is valid authoritative truth", async () => {
  const player = new PlayerCoordinator();
  resetCore(snapshot("COMPLETED", "song-a", 0, 0));
  const poll = await player.runPoll(() => client.playback(), (x) => x, () => performance.now());
  assert.equal(poll.status, "accepted");
  assert.equal(player.fresh, true);
  assert.equal(player.snapshot.state, "COMPLETED");
  assert.equal(player.snapshot.position_seconds, 0);
  assert.equal(player.snapshot.duration_seconds, 0);
});

await new Promise((done) => server.close(done));
if (failures > 0) {
  console.error(`${failures} real command-outcome integration test(s) failed`);
  process.exitCode = 1;
} else {
  console.log("ALL REAL COMMAND-OUTCOME INTEGRATION TESTS PASSED");
}
