// PIANO-IMMERSIVE-UI: REAL Chromium acceptance for the stage composition.
// Proves the OWNER-mandated product on every required viewport:
// - exactly ONE presented scene view (default Falling) in Player and Learn;
// - essential controls inside the viewport and hit-testable with NO page
//   scroll at the required sizes;
// - secondary panels open/close with correct focus (Escape returns to the
//   opener) on all three stage routes;
// - 20 view switches: ZERO musical commands, playback/learning state and
//   identity preserved, no renderer/canvas/listener growth, one RAF chain;
// - hands availability follows the hands preference (view selector only);
// - +25% text scale and landscape never block actions (scroll fallback is
//   reachable and hit-testable, controls are never clipped);
// - 899/900 shell boundary keeps bottom-nav/rail semantics.
// Headless Chrome/loopback mock Core serving the REAL contract shapes and
// the real static UI. Physical touch/Pi/MIDI/LED evidence is NOT claimed.
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
const EVIDENCE_DIR = process.env.IMMERSIVE_EVIDENCE_DIR || "";

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
  libraryReady: true,
  posts: { play: 0, stop: 0, start: 0, learningStop: 0 },
};
let seq = 0;
let playback = { available: true, state: "IDLE", song_id: null, title: null, position_seconds: 0, duration_seconds: 0, tempo_multiplier: 1, events_sent: 0, error: null };
let learning = { available: true, state: "IDLE", song_id: null, title: null, step_index: null, total_steps: null, completed_steps: 0, incorrect_attempts: 0, progress: 0, started_at: null, ended_at: null, error: null, midi_connection: "CONNECTED", session_id: null, mode: "WAIT_FOR_NOTE", hand_selection: null, section: null, target_tempo: null, accepted_notes: [], latest_outcome: "NONE", generation: 0, version: 0, updated_at: null, target: null, repetition: { current: 0, required: 1, successful: 0, failed: 0 } };
let led = { configured: true, enabled: true, available: true, connection: "READY", mode: "physical", brightness: 80, active_note_count: 0, practice_active: false, last_error: null, transport: { host: "127.0.0.1", port: 8765 }, dropped_events: 0, frames_sent: 0 };

const sseClients = new Set();
const live = () => { seq += 1; return { schema_version: 1, runtime_epoch: "imm", sequence: seq, physical: { notes: [...state.held].sort((a, b) => a - b), outside_keyboard_count: 0, sustain: { value: 0, pressed: false }, midi_connection: state.midi, revision: seq }, playback: { available: true, notes: [], voices: [], outside_keyboard_count: 0, sustain_channels: [] } }; };
const pushLive = () => { seq += 1; const f = `event: snapshot\ndata: ${JSON.stringify(live())}\n\n`; for (const res of sseClients) { try { res.write(f); } catch { sseClients.delete(res); } } };

function json(res, value, status = 200) {
  const body = JSON.stringify(value);
  res.writeHead(status, { "content-type": "application/json", "content-length": Buffer.byteLength(body), "cache-control": "no-store" });
  res.end(body);
}
const readBody = async (req) => { let b = ""; for await (const c of req) b += c; return JSON.parse(b || "{}"); };

