// PIANO-UI-008 U8-P5: REAL Chromium/HTTP premium Piano/Device + Settings
// gates. Headless Chrome uses SwiftShader; results are not physical-device
// evidence. The controlled server is loopback-only. The mock Core serves
// the REAL contract shapes (health/state/led/device/learning/playback);
// all assertions are consumer-presentation gates over authoritative data —
// real recovery, mutex and LED semantics stay covered by the canonical
// suites.
import assert from "node:assert/strict";
import { BrowserDriver } from "../release_harness/browser_driver.mjs";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { createServer } from "node:http";
import { mkdtemp, readFile, rm, writeFile, mkdir } from "node:fs/promises";
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
  { song_id: "song-x", filename: "nocturne-x.mid", relpath: "nocturne-x.mid", extension: ".mid", size_bytes: 800, status: "parsed", midi_format: 1, track_count: 1, ticks_per_beat: 480, duration_seconds: 120, tempo_bpm_first: 90, title: "Nocturne X", indexed_at_utc: "2026-10-01T00:00:00Z" },
];

const deviceFull = () => ({
  product: "Piano Core",
  device_id: "6f0a2c1e-0000-4000-8000-5bbcd1a2e3f4",
  display_code: "PC7X-2049",
  identity_schema: "piano-core-identity-v1",
});

const state = {
  midi: "CONNECTED",
  held: new Set(),
  delayStateMs: 0,
  led: {
    configured: true, enabled: true, available: true, connection: "READY",
    mode: "physical", brightness: 80, active_note_count: 0, practice_active: false,
    last_error: null, transport: { host: "127.0.0.1", port: 8765 }, dropped_events: 0, frames_sent: 0,
  },
  denyNextLedPut: false,
  puts: { led: [] },
  deviceUnavailable: false,
};
let seq = 0;
const sseClients = new Set();
const liveSnapshot = () => ({
  schema_version: 1,
  runtime_epoch: "ui008-p5",
  sequence: seq,
  physical: {
    notes: [...state.held].sort((a, b) => a - b),
    outside_keyboard_count: 0,
    sustain: { value: 0, pressed: false },
    midi_connection: state.midi,
    revision: seq,
  },
  playback: { available: true, notes: [], voices: [], outside_keyboard_count: 0, sustain_channels: [] },
});
function pushLiveNotes() {
  seq += 1;
  const frame = `event: snapshot\ndata: ${JSON.stringify(liveSnapshot())}\n\n`;
  for (const res of sseClients) {
    try { res.write(frame); } catch { sseClients.delete(res); }
  }
}
/** Test-side physical key press: updates authoritative truth and streams it. */
function setHeld(notes) {
  state.held = new Set(notes);
  pushLiveNotes();
}

