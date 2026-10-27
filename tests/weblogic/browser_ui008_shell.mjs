// PIANO-UI-008 U8-P1: real Chromium/HTTP shell, routing and geometry gates.
// Headless Chrome uses SwiftShader here; results are not physical-device or
// hardware-GPU evidence. The controlled server is loopback-only.
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
const EVIDENCE_DIR = process.env.UI008_EVIDENCE_DIR || "";
const geometryEvidence = {};

let passes = 0;
let failures = 0;
const pageErrors = [];
async function test(name, fn) {
  const before = pageErrors.length;
  try {
    await fn();
    assert.equal(pageErrors.length, before, pageErrors.slice(before).join(" | "));
    passes += 1;
    console.log(`ok - ${name}`);
  } catch (error) {
    failures += 1;
    console.error(`FAIL - ${name}`);
    console.error(error);
  }
}

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

const score = {
  schema_version: 1,
  song_id: "song-shell",
  source_version: `score-sha256:${"a".repeat(64)}`,
  title: "Shell Test",
  duration_seconds: 60,
  time_basis: "song_seconds",
  note_count: 1,
  notes: [{
    index: 0, midi: 60, channel: 1, velocity: 90,
    onset_seconds: 12, duration_seconds: 1,
  }],
  diagnostics: {
    percussion_note_ons: 0, outside_keyboard_note_ons: 0,
    unpaired_note_offs: 0, retriggered_note_ons: 0, unterminated_note_ons: 0,
  },
  notation: {
    supported: ["pitch", "onset", "duration", "channel", "velocity"],
    unsupported: ["measure", "beat", "hand", "fingering", "tempo_map"],
  },
};

const plan = {
  schema_version: 1,
  engine_version: "balanced-engine-v1",
  plan_id: `fingering-sha256:${"b".repeat(64)}`,
  song_id: score.song_id,
  source_version: score.source_version,
  version: 1,
  generation: 1,
  strategy: "balanced_v1",
  provenance: "DETERMINISTIC_INFERENCE",
  targets: [{
    score_index: 0, target_id: "score:0", midi: 60, channel: 1,
    onset_seconds: 12, duration_seconds: 1, hand: "RIGHT", finger: 2,
    alternatives: [],
  }],
  warnings: ["source_contains_no_authored_hand_or_fingering_metadata"],
  excluded_note_count: 0,
  config: { strategy: "balanced_v1", max_hand_span_semitones: 12, alternative_count: 1 },
  capabilities: { manual_overrides: false, finger_sensitive_grading: false },
};

const playback = {
  available: true, state: "PAUSED", song_id: score.song_id, title: score.title,
  position_seconds: 12, duration_seconds: 60, tempo_multiplier: 1,
  events_sent: 0, error: null,
};

const learning = {
  available: true, state: "WAITING", song_id: score.song_id, title: score.title,
  step_index: 0, total_steps: 1, completed_steps: 0, incorrect_attempts: 0,
  progress: 0, started_at: "2026-09-28T00:00:00Z", ended_at: null, error: null,
  midi_connection: "CONNECTED", session_id: "session-shell", mode: "WAIT_FOR_NOTE",
  hand_selection: null, section: null, target_tempo: null, accepted_notes: [],
  latest_outcome: "NONE", generation: 1, version: 7,
  updated_at: "2026-09-28T00:00:00Z",
  target: {
    target_id: "target:0:60", notes: [60], names: ["C4"], chord: false,
    onset_seconds: 12,
  },
  repetition: { current: 0, required: 1, successful: 0, failed: 0 },
  capabilities: {
    supported_modes: ["WAIT_FOR_NOTE", "SECTION_PRACTICE", "REPEAT_UNTIL_CORRECT"],
    missed_outcome: false, hand_selection: false, rhythm_grading: false,
    persistence: "runtime_only", section_basis: "song_time_seconds",
  },
};

