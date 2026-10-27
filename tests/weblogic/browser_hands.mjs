// PIANO-UI-007: REAL Chromium/HTTP 3D-hands validation.
//
// Everything is real: packaged Google Chrome (headless), a real loopback
// HTTP appliance serving the real packaged app.js/handmotion.js/
// handrender.js + the vendored Three.js over HTTP, synthetic-but-real
// canonical /api/v1 payloads (playback anchor truth, score projections,
// fingering plans), the real browser fetch stack, the real import map,
// real WebGL2/WebGPU rendering under SwiftShader and the real hands
// wiring. Loopback only, controlled; never touches a device, deployed
// service, or non-loopback endpoint.
//
// HONEST PERFORMANCE/BACKEND SCOPE: everything rendered here is
// headless Chromium on SwiftShader (software). Results are NEVER
// device FPS, tablet performance, Pi performance or GPU hardware
// performance.
//
// Run with TMPDIR=/var/tmp when the host tmpfs is full.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { createServer } from "node:http";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { extname, join, resolve } from "node:path";

const require = createRequire(import.meta.url);
const WebSocket = require("ws");
const ROOT = resolve(import.meta.dirname, "../..");
const STATIC = join(ROOT, "src/piano_core/web/static");
const CHROME = process.env.CHROME || "/usr/bin/google-chrome";

let failures = 0;
let passes = 0;
const pageErrors = [];
const evidence = {};
async function test(name, fn) {
  const errorCount = pageErrors.length;
  try {
    await fn();
    assert.equal(
      pageErrors.length,
      errorCount,
      `uncaught page error: ${pageErrors.slice(errorCount).join(" | ")}`
    );
    passes += 1;
    console.log(`ok - ${name}`);
  } catch (error) {
    failures += 1;
    console.error(`FAIL - ${name}`);
    console.error(error);
  }
}

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

/* ---------------- synthetic canonical appliance ---------------- */

function makeScore({ songId, notes, duration, sourceChar = "a" }) {
  return {
    schema_version: 1,
    song_id: songId,
    source_version: `score-sha256:${sourceChar.repeat(64)}`,
    title: `Song ${songId}`,
    duration_seconds: duration,
    time_basis: "song_seconds",
    note_count: notes.length,
    notes,
    diagnostics: {
      percussion_note_ons: 0,
      outside_keyboard_note_ons: 0,
      unpaired_note_offs: 0,
      retriggered_note_ons: 0,
      unterminated_note_ons: 0,
    },
    notation: {
      supported: ["pitch", "onset", "duration", "channel", "velocity"],
      unsupported: ["measure", "beat", "hand", "fingering", "tempo_map"],
    },
  };
}

/**
 * Deterministic test-data plan generator (NOT a fingering engine): one
 * greedy pass that keeps every hand/finger occupancy interval disjoint
 * so the payload is always acceptable to the app's strict validation.
 * Designed songs exercise held+moving material safely.
 */
function makeHandsPlan(score, { override = null } = {}) {
  const playable = score.notes.filter(
    (n) => n.midi >= 21 && n.midi <= 108 && n.channel !== 10
  );
  const targets = override
    ? override(playable)
    : greedyPlan(playable);
  return {
    schema_version: 1,
    engine_version: "balanced-engine-v1",
    plan_id: `fingering-sha256:${"c".repeat(64)}`,
    song_id: score.song_id,
    source_version: score.source_version,
    version: 1,
    generation: 1,
    strategy: "balanced_v1",
    provenance: "DETERMINISTIC_INFERENCE",
    targets,
    warnings: ["source_contains_no_authored_hand_or_fingering_metadata"],
    excluded_note_count: 0,
    config: {
      strategy: "balanced_v1",
      max_hand_span_semitones: 12,
      alternative_count: 1,
    },
    capabilities: { manual_overrides: false, finger_sensitive_grading: false },
  };
}

function greedyPlan(playable) {
  const free = { LEFT: [1, 2, 3, 4, 5], RIGHT: [1, 2, 3, 4, 5] };
  const busy = { LEFT: new Map(), RIGHT: new Map() }; // finger -> end time
  const targets = [];
  const groups = new Map();
  for (const note of playable) {
    if (!groups.has(note.onset_seconds)) groups.set(note.onset_seconds, []);
    groups.get(note.onset_seconds).push(note);
  }
  for (const onset of [...groups.keys()].sort((a, b) => a - b)) {
    const notes = groups.get(onset).sort((a, b) => a.midi - b.midi);
    const lower = notes.filter((n) => n.midi < 60);
    const upper = notes.filter((n) => n.midi >= 60);
    const assign = (list, hand) => {
      const orderFingers = hand === "LEFT" ? [5, 4, 3, 2, 1] : [1, 2, 3, 4, 5];
      list.forEach((note, slot) => {
        const prefer = orderFingers[slot % orderFingers.length];
        const candidates = [prefer, ...orderFingers.filter((f) => f !== prefer)];
        let chosen = null;
        for (const finger of candidates) {
          const end = busy[hand].get(finger) ?? -1;
          if (end <= note.onset_seconds) {
            chosen = finger;
            break;
          }
        }
        if (chosen === null) chosen = prefer; // degenerate: still valid data
        busy[hand].set(chosen, note.onset_seconds + note.duration_seconds);
        targets.push({
          score_index: note.index,
          target_id: `score:${note.index}`,
          midi: note.midi,
          channel: note.channel,
          onset_seconds: note.onset_seconds,
          duration_seconds: note.duration_seconds,
          hand,
          finger: chosen,
          alternatives: [],
        });
      });
    };
    assign(lower, "LEFT");
    assign(upper, "RIGHT");
  }
  return targets.sort((a, b) => a.score_index - b.score_index);
}

function simpleNotes(count, duration) {
  const notes = [];
  for (let i = 0; i < count; i += 1) {
    notes.push({
      index: i,
      midi: 36 + ((i * 5) % 60),
      channel: 1,
      velocity: 40 + (i % 80),
      onset_seconds: (i / count) * duration,
      duration_seconds: Math.min(0.5, (duration / count) * 0.4),
    });
  }
  return notes;
}

const SONG_DURATION = 120;
const core = {
  epoch: "hands-runtime-a",
  playback: {
    state: "IDLE",
    songId: null,
    position: 0,
    playingSince: null,
    tempo: 1.0,
  },
  learning: null,
  scoreFailures: false,
  fingeringFailures: false,
  blockVendor: false,
  blockAsset: false,
  scores: new Map(),
  plans: new Map(),
  scoreRequests: 0,
  musicalCommands: [],
};

function playbackSnapshot() {
  const pb = core.playback;
  const duration = core.scores.get(pb.songId)?.duration_seconds ?? SONG_DURATION;
  let position = pb.position;
  if (pb.state === "PLAYING" && pb.playingSince !== null) {
    position = pb.position + (Date.now() - pb.playingSince) / 1000;
  }
  position = Math.max(0, Math.min(position, duration));
  return {
    available: true,
    state: pb.state,
    tempo_multiplier: pb.tempo,
    position_seconds: Math.round(position * 1000) / 1000,
    duration_seconds: duration,
    events_sent: 0,
    error: null,
    song_id: pb.songId,
    title: pb.songId ? `Song ${pb.songId}` : null,
  };
}

function learningSnapshot() {
  return {
    available: true, state: "IDLE", song_id: null, title: null,
    step_index: null, total_steps: null, completed_steps: 0,
    incorrect_attempts: 0, progress: 0.0, target: null, started_at: null,
    ended_at: null, error: null, midi_connection: "CONNECTED",
    session_id: null, mode: null, hand_selection: null, section: null,
    target_tempo: null, accepted_notes: [], latest_outcome: "NONE",
    repetition: { current: 0, required: 1, successful: 0, failed: 0 },
    generation: 0, version: 0, updated_at: null,
    capabilities: {
      supported_modes: ["WAIT_FOR_NOTE", "SECTION_PRACTICE", "REPEAT_UNTIL_CORRECT"],
      missed_outcome: false, hand_selection: false, rhythm_grading: false,
      persistence: "runtime_only", section_basis: "song_time_seconds",
    },
  };
}

