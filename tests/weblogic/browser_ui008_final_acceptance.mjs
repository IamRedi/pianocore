// PIANO-UI-008 U8-P6: REAL Chromium/HTTP final integrated acceptance.
// Long-session cross-surface loop, per-viewport product gates, recovery
// consistency, text-scale resilience proxy and the final evidence
// captures. Headless Chrome/SwiftShader; loopback-only mock Core serving
// the REAL contract shapes. Physical touch/Pi/MIDI/LED evidence is NOT
// claimed here (recorded NOT_RUN separately).
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

let passes = 0;
let failures = 0;
const pageErrors = [];
async function test(name, fn) {
  const before = pageErrors.length;
  const started = Date.now();
  try {
    await fn();
    assert.equal(pageErrors.length, before, pageErrors.slice(before).join(" | "));
    passes += 1;
    console.log(`ok - ${name} (${Date.now() - started}ms)`);
  } catch (error) {
    failures += 1;
    console.error(`FAIL - ${name}`);
    console.error(error);
  }
}
const sleep = (ms) => new Promise((d) => setTimeout(d, ms));

/* ---------------- controlled Core (loopback) ---------------- */

const songs = [
  { song_id: "song-x", filename: "nocturne-x.mid", relpath: "nocturne-x.mid", extension: ".mid", size_bytes: 800, status: "parsed", midi_format: 1, track_count: 1, ticks_per_beat: 480, duration_seconds: 120, tempo_bpm_first: 90, title: "Nocturne X", indexed_at_utc: "2026-10-02T00:00:00Z" },
];
const score = { schema_version: 1, song_id: "song-x", source_version: `score-sha256:${"a".repeat(64)}`, title: "Nocturne X", duration_seconds: 120, time_basis: "song_seconds", note_count: 3, notes: [ { index: 0, midi: 67, channel: 1, velocity: 90, onset_seconds: 2, duration_seconds: 1 }, { index: 1, midi: 71, channel: 1, velocity: 90, onset_seconds: 6, duration_seconds: 1 }, { index: 2, midi: 74, channel: 1, velocity: 90, onset_seconds: 10, duration_seconds: 2 } ], diagnostics: { percussion_note_ons: 0, outside_keyboard_note_ons: 0, unpaired_note_offs: 0, retriggered_note_ons: 0, unterminated_note_ons: 0 }, notation: { supported: ["pitch", "onset", "duration"], unsupported: ["measure", "beat", "hand", "fingering"] } };
const plan = { schema_version: 1, engine_version: "balanced-engine-v1", plan_id: `fingering-sha256:${"b".repeat(64)}`, song_id: "song-x", source_version: `score-sha256:${"a".repeat(64)}`, version: 1, generation: 1, strategy: "balanced_v1", provenance: "DETERMINISTIC_INFERENCE", targets: [ { score_index: 0, target_id: "score:0", midi: 67, channel: 1, onset_seconds: 2, duration_seconds: 1, hand: "RIGHT", finger: 2, alternatives: [] }, { score_index: 1, target_id: "score:1", midi: 71, channel: 1, onset_seconds: 6, duration_seconds: 1, hand: "RIGHT", finger: 4, alternatives: [] } ], warnings: [], excluded_note_count: 0, config: { strategy: "balanced_v1", max_hand_span_semitones: 12, alternative_count: 1 }, capabilities: { manual_overrides: false, finger_sensitive_grading: false } };

