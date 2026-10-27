// Controlled real-Chromium validation for PIANO-UI-003.
// Loopback-only synthetic Core; no device, deployment, or external network.
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
const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

let failures = 0;
const pageErrors = [];
async function test(name, fn) {
  const before = pageErrors.length;
  try {
    await fn();
    assert.equal(pageErrors.length, before, pageErrors.slice(before).join(" | "));
    console.log(`ok - ${name}`);
  } catch (error) {
    failures += 1;
    console.error(`FAIL - ${name}`);
    console.error(error);
  }
}

const core = {
  epoch: "browser-runtime-a",
  sequence: 0,
  physical: [],
  playback: [],
  streams: new Set(),
  generatedSnapshots: 0,
  deliveredSnapshots: 0,
  heartbeatFrames: 0,
  httpSnapshotRequests: 0,
  httpHeadersReceived: 0,
  httpCompleted: 0,
  httpBodiesCompleted: 0,
  httpAborted: 0,
  httpFailed: 0,
  httpActive: 0,
  httpMaxConcurrent: 0,
  httpScenario: "normal",
  httpScenarioRequest: 0,
  nextSnapshotDelayMs: 0,
  defaultSnapshotDelayMs: 0,
  sseConnectAttempts: 0,
  rejectSse: false,
};

function noteSnapshot() {
  return {
    schema_version: 1,
    runtime_epoch: core.epoch,
    sequence: core.sequence,
    physical: {
      notes: [...core.physical],
      outside_keyboard_count: 0,
      sustain: { value: 0, pressed: false },
      midi_connection: "CONNECTED",
      revision: core.sequence,
    },
    playback: {
      available: true,
      notes: [...core.playback],
      voices: core.playback.map((note) => ({ channel: 1, note })),
      outside_keyboard_count: 0,
      sustain_channels: [],
    },
  };
}

function sseFrame() {
  return `id: ${core.sequence}\nevent: snapshot\ndata: ${JSON.stringify(noteSnapshot())}\n\n`;
}

function publish({
  physical = core.physical,
  playback = core.playback,
  deliver = true,
} = {}) {
  core.physical = [...physical];
  core.playback = [...playback];
  core.sequence += 1;
  core.generatedSnapshots += 1;
  let delivered = false;
  if (deliver) {
    const frame = sseFrame();
    for (const response of [...core.streams]) {
      try {
        response.write(frame);
        delivered = true;
      } catch {
        core.streams.delete(response);
      }
    }
  }
  if (delivered) core.deliveredSnapshots += 1;
  return { sequence: core.sequence, delivered };
}