function noteSnapshot() {
  return {
    schema_version: 1,
    runtime_epoch: core.epoch,
    sequence: 1,
    physical: {
      notes: [], outside_keyboard_count: 0,
      sustain: { value: 0, pressed: false },
      midi_connection: "CONNECTED", revision: 1,
    },
    playback: {
      available: true, notes: [], voices: [],
      outside_keyboard_count: 0, sustain_channels: [],
    },
  };
}

function json(response, value, status = 200) {
  const body = JSON.stringify(value);
  response.writeHead(status, {
    "content-type": "application/json",
    "content-length": Buffer.byteLength(body),
    "cache-control": "no-store",
  });
  response.end(body);
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url, "http://127.0.0.1");
  if (request.method !== "GET" && /^\/api\/v1\/(playback|practice|learning)(\/|$)/.test(url.pathname)) {
    core.musicalCommands.push(`${request.method} ${url.pathname}`);
  }
  if (url.pathname.startsWith("/ui/vendor/") && core.blockVendor) {
    response.writeHead(404).end();
    return;
  }
  if (url.pathname === "/ui/assets/piano-hand-tier-a.glb" && core.blockAsset) {
    response.writeHead(404).end();
    return;
  }
  if (url.pathname === "/" || url.pathname === "/ui") {
    response.writeHead(302, { location: "/ui/" });
    response.end();
    return;
  }
  if (url.pathname.startsWith("/ui/")) {
    const name = url.pathname.slice(4) || "index.html";
    const path = join(STATIC, name);
    try {
      const body = await readFile(path);
      const types = {
        ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
        ".svg": "image/svg+xml", ".webmanifest": "application/manifest+json",
        ".txt": "text/plain",
      };
      response.writeHead(200, {
        "content-type": types[extname(path)] || "application/octet-stream",
        "cache-control": "no-store",
      });
      response.end(body);
    } catch {
      response.writeHead(404).end();
    }
    return;
  }
  if (url.pathname === "/api/v1/live-notes" || url.pathname === "/api/v1/live-notes/stream") {
    if (url.pathname.endsWith("/stream")) {
      response.writeHead(200, {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-cache",
        connection: "close",
      });
      response.write(`id: 1\nevent: snapshot\ndata: ${JSON.stringify(noteSnapshot())}\n\n`);
      request.on("close", () => response.end());
      return;
    }
    json(response, noteSnapshot());
    return;
  }
  if (url.pathname === "/api/v1/health") {
    json(response, {
      status: "ok",
      application: { state: "READY", since: "2026-09-20T00:00:00Z" },
      midi_input: { connection: "CONNECTED" },
      library: { ready: true, entries: 1 },
      playback: playbackSnapshot(),
    });
    return;
  }
  if (url.pathname === "/api/v1/state") {
    json(response, {
      midi_connection: "CONNECTED", sustain: { pressed: false, value: 0 },
      pressed_notes: [], last_input: null, revision: 1,
    });
    return;
  }
  if (url.pathname === "/api/v1/led") {
    json(response, {
      available: false, configured: false, enabled: false, connection: "DISABLED",
      mode: null, brightness: null, active_note_count: 0, practice_active: false,
      last_error: null, transport: null, dropped_events: 0, frames_sent: 0,
    });
    return;
  }
  if (url.pathname === "/api/v1/practice" || url.pathname === "/api/v1/learning") {
    json(response, learningSnapshot());
    return;
  }
  if (url.pathname.startsWith("/api/v1/score/")) {
    core.scoreRequests += 1;
    if (core.scoreFailures) {
      json(response, { error: { code: "internal_error", message: "synthetic", status: 500 } }, 500);
      return;
    }
    const songId = decodeURIComponent(url.pathname.slice("/api/v1/score/".length));
    const score = core.scores.get(songId);
    if (!score) {
      json(response, { error: { code: "song_not_found", message: "unknown", status: 404 } }, 404);
      return;
    }
    json(response, score);
    return;
  }
  if (url.pathname.startsWith("/api/v1/fingering/")) {
    if (core.fingeringUnsupported) {
      json(response, { error: { code: "fingering_unsupported", message: "held fingers leave no valid assignment for the new attack", status: 422 } }, 422);
      return;
    }
    if (core.fingeringFailures) {
      json(response, { error: { code: "fingering_unavailable", message: "synthetic", status: 503 } }, 503);
      return;
    }
    const songId = decodeURIComponent(url.pathname.slice("/api/v1/fingering/".length));
    const score = core.scores.get(songId);
    const plan = core.plans.get(songId);
    if (!score || !plan) {
      json(response, { error: { code: "song_not_found", message: "unknown", status: 404 } }, 404);
      return;
    }
    json(response, plan);
    return;
  }
  if (url.pathname.startsWith("/api/v1/library/songs/")) {
    const songId = decodeURIComponent(url.pathname.slice("/api/v1/library/songs/".length));
    json(response, {
      song_id: songId, title: `Song ${songId}`, filename: `${songId}.mid`,
      relpath: `${songId}.mid`, extension: ".mid", size_bytes: 1000,
      status: "READY", midi_format: 1, track_count: 1, ticks_per_beat: 96,
      duration_seconds: SONG_DURATION, tempo_bpm_first: 120,
      indexed_at_utc: "2026-09-20T00:00:00Z",
    });
    return;
  }
  if (url.pathname === "/api/v1/library/songs") {
    json(response, { query: { q: null, limit: 20, offset: 0, status: null, format: null }, count: 1, songs: [{ song_id: "song-preview", title: "Renderer test selection", filename: "renderer.mid", duration_seconds: SONG_DURATION, status: "parsed" }] });
    return;
  }
  if (url.pathname === "/api/v1/library") {
    json(response, { ready: true, total: 1, parsed: 1, pending: 0, invalid: 0, unsupported: 0 });
    return;
  }
  if (url.pathname === "/api/v1/playback") {
    json(response, playbackSnapshot());
    return;
  }
  json(response, { error: { code: "not_found", message: "not found" } }, 404);
});

await new Promise((done) => server.listen(0, "127.0.0.1", done));
const appPort = server.address().port;

/* ---------------- Chrome + CDP ---------------- */

const probe = createServer();
await new Promise((done) => probe.listen(0, "127.0.0.1", done));
const debugPort = probe.address().port;
await new Promise((done) => probe.close(done));
const profile = await mkdtemp(join(tmpdir(), "piano-ui007-chrome-"));
// SwiftShader software rendering (headless): results are NEVER device
// performance. --enable-unsafe-swiftshader is required for software
// WebGL on current Chrome; --disable-gpu is deliberately NOT passed so
// the renderer's genuine backend ladder (WebGPU -> WebGL2) engages.
const chrome = spawn(CHROME, [
  "--headless=new", "--no-sandbox", "--disable-dev-shm-usage",
  "--enable-unsafe-swiftshader",
  `--remote-debugging-port=${debugPort}`, `--user-data-dir=${profile}`,
  `http://127.0.0.1:${appPort}/ui/#/learn`,
], { stdio: ["ignore", "ignore", "pipe"] });

async function fetchJson(url) {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    try { return await (await fetch(url)).json(); } catch { await sleep(50); }
  }
  throw new Error(`Chrome debug endpoint unavailable: ${url}`);
}