const learningIdle = () => ({
  available: true, state: "IDLE", song_id: null, title: null,
  step_index: null, total_steps: null, completed_steps: 0, incorrect_attempts: 0,
  progress: 0, started_at: null, ended_at: null, error: null,
  midi_connection: state.midi, session_id: null, mode: "WAIT_FOR_NOTE",
  hand_selection: null, section: null, target_tempo: null, accepted_notes: [],
  latest_outcome: "NONE", generation: 0, version: 0, updated_at: null,
  target: null, repetition: { current: 0, required: 1, successful: 0, failed: 0 },
});

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
    if (state.denyNextLedPut) {
      state.denyNextLedPut = false;
      json(res, { error: { code: "internal_error", message: "led rejected" } }, 500);
      return;
    }
    if (body.enabled !== undefined) state.led.enabled = body.enabled;
    if (body.mode !== undefined) state.led.mode = body.mode;
    if (body.brightness !== undefined) state.led.brightness = body.brightness;
    state.led.available = state.led.enabled;
    json(res, ledPayload());
    return;
  }
  if (req.method === "POST" && url.pathname === "/api/v1/led/clear") {
    json(res, ledPayload());
    return;
  }
  if (req.method === "POST" && url.pathname === "/api/v1/playback/play") {
    json(res, { error: { code: "not_ready", message: "nf" } }, 503);
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
    if (state.deviceUnavailable) { json(res, { error: { code: "device_identity_unavailable", message: "na" } }, 503); return; }
    json(res, deviceFull());
    return;
  }
  if (url.pathname === "/api/v1/state") {
    if (state.delayStateMs) await sleep(state.delayStateMs);
    json(res, {
      schema_version: 1, midi_connection: state.midi,
      sustain: { pressed: false, value: 0 },
      pressed_notes: [...state.held].sort((a, b) => a - b),
      last_input: state.held.size ? { type: "NOTE_ON", channel: 0, timestamp: "t", note: [...state.held][0], value: 100 } : null,
      revision: 1,
    });
    return;
  }
  if (url.pathname === "/api/v1/health") {
    json(res, {
      status: "ok", application: { state: "READY", since: "t" },
      midi_input: { connection: state.midi },
      library: { ready: true, entries: songs.length },
      playback: { available: true, state: "IDLE", song_id: null, title: null, position_seconds: 0, duration_seconds: 0, tempo_multiplier: 1, events_sent: 0, error: null },
    });
    return;
  }
  if (url.pathname === "/api/v1/led") { json(res, ledPayload()); return; }
  if (url.pathname === "/api/v1/learning" || url.pathname === "/api/v1/practice") { json(res, learningIdle()); return; }
  if (url.pathname === "/api/v1/playback") { json(res, { available: true, state: "IDLE", song_id: null, title: null, position_seconds: 0, duration_seconds: 0, tempo_multiplier: 1, events_sent: 0, error: null }); return; }
  if (url.pathname === "/api/v1/live-notes") {
    seq += 1;
    json(res, liveSnapshot());
    return;
  }
  if (url.pathname === "/api/v1/live-notes/stream") {
    res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache", connection: "close" });
    seq += 1;
    res.write(`event: snapshot\ndata: ${JSON.stringify(liveSnapshot())}\n\n`);
    sseClients.add(res);
    const hb = setInterval(() => { try { res.write(": heartbeat\n\n"); } catch {} }, 2000);
    req.on("close", () => { clearInterval(hb); sseClients.delete(res); });
    return;
  }
  if (url.pathname === "/api/v1/library/songs") { json(res, { query: {}, count: songs.length, songs }); return; }
  const songMatch = url.pathname.match(/^\/api\/v1\/library\/songs\/(.+)$/);
  if (songMatch) { json(res, songs[0]); return; }
  const scoreMatch = url.pathname.match(/^\/api\/v1\/score\/(.+)$/);
  if (scoreMatch) { json(res, { schema_version: 1, song_id: decodeURIComponent(scoreMatch[1]), source_version: `score-sha256:${"a".repeat(64)}`, title: "Sonata", duration_seconds: 120, time_basis: "song_seconds", note_count: 1, notes: [ { index: 0, midi: 67, channel: 1, velocity: 90, onset_seconds: 2, duration_seconds: 1 } ], diagnostics: { percussion_note_ons: 0, outside_keyboard_note_ons: 0, unpaired_note_offs: 0, retriggered_note_ons: 0, unterminated_note_ons: 0 }, notation: { supported: ["pitch", "onset", "duration"], unsupported: ["measure", "beat", "hand", "fingering"] } }); return; }
  const fingeringMatch = url.pathname.match(/^\/api\/v1\/fingering\/(.+)$/);
  if (fingeringMatch) { json(res, { schema_version: 1, engine_version: "balanced-engine-v1", plan_id: `fingering-sha256:${"b".repeat(64)}`, song_id: decodeURIComponent(fingeringMatch[1]), source_version: `score-sha256:${"a".repeat(64)}`, version: 1, generation: 1, strategy: "balanced_v1", provenance: "DETERMINISTIC_INFERENCE", targets: [ { score_index: 0, target_id: "score:0", midi: 67, channel: 1, onset_seconds: 2, duration_seconds: 1, hand: "RIGHT", finger: 2, alternatives: [] } ], warnings: [], excluded_note_count: 0, config: { strategy: "balanced_v1", max_hand_span_semitones: 12, alternative_count: 1 }, capabilities: { manual_overrides: false, finger_sensitive_grading: false } }); return; }
  json(res, { error: { code: "not_found", message: "nf" } }, 404);
});

