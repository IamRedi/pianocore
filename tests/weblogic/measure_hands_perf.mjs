// PIANO-UI-007 performance measurement (NOT an assertion suite).
//
// Measures, separately and with exact scope:
//   1. MotionEngine evaluate()      — Node, dense canonical-shape plan
//   2. Analytical IK solveHandPose() — Node, same plan
//   3. Scene update + frame interval — real packaged UI in headless
//      Chromium over loopback HTTP
//
// HONEST SCOPE: the browser numbers below come from HEADLESS Chromium on
// SwiftShader (software rendering). They are NEVER device FPS, tablet
// performance, Raspberry Pi performance or GPU hardware performance.
//
// Run: TMPDIR=/var/tmp node tests/weblogic/measure_hands_perf.mjs
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { createServer } from "node:http";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { extname, join, resolve } from "node:path";

import { noteCenterFraction, PIANO_NOTE_MIN, PIANO_NOTE_MAX } from "../../src/piano_core/web/static/app.js";
import { createMotionView, evaluate, solveHandPose, contactIntegrity } from "../../src/piano_core/web/static/handmotion.js";

const require = createRequire(import.meta.url);
const WebSocket = require("ws");
const ROOT = resolve(import.meta.dirname, "../..");
const STATIC = join(ROOT, "src/piano_core/web/static");
const CHROME = process.env.CHROME || "/usr/bin/google-chrome";

/* ---------------- 1+2: Node MotionEngine / IK ---------------- */

const geometry = {
  centerFractions: (() => {
    const map = new Map();
    for (let midi = PIANO_NOTE_MIN; midi <= PIANO_NOTE_MAX; midi += 1) {
      map.set(midi, noteCenterFraction(midi));
    }
    return map;
  })(),
};

// Dense realistic material: ~6 s of music, chords + held notes + fast
// runs on both hands (2,000+ targets).
const targets = [];
let index = 0;
for (let t = 0; t < 150; t += 0.06) {
  const chord = 1 + (Math.floor(t * 10) % 4);
  for (let k = 0; k < chord; k += 1) {
    targets.push({
      score_index: index,
      target_id: `score:${index}`,
      midi: 21 + ((index * 7) % 88),
      channel: 1,
      onset_seconds: t,
      duration_seconds: 0.3 + ((index % 5) * 0.1),
      hand: index % 2 === 0 ? "RIGHT" : "LEFT",
      finger: 1 + (k % 5),
      alternatives: [],
    });
    index += 1;
  }
}
const plan = { plan_id: `fingering-sha256:${"f".repeat(64)}`, targets };
const view = createMotionView({
  songId: "perf",
  sourceVersion: `score-sha256:${"a".repeat(64)}`,
  plan,
  geometry,
});

function measure(label, fn, iterations) {
  for (let warm = 0; warm < 2000; warm += 1) fn(warm);
  const start = process.hrtime.bigint();
  for (let i = 0; i < iterations; i += 1) fn(i);
  const end = process.hrtime.bigint();
  const usPerOp = Number(end - start) / 1000 / iterations;
  console.log(
    `${label}: ${usPerOp.toFixed(3)} us/op over ${iterations} iterations ` +
      `(dense plan: ${targets.length} targets, both hands)`
  );
  return usPerOp;
}

console.log("=== Node (Ubuntu mini PC, Node " + process.version + ") ===");
const evalUs = measure(
  "MotionEngine evaluate",
  (i) => evaluate(view, (i % 14990) / 100, 1),
  20000
);
const ikUs = measure(
  "IK solveHandPose (both hands)",
  (i) => {
    const state = evaluate(view, (i % 14990) / 100, 1);
    if (state.hands.LEFT) solveHandPose(state.hands.LEFT);
    if (state.hands.RIGHT) solveHandPose(state.hands.RIGHT);
  },
  20000
);
// Contact integrity spot check on the measured path.
const integrityState = evaluate(view, 42.42, 1);
for (const hand of ["LEFT", "RIGHT"]) {
  if (integrityState.hands[hand]) {
    assert.equal(
      contactIntegrity(integrityState.hands[hand], solveHandPose(integrityState.hands[hand])),
      true
    );
  }
}

/* ---------------- 3: browser scene update + frame interval ---------------- */