const state = {
  midi: "CONNECTED",
  held: new Set(),
  delayStateMs: 0,
  libraryReady: true,
  deviceOk: true,
  denyNextStart: false,
  puts: { led: [] },
  led: { configured: true, enabled: true, available: true, connection: "READY", mode: "physical", brightness: 80, active_note_count: 0, practice_active: false, last_error: null, transport: { host: "127.0.0.1", port: 8765 }, dropped_events: 0, frames_sent: 0 },
  posts: { play: 0, stop: 0, start: 0, learningStop: 0 },
};
let seq = 0;
let playback = { available: true, state: "IDLE", song_id: null, title: null, position_seconds: 0, duration_seconds: 0, tempo_multiplier: 1, events_sent: 0, error: null };
let learning = { available: true, state: "IDLE", song_id: null, title: null, step_index: null, total_steps: null, completed_steps: 0, incorrect_attempts: 0, progress: 0, started_at: null, ended_at: null, error: null, midi_connection: "CONNECTED", session_id: null, mode: "WAIT_FOR_NOTE", hand_selection: null, section: null, target_tempo: null, accepted_notes: [], latest_outcome: "NONE", generation: 0, version: 0, updated_at: null, target: null, repetition: { current: 0, required: 1, successful: 0, failed: 0 } };

const sseClients = new Set();
const live = () => { seq += 1; return { schema_version: 1, runtime_epoch: "ui008-p6", sequence: seq, physical: { notes: [...state.held].sort((a, b) => a - b), outside_keyboard_count: 0, sustain: { value: 0, pressed: false }, midi_connection: state.midi, revision: seq }, playback: { available: true, notes: [], voices: [], outside_keyboard_count: 0, sustain_channels: [] } }; };
const pushLive = () => { seq += 1; const f = `event: snapshot\ndata: ${JSON.stringify(live())}\n\n`; for (const res of sseClients) { try { res.write(f); } catch { sseClients.delete(res); } } };
const setHeld = (notes) => { state.held = new Set(notes); pushLive(); };

function json(res, value, status = 200) {
  const body = JSON.stringify(value);
  res.writeHead(status, { "content-type": "application/json", "content-length": Buffer.byteLength(body), "cache-control": "no-store" });
  res.end(body);
}
const readBody = async (req) => { let b = ""; for await (const c of req) b += c; return JSON.parse(b || "{}"); };