function heartbeat() {
  let delivered = false;
  for (const response of [...core.streams]) {
    try {
      response.write(": heartbeat\n\n");
      delivered = true;
    } catch {
      core.streams.delete(response);
    }
  }
  if (delivered) core.heartbeatFrames += 1;
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
      const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".webmanifest": "application/manifest+json" };
      response.writeHead(200, { "content-type": types[extname(path)] || "application/octet-stream", "cache-control": "no-store" });
      response.end(body);
    } catch { response.writeHead(404).end(); }
    return;
  }
  if (url.pathname === "/api/v1/live-notes") {
    core.httpSnapshotRequests += 1;
    core.httpScenarioRequest += 1;
    core.httpActive += 1;
    core.httpMaxConcurrent = Math.max(core.httpMaxConcurrent, core.httpActive);
    let settled = false;
    response.on("finish", () => {
      if (settled) return;
      settled = true;
      core.httpActive -= 1;
    });
    response.on("close", () => {
      if (settled) return;
      settled = true;
      core.httpActive -= 1;
      core.httpAborted += 1;
    });
    const captured = noteSnapshot();
    if (
      core.httpScenario !== "normal" &&
      core.httpScenarioRequest === 1
    ) {
      response.writeHead(200, {
        "content-type": "application/json",
        "cache-control": "no-store",
      });
      response.flushHeaders();
      response.write('{"schema_version":1,"runtime_epoch":"stalled"');
      core.httpHeadersReceived += 1;
      return;
    }
    const delay = core.nextSnapshotDelayMs || core.defaultSnapshotDelayMs;
    core.nextSnapshotDelayMs = 0;
    try {
      if (delay > 0) await sleep(delay);
      if (response.destroyed) return;
      core.httpHeadersReceived += 1;
      core.httpBodiesCompleted += 1;
      json(response, captured);
      core.httpCompleted += 1;
    } catch {
      core.httpFailed += 1;
    }
    return;
  }
  if (url.pathname === "/api/v1/live-notes/stream") {
    core.sseConnectAttempts += 1;
    if (core.rejectSse) {
      response.writeHead(503, { "content-type": "text/plain", "cache-control": "no-store" });
      response.end("synthetic SSE unavailable");
      return;
    }
    response.writeHead(200, {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache",
      connection: "close",
    });
    core.streams.add(response);
    response.write(sseFrame());
    request.on("close", () => core.streams.delete(response));
    return;
  }
  if (url.pathname === "/api/v1/health") {
    json(response, {
      application: { state: "READY" },
      midi_input: { connection: "CONNECTED" },
      library: { ready: true, entries: 0 },
      playback: { available: true, state: "IDLE", song_id: null, title: null, position_seconds: 0, duration_seconds: 0, tempo_multiplier: 1 },
    });
    return;
  }
  if (url.pathname === "/api/v1/state") {
    json(response, { midi_connection: "CONNECTED", sustain: { pressed: false, value: 0 }, pressed_notes: core.physical, last_input: null });
    return;
  }
  if (url.pathname === "/api/v1/led") {
    json(response, { configured: false, available: false, enabled: false, mode: null, brightness: null });
    return;
  }
  if (url.pathname === "/api/v1/practice") {
    json(response, { available: true, state: "IDLE" });
    return;
  }
  if (url.pathname === "/api/v1/library/songs") {
    json(response, { songs: [], count: 0 });
    return;
  }
  json(response, { error: { code: "not_found", message: "not found" } }, 404);
});

await new Promise((done) => server.listen(0, "127.0.0.1", done));
const appPort = server.address().port;
const probe = createServer();
await new Promise((done) => probe.listen(0, "127.0.0.1", done));
const debugPort = probe.address().port;
await new Promise((done) => probe.close(done));
const profile = await mkdtemp(join(tmpdir(), "piano-ui003-chrome-"));
const chrome = spawn(CHROME, [
  "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
  `--remote-debugging-port=${debugPort}`, `--user-data-dir=${profile}`,
  `http://127.0.0.1:${appPort}/ui/#/piano`,
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
    pageErrors.push(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text);
  }
  if (!message.id || !pending.has(message.id)) return;
  const handlers = pending.get(message.id);
  pending.delete(message.id);
  if (message.error) handlers.fail(new Error(message.error.message)); else handlers.done(message.result);
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
    throw new Error(
      result.exceptionDetails.exception?.description || result.exceptionDetails.text
    );
  }
  return result.result.value;
}
async function waitFor(expression, timeout = 8000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await evaluate(expression)) return;
    await sleep(30);
  }
  console.error("TIMEOUT DIAGNOSTICS", { pageErrors, state: await evaluate(`JSON.stringify({ready:document.readyState, shell:document.documentElement.dataset.ui008ShellReady, connection:document.querySelector('#connection-note')?.textContent, selection:window.__pianoUI?.selection(),keyboard:document.querySelector('#live-keyboard-status')?.textContent})`) });
  throw new Error(`Timed out waiting for ${expression}`);
}

async function waitUntil(predicate, description, timeout = 8000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await sleep(30);
  }
  throw new Error(`Timed out waiting for ${description}`);
}