const targets = await fetchJson(`http://127.0.0.1:${debugPort}/json/list`);
const target = targets.find((item) => item.type === "page");
assert.ok(target);
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((done, fail) => { ws.once("open", done); ws.once("error", fail); });
let cdpId = 0;
const pending = new Map();
ws.on("message", (raw) => {
  const message = JSON.parse(raw);
  if (message.method === "Runtime.exceptionThrown") {
    pageErrors.push(
      message.params.exceptionDetails.exception?.description ||
      message.params.exceptionDetails.text
    );
  }
  if (!message.id || !pending.has(message.id)) return;
  const handlers = pending.get(message.id);
  pending.delete(message.id);
  if (message.error) handlers.fail(new Error(message.error.message));
  else handlers.done(message.result);
});
function cdp(method, params = {}) {
  return new Promise((done, fail) => {
    const id = ++cdpId;
    pending.set(id, { done, fail });
    ws.send(JSON.stringify({ id, method, params }));
  });
}
async function evaluate(expression) {
  const result = await cdp("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
  }
  return result.result.value;
}
async function waitFor(expression, timeout = 30000) {
  const deadline = Date.now() + timeout;
  let last;
  while (Date.now() < deadline) {
    try {
      last = await evaluate(expression);
      if (last) return;
    } catch { /* retried below */ }
    await sleep(60);
  }
  throw new Error(`Timed out waiting for ${expression} (last: ${JSON.stringify(last)})`);
}
async function captureEvidence(filename, { follow = false } = {}) {
  const directory = process.env.UI007_CAPTURE_DIR;
  if (!directory) return;
  await mkdir(directory, { recursive: true });
  await cdp("Emulation.setDeviceMetricsOverride", {
    width: 600, height: 800, deviceScaleFactor: 1, mobile: false,
  });
  await evaluate("scrollTo(0, 0)");
  await sleep(250);
  await evaluate(`document.getElementById('hands-stage').scrollIntoView({ block: 'center' })`);
  await sleep(500);
  if (follow) {
    await evaluate("document.getElementById('hands-camera-follow').click()");
    await waitFor(`globalThis.__pianoHands.info().cameraMode === 'FOLLOW'`);
    await sleep(250);
  }
  const shot = await cdp("Page.captureScreenshot", {
    format: "png",
    captureBeyondViewport: false,
    fromSurface: true,
  });
  await writeFile(join(directory, filename), Buffer.from(shot.data, "base64"));
  if (follow) {
    await evaluate("document.getElementById('hands-camera-full').click()");
    await waitFor(`globalThis.__pianoHands.info().cameraMode === 'FULL'`);
  }
}
const handsStage = () => evaluate("({ ...document.getElementById('hands-stage').dataset })");
// IMMERSIVE composition: the hands renderer initializes lazily when the
// Hands view is presented, and the workspace view persists across reloads
// exactly as the user left it. After a reload, re-present the Hands view
// so the frozen renderer/asset contract below is exercised unchanged.
const presentHands = async () => {
  await waitFor("!!document.querySelector('.song-action-btn[data-action=learn]')");
  await evaluate("document.querySelector('.song-action-btn[data-action=learn]').click()");
  await waitFor("document.documentElement.dataset.ui008ShellReady === 'true'", 30000);
  await evaluate(`(() => {
    const el = document.getElementById('ui008-view-learn');
    if (!el) return;
    el.value = 'hands';
    el.dispatchEvent(new Event('change', { bubbles: true }));
  })()`);
  await waitFor("!document.getElementById('hands').hidden", 15000);
};

await cdp("Runtime.enable");
await cdp("Page.enable");
await cdp("Runtime.evaluate", { expression: "1" });
await waitFor("!!document.getElementById('hands-stage')");
await waitFor("!!document.querySelector('.song-action-btn[data-action=learn]')");
await evaluate("document.querySelector('.song-action-btn[data-action=learn]').click()");
// IMMERSIVE composition update: the hands renderer initializes LAZILY on
// first presentation of the Hands view. Present it once up front so the
// frozen UI-007 renderer/asset contract below is exercised unchanged.
await waitFor("document.documentElement.dataset.ui008ShellReady === 'true'", 30000);
await evaluate(`(() => { const el = document.getElementById('ui008-view-learn'); el.value = 'hands'; el.dispatchEvent(new Event('change', { bubbles: true })); })()`);
await waitFor("!document.getElementById('hands').hidden", 15000);

/* ---------------- tests ---------------- */

await test("renderer initializes on the real packaged UI (backend recorded)", async () => {
  await waitFor(`!!globalThis.__pianoHands && globalThis.__pianoHands.info()`, 60000);
  const info = await evaluate("globalThis.__pianoHands.info()");
  assert.ok(["webgpu", "webgl2"].includes(info.backend), `backend ${info.backend}`);
  assert.equal(info.status, "ready");
  assert.equal(info.assetState, "ready", info.assetError);
  evidence.backend = info.backend;
  evidence.effectiveQuality = info.effectiveQuality;
  const glProbe = await evaluate(`(() => {
    const canvas = document.getElementById('hands-canvas');
    return { width: canvas.width > 0, height: canvas.height > 0 };
  })()`);
  assert.ok(glProbe.width && glProbe.height, "canvas has a backing store");
});

await test("no song yet: explicit idle/no-plan states (nothing invented)", async () => {
  core.playback = { state: "IDLE", songId: null, position: 0, playingSince: null, tempo: 1 };
  const data = await handsStage();
  assert.ok(["idle", "no-plan", "waiting-score"].includes(data.status), data.status);
});

core.scores.set("song-a", makeScore({
  songId: "song-a", notes: simpleNotes(120, SONG_DURATION), duration: SONG_DURATION,
}));
core.plans.set("song-a", makeHandsPlan(core.scores.get("song-a")));
core.playback = { state: "PLAYING", songId: "song-a", position: 4.0, playingSince: Date.now(), tempo: 1 };

await test("Fal rig neutral pose renders both hands without inventing contact", async () => {
  const notes = [48, 72].map((midi, index) => ({
    index, midi, channel: 1, velocity: 80,
    onset_seconds: 10, duration_seconds: 0.5,
  }));
  const score = makeScore({ songId: "song-neutral", notes, duration: 12, sourceChar: "e" });
  core.scores.set("song-neutral", score);
  core.plans.set("song-neutral", makeHandsPlan(score));
  core.playback = { state: "PAUSED", songId: "song-neutral", position: 0, playingSince: null, tempo: 1 };
  await waitFor(`globalThis.__pianoHands.info().identity.startsWith('song-neutral|')`, 15000);
  const motion = await evaluate("globalThis.__pianoHands.motion()");
  assert.ok(motion);
  for (const hand of ["LEFT", "RIGHT"]) {
    if (!motion.hands[hand]) continue;
    for (let finger = 1; finger <= 5; finger += 1) {
      assert.equal(motion.hands[hand].fingers[finger].press, 0);
    }
  }
  await captureEvidence("tier-a-neutral-pose.png");
  core.playback = { state: "PLAYING", songId: "song-a", position: 4.0, playingSince: Date.now(), tempo: 1 };
  await waitFor(`globalThis.__pianoHands.info().identity.startsWith('song-a|')`, 15000);
});

await test("playback drives the real Tier-A rig from the canonical plan", async () => {
  await waitFor(`document.getElementById('hands-stage').dataset.status === 'ready'`);
  const data = await handsStage();
  assert.equal(data.enabled, "on");
  assert.ok(data.hands.includes("asset"), data.hands);
  const info = await evaluate("globalThis.__pianoHands.info()");
  assert.deepEqual(info.hands, { LEFT: "asset", RIGHT: "asset" });
  assert.equal(
    info.identity,
    "song-a|score-sha256:" + "a".repeat(64) + "|fingering-sha256:" + "c".repeat(64)
  );
  evidence.identity = info.identity;
});

