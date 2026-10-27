// PIANO-UI-003 U3-R3: REAL Chromium/HTTP command-outcome regression.
//
// This is the exact test class whose absence previously hid the browser
// defect: Node-only evidence classified a truncated response body by
// environment-specific exception text, while Chromium behaved differently.
// Everything here is real: real Google Chrome (packaged, headless), real
// loopback HTTP server that APPLIES each mutating command before answering,
// the real packaged app.js (served over HTTP and imported by the page), the
// real browser fetch stack, and the real PlayerCoordinator.
//
// Required regression: the server sends 200 headers for a PAUSE it has just
// applied, writes partial JSON and terminates the socket before body
// completion. The browser ApiClient MUST classify network_error / transport
// uncertainty (NOT invalid_response), the Player command outcome MUST become
// UNKNOWN, old freshness MUST be invalidated, and reconciliation MUST
// converge the presentation to Core truth.
//
// Loopback-only, controlled; never touches a device, deployed service, or
// non-loopback network endpoint. Run with TMPDIR=/var/tmp when the host
// tmpfs is full (Chrome profile allocation).
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
const copy = (value) => JSON.parse(JSON.stringify(value));
const song = (id) => ({
  song_id: id,
  title: id === "song-a" ? "Song A" : "Song B",
  filename: `${id}.mid`,
  duration_seconds: id === "song-a" ? 60 : 80,
  midi_format: 1,
  track_count: 2,
  status: "READY",
});
const snap = (state, id = "song-a", position = 10, duration = 60) => ({
  available: true,
  state,
  song_id: state === "IDLE" ? null : id,
  title: state === "IDLE" ? null : song(id).title,
  position_seconds: position,
  duration_seconds: duration,
  // Core always publishes a real tempo number (engine.tempo_scale).
  tempo_multiplier: 1,
  events_sent: 0,
  error: null,
});
const GARBAGE = { available: true, state: "GARBAGE", position_seconds: 0, duration_seconds: 0 };
// Exact closed-adapter payload captured live from
// piano_core.api.serialization.playback_session_payload(manager.snapshot())
// after MidiOutputAdapter.close(): legitimate authoritative
// playback-unavailable truth with real last-session values.
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
const learningIdle = () => ({
  available: true,
  state: "IDLE",
  song_id: null,
  title: null,
  step_index: null,
  total_steps: null,
  completed_steps: 0,
  incorrect_attempts: 0,
  progress: 0,
  target: null,
  session_id: null,
  mode: "WAIT_FOR_NOTE",
  section: null,
  accepted_notes: [],
  latest_outcome: "NONE",
  repetition: { current: 0, required: 1, successful: 0, failed: 0 },
  generation: 0,
  version: 0,
  midi_connection: "CONNECTED",
  error: null,
});
const learningWaiting = (overrides = {}) => ({
  ...learningIdle(),
  state: "WAITING",
  song_id: "song-a",
  title: "Song A",
  step_index: 0,
  total_steps: 2,
  target: { target_id: "target:0.000000000:60-64", notes: [60, 64], names: ["C4", "E4"], chord: true, onset_seconds: 0 },
  session_id: "learning-session-1",
  version: 1,
  generation: 1,
  ...overrides,
});

// ---- controlled Core stub ------------------------------------------------

const core = {
  playback: snap("PAUSED"),
  // How the NEXT mutating command answers (the command is ALWAYS applied
  // to core truth first — outcome provability is what varies).
  commandMode: "ok",
  counts: {},
  holdPlayback: false,
  pendingPlayback: [],
  getQueue: [],
  learning: learningIdle(),
};

function resetCore(playback) {
  core.playback = copy(playback);
  core.commandMode = "ok";
  core.counts = {};
  core.holdPlayback = false;
  core.pendingPlayback = [];
  core.getQueue = [];
  core.learning = learningIdle();
}

function json(res, value, status = 200) {
  const body = JSON.stringify(value);
  res.writeHead(status, {
    "content-type": "application/json",
    "content-length": Buffer.byteLength(body),
    "cache-control": "no-store",
  });
  res.end(body);
}