const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://127.0.0.1");
  if (req.method === "PUT" && url.pathname === "/api/v1/led") {
    const body = await readBody(req);
    state.puts.led.push(body);
    if (body.enabled !== undefined) state.led.enabled = body.enabled;
    if (body.mode !== undefined) state.led.mode = body.mode;
    if (body.brightness !== undefined) state.led.brightness = body.brightness;
    state.led.available = state.led.enabled;
    json(res, ledPayload());
    return;
  }
  if (req.method === "POST" && url.pathname === "/api/v1/led/clear") { json(res, ledPayload()); return; }
  if (req.method === "POST" && url.pathname === "/api/v1/playback/play") {
    state.posts.play += 1;
    if (learning.state === "WAITING") { json(res, { error: { code: "playback_already_active", message: "practice owns" } }, 409); return; }
    playback = { ...playback, state: "PLAYING", song_id: "song-x", title: "Nocturne X", duration_seconds: 120, position_seconds: 0, events_sent: 4 };
    json(res, playback);
    return;
  }
  if (req.method === "POST" && url.pathname === "/api/v1/playback/pause") { playback = { ...playback, state: "PAUSED" }; json(res, playback); return; }
  if (req.method === "POST" && url.pathname === "/api/v1/playback/resume") { playback = { ...playback, state: "PLAYING" }; json(res, playback); return; }
  if (req.method === "POST" && url.pathname === "/api/v1/playback/stop") { state.posts.stop += 1; playback = { ...playback, state: "IDLE", song_id: null, title: null, position_seconds: 0, events_sent: 0 }; json(res, { ...playback, stopped: true }); return; }
  if (req.method === "POST" && url.pathname === "/api/v1/learning/start") {
    state.posts.start += 1;
    if (state.denyNextStart) { state.denyNextStart = false; json(res, { error: { code: "activity_conflict", message: "conflict" } }, 409); return; }
    learning = { ...learning, state: "WAITING", song_id: "song-x", title: "Nocturne X", step_index: 0, total_steps: 2, completed_steps: 0, progress: 0, session_id: "p6-sess-1", version: 1, generation: 1, target: { notes: [67], names: ["G4"], chord: false, onset_seconds: 2, target_id: "t:0" }, started_at: "t", updated_at: "t" };
    json(res, learning);
    return;
  }
  if (req.method === "POST" && url.pathname === "/api/v1/learning/stop") {
    state.posts.learningStop += 1;
    learning = { ...learning, state: "IDLE", song_id: null, title: null, step_index: null, total_steps: null, completed_steps: 0, progress: 0, session_id: null, target: null, version: learning.version + 1 };
    json(res, learning);
    return;
  }
  if (url.pathname.startsWith("/ui/")) {
    const name = url.pathname.slice(4) || "index.html";
    const path = join(STATIC, name);
    if (!path.startsWith(STATIC)) { res.writeHead(404).end(); return; }
    try {
      const body = await readFile(path);
      const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".webmanifest": "application/manifest+json", ".glb": "model/gltf-binary" };
      res.writeHead(200, { "content-type": types[extname(path)] || "application/octet-stream", "cache-control": "no-store" });
      res.end(body);
    } catch { res.writeHead(404).end(); }
    return;
  }
  if (url.pathname === "/" || url.pathname === "/ui") { res.writeHead(302, { location: "/ui/" }); res.end(); return; }
  if (url.pathname === "/api/v1/device") {
    if (!state.deviceOk) { json(res, { error: { code: "device_identity_unavailable" } }, 503); return; }
    json(res, { product: "Piano Core", device_id: "6f0a2c1e-0000-4000-8000-5bbcd1a2e3f4", display_code: "PC7X-2049", identity_schema: "piano-core-identity-v1" });
    return;
  }
  if (url.pathname === "/api/v1/health") {
    json(res, { status: state.libraryReady ? "ok" : "degraded", application: { state: "READY", since: "t" }, midi_input: { connection: state.midi }, library: { ready: state.libraryReady, entries: state.libraryReady ? songs.length : null }, playback });
    return;
  }
  if (url.pathname === "/api/v1/state") {
    if (state.delayStateMs) await sleep(state.delayStateMs);
    json(res, { schema_version: 1, midi_connection: state.midi, sustain: { pressed: false, value: 0 }, pressed_notes: [...state.held].sort((a, b) => a - b), last_input: state.held.size ? { type: "NOTE_ON", channel: 0, timestamp: "t", note: [...state.held][0], value: 100 } : null, revision: 1 });
    return;
  }
  if (url.pathname === "/api/v1/led") { json(res, ledPayload()); return; }
  if (url.pathname === "/api/v1/learning" || url.pathname === "/api/v1/practice") { json(res, learning); return; }
  if (url.pathname === "/api/v1/playback") { json(res, playback); return; }
  if (url.pathname === "/api/v1/live-notes") { json(res, live()); return; }
  if (url.pathname === "/api/v1/live-notes/stream") {
    res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache", connection: "close" });
    res.write(`event: snapshot\ndata: ${JSON.stringify(live())}\n\n`);
    sseClients.add(res);
    const hb = setInterval(() => { try { res.write(": heartbeat\n\n"); } catch {} }, 2000);
    req.on("close", () => { clearInterval(hb); sseClients.delete(res); });
    return;
  }
  if (url.pathname === "/api/v1/library/songs") { json(res, { query: {}, count: songs.length, songs }); return; }
  const songMatch = url.pathname.match(/^\/api\/v1\/library\/songs\/(.+)$/);
  if (songMatch) { json(res, songs[0]); return; }
  if (url.pathname === "/api/v1/score/song-x") { json(res, score); return; }
  if (url.pathname === "/api/v1/fingering/song-x") { json(res, plan); return; }
  json(res, { error: { code: "not_found", message: "nf" } }, 404);
});
function ledPayload() { return { ...state.led, available: state.led.enabled && state.led.configured && state.led.connection !== "UNAVAILABLE" }; }