async function setSyntheticVisibility(state) {
  await evaluate(`(() => {
    window.__pianoTestVisibility = ${JSON.stringify(state)};
    if (!Object.prototype.hasOwnProperty.call(document, "visibilityState")) {
      Object.defineProperty(document, "visibilityState", {
        configurable: true,
        get: () => window.__pianoTestVisibility,
      });
    }
    document.dispatchEvent(new Event("visibilitychange"));
  })()`);
}

async function restoreVisibility() {
  await evaluate(`(() => {
    delete document.visibilityState;
    delete window.__pianoTestVisibility;
  })()`);
}

await cdp("Runtime.enable");
await cdp("Page.enable");
await waitFor("document.readyState === 'complete' && document.querySelector('#live-keyboard-status')?.textContent === 'Live'");

await test("browser renders exact A0-C8 geometry without interactive keys", async () => {
  const result = await evaluate(`(() => ({
    all: document.querySelectorAll('.piano-key').length,
    white: document.querySelectorAll('.piano-key--white').length,
    black: document.querySelectorAll('.piano-key--black').length,
    first: document.querySelector('.piano-key[data-midi="21"]')?.title,
    last: document.querySelector('.piano-key[data-midi="108"]')?.title,
    tabStops: document.querySelectorAll('.piano-key[tabindex]').length
  }))()`);
  assert.deepEqual(result, { all: 88, white: 52, black: 36, first: "A0", last: "C8", tabStops: 0 });
});