await test("hand/finger identity and EXACT key contact from canonical geometry", async () => {
  // Park on a known attack window: note i=30 (onset 30.0, duration 0.4).
  core.playback = { state: "PAUSED", songId: "song-a", position: 30.2, playingSince: null, tempo: 1 };
  await waitFor(
    `document.getElementById('hands-stage').dataset.status === 'ready' &&
     globalThis.__pianoHands.motion().positionSeconds > 29.9 &&
     globalThis.__pianoHands.motion().positionSeconds < 30.5`,
    20000
  );
  const probe = await evaluate(`(async () => {
    const { noteCenterFraction } = await import('/ui/app.js');
    const motion = globalThis.__pianoHands.motion();
    if (!motion) return { ok: false, why: "no motion" };
    const pressed = [];
    for (const hand of ["LEFT", "RIGHT"]) {
      const h = motion.hands[hand];
      if (!h || h.omitted) continue;
      for (let f = 1; f <= 5; f += 1) {
        const fm = h.fingers[f];
        if (fm.phase && !["PREPARE","APPROACH","PRESS","HOLD","RELEASE","RECOVER"].includes(fm.phase)) {
          return { ok: false, why: "phase outside locked model", phase: fm.phase };
        }
        if (fm.contact && fm.press > 0) {
          const expectedX = (noteCenterFraction(fm.midi) - 0.5) * 1.222;
          if (Math.abs(fm.contact.x - expectedX) > 1e-9) {
            return { ok: false, why: "contact x off canonical geometry", hand, f };
          }
          pressed.push({ hand, f, midi: fm.midi, phase: fm.phase, press: fm.press });
        }
      }
    }
    return { ok: true, pressed };
  })()`);
  assert.ok(probe.ok, JSON.stringify(probe));
  assert.ok(probe.pressed.length >= 1, "at least one pressed finger at the parked onset");
  await captureEvidence("tier-a-single-contact.png", { follow: true });
});

await test("press -> hold -> release transitions on parked canonical positions", async () => {
  const parkedPhases = async (position) => {
    core.playback = { state: "PAUSED", songId: "song-a", position, playingSince: null, tempo: 1 };
    await waitFor(
      `Math.abs(globalThis.__pianoHands.motion().positionSeconds - (${position})) < 0.05`,
      15000
    );
    return evaluate(`(() => {
      const m = globalThis.__pianoHands.motion();
      const phases = [];
      for (const hand of ["LEFT", "RIGHT"]) {
        const h = m && m.hands[hand];
        if (!h || h.omitted) continue;
        for (let f = 1; f <= 5; f += 1) {
          const fm = h.fingers[f];
          if (fm.press > 0 || fm.phase === "RELEASE") {
            phases.push({ phase: fm.phase, press: fm.press });
          }
        }
      }
      return phases;
    })()`);
  };
  const held = await parkedPhases(30.2); // inside [30.0, 30.4): HOLD/PRESS
  assert.ok(
    held.some((s) => s.phase === "HOLD" || s.phase === "PRESS"),
    `held sample: ${JSON.stringify(held)}`
  );
  const released = await parkedPhases(30.35); // release window [30.28, 30.4)
  assert.ok(
    released.some((s) => s.phase === "RELEASE"),
    `released sample: ${JSON.stringify(released)}`
  );
  // The same parked position twice yields identical motion (pause frozen).
  const a = await evaluate("JSON.stringify(globalThis.__pianoHands.motion())");
  await sleep(400);
  const b = await evaluate("JSON.stringify(globalThis.__pianoHands.motion())");
  assert.equal(a, b, "paused pose frozen");
});

await test("Fal rig handles repeated-note re-press on the same semantic finger", async () => {
  const notes = [
    { index: 0, midi: 60, channel: 1, velocity: 84, onset_seconds: 1, duration_seconds: 0.35 },
    { index: 1, midi: 60, channel: 1, velocity: 86, onset_seconds: 2, duration_seconds: 0.35 },
  ];
  const score = makeScore({ songId: "song-repeat", notes, duration: 4, sourceChar: "d" });
  core.scores.set("song-repeat", score);
  core.plans.set("song-repeat", makeHandsPlan(score, { override: () => notes.map((note) => ({
    score_index: note.index, target_id: `score:${note.index}`, midi: note.midi,
    channel: note.channel, onset_seconds: note.onset_seconds,
    duration_seconds: note.duration_seconds, hand: "RIGHT", finger: 3, alternatives: [],
  })) }));
  core.playback = { state: "PAUSED", songId: "song-repeat", position: 2.15, playingSince: null, tempo: 1 };
  await waitFor(`globalThis.__pianoHands.info().identity.startsWith('song-repeat|')`, 15000);
  const state = await evaluate("globalThis.__pianoHands.motion().hands.RIGHT.fingers[3]");
  assert.equal(state.midi, 60);
  assert.ok(state.press > 0);
});

await test("Fal rig reaches an explicit black-key contact", async () => {
  const notes = [{ index: 0, midi: 61, channel: 1, velocity: 88, onset_seconds: 1, duration_seconds: 0.6 }];
  const score = makeScore({ songId: "song-black", notes, duration: 3, sourceChar: "b" });
  core.scores.set("song-black", score);
  core.plans.set("song-black", makeHandsPlan(score));
  core.playback = { state: "PAUSED", songId: "song-black", position: 1.2, playingSince: null, tempo: 1 };
  await waitFor(`globalThis.__pianoHands.info().identity.startsWith('song-black|')`, 15000);
  const pressed = await evaluate(`(() => {
    const hand = globalThis.__pianoHands.motion().hands.RIGHT;
    return Object.values(hand.fingers).find((finger) => finger.press > 0);
  })()`);
  assert.equal(pressed.midi, 61);
  await captureEvidence("tier-a-black-key-contact.png", { follow: true });
});

await test("Fal rig renders a same-hand triad through the canonical contacts", async () => {
  const notes = [60, 64, 67].map((midi, index) => ({
    index, midi, channel: 1, velocity: 90, onset_seconds: 1, duration_seconds: 0.8,
  }));
  const fingers = [1, 3, 5];
  const score = makeScore({ songId: "song-triad", notes, duration: 3, sourceChar: "c" });
  core.scores.set("song-triad", score);
  core.plans.set("song-triad", makeHandsPlan(score, { override: () => notes.map((note, index) => ({
    score_index: note.index, target_id: `score:${note.index}`, midi: note.midi,
    channel: note.channel, onset_seconds: note.onset_seconds,
    duration_seconds: note.duration_seconds, hand: "RIGHT", finger: fingers[index], alternatives: [],
  })) }));
  core.playback = { state: "PAUSED", songId: "song-triad", position: 1.25, playingSince: null, tempo: 1 };
  await waitFor(`globalThis.__pianoHands.info().identity.startsWith('song-triad|')`, 15000);
  const pressed = await evaluate(`Object.values(globalThis.__pianoHands.motion().hands.RIGHT.fingers).filter((finger) => finger.press > 0).length`);
  assert.equal(pressed, 3);
  await captureEvidence("tier-a-chord.png");
});

await test("Fal rig reconstructs an octave lateral move without teleport regression", async () => {
  const notes = [
    { index: 0, midi: 60, channel: 1, velocity: 82, onset_seconds: 1, duration_seconds: 0.4 },
    { index: 1, midi: 72, channel: 1, velocity: 82, onset_seconds: 2, duration_seconds: 0.4 },
  ];
  const score = makeScore({ songId: "song-octave", notes, duration: 4, sourceChar: "f" });
  core.scores.set("song-octave", score);
  core.plans.set("song-octave", makeHandsPlan(score, { override: () => notes.map((note) => ({
    score_index: note.index, target_id: `score:${note.index}`, midi: note.midi,
    channel: note.channel, onset_seconds: note.onset_seconds,
    duration_seconds: note.duration_seconds, hand: "RIGHT", finger: 1, alternatives: [],
  })) }));
  core.playback = { state: "PAUSED", songId: "song-octave", position: 2.15, playingSince: null, tempo: 1 };
  await waitFor(`globalThis.__pianoHands.info().identity.startsWith('song-octave|')`, 15000);
  const state = await evaluate("globalThis.__pianoHands.motion().hands.RIGHT.fingers[1]");
  assert.equal(state.midi, 72);
  assert.ok(state.press > 0);
  core.playback = { state: "PAUSED", songId: "song-a", position: 30.2, playingSince: null, tempo: 1 };
  await waitFor(`globalThis.__pianoHands.info().identity.startsWith('song-a|')`, 15000);
});