function health(playback) {
  return {
    application: { state: "READY" },
    midi_input: { connection: "CONNECTED" },
    library: { ready: true, entries: 2 },
    playback,
  };
}

async function sendPartialThenDestroy(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "content-type": "application/json",
    // The headers promise the FULL body; the socket dies before completing.
    "content-length": Buffer.byteLength(body),
    "cache-control": "no-store",
  });
  res.flushHeaders();
  res.write(body.slice(0, Math.max(1, Math.floor(body.length / 3))));
  await sleep(20);
  res.socket.destroy();
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://127.0.0.1");
  const key = `${req.method} ${url.pathname}`;
  core.counts[key] = (core.counts[key] || 0) + 1;
  if (url.pathname === "/" || url.pathname === "/ui") {
    res.writeHead(302, { location: "/ui/" });
    res.end();
    return;
  }
  if (url.pathname.startsWith("/ui/")) {
    const name = url.pathname.slice(4) || "index.html";
    const path = join(STATIC, name);
    if (!path.startsWith(STATIC)) { res.writeHead(404).end(); return; }
    try {
      const body = await readFile(path);
      const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".webmanifest": "application/manifest+json" };
      res.writeHead(200, { "content-type": types[extname(path)] || "application/octet-stream", "cache-control": "no-store" });
      res.end(body);
    } catch { res.writeHead(404).end(); }
    return;
  }
  if (url.pathname === "/api/v1/health") {
    json(res, health(copy(core.playback)));
    return;
  }
  if (url.pathname === "/api/v1/state") {
    json(res, { midi_connection: "CONNECTED", sustain: { pressed: false, value: 0 }, pressed_notes: [], last_input: null });
    return;
  }
  if (url.pathname === "/api/v1/live-notes") {
    json(res, {
      schema_version: 1,
      runtime_epoch: "chromium-u3r3",
      sequence: 1,
      physical: { notes: [], midi_connection: "CONNECTED" },
      playback: { notes: [] },
    });
    return;
  }
  if (url.pathname === "/api/v1/live-notes/stream") {
    res.writeHead(200, { "content-type": "text/event-stream" });
    res.write(": silent stream; HTTP recovery covers the snapshot\n\n");
    return;
  }
  if (url.pathname === "/api/v1/led") {
    json(res, { configured: false, available: false, enabled: false, mode: null, brightness: 100 });
    return;
  }
  if (url.pathname === "/api/v1/practice") {
    json(res, { available: true, state: "IDLE" });
    return;
  }
  if (url.pathname === "/api/v1/learning" && req.method === "GET") {
    json(res, copy(core.learning));
    return;
  }
  if (url.pathname.startsWith("/api/v1/learning/") && req.method === "POST") {
    let body = "";
    for await (const chunk of req) body += chunk;
    const parsed = body ? JSON.parse(body) : {};
    const action = url.pathname.split("/").pop();
    if (action === "start") {
      core.learning = learningWaiting({
        mode: parsed.mode || "WAIT_FOR_NOTE",
        section: parsed.section || null,
        repetition: {
          current: 0,
          required: parsed.required_repetitions || 1,
          successful: 0,
          failed: 0,
        },
      });
    } else if (action === "restart-target") {
      core.learning = { ...core.learning, version: core.learning.version + 1, latest_outcome: "NONE", accepted_notes: [] };
    } else if (action === "restart-section") {
      core.learning = { ...core.learning, version: core.learning.version + 1, step_index: 0, completed_steps: 0, progress: 0, latest_outcome: "NONE" };
    } else if (action === "stop") {
      core.learning = learningIdle();
    }
    json(res, copy(core.learning));
    return;
  }
  if (url.pathname === "/api/v1/library/songs") {
    json(res, { songs: [song("song-a"), song("song-b")], count: 2, total: 2 });
    return;
  }
  if (url.pathname === "/api/v1/library/songs/song-a") {
    json(res, song("song-a"));
    return;
  }
  if (url.pathname === "/api/v1/library/songs/song-b") {
    json(res, song("song-b"));
    return;
  }
  if (url.pathname === "/api/v1/playback" && req.method === "GET") {
    const queued = core.getQueue.shift();
    const payload = queued !== undefined ? queued : copy(core.playback);
    if (core.holdPlayback) core.pendingPlayback.push([res, payload]);
    else json(res, payload);
    return;
  }
  if (url.pathname.startsWith("/api/v1/playback/") && req.method === "POST") {
    const action = url.pathname.split("/").pop();
    let body = "";
    for await (const chunk of req) body += chunk;
    // The server APPLIES the command first (Core may have applied it even
    // when the response cannot prove the outcome).
    if (action === "pause") core.playback = { ...core.playback, state: "PAUSED", position_seconds: 10 };
    else if (action === "resume") core.playback = { ...core.playback, state: "PLAYING", position_seconds: 11 };
    else if (action === "stop") core.playback = snap("IDLE", null, 0, 0);
    const mode = core.commandMode;
    if (mode === "truncate") { await sendPartialThenDestroy(res, 200, core.playback); return; }
    if (mode === "typed409Truncate") {
      await sendPartialThenDestroy(res, 409, { error: { code: "invalid_state_transition", message: "controlled rejection" } });
      return;
    }
    if (mode === "typed409") {
      json(res, { error: { code: "invalid_state_transition", message: "controlled rejection" } }, 409);
      return;
    }
    if (mode === "malformed") {
      res.writeHead(200, { "content-type": "application/json", "content-length": 13, "cache-control": "no-store" });
      res.end("{invalid-json");
      return;
    }
    if (mode === "garbage") { json(res, GARBAGE); return; }
    json(res, copy(core.playback));
    return;
  }
  json(res, { error: { code: "not_found", message: "not found" } }, 404);
});

