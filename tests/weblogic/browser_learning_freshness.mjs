// PIANO-UI-004 U4-F3: REAL Chromium/HTTP Learning ordering + freshness.
//
// Independent-review reproductions, retained as browser regressions:
//   1. A Learning GET started BEFORE an accepted Stop returns AFTER it —
//      the Learning presentation authority (request generation boundary)
//      must reject it, so the stopped session can never resurrect.
//   2. Repeated Learning API failures (500) must expire Learning
//      presentation within the bounded freshness window while general
//      Core health stays healthy — Player/Core/LiveNote are NOT marked
//      unavailable — and one valid snapshot must restore authority.
//
// Everything is real: real Google Chrome (packaged, headless), real
// loopback HTTP server applying each command, the real packaged app.js
// served over HTTP, the real browser fetch stack and the real
// LearningCoordinator wiring. Loopback-only, controlled; never touches
// a device, deployed service, or non-loopback endpoint.
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
const copy = (value) => JSON.parse(JSON.stringify(value));
const song = (id) => ({
  song_id: id,
  title: "Song A",
  filename: `${id}.mid`,
  duration_seconds: 60,
  midi_format: 1,
  track_count: 2,
  status: "READY",
});
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
  target: {
    target_id: "target:0:60-64",
    notes: [60, 64],
    names: ["C4", "E4"],
    chord: true,
    onset_seconds: 0,
  },
  session_id: "learning-session-1",
  version: 1,
  generation: 1,
  ...overrides,
});

// ---- controlled Core stub ------------------------------------------------

const core = {
  learning: learningIdle(),
  counts: {},
  // When true, GET /api/v1/learning answers 500 (repeated failure).
  failLearning: false,
  // When > 0, GET /api/v1/learning captures Core truth at REQUEST time
  // and answers that (old) payload after the delay — an in-flight poll
  // racing a command, exactly the resurrect window.
  delayLearningMs: 0,
  delayedServed: 0,
};