await test("resume advances, pause freezes, seek reconstructs immediately", async () => {
  const parkedAt = async (position) => {
    core.playback = { state: "PAUSED", songId: "song-a", position, playingSince: null, tempo: 1 };
    await waitFor(
      `Math.abs(globalThis.__pianoHands.motion().positionSeconds - (${position})) < 0.05`,
      15000
    );
    return evaluate("globalThis.__pianoHands.motion().positionSeconds");
  };
  core.playback = { state: "PLAYING", songId: "song-a", position: 45, playingSince: Date.now(), tempo: 1 };
  await waitFor("globalThis.__pianoHands.motion().positionSeconds > 45.05", 8000);
  const p1 = await evaluate("globalThis.__pianoHands.motion().positionSeconds");
  await sleep(600);
  const p2 = await evaluate("globalThis.__pianoHands.motion().positionSeconds");
  assert.ok(p2 > p1, `advancing ${p1} -> ${p2}`);
  const frozen1 = await parkedAt(50);
  await sleep(400);
  const frozen2 = await evaluate("globalThis.__pianoHands.motion().positionSeconds");
  assert.equal(frozen1, frozen2, "paused position frozen");
  // "seek": authoritative position jump -> hands reconstruct immediately
  await parkedAt(75);
});

await test("camera FULL/FOLLOW switching is presentation-only", async () => {
  await evaluate("document.getElementById('hands-camera-follow').click()");
  await waitFor(`globalThis.__pianoHands.info().cameraMode === 'FOLLOW'`);
  const identity1 = await evaluate("globalThis.__pianoHands.info().identity");
  await evaluate("document.getElementById('hands-camera-full').click()");
  await waitFor(`globalThis.__pianoHands.info().cameraMode === 'FULL'`);
  const identity2 = await evaluate("globalThis.__pianoHands.info().identity");
  assert.equal(identity1, identity2, "camera switch never touches the motion view");
});

await test("2D visual mode switching does not disturb the hands", async () => {
  await evaluate("document.getElementById('visual-mode-sheet').click()");
  await sleep(300);
  const info1 = await evaluate("globalThis.__pianoHands.info()");
  await evaluate("document.getElementById('visual-mode-falling').click()");
  await sleep(300);
  const info2 = await evaluate("globalThis.__pianoHands.info()");
  assert.equal(info1.identity, info2.identity);
  assert.equal(info1.status, "ready");
});

await test("reload reconstructs hands from authoritative state only", async () => {
  const documentBeforeReload = await evaluate("performance.timeOrigin");
  await cdp("Page.reload", {});
  await waitFor(`performance.timeOrigin !== ${documentBeforeReload} && document.documentElement?.dataset.ui008ShellReady === 'true'`, 15000);
  await presentHands();
  await waitFor(`!!globalThis.__pianoHands && globalThis.__pianoHands.info()`, 60000);
  await waitFor(`document.getElementById('hands-stage').dataset.status === 'ready'`, 30000);
  const info = await evaluate("globalThis.__pianoHands.info()");
  assert.equal(info.identity, evidence.identity);
});

await test("A->B song switch re-binds identity (old pose never survives)", async () => {
  core.scores.set("song-b", makeScore({
    songId: "song-b", notes: simpleNotes(80, 90), duration: 90, sourceChar: "b",
  }));
  core.plans.set("song-b", makeHandsPlan(core.scores.get("song-b")));
  core.playback = { state: "PAUSED", songId: "song-b", position: 5, playingSince: null, tempo: 1 };
  await waitFor(`globalThis.__pianoHands.info().identity.startsWith('song-b|')`, 15000);
  const data = await handsStage();
  assert.equal(data.status, "ready");
});

await test("same-song source replacement installs the new plan only", async () => {
  const scoreB = makeScore({
    songId: "song-b", notes: simpleNotes(60, 90), duration: 90, sourceChar: "f",
  });
  core.scores.set("song-b", scoreB);
  core.plans.set("song-b", makeHandsPlan(scoreB));
  try {
    await waitFor(
      `globalThis.__pianoHands.info().identity.includes('score-sha256:' + 'f'.repeat(64))`,
      15000
    );
  } catch (error) {
    const before = core.scoreRequests;
    await sleep(3000);
    const diagnostics = await evaluate(`(() => ({
      stage: { ...document.getElementById('visual-stage').dataset },
      info: globalThis.__pianoHands.info(),
    }))()`);
    diagnostics.serverScoreRequestsBefore = before;
    diagnostics.serverScoreRequestsAfter = core.scoreRequests;
    console.error("replacement diagnostics:", JSON.stringify(diagnostics, null, 1));
    throw error;
  }
  assert.equal((await handsStage()).status, "ready");
});

await test("occupancy violation: fail-closed hand omission, music continues", async () => {
  // Designed material: LEFT holds note0 [0,4) while notes 1-2 overlap it.
  const occNotes = [
    { index: 0, midi: 40, channel: 1, velocity: 80, onset_seconds: 0, duration_seconds: 4 },
    { index: 1, midi: 72, channel: 1, velocity: 90, onset_seconds: 0.5, duration_seconds: 0.5 },
    { index: 2, midi: 44, channel: 1, velocity: 80, onset_seconds: 1, duration_seconds: 0.5 },
    { index: 3, midi: 76, channel: 1, velocity: 90, onset_seconds: 1, duration_seconds: 0.5 },
    { index: 4, midi: 47, channel: 1, velocity: 80, onset_seconds: 2, duration_seconds: 0.5 },
    { index: 5, midi: 79, channel: 1, velocity: 90, onset_seconds: 2.5, duration_seconds: 0.5 },
  ];
  const occupancyScore = makeScore({
    songId: "song-occ", notes: occNotes, duration: 8, sourceChar: "3",
  });
  core.scores.set("song-occ", occupancyScore);
  core.plans.set("song-occ", makeHandsPlan(occupancyScore, {
    override: () => {
      // Exact score coverage; only the FINGER assignment is corrupted so
      // the same LEFT hand+finger overlaps over DIFFERENT keys. The app
      // must OMIT the hand, never repair it.
      return [
        { score_index: 0, target_id: "score:0", midi: 40, channel: 1, onset_seconds: 0, duration_seconds: 4, hand: "LEFT", finger: 5, alternatives: [] },
        { score_index: 1, target_id: "score:1", midi: 72, channel: 1, onset_seconds: 0.5, duration_seconds: 0.5, hand: "RIGHT", finger: 1, alternatives: [] },
        { score_index: 2, target_id: "score:2", midi: 44, channel: 1, onset_seconds: 1, duration_seconds: 0.5, hand: "LEFT", finger: 4, alternatives: [] },
        { score_index: 3, target_id: "score:3", midi: 76, channel: 1, onset_seconds: 1, duration_seconds: 0.5, hand: "RIGHT", finger: 3, alternatives: [] },
        { score_index: 4, target_id: "score:4", midi: 47, channel: 1, onset_seconds: 2, duration_seconds: 0.5, hand: "LEFT", finger: 5, alternatives: [] },
        { score_index: 5, target_id: "score:5", midi: 79, channel: 1, onset_seconds: 2.5, duration_seconds: 0.5, hand: "RIGHT", finger: 5, alternatives: [] },
      ];
    },
  }));
  core.playback = { state: "PAUSED", songId: "song-occ", position: 2.2, playingSince: null, tempo: 1 };
  await waitFor(`document.getElementById('hands-stage').dataset.status === 'partial'`, 15000);
  const motion = await evaluate(`(() => {
    const m = globalThis.__pianoHands.motion();
    return {
      left: m.hands.LEFT === null ? "omitted" : "present",
      rightPresent: !!m.hands.RIGHT,
      warnings: m.warnings,
    };
  })()`);
  assert.equal(motion.left, "omitted", "violating hand hidden");
  assert.ok(motion.rightPresent, "other hand still usable");
  assert.ok(
    motion.warnings.some((w) => w.startsWith("plan_occupancy_violation")),
    JSON.stringify(motion.warnings)
  );
  const info = await evaluate("globalThis.__pianoHands.info()");
  assert.equal(info.hands.LEFT, "hidden");
  // playback continuity unaffected
  core.playback = { state: "PLAYING", songId: "song-occ", position: 3, playingSince: Date.now(), tempo: 1 };
  await waitFor("globalThis.__pianoHands.motion().positionSeconds > 3.05", 8000);
  assert.ok(true, "playback continues");
});