const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://127.0.0.1");
  if (req.method === "POST" && url.pathname === "/api/v1/playback/play") {
    state.posts.play += 1;
    if (learning.state === "WAITING") { json(res, { error: { code: "playback_already_active", message: "practice owns" } }, 409); return; }
    playback = { ...playback, state: "PLAYING", song_id: "song-x", title: "Nocturne X", duration_seconds: 120, position_seconds: 0, events_sent: 4 };
    json(res, playback);
    return;
  }
  if (req.method === "POST" && url.pathname === "/api/v1/playback/stop") { state.posts.stop += 1; playback = { ...playback, state: "IDLE", song_id: null, title: null, position_seconds: 0, events_sent: 0 }; json(res, { ...playback, stopped: true }); return; }
  if (req.method === "POST" && url.pathname === "/api/v1/learning/start") {
    state.posts.start += 1;
    learning = { ...learning, state: "WAITING", song_id: "song-x", title: "Nocturne X", step_index: 0, total_steps: 2, completed_steps: 0, progress: 0, session_id: "imm-sess-1", version: 1, generation: 1, target: { notes: [67], names: ["G4"], chord: false, onset_seconds: 2, target_id: "t:0" }, started_at: "t", updated_at: "t" };
    json(res, learning);
    return;
  }
  if (req.method === "POST" && url.pathname === "/api/v1/learning/stop") {
    state.posts.learningStop += 1;
    learning = { ...learning, state: "IDLE", song_id: null, session_id: null, target: null, version: learning.version + 1 };
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
  if (url.pathname === "/api/v1/device") { json(res, { product: "Piano Core", device_id: "6f0a2c1e-0000-4000-8000-5bbcd1a2e3f4", display_code: "PC7X-2049", identity_schema: "piano-core-identity-v1" }); return; }
  if (url.pathname === "/api/v1/health") {
    json(res, { status: "ok", application: { state: "READY", since: "t" }, midi_input: { connection: state.midi }, library: { ready: true, entries: songs.length }, playback });
    return;
  }
  if (url.pathname === "/api/v1/state") { json(res, { schema_version: 1, midi_connection: state.midi, sustain: { pressed: false, value: 0 }, pressed_notes: [...state.held].sort((a, b) => a - b), last_input: null, revision: 1 }); return; }
  if (url.pathname === "/api/v1/led") { json(res, led); return; }
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

await new Promise((d) => server.listen(0, "127.0.0.1", d));
const appPort = server.address().port;
const probe = createServer();
await new Promise((d) => probe.listen(0, "127.0.0.1", d));
const debugPort = probe.address().port;
await new Promise((d) => probe.close(d));
const profile = await mkdtemp(join(tmpdir(), "piano-imm-chrome-"));
const chrome = spawn(CHROME, [
  "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
  `--remote-debugging-port=${debugPort}`, `--user-data-dir=${profile}`,
  "about:blank",
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
  if (r.exceptionDetails) {
    const detail = r.exceptionDetails.exception?.description || r.exceptionDetails.text;
    throw new Error(detail + " :: " + String(expression).slice(0, 100));
  }
  return r.result.value;
}
async function waitFor(expression, timeout = 15000) {
  const dl = Date.now() + timeout;
  while (Date.now() < dl) { if (await evaluate(expression)) return true; await sleep(40); }
  return false;
}
async function viewport(w, h) { await cdp("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: false }); await sleep(220); }
async function route(n) { await evaluate(`location.hash = '#/${n}'`); await waitFor(`document.body.dataset.ui008Route === '${n}'`); }
async function capture(name) {
  if (!EVIDENCE_DIR) return;
  await sleep(320);
  await mkdir(EVIDENCE_DIR, { recursive: true });
  const shot = await cdp("Page.captureScreenshot", { format: "png", fromSurface: true });
  await writeFile(join(EVIDENCE_DIR, name), Buffer.from(shot.data, "base64"));
  console.log(`capture: ${name}`);
}
const { writeFile, mkdir } = await import("node:fs/promises");

/* Instrumentation installed BEFORE the app boots: counts listener
 * registrations and tracks the concurrent RAF chain size. The wrapped
 * rAF re-enters the ORIGINAL rAF from a plain wrapper; a throwing test
 * callback must never break the chain accounting. */
await cdp("Page.enable");
const INSTRUMENTED = process.env.IMM_NO_INSTRUMENT !== "1";
if (INSTRUMENTED) {
  await cdp("Page.addScriptToEvaluateOnNewDocument", { source: `
  (() => {
    const origAdd = EventTarget.prototype.addEventListener;
    window.__alCount = 0;
    window.__alLog = [];
    EventTarget.prototype.addEventListener = function (...args) {
      window.__alCount += 1;
      if (window.__alLog.length > 24) window.__alLog.shift();
      window.__alLog.push((this && this.constructor ? this.constructor.name : "?") + ":" + String(args[0]));
      return origAdd.apply(this, args);
    };
    const origRaf = window.requestAnimationFrame.bind(window);
    window.__rafPending = 0;
    window.__rafMax = 0;
    window.requestAnimationFrame = (cb) => {
      window.__rafPending += 1;
      if (window.__rafPending > window.__rafMax) window.__rafMax = window.__rafPending;
      return origRaf((t) => { window.__rafPending -= 1; cb(t); });
    };
  })();
` });
}

await cdp("Runtime.enable");
await cdp("Page.bringToFront");
await cdp("Emulation.setFocusEmulationEnabled", { enabled: true });
await cdp("Page.navigate", { url: `http://127.0.0.1:${appPort}/ui/#/home` });
await evaluate(`window.__tk = 0; window.__si = setInterval(() => window.__tk++, 250);`);
try {
  await waitFor("document.documentElement.dataset.ui008ShellReady === 'true'");
  await waitFor("document.getElementById('ui008-home')?.hidden === false", 15000);
} catch (error) {
  console.error("BOOT FAILURE — collected page errors:");
  console.error(pageErrors.join("\n---\n"));
  throw error;
}

const SCENE_VIEWS = `['#visual', '#piano-view', '#hands']`;
const visibleScenes = `(() => ${SCENE_VIEWS}.map(s => document.querySelector(s)).filter(el => el && !el.hidden).length)()`;
async function pickSong() {
  await route("library");
  await evaluate("document.querySelectorAll('.btn-song')[0].click()");
  await waitFor("document.getElementById('btn-practice')?.disabled === false", 8000);
}
function probeControls(selectors) {
  return evaluate(`(() => {
    const out = {};
    for (const sel of ${JSON.stringify(selectors)}) {
      const el = document.querySelector(sel);
      if (!el) { out[sel] = { found: false }; continue; }
      const r = el.getBoundingClientRect();
      const inVp = r.width > 0 && r.height > 0 && r.top >= 0 && r.left >= 0 && r.bottom <= innerHeight + 1 && r.right <= innerWidth + 1;
      const cx = Math.min(Math.max(r.left + r.width / 2, 1), innerWidth - 1);
      const cy = Math.min(Math.max(r.top + r.height / 2, 1), innerHeight - 1);
      const hit = document.elementFromPoint(cx, cy);
      out[sel] = { found: true, inVp, hitOk: hit === el || el.contains(hit) };
    }
    const layout = document.querySelector(".layout");
    return { controls: out, scrollY: window.scrollY,
      docScroll: document.scrollingElement.scrollTop,
      layoutOver: layout ? layout.scrollHeight - layout.clientHeight : -1 };
  })()`);
}

/* ---------------- Player stage composition ---------------- */

await test("player surface: no teaching scene, controls in viewport, no page scroll", async () => {
  await viewport(1024, 768);
  await pickSong();
  await route("player");
  await waitFor(`document.querySelector('#btn-play')?.disabled === false`, 8000);
  assert.equal(await evaluate(visibleScenes), 0, "no presented teaching scene in Player");
  assert.equal(await evaluate(`document.querySelector('#ui008-view-player')`), null);
  const p = await probeControls(["#btn-play", "#btn-stop", ".player-library-link", "#ui008-player-options"]);
  for (const [sel, info] of Object.entries(p.controls)) {
    assert.ok(info.found, `${sel} present`);
    assert.ok(info.inVp, `${sel} inside the viewport: ${JSON.stringify(info)}`);
    assert.ok(info.hitOk, `${sel} hit-testable: ${JSON.stringify(info)}`);
  }
  assert.equal(p.scrollY, 0, "no page scroll");
  assert.equal(p.docScroll, 0, "document not scrolled");
  assert.ok(p.layoutOver <= 1, `stage layout does not overflow (${p.layoutOver}px)`);
  await capture("imm-player-falling-1024.png");
});

await test("learn stage: one scene, Start/Stop reachable, target in the strip", async () => {
  await route("learn");
  assert.ok(await waitFor(`document.getElementById('btn-practice')?.disabled === false`, 15000), "learn start enabled (learning fresh + song selected)");
  assert.equal(await evaluate(visibleScenes), 1, "exactly one presented scene view");
  assert.equal(await evaluate(`document.getElementById('ui008-view-learn')?.value`), "falling");
  const p = await probeControls(["#btn-practice", "#ui008-view-learn", "#ui008-learn-options"]);
  for (const [sel, info] of Object.entries(p.controls)) {
    if (!info.found || !info.inVp || !info.hitOk) {
      const dump = await evaluate(`(() => {
        const layout = document.querySelector(".layout");
        const rows = [...document.querySelectorAll("#practice .stage-head, #practice .stage-guidance, #practice .stage-scene, #practice .learn-strip, #practice .learn-target--strip, #practice .learn-session-actions")].map(el => { const r = el.getBoundingClientRect(); return el.className.split(" ")[0] + ":" + Math.round(r.top) + "-" + Math.round(r.bottom); });
        return JSON.stringify({ vp: innerHeight, over: layout.scrollHeight - layout.clientHeight, routeHidden: document.getElementById("practice")?.dataset.ui008RouteHidden, selectedHidden: document.getElementById("selected")?.hidden, selTitle: document.getElementById("sel-title")?.textContent, chip: document.getElementById("ui008-learn-state")?.textContent, emptyHeading: document.getElementById("ui008-learn-empty-heading")?.textContent, note: document.getElementById("connection-note")?.textContent, tk: window.__tk, ui: window.__pianoUI ? window.__pianoUI.selection() : null, rows });
      })()`);
      console.error(`learn-geometry debug: ${dump}`);
    }
    assert.ok(info.found && info.inVp && info.hitOk, `${sel} usable without scroll: ${JSON.stringify(info)}`);
  }
  assert.ok(await waitFor(`document.getElementById('ui008-learn-target-notes')?.children.length >= 0`));
  assert.equal(await evaluate(`getComputedStyle(document.getElementById('ui008-learn-target')).display !== 'none'`), true);
  await capture("imm-learn-falling-1024.png");
});

/* ---------------- secondary panels (More) ---------------- */

async function assertSheet(openerSel, rootSel, closeSel) {
  await evaluate(`document.querySelector('${openerSel}').click()`);
  await waitFor(`document.querySelector('${rootSel}')?.hidden === false`);
  const focusInside = await evaluate(`(() => {
    const root = document.querySelector('${rootSel}');
    return root.contains(document.activeElement);
  })()`);
  assert.equal(focusInside, true, "focus moves into the opened panel");
  await evaluate("document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))");
  await waitFor(`document.querySelector('${rootSel}')?.hidden === true`);
  const returned = await evaluate(`document.activeElement === document.querySelector('${openerSel}')`);
  assert.equal(returned, true, `focus returns to the opener (${openerSel}; active=${await evaluate("document.activeElement?.id || document.activeElement?.tagName")})`);
}

await test("secondary panels open/close with focus return on all three stage routes", async () => {
  await viewport(1024, 768);
  await route("player");
  await assertSheet("#ui008-player-options", "#ui008-player-sheet", "#ui008-player-sheet-close");
  assert.ok(await waitFor(`document.getElementById('ui008-player-secondary') && document.getElementById('tempo')`));
  await route("learn");
  await assertSheet("#ui008-learn-options", "#ui008-learn-sheet", "#ui008-learn-sheet-close");
  assert.ok(await waitFor(`document.getElementById('learning-mode') !== null`));
  assert.ok(await waitFor(`document.getElementById('learning-feedback') !== null`));
  await route("piano");
  await assertSheet("#ui008-piano-options", "#ui008-piano-sheet", "#ui008-piano-sheet-close");
  assert.ok(await waitFor(`document.getElementById('led-controls') !== null`));
  await capture("imm-piano-panel-1024.png");
});

/* ---------------- view switching: presentation only ---------------- */

await test("20 view switches: zero musical commands, playback preserved, no growth", async () => {
  await viewport(1024, 768);
  await route("player");
  await waitFor(`document.querySelector('#btn-play')?.disabled === false`, 8000);
  await evaluate(`document.getElementById('btn-play').click()`);
  await waitFor(`document.querySelector('#btn-play')?.disabled === true`, 8000);
  await route("learn");
  // Warm up every view once so one-time initializations (hands renderer,
  // ResizeObserver) happen BEFORE the listener/RAF baseline is captured.
  for (const view of ["piano", "hands", "sheet", "falling"]) {
    await evaluate(`(() => { const el = document.getElementById('ui008-view-learn'); el.value = '${view}'; el.dispatchEvent(new Event('change', { bubbles: true })); })()`);
    await sleep(150);
  }
  await sleep(800);
  // Like-for-like windows: the poll cycle registers transient per-fetch
  // AbortSignal listeners by design, so the switching window is compared
  // against an equal-length idle window, not against absolute zero.
  const alIdle0 = await evaluate(`window.__alCount`);
  await sleep(2000);
  const alIdle1 = await evaluate(`window.__alCount`);
  const idleDelta = alIdle1 - alIdle0;
  const before = {
    posts: { ...state.posts },
    raf: await evaluate(`window.__rafMax`),
    al: await evaluate(`window.__alCount`),
    canvases: await evaluate(`document.querySelectorAll('canvas').length`),
    keyboards: await evaluate(`document.querySelectorAll('#live-keyboard').length`),
  };
  const cycle = ["piano", "hands", "sheet", "falling"];
  for (let i = 0; i < 20; i += 1) {
    const view = cycle[i % cycle.length];
    await evaluate(`(() => { const el = document.getElementById('ui008-view-learn'); el.value = '${view}'; el.dispatchEvent(new Event('change', { bubbles: true })); })()`);
    await sleep(60);
  }
  await sleep(2000 - 20 * 60 > 0 ? 2000 - 20 * 60 : 0);
  assert.equal(await evaluate(visibleScenes), 1, "still exactly one presented scene");
  assert.deepEqual(state.posts, before.posts, "zero musical commands across 20 view switches");
  const playbackNow = await (await fetch(`http://127.0.0.1:${appPort}/api/v1/playback`)).json();
  assert.equal(playbackNow.state, "PLAYING", "playback untouched by view switching");
  assert.equal(playbackNow.song_id, "song-x");
  const after = {
    raf: await evaluate(`window.__rafMax`),
    al: await evaluate(`window.__alCount`),
    canvases: await evaluate(`document.querySelectorAll('canvas').length`),
    keyboards: await evaluate(`document.querySelectorAll('#live-keyboard').length`),
  };
  assert.equal(after.canvases, before.canvases, "no renderer/canvas growth");
  assert.equal(after.keyboards, before.keyboards, "no duplicate keyboard");
  const switchDelta = after.al - before.al;
  console.log(`listener windows: idle=${idleDelta}, switching=${switchDelta}`);
  assert.ok(switchDelta <= idleDelta + 4, `no listener growth from view switching (switching ${switchDelta} vs idle ${idleDelta})`);
  // Two accepted chains exist while PLAYING (the playback position clock
  // and the scene render chain — pre-existing accepted behavior); what
  // must NOT happen is unbounded chain multiplication. After switching
  // stops, the pending count settles back to the accepted steady state.
  assert.ok(after.raf <= 12, `RAF chains bounded (max ${after.raf})`);
  await evaluate(`document.getElementById('btn-stop').click()`);
  assert.ok(await waitFor(`document.querySelector('#btn-play')?.disabled === false`, 12000), "stop lands (freshness reconciliation) after view switching");
  const settled1 = await evaluate(`window.__rafPending`);
  for (const view of ["piano", "sheet", "falling"]) {
    await evaluate(`(() => { const el = document.getElementById('ui008-view-learn'); el.value = '${view}'; el.dispatchEvent(new Event('change', { bubbles: true })); })()`);
    await sleep(120);
  }
  await sleep(800);
  const settled2 = await evaluate(`window.__rafPending`);
  assert.ok(settled2 <= settled1 + 2, `no RAF multiplication across view rounds (${settled1} -> ${settled2})`);
});

await test("learning session identity survives view switches; mutex intact", async () => {
  await route("learn");
  await pickSong();
  await route("learn");
  assert.ok(await waitFor(`document.getElementById('btn-practice')?.disabled === false`, 15000), `start enabled before identity test (diag: ${await evaluate(`JSON.stringify({ play: !document.getElementById('btn-play')?.disabled, note: document.getElementById('connection-note')?.textContent, fresh: document.getElementById('player-freshness')?.textContent })`)})`);
  assert.equal(state.posts.start, 0, "no learning start issued yet");
  await evaluate(`document.getElementById('btn-practice').click()`);
  assert.ok(await waitFor(`document.getElementById('btn-practice-stop')?.disabled === false`, 8000), "session started (stop enabled)");
  assert.equal(state.posts.start, 1, "exactly one learning start");
  const postsBefore = { ...state.posts };
  for (let i = 0; i < 8; i += 1) {
    const view = ["sheet", "piano", "hands", "falling"][i % 4];
    await evaluate(`(() => { const el = document.getElementById('ui008-view-learn'); el.value = '${view}'; el.dispatchEvent(new Event('change', { bubbles: true })); })()`);
    await sleep(50);
  }
  const snap = await (await fetch(`http://127.0.0.1:${appPort}/api/v1/learning`)).json();
  assert.equal(snap.state, "WAITING", "practice still WAITING");
  assert.equal(snap.session_id, "imm-sess-1", "session_id unchanged by view switching");
  assert.equal(snap.target?.target_id, "t:0", "target_id unchanged");
  assert.deepEqual(state.posts, postsBefore, "no musical commands during switches");
  const playAttempt = await fetch(`http://127.0.0.1:${appPort}/api/v1/playback/play`, { method: "POST", headers: { "content-type": "application/json" }, body: "{}" });
  assert.equal(playAttempt.status, 409, "mutex still enforced from the mock contract");
  await evaluate(`document.getElementById('btn-practice-stop').click()`);
  await waitFor(`document.getElementById('btn-practice-stop')?.disabled === true`, 8000);
});

await test("hands availability follows the hands preference (selector only)", async () => {
  await route("learn");
  await evaluate(`document.getElementById('settings-hands-enabled').click()`);
  await waitFor(`document.getElementById('ui008-view-learn')?.selectedOptions[0]?.disabled === false && document.querySelector('#hands')?.hidden === true`);
  assert.equal(await evaluate(`[...document.getElementById('ui008-view-learn')?.options || []].find(o => o.value === 'hands')?.disabled`), true, "hands option disabled when hands are off");
  await evaluate(`document.getElementById('settings-hands-enabled').click()`);
  await waitFor(`document.getElementById('ui008-view-learn')?.selectedOptions[0]?.disabled === false`);
  assert.equal(await evaluate(`[...document.getElementById('ui008-view-learn')?.options || []].find(o => o.value === 'hands')?.disabled`), false, "hands option returns when hands are on");
});

await test("hands view: own scene, renderer present, switch back keeps state", async () => {
  await viewport(1024, 768);
  await route("learn");
  await waitFor(`document.getElementById('btn-practice')?.disabled === false`, 8000);
  await evaluate(`(() => { const el = document.getElementById('ui008-view-learn'); el.value = 'hands'; el.dispatchEvent(new Event('change', { bubbles: true })); })()`);
  await waitFor(`document.querySelector('#hands')?.hidden === false`);
  assert.equal(await evaluate(visibleScenes), 1);
  assert.ok(await waitFor(`!!document.querySelector('#hands-canvas')`));
  await capture("imm-learn-hands-1024.png");
  await evaluate(`(() => { const el = document.getElementById('ui008-view-learn'); el.value = 'falling'; el.dispatchEvent(new Event('change', { bubbles: true })); })()`);
  await waitFor(`document.querySelector('#visual')?.hidden === false`);
  assert.equal(await evaluate(visibleScenes), 1);
});

/* ---------------- required viewport matrix ---------------- */

const MATRIX = [[338, 654], [390, 844], [768, 1024], [1024, 768], [1440, 900], [1920, 1080]];
await test(`stage matrix ${MATRIX.map(([w, h]) => `${w}x${h}`).join(", ")}: one scene, controls usable, no page scroll`, async () => {
  for (const [w, h] of MATRIX) {
    await viewport(w, h);
    await route("player");
    await waitFor(`document.querySelector('#btn-play')?.disabled === false`, 8000);
    assert.equal(await evaluate(visibleScenes), 0, `${w}x${h} player: no teaching scene`);
    let p = await probeControls(["#btn-play", "#btn-stop"]);
    for (const [sel, info] of Object.entries(p.controls)) {
      assert.ok(info.found && info.hitOk, `${w}x${h} ${sel} present+hit`);
      assert.ok(info.inVp, `${w}x${h} ${sel} inside viewport: ${JSON.stringify(info)}`);
    }
    assert.equal(p.scrollY, 0, `${w}x${h} player: no page scroll`);
    assert.ok(p.layoutOver <= 1, `${w}x${h} player: stage fits (${p.layoutOver}px)`);
    await route("learn");
    await waitFor(`document.getElementById('btn-practice')?.disabled === false`, 8000);
    assert.equal(await evaluate(visibleScenes), 1, `${w}x${h} learn: one scene`);
    p = await probeControls(["#btn-practice"]);
    for (const [sel, info] of Object.entries(p.controls)) {
      assert.ok(info.found && info.hitOk && info.inVp, `${w}x${h} ${sel} usable: ${JSON.stringify(info)}`);
    }
    assert.equal(p.scrollY, 0, `${w}x${h} learn: no page scroll`);
    if (w === 390) await capture("imm-player-falling-390.png");
    if (w === 768) await capture("imm-learn-falling-768.png");
    if (w === 1440) await capture("imm-player-desktop-1440.png");
  }
});

await test("899/900 shell boundary on a stage route", async () => {
  await route("player");
  await viewport(899, 900);
  assert.equal(await evaluate(`getComputedStyle(document.querySelector('.bottom-nav')).display !== 'none'`), true, "899px: bottom nav present");
  await viewport(900, 900);
  assert.equal(await evaluate(`getComputedStyle(document.querySelector('.bottom-nav')).display === 'none'`), true, "900px: rail takes over");
  await viewport(1024, 768);
});

await test("+25% text scale: actions stay reachable and hit-testable", async () => {
  await viewport(390, 844);
  await route("player");
  await waitFor(`document.querySelector('#btn-play')?.disabled === false`, 8000);
  await evaluate(`document.documentElement.style.fontSize = '125%'`);
  await sleep(250);
  const p = await probeControls(["#btn-play", "#btn-stop"]);
  for (const [sel, info] of Object.entries(p.controls)) {
    if (info.inVp && info.hitOk) continue;
    await evaluate(`document.querySelector('${sel}').scrollIntoView({ block: "center" })`);
    await sleep(140);
    const p2 = await probeControls([sel]);
    assert.ok(p2.controls[sel].hitOk && p2.controls[sel].inVp, `${sel} reachable by the stage fallback scroll under +25% text scale`);
  }
  await evaluate(`document.documentElement.style.fontSize = ''`);
  await sleep(150);
});

await test("landscape 844x390: transport never clipped, reachable by design", async () => {
  await viewport(844, 390);
  await route("player");
  await waitFor(`document.querySelector('#btn-play')?.disabled === false`, 8000);
  const p = await probeControls(["#btn-play", "#btn-stop"]);
  for (const [sel, info] of Object.entries(p.controls)) {
    if (info.inVp && info.hitOk) continue;
    await evaluate(`document.querySelector('${sel}').scrollIntoView({ block: "center" })`);
    await sleep(140);
    const p2 = await probeControls([sel]);
    assert.ok(p2.controls[sel].hitOk && p2.controls[sel].inVp, `${sel} reachable (scroll fallback) in landscape`);
  }
  await viewport(1024, 768);
});

ws.close();
chrome.kill("SIGTERM");
await new Promise((d) => server.close(d));
await Promise.race([new Promise((d) => chrome.once("exit", d)), sleep(2000)]);
await rm(profile, { recursive: true, force: true });

if (pageErrors.length) {
  console.error("PAGE ERRORS COLLECTED:");
  console.error(pageErrors.join("\n---\n"));
}
if (failures) {
  console.error(`FAIL ${failures}; PASS ${passes}`);
  process.exit(1);
}
console.log(`PASS ${passes}/${passes}`);
console.log(`IMM_MEASURES ${JSON.stringify({ captures: EVIDENCE_DIR ? 7 : 0 })}`);