function resetCore() {
  core.learning = learningIdle();
  core.counts = {};
  core.failLearning = false;
  core.delayLearningMs = 0;
  core.delayedServed = 0;
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

function health() {
  return {
    application: { state: "READY" },
    midi_input: { connection: "CONNECTED" },
    library: { ready: true, entries: 2 },
    playback: { available: true, state: "PLAYING", song_id: "song-a", title: "Song A", position_seconds: 1, duration_seconds: 60, tempo_multiplier: 1, events_sent: 0, error: null },
  };
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://127.0.0.1");
  const key = `${req.method} ${url.pathname}`;
  core.counts[key] = (core.counts[key] || 0) + 1;
  if (url.pathname === "/") {
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
    json(res, health());
    return;
  }
  if (url.pathname === "/api/v1/state") {
    json(res, { midi_connection: "CONNECTED", sustain: { pressed: false, value: 0 }, pressed_notes: [], last_input: null });
    return;
  }
  if (url.pathname === "/api/v1/live-notes") {
    json(res, {
      schema_version: 1,
      runtime_epoch: "ui004-u4f3",
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
    if (core.failLearning) {
      json(res, { error: { code: "internal_error", message: "controlled learning failure" } }, 500);
      return;
    }
    if (core.delayLearningMs > 0) {
      // Capture the PRE-COMMAND truth now; answer it after the delay.
      const captured = copy(core.learning);
      const response = res;
      setTimeout(() => {
        core.delayedServed += 1;
        json(response, captured);
      }, core.delayLearningMs);
      return;
    }
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
        version: 1,
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
    json(res, { songs: [song("song-a")], count: 1, total: 1 });
    return;
  }
  if (url.pathname === "/api/v1/library/songs/song-a") {
    json(res, song("song-a"));
    return;
  }
  if (url.pathname === "/api/v1/playback" && req.method === "GET") {
    json(res, health().playback);
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
const profile = await mkdtemp(join(tmpdir(), "piano-ui004-learning-"));
const chrome = spawn(CHROME, [
  "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
  `--remote-debugging-port=${debugPort}`, `--user-data-dir=${profile}`,
  `http://127.0.0.1:${appPort}/ui/`,
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
async function waitFor(expression, timeout = 12000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (await evaluate(expression)) return;
    await sleep(40);
  }
  throw new Error(`Timed out waiting for: ${expression}`);
}
async function waitForServer(predicate, label, timeout = 12000) {
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

await cdp("Runtime.enable");
await cdp("Page.enable");

// Wait for the real served app to boot before touching the module system.
await waitFor("document.readyState === 'complete' && document.querySelector('#connection-note')?.textContent === 'Connected'");

await test("REQUIRED: pre-command Learning GET returning after Stop cannot resurrect the session", async () => {
  resetCore();
  // The page reaches Learning WAITING truth through a normal poll.
  core.learning = learningWaiting();
  await waitFor("document.querySelector('#practice-status')?.textContent === 'Waiting — play the lit key(s) on the piano'");
  await waitFor("document.querySelector('#btn-practice-stop')?.disabled === false");
  assert.equal(await evaluate("document.querySelector('[data-midi=\"60\"]').dataset.expected"), "true");

  // The NEXT poll's Learning GET is delayed; it captures WAITING truth
  // at request time. The Stop command is applied while it is in flight.
  core.delayLearningMs = 1500;
  const before = core.counts["GET /api/v1/learning"] || 0;
  await waitForServer(
    () => (core.counts["GET /api/v1/learning"] || 0) > before,
    "delayed Learning GET in flight"
  );
  await evaluate("document.querySelector('#btn-practice-stop').click()");
  await waitFor("document.querySelector('#practice').hidden === true", 6000);
  await waitForServer(() => core.delayedServed > 0, "delayed pre-command Learning GET answered");
  core.delayLearningMs = 0; // no more delays: later polls answer immediately
  // Give any (rejected) application a poll cycle to show up.
  await sleep(1300);
  // The stopped session must NOT have resurrected.
  assert.equal(await evaluate("document.querySelector('#practice').hidden"), true);
  assert.equal(await evaluate("document.querySelector('[data-midi=\"60\"]').dataset.expected"), "false");
  assert.equal(await evaluate("document.querySelector('#btn-practice-stop').disabled"), true);
  assert.equal((core.counts["POST /api/v1/learning/stop"] || 0), 1);
});

await test("REQUIRED: repeated Learning 500 expires presentation bounded while Core stays healthy, then recovers", async () => {
  resetCore();
  core.learning = learningWaiting();
  // Wait for the FULLY applied state (panel visible + expected overlay
  // set), never for panel text alone: a hidden panel retains old text.
  await waitFor(
    "document.querySelector('#practice').hidden === false && " +
    "document.querySelector('#practice-status')?.textContent === 'Waiting — play the lit key(s) on the piano' && " +
    "document.querySelector('[data-midi=\"60\"]').dataset.expected === 'true' && " +
    "document.querySelector('[data-midi=\"64\"]').dataset.expected === 'true'"
  );

  // Learning API starts failing repeatedly; general Core stays healthy.
  core.failLearning = true;
  await waitFor(
    "document.querySelector('#practice-status')?.textContent === 'Learning state is stale — waiting for Piano Core'",
    12000
  );
  // Explicit stale status; no target, no expected overlay, no feedback.
  assert.equal(await evaluate("document.querySelector('#pill-practice .pill-value').textContent"), "stale");
  assert.equal(await evaluate("document.querySelector('#learning-feedback').textContent"), "—");
  assert.equal(await evaluate("document.querySelector('#practice-target').textContent"), "");
  assert.equal(await evaluate("document.querySelector('[data-midi=\"60\"]').dataset.expected"), "false");
  assert.equal(await evaluate("document.querySelector('[data-midi=\"64\"]').dataset.expected"), "false");
  // Learning mutating controls are disabled.
  assert.equal(await evaluate("document.querySelector('#btn-practice-stop').disabled"), true);
  assert.equal(await evaluate("document.querySelector('#btn-learning-restart-target').disabled"), true);
  assert.equal(await evaluate("document.querySelector('#btn-learning-restart-section').disabled"), true);
  // General Core health is NOT marked unavailable by Learning staleness.
  assert.equal(await evaluate("document.querySelector('#pill-app .pill-value').textContent"), "READY");
  assert.equal(await evaluate("document.querySelector('#pill-playback .pill-value').textContent"), "PLAYING");
  assert.equal(await evaluate("document.querySelector('#pill-midi .pill-value').textContent"), "CONNECTED");

  // Recovery: one valid Learning snapshot replaces the stale replica.
  core.failLearning = false;
  core.learning = learningWaiting({ version: 5, step_index: 1 });
  await waitFor(
    "document.querySelector('#practice').hidden === false && " +
    "document.querySelector('#practice-status')?.textContent === 'Waiting — play the lit key(s) on the piano' && " +
    "document.querySelector('#btn-practice-stop').disabled === false",
    8000
  );
  assert.equal(await evaluate("document.querySelector('#pill-practice .pill-value').textContent"), "waiting");
  assert.equal(await evaluate("document.querySelector('[data-midi=\"60\"]').dataset.expected"), "true");
  assert.equal(await evaluate("document.querySelector('[data-midi=\"64\"]').dataset.expected"), "true");
  assert.equal(await evaluate("document.querySelector('#btn-practice-stop').disabled"), false);
});

ws.close();
chrome.kill("SIGTERM");
await new Promise((resolveExit) => chrome.once("exit", resolveExit));
await new Promise((resolveClose) => server.close(resolveClose));
await removeChromeProfile(profile);

if (failures > 0) {
  console.error(`${failures} browser learning-freshness test(s) failed`);
  process.exitCode = 1;
} else {
  console.log("ALL REAL CHROMIUM LEARNING FRESHNESS TESTS PASSED");
}