await test("dense chromatic cluster: contacts immutable, hands rendered", async () => {
  const clusterNotes = [];
  let index = 0;
  for (let c = 0; c < 6; c += 1) {
    for (const midi of [48, 50, 52, 60, 62, 64]) {
      clusterNotes.push({
        index: index++, midi, channel: 1, velocity: 90,
        onset_seconds: c * 2, duration_seconds: 1.4,
      });
    }
  }
  const dense = makeScore({ songId: "song-dense", notes: clusterNotes, duration: 20, sourceChar: "4" });
  core.scores.set("song-dense", dense);
  core.plans.set("song-dense", makeHandsPlan(dense, {
    override: (playable) => greedyPlan(playable),
  }));
  core.playback = { state: "PAUSED", songId: "song-dense", position: 0.5, playingSince: null, tempo: 1 };
  await waitFor(`globalThis.__pianoHands.info().identity.startsWith('song-dense|')`, 15000);
  const probe = await evaluate(`(() => {
    const m = globalThis.__pianoHands.motion();
    const info = globalThis.__pianoHands.info();
    let pressed = 0;
    for (const hand of ["LEFT", "RIGHT"]) {
      const h = m.hands[hand];
      if (!h || h.omitted) continue;
      for (let f = 1; f <= 5; f += 1) {
        if (h.fingers[f].press > 0) pressed += 1;
      }
    }
    return { pressed, status: info.status, warnings: info.poseWarnings };
  })()`);
  assert.ok(probe.pressed >= 1, `pressed fingers ${probe.pressed}`);
  for (const warning of probe.warnings) {
    assert.ok(
      warning.startsWith("collision_unresolved") || warning.startsWith("pose_clamped"),
      `locked vocabulary only: ${warning}`
    );
  }
  await captureEvidence("tier-a-two-hand-chord.png");
});

await test("actual-rig frame continuity and render cost are recorded", async () => {
  const metrics = await evaluate(`new Promise((done) => {
    const intervals = [];
    const renderMs = [];
    let previous = 0;
    function sample(now) {
      if (previous) intervals.push(now - previous);
      previous = now;
      const info = globalThis.__pianoHands.info();
      if (Number.isFinite(info.lastRenderMs)) renderMs.push(info.lastRenderMs);
      if (intervals.length >= 300) {
        const percentile = (values, p) => {
          const sorted = [...values].sort((a, b) => a - b);
          return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * p))];
        };
        done({
          intervalMedian: percentile(intervals, 0.5),
          intervalP95: percentile(intervals, 0.95),
          renderMedian: percentile(renderMs, 0.5),
          renderP95: percentile(renderMs, 0.95),
          maxInterval: Math.max(...intervals),
          backend: info.backend,
          quality: info.effectiveQuality,
          assetState: info.assetState,
        });
        return;
      }
      requestAnimationFrame(sample);
    }
    requestAnimationFrame(sample);
  })`);
  assert.equal(metrics.assetState, "ready");
  assert.ok(Number.isFinite(metrics.intervalP95));
  assert.ok(Number.isFinite(metrics.renderP95));
  evidence.actualRigPerformance = metrics;
});

await test("WebGL context loss recovery (where supported)", async () => {
  const supported = await evaluate(`(() => {
    const canvas = document.getElementById('hands-canvas');
    const gl = canvas.getContext('webgl2');
    if (!gl) return false;
    const ext = gl.getExtension('WEBGL_lose_context');
    if (!ext) return false;
    // The extension handle cannot cross the CDP boundary; stash it in
    // the page for the restore step.
    globalThis.__ui007Lose = ext;
    return true;
  })()`);
  if (!supported) {
    evidence.contextLoss = "NOT_SUPPORTED_IN_THIS_BROWSER";
    console.log("  (context loss simulation unsupported here; recorded honestly)");
    return;
  }
  await evaluate("globalThis.__ui007Lose.loseContext(), true");
  await sleep(500);
  const restored = await evaluate(`(() => {
    if (!globalThis.__ui007Lose) return false;
    globalThis.__ui007Lose.restoreContext();
    return true;
  })()`);
  assert.equal(restored, true, "restore handle available");
  // Either a controlled recovery or a controlled COMPATIBILITY degrade —
  // never a crash and never uncontrolled state.
  await waitFor(
    `['ready','unavailable'].includes(document.getElementById('hands-stage').dataset.status)`,
    30000
  );
  const finalStatus = (await handsStage()).status;
  const recoveryInfo = await evaluate("globalThis.__pianoHands.info()");
  evidence.contextLoss = `recovered_to_${finalStatus}`;
  evidence.recoveryError = recoveryInfo.recoveryError || null;
  if (finalStatus === "ready") {
    await waitFor(
      `Object.values(globalThis.__pianoHands.info().hands).every((source) => source === 'asset')`,
      15000
    );
    const info = await evaluate("globalThis.__pianoHands.info()");
    assert.ok(info.identity, "identity re-established after recovery");
    assert.equal(info.assetState, "ready", info.assetError);
    assert.deepEqual(info.hands, { LEFT: "asset", RIGHT: "asset" });
  }
});

await test("DPR 2, landscape, portrait, desktop: no overflow, no errors", async () => {
  // Deterministic parked playback for the viewport sweep.
  core.playback = { state: "PAUSED", songId: "song-a", position: 60, playingSince: null, tempo: 1 };
  const viewports = [
    { name: "dpr2-desktop", width: 1280, height: 800, deviceScaleFactor: 2, mobile: false },
    { name: "landscape-tablet", width: 1024, height: 768, deviceScaleFactor: 1, mobile: false },
    { name: "portrait-tablet", width: 768, height: 1024, deviceScaleFactor: 1, mobile: true },
    { name: "portrait-phone", width: 360, height: 740, deviceScaleFactor: 2, mobile: true },
    { name: "desktop", width: 1440, height: 900, deviceScaleFactor: 1, mobile: false },
  ];
  for (const vp of viewports) {
    await cdp("Emulation.setDeviceMetricsOverride", {
      width: vp.width, height: vp.height,
      deviceScaleFactor: vp.deviceScaleFactor, mobile: vp.mobile,
    });
    await sleep(500);
    const probe = await evaluate(`(() => {
      const doc = document.documentElement;
      return {
        overflow: doc.scrollWidth - doc.clientWidth,
        status: document.getElementById('hands-stage').dataset.status,
        enabled: document.getElementById('hands-stage').dataset.enabled,
      };
    })()`);
    assert.ok(probe.overflow <= 1, `${vp.name} horizontal overflow ${probe.overflow}`);
    assert.notEqual(probe.status, "unavailable", `${vp.name} status`);
    evidence[`viewport_${vp.name}`] = probe.status;
  }
  await cdp("Emulation.clearDeviceMetricsOverride", {});
});

await test("renderer init failure degrades to COMPATIBILITY; 2D UI survives", async () => {
  core.blockVendor = true;
  const documentBeforeReload = await evaluate("performance.timeOrigin");
  await cdp("Page.reload", {});
  await waitFor(`performance.timeOrigin !== ${documentBeforeReload} && document.documentElement?.dataset.ui008ShellReady === 'true'`, 15000);
  await presentHands();
  await waitFor(
    `document.getElementById('hands-stage').dataset.status === 'compatibility'`,
    60000
  );
  const visual = await evaluate(
    `document.getElementById('visual-stage').dataset.status`
  );
  const summary = await evaluate(
    `document.getElementById('hands-summary').textContent`
  );
  assert.ok(
    ["ready", "no-song", "loading", "empty", "stale", "unavailable"].includes(visual),
    `2D visual still functional (${visual})`
  );
  assert.ok(summary.toLowerCase().includes("2d") || summary.toLowerCase().includes("unavailable"),
    `honest compatibility wording: ${summary}`);
  evidence.compatibilityWording = summary;
});

