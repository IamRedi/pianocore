// Optional real-Chromium reproduction for PIANO-UI-002 findings F1-F6.
// Run directly; it starts a loopback-only controlled Core stub and never
// touches a device, deployed service, or non-loopback network endpoint.
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

const sleep = (ms) => new Promise((resolveSleep) => setTimeout(resolveSleep, ms));
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
  song_id: id,
  title: id ? song(id).title : null,
  position_seconds: position,
  duration_seconds: duration,
  tempo_multiplier: 1,
});

const core = {
  playback: snap("PAUSED"),
  counts: {},
  healthSeen: 0,
  holdHealthAfter: Infinity,
  pendingHealth: [],
  holdSongA: false,
  pendingSongA: [],
  failAction: null,
  holdPlayback: false,
  pendingPlayback: [],
  lastPlaySong: null,
};

function discardPendingResponses() {
  const discard = (response) => {
    if (!response.writableEnded) {
      json(response, { error: { code: "test_reset", message: "controlled test reset" } }, 503);
    }
  };
  for (const [response] of core.pendingHealth) discard(response);
  for (const response of core.pendingSongA) discard(response);
  for (const [response] of core.pendingPlayback) discard(response);
}

function resetCore(playback) {
  discardPendingResponses();
  core.playback = copy(playback);
  core.counts = {};
  core.healthSeen = 0;
  core.holdHealthAfter = Infinity;
  core.pendingHealth = [];
  core.holdSongA = false;
  core.pendingSongA = [];
  core.failAction = null;
  core.holdPlayback = false;
  core.pendingPlayback = [];
  core.lastPlaySong = null;
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
    core.healthSeen += 1;
    const captured = health(copy(core.playback));
    if (core.healthSeen > core.holdHealthAfter) core.pendingHealth.push([res, captured]);
    else json(res, captured);
    return;
  }
  if (url.pathname === "/api/v1/state") {
    json(res, { midi_connection: "CONNECTED", sustain: { pressed: false, value: 0 }, pressed_notes: [], last_input: null });
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
  if (url.pathname === "/api/v1/library/songs") {
    json(res, { songs: [song("song-a"), song("song-b")], count: 2, total: 2 });
    return;
  }
  if (url.pathname === "/api/v1/library/songs/song-a") {
    if (core.holdSongA) core.pendingSongA.push(res);
    else json(res, song("song-a"));
    return;
  }
  if (url.pathname === "/api/v1/library/songs/song-b") {
    json(res, song("song-b"));
    return;
  }
  if (url.pathname === "/api/v1/playback" && req.method === "GET") {
    const captured = copy(core.playback);
    if (core.holdPlayback) core.pendingPlayback.push([res, captured]);
    else json(res, captured);
    return;
  }
  if (url.pathname.startsWith("/api/v1/playback/") && req.method === "POST") {
    const action = url.pathname.split("/").pop();
    let body = "";
    for await (const chunk of req) body += chunk;
    if (core.failAction === action) {
      req.socket.destroy();
      return;
    }
    if (action === "play") {
      const payload = JSON.parse(body || "{}");
      core.lastPlaySong = payload.song_id;
      core.playback = snap("PLAYING", payload.song_id, 0, payload.song_id === "song-b" ? 80 : 60);
    } else if (action === "pause") core.playback = { ...core.playback, state: "PAUSED" };
    else if (action === "resume") core.playback = { ...core.playback, state: "PLAYING" };
    else if (action === "stop") core.playback = snap("IDLE", null, 0, 0);
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
const profile = await mkdtemp(join(tmpdir(), "piano-ui002-chrome-"));
const chrome = spawn(CHROME, [
  "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
  "--disable-extensions", "--disable-background-networking",
  "--disable-component-update", "--renderer-process-limit=1",
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
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
  return result.result.value;
}
async function waitFor(expression, timeout = 8000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (await evaluate(expression)) return;
    await sleep(40);
  }
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
  try {
    await waitFor("document.readyState === 'complete' && document.querySelector('#connection-note')?.textContent === 'Connected'");
  } catch (error) {
    const browserState = await evaluate(`(() => ({
      readyState: document.readyState,
      connection: document.querySelector('#connection-note')?.textContent,
      playerState: document.querySelector('#player-state')?.textContent,
      freshness: document.querySelector('#player-freshness')?.textContent
    }))()`);
    console.error(`RELOAD_DIAGNOSTIC ${JSON.stringify({
      browserState,
      counts: core.counts,
      healthSeen: core.healthSeen,
      pendingHealth: core.pendingHealth.length,
      pendingSongA: core.pendingSongA.length,
      pendingPlayback: core.pendingPlayback.length,
    })}`);
    throw error;
  }
}
function releaseHealth() {
  const pendingResponse = core.pendingHealth.shift();
  assert.ok(pendingResponse, "expected held health response");
  json(...pendingResponse);
}
function failHealth(status = 503) {
  const pendingResponse = core.pendingHealth.shift();
  assert.ok(pendingResponse, "expected held health response");
  json(
    pendingResponse[0],
    { error: { code: "not_ready", message: "controlled health failure" } },
    status
  );
}
function releasePlayback() {
  const pendingResponse = core.pendingPlayback.shift();
  assert.ok(pendingResponse, "expected held playback response");
  json(...pendingResponse);
}
function releaseSongA() {
  const res = core.pendingSongA.shift();
  assert.ok(res, "expected held song-a metadata response");
  json(res, song("song-a"));
}

await cdp("Runtime.enable");
await cdp("Page.enable");

await test("browser F1 freshness expires in PAUSED without animation frames", async () => {
  resetCore(snap("PAUSED"));
  core.holdHealthAfter = 1;
  await reload();
  await waitFor("document.querySelector('#player-state').textContent === 'Paused'");
  await waitFor("document.querySelector('#player-state').textContent === 'State stale'", 5000);
  assert.equal(await evaluate("[...document.querySelectorAll('#btn-play,#btn-pause,#btn-resume,#btn-stop')].every(x => x.disabled)"), true);
});

await test("browser F2 delayed pre-command poll cannot overwrite Pause", async () => {
  resetCore(snap("PLAYING"));
  core.holdHealthAfter = 1;
  await reload();
  await waitFor("document.querySelector('#btn-pause').disabled === false");
  await waitFor("globalThis.__never || false", 20).catch(() => {});
  await waitForServer(() => core.pendingHealth.length > 0, "held pre-command health poll");
  await evaluate("document.querySelector('#btn-pause').click()");
  await waitFor("document.querySelector('#player-state').textContent === 'Paused'");
  releaseHealth();
  await sleep(150);
  assert.equal(await evaluate("document.querySelector('#player-state').textContent"), "Paused");
});

await test("browser RR-1 obsolete poll failure cannot invalidate reconciled Pause", async () => {
  resetCore(snap("PLAYING"));
  core.holdHealthAfter = 1;
  await reload();
  await waitFor("document.querySelector('#btn-pause').disabled === false");
  await waitForServer(() => core.pendingHealth.length > 0, "obsolete pre-Pause health poll");
  await evaluate("document.querySelector('#btn-pause').click()");
  await waitFor(
    "document.querySelector('#player-state').textContent === 'Paused' && " +
    "document.querySelector('#player-freshness').textContent === 'Synced with Core' && " +
    "document.querySelector('#btn-resume').disabled === false"
  );
  failHealth();
  await sleep(200);
  assert.equal(await evaluate("document.querySelector('#player-state').textContent"), "Paused");
  assert.equal(await evaluate("document.querySelector('#player-freshness').textContent"), "Synced with Core");
  assert.equal(await evaluate("document.querySelector('#btn-resume').disabled"), false);
});

await test("browser RR-1 current poll failure still enters recovery", async () => {
  resetCore(snap("PAUSED"));
  core.holdHealthAfter = 1;
  await reload();
  await waitForServer(() => core.pendingHealth.length > 0, "current health failure");
  failHealth();
  await waitFor("document.querySelector('#player-state').textContent === 'Core unavailable'");
  assert.equal(await evaluate("document.querySelector('#player-freshness').textContent.includes('position frozen')"), true);
  assert.equal(await evaluate("[...document.querySelectorAll('#btn-play,#btn-pause,#btn-resume,#btn-stop')].every(x => x.disabled)"), true);
  core.holdHealthAfter = Infinity;
  await waitFor("document.querySelector('#player-freshness').textContent === 'Synced with Core'", 3000);
});

await test("browser F3 terminal A hydration cannot replace explicit B", async () => {
  resetCore(snap("COMPLETED", "song-a", 60, 60));
  core.holdSongA = true;
  await reload();
  await waitFor("document.querySelectorAll('.btn-song').length === 2");
  await evaluate("document.querySelectorAll('.btn-song')[1].click()");
  await waitFor("document.querySelector('#sel-title').textContent === 'Song B'");
  assert.ok(core.pendingSongA.length > 0);
  core.holdSongA = false;
  for (const res of core.pendingSongA.splice(0)) json(res, song("song-a"));
  await sleep(100);
  assert.equal(await evaluate("document.querySelector('#sel-title').textContent"), "Song B");
  await evaluate("document.querySelector('#btn-play').click()");
  await waitFor("document.querySelector('#player-title').textContent === 'Song B'");
  assert.equal(core.lastPlaySong, "song-b");
});

for (const [label, initial] of [
  ["PAUSED", snap("PAUSED")],
  ["PLAYING", snap("PLAYING")],
  ["COMPLETED", snap("COMPLETED", "song-a", 60, 60)],
]) {
  await test(`browser RR-2 ${label} expiry survives late metadata`, async () => {
    resetCore(initial);
    core.holdSongA = true;
    core.holdHealthAfter = 1;
    await reload();
    await waitForServer(() => core.pendingSongA.length > 0, `${label} metadata hydration`);
    await waitFor("document.querySelector('#player-state').textContent === 'State stale'", 5000);
    const frozen = await evaluate("document.querySelector('#player-progress-fill').style.transform");
    releaseSongA();
    await waitFor("document.querySelector('#player-meta').textContent.includes('2 tracks')");
    await sleep(250);
    assert.equal(await evaluate("document.querySelector('#player-state').textContent"), "State stale");
    assert.notEqual(await evaluate("document.querySelector('#player-freshness').textContent"), "Synced with Core");
    assert.equal(await evaluate("document.querySelector('#player-progress-fill').style.transform"), frozen);
    assert.equal(await evaluate("[...document.querySelectorAll('#btn-play,#btn-pause,#btn-resume,#btn-stop')].every(x => x.disabled)"), true);
  });
}

for (const [action, initial] of [
  ["play", snap("COMPLETED", "song-a", 60, 60)],
  ["pause", snap("PLAYING")],
  ["resume", snap("PAUSED")],
  ["stop", snap("PLAYING")],
]) {
  await test(`browser RR-2/F4 ${action} uncertainty survives late metadata until reconciliation`, async () => {
    resetCore(initial);
    core.holdSongA = true;
    core.holdHealthAfter = 1;
    await reload();
    await waitForServer(() => core.pendingSongA.length > 0, `${action} delayed metadata`);
    if (action === "play") {
      await waitFor("document.querySelectorAll('.btn-song').length === 2");
      await evaluate("document.querySelectorAll('.btn-song')[1].click()");
    }
    await waitFor(`document.querySelector('#btn-${action}').disabled === false`);
    core.failAction = action;
    core.holdPlayback = true;
    await evaluate(`document.querySelector('#btn-${action}').click()`);
    await waitFor("document.querySelector('#player-freshness').textContent.includes('outcome unknown')");
    assert.equal(await evaluate("[...document.querySelectorAll('#btn-play,#btn-pause,#btn-resume,#btn-stop')].every(x => x.disabled)"), true);
    const frozen = await evaluate("document.querySelector('#player-progress-fill').style.transform");
    releaseSongA();
    await waitFor("document.querySelector('#player-meta').textContent.includes('2 tracks')");
    await sleep(250);
    assert.equal(await evaluate("document.querySelector('#player-state').textContent"), "Core unavailable");
    assert.equal(await evaluate("document.querySelector('#player-freshness').textContent.includes('outcome unknown')"), true);
    assert.equal(await evaluate("document.querySelector('#player-progress-fill').style.transform"), frozen);
    assert.equal(await evaluate("[...document.querySelectorAll('#btn-play,#btn-pause,#btn-resume,#btn-stop')].every(x => x.disabled)"), true);
    await waitForServer(() => core.pendingPlayback.length > 0, "held command reconciliation");
    core.failAction = null;
    core.holdPlayback = false;
    releasePlayback();
    await waitFor("document.querySelector('#player-freshness').textContent === 'Synced with Core'");
  });
}

await test("browser F5 small backward poll correction does not step progress backward", async () => {
  resetCore(snap("PLAYING", "song-a", 10, 60));
  core.holdHealthAfter = 1;
  await reload();
  await waitForServer(() => core.pendingHealth.length > 0, "held drift health poll");
  const before = await evaluate("Number(document.querySelector('#player-progress-fill').style.transform.match(/[0-9.]+/)[0])");
  const duration = core.pendingHealth[0][1].playback.duration_seconds;
  const beforePosition = before * duration;
  const incomingPosition = Math.max(0, beforePosition - 0.1);
  core.pendingHealth[0][1].playback.position_seconds = incomingPosition;
  assert.ok(incomingPosition < beforePosition, "fixture must be a backward correction");
  assert.ok(
    beforePosition - incomingPosition < 0.35,
    "fixture must stay inside the Player small-drift tolerance"
  );
  await evaluate(`(() => {
    const fill = document.querySelector('#player-progress-fill');
    globalThis.__f5Progress = {
      values: [${before}],
      observer: new MutationObserver(() => {
        const match = fill.style.transform.match(/[0-9.]+/);
        if (match) globalThis.__f5Progress.values.push(Number(match[0]));
      })
    };
    globalThis.__f5Progress.observer.observe(fill, {
      attributes: true,
      attributeFilter: ['style']
    });
  })()`);
  const healthSeenAtRelease = core.healthSeen;
  releaseHealth();
  await waitForServer(
    () => core.healthSeen > healthSeenAtRelease,
    "post-F5 poll proves the corrected response was applied"
  );
  const observed = await evaluate(`(() => {
    const trace = globalThis.__f5Progress;
    trace.observer.disconnect();
    const values = trace.values;
    delete globalThis.__f5Progress;
    return { minimum: Math.min(...values), after: values.at(-1), samples: values.length };
  })()`);
  console.log(`F5_DIAGNOSTIC ${JSON.stringify({
    expectedMinimum: before,
    incomingPosition,
    browserState: "PLAYING",
    healthSeenAtRelease,
    ...observed,
  })}`);
  assert.ok(
    observed.minimum >= before,
    `${observed.minimum} stepped behind ${before}`
  );
});

await test("browser F6 Space on summary preserves native disclosure ownership", async () => {
  resetCore(snap("PAUSED"));
  core.holdHealthAfter = 1;
  await reload();
  await evaluate("document.querySelector('summary').focus()");
  const beforeResume = core.counts["POST /api/v1/playback/resume"] || 0;
  await cdp("Input.dispatchKeyEvent", { type: "rawKeyDown", key: " ", code: "Space", windowsVirtualKeyCode: 32 });
  await cdp("Input.dispatchKeyEvent", { type: "keyUp", key: " ", code: "Space", windowsVirtualKeyCode: 32 });
  await waitFor("document.querySelector('details').open === true");
  assert.equal(core.counts["POST /api/v1/playback/resume"] || 0, beforeResume);
});

ws.close();
chrome.kill("SIGTERM");
await new Promise((resolveExit) => chrome.once("exit", resolveExit));
await new Promise((resolveClose) => server.close(resolveClose));
await removeChromeProfile(profile);

if (failures > 0) {
  console.error(`${failures} browser remediation test(s) failed`);
  process.exitCode = 1;
} else {
  console.log("ALL CHROMIUM REMEDIATION TESTS PASSED");
}
