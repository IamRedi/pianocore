// PIANO-UI-005: REAL Chromium/HTTP Visual Learning System validation.
//
// Everything is real: packaged Google Chrome (headless), a real loopback
// HTTP appliance serving the real packaged app.js/visualizer.js over
// HTTP, synthetic-but-real canonical /api/v1 payloads (playback anchor
// truth, Learning snapshots, score projections), the real browser fetch
// stack, real canvas rendering and the real visual wiring. Loopback
// only, controlled; never touches a device, deployed service, or
// non-loopback endpoint.
//
// Validated here (drawing + integration; the pure mapping model is
// covered by visualizer.test.mjs):
//   1. three visual modes render and switch during playback without
//      losing song position or Core authority;
//   2. Learning WAITING drives the expected-target overlay and the
//      self-paced position;
//   3. stale Player truth freezes the visual (no extrapolation);
//   4. reload reconstructs visual truth from authoritative state only;
//   5. empty and zero-duration material render explicit safe states;
//   6. tablet portrait/landscape, desktop and high-DPI viewports with
//      zero horizontal overflow;
//   7. synthetic performance: 100/500-note songs, dense chords and
//      rapid mode switching keep frame preparation bounded.
// Run with TMPDIR=/var/tmp when the host tmpfs is full.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { createServer } from "node:http";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { extname, join, resolve } from "node:path";

const require = createRequire(import.meta.url);
const WebSocket = require("ws");
const ROOT = resolve(import.meta.dirname, "../..");
const STATIC = join(ROOT, "src/piano_core/web/static");
const CHROME = process.env.CHROME || "/usr/bin/google-chrome";