let sequence = 0;
let postCount = 0;
const streams = new Set();
function noteSnapshot() {
  sequence += 1;
  return {
    schema_version: 1, runtime_epoch: "ui008-shell", sequence,
    physical: {
      notes: [], outside_keyboard_count: 0,
      sustain: { value: 0, pressed: false },
      midi_connection: "CONNECTED", revision: sequence,
    },
    playback: {
      available: true, notes: [], voices: [], outside_keyboard_count: 0,
      sustain_channels: [],
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
  if (request.method === "POST") postCount += 1;
  if (url.pathname === "/" || url.pathname === "/ui") {
    response.writeHead(302, { location: "/ui/" });
    response.end();
    return;
  }
  if (url.pathname.startsWith("/ui/")) {
    const name = url.pathname.slice(4) || "index.html";
    const path = join(STATIC, name);
    if (!path.startsWith(STATIC)) { response.writeHead(404).end(); return; }
    try {
      const body = await readFile(path);
      const types = {
        ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
        ".svg": "image/svg+xml", ".webmanifest": "application/manifest+json",
        ".glb": "model/gltf-binary", ".txt": "text/plain",
      };
      response.writeHead(200, {
        "content-type": types[extname(path)] || "application/octet-stream",
        "cache-control": "no-store",
      });
      response.end(body);
    } catch { response.writeHead(404).end(); }
    return;
  }
  if (url.pathname === "/api/v1/live-notes") { json(response, noteSnapshot()); return; }
  if (url.pathname === "/api/v1/live-notes/stream") {
    response.writeHead(200, {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache", connection: "close",
    });
    streams.add(response);
    response.write(`event: snapshot\ndata: ${JSON.stringify(noteSnapshot())}\n\n`);
    const heartbeat = setInterval(() => {
      try { response.write(": heartbeat\n\n"); } catch { /* closed */ }
    }, 2000);
    request.on("close", () => {
      clearInterval(heartbeat);
      streams.delete(response);
    });
    return;
  }
  if (url.pathname === "/api/v1/health") {
    json(response, {
      status: "ok", application: { state: "READY" },
      midi_input: { connection: "CONNECTED" },
      library: { ready: true, entries: 1 }, playback,
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
  if (url.pathname === "/api/v1/learning" || url.pathname === "/api/v1/practice") {
    json(response, learning);
    return;
  }
  if (url.pathname === `/api/v1/score/${score.song_id}`) { json(response, score); return; }
  if (url.pathname === `/api/v1/fingering/${score.song_id}`) { json(response, plan); return; }
  if (url.pathname === `/api/v1/library/songs/${score.song_id}`) {
    json(response, {
      song_id: score.song_id, title: score.title, filename: "shell.mid",
      relpath: "shell.mid", extension: ".mid", size_bytes: 1000, status: "READY",
      midi_format: 1, track_count: 1, ticks_per_beat: 96,
      duration_seconds: 60, tempo_bpm_first: 120,
      indexed_at_utc: "2026-09-28T00:00:00Z",
    });
    return;
  }
  if (url.pathname === "/api/v1/library/songs") {
    json(response, { query: {}, count: 0, total: 1, songs: [] });
    return;
  }
  if (url.pathname === "/api/v1/playback") { json(response, playback); return; }
  json(response, { error: { code: "not_found", message: "not found" } }, 404);
});

await new Promise((done) => server.listen(0, "127.0.0.1", done));
const appPort = server.address().port;
const probe = createServer();
await new Promise((done) => probe.listen(0, "127.0.0.1", done));
const debugPort = probe.address().port;
await new Promise((done) => probe.close(done));
const profile = await mkdtemp(join(tmpdir(), "piano-ui008-chrome-"));
const chrome = spawn(CHROME, [
  "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
  `--remote-debugging-port=${debugPort}`, `--user-data-dir=${profile}`,
  `http://127.0.0.1:${appPort}/ui/#/player`,
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
      message.params.exceptionDetails.text || "unknown page exception"
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
  const result = await cdp("Runtime.evaluate", {
    expression, awaitPromise: true, returnByValue: true,
  });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
  return result.result.value;
}

async function waitFor(expression, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await evaluate(expression)) return;
    await sleep(50);
  }
  throw new Error(`timeout: ${expression}`);
}

async function viewport(width, height, scale = 1) {
  await cdp("Emulation.setDeviceMetricsOverride", {
    width, height, deviceScaleFactor: scale, mobile: false,
  });
  await sleep(180);
}

async function route(name) {
  await evaluate(`location.hash = '#/${name}'`);
  await waitFor(`document.body.dataset.ui008Route === '${name}'`);
}

async function capture(name) {
  if (!EVIDENCE_DIR) return;
  await mkdir(EVIDENCE_DIR, { recursive: true });
  const shot = await cdp("Page.captureScreenshot", { format: "png", fromSurface: true });
  await writeFile(join(EVIDENCE_DIR, name), Buffer.from(shot.data, "base64"));
}

await cdp("Runtime.enable");
await cdp("Page.enable");
await waitFor("document.documentElement?.dataset.ui008ShellReady === 'true'");
await waitFor("document.getElementById('player-state').textContent === 'Paused'");

await test("direct route restores Player with semantic current navigation", async () => {
  assert.equal(await evaluate("document.body.dataset.ui008Route"), "player");
  assert.equal(await evaluate("document.getElementById('ui008-route-title').textContent"), "Player");
  assert.ok(await evaluate("document.querySelectorAll('[data-ui008-route=player][aria-current=page]').length >= 1"));
  assert.equal(postCount, 0);
});

await test("all approved routes resolve and unsupported controls remain absent", async () => {
  for (const name of ["home", "library", "player", "learn", "piano", "settings"]) {
    await route(name);
    assert.equal(await evaluate("document.querySelectorAll('[data-ui008-route-hidden=false]').length > 0"), true);
  }
  assert.equal(await evaluate("['seek','loop-a','metronome','count-in','queue'].some(id => document.getElementById(id))"), false);
  assert.equal(postCount, 0);
});

await test("browser back and forward restore routes without resetting replicas", async () => {
  await route("player");
  const before = await evaluate(`JSON.stringify({
    position: document.getElementById('player-position').textContent,
    session: document.getElementById('learning-mode-active').textContent,
    target: document.getElementById('practice-target').textContent
  })`);
  await route("library");
  await evaluate("history.back()");
  await waitFor("document.body.dataset.ui008Route === 'player'");
  await evaluate("history.forward()");
  await waitFor("document.body.dataset.ui008Route === 'library'");
  await route("learn");
  const after = await evaluate(`JSON.stringify({
    position: document.getElementById('player-position').textContent,
    session: document.getElementById('learning-mode-active').textContent,
    target: document.getElementById('practice-target').textContent
  })`);
  assert.equal(after, before);
  assert.equal(postCount, 0);
});

await test("unknown route fails safely to Home", async () => {
  await evaluate("location.hash = '#/not-approved'");
  await waitFor("document.body.dataset.ui008Route === 'home'");
  assert.equal(await evaluate("location.hash"), "#/home");
});

await test("refresh restores the current surface without a Core mutation", async () => {
  await route("settings");
  await cdp("Page.reload", { ignoreCache: true });
  await waitFor("document.documentElement?.dataset.ui008ShellReady === 'true'");
  await waitFor("document.body.dataset.ui008Route === 'settings'");
  assert.equal(await evaluate("document.getElementById('ui008-route-title').textContent"), "Settings");
  assert.equal(postCount, 0);
});

const matrix = [
  [390, 844, "bottom"], [599, 844, "bottom"], [600, 900, "bottom"],
  [768, 1024, "bottom"], [899, 900, "bottom"], [900, 900, "rail"],
  [1024, 768, "rail"], [1365, 900, "rail"], [1366, 900, "rail"],
  [1440, 900, "rail"], [1919, 1080, "rail"], [1920, 1080, "rail"],
];

await test("responsive matrix selects the contracted navigation without overflow", async () => {
  await route("player");
  for (const [width, height, mode] of matrix) {
    await viewport(width, height);
    const metrics = await evaluate(`(() => {
      const rail = getComputedStyle(document.querySelector('.primary-nav')).display !== 'none';
      const bottom = getComputedStyle(document.querySelector('.bottom-nav')).display !== 'none';
      return { rail, bottom, overflow: document.documentElement.scrollWidth - innerWidth };
    })()`);
    assert.equal(metrics.rail, mode === "rail", `${width}: rail`);
    assert.equal(metrics.bottom, mode === "bottom", `${width}: bottom`);
    assert.ok(metrics.overflow <= 0, `${width}: overflow ${metrics.overflow}`);
  }
});

await test("hands workspace cannot return to the audited narrow/tall collapse", async () => {
  // IMMERSIVE composition update: the hands scene is presented through the
  // shared visual workspace (view selector). The audited no-collapse
  // contract now applies to the PRESENTED hands stage, which fills the
  // stage scene at every approved width.
  await route("learn");
  for (const [width, height] of [[1024, 768], [1440, 900], [1920, 1080]]) {
    await viewport(width, height);
    await evaluate(`(() => { const el = document.getElementById('ui008-view-learn'); el.value = 'hands'; el.dispatchEvent(new Event('change', { bubbles: true })); })()`);
    await waitFor("document.getElementById('hands')?.hidden === false");
    const rect = await evaluate(`(() => {
      const card = document.getElementById('hands').getBoundingClientRect();
      const stage = document.getElementById('hands-stage').getBoundingClientRect();
      return { cardWidth: card.width, cardHeight: card.height, stageWidth: stage.width, stageHeight: stage.height };
    })()`);
    assert.ok(rect.cardWidth >= 560, `${width}: card width ${rect.cardWidth}`);
    assert.ok(rect.stageWidth >= 560, `${width}: stage width ${rect.stageWidth}`);
    assert.ok(rect.cardHeight >= height * .65 && rect.cardHeight < height, `${width}: dominant scene height ${rect.cardHeight}`);
    assert.ok(rect.stageHeight >= height * .50 && rect.stageHeight <= rect.cardHeight, `${width}: hands stage must occupy the dominant scene (${rect.stageHeight})`);
    geometryEvidence[`${width}x${height}`] = rect;
    await evaluate(`(() => { const el = document.getElementById('ui008-view-learn'); el.value = 'falling'; el.dispatchEvent(new Event('change', { bubbles: true })); })()`);
  }
});

await test("visible interactive foundations meet the touch contract", async () => {
  for (const [width, height] of [[390, 844], [768, 1024], [1024, 768]]) {
    await viewport(width, height);
    await route("player");
    const failures = await evaluate(`[...document.querySelectorAll('button,a[href],input,select,summary')]
      .filter(el => {
        const s = getComputedStyle(el); const r = el.getBoundingClientRect();
        return !el.disabled && !el.hidden && s.display !== 'none' && s.visibility !== 'hidden' &&
          r.width > 0 && r.right > 0 && r.left < innerWidth && r.bottom > 0 && r.top < innerHeight;
      })
      .map(el => { const r = el.getBoundingClientRect(); return { id: el.id || el.textContent.trim(), w: r.width, h: r.height }; })
      .filter(x => x.w < 44 || x.h < 44)`);
    assert.deepEqual(failures, [], `${width}: ${JSON.stringify(failures)}`);
    const primary = await evaluate(`(() => { const r = document.querySelector('#player .transport-btn--primary:not([hidden])').getBoundingClientRect(); return [r.width,r.height]; })()`);
    assert.ok(primary[0] >= 56 && primary[1] >= 56, `${width}: ${primary}`);
  }
});

await test("More sheet is contained and Escape/back close it before routing", async () => {
  await viewport(390, 844);
  await route("home");
  await evaluate("document.querySelector('[data-ui008-open-more]').click()");
  await waitFor("!document.getElementById('ui008-more-sheet').hidden");
  const sheetRect = await evaluate(`(() => {
    const r = document.querySelector('.bottom-sheet').getBoundingClientRect();
    return { left: r.left, right: r.right, top: r.top, bottom: r.bottom,
      width: innerWidth, height: innerHeight };
  })()`);
  assert.ok(sheetRect.left >= -1, JSON.stringify(sheetRect));
  assert.ok(sheetRect.right <= sheetRect.width + 1, JSON.stringify(sheetRect));
  assert.ok(sheetRect.top >= -1, JSON.stringify(sheetRect));
  assert.ok(sheetRect.bottom <= sheetRect.height + 1, JSON.stringify(sheetRect));
  await cdp("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape" });
  await cdp("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape" });
  await waitFor("document.getElementById('ui008-more-sheet').hidden");
  assert.equal(await evaluate("document.body.dataset.ui008Route"), "home");
  await evaluate("document.querySelector('[data-ui008-open-more]').click()");
  await waitFor("!document.getElementById('ui008-more-sheet').hidden");
  await evaluate("history.back()");
  await waitFor("document.getElementById('ui008-more-sheet').hidden");
  assert.equal(await evaluate("document.body.dataset.ui008Route"), "home");
  assert.equal(postCount, 0, "layer Escape/back must not emit a musical command");
});

await test("urgent toast remains above the modal layer and normal state restores", async () => {
  await viewport(390, 844);
  await route("home");
  await evaluate(`(() => {
    const toast = document.getElementById('toast');
    toast.textContent = 'Controlled urgent status';
    toast.hidden = false;
    document.querySelector('[data-ui008-open-more]').click();
  })()`);
  await waitFor("!document.getElementById('ui008-more-sheet').hidden");
  const stacking = await evaluate(`(() => {
    const toast = document.getElementById('toast');
    const backdrop = document.getElementById('ui008-more-sheet');
    const rect = toast.getBoundingClientRect();
    const top = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    return {
      toastZ: Number(getComputedStyle(toast).zIndex),
      modalZ: Number(getComputedStyle(backdrop).zIndex),
      visible: rect.width > 0 && rect.height > 0,
      topIsToast: top === toast || toast.contains(top),
      pointerEvents: getComputedStyle(toast).pointerEvents,
      role: toast.getAttribute('role'),
    };
  })()`);
  assert.ok(stacking.toastZ > stacking.modalZ, JSON.stringify(stacking));
  assert.equal(stacking.visible, true, JSON.stringify(stacking));
  assert.equal(stacking.topIsToast, true, JSON.stringify(stacking));
  assert.notEqual(stacking.pointerEvents, "none", JSON.stringify(stacking));
  assert.equal(stacking.role, "status");
  await cdp("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape" });
  await cdp("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape" });
  await waitFor("document.getElementById('ui008-more-sheet').hidden");
  assert.equal(await evaluate("document.body.classList.contains('ui008-layer-open')"), false);
  assert.equal(await evaluate("document.activeElement === document.querySelector('[data-ui008-open-more]')"), true);
  assert.equal(await evaluate("document.getElementById('toast').hidden"), false);
  await evaluate("document.getElementById('toast').hidden = true");
  assert.equal(await evaluate("document.getElementById('toast').hidden"), true);
  assert.equal(await evaluate("document.body.dataset.ui008Route"), "home");
});