await test("missing GLB fails closed to Tier-R without breaking the renderer", async () => {
  core.blockVendor = false;
  core.blockAsset = true;
  const documentBeforeReload = await evaluate("performance.timeOrigin");
  await cdp("Page.reload", {});
  await waitFor(`performance.timeOrigin !== ${documentBeforeReload} && document.documentElement?.dataset.ui008ShellReady === 'true'`, 15000);
  await presentHands();
  await waitFor(`!!globalThis.__pianoHands && globalThis.__pianoHands.info()`, 60000);
  await waitFor(`globalThis.__pianoHands.info().assetState === 'fallback'`, 30000);
  const info = await evaluate("globalThis.__pianoHands.info()");
  assert.equal(info.status, "ready");
  assert.ok(info.assetError);
  core.blockAsset = false;
});

await test("vendor restored: hands recover on reload", async () => {
  core.blockVendor = false;
  core.blockAsset = false;
  const documentBeforeReload = await evaluate("performance.timeOrigin");
  await cdp("Page.reload", {});
  await waitFor(`performance.timeOrigin !== ${documentBeforeReload} && document.documentElement?.dataset.ui008ShellReady === 'true'`, 15000);
  await presentHands();
  await waitFor(`!!globalThis.__pianoHands && globalThis.__pianoHands.info()`, 60000);
  await waitFor(`document.getElementById('hands-stage').dataset.status === 'ready'`, 30000);
});

await test("bounded read-only debug surface only", async () => {
  const probe = await evaluate(`(() => {
    const surface = globalThis.__pianoHands;
    const info = surface.info();
    const motion = surface.motion();
    return {
      frozen: Object.isFrozen(surface),
      keys: Object.keys(surface).sort(),
      infoFrozen: info === null ? null : Object.isFrozen(info),
      motionFrozen: motion === null ? null : Object.isFrozen(motion),
    };
  })()`);
  assert.equal(probe.frozen, true);
  assert.deepEqual(probe.keys, ["info", "motion"]);
  assert.equal(probe.infoFrozen, true);
  assert.equal(probe.motionFrozen, true);
});

await test("historical canonical Bass Melody material produces moving Tier-A hands", async () => {
  const fixture = JSON.parse(await readFile(join(ROOT, 'tests/weblogic/fixtures/bass_melody.json'), 'utf8'));
  const sid=fixture.score.song_id;
  core.scores.set(sid,fixture.score);core.plans.set(sid,fixture.fingering);
  await evaluate(`(async () => {
    const {HandRenderer}=await import('/ui/handrender.js');
    const original=HandRenderer.prototype.render;
    const probe={frames:0,renderers:new Set()};
    HandRenderer.prototype.render=function(motion) {
      const result=original.call(this,motion);
      probe.frames++;probe.renderers.add(this);
      const c=this._camera;
      if(c) probe.camera={position:c.position.toArray(),aspect:c.aspect};
      return result;
    };
    globalThis.__cameraProbe=probe;
  })()`);
  const samples=[];
  for(const position of [8.1,8.6,9.1]) {
    core.playback={state:'PAUSED',songId:sid,position,playingSince:null,tempo:1};
    await waitFor(`globalThis.__pianoHands.motion()?.identity.songId === '${sid}' && globalThis.__pianoHands.motion().positionSeconds === ${position}`);
    samples.push(await evaluate('globalThis.__pianoHands.motion()'));
    assert.equal((await evaluate('globalThis.__pianoHands.info()')).assetState,'ready');
    assert.equal((await handsStage()).hands,'asset+asset');
  }
  assert.notDeepEqual(samples[0].hands,samples[1].hands);
  assert.notDeepEqual(samples[1].hands,samples[2].hands);
  evidence.knownGood={song:sid,positions:samples.map(s=>s.positionSeconds),frames:await evaluate('globalThis.__cameraProbe.frames')};
});