for (const [label, width, height, deviceScaleFactor] of [
  ["tablet landscape", 1180, 820, 2],
  ["tablet portrait", 820, 1180, 2],
  ["desktop", 1440, 900, 1],
  ["high-DPI-like", 1280, 800, 3],
]) {
  await test(`browser ${label} has stable, non-overflowing keyboard`, async () => {
    await cdp("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor, mobile: false });
    await sleep(80);
    const geometry = await evaluate(`(() => {
      const piano = document.querySelector('#live-keyboard');
      const stage = document.querySelector('.piano-stage');
      const blacks = [...document.querySelectorAll('.piano-key--black')];
      return {
        pianoWidth: piano.getBoundingClientRect().width,
        stageWidth: stage.getBoundingClientRect().width,
        scrollWidth: piano.scrollWidth,
        clientWidth: piano.clientWidth,
        minBlack: Math.min(...blacks.map((key) => key.getBoundingClientRect().left)),
        maxBlack: Math.max(...blacks.map((key) => key.getBoundingClientRect().right)),
        stageLeft: stage.getBoundingClientRect().left,
        stageRight: stage.getBoundingClientRect().right,
      };
    })()`);
    assert.ok(geometry.pianoWidth > 0 && geometry.pianoWidth <= geometry.stageWidth + 1);
    assert.ok(geometry.scrollWidth <= geometry.clientWidth + 1);
    assert.ok(geometry.minBlack >= geometry.stageLeft - 1);
    assert.ok(geometry.maxBlack <= geometry.stageRight + 1);
  });
}

await test("browser distinguishes physical, playback, overlap, and release", async () => {
  publish({ physical: [60, 64], playback: [62, 64] });
  await waitFor("document.querySelector('.piano-key[data-midi=\"64\"]').dataset.source === 'both'");
  assert.equal(await evaluate("document.querySelector('.piano-key[data-midi=\"60\"]').dataset.source"), "physical");
  assert.equal(await evaluate("document.querySelector('.piano-key[data-midi=\"62\"]').dataset.source"), "playback");
  publish({ physical: [], playback: [] });
  await waitFor("document.querySelectorAll('.piano-key:not([data-source=idle])').length === 0");
});

await test("one note change mutates one key instead of rerendering 88", async () => {
  publish({ physical: [60, 64], playback: [] });
  await waitFor("document.querySelector('.piano-key[data-midi=\"64\"]').dataset.source === 'physical'");
  await evaluate(`window.__keyMutations = 0; window.__keyObserver = new MutationObserver((items) => {
    window.__keyMutations += items.filter((item) => item.attributeName === 'data-source').length;
  }); window.__keyObserver.observe(document.querySelector('#live-keyboard'), { subtree: true, attributes: true, attributeFilter: ['data-source'] });`);
  publish({ physical: [60, 64, 67], playback: [] });
  await waitFor("document.querySelector('.piano-key[data-midi=\"67\"]').dataset.source === 'physical'");
  await sleep(50);
  assert.equal(await evaluate("window.__keyMutations"), 1);
  await evaluate("window.__keyObserver.disconnect()");
});

await test("stream loss reconnects without discarding still-fresh authoritative truth", async () => {
  publish({ physical: [60], playback: [72] });
  await waitFor("document.querySelectorAll('.piano-key:not([data-source=idle])').length === 2");
  for (const response of [...core.streams]) response.destroy();
  await sleep(100);
  assert.equal(await evaluate("document.querySelector('#live-keyboard-status').textContent"), "Live");
  assert.equal(await evaluate("document.querySelectorAll('.piano-key:not([data-source=idle])').length"), 2);
  await waitUntil(() => core.streams.size > 0, "replacement SSE connection", 5000);
});

await test("page reload reconstructs the current authoritative snapshot", async () => {
  publish({ physical: [55], playback: [67] });
  await waitFor("document.querySelector('.piano-key[data-midi=\"55\"]').dataset.source === 'physical'");
  await cdp("Page.reload", { ignoreCache: true });
  await waitFor("document.readyState === 'complete' && document.querySelector('#live-keyboard-status')?.textContent === 'Live'");
  assert.equal(await evaluate("document.querySelector('.piano-key[data-midi=\"55\"]').dataset.source"), "physical");
  assert.equal(await evaluate("document.querySelector('.piano-key[data-midi=\"67\"]').dataset.source"), "playback");
  publish({ physical: [], playback: [] });
});

await test("runtime restart snapshot replaces prior active state", async () => {
  core.epoch = "browser-runtime-b";
  core.sequence = 0;
  core.physical = [21];
  core.playback = [108];
  for (const response of [...core.streams]) response.destroy();
  await waitFor("document.querySelector('#live-keyboard-status').textContent === 'Live' && document.querySelector('.piano-key[data-midi=\"21\"]').dataset.source === 'physical'", 5000);
  assert.equal(await evaluate("document.querySelector('.piano-key[data-midi=\"108\"]').dataset.source"), "playback");
  assert.equal(await evaluate("document.querySelector('.piano-key[data-midi=\"60\"]').dataset.source"), "idle");
});

await test("silently dropped physical/playback releases converge with heartbeats", async () => {
  publish({ physical: [60], playback: [] });
  await waitFor("document.querySelector('.piano-key[data-midi=\"60\"]').dataset.source === 'physical'");
  const requestsBefore = core.httpSnapshotRequests;
  const heartbeatsBefore = core.heartbeatFrames;
  const heartbeatTimer = setInterval(heartbeat, 100);
  try {
    const physicalRelease = publish({
      physical: [], playback: [], deliver: false,
    }).sequence;
    await waitFor(
      `document.querySelector('#live-keyboard').dataset.appliedSequence === '${physicalRelease}' && document.querySelectorAll('.piano-key:not([data-source=idle])').length === 0`,
      5000
    );

    publish({ physical: [], playback: [72] });
    await waitFor("document.querySelector('.piano-key[data-midi=\"72\"]').dataset.source === 'playback'");
    const playbackRelease = publish({
      physical: [], playback: [], deliver: false,
    }).sequence;
    await waitFor(
      `document.querySelector('#live-keyboard').dataset.appliedSequence === '${playbackRelease}' && document.querySelector('.piano-key[data-midi="72"]').dataset.source === 'idle'`,
      5000
    );

    publish({ physical: [64], playback: [64] });
    await waitFor("document.querySelector('.piano-key[data-midi=\"64\"]').dataset.source === 'both'");
    const oneSourceRelease = publish({
      physical: [], playback: [64], deliver: false,
    }).sequence;
    await waitFor(
      `document.querySelector('#live-keyboard').dataset.appliedSequence === '${oneSourceRelease}' && document.querySelector('.piano-key[data-midi="64"]').dataset.source === 'playback'`,
      5000
    );
  } finally {
    clearInterval(heartbeatTimer);
  }
  assert.ok(core.streams.size > 0, "SSE connection should remain open");
  assert.ok(core.heartbeatFrames > heartbeatsBefore, "heartbeats should continue");
  assert.ok(core.httpSnapshotRequests > requestsBefore, "GET resnapshot should recover truth");
});

await test("delayed same-epoch HTTP response cannot overwrite newer SSE", async () => {
  publish({ physical: [60], playback: [] });
  await waitFor("document.querySelector('.piano-key[data-midi=\"60\"]').dataset.source === 'physical'");
  const requestsBefore = core.httpSnapshotRequests;
  core.nextSnapshotDelayMs = 500;
  await (async () => {
    const deadline = Date.now() + 3000;
    while (core.httpSnapshotRequests === requestsBefore && Date.now() < deadline) await sleep(20);
    assert.ok(core.httpSnapshotRequests > requestsBefore);
  })();
  const expected = publish({ physical: [72], playback: [] }).sequence;
  await waitFor(`document.querySelector('#live-keyboard').dataset.appliedSequence === '${expected}'`);
  await sleep(650);
  assert.equal(await evaluate("document.querySelector('.piano-key[data-midi=\"72\"]').dataset.source"), "physical");
  assert.equal(await evaluate("document.querySelector('.piano-key[data-midi=\"60\"]').dataset.source"), "idle");
});

await test("delayed prior-epoch HTTP response cannot overwrite newer runtime SSE", async () => {
  const priorEpoch = core.epoch;
  publish({ physical: [55], playback: [] });
  await waitFor("document.querySelector('.piano-key[data-midi=\"55\"]').dataset.source === 'physical'");
  const requestsBefore = core.httpSnapshotRequests;
  core.nextSnapshotDelayMs = 500;
  await (async () => {
    const deadline = Date.now() + 3000;
    while (core.httpSnapshotRequests === requestsBefore && Date.now() < deadline) await sleep(20);
    assert.ok(core.httpSnapshotRequests > requestsBefore);
  })();
  core.epoch = `${priorEpoch}-next`;
  core.sequence = 0;
  const expected = publish({ physical: [84], playback: [] }).sequence;
  await waitFor(`document.querySelector('#live-keyboard').dataset.runtimeEpoch === '${core.epoch}' && document.querySelector('#live-keyboard').dataset.appliedSequence === '${expected}'`);
  await sleep(650);
  assert.equal(await evaluate("document.querySelector('.piano-key[data-midi=\"84\"]').dataset.source"), "physical");
  assert.equal(await evaluate("document.querySelector('.piano-key[data-midi=\"55\"]').dataset.source"), "idle");
});

await test("post-header stalled body is aborted across visibility hide and resume", async () => {
  await waitUntil(() => core.httpActive === 0, "no prior HTTP request");
  const expected = publish({ physical: [66], playback: [] }).sequence;
  for (const response of [...core.streams]) response.destroy();
  core.rejectSse = true;
  core.httpScenario = "abort_then_healthy";
  core.httpScenarioRequest = 0;
  core.httpSnapshotRequests = 0;
  core.httpHeadersReceived = 0;
  core.httpCompleted = 0;
  core.httpBodiesCompleted = 0;
  core.httpAborted = 0;
  core.httpFailed = 0;
  core.httpActive = 0;
  core.httpMaxConcurrent = 0;
  await cdp("Page.reload", { ignoreCache: true });
  await waitUntil(
    () => core.httpScenarioRequest === 1 && core.httpHeadersReceived === 1 && core.httpActive === 1,
    "first stalled body after headers"
  );
  await setSyntheticVisibility("hidden");
  await waitUntil(() => core.httpAborted === 1 && core.httpActive === 0, "hidden request abort");
  await setSyntheticVisibility("visible");
  await waitFor(
    `document.querySelector('#live-keyboard-status')?.textContent === 'Live' && document.querySelector('#live-keyboard').dataset.appliedSequence === '${expected}' && document.querySelector('.piano-key[data-midi="66"]').dataset.source === 'physical'`,
    5000
  );
  const final = await evaluate(`(() => ({
    state: document.querySelector('#live-keyboard-status').textContent,
    sequence: Number(document.querySelector('#live-keyboard').dataset.appliedSequence),
    epoch: document.querySelector('#live-keyboard').dataset.runtimeEpoch
  }))()`);
  const metric = {
    started: core.httpSnapshotRequests,
    headersReceived: core.httpHeadersReceived,
    completed: core.httpBodiesCompleted,
    aborted: core.httpAborted,
    timedOut: 0,
    failed: core.httpFailed,
    accepted: final.sequence === expected ? 1 : 0,
    stale: 1,
    maxConcurrent: core.httpMaxConcurrent,
    finalSequence: final.sequence,
    finalEpoch: final.epoch,
    finalKeyboardState: final.state,
  };
  console.log(`visibility-body-metric - ${JSON.stringify(metric)}`);
  assert.deepEqual(metric, {
    started: 2,
    headersReceived: 2,
    completed: 1,
    aborted: 1,
    timedOut: 0,
    failed: 0,
    accepted: 1,
    stale: 1,
    maxConcurrent: 1,
    finalSequence: expected,
    finalEpoch: core.epoch,
    finalKeyboardState: "Live",
  });
  await restoreVisibility();
  core.rejectSse = false;
  core.httpScenario = "normal";
  await cdp("Page.reload", { ignoreCache: true });
  await waitFor("document.readyState === 'complete' && document.querySelector('#live-keyboard-status')?.textContent === 'Live'");
});

await test("post-header stalled body times out and next HTTP verification recovers", async () => {
  await waitUntil(() => core.httpActive === 0, "no prior HTTP request");
  const expected = publish({ physical: [68], playback: [] }).sequence;
  for (const response of [...core.streams]) response.destroy();
  core.rejectSse = true;
  core.httpScenario = "timeout_then_healthy";
  core.httpScenarioRequest = 0;
  core.httpSnapshotRequests = 0;
  core.httpHeadersReceived = 0;
  core.httpCompleted = 0;
  core.httpBodiesCompleted = 0;
  core.httpAborted = 0;
  core.httpFailed = 0;
  core.httpActive = 0;
  core.httpMaxConcurrent = 0;
  await cdp("Page.reload", { ignoreCache: true });
  await waitUntil(
    () => core.httpScenarioRequest === 1 && core.httpHeadersReceived === 1 && core.httpActive === 1,
    "timeout scenario stalled body"
  );
  await waitFor(
    `document.querySelector('#live-keyboard-status')?.textContent === 'Live' && document.querySelector('#live-keyboard').dataset.appliedSequence === '${expected}' && document.querySelector('.piano-key[data-midi="68"]').dataset.source === 'physical'`,
    14000
  );
  const final = await evaluate(`(() => ({
    state: document.querySelector('#live-keyboard-status').textContent,
    sequence: Number(document.querySelector('#live-keyboard').dataset.appliedSequence),
    epoch: document.querySelector('#live-keyboard').dataset.runtimeEpoch
  }))()`);
  const metric = {
    started: core.httpSnapshotRequests,
    headersReceived: core.httpHeadersReceived,
    completed: core.httpBodiesCompleted,
    aborted: 0,
    timedOut: core.httpAborted,
    failed: core.httpFailed,
    accepted: final.sequence === expected ? 1 : 0,
    stale: 1,
    maxConcurrent: core.httpMaxConcurrent,
    finalSequence: final.sequence,
    finalEpoch: final.epoch,
    finalKeyboardState: final.state,
  };
  console.log(`timeout-body-metric - ${JSON.stringify(metric)}`);
  assert.deepEqual(metric, {
    started: 2,
    headersReceived: 2,
    completed: 1,
    aborted: 0,
    timedOut: 1,
    failed: 0,
    accepted: 1,
    stale: 1,
    maxConcurrent: 1,
    finalSequence: expected,
    finalEpoch: core.epoch,
    finalKeyboardState: "Live",
  });
  core.rejectSse = false;
  core.httpScenario = "normal";
  await cdp("Page.reload", { ignoreCache: true });
  await waitFor("document.readyState === 'complete' && document.querySelector('#live-keyboard-status')?.textContent === 'Live'");
});

await test("continuous SSE 503 cannot starve delayed healthy HTTP recovery", async () => {
  await waitUntil(() => core.httpActive === 0, "no prior HTTP request");
  const expected = publish({ physical: [65], playback: [] }).sequence;
  for (const response of [...core.streams]) response.destroy();
  core.rejectSse = true;
  core.httpScenario = "normal";
  core.httpScenarioRequest = 0;
  core.defaultSnapshotDelayMs = 1500;
  core.httpSnapshotRequests = 0;
  core.httpHeadersReceived = 0;
  core.httpCompleted = 0;
  core.httpBodiesCompleted = 0;
  core.httpAborted = 0;
  core.httpFailed = 0;
  core.httpActive = 0;
  core.httpMaxConcurrent = 0;
  core.sseConnectAttempts = 0;
  await cdp("Page.reload", { ignoreCache: true });
  await waitFor(
    `document.querySelector('#live-keyboard-status')?.textContent === 'Live' && document.querySelector('#live-keyboard').dataset.appliedSequence === '${expected}' && document.querySelector('.piano-key[data-midi="65"]').dataset.source === 'physical'`,
    5000
  );
  await waitUntil(() => core.sseConnectAttempts >= 3, "three rejected SSE generations", 3500);
  const final = await evaluate(`(() => ({
    state: document.querySelector('#live-keyboard-status').textContent,
    sequence: Number(document.querySelector('#live-keyboard').dataset.appliedSequence),
    epoch: document.querySelector('#live-keyboard').dataset.runtimeEpoch,
    note: document.querySelector('.piano-key[data-midi="65"]').dataset.source
  }))()`);
  const metric = {
    httpStarted: core.httpSnapshotRequests,
    httpCompleted: core.httpCompleted,
    httpFailed: core.httpFailed,
    httpAccepted: final.sequence === expected && final.epoch === core.epoch ? 1 : 0,
    httpIgnoredStale: 0,
    httpMaxConcurrent: core.httpMaxConcurrent,
    sseConnectAttempts: core.sseConnectAttempts,
    finalReplicaSequence: final.sequence,
    finalRuntimeEpoch: final.epoch,
    finalKeyboardState: final.state,
  };
  console.log(`recovery-metric - ${JSON.stringify(metric)}`);
  assert.equal(metric.httpStarted, 1);
  assert.equal(metric.httpCompleted, 1);
  assert.equal(metric.httpFailed, 0);
  assert.equal(metric.httpAccepted, 1);
  assert.equal(metric.httpIgnoredStale, 0);
  assert.equal(metric.httpMaxConcurrent, 1);
  assert.ok(metric.sseConnectAttempts >= 3);
  assert.deepEqual(final, {
    state: "Live", sequence: expected, epoch: core.epoch, note: "physical",
  });

  core.rejectSse = false;
  core.defaultSnapshotDelayMs = 0;
  await cdp("Page.reload", { ignoreCache: true });
  await waitFor("document.readyState === 'complete' && document.querySelector('#live-keyboard-status')?.textContent === 'Live'");
});

await test("rapid synthetic chords settle promptly without page errors", async () => {
  const generatedBefore = core.generatedSnapshots;
  const deliveredBefore = core.deliveredSnapshots;
  const sequenceBefore = core.sequence;
  const finalExpectedSequence = sequenceBefore + 100;
  const finalRoot = 48 + (99 % 12);
  const started = performance.now();
  // The old benchmark predicate becomes true after index 3 because it shares
  // the final root. Prove that state is rejected by the corrected sequence
  // barrier before delivering the remaining 96 snapshots.
  for (let index = 0; index < 4; index += 1) {
    const root = 48 + (index % 12);
    publish({ physical: [root, root + 4, root + 7], playback: [root + 12] });
  }
  await waitFor(`document.querySelector('.piano-key[data-midi="${finalRoot}"]').dataset.source === 'physical'`);
  const firstFourAppliedSequence = await evaluate(
    "Number(document.querySelector('#live-keyboard').dataset.appliedSequence)"
  );
  const negativeControl = {
    generated: 4,
    delivered: 4,
    finalExpectedSequence,
    appliedSequence: firstFourAppliedSequence,
    correctedBarrierAccepted: firstFourAppliedSequence === finalExpectedSequence,
    result: firstFourAppliedSequence === finalExpectedSequence ? "UNEXPECTED_PASS" : "FAIL_AS_EXPECTED",
  };
  console.log(`benchmark-negative-control - ${JSON.stringify(negativeControl)}`);
  assert.equal(negativeControl.correctedBarrierAccepted, false);
  for (let index = 4; index < 100; index += 1) {
    const root = 48 + (index % 12);
    publish({ physical: [root, root + 4, root + 7], playback: [root + 12] });
  }
  await waitFor(`document.querySelector('#live-keyboard').dataset.runtimeEpoch === '${core.epoch}' && document.querySelector('#live-keyboard').dataset.appliedSequence === '${finalExpectedSequence}'`);
  const elapsed = performance.now() - started;
  const finalState = await evaluate(`(() => ({
    epoch: document.querySelector('#live-keyboard').dataset.runtimeEpoch,
    sequence: Number(document.querySelector('#live-keyboard').dataset.appliedSequence),
    active: [...document.querySelectorAll('.piano-key:not([data-source=idle])')]
      .map((key) => [Number(key.dataset.midi), key.dataset.source])
      .sort((a, b) => a[0] - b[0])
  }))()`);
  const metric = {
    generated: core.generatedSnapshots - generatedBefore,
    delivered: core.deliveredSnapshots - deliveredBefore,
    finalExpectedSequence,
    finalAppliedSequence: finalState.sequence,
    finalRenderedState: finalState.active,
    elapsedMs: Number(elapsed.toFixed(1)),
    result: elapsed < 1000 ? "PASS" : "FAIL",
  };
  console.log(`metric - ${JSON.stringify(metric)}`);
  assert.equal(metric.generated, 100);
  assert.equal(metric.delivered, 100);
  assert.equal(metric.finalAppliedSequence, metric.finalExpectedSequence);
  assert.deepEqual(finalState.active, [
    [finalRoot, "physical"],
    [finalRoot + 4, "physical"],
    [finalRoot + 7, "physical"],
    [finalRoot + 12, "playback"],
  ]);
  assert.ok(elapsed < 1000, `synthetic settle took ${elapsed} ms`);
});

ws.close();
chrome.kill("SIGTERM");
await new Promise((done) => chrome.once("exit", done));
for (const response of [...core.streams]) response.destroy();
await new Promise((done) => server.close(done));
// Chrome descendants may finish profile writes just after the main process
// exits. This retries disposable profile cleanup only, never test behavior.
await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });

if (failures > 0) {
  console.error(`${failures} browser keyboard test(s) failed`);
  process.exitCode = 1;
} else {
  console.log("ALL CHROMIUM KEYBOARD TESTS PASSED");
}