function makeScore(songId, notes, duration, sourceChar) {
  return {
    schema_version: 1,
    song_id: songId,
    source_version: `score-sha256:${sourceChar.repeat(64)}`,
    title: `Song ${songId}`,
    duration_seconds: duration,
    time_basis: "song_seconds",
    note_count: notes.length,
    notes,
    diagnostics: { percussion_note_ons: 0, outside_keyboard_note_ons: 0, unpaired_note_offs: 0, retriggered_note_ons: 0, unterminated_note_ons: 0 },
    notation: { supported: [], unsupported: [] },
  };
}
const browserNotes = targets.slice(0, 400).map((t2) => ({
  index: t2.score_index,
  midi: t2.midi,
  channel: 1,
  velocity: 80,
  onset_seconds: t2.onset_seconds / 25,
  duration_seconds: t2.duration_seconds,
}));
const core = {
  playback: { state: "IDLE", songId: null, position: 0, playingSince: null, tempo: 1 },
  score: makeScore("perf-song", browserNotes, 6, "a"),
};
core.score.targets = core.score.notes.map((n) => n);
function planFor(score) {
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
    targets: score.notes.map((note, slot) => ({
      score_index: note.index,
      target_id: `score:${note.index}`,
      midi: note.midi,
      channel: 1,
      onset_seconds: note.onset_seconds,
      duration_seconds: note.duration_seconds,
      hand: note.midi < 60 ? "LEFT" : "RIGHT",
      finger: 1 + (slot % 5),
      alternatives: [],
    })),
    warnings: [],
    excluded_note_count: 0,
    config: { strategy: "balanced_v1", max_hand_span_semitones: 12, alternative_count: 1 },
    capabilities: { manual_overrides: false, finger_sensitive_grading: false },
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
  if (url.pathname === "/" || url.pathname === "/ui") {
    response.writeHead(302, { location: "/ui/" });
    response.end();
    return;
  }
  if (url.pathname.startsWith("/ui/")) {
    const name = url.pathname.slice(4) || "index.html";
    try {
      const body = await readFile(join(STATIC, name));
      const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".webmanifest": "application/manifest+json", ".txt": "text/plain" };
      response.writeHead(200, {
        "content-type": types[extname(name)] || "application/octet-stream",
        "cache-control": "no-store",
      });
      response.end(body);
    } catch {
      response.writeHead(404).end();
    }
    return;
  }
  const routes = {
    "/api/v1/live-notes": { schema_version: 1, runtime_epoch: "e", sequence: 1, physical: { notes: [], outside_keyboard_count: 0, sustain: { value: 0, pressed: false }, midi_connection: "CONNECTED", revision: 1 }, playback: { available: true, notes: [], voices: [], outside_keyboard_count: 0, sustain_channels: [] } },
    "/api/v1/health": { status: "ok", application: { state: "READY", since: "2026-09-20T00:00:00Z" }, midi_input: { connection: "CONNECTED" }, library: { ready: true, entries: 1 }, playback: null },
    "/api/v1/led": { available: false, configured: false, enabled: false, connection: "DISABLED", mode: null, brightness: null, active_note_count: 0, practice_active: false, last_error: null, transport: null, dropped_events: 0, frames_sent: 0 },
  };
  if (routes[url.pathname]) {
    const value = { ...routes[url.pathname] };
    if (url.pathname === "/api/v1/health") value.playback = playbackSnapshot();
    json(response, value);
    return;
  }
  if (url.pathname === "/api/v1/learning") {
    json(response, { available: true, state: "IDLE", song_id: null, title: null, step_index: null, total_steps: null, completed_steps: 0, incorrect_attempts: 0, progress: 0, target: null, started_at: null, ended_at: null, error: null, midi_connection: "CONNECTED", session_id: null, mode: null, hand_selection: null, section: null, target_tempo: null, accepted_notes: [], latest_outcome: "NONE", repetition: { current: 0, required: 1, successful: 0, failed: 0 }, generation: 0, version: 0, updated_at: null, capabilities: { supported_modes: [], missed_outcome: false, hand_selection: false, rhythm_grading: false, persistence: "runtime_only", section_basis: "song_time_seconds" } });
    return;
  }
  if (url.pathname === "/api/v1/state") {
    json(response, { midi_connection: "CONNECTED", sustain: { pressed: false, value: 0 }, pressed_notes: [], last_input: null, revision: 1 });
    return;
  }
  if (url.pathname === "/api/v1/playback") {
    json(response, playbackSnapshot());
    return;
  }
  if (url.pathname.startsWith("/api/v1/score/")) { json(response, core.score); return; }
  if (url.pathname.startsWith("/api/v1/fingering/")) { json(response, planFor(core.score)); return; }
  if (url.pathname === "/api/v1/library/songs/perf-song") {
    json(response, { song_id: "perf-song", title: "Song perf-song", filename: "perf-song.mid", relpath: "perf-song.mid", extension: ".mid", size_bytes: 1000, status: "READY", midi_format: 1, track_count: 1, ticks_per_beat: 96, duration_seconds: 6, tempo_bpm_first: 120, indexed_at_utc: "2026-09-20T00:00:00Z" });
    return;
  }
  if (url.pathname === "/api/v1/library/songs") {
    json(response, { query: { q: null, limit: 20, offset: 0, status: null, format: null }, count: 0, songs: [] });
    return;
  }
  if (url.pathname === "/api/v1/library") {
    json(response, { ready: true, total: 1, parsed: 1, pending: 0, invalid: 0, unsupported: 0 });
    return;
  }
  json(response, { error: { code: "not_found", message: "not found" } }, 404);
});
function playbackSnapshot() {
  const pb = core.playback;
  const duration = core.score.duration_seconds;
  let position = pb.position;
  if (pb.state === "PLAYING" && pb.playingSince !== null) {
    position = pb.position + (Date.now() - pb.playingSince) / 1000;
  }
  position = Math.max(0, Math.min(position, duration));
  return { available: true, state: pb.state, tempo_multiplier: pb.tempo, position_seconds: Math.round(position * 1000) / 1000, duration_seconds: duration, events_sent: 0, error: null, song_id: pb.songId, title: pb.songId ? core.score.title : null };
}