function ledPayload() {
  return { ...state.led, available: state.led.enabled && state.led.configured && state.led.connection !== "UNAVAILABLE" };
}

await new Promise((d) => server.listen(0, "127.0.0.1", d));
const appPort = server.address().port;
const probe = createServer();
await new Promise((d) => probe.listen(0, "127.0.0.1", d));
const debugPort = probe.address().port;
await new Promise((d) => probe.close(d));
const profile = await mkdtemp(join(tmpdir(), "piano-ui008-p5-chrome-"));
const chrome = spawn(CHROME, [
  "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
  `--remote-debugging-port=${debugPort}`, `--user-data-dir=${profile}`,
  `http://127.0.0.1:${appPort}/ui/#/piano`,
], { stdio: ["ignore", "ignore", "pipe"] });
const cleanupDriver = new BrowserDriver();
cleanupDriver.chrome = chrome;
cleanupDriver.profile = profile;
chrome.once("exit", (code, signal) => { cleanupDriver.exit = { code, signal }; });

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
/** Wait on Node-side mock state (page evaluation cannot see it). */
async function waitForNode(fn, timeout = 8000) {
  const dl = Date.now() + timeout;
  while (Date.now() < dl) { if (fn()) return true; await sleep(40); }
  return false;
}
async function viewport(w, h) { await cdp("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: false }); await sleep(180); }
async function route(n) { await evaluate(`location.hash = '#/${n}'`); await waitFor(`document.body.dataset.ui008Route === '${n}'`); }
async function capture(name) {
  if (!EVIDENCE_DIR) return;
  await evaluate("new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
  await sleep(250);
  await mkdir(EVIDENCE_DIR, { recursive: true });
  const shot = await cdp("Page.captureScreenshot", { format: "png", fromSurface: true });
  await writeFile(join(EVIDENCE_DIR, name), Buffer.from(shot.data, "base64"));
}

try {
await cdp("Runtime.enable");
await cdp("Page.enable");
try {
  assert.equal(await waitFor("document.documentElement?.dataset.ui008ShellReady === 'true'"), true, "new document must finish shell boot");
  await waitFor("document.getElementById('piano-state')?.textContent === 'Piano connected'", 15000);
} catch (error) {
  console.error("BOOT FAILURE — collected page errors:");
  console.error(pageErrors.join("\n---\n"));
  throw error;
}

/* ---------------- PIANO SURFACE ---------------- */

await test("piano connected: consumer chip, identity, guidance, honest activity", async () => {
  assert.equal(await evaluate("document.getElementById('piano-state').textContent"), "Piano connected");
  assert.ok((await evaluate("document.getElementById('piano-identity').textContent")).includes("PC7X-2049"), "display code missing");
  assert.ok((await evaluate("document.getElementById('piano-guidance').textContent")).includes("sees your piano"));
  assert.equal(await evaluate("document.getElementById('piano-notes').textContent"), "No keys held right now.");
  assert.equal(await evaluate("document.getElementById('piano-sustain').textContent"), "Sustain pedal is up.");
  const cardText = await evaluate("document.getElementById('piano-status').textContent");
  assert.ok(!/CONNECTED|DISCONNECTED|StateStore|websocket|MIDI input/.test(cardText), `leak: ${cardText}`);
});

await test("piano keyboard: 88 canonical keys reused, edge keys present", async () => {
  const m = await evaluate(`(() => {
    const midis = new Set([...document.querySelectorAll('#live-keyboard .piano-key')].map(k => Number(k.dataset.midi)));
    return {
      keys: midis.size,
      hasA0: midis.has(21),
      hasC8: midis.has(108),
      keyboards: document.querySelectorAll('#live-keyboard').length,
    };
  })()`);
  assert.equal(m.keys, 88);
  assert.ok(m.hasA0, "A0 missing");
  assert.ok(m.hasC8, "C8 missing");
  assert.equal(m.keyboards, 1);
});

await test("piano pressed keys: physical keys light up; consumer summary", async () => {
  setHeld([67, 72]);
  await waitFor(`[...document.querySelectorAll('#live-keyboard .piano-key')].filter(k => k.dataset.source === 'physical').length === 2`, 8000);
  const summary = await evaluate("document.getElementById('live-keyboard-summary').textContent");
  assert.ok(summary.includes("2 keys lit") && summary.includes("by you"), `summary: ${summary}`);
  await waitFor(`document.getElementById('piano-notes').textContent.includes('Holding G4')`, 8000);
  assert.ok((await evaluate("document.getElementById('piano-notes').textContent")).includes("C5"), "activity line missing held keys");
  setHeld([]);
  await waitFor(`document.getElementById('live-keyboard-summary').textContent.includes('No keys lit')`, 8000);
});

await test("piano disconnected: consumer wording only, no enum leak", async () => {
  state.midi = "DISCONNECTED";
  await waitFor("document.getElementById('piano-state').textContent === 'Piano disconnected'", 8000);
  const cardText = await evaluate("document.getElementById('piano-status').textContent");
  assert.ok(cardText.includes("not connected") || cardText.includes("Plug it in"), `guidance: ${cardText}`);
  assert.ok(!/CONNECTED|DISCONNECTED|UNAVAILABLE/.test(cardText), `enum leak: ${cardText}`);
  state.midi = "CONNECTED";
  await waitFor("document.getElementById('piano-state').textContent === 'Piano connected'", 8000);
});

await test("recovering: slow Core marks the surface and recovers without refresh", async () => {
  state.delayStateMs = 6000;
  await waitFor("document.getElementById('piano-state').textContent === 'Reconnecting'", 12000);
  state.delayStateMs = 0;
  await waitFor("document.getElementById('piano-state').textContent === 'Piano connected'", 12000);
  assert.equal(await evaluate("document.getElementById('piano-state').textContent"), "Piano connected");
});

/* ---------------- LED ---------------- */

await test("led available: consumer chip/status; controls enabled", async () => {
  await waitFor("document.getElementById('led-chip').textContent === 'Guidance lights ready'", 8000);
  assert.equal(await evaluate("document.getElementById('led-enabled').disabled"), false);
  assert.equal(await evaluate("document.getElementById('led-mode').disabled"), false);
  assert.equal(await evaluate("document.getElementById('led-brightness').disabled"), false);
  const status = await evaluate("document.getElementById('led-status').textContent");
  assert.ok(!/ws |127\.0\.0\.1|:8765/.test(status), `transport leak: ${status}`);
});

await test("led command: exactly one PUT per change; canonical reconciliation", async () => {
  state.puts.led.length = 0;
  await evaluate("document.getElementById('led-enabled').click()");
  await waitForNode(() => state.puts.led.length === 1);
  assert.deepEqual(state.puts.led[0], { enabled: false });
  await waitFor("document.getElementById('led-enabled').checked === false", 8000);
  assert.equal(await evaluate("document.getElementById('led-chip').textContent"), "Lights off");
  state.puts.led.length = 0;
  await evaluate("document.getElementById('led-enabled').click()");
  await waitForNode(() => state.puts.led.length === 1);
  assert.deepEqual(state.puts.led[0], { enabled: true });
  await waitFor("document.getElementById('led-chip').textContent === 'Guidance lights ready'", 8000);
  // brightness: one command on change (release), not on every input event
  state.puts.led.length = 0;
  await evaluate(`(() => {
    const b = document.getElementById('led-brightness');
    b.value = 55;
    b.dispatchEvent(new Event('input', { bubbles: true }));
    b.dispatchEvent(new Event('change', { bubbles: true }));
  })()`);
  await waitForNode(() => state.puts.led.length === 1);
  await sleep(400);
  assert.equal(state.puts.led.length, 1, "brightness emitted more than one command");
  assert.deepEqual(state.puts.led[0], { brightness: 55 });
  await waitFor("document.getElementById('led-brightness-value').textContent === '55%'", 8000);
});

await test("led command denial: honest error, no optimistic state", async () => {
  state.puts.led.length = 0;
  state.denyNextLedPut = true;
  await evaluate("document.getElementById('led-enabled').click()");
  await waitForNode(() => state.puts.led.length === 1);
  await waitFor("document.getElementById('led-error').hidden === false", 8000);
  await sleep(600);
  assert.equal(state.puts.led.length, 1, "denied change retried or duplicated");
  // the canonical snapshot still says enabled -> the switch reconciles back
  await waitFor("document.getElementById('led-enabled').checked === true", 10000);
  assert.equal(await evaluate("document.getElementById('led-chip').textContent"), "Guidance lights ready");
  await waitFor("document.getElementById('led-error').hidden === true", 12000);
});

await test("led unavailable + not configured: honest, control stays truthful", async () => {
  state.led.available = false;
  state.led.enabled = false;
  await waitFor("document.getElementById('led-chip').textContent === 'Lights off'", 8000);
  state.led.enabled = true;
  state.led.connection = "UNAVAILABLE";
  await waitFor("document.getElementById('led-chip').textContent === 'Lights unavailable'", 8000);
  // configured-but-unavailable stays toggleable (accepted P014-HF1 rule)
  assert.equal(await evaluate("document.getElementById('led-enabled').disabled"), false);
  state.led.configured = false;
  state.led.enabled = false;
  await waitFor("document.getElementById('led-chip').textContent === 'No guidance lights'", 8000);
  assert.ok((await evaluate("document.getElementById('led-status').textContent")).includes("no keyboard lights"), "honest unconfigured line missing");
  Object.assign(state.led, { configured: true, enabled: true, connection: "READY" });
  await waitFor("document.getElementById('led-chip').textContent === 'Guidance lights ready'", 8000);
});

/* ---------------- SETTINGS ---------------- */

await test("settings: exactly the real groups; unsupported settings absent", async () => {
  await route("settings");
  const m = await evaluate(`(() => ({
    groups: [...document.querySelectorAll('#ui008-settings .settings-group h3')].map(h => h.textContent),
    visual: !!document.getElementById('settings-visual-mode'),
    hands: !!document.getElementById('settings-hands-enabled'),
    quality: !!document.getElementById('settings-hands-quality'),
    guidanceChip: document.getElementById('settings-led-chip').textContent,
    aboutRows: document.getElementById('settings-about-rows').children.length,
    persistenceNote: document.getElementById('settings-persistence-note').textContent,
  }))()`);
  assert.deepEqual(m.groups, ["Visuals", "Guidance", "About"]);
  assert.ok(m.visual && m.hands && m.quality);
  assert.equal(m.guidanceChip, "Guidance lights ready");
  assert.ok(m.aboutRows >= 3, "about rows missing");
  const note = (await evaluate("document.getElementById('settings-persistence-note').textContent")).replace(/\s+/g, " ");
  assert.ok(note.includes("this browser"), `persistence note missing: ${note}`);
  assert.ok(note.includes("no other preferences yet"), `persistence note incomplete: ${note}`);
  const text = await evaluate("document.getElementById('ui008-settings').textContent");
  for (const banned of ["Theme", "theme", "Reset", "Microphone", "microphone", "Bluetooth", "account", "Account"]) {
    assert.ok(!text.includes(banned), `invented setting: ${banned}`);
  }
});

await test("settings visual preference: local persistence where persistence really exists", async () => {
  await evaluate("document.getElementById('settings-visual-hybrid').click()");
  await waitFor(`localStorage.getItem('piano-core.visual-mode') === 'hybrid'`, 8000);
  assert.equal(await evaluate("document.getElementById('settings-visual-hybrid').getAttribute('aria-pressed')"), "true");
  assert.equal(await evaluate("document.getElementById('visual-mode-hybrid').getAttribute('aria-pressed')"), "true", "visual card mirror not synced");
  await cdp("Page.reload", { ignoreCache: true });
  await waitFor("document.documentElement.dataset.ui008ShellReady === 'true'", 20000);
  await waitFor("document.getElementById('settings-visual-hybrid')?.getAttribute('aria-pressed') === 'true'", 15000);
  assert.equal(await evaluate("localStorage.getItem('piano-core.visual-mode')"), "hybrid", "preference lost on reload");
  await evaluate("document.getElementById('settings-visual-falling').click()");
  await waitFor(`localStorage.getItem('piano-core.visual-mode') === 'falling'`, 8000);
});

await test("settings hands preference mirrors the accepted local pref", async () => {
  await route("settings");
  await evaluate("document.getElementById('settings-hands-enabled').click()");
  await waitFor(`localStorage.getItem('piano-core.hands-enabled') === 'off'`, 8000);
  // IMMERSIVE update: the hands on/off checkbox lives in Settings only now
  // (the hands-card duplicate retired with the scene composition); the
  // accepted applier + storage key are unchanged.
  assert.equal(await evaluate("localStorage.getItem('piano-core.hands-enabled')"), "off", "hands preference stored off");
  await evaluate("document.getElementById('settings-hands-enabled').click()");
  await waitFor(`localStorage.getItem('piano-core.hands-enabled') === 'on'`, 8000);
  await evaluate("document.getElementById('settings-hands-camera-follow').click()");
  await waitFor(`localStorage.getItem('piano-core.hands-camera') === 'FOLLOW'`, 8000);
  await evaluate("document.getElementById('settings-hands-camera-full').click()");
  await waitFor(`localStorage.getItem('piano-core.hands-camera') === 'FULL'`, 8000);
});

await test("settings technical identity stays behind the disclosure", async () => {
  const closedVisible = await evaluate(`(() => {
    const details = document.querySelector('.system-status');
    return details.open;
  })()`);
  assert.equal(closedVisible, false, "technical disclosure must default closed");
  await evaluate("document.querySelector('.system-status summary').click()");
  await waitFor("document.querySelector('.system-status').open === true");
  const pill = await evaluate("document.getElementById('pill-device').textContent");
  assert.ok(pill.includes("6f0a2c1e"), "device id missing from technical disclosure");
  const transport = await evaluate("document.getElementById('pill-led-transport').textContent");
  assert.ok(transport.includes("8765"), "led transport missing from technical disclosure");
  await evaluate("document.querySelector('.system-status summary').click()");
});

/* ---------------- COPY / LEAKAGE AUDIT ---------------- */

await test("consumer-copy audit: no technical leakage on any primary surface", async () => {
  const report = await evaluate(`(() => {
    const banned = /StateStore|websocket|WebSocket|endpoint|mutex|localhost|127\\.0\\.0\\.1|NOTE_ON|NOTE_OFF|WAIT_FOR_NOTE|SECTION_PRACTICE|REPEAT_UNTIL_CORRECT|activity_conflict|playback_already_active|library_unavailable|network_error|Core output ownership|device_identity|MIDI input|\\bws \\d/;
    const leaks = [];
    for (const surface of document.querySelectorAll(".route-surface")) {
      if (surface.classList.contains("settings-foundation")) continue; // authorized diagnostic disclosure
      if (surface.classList.contains("live-piano-card")) {
        // legend + status words are accepted; scan the hint/summary lines only
        const scope = [surface.querySelector(".hint"), surface.querySelector("#live-keyboard-summary")]
          .filter(Boolean).map(e => e.textContent).join(" ");
        if (banned.test(scope)) leaks.push(scope.slice(0, 120));
        continue;
      }
      if (banned.test(surface.textContent)) leaks.push((surface.id || surface.className).slice(0, 60));
    }
    return leaks;
  })()`);
  assert.deepEqual(report, [], JSON.stringify(report));
});

/* ---------------- ROUTES / LIFECYCLE ---------------- */

await test("routes: direct #/piano + #/settings, refresh, cycles — no duplicates", async () => {
  await route("piano");
  await cdp("Page.reload", { ignoreCache: true });
  await waitFor("document.documentElement.dataset.ui008ShellReady === 'true'", 20000);
  await waitFor("document.body.dataset.ui008Route === 'piano'", 10000);
  assert.equal(await evaluate("document.getElementById('piano-state').textContent === 'Piano connected' || document.getElementById('piano-state').textContent === 'Reconnecting'"), true);
  await route("settings");
  await route("home");
  await route("piano");
  await route("settings");
  const counts = await evaluate(`(() => ({
    keyboards: document.querySelectorAll('#live-keyboard').length,
    pianoCards: document.querySelectorAll('#piano-status').length,
    settingsCards: document.querySelectorAll('#ui008-settings').length,
    ledCards: document.querySelectorAll('#led-controls').length,
  }))()`);
  assert.deepEqual(counts, { keyboards: 1, pianoCards: 1, settingsCards: 1, ledCards: 1 });
  state.puts.led.length = 0;
  await route("piano");
  await evaluate("document.getElementById('btn-led-clear').click()");
  await sleep(500);
  // clear is a POST /led/clear, not a PUT; ensure no PUT storm happened
  assert.equal(state.puts.led.length, 0, "clear emitted PUT commands");
});

/* ---------------- RESPONSIVE / ACCESSIBILITY ---------------- */

await test("responsive: 7 canonical viewports on Piano and Settings", async () => {
  const problems = [];
  for (const [w, h] of [[390, 844], [768, 1024], [899, 900], [900, 900], [1024, 768], [1440, 900], [1920, 1080]]) {
    await viewport(w, h);
    for (const routeName of ["piano", "settings"]) {
      await route(routeName);
      const chipId = routeName === "piano" ? "piano-state" : "settings-led-chip";
      const m = await evaluate(`(() => {
        const de = document.documentElement;
        const chip = document.getElementById('${chipId}').getBoundingClientRect();
        const keyboard = document.getElementById('live-keyboard').getBoundingClientRect();
        const ledEnabled = document.getElementById('led-enabled').getBoundingClientRect();
        const visible = (r) => r.width > 0 && r.height > 0;
        return {
          overflowX: Math.max(de.scrollWidth - de.clientWidth, 0),
          chipVisible: visible(chip) && chip.top >= 0,
          keyboardH: Math.round(keyboard.height),
          ledSwitchH: Math.round(ledEnabled.height),
        };
      })()`);
      if (m.overflowX > 1) problems.push(`${w}/${routeName}: overflowX ${m.overflowX}`);
      if (!m.chipVisible) problems.push(`${w}/${routeName}: chip not visible`);
      if (routeName === "piano" && m.keyboardH <= 0) problems.push(`${w}/piano: keyboard collapsed`);
      if (routeName === "piano" && m.ledSwitchH > 0 && m.ledSwitchH < 44) problems.push(`${w}/piano: switch ${m.ledSwitchH}`);
    }
  }
  await viewport(1024, 768);
  await route("piano");
  assert.deepEqual(problems, [], JSON.stringify(problems));
});

await test("accessibility: labels, live status, switch naming, slider output", async () => {
  await route("piano");
  const a = await evaluate(`(() => {
    const labelled = (id) => !!document.querySelector('[for="' + id + '"]');
    return {
      ledModeLabel: labelled('led-mode'),
      ledBrightnessLabel: labelled('led-brightness'),
      ledSwitchText: document.querySelector('#led-controls .led-switch-label')?.textContent,
      ledGroupLabel: document.getElementById('led-controls').getAttribute('aria-label'),
      keyboardRole: document.getElementById('live-keyboard').getAttribute('role'),
      summaryLive: document.getElementById('live-keyboard-summary').getAttribute('aria-live'),
      settingsVisualGroup: !!document.getElementById('settings-visual-mode-label'),
      settingsQualityLabelled: labelled('settings-hands-quality'),
    };
  })()`);
  assert.ok(a.ledModeLabel && a.ledBrightnessLabel);
  assert.equal(a.ledSwitchText, "Lights on");
  assert.equal(a.ledGroupLabel, "Keyboard light controls");
  assert.equal(a.keyboardRole, "img");
  assert.equal(a.summaryLive, "polite");
  assert.ok(a.settingsVisualGroup && a.settingsQualityLabelled);
  // slider output follows the value without a command (input event only)
  await evaluate(`(() => {
    const b = document.getElementById('led-brightness');
    b.value = 77;
    b.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  assert.equal(await evaluate("document.getElementById('led-brightness-value').textContent"), "77%");
});

/* ---------------- VISUAL EVIDENCE ---------------- */

await test("fresh visual evidence captures Piano + Settings", async () => {
  await sleep(4500); // let any earlier toast retire before capturing
  await viewport(1024, 768);
  await route("piano");
  await waitFor("document.getElementById('piano-state').textContent === 'Piano connected'", 10000);
  await sleep(400);
  await capture("p5-piano-connected-tablet.png");
  setHeld([60, 67, 72]);
  await waitFor(`[...document.querySelectorAll('#live-keyboard .piano-key')].filter(k => k.dataset.source === 'physical').length === 3`, 8000);
  await sleep(300);
  await capture("p5-piano-pressed-keys.png");
  setHeld([]);
  await waitFor(`document.getElementById('live-keyboard-summary').textContent.includes('No keys lit')`, 8000);
  state.midi = "DISCONNECTED";
  await waitFor("document.getElementById('piano-state').textContent === 'Piano disconnected'", 8000);
  await sleep(300);
  await capture("p5-piano-disconnected.png");
  state.midi = "CONNECTED";
  await waitFor("document.getElementById('piano-state').textContent === 'Piano connected'", 8000);
  await capture("p5-piano-led-available.png");
  await route("settings");
  await sleep(400);
  await capture("p5-settings-tablet.png");
  await viewport(390, 844);
  await sleep(400);
  await capture("p5-settings-phone.png");
  await route("piano");
  await sleep(400);
  await capture("p5-piano-phone.png");
  await viewport(1440, 900);
  await sleep(400);
  await capture("p5-piano-desktop.png");
  await route("learn");
  await sleep(600);
  await capture("p5-consumer-copy-learn.png");
  await viewport(1024, 768);
  await route("piano");
});

} finally {
  cleanupDriver.ws = ws; cleanupDriver.send = cdp; cleanupDriver.ev = evaluate;
  const cleanup = await cleanupDriver.close();
  let closeTimer;
  try {
    await Promise.race([new Promise((done) => server.close(done)), new Promise((_, fail) => {
      closeTimer = setTimeout(() => fail(new Error("Owned fixture HTTP shutdown deadline")), 5000);
    })]);
  } finally { clearTimeout(closeTimer); }
  assert.equal(cleanup.status, "PASS", JSON.stringify(cleanup));
}

if (failures) {
  console.error(`FAIL ${failures}; PASS ${passes}`);
  process.exit(1);
}
console.log(`PASS ${passes}/${passes}`);
console.log(`P5_MEASURES ${JSON.stringify({ captures: EVIDENCE_DIR ? 9 : 0 })}`);