await new Promise((resolveListen) => server.listen(0, "127.0.0.1", resolveListen));
const appPort = server.address().port;

const probe = createServer();
await new Promise((resolveListen) => probe.listen(0, "127.0.0.1", resolveListen));
const debugPort = probe.address().port;
await new Promise((resolveClose) => probe.close(resolveClose));
const profile = await mkdtemp(join(tmpdir(), "piano-ui003-chrome-"));
const chrome = spawn(CHROME, [
  "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
  `--remote-debugging-port=${debugPort}`, `--user-data-dir=${profile}`,
  `http://127.0.0.1:${appPort}/ui/#/learn`,
], { stdio: ["ignore", "ignore", "pipe"] });

async function fetchJson(url, options) {
  for (let i = 0; i < 100; i += 1) {
    try { const response = await fetch(url, options); return await response.json(); }
    catch { await sleep(50); }
  }
  throw new Error(`Chrome debug endpoint unavailable: ${url}`);
}

const targets = await fetchJson(`http://127.0.0.1:${debugPort}/json/list`);
const target = targets.find((item) => item.type === "page");
assert.ok(target, "Chrome page target missing");
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolveOpen, rejectOpen) => { ws.once("open", resolveOpen); ws.once("error", rejectOpen); });
let cdpId = 0;
const pending = new Map();
ws.on("message", (raw) => {
  const message = JSON.parse(raw);
  if (message.method === "Runtime.exceptionThrown") {
    const details = message.params.exceptionDetails;
    pageErrors.push(details.exception?.description || details.text || "unknown page exception");
    return;
  }
  if (!message.id || !pending.has(message.id)) return;
  const { resolve: ok, reject } = pending.get(message.id);
  pending.delete(message.id);
  if (message.error) reject(new Error(message.error.message)); else ok(message.result);
});
function cdp(method, params = {}) {
  return new Promise((ok, reject) => {
    const id = ++cdpId;
    pending.set(id, { resolve: ok, reject });
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
async function waitFor(expression, timeout = 8000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (await evaluate(expression)) return;
    await sleep(40);
  }
  console.error("TIMEOUT DIAGNOSTICS", { pageErrors, state: await evaluate(`JSON.stringify({ready:document.readyState, shell:document.documentElement.dataset.ui008ShellReady, connection:document.querySelector('#connection-note')?.textContent, selection:window.__pianoUI?.selection(),keyboard:document.querySelector('#live-keyboard-status')?.textContent})`) });
  throw new Error(`Timed out waiting for: ${expression}`);
}
async function waitForServer(predicate, label, timeout = 8000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (predicate()) return;
    await sleep(25);
  }
  throw new Error(`Timed out waiting for server state: ${label}`);
}
async function removeChromeProfile(path) {
  let lastError = null;
  for (let attempt = 0; attempt < 20; attempt += 1) {
    try {
      await rm(path, { recursive: true, force: true, maxRetries: 2, retryDelay: 50 });
      return;
    } catch (error) {
      lastError = error;
      await sleep(100);
    }
  }
  throw lastError;
}
async function reload() {
  await cdp("Page.reload", { ignoreCache: true });
  await waitFor("document.readyState === 'complete' && document.querySelector('#connection-note')?.textContent === 'Connected'");
}
function releasePlayback() {
  const held = core.pendingPlayback.shift();
  assert.ok(held, "expected held playback GET response");
  const [res, payload] = held;
  json(res, payload);
}
function releasePlaybackWith(payload) {
  const held = core.pendingPlayback.shift();
  assert.ok(held, "expected held playback GET response");
  const [res] = held;
  json(res, payload);
}

await cdp("Runtime.enable");
await cdp("Page.enable");

// Wait for the real served app to boot before touching the module system.
await waitFor("document.readyState === 'complete' && document.querySelector('#connection-note')?.textContent === 'Connected'");

// Import the REAL served module in the page and keep handles for direct use.
await evaluate(
  `(async () => { globalThis.u3r3 = await import('http://127.0.0.1:${appPort}/ui/app.js'); return 'imported'; })()`
);

await test("REQUIRED: real Chromium terminated 200 body classifies as transport network_error, not invalid_response", async () => {
  resetCore(snap("PLAYING"));
  core.commandMode = "truncate"; // server applies PAUSE, then the body dies
  const outcome = await evaluate(`(async () => {
    const { ApiClient } = globalThis.u3r3;
    const client = new ApiClient(null, 2000);
    try {
      await client.pause();
      return { rejected: false };
    } catch (error) {
      return {
        name: error.name,
        status: error.status,
        code: error.code,
        phase: error.phase || null,
        message: String(error.message || ""),
        causeMessage: error.cause ? String(error.cause.message || error.cause) : null,
      };
    }
  })()`);
  assert.equal(outcome.code, "network_error", `classification was ${JSON.stringify(outcome)}`);
  assert.equal(outcome.status, 0);
  assert.equal(outcome.phase, "body");
  assert.notEqual(outcome.code, "invalid_response");
  // The classification never depended on the Chromium message text
  // ("Failed to fetch"): the phase marker is the single input.
  await waitForServer(() => core.playback.state === "PAUSED", "Core applied the PAUSE");
});

await test("real Chromium complete malformed 200 body remains invalid_response", async () => {
  core.commandMode = "malformed";
  const outcome = await evaluate(`(async () => {
    const { ApiClient } = globalThis.u3r3;
    const client = new ApiClient(null, 2000);
    try {
      await client.pause();
      return { rejected: false };
    } catch (error) {
      return { status: error.status, code: error.code, phase: error.phase || null };
    }
  })()`);
  assert.deepEqual(outcome, { status: 200, code: "invalid_response", phase: "classify" });
});

await test("real Chromium complete unusable success JSON parses but fails usability validation", async () => {
  core.commandMode = "garbage";
  const outcome = await evaluate(`(async () => {
    const { ApiClient, isUsablePlaybackSnapshot } = globalThis.u3r3;
    const client = new ApiClient(null, 2000);
    const payload = await client.pause();
    return { payload, usable: isUsablePlaybackSnapshot(payload) };
  })()`);
  assert.deepEqual(outcome.payload, GARBAGE);
  assert.equal(outcome.usable, false);
});

await test("real Chromium typed 409 rejection remains distinct from transport and parse failures", async () => {
  core.commandMode = "typed409";
  const outcome = await evaluate(`(async () => {
    const { ApiClient } = globalThis.u3r3;
    const client = new ApiClient(null, 2000);
    try {
      await client.pause();
      return { rejected: false };
    } catch (error) {
      return { status: error.status, code: error.code, phase: error.phase || null };
    }
  })()`);
  assert.deepEqual(outcome, { status: 409, code: "invalid_state_transition", phase: "classify" });
});

await test("REQUIRED: real Chromium PlayerCoordinator PAUSE with terminated body -> UNKNOWN -> reconciled PAUSED", async () => {
  resetCore(snap("PLAYING"));
  await reload();
  await waitFor("document.querySelector('#player-state').textContent === 'Playing'");
  core.commandMode = "truncate"; // Core applies PAUSE, response body dies
  core.holdPlayback = true; // hold the command reconciliation GET
  await evaluate("document.querySelector('#btn-pause').click()");
  await waitFor("document.querySelector('#player-freshness').textContent.includes('outcome unknown')");
  assert.equal(
    await evaluate("[...document.querySelectorAll('#btn-play,#btn-pause,#btn-resume,#btn-stop')].every(x => x.disabled)"),
    true,
    "controls inhibited during outcome-unknown"
  );
  const frozen = await evaluate("document.querySelector('#player-progress-fill').style.transform");
  await sleep(250);
  assert.equal(
    await evaluate("document.querySelector('#player-freshness').textContent.includes('outcome unknown')"),
    true
  );
  assert.equal(
    await evaluate("document.querySelector('#player-progress-fill').style.transform"),
    frozen,
    "presentation stays frozen during uncertainty"
  );
  // Reconciliation GET 1: valid JSON, unusable payload -> must NOT clear.
  core.commandMode = "ok";
  releasePlaybackWith(GARBAGE);
  await sleep(300);
  assert.equal(
    await evaluate("document.querySelector('#player-freshness').textContent.includes('outcome unknown')"),
    true,
    "a bad reconciliation GET cannot clear outcome-unknown"
  );
  assert.equal(
    await evaluate("document.querySelector('#player-state').textContent"),
    "Core unavailable",
    "no unusable state becomes authoritative"
  );
  // Reconciliation GET 2: valid authoritative PAUSED -> accepted.
  core.playback = snap("PAUSED");
  core.holdPlayback = false;
  await waitFor("document.querySelector('#player-freshness').textContent === 'Synced with Core'", 6000);
  assert.equal(await evaluate("document.querySelector('#player-state').textContent"), "Paused");
  assert.equal(await evaluate("document.querySelector('#btn-resume').disabled"), false);
  assert.equal(core.playback.state, "PAUSED");
});

await test("real Chromium normal poll with unusable playback payload never renders GARBAGE authority", async () => {
  resetCore(snap("PAUSED"));
  await reload();
  await waitFor("document.querySelector('#player-state').textContent === 'Paused'");
  // The next health polls carry an unusable playback payload; the prior
  // authority stays until the independent freshness horizon expires.
  core.playback = GARBAGE;
  await waitFor("document.querySelector('#player-state').textContent === 'State stale'", 8000);
  assert.notEqual(
    await evaluate("document.querySelector('#pill-playback .pill-value').textContent"),
    "GARBAGE",
    "unusable poll payload never becomes the authoritative pill"
  );
  const enabled = await evaluate(
    "[...document.querySelectorAll('#btn-play,#btn-pause,#btn-resume,#btn-stop')].some(x => !x.disabled)"
  );
  assert.equal(enabled, false, "no transport control is offered from an unusable snapshot");
  core.playback = snap("PAUSED");
  await waitFor("document.querySelector('#player-state').textContent === 'Paused'", 6000);
});

await test("real Chromium valid-unavailable playback renders Unavailable with normal health rendering", async () => {
  // Legitimate authoritative unavailable truth (closed-adapter payload
  // captured live from the Core serializer) plus valid health data.
  resetCore(CORE_UNAVAILABLE);
  await reload();
  await waitFor("document.querySelector('#connection-note')?.textContent === 'Connected'");
  assert.equal(
    await evaluate("document.querySelector('#pill-playback .pill-value').textContent"),
    "unavailable",
    "playback pill shows honest unavailable truth"
  );
  assert.equal(await evaluate("document.querySelector('#player-state').textContent"), "Unavailable");
  assert.equal(
    await evaluate("document.querySelector('#player-freshness').textContent"),
    "Synced with Core",
    "unavailable truth is synced authority, not a stale error"
  );
  assert.equal(
    await evaluate("[...document.querySelectorAll('#btn-play,#btn-pause,#btn-resume,#btn-stop')].every(x => x.disabled)"),
    true,
    "transport stays disabled for unavailable playback"
  );
  assert.equal(await evaluate("document.querySelector('#pill-app .pill-value').textContent"), "READY");
  assert.equal(await evaluate("document.querySelector('#pill-library .pill-value').textContent"), "2");
  // Library listing populates through its debounced search path.
  await waitFor("document.querySelectorAll('.btn-song').length === 2", 6000);
  assert.equal(
    await evaluate("document.querySelector('#pill-playback .pill-value').textContent"),
    "unavailable",
    "playback stays honestly unavailable while other subsystems render"
  );
  // The page-level classifier distinguishes unavailable truth from invalid
  // (the module handle is re-imported after the reload above).
  assert.equal(
    await evaluate(`(async () => {
      const { classifyPlaybackPayload, PLAYBACK_PAYLOAD_CLASS, isUsablePlaybackSnapshot } =
        await import('http://127.0.0.1:${appPort}/ui/app.js');
      return classifyPlaybackPayload(${JSON.stringify(CORE_UNAVAILABLE)}) === PLAYBACK_PAYLOAD_CLASS.VALID_UNAVAILABLE &&
        isUsablePlaybackSnapshot(${JSON.stringify(CORE_UNAVAILABLE)}) === false;
    })()`),
    true
  );
  // A later available poll restores normal playback authority.
  core.playback = snap("PLAYING");
  await waitFor("document.querySelector('#player-state').textContent === 'Playing'", 6000);
});

await test("real Chromium zero-duration COMPLETED 0/0 renders Completed with fresh semantics", async () => {
  resetCore({ ...snap("COMPLETED", "song-a", 0, 0), position_seconds: 0, duration_seconds: 0 });
  await reload();
  await waitFor("document.querySelector('#player-state').textContent === 'Completed'");
  assert.equal(
    await evaluate("document.querySelector('#player-freshness').textContent"),
    "Synced with Core"
  );
  assert.equal(
    await evaluate("document.querySelector('#connection-note')?.textContent === 'Connected'"),
    true,
    "no Connecting regression for zero-duration completion"
  );
  // COMPLETED enables a new session only with an explicit selection
  // (accepted UI-002 contract); the canonical pill carries the state.
  assert.equal(
    await evaluate("document.querySelector('#pill-playback .pill-value').textContent"),
    "COMPLETED"
  );
  assert.equal(await evaluate("document.querySelector('#btn-pause').disabled"), true);
  assert.equal(await evaluate("document.querySelector('#btn-resume').disabled"), true);
  assert.equal(await evaluate("document.querySelector('#btn-stop').disabled"), true);
});

await test("Learning V2 packaged UI starts, advances, reloads, degrades and stops authoritatively", async () => {
  resetCore(snap("IDLE", null, 0, 0));
  await cdp("Emulation.setDeviceMetricsOverride", {
    width: 1024, height: 768, deviceScaleFactor: 1, mobile: false,
  });
  await reload();
  await waitFor("document.querySelectorAll('.btn-song').length === 2", 6000);
  await evaluate("document.querySelector('.btn-song').click()");
  await waitFor("document.querySelector('#selected').hidden === false");
  await evaluate(`(() => {
    const mode = document.querySelector('#learning-mode');
    mode.value = 'REPEAT_UNTIL_CORRECT';
    mode.dispatchEvent(new Event('change', { bubbles: true }));
    document.querySelector('#learning-repetitions').value = '3';
    document.querySelector('#btn-practice').click();
  })()`);
  await waitFor("document.querySelector('#practice').hidden === false && document.querySelector('#practice-target').textContent === 'C4 + E4'");
  assert.equal(await evaluate("document.querySelector('#learning-mode-active').textContent"), "Repeat until correct");
  assert.match(await evaluate("document.querySelector('#learning-repetition-active').textContent"), /0 \/ 3/);
  assert.equal(await evaluate("document.querySelector('[data-midi=\"60\"]').dataset.expected"), "true");
  assert.equal(await evaluate("document.querySelector('[data-midi=\"64\"]').dataset.expected"), "true");

  // Simulate the Core-owned progression; the browser only consumes the next snapshot.
  core.learning = learningWaiting({
    mode: "REPEAT_UNTIL_CORRECT",
    step_index: 1,
    completed_steps: 1,
    progress: 0.5,
    target: { target_id: "target:1.000000000:67", notes: [67], names: ["G4"], chord: false, onset_seconds: 1 },
    latest_outcome: "CORRECT",
    repetition: { current: 0, required: 3, successful: 3, failed: 0 },
    version: 5,
  });
  await waitFor("document.querySelector('#practice-target').textContent === 'G4'", 6000);
  assert.equal(await evaluate("document.querySelector('[data-midi=\"60\"]').dataset.expected"), "false");
  assert.equal(await evaluate("document.querySelector('[data-midi=\"67\"]').dataset.expected"), "true");
  assert.equal(await evaluate("document.querySelector('#learning-feedback').textContent"), "Correct");

  // Portrait reload must reconstruct the complete Core session without client history.
  await cdp("Emulation.setDeviceMetricsOverride", {
    width: 768, height: 1024, deviceScaleFactor: 2, mobile: true,
  });
  await reload();
  await waitFor("document.querySelector('#practice-target').textContent === 'G4'", 6000);
  assert.equal(await evaluate("document.querySelector('[data-midi=\"67\"]').dataset.expected"), "true");
  const overflow = await evaluate(`(() => {
    const card = document.querySelector('#practice').getBoundingClientRect();
    return card.right > document.documentElement.clientWidth + 1;
  })()`);
  assert.equal(overflow, false, "learning card stays inside portrait viewport");

  core.learning = { ...core.learning, midi_connection: "DISCONNECTED", latest_outcome: "INPUT_UNAVAILABLE", version: 6 };
  await waitFor("document.querySelector('#practice-note').textContent.includes('cannot progress')", 6000);
  assert.match(await evaluate("document.querySelector('#learning-feedback').textContent"), /Input unavailable/);

  await evaluate("document.querySelector('#btn-learning-restart-target').click()");
  await waitForServer(() => core.learning.version === 7, "guarded target restart");
  await waitFor("document.querySelector('#learning-feedback').textContent === '—'", 6000);
  await evaluate("document.querySelector('#btn-practice-stop').click()");
  await waitForServer(() => core.learning.state === "IDLE", "guarded learning stop");
  await waitFor("document.querySelector('#practice').hidden === true", 6000);
  assert.equal(await evaluate("document.querySelector('[data-midi=\"67\"]').dataset.expected"), "false");
});

ws.close();
chrome.kill("SIGTERM");
await new Promise((resolveExit) => chrome.once("exit", resolveExit));
await new Promise((resolveClose) => server.close(resolveClose));
await removeChromeProfile(profile);

if (failures > 0) {
  console.error(`${failures} browser command-outcome test(s) failed`);
  process.exitCode = 1;
} else {
  console.log("ALL REAL CHROMIUM COMMAND-OUTCOME TESTS PASSED");
}