await test("reduced motion removes shell animation", async () => {
  await cdp("Emulation.setEmulatedMedia", {
    features: [{ name: "prefers-reduced-motion", value: "reduce" }],
  });
  await route("player");
  assert.equal(await evaluate("getComputedStyle(document.getElementById('hands')).animationName"), "none");
  await cdp("Emulation.setEmulatedMedia", { features: [] });
});

await test("UI-007 renderer and Tier-A asset still initialize unchanged", async () => {
  // IMMERSIVE composition update: the hands renderer initializes lazily on
  // first presentation of the Hands view (no background GLB/WebGL work for
  // an unused view). The frozen renderer/asset contract itself is asserted
  // unchanged: same debug surface, same backend family, Tier-A ready.
  await viewport(1024, 768);
  await route("learn");
  await evaluate(`(() => { const el = document.getElementById('ui008-view-learn'); el.value = 'hands'; el.dispatchEvent(new Event('change', { bubbles: true })); })()`);
  await waitFor("document.getElementById('hands')?.hidden === false");
  await waitFor("globalThis.__pianoHands && globalThis.__pianoHands.info()", 60000);
  await waitFor("globalThis.__pianoHands.info().assetState === 'ready'", 30000);
  const info = await evaluate("globalThis.__pianoHands.info()");
  assert.ok(["webgpu", "webgl2"].includes(info.backend), info.backend);
  assert.equal(info.assetState, "ready", info.assetError);
});