await new Promise((done) => server.listen(0, "127.0.0.1", done));
const appPort = server.address().port;
const probe = createServer();
await new Promise((done) => probe.listen(0, "127.0.0.1", done));
const debugPort = probe.address().port;
await new Promise((done) => probe.close(done));
const profile = await mkdtemp(join(tmpdir(), "piano-ui007-perf-"));
const chrome = spawn(CHROME, [
  "--headless=new", "--no-sandbox", "--disable-dev-shm-usage",
  "--enable-unsafe-swiftshader",
  `--remote-debugging-port=${debugPort}`, `--user-data-dir=${profile}`,
  `http://127.0.0.1:${appPort}/ui/#/player`,
], { stdio: ["ignore", "ignore", "pipe"] });

async function fetchJson(url) {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    try { return await (await fetch(url)).json(); } catch { await sleep(50); }
  }
  throw new Error(`Chrome debug endpoint unavailable: ${url}`);
}
const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
const targetsList = await fetchJson(`http://127.0.0.1:${debugPort}/json/list`);
const page = targetsList.find((item) => item.type === "page");
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((done, fail) => { ws.once("open", done); ws.once("error", fail); });
let cdpId = 0;
const pending = new Map();
ws.on("message", (raw) => {
  const message = JSON.parse(raw);
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
async function cdpEvaluate(expression) {
  const result = await cdp("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description);
  return result.result.value;
}
async function waitFor(expression, timeout = 60000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await cdpEvaluate(expression).catch(() => false)) return;
    await sleep(60);
  }
  throw new Error(`Timed out: ${expression}`);
}
await cdp("Runtime.enable");
await cdp("Page.enable");

core.playback = { state: "PLAYING", songId: "perf-song", position: 0, playingSince: Date.now(), tempo: 1 };
await waitFor("!!globalThis.__pianoHands && globalThis.__pianoHands.info() && globalThis.__pianoHands.info().status === 'ready'");
// Let the AUTO quality settle and the rAF loop run for a warmup period.
await sleep(8000);
const samples = await cdpEvaluate(`(async () => {
  const frames = 300;
  const intervals = [];
  const renders = [];
  let last = null;
  for (let i = 0; i < frames; i += 1) {
    await new Promise((done) => requestAnimationFrame(() => done()));
    const info = globalThis.__pianoHands.info();
    if (last !== null) intervals.push(info.lastFrameIntervalMs);
    renders.push(info.lastRenderMs);
    last = info;
  }
  const percentile = (list, p) => {
    const sorted = [...list].sort((a, b) => a - b);
    return sorted[Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length))];
  };
  return {
    frames,
    intervalMedian: percentile(intervals, 50),
    intervalP95: percentile(intervals, 95),
    renderMedian: percentile(renders, 50),
    renderP95: percentile(renders, 95),
    backend: globalThis.__pianoHands.info().backend,
    effectiveQuality: globalThis.__pianoHands.info().effectiveQuality,
  };
})()`);

console.log("=== Browser (HEADLESS Chromium " + CHROME + ", SwiftShader software rendering) ===");
console.log(
  `scene update (renderer.render incl. IK solve): median ${samples.renderMedian} ms, ` +
  `p95 ${samples.renderP95} ms over ${samples.frames} frames`
);
console.log(
  `browser frame interval: median ${samples.intervalMedian} ms ` +
  `(p95 ${samples.intervalP95} ms), backend ${samples.backend}, ` +
  `quality ${samples.effectiveQuality}`
);
console.log(
  "SCOPE: headless Chromium + SwiftShader (software). NOT device FPS, " +
  "NOT tablet performance, NOT Raspberry Pi performance, NOT GPU hardware performance."
);

ws.close();
server.close();
chrome.kill("SIGKILL");
await rm(profile, { recursive: true, force: true }).catch(() => {});