await new Promise((d) => server.listen(0, "127.0.0.1", d));
const appPort = server.address().port;
const probe = createServer();
await new Promise((d) => probe.listen(0, "127.0.0.1", d));
const debugPort = probe.address().port;
await new Promise((d) => probe.close(d));
const profile = await mkdtemp(join(tmpdir(), "piano-ui008-p6-chrome-"));
const chrome = spawn(CHROME, [
  "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
  `--remote-debugging-port=${debugPort}`, `--user-data-dir=${profile}`,
  `http://127.0.0.1:${appPort}/ui/#/home`,
], { stdio: ["ignore", "ignore", "pipe"] });

const fetchJson = async (u) => { for (let i = 0; i < 120; i += 1) { try { return await (await fetch(u)).json(); } catch { await sleep(50); } } throw new Error(u); };
const targets = await fetchJson(`http://127.0.0.1:${debugPort}/json/list`);
const ws = new WebSocket(targets.find((t) => t.type === "page").webSocketDebuggerUrl);
await new Promise((d, f) => { ws.once("open", d); ws.once("error", f); });
let cdpId = 0;
const pending = new Map();
ws.on("message", (raw) => {
  const m = JSON.parse(raw);
  if (m.method === "Runtime.exceptionThrown") pageErrors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
  if (!m.id || !pending.has(m.id)) return;
  const h = pending.get(m.id); pending.delete(m.id);
  m.error ? h.fail(new Error(m.error.message)) : h.done(m.result);
});
const cdp = (method, params = {}) => new Promise((done, fail) => { const i = ++cdpId; pending.set(i, { done, fail }); ws.send(JSON.stringify({ id: i, method, params })); });
async function evaluate(expression) {
  const r = await cdp("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + " :: " + String(expression).slice(0, 100));
  return r.result.value;
}
async function waitFor(expression, timeout = 15000) {
  const dl = Date.now() + timeout;
  while (Date.now() < dl) { if (await evaluate(expression)) return true; await sleep(40); }
  return false;
}
async function viewport(w, h) { await cdp("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: false }); await sleep(200); }
async function route(n) { await evaluate(`location.hash = '#/${n}'`); await waitFor(`document.body.dataset.ui008Route === '${n}'`); }
async function capture(name) {
  if (!EVIDENCE_DIR) return;
  await evaluate("new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
  await sleep(280);
  await mkdir(EVIDENCE_DIR, { recursive: true });
  const shot = await cdp("Page.captureScreenshot", { format: "png", fromSurface: true });
  await writeFile(join(EVIDENCE_DIR, name), Buffer.from(shot.data, "base64"));
}
const SURFACES = ["home", "library", "player", "learn", "piano", "settings"];

try {
await cdp("Runtime.enable");
await cdp("Page.enable");
try {
  await waitFor("document.documentElement?.dataset.ui008ShellReady === 'true'");
  await waitFor("document.getElementById('ui008-home')?.hidden === false", 15000);
} catch (error) {
  console.error("BOOT FAILURE — collected page errors:");
  console.error(pageErrors.join("\n---\n"));
  throw error;
}

/* ---------------- integrated long-session loop (P6-R) ---------------- */

await test("long session: six full surface cycles with interactions stay stable", async () => {
  await viewport(1024, 768);
  // one-time setup: select the song so Player/Learn have truth everywhere
  await route("library");
  await evaluate("document.querySelectorAll('.btn-song')[0].click()");
  await waitFor("document.getElementById('btn-practice')?.disabled === false", 8000);
  const before = await evaluate(`(() => ({
    keyboards: document.querySelectorAll('#live-keyboard').length,
    canvases: document.querySelectorAll('canvas').length,
    pianoCards: document.querySelectorAll('#piano-status').length,
    settingsCards: document.querySelectorAll('#ui008-settings').length,
    domNodes: document.getElementsByTagName('*').length,
    deviceFetches: performance.getEntriesByType('resource').filter(e => e.name.endsWith('/api/v1/device')).length,
  }))()`);
  let ledToggles = 0;
  for (let cycle = 0; cycle < 6; cycle += 1) {
    await route("player");
    await evaluate(`document.getElementById('visual-mode-${["sheet", "falling", "hybrid"][cycle % 3]}').click()`);
    await sleep(90);
    await route("learn");
    if (cycle === 0) { await evaluate("document.getElementById('btn-practice').click()"); await waitFor("document.getElementById('ui008-learn-state').textContent === 'Your turn'", 8000); }
    await route("piano");
    if (cycle === 1) { await evaluate("document.getElementById('led-enabled').click()"); ledToggles += 1; await sleep(500); await evaluate("document.getElementById('led-enabled').click()"); ledToggles += 1; await sleep(500); }
    await route("settings");
    if (cycle === 2) { await evaluate("document.getElementById('settings-hands-enabled').click()"); await sleep(150); await evaluate("document.getElementById('settings-hands-enabled').click()"); await sleep(150); }
    await route("home");
    await sleep(120);
  }
  // The accepted mutex means Player Play is DISABLED while Practice waits:
  // end the lesson first, then exercise the transport (order is the
  // product truth, not a test convenience).
  await route("learn");
  await evaluate("document.getElementById('btn-practice-stop').click()");
  await waitFor("document.getElementById('ui008-learn-state').textContent === 'Ready'", 8000);
  await route("player");
  await waitFor("document.getElementById('btn-play').disabled === false", 10000);
  await evaluate("document.getElementById('btn-play').click()");
  await waitFor("document.getElementById('btn-pause').disabled === false", 8000);
  await evaluate("document.getElementById('btn-stop').click()");
  const after = await evaluate(`(() => ({
    keyboards: document.querySelectorAll('#live-keyboard').length,
    canvases: document.querySelectorAll('canvas').length,
    pianoCards: document.querySelectorAll('#piano-status').length,
    settingsCards: document.querySelectorAll('#ui008-settings').length,
    domNodes: document.getElementsByTagName('*').length,
    deviceFetches: performance.getEntriesByType('resource').filter(e => e.name.endsWith('/api/v1/device')).length,
  }))()`);
  assert.equal(after.keyboards, before.keyboards);
  assert.equal(after.canvases, before.canvases);
  assert.equal(after.pianoCards, before.pianoCards);
  assert.equal(after.settingsCards, before.settingsCards);
  assert.ok(after.domNodes <= before.domNodes + 40, `DOM grew: ${before.domNodes} -> ${after.domNodes}`);
  assert.equal(after.deviceFetches, 1, "device identity was re-fetched without a reconnect");
  assert.equal(state.posts.play, 1, "play not exactly once");
  assert.equal(state.posts.stop, 1, "stop not exactly once");
  assert.equal(state.posts.start, 1, "learn start not exactly once");
  assert.equal(state.posts.learningStop, 1, "learn stop not exactly once");
  assert.equal(state.puts.led.length, 2, "led toggles not exactly twice");
});

/* ---------------- phone 390x844 — every surface (P6-H) ---------------- */

await test("phone 390x844: all six surfaces clean; toast clears bottom nav", async () => {
  await viewport(390, 844);
  const problems = [];
  for (const name of SURFACES) {
    await route(name);
    const m = await evaluate(`(() => {
      const de = document.documentElement;
      const nav = document.querySelector('.bottom-nav').getBoundingClientRect();
      const toast = document.getElementById('toast');
      const toastVisible = toast && !toast.hidden;
      return {
        overflowX: Math.max(de.scrollWidth - de.clientWidth, 0),
        navH: Math.round(nav.height),
        toastOverlap: toastVisible && toast.getBoundingClientRect().bottom > nav.top,
        keyboardH: Math.round(document.getElementById('live-keyboard').getBoundingClientRect().height),
      };
    })()`);
    if (m.overflowX > 1) problems.push(`${name}: overflowX ${m.overflowX}`);
    if (m.navH <= 0) problems.push(`${name}: bottom nav missing`);
    if (name === "piano" && m.keyboardH <= 0) problems.push(`${name}: keyboard collapsed`);
  }
  // force a toast on the piano route and verify it cannot hide behind the nav
  await route("piano");
  await evaluate("document.getElementById('btn-led-clear').click()");
  await waitFor("document.getElementById('toast').hidden === false", 8000);
  const overlap = await evaluate(`(() => {
    const nav = document.querySelector('.bottom-nav').getBoundingClientRect();
    const t = document.getElementById('toast').getBoundingClientRect();
    return t.bottom > nav.top;
  })()`);
  if (overlap) problems.push("toast overlaps bottom nav");
  assert.deepEqual(problems, [], JSON.stringify(problems));
});

/* ---------------- tablet landscape 1024x768 — primary target (P6-I) ---------------- */

await test("tablet 1024x768: primary action visible on every surface", async () => {
  await viewport(1024, 768);
  const problems = [];
  const probes = {
    home: "document.querySelector('.home-quick-card').getBoundingClientRect()",
    library: "document.getElementById('search').getBoundingClientRect()",
    player: "document.getElementById('btn-play').getBoundingClientRect()",
    settings: "document.getElementById('settings-visual-mode').getBoundingClientRect()",
  };
  for (const name of SURFACES) {
    await route(name);
    if (name === "learn") {
      // primary control depends on state: Start (ready) or Stop (waiting);
      // the state chip itself is a text status, not a touch target.
      const m = await evaluate(`(() => {
        const de = document.documentElement;
        const chip = document.getElementById('ui008-learn-state').getBoundingClientRect();
        const start = document.getElementById('btn-practice');
        const stop = document.getElementById('btn-practice-stop');
        const control = (start && !start.hidden && !start.disabled) ? start.getBoundingClientRect() : (stop && !stop.disabled ? stop.getBoundingClientRect() : (chip.top >= 0 ? chip : start.getBoundingClientRect()));
        return { overflowX: Math.max(de.scrollWidth - de.clientWidth, 0), top: Math.round(control.top), h: Math.round(control.height), inView: control.top >= 0 && control.top < innerHeight, controlH: Math.round(control.height) };
      })()`);
      if (m.overflowX > 1) problems.push(`learn: overflowX ${m.overflowX}`);
      if (!m.inView) problems.push(`learn: primary control below the fold (top ${m.top})`);
      if (m.controlH > 0 && m.controlH < 44) problems.push(`learn: primary control ${m.controlH}px`);
      continue;
    }
    if (name === "piano") {
      const m = await evaluate(`(() => {
        const de = document.documentElement;
        const chip = document.getElementById('piano-state').getBoundingClientRect();
        const sw = document.getElementById('led-enabled').getBoundingClientRect();
        return { overflowX: Math.max(de.scrollWidth - de.clientWidth, 0), top: Math.round(chip.top), inView: chip.top >= 0 && chip.top < innerHeight, swH: Math.round(sw.height) };
      })()`);
      if (m.overflowX > 1) problems.push(`piano: overflowX ${m.overflowX}`);
      if (!m.inView) problems.push(`piano: connection status below the fold (top ${m.top})`);
      if (m.swH > 0 && m.swH < 44) problems.push(`piano: LED switch ${m.swH}px`);
      continue;
    }
    const m = await evaluate(`(() => {
      const de = document.documentElement;
      const r = ${probes[name]};
      return { overflowX: Math.max(de.scrollWidth - de.clientWidth, 0), top: Math.round(r.top), h: Math.round(r.height), inView: r.top >= 0 && r.top < innerHeight };
    })()`);
    if (m.overflowX > 1) problems.push(`${name}: overflowX ${m.overflowX}`);
    if (!m.inView) problems.push(`${name}: primary control below the fold (top ${m.top})`);
    if (m.h > 0 && m.h < 44) problems.push(`${name}: primary control ${m.h}px`);
  }
  await route("piano");
  const kb = await evaluate("Math.round(document.getElementById('live-keyboard').getBoundingClientRect().height)");
  if (kb < 100) problems.push(`piano keyboard only ${kb}px at tablet landscape`);
  assert.deepEqual(problems, [], JSON.stringify(problems));
});

/* ---------------- tablet portrait 768x1024 (P6-J) ---------------- */

await test("tablet portrait 768x1024: intentional reflow without overflow", async () => {
  await viewport(768, 1024);
  const problems = [];
  for (const name of SURFACES) {
    await route(name);
    const m = await evaluate(`(() => {
      const de = document.documentElement;
      return { overflowX: Math.max(de.scrollWidth - de.clientWidth, 0), bottomNav: document.querySelector('.bottom-nav').getBoundingClientRect().height > 0 };
    })()`);
    if (m.overflowX > 1) problems.push(`${name}: overflowX ${m.overflowX}`);
    if (!m.bottomNav) problems.push(`${name}: bottom nav missing (portrait must not stretch desktop rail)`);
  }
  assert.deepEqual(problems, [], JSON.stringify(problems));
});

/* ---------------- desktop 1440x900 / 1920x1080 (P6-K) ---------------- */

await test("desktop 1440x900 and 1920x1080: breathing room, no extra chrome", async () => {
  const problems = [];
  for (const [w, h] of [[1440, 900], [1920, 1080]]) {
    await viewport(w, h);
    for (const name of ["player", "learn", "piano"]) {
      await route(name);
      const m = await evaluate(`(() => {
        const de = document.documentElement;
        const kb = document.getElementById('live-keyboard').getBoundingClientRect();
        return { overflowX: Math.max(de.scrollWidth - de.clientWidth, 0), kbW: Math.round(kb.width) };
      })()`);
      if (m.overflowX > 1) problems.push(`${w}/${name}: overflowX ${m.overflowX}`);
      if (name === "piano" && m.kbW < 900) problems.push(`${w}/piano: keyboard only ${m.kbW}px wide (space not used for the instrument)`);
    }
  }
  await viewport(1024, 768);
  assert.deepEqual(problems, [], JSON.stringify(problems));
});

/* ---------------- text-scale resilience proxy (P6-Q, bounded) ---------------- */

await test("text-scale proxy: +25% root font size keeps phone surfaces usable", async () => {
  await viewport(390, 844);
  await evaluate("document.documentElement.style.fontSize = '20px'");
  const problems = [];
  for (const name of SURFACES) {
    await route(name);
    const overflowX = await evaluate("Math.max(document.documentElement.scrollWidth - document.documentElement.clientWidth, 0)");
    if (overflowX > 1) problems.push(`${name}: overflowX ${overflowX} at 20px root`);
  }
  await evaluate("document.documentElement.style.fontSize = ''");
  assert.deepEqual(problems, [], JSON.stringify(problems));
});

/* ---------------- recovery consistency (P6-S) ---------------- */

await test("recovery: stale, reconnect, MIDI loss and LED loss stay calm and truthful", async () => {
  state.delayStateMs = 6000;
  await route("piano");
  await waitFor("document.getElementById('piano-state').textContent === 'Reconnecting'", 15000);
  state.delayStateMs = 0;
  await waitFor("document.getElementById('piano-state').textContent === 'Piano connected'", 15000);
  assert.equal(await evaluate("document.getElementById('piano-state').textContent"), "Piano connected");
  state.midi = "DISCONNECTED";
  await waitFor("document.getElementById('piano-state').textContent === 'Piano disconnected'", 10000);
  const banned = /CONNECTED|DISCONNECTED|StateStore|endpoint|mutex|127\.0\.0\.1/;
  const pianoText = await evaluate("document.getElementById('piano-status').textContent");
  assert.ok(!banned.test(pianoText), `leak: ${pianoText}`);
  state.midi = "CONNECTED";
  await waitFor("document.getElementById('piano-state').textContent === 'Piano connected'", 10000);
  state.led.enabled = false;
  await waitFor("document.getElementById('led-chip').textContent === 'Lights off'", 10000);
  state.led.enabled = true;
  await waitFor("document.getElementById('led-chip').textContent === 'Guidance lights ready'", 10000);
  state.libraryReady = false;
  await waitFor("document.getElementById('library-summary').textContent.includes('unavailable')", 12000);
  state.libraryReady = true;
  await waitFor("document.getElementById('library-summary').textContent.includes('songs in the library')", 12000);
});

/* ---------------- final evidence captures (P6-U) ---------------- */

await test("final visual evidence: 16 polished captures", async () => {
  await sleep(4200); // retire toasts
  await viewport(1024, 768);
  setHeld([]);
  await route("home"); await sleep(350);
  await capture("p6-home-tablet.png");
  await route("library"); await sleep(350);
  await capture("p6-library-tablet.png");
  await route("player"); await sleep(500);
  await capture("p6-player-tablet.png");
  await evaluate("document.getElementById('ui008-performance-btn').click()");
  await waitFor("document.body.dataset.ui008Performance === 'on'", 8000);
  await sleep(400);
  await capture("p6-player-performance.png");
  await evaluate("document.getElementById('ui008-performance-btn').click()");
  await waitFor("document.body.dataset.ui008Performance === 'off'", 8000);
  await route("learn"); await sleep(400);
  await capture("p6-learn-tablet.png");
  await evaluate("document.getElementById('btn-practice').click()");
  await waitFor("document.getElementById('ui008-learn-state').textContent === 'Your turn'", 8000);
  await sleep(300);
  await capture("p6-learn-active-target.png");
  await evaluate("document.getElementById('btn-practice-stop').click()");
  await waitFor("document.getElementById('ui008-learn-state').textContent === 'Ready'", 8000);
  setHeld([60, 67]);
  await route("piano"); await sleep(600);
  await capture("p6-piano-tablet.png");
  setHeld([]);
  await route("settings"); await sleep(350);
  await capture("p6-settings-tablet.png");
  await viewport(390, 844);
  await route("home"); await sleep(350);
  await capture("p6-phone-home.png");
  await route("player"); await sleep(400);
  await capture("p6-phone-player.png");
  await route("learn"); await sleep(350);
  await capture("p6-phone-learn.png");
  await route("piano"); await sleep(350);
  await capture("p6-phone-piano.png");
  await route("settings"); await sleep(350);
  await capture("p6-phone-settings.png");
  await viewport(1440, 900);
  await route("player"); await sleep(500);
  await capture("p6-desktop-player.png");
  await route("learn"); await sleep(400);
  await capture("p6-desktop-learn.png");
  // offline/reconnecting state (on the Piano surface, where the chip shows)
  await viewport(1024, 768);
  await route("piano");
  state.delayStateMs = 6000;
  await waitFor("document.getElementById('piano-state').textContent === 'Reconnecting'", 15000);
  await sleep(200);
  await capture("p6-offline-reconnecting.png");
  state.delayStateMs = 0;
  await waitFor("document.getElementById('piano-state').textContent === 'Piano connected'", 15000);
  await sleep(9000); // drain any delayed poll before exit
});

} finally {
  ws.close();
  chrome.kill("SIGTERM");
  await new Promise((d) => server.close(d));
  await Promise.race([new Promise((d) => chrome.once("exit", d)), sleep(2000)]);
  await rm(profile, { recursive: true, force: true });
}

if (failures) {
  console.error(`FAIL ${failures}; PASS ${passes}`);
  process.exit(1);
}
console.log(`PASS ${passes}/${passes}`);
console.log(`P6_MEASURES ${JSON.stringify({ captures: EVIDENCE_DIR ? 16 : 0 })}`);