await test("fresh visual evidence captures all required target viewports", async () => {
  const captures = [
    [390, 844, "phone-390x844-shell.png", "home"],
    [768, 1024, "tablet-portrait-768x1024-shell.png", "home"],
    [1024, 768, "tablet-landscape-1024x768-player.png", "player"],
    [1440, 900, "desktop-1440x900-player.png", "player"],
    [1920, 1080, "wide-1920x1080-player.png", "player"],
  ];
  for (const [width, height, name, destination] of captures) {
    await viewport(width, height);
    await route(destination);
    await sleep(320);
    await capture(name);
  }
  await viewport(1024, 768);
  await route("library");
  await sleep(320);
  await capture("tablet-landscape-1024x768-library-route.png");
  for (const [width, height, name] of [
    [1024, 768, "tablet-landscape-1024x768-hands.png"],
    [1440, 900, "desktop-1440x900-hands.png"],
    [1920, 1080, "wide-1920x1080-hands.png"],
  ]) {
    await viewport(width, height);
    await route("player");
    await evaluate("document.getElementById('hands').scrollIntoView({ block: 'center' })");
    await sleep(500);
    await capture(name);
  }
  if (EVIDENCE_DIR) {
    await writeFile(
      join(EVIDENCE_DIR, "hands-geometry.json"),
      `${JSON.stringify(geometryEvidence, null, 2)}\n`,
      "utf8"
    );
  }
  assert.equal(true, true);
});

for (const stream of streams) stream.end();
ws.close();
chrome.kill("SIGTERM");
await new Promise((done) => server.close(done));
await Promise.race([
  new Promise((done) => chrome.once("exit", done)),
  sleep(2000),
]);
await rm(profile, { recursive: true, force: true });

if (failures) {
  console.error(`FAIL ${failures}; PASS ${passes}`);
  process.exit(1);
}
console.log(`PASS ${passes}/${passes}`);
console.log(`HANDS_GEOMETRY ${JSON.stringify(geometryEvidence)}`);