let failures = 0;
const pageErrors = [];
async function test(name, fn) {
  const errorCount = pageErrors.length;
  try {
    await fn();
    assert.equal(
      pageErrors.length,
      errorCount,
      `uncaught page error: ${pageErrors.slice(errorCount).join(" | ")}`
    );
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

function makeFingering(score) {
  return {
    schema_version: 1,
    engine_version: "balanced-engine-v1",
    plan_id: `fingering-sha256:${"b".repeat(64)}`,
    song_id: score.song_id,
    source_version: score.source_version,
    version: 1,
    generation: 1,
    strategy: "balanced_v1",
    provenance: "DETERMINISTIC_INFERENCE",
    targets: score.notes
      .filter((note) => note.midi >= 21 && note.midi <= 108 && note.channel !== 10)
      .map((note) => ({
        score_index: note.index,
        target_id: `score:${note.index}`,
        midi: note.midi,
        channel: note.channel,
        onset_seconds: note.onset_seconds,
        duration_seconds: note.duration_seconds,
        hand: note.midi < 60 ? "LEFT" : "RIGHT",
        finger: 2,
        alternatives: [3],
      })),
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

function simpleNotes(count, duration) {
  const notes = [];
  for (let i = 0; i < count; i += 1) {
    notes.push({
      index: i,
      midi: 21 + ((i * 7) % 87),
      channel: 1,
      velocity: 40 + (i % 80),
      onset_seconds: (i / count) * duration,
      duration_seconds: Math.min(0.6, duration / count / 2),
    });
  }
  return notes;
}

function denseChordNotes(chords, duration) {
  const notes = [];
  let index = 0;
  for (let c = 0; c < chords; c += 1) {
    const onset = (c / chords) * duration;
    for (const midi of [48, 52, 55, 60, 64, 67, 72]) {
      notes.push({
        index: index++,
        midi,
        channel: 1,
        velocity: 90,
        onset_seconds: onset,
        duration_seconds: 0.25,
      });
    }
  }
  return notes;
}

const SONG_DURATION = 120;
const core = {
  epoch: "visual-runtime-a",
  playback: {
    state: "IDLE",
    songId: null,
    position: 0,
    playingSince: null,
    tempo: 1.0,
  },
  learning: null, // canonical Learning snapshot or null
  learningFailures: false,
  scoreFailures: false,
  fingeringFailures: false,
  scores: new Map(),
  scoreRequests: 0,
  scoreDelayMs: 0,
  fingeringDelayMs: 0,
  streams: new Set(),
  sequence: 0,
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
  if (!core.learning) {
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
  return core.learning;
}

function noteSnapshot() {
  core.sequence += 1;
  return {
    schema_version: 1,
    runtime_epoch: core.epoch,
    sequence: core.sequence,
    physical: {
      notes: [], outside_keyboard_count: 0,
      sustain: { value: 0, pressed: false },
      midi_connection: "CONNECTED", revision: core.sequence,
    },
    playback: {
      available: true,
      notes: [], voices: [], outside_keyboard_count: 0, sustain_channels: [],
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
  if (url.pathname === "/api/v1/live-notes") {
    json(response, noteSnapshot());
    return;
  }
  if (url.pathname === "/api/v1/live-notes/stream") {
    response.writeHead(200, {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache",
      connection: "close",
    });
    core.streams.add(response);
    const frame = `id: ${core.sequence}\nevent: snapshot\ndata: ${JSON.stringify(noteSnapshot())}\n\n`;
    response.write(frame);
    const heartbeat = setInterval(() => {
      try { response.write(": heartbeat\n\n"); } catch { /* closed */ }
    }, 2000);
    request.on("close", () => {
      clearInterval(heartbeat);
      core.streams.delete(response);
    });
    return;
  }
  if (url.pathname === "/api/v1/health") {
    json(response, {
      status: "ok",
      application: { state: "READY", since: "2026-09-18T00:00:00Z" },
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
    if (core.learningFailures && url.pathname === "/api/v1/learning") {
      json(response, { error: { code: "internal_error", message: "synthetic failure", status: 500 } }, 500);
      return;
    }
    json(response, learningSnapshot());
    return;
  }
  if (url.pathname.startsWith("/api/v1/score/")) {
    core.scoreRequests += 1;
    if (core.scoreFailures) {
      json(response, { error: { code: "internal_error", message: "synthetic failure", status: 500 } }, 500);
      return;
    }
    const songId = decodeURIComponent(url.pathname.slice("/api/v1/score/".length));
    const score = core.scores.get(songId);
    if (!score) {
      json(response, { error: { code: "song_not_found", message: "unknown", status: 404 } }, 404);
      return;
    }
    if (core.scoreDelayMs) {
      setTimeout(() => json(response, score), core.scoreDelayMs);
    } else json(response, score);
    return;
  }
  if (url.pathname.startsWith("/api/v1/fingering/")) {
    if (core.fingeringFailures) {
      json(response, { error: { code: "fingering_unsupported", message: "synthetic", status: 422 } }, 422);
      return;
    }
    const songId = decodeURIComponent(url.pathname.slice("/api/v1/fingering/".length));
    const score = core.scores.get(songId);
    if (!score) {
      json(response, { error: { code: "song_not_found", message: "unknown", status: 404 } }, 404);
      return;
    }
    const payload = makeFingering(score);
    if (core.fingeringDelayMs) {
      setTimeout(() => json(response, payload), core.fingeringDelayMs);
    } else json(response, payload);
    return;
  }
  if (url.pathname.startsWith("/api/v1/library/songs/")) {
    const songId = decodeURIComponent(url.pathname.slice("/api/v1/library/songs/".length));
    json(response, {
      song_id: songId, title: `Song ${songId}`, filename: `${songId}.mid`,
      relpath: `${songId}.mid`, extension: ".mid", size_bytes: 1000,
      status: "READY", midi_format: 1, track_count: 1, ticks_per_beat: 96,
      duration_seconds: SONG_DURATION, tempo_bpm_first: 120,
      indexed_at_utc: "2026-09-18T00:00:00Z",
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
const profile = await mkdtemp(join(tmpdir(), "piano-ui005-chrome-"));
const chrome = spawn(CHROME, [
  "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
  `--remote-debugging-port=${debugPort}`, `--user-data-dir=${profile}`,
  `http://127.0.0.1:${appPort}/ui/#/learn`,
], { stdio: ["ignore", "ignore", "pipe"] });

async function fetchJson(url) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
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
async function waitFor(expression, timeout = 10000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await evaluate(expression)) return;
    await sleep(40);
  }
  throw new Error(`Timed out waiting for ${expression}`);
}
const stage = () => evaluate("({ ...document.getElementById('visual-stage').dataset })");
const summaryText = () => evaluate("document.getElementById('visual-summary').textContent");

async function clickMode(mode) {
  await evaluate(`document.getElementById('visual-mode-${mode}').click()`);
}

await cdp("Runtime.enable");
await cdp("Page.enable");
await waitFor("!!document.getElementById('visual-stage')");
await waitFor("!!document.querySelector('.song-action-btn[data-action=learn]')");
await evaluate("document.querySelector('.song-action-btn[data-action=learn]').click()");

/* ---------------- tests ---------------- */

await test("no song: explicit no-song state (nothing invented)", async () => {
  await waitFor(`document.getElementById('visual-stage').dataset.status === 'no-song'`);
  const data = await stage();
  assert.equal(data.status, "no-song");
  assert.ok((await summaryText()).toLowerCase().includes("no song"));
});

core.scores.set("song-a", makeScore({ songId: "song-a", notes: simpleNotes(120, SONG_DURATION), duration: SONG_DURATION }));
core.playback = { state: "PLAYING", songId: "song-a", position: 10.0, playingSince: Date.now(), tempo: 1.0 };

await test("playback drives the falling view from canonical score truth", async () => {
  await waitFor(`document.getElementById('visual-stage').dataset.status === 'ready'`);
  const data = await stage();
  assert.equal(data.mode, "falling");
  assert.ok(Number(data.position) >= 10, `position ${data.position}`);
  assert.ok(Number(data.visible) > 0, `visible ${data.visible}`);
  assert.ok(core.scoreRequests >= 1, "score endpoint was fetched");
  assert.ok((await summaryText()).includes("Song song-a"));
});

await test("version-bound inferred fingering reaches the visual consumer", async () => {
  await waitFor(`document.getElementById('visual-stage').dataset.fingeringStatus === 'ready'`);
  const data = await stage();
  assert.equal(data.fingeringStatus, "ready");
  assert.equal(Number(data.fingeringCount), 120);
});

await test("same-song source replacement clears old advice and rejects delayed A", async () => {
  const songId = "same-source-replacement";
  const scoreA = makeScore({
    songId, duration: 10, sourceChar: "a",
    notes: [{
      index: 0, midi: 48, channel: 1, velocity: 80,
      onset_seconds: 0, duration_seconds: 4,
    }],
  });
  const scoreB = makeScore({
    songId, duration: 10, sourceChar: "b",
    notes: [
      {
        index: 0, midi: 52, channel: 1, velocity: 80,
        onset_seconds: 0, duration_seconds: 4,
      },
      {
        index: 1, midi: 67, channel: 1, velocity: 80,
        onset_seconds: 1, duration_seconds: 2,
      },
    ],
  });
  core.scores.set(songId, scoreA);
  core.fingeringDelayMs = 2500;
  core.playback = {
    state: "PAUSED", songId, position: 1, playingSince: null, tempo: 1,
  };
  await waitFor(
    `document.getElementById('visual-stage').dataset.songId === '${songId}' &&
     document.getElementById('visual-stage').dataset.fingeringStatus === 'loading'`
  );
  core.scores.set(songId, scoreB);
  core.fingeringDelayMs = 400;
  await waitFor(
    `document.getElementById('visual-stage').dataset.fingeringStatus === 'loading' &&
     Number(document.getElementById('visual-stage').dataset.fingeringCount) === 0`,
    10000
  );
  await waitFor(
    `document.getElementById('visual-stage').dataset.fingeringStatus === 'ready' &&
     Number(document.getElementById('visual-stage').dataset.fingeringCount) === 2`,
    10000
  );
  await sleep(3000);
  assert.equal(Number((await stage()).fingeringCount), 2);
  core.fingeringDelayMs = 0;
  core.playback = {
    state: "PLAYING", songId: "song-a", position: 10,
    playingSince: Date.now(), tempo: 1,
  };
  await waitFor(`document.getElementById('visual-stage').dataset.songId === 'song-a'`);
});

await test("fingering failure clears advice and a later poll recovers it", async () => {
  core.fingeringFailures = true;
  core.scores.set("finger-failure", makeScore({
    songId: "finger-failure", notes: simpleNotes(20, 30), duration: 30,
  }));
  core.playback = { state: "PAUSED", songId: "finger-failure", position: 3, playingSince: null, tempo: 1 };
  await waitFor(`document.getElementById('visual-stage').dataset.songId === 'finger-failure' && document.getElementById('visual-stage').dataset.fingeringStatus === 'error'`);
  assert.equal(Number((await stage()).fingeringCount), 0);
  core.fingeringFailures = false;
  await waitFor(`document.getElementById('visual-stage').dataset.fingeringStatus === 'ready'`, 15000);
  assert.equal(Number((await stage()).fingeringCount), 20);
  core.playback = { state: "PLAYING", songId: "song-a", position: 10, playingSince: Date.now(), tempo: 1 };
  await waitFor(`document.getElementById('visual-stage').dataset.songId === 'song-a' && document.getElementById('visual-stage').dataset.fingeringStatus === 'ready'`);
});

await test("Sheet and Falling draw explicit left/right finger labels from one plan", async () => {
  core.scores.set("finger-labels", makeScore({
    songId: "finger-labels", duration: 20,
    notes: [
      { index: 0, midi: 48, channel: 1, velocity: 90, onset_seconds: 2, duration_seconds: 3 },
      { index: 1, midi: 72, channel: 1, velocity: 90, onset_seconds: 2, duration_seconds: 3 },
    ],
  }));
  await evaluate(`(() => {
    window.__fingerLabels = [];
    const original = CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText = function(text, ...args) {
      if (/^[LR][1-5]$/.test(String(text))) window.__fingerLabels.push(String(text));
      return original.call(this, text, ...args);
    };
  })()`);
  core.playback = { state: "PAUSED", songId: "finger-labels", position: 2, playingSince: null, tempo: 1 };
  await waitFor(`document.getElementById('visual-stage').dataset.songId === 'finger-labels' && document.getElementById('visual-stage').dataset.fingeringStatus === 'ready'`);
  await clickMode("hybrid");
  await waitFor(`window.__fingerLabels.includes('L2') && window.__fingerLabels.includes('R2')`);
  const labels = await evaluate("[...new Set(window.__fingerLabels)]");
  assert.ok(labels.includes("L2"));
  assert.ok(labels.includes("R2"));
  core.playback = { state: "PLAYING", songId: "song-a", position: 10, playingSince: Date.now(), tempo: 1 };
  await waitFor(`document.getElementById('visual-stage').dataset.songId === 'song-a'`);
  await clickMode("falling");
});

await test("mode switching during playback keeps song position and authority", async () => {
  await waitFor(`document.getElementById('visual-stage').dataset.status === 'ready'`);
  const before = Number((await stage()).position);
  await clickMode("sheet");
  await sleep(150);
  const sheetData = await stage();
  assert.equal(sheetData.mode, "sheet");
  assert.equal(sheetData.status, "ready");
  const sheetPos = Number(sheetData.position);
  await clickMode("hybrid");
  await sleep(150);
  const hybridData = await stage();
  assert.equal(hybridData.mode, "hybrid");
  await clickMode("falling");
  await sleep(150);
  const after = Number((await stage()).position);
  assert.ok(sheetPos >= before, `sheet kept position ${sheetPos} >= ${before}`);
  assert.ok(after >= sheetPos, `position never reset: ${after} >= ${sheetPos}`);
  assert.equal((await stage()).status, "ready");
  // Both panes exist; in hybrid both are visible and sized.
  await clickMode("hybrid");
  await sleep(150);
  const hybridSizes = await evaluate(`(() => {
    const panes = ['sheet-pane', 'falling-pane'].map((id) => document.getElementById(id));
    return panes.map((p) => ({ hidden: p.hidden, w: p.clientWidth, h: p.clientHeight }));
  })()`);
  for (const pane of hybridSizes) {
    assert.equal(pane.hidden, false);
    assert.ok(pane.w > 0 && pane.h > 0);
  }
});

await test("pause freezes the falling stream at the authoritative position", async () => {
  core.playback = { ...core.playback, state: "PAUSED", playingSince: null, position: 35.0 };
  await waitFor(`Math.abs(Number(document.getElementById('visual-stage').dataset.position) - 35) < 0.6`);
  const frozenAt = Number((await stage()).position);
  await sleep(1400);
  const still = Number((await stage()).position);
  assert.ok(Math.abs(still - frozenAt) < 0.6, `frozen: ${frozenAt} vs ${still}`);
  assert.equal((await stage()).status, "ready");
});

await test("learning WAITING owns the visual position and shows the target overlay", async () => {
  core.learning = {
    available: true, state: "WAITING", song_id: "song-a", title: "Song song-a",
    step_index: 4, total_steps: 120, completed_steps: 4, incorrect_attempts: 0,
    progress: 0.0333,
    target: { notes: [60, 64, 67], names: ["C4", "E4", "G4"], chord: true, onset_seconds: 42.0, target_id: "target:4:60-64-67" },
    started_at: "2026-09-18T12:00:00Z", ended_at: null, error: null,
    midi_connection: "CONNECTED", session_id: "sess-1", mode: "WAIT_FOR_NOTE",
    hand_selection: null, section: { start_seconds: 30.0, end_seconds: 60.0 },
    target_tempo: null, accepted_notes: [], latest_outcome: "NONE",
    repetition: { current: 0, required: 1, successful: 0, failed: 0 },
    generation: 1, version: 9, updated_at: "2026-09-18T12:00:01Z",
    capabilities: {
      supported_modes: ["WAIT_FOR_NOTE"], missed_outcome: false,
      hand_selection: false, rhythm_grading: false,
      persistence: "runtime_only", section_basis: "song_time_seconds",
    },
  };
  await waitFor(`Math.abs(Number(document.getElementById('visual-stage').dataset.position) - 42) < 0.75`);
  const data = await stage();
  assert.equal(data.status, "ready");
  assert.ok((await summaryText()).includes("target C4 + E4 + G4"));
  // Self-paced: position stays pinned to the owed target across polls.
  const first = Number((await stage()).position);
  await sleep(1300);
  const second = Number((await stage()).position);
  assert.ok(Math.abs(second - first) < 0.2, `self-paced static: ${first} vs ${second}`);
});

await test("learning target advance moves the visual position to the next target", async () => {
  core.learning = {
    ...core.learning,
    step_index: 5,
    version: 10,
    target: { notes: [72], names: ["C5"], chord: false, onset_seconds: 43.0, target_id: "target:5:72" },
  };
  await waitFor(`Math.abs(Number(document.getElementById('visual-stage').dataset.position) - 43) < 0.75`);
});

await test("stale Player truth freezes the visual (no extrapolation)", async () => {
  // Switch back to playback authority, then stop serving progress:
  // hold PLAYING frozen at a fixed position and let freshness expire.
  core.learning = null;
  core.playback = { state: "PLAYING", songId: "song-a", position: 50.0, playingSince: null, tempo: 1.0 };
  await waitFor(`Math.abs(Number(document.getElementById('visual-stage').dataset.position) - 50) < 0.6`);
  // Force server-side silence: keep the payload constant and shift its
  // receipt into the past by freezing the synthetic clock.
  core.playback = { ...core.playback, playingSince: null };
  await evaluate(`(() => {
    // Simulate Core silence: the anchor ages past PLAYBACK_FRESHNESS_MS.
    const ev = new Event('visibilitychange');
    return true;
  })()`);
  // Simply wait beyond the 3.5s freshness window with no server changes:
  // the synthetic server keeps position pinned at 50 via fixed snapshots,
  // so the client anchor ages only if polls stop. Stop the poll cycle by
  // hiding the document (polls are visible-only).
  await evaluate(`(() => {
    window.__pianoTestVisibility = 'hidden';
    if (!Object.prototype.hasOwnProperty.call(document, 'visibilityState')) {
      Object.defineProperty(document, 'visibilityState', {
        configurable: true,
        get: () => window.__pianoTestVisibility,
      });
    }
    document.dispatchEvent(new Event('visibilitychange'));
    return true;
  })()`);
  await sleep(4200);
  const data = await stage();
  assert.equal(data.status, "stale", `status ${data.status}`);
  const frozen = Number(data.position);
  await sleep(1200);
  const still = Number((await stage()).position);
  assert.ok(Math.abs(still - still) === 0);
  assert.ok(Math.abs(Number(still) - frozen) < 0.05, `no drift: ${frozen} vs ${still}`);
  // Restore visibility; the next poll re-authorizes the visual.
  await evaluate(`(() => {
    window.__pianoTestVisibility = 'visible';
    document.dispatchEvent(new Event('visibilitychange'));
    return true;
  })()`);
  await waitFor(`document.getElementById('visual-stage').dataset.status === 'ready'`);
});

await test("learning stale de-authorizes the target overlay only", async () => {
  core.learning = {
    ...learningSnapshot(),
    state: "WAITING", song_id: "song-a", title: "Song song-a",
    step_index: 6, total_steps: 120, completed_steps: 6,
    target: { notes: [60], names: ["C4"], chord: false, onset_seconds: 44.0, target_id: "target:6:60" },
    session_id: "sess-2", mode: "WAIT_FOR_NOTE", version: 20,
    midi_connection: "CONNECTED", accepted_notes: [], latest_outcome: "NONE",
    repetition: { current: 0, required: 1, successful: 0, failed: 0 },
    generation: 2, updated_at: "2026-09-18T12:00:05Z",
    capabilities: {
      supported_modes: ["WAIT_FOR_NOTE"], missed_outcome: false,
      hand_selection: false, rhythm_grading: false,
      persistence: "runtime_only", section_basis: "song_time_seconds",
    },
  };
  core.playback = { state: "IDLE", songId: null, position: 0, playingSince: null, tempo: 1.0 };
  await waitFor(`document.getElementById('visual-summary').textContent.includes('target C4')`);
  core.learningFailures = true;
  await sleep(6500); // > LEARNING_FRESHNESS_MS (5s) of failures
  const summary = await summaryText();
  assert.ok(!summary.includes("target"), `target overlay removed: "${summary}"`);
  core.learningFailures = false;
  await waitFor(`document.getElementById('visual-summary').textContent.includes('target C4')`);
});

await test("reload reconstructs visual truth from authoritative state only", async () => {
  // Learning still WAITING at target onset 44.0 for song-a.
  const documentBeforeReload = await evaluate("performance.timeOrigin");
  await cdp("Page.reload", { ignoreCache: true });
  await waitFor(`performance.timeOrigin !== ${documentBeforeReload} && document.documentElement?.dataset.ui008ShellReady === 'true'`, 15000);
  await waitFor("!!document.getElementById('visual-stage')");
await waitFor("!!document.querySelector('.song-action-btn[data-action=learn]')");
await evaluate("document.querySelector('.song-action-btn[data-action=learn]').click()");
  await waitFor(`document.getElementById('visual-stage').dataset.status === 'ready'`, 15000);
  await waitFor(`Math.abs(Number(document.getElementById('visual-stage').dataset.position) - 44) < 0.75`, 15000);
  // The score (and therefore its title) must also be reconstructed from
  // the authoritative song identity — no visual history exists.
  await waitFor(`document.getElementById('visual-summary').textContent.includes('Song song-a')`, 15000);
  await waitFor(`document.getElementById('visual-summary').textContent.includes('target C4')`, 15000);
});

await test("empty material renders an explicit empty state", async () => {
  core.learning = null;
  core.scores.set("song-empty", makeScore({ songId: "song-empty", notes: [], duration: 12.0 }));
  core.playback = { state: "PLAYING", songId: "song-empty", position: 1.0, playingSince: Date.now(), tempo: 1.0 };
  await waitFor(`document.getElementById('visual-stage').dataset.status === 'empty'`);
  assert.ok((await summaryText()).toLowerCase().includes("no playable"));
  core.scores.set("song-zero", makeScore({
    songId: "song-zero",
    notes: [
      { index: 0, midi: 60, channel: 1, velocity: 90, onset_seconds: 2.0, duration_seconds: 0 },
      { index: 1, midi: 64, channel: 1, velocity: 90, onset_seconds: 3.0, duration_seconds: 0 },
    ],
    duration: 6.0,
  }));
  core.playback = { state: "PLAYING", songId: "song-zero", position: 1.5, playingSince: Date.now(), tempo: 1.0 };
  await waitFor(`document.getElementById('visual-stage').dataset.status === 'ready'`);
  await waitFor(`Number(document.getElementById('visual-stage').dataset.visible) >= 1`);
});

await test("score fetch failure degrades honestly without breaking playback truth", async () => {
  core.scoreFailures = true;
  core.scores.set("song-broken", makeScore({ songId: "song-broken", notes: simpleNotes(10, 30), duration: 30 }));
  core.playback = { state: "PLAYING", songId: "song-broken", position: 2.0, playingSince: Date.now(), tempo: 1.0 };
  await waitFor(`document.getElementById('visual-stage').dataset.status === 'unavailable'`);
  // The player card keeps its own authoritative truth.
  const playerState = await evaluate("document.getElementById('player-state').textContent");
  assert.equal(playerState, "Playing");
  core.scoreFailures = false;
  await waitFor(`document.getElementById('visual-stage').dataset.status === 'ready'`);
});

/* ---------------- viewports ---------------- */

async function setViewport(width, height, scale) {
  await cdp("Emulation.setDeviceMetricsOverride", {
    width, height, deviceScaleFactor: scale, mobile: false,
  });
  await sleep(300);
}

await test("tablet landscape 1024x768: no horizontal overflow, visual sized", async () => {
  await setViewport(1024, 768, 1);
  // Immersive composition: normalize to the single-pane falling notes
  // mode (the configured hybrid mode legitimately splits the scene).
  await evaluate("document.getElementById('visual-mode-falling').click()");
  await waitFor("document.getElementById('visual-stage').dataset.mode === 'falling'");
  const overflow = await evaluate(
    "document.documentElement.scrollWidth - document.documentElement.clientWidth"
  );
  assert.ok(overflow <= 1, `overflow ${overflow}`);
  const rect = await evaluate(
    "(() => { const r = document.getElementById('falling-canvas').getBoundingClientRect(); return { w: r.width, h: r.height, route: document.body.dataset.ui008Route, sceneRoute: document.getElementById('ui008-workspace-scene')?.dataset.view, sceneParent: document.getElementById('ui008-workspace-scene')?.parentElement?.id || document.getElementById('ui008-workspace-scene')?.parentElement?.className, layoutOver: (() => { const l = document.querySelector('.layout'); return l.scrollHeight - l.clientHeight; })(), layoutCss: (() => { const l = document.querySelector('.layout'); const cs = getComputedStyle(l); return cs.display + '/' + cs.flexDirection + '/' + cs.maxWidth; })(), sceneRect: (() => { const r = document.getElementById('ui008-workspace-scene').getBoundingClientRect(); return Math.round(r.width) + 'x' + Math.round(r.height); })(), slotRect: (() => { const r = document.getElementById('ui008-scene-slot-learn').getBoundingClientRect(); return Math.round(r.top) + '-' + Math.round(r.bottom); })(), chain: (() => { const ids = ['ui008-workspace-scene', 'visual', 'visual-stage', 'falling-pane', 'falling-canvas']; return ids.map(id => { const el = document.getElementById(id); if (!el) return id + ':missing'; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return id + ':' + Math.round(r.width) + 'x' + Math.round(r.height) + '/' + cs.display + '/' + cs.height; }).join(' | '); })() }; })()"
  );
  assert.ok(rect.w > 400 && rect.h > 150, JSON.stringify(rect));
});

await test("tablet portrait 768x1024: no horizontal overflow, visual sized", async () => {
  await setViewport(768, 1024, 1);
  await evaluate("document.getElementById('visual-mode-falling').click()");
  await waitFor("document.getElementById('visual-stage').dataset.mode === 'falling'");
  const overflow = await evaluate(
    "document.documentElement.scrollWidth - document.documentElement.clientWidth"
  );
  assert.ok(overflow <= 1, `overflow ${overflow}`);
  const rect = await evaluate(
    "(() => { const r = document.getElementById('falling-canvas').getBoundingClientRect(); return { w: r.width, h: r.height }; })()"
  );
  assert.ok(rect.w > 250 && rect.h > 150, JSON.stringify(rect));
});

await test("desktop 1280x800 and high-DPI (dpr=2) backing store is crisp", async () => {
  await setViewport(1280, 800, 2);
  await evaluate("document.getElementById('visual-mode-falling').click()");
  await waitFor("document.getElementById('visual-stage').dataset.mode === 'falling'");
  const backing = await evaluate(
    "(() => { const c = document.getElementById('falling-canvas'); return { w: c.width, h: c.height, cw: c.clientWidth, ch: c.clientHeight }; })()"
  );
  assert.ok(backing.w >= backing.cw * 1.9, JSON.stringify(backing));
  const overflow = await evaluate(
    "document.documentElement.scrollWidth - document.documentElement.clientWidth"
  );
  assert.ok(overflow <= 1, `overflow ${overflow}`);
  await setViewport(1280, 800, 1);
});

await test("resize is stable: canvas re-renders without errors or geometry loss", async () => {
  await setViewport(900, 700, 1);
  const before = await stage();
  await setViewport(1400, 900, 1);
  await sleep(400);
  const after = await stage();
  assert.equal(after.status, before.status);
  assert.ok(Number(after.visible) > 0);
});

/* ---------------- synthetic performance ---------------- */

async function waitForScoreFrame(songId) {
  await waitFor(`document.getElementById('visual-stage').dataset.scoreSongId === ${JSON.stringify(songId)} && document.getElementById('visual-stage').dataset.status === 'ready'`);
  await sleep(300); // explicit warmup after the matching rendered score frame
}

async function measureFrames(count, songId) {
  return evaluate(`(async () => {
    const stage = document.getElementById('visual-stage');
    const samples = [];
    for (let i = 0; i < ${count}; i += 1) {
      await new Promise((done) => requestAnimationFrame(done));
      if (stage.dataset.scoreSongId !== ${JSON.stringify(songId)} || stage.dataset.status !== 'ready') throw new Error('benchmark scenario identity changed');
      samples.push(Number(stage.dataset.frameMs));
    }
    samples.sort((a, b) => a - b);
    const median = samples[Math.floor(samples.length / 2)];
    const p95 = samples[Math.floor(samples.length * 0.95)];
    const max = samples[samples.length - 1];
    return { median, p95, max };
  })()`);
}

await test("performance: 100-note song stays frame-bounded", async () => {
  await setViewport(1280, 800, 1);
  core.scores.set("perf-100", makeScore({ songId: "perf-100", notes: simpleNotes(100, SONG_DURATION), duration: SONG_DURATION }));
  core.playback = { state: "PLAYING", songId: "perf-100", position: 20.0, playingSince: Date.now(), tempo: 1.0 };
  await waitForScoreFrame("perf-100");
  const stats = await measureFrames(60, "perf-100");
  console.log(`    100 notes: median ${stats.median} ms, p95 ${stats.p95} ms, max ${stats.max} ms`);
  assert.ok(stats.p95 < 16, `p95 frame prep ${stats.p95} ms`);
});

await test("performance: 500-note song stays frame-bounded", async () => {
  core.scores.set("perf-500", makeScore({ songId: "perf-500", notes: simpleNotes(500, SONG_DURATION), duration: SONG_DURATION }));
  core.playback = { state: "PLAYING", songId: "perf-500", position: 40.0, playingSince: Date.now(), tempo: 1.0 };
  await waitForScoreFrame("perf-500");
  const stats = await measureFrames(60, "perf-500");
  console.log(`    500 notes: median ${stats.median} ms, p95 ${stats.p95} ms, max ${stats.max} ms`);
  assert.ok(stats.p95 < 16, `p95 frame prep ${stats.p95} ms`);
});

await test("performance: dense chord passage stays frame-bounded", async () => {
  core.scores.set("perf-chords", makeScore({ songId: "perf-chords", notes: denseChordNotes(300, SONG_DURATION), duration: SONG_DURATION }));
  core.playback = { state: "PLAYING", songId: "perf-chords", position: 60.0, playingSince: Date.now(), tempo: 1.0 };
  await waitForScoreFrame("perf-chords");
  const stats = await measureFrames(60, "perf-chords");
  console.log(`    dense chords (2100 notes): median ${stats.median} ms, p95 ${stats.p95} ms, max ${stats.max} ms`);
  assert.ok(stats.p95 < 16, `p95 frame prep ${stats.p95} ms`);
});

await test("performance: rapid mode switching stays bounded and error-free", async () => {
  const modes = ["sheet", "falling", "hybrid"];
  for (let i = 0; i < 21; i += 1) {
    await clickMode(modes[i % 3]);
    await sleep(30);
  }
  await sleep(400);
  const data = await stage();
  assert.equal(data.status, "ready");
  await waitForScoreFrame("perf-chords");
  const stats = await measureFrames(30, "perf-chords");
  console.log(`    post-switch: median ${stats.median} ms, p95 ${stats.p95} ms`);
  assert.ok(stats.p95 < 16, `p95 frame prep ${stats.p95} ms`);
});

await test("long-song viewport keeps memory of visible notes bounded", async () => {
  core.scores.set("perf-long", makeScore({ songId: "perf-long", notes: simpleNotes(500, 3600), duration: 3600 }));
  core.playback = { state: "PLAYING", songId: "perf-long", position: 1800.0, playingSince: Date.now(), tempo: 1.0 };
  await waitFor(`document.getElementById('visual-stage').dataset.status === 'ready'`);
  // Wait until the client actually saw the new song's position (the
  // previous frame may still describe the prior song).
  await waitFor(`Number(document.getElementById('visual-stage').dataset.position) > 1500`, 15000);
  await waitFor(`Number(document.getElementById('visual-stage').dataset.visible) > 0`, 15000);
  const longData = await stage();
  const visible = Number(longData.visible);
  assert.ok(visible < 120, `visible slice bounded: ${visible}`);
});


await test("remediation: delayed song B clears all song A score geometry immediately", async () => {
  core.learning=null;
  core.scores.set("clear-a",makeScore({songId:"clear-a",notes:simpleNotes(120,120),duration:120}));
  core.playback={state:"PLAYING",songId:"clear-a",position:10,playingSince:Date.now(),tempo:1};
  await waitForScoreFrame("clear-a");
  assert.ok(Number((await stage()).visible)>0);
  core.scoreDelayMs=2000;
  core.scores.set("clear-b",makeScore({songId:"clear-b",notes:[],duration:120}));
  core.playback={state:"PLAYING",songId:"clear-b",position:50,playingSince:Date.now(),tempo:1};
  await waitFor("document.getElementById('visual-stage').dataset.songId === 'clear-b' && document.getElementById('visual-stage').dataset.status === 'loading'");
  const data=await stage();
  assert.equal(data.visible,"0");assert.equal(data.scoreSongId,"");
  assert.equal(data.section,"");assert.equal(data.targetCount,"0");
  await waitFor("document.getElementById('visual-stage').dataset.status === 'empty'");
  core.scoreDelayMs=0;
});
await test("remediation: malformed current score errors then retries and recovers", async () => {
  core.scores.set("bad-200",{song_id:"bad-200"});
  core.playback={state:"PLAYING",songId:"bad-200",position:5,playingSince:Date.now(),tempo:1};
  await waitFor("document.getElementById('visual-stage').dataset.songId === 'bad-200' && document.getElementById('visual-stage').dataset.status === 'unavailable'");
  const requests=core.scoreRequests;
  assert.ok((await evaluate("document.getElementById('player-state').textContent")).length>0);
  core.scores.set("bad-200",makeScore({songId:"bad-200",notes:simpleNotes(100,120),duration:120}));
  await waitForScoreFrame("bad-200");
  assert.ok(core.scoreRequests>requests);
  assert.ok(Number((await stage()).position)>=5);
});
await test("remediation: canonical overlapping score shows long active note at song start", async () => {
  core.scores.set("overlap",makeScore({songId:"overlap",duration:30,notes:[
    {index:0,midi:60,channel:1,velocity:90,onset_seconds:0,duration_seconds:30},
    {index:1,midi:64,channel:1,velocity:80,onset_seconds:10,duration_seconds:1},
    {index:2,midi:67,channel:1,velocity:70,onset_seconds:20,duration_seconds:1}]}));
  core.playback={state:"PAUSED",songId:"overlap",position:0,playingSince:Date.now(),tempo:1};
  await clickMode("falling");await waitForScoreFrame("overlap");
  assert.equal((await stage()).position,"0");assert.equal((await stage()).visible,"1");
});

await test("remediation: stale/unavailable overlays paint finite pixels inside real canvas", async () => {
  const result=await evaluate(`(async()=>{
    const {VisualStage}=await import('/ui/visualizer.js');
    const results=[];
    for(const status of ['stale','unavailable']) {
      const canvas=document.createElement('canvas');canvas.width=800;canvas.height=300;
      const ctx=canvas.getContext('2d');const calls=[];
      const original=ctx.fillText.bind(ctx);
      ctx.fillText=(text,x,y)=>{calls.push([x,y]);original(text,x,y);};
      const pane={ctx,cssWidth:800,cssHeight:300};
      VisualStage.prototype._drawStaleOverlay.call({falling:{pane}}, {mode:'falling',status});
      results.push({finite:calls.flat().every(Number.isFinite),calls,
        painted:ctx.getImageData(1,150,1,1).data[3]>0,
        topClear:ctx.getImageData(1,1,1,1).data[3]===0});
    }
    return results;
  })()`);
  for(const r of result) {assert.ok(r.finite);assert.ok(r.painted);assert.ok(r.topClear);assert.deepEqual(r.calls,[[400,154]]);}
});

/* ---------------- teardown ---------------- */

try { ws.close(); } catch { /* already closed */ }
chrome.kill("SIGTERM");
await new Promise((done) => {
  chrome.once("exit", done);
  setTimeout(done, 3000);
});
await rm(profile, { recursive: true, force: true }).catch(() => {});
server.close();
await new Promise((done) => setTimeout(done, 200));

if (failures > 0) {
  console.error(`${failures} VISUAL BROWSER TEST(S) FAILED`);
  process.exit(1);
}
console.log("ALL VISUAL BROWSER TESTS PASSED");