await test("unsupported fingering hides hands, explains limitation and fully frames keyboard", async () => {
  core.fingeringUnsupported=true;
  const score=makeScore({songId:'unsupported-song',notes:simpleNotes(5,10),duration:10,sourceChar:'9'});
  core.scores.set(score.song_id,score);
  core.playback={state:'PAUSED',songId:score.song_id,position:2,playingSince:null,tempo:1};
  try {
    await waitFor(`document.getElementById('hands-summary').textContent.includes('does not support Hands')`);
    assert.equal((await handsStage()).hands,'hidden+hidden');
    evidence.unsupported=[];
    for(const mode of ['FOLLOW','FULL']) {
      await evaluate(`document.getElementById('hands-camera-${mode.toLowerCase()}').click()`);
      for(const [width,height] of [[390,844],[844,390],[1024,768],[1440,900]]) {
        await cdp('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width<900});
        await waitFor(`(() => {const s=document.getElementById('hands-stage'),c=document.getElementById('hands-canvas');return c.width===s.clientWidth && c.height===s.clientHeight;})()`);
        await waitFor(`(async () => {const {fullCameraPose}=await import('/ui/handrender.js');const p=globalThis.__cameraProbe.camera;return p && JSON.stringify(p.position)===JSON.stringify(Object.values(fullCameraPose(p.aspect).position));})()`);
        const p=await evaluate('globalThis.__cameraProbe.camera');
        assert.equal(await evaluate('document.querySelectorAll("#hands-canvas").length'),1);
        evidence.unsupported.push({mode,width,height,...p});
      }
    }
    await captureEvidence('unsupported-full-keyboard.png');
    const timeOrigin=await evaluate('performance.timeOrigin');
    await cdp('Page.reload',{});
    await waitFor(`performance.timeOrigin !== ${timeOrigin} && document.documentElement?.dataset.ui008ShellReady === 'true'`);
    await presentHands();
    await waitFor(`document.getElementById('hands-stage').dataset.status === 'unsupported' && globalThis.__pianoHands.info()?.assetState === 'ready'`);
    assert.equal((await handsStage()).hands,'hidden+hidden');
    await captureEvidence('unsupported-first-open.png');
  } finally {
    core.fingeringUnsupported=false;
    core.playback={state:'PAUSED',songId:'song-a',position:30.2,playingSince:null,tempo:1};
    await cdp('Emulation.clearDeviceMetricsOverride',{});
    await waitFor(`globalThis.__pianoHands.info()?.identity?.startsWith('song-a|')`);
  }
});

await test("Compatibility resumes every 3D quality without reload or musical commands", async () => {
  const beforeCommands = [...core.musicalCommands];
  const beforeMotion = await evaluate("JSON.stringify(globalThis.__pianoHands.motion())");
  evidence.qualityResume = [];
  const choose = async (mode) => evaluate(`(() => {
    const el = document.getElementById('hands-quality');
    el.value = ${JSON.stringify(mode)};
    el.dispatchEvent(new Event('change', { bubbles: true }));
  })()`);
  for (const mode of ["AUTO", "PREMIUM", "BALANCED", "BATTERY"]) {
    await choose("COMPATIBILITY");
    assert.equal((await handsStage()).status, "compatibility");
    await choose(mode);
    const sample = { mode, firstAfterChange: await handsStage() };
    evidence.qualityResume.push(sample);
    try {
      await waitFor("globalThis.__pianoHands.info()?.status === 'ready' && globalThis.__pianoHands.info().assetState === 'ready' && document.getElementById('hands-stage').dataset.status === 'ready'", 15000);
    } finally {
      sample.final = await evaluate("globalThis.__pianoHands.info()");
    }
    assert.equal(sample.final.assetState, "ready", "accepted GLB reloaded");
    assert.equal(sample.final.quality, mode);
    assert.equal(await evaluate("document.querySelectorAll('#hands-canvas').length"), 1);
    assert.equal(await evaluate("JSON.stringify(globalThis.__pianoHands.motion())"), beforeMotion,
      "quality recovery preserves canonical paused frame");
  }
  // Rapid user changes must serialize recovery and honor the latest mode.
  await evaluate(`(() => {
    const el = document.getElementById('hands-quality');
    for (const mode of ['COMPATIBILITY','AUTO','COMPATIBILITY','BATTERY','AUTO']) {
      el.value = mode; el.dispatchEvent(new Event('change', { bubbles: true }));
    }
  })()`);
  await waitFor("globalThis.__pianoHands.info()?.status === 'ready' && globalThis.__pianoHands.info().assetState === 'ready' && globalThis.__pianoHands.info().quality === 'AUTO'", 15000);
  assert.equal(await evaluate("document.querySelectorAll('#hands-canvas').length"), 1);
  assert.deepEqual(core.musicalCommands, beforeCommands);
});

await test("Hands recovery keeps one canvas, one visual RAF and stable context listeners", async () => {
  await evaluate(`(() => {
    const originalRAF = requestAnimationFrame;
    const originalCancel = cancelAnimationFrame;
    const originalAdd = EventTarget.prototype.addEventListener;
    const originalRemove = EventTarget.prototype.removeEventListener;
    const pending = new Set();
    const listeners = [];
    const probe = { peakRAF: 0, frames: 0 };
    const capture = options => typeof options === 'boolean' ? options : !!options?.capture;
    globalThis.requestAnimationFrame = cb => {
      if (cb.name !== 'tickVisualStage') return originalRAF(cb);
      let id = originalRAF(now => { pending.delete(id); probe.frames++; cb(now); });
      pending.add(id); probe.peakRAF = Math.max(probe.peakRAF, pending.size); return id;
    };
    globalThis.cancelAnimationFrame = id => { pending.delete(id); originalCancel(id); };
    EventTarget.prototype.addEventListener = function(type, cb, options) {
      if (this.id === 'hands-canvas' && !listeners.some(x => x.target === this && x.type === type && x.cb === cb && x.capture === capture(options))) {
        listeners.push({target:this,type,cb,capture:capture(options)});
      }
      return originalAdd.call(this,type,cb,options);
    };
    EventTarget.prototype.removeEventListener = function(type, cb, options) {
      const i = listeners.findIndex(x => x.target === this && x.type === type && x.cb === cb && x.capture === capture(options));
      if (i >= 0) listeners.splice(i,1);
      return originalRemove.call(this,type,cb,options);
    };
    globalThis.__handsLifecycleProbe = {
      read: () => ({...probe, listeners:listeners.map(x=>x.type).sort(),pending:pending.size}),
      restore: () => { globalThis.requestAnimationFrame=originalRAF; globalThis.cancelAnimationFrame=originalCancel; EventTarget.prototype.addEventListener=originalAdd; EventTarget.prototype.removeEventListener=originalRemove; }
    };
  })()`);
  await evaluate(`(async () => {
    const {HandRenderer}=await import('/ui/handrender.js');
    const original=HandRenderer.prototype.render;
    globalThis.__renderedHandsInstances=new Set();
    HandRenderer.prototype.render=function(motion) {
      globalThis.__renderedHandsInstances.add(this);
      return original.call(this,motion);
    };
  })()`);
  const beforeCommands = [...core.musicalCommands];
  const samples = [];
  try {
    for (let i = 0; i < 3; i++) {
      await evaluate(`(() => {
        const el=document.getElementById('hands-quality');
        for(const mode of ['COMPATIBILITY','AUTO']) { el.value=mode; el.dispatchEvent(new Event('change',{bubbles:true})); }
      })()`);
      await waitFor("document.getElementById('hands-stage').dataset.status === 'ready' && globalThis.__pianoHands.info().assetState === 'ready'", 15000);
      for (const view of ["falling", "hands", "piano", "hands"]) {
        await evaluate(`(() => { const el=document.getElementById('ui008-view-learn'); el.value=${JSON.stringify(view)}; el.dispatchEvent(new Event('change',{bubbles:true})); })()`);
        await waitFor(`document.getElementById('ui008-view-learn').value === ${JSON.stringify(view)}`);
      }
      await waitFor("globalThis.__handsLifecycleProbe.read().frames > 10");
      const sample=await evaluate("globalThis.__handsLifecycleProbe.read()");
      sample.canvasCount=await evaluate("document.querySelectorAll('#hands-canvas').length");
      samples.push(sample);
      assert.equal(sample.canvasCount,1);
      assert.equal(await evaluate('globalThis.__renderedHandsInstances.size'),1,'same renderer across all resumes');
      assert.equal(sample.peakRAF,1,"one outstanding product visual animation frame");
      if(i>0) assert.deepEqual(sample.listeners,samples[0].listeners,"no context-listener growth");
    }
    assert.deepEqual(core.musicalCommands,beforeCommands);
  } finally {
    evidence.lifecycleGrowth=samples;
    await evaluate("globalThis.__handsLifecycleProbe.restore()");
  }
});

await test("Hands route, selected-view reload, reduced motion and orientation preserve Core", async () => {
  const beforeCommands=[...core.musicalCommands];
  for(const route of ['player','library','learn']) {
    await evaluate(`document.querySelector('.primary-nav a[href="#/${route}"]').click()`);
    await waitFor(`document.body.dataset.ui008Route === '${route}'`);
  }
  await presentHands();
  await waitFor("document.getElementById('hands-stage').dataset.status === 'ready'");
  await cdp("Emulation.setEmulatedMedia",{features:[{name:"prefers-reduced-motion",value:"reduce"}]});
  evidence.lifecycleViewports=[];
  for(const [width,height] of [[390,844],[844,390],[390,844],[1024,768],[1440,900]]) {
    await cdp("Emulation.setDeviceMetricsOverride",{width,height,deviceScaleFactor:1,mobile:width<900});
    // resizeHandsRenderer passes the actual stage client size (integer
    // layout dimensions), not the transformed bounding rect, to resize().
    await waitFor(`(() => { const s=document.getElementById('hands-stage'); const c=document.getElementById('hands-canvas'); return s.clientWidth>0 && s.clientHeight>0 && c.width===s.clientWidth && c.height===s.clientHeight; })()`);
    const info=await evaluate("globalThis.__pianoHands.info()");
    assert.equal(info.status,"ready");
    assert.equal(await evaluate("document.querySelectorAll('#hands-canvas').length"),1);
    evidence.lifecycleViewports.push({width,height,status:info.status,assetState:info.assetState,
      geometry:await evaluate("(() => { const s=document.getElementById('hands-stage'),c=document.getElementById('hands-canvas'); return {stageWidth:s.clientWidth,stageHeight:s.clientHeight,bufferWidth:c.width,bufferHeight:c.height,rect:c.getBoundingClientRect().toJSON()}; })()")});
  }
  await cdp("Emulation.clearDeviceMetricsOverride",{});
  await cdp("Emulation.setEmulatedMedia",{features:[]});
  const beforeReload=await evaluate("performance.timeOrigin");
  await cdp("Page.reload",{});
  await waitFor(`performance.timeOrigin !== ${beforeReload} && document.documentElement?.dataset.ui008ShellReady === 'true'`);
  await presentHands();
  await waitFor("document.getElementById('hands-stage').dataset.status === 'ready' && globalThis.__pianoHands.info().assetState === 'ready'",30000);
  assert.deepEqual(core.musicalCommands,beforeCommands);
});

await test("reload with Compatibility selected resumes the same renderer in Auto", async () => {
  await evaluate(`(() => {const q=document.getElementById('hands-quality');q.value='COMPATIBILITY';q.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  const before=await evaluate('performance.timeOrigin');
  await cdp('Page.reload',{});
  await waitFor(`performance.timeOrigin !== ${before} && document.documentElement?.dataset.ui008ShellReady === 'true'`);
  await presentHands();
  await waitFor(`document.getElementById('hands-stage').dataset.status === 'compatibility' && !!globalThis.__pianoHands`);
  await evaluate(`(() => {const q=document.getElementById('hands-quality');q.value='AUTO';q.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await waitFor(`document.getElementById('hands-stage').dataset.status === 'ready' && globalThis.__pianoHands.info()?.assetState === 'ready'`,30000);
  assert.equal((await handsStage()).hands,'asset+asset');
  assert.equal(await evaluate('document.querySelectorAll("#hands-canvas").length'),1);
});

/* ---------------- report ---------------- */

console.log("\n--- recorded evidence (honest scope) ---");
console.log(JSON.stringify(evidence, null, 1));
console.log(`\n${passes} passed, ${failures} failed`);
ws.close();
server.close();
chrome.kill("SIGKILL");
await rm(profile, { recursive: true, force: true }).catch(() => {});
if (failures > 0) process.exit(1);
