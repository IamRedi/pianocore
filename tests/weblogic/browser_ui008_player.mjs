// PIANO-UI-008 U8-P3: real Chromium/HTTP Player workspace gates.
// Headless Chrome uses SwiftShader here; results are not physical-device or
// hardware-GPU evidence. The controlled server is loopback-only.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { createServer } from "node:http";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
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
const measures = {};
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
const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

/* ---------------- controlled Core (loopback) ---------------- */

const songs = [
  { song_id: "song-a", filename: "sonata-a.mid", relpath: "sonata-a.mid", extension: ".mid", size_bytes: 900, status: "parsed", midi_format: 1, track_count: 2, ticks_per_beat: 480, duration_seconds: 240, tempo_bpm_first: 92, title: "Sonata in A", indexed_at_utc: "2026-09-28T00:00:00Z" },
  { song_id: "song-b", filename: "sonata-b.mid", relpath: "sonata-b.mid", extension: ".mid", size_bytes: 800, status: "parsed", midi_format: 1, track_count: 1, ticks_per_beat: 480, duration_seconds: 180, tempo_bpm_first: 120, title: "Sonata in B", indexed_at_utc: "2026-09-28T00:00:00Z" },
];

const core = {
  playback: { available: false, state: null, song_id: null, title: null, position_seconds: null, duration_seconds: null, tempo_multiplier: null, events_sent: null, error: null },
  learning: {
    available: true, state: "IDLE", song_id: null, title: null,
    step_index: null, total_steps: null, completed_steps: 0, incorrect_attempts: 0,
    progress: 0, started_at: null, ended_at: null, error: null,
    midi_connection: "CONNECTED", session_id: null, mode: null,
    hand_selection: null, section: null, target_tempo: null, accepted_notes: [],
    latest_outcome: "NONE", generation: 0, version: 0, updated_at: null,
    target: null, repetition: { current: 0, required: 1, successful: 0, failed: 0 },
  },
  led: { available: false, configured: false, enabled: false, connection: "DISABLED", mode: null, brightness: null, active_note_count: 0, practice_active: false, last_error: null, transport: null, dropped_events: 0, frames_sent: 0 },
  ledConfigured: false,
  denyNextPlay: false,
  denyNextPause: false,
  posts: { play: [], pause: [], resume: [], stop: [], led: [] },
};

function setIdle() {
  core.playback = { available: true, state: "IDLE", song_id: null, title: null, position_seconds: 0, duration_seconds: 0, tempo_multiplier: 1, events_sent: 0, error: null };
}

const score = (songId) => ({
  schema_version: 1, song_id: songId, source_version: `score-sha256:${"a".repeat(64)}`,
  title: "Sonata", duration_seconds: 240, time_basis: "song_seconds", note_count: 2,
  notes: [
    { index: 0, midi: 60, channel: 1, velocity: 90, onset_seconds: 4, duration_seconds: 1 },
    { index: 1, midi: 67, channel: 1, velocity: 80, onset_seconds: 6, duration_seconds: 1 },
  ],
  diagnostics: { percussion_note_ons: 0, outside_keyboard_note_ons: 0, unpaired_note_offs: 0, retriggered_note_ons: 0, unterminated_note_ons: 0 },
  notation: { supported: ["pitch", "onset", "duration", "channel", "velocity"], unsupported: ["measure", "beat", "hand", "fingering"] },
});

const plan = (songId) => ({
  schema_version: 1, engine_version: "balanced-engine-v1", plan_id: `fingering-sha256:${"b".repeat(64)}`,
  song_id: songId, source_version: `score-sha256:${"a".repeat(64)}`, version: 1, generation: 1,
  strategy: "balanced_v1", provenance: "DETERMINISTIC_INFERENCE",
  targets: [{ score_index: 0, target_id: "score:0", midi: 60, channel: 1, onset_seconds: 4, duration_seconds: 1, hand: "RIGHT", finger: 2, alternatives: [] }],
  warnings: ["source_contains_no_authored_hand_or_fingering_metadata"], excluded_note_count: 0,
  config: { strategy: "balanced_v1", max_hand_span_semitones: 12, alternative_count: 1 },
  capabilities: { manual_overrides: false, finger_sensitive_grading: false },
});

let sequence = 0;
function noteSnapshot() {
  sequence += 1;
  return {
    schema_version: 1, runtime_epoch: "ui008-p3", sequence,
    physical: { notes: [], outside_keyboard_count: 0, sustain: { value: 0, pressed: false }, midi_connection: "CONNECTED", revision: sequence },
    playback: { available: true, notes: [], voices: [], outside_keyboard_count: 0, sustain_channels: [] },
  };
}

function json(res, value, status = 200) {
  const body = JSON.stringify(value);
  res.writeHead(status, { "content-type": "application/json", "content-length": Buffer.byteLength(body), "cache-control": "no-store" });
  res.end(body);
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://127.0.0.1");
  if (req.method === "POST") {
    let body = "";
    for await (const chunk of req) body += chunk;
    const parsed = JSON.parse(body || "{}");
    if (url.pathname === "/api/v1/playback/play") {
      core.posts.play.push(parsed);
      if (core.denyNextPlay) {
        core.denyNextPlay = false;
        json(res, { error: { code: "playback_already_active", message: "a session is already active" } }, 409);
        return;
      }
      const song = songs.find((s) => s.song_id === parsed.song_id);
      core.playback = { available: true, state: "PLAYING", song_id: parsed.song_id, title: song?.title ?? null, position_seconds: 0, duration_seconds: song?.duration_seconds ?? 200, tempo_multiplier: parsed.tempo_multiplier ?? 1, events_sent: 4, error: null };
      json(res, core.playback);
      return;
    }
    if (url.pathname === "/api/v1/playback/pause") {
      core.posts.pause.push(parsed);
      if (core.denyNextPause) {
        core.denyNextPause = false;
        json(res, { error: { code: "playback_command_rejected", message: "pause was rejected" } }, 409);
        return;
      }
      if (core.playback.state !== "PLAYING") {
        json(res, { error: { code: "playback_not_active", message: "nothing is playing" } }, 409);
        return;
      }
      core.playback = { ...core.playback, state: "PAUSED" };
      json(res, core.playback);
      return;
    }
    if (url.pathname === "/api/v1/playback/resume") {
      core.posts.resume.push(parsed);
      if (core.playback.state !== "PAUSED") {
        json(res, { error: { code: "playback_not_paused", message: "nothing is paused" } }, 409);
        return;
      }
      core.playback = { ...core.playback, state: "PLAYING" };
      json(res, core.playback);
      return;
    }
    if (url.pathname === "/api/v1/playback/stop") {
      core.posts.stop.push(parsed);
      const had = core.playback.state === "PLAYING" || core.playback.state === "PAUSED";
      core.playback = { ...core.playback, state: "IDLE", song_id: null, title: null, position_seconds: 0, duration_seconds: 0 };
      json(res, { ...core.playback, stopped: had });
      return;
    }
    if (url.pathname === "/api/v1/led/clear") { core.posts.led.push({ clear: true }); json(res, core.led); return; }
    json(res, { error: { code: "not_found", message: "nf" } }, 404);
    return;
  }
  if (url.pathname === "/" || url.pathname === "/ui") { res.writeHead(302, { location: "/ui/" }); res.end(); return; }
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
  if (url.pathname === "/api/v1/live-notes") { json(res, noteSnapshot()); return; }
  if (url.pathname === "/api/v1/live-notes/stream") {
    res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache", connection: "close" });
    res.write(`event: snapshot\ndata: ${JSON.stringify(noteSnapshot())}\n\n`);
    const hb = setInterval(() => { try { res.write(": heartbeat\n\n"); } catch {} }, 2000);
    req.on("close", () => clearInterval(hb));
    return;
  }
  if (url.pathname === "/api/v1/health") {
    json(res, { status: "ok", application: { state: "READY" }, midi_input: { connection: "CONNECTED" }, library: { ready: true, entries: songs.length }, playback: core.playback });
    return;
  }
  if (url.pathname === "/api/v1/state") { json(res, { midi_connection: "CONNECTED", sustain: { pressed: false, value: 0 }, pressed_notes: [], last_input: null, revision: 1 }); return; }
  if (url.pathname === "/api/v1/led") {
    if (req.method === "PUT") {
      let body = "";
      for await (const chunk of req) body += chunk;
      const parsed = JSON.parse(body || "{}");
      core.posts.led.push(parsed);
      if (core.ledConfigured) {
        core.led = { ...core.led, configured: true, enabled: parsed.enabled === true, connection: "CONNECTED", mode: parsed.mode ?? core.led.mode, brightness: parsed.brightness ?? core.led.brightness };
      }
      json(res, core.led);
      return;
    }
    json(res, core.led);
    return;
  }
  if (url.pathname === "/api/v1/learning" || url.pathname === "/api/v1/practice") { json(res, core.learning); return; }
  if (url.pathname === "/api/v1/playback") { json(res, core.playback); return; }
  if (url.pathname === "/api/v1/library/songs") { json(res, { query: {}, count: songs.length, songs }); return; }
  const songMatch = url.pathname.match(/^\/api\/v1\/library\/songs\/(.+)$/);
  if (songMatch) {
    const rec = songs.find((s) => s.song_id === decodeURIComponent(songMatch[1]));
    rec ? json(res, rec) : json(res, { error: { code: "song_not_found", message: "nf" } }, 404);
    return;
  }
  const scoreMatch = url.pathname.match(/^\/api\/v1\/score\/(.+)$/);
  if (scoreMatch) { json(res, score(decodeURIComponent(scoreMatch[1]))); return; }
  const fingeringMatch = url.pathname.match(/^\/api\/v1\/fingering\/(.+)$/);
  if (fingeringMatch) { json(res, plan(decodeURIComponent(fingeringMatch[1]))); return; }
  json(res, { error: { code: "not_found", message: "nf" } }, 404);
});

await new Promise((done) => server.listen(0, "127.0.0.1", done));
const appPort = server.address().port;
const probe = createServer();
await new Promise((done) => probe.listen(0, "127.0.0.1", done));
const debugPort = probe.address().port;
await new Promise((done) => probe.close(done));
const profile = await mkdtemp(join(tmpdir(), "piano-ui008-p3-chrome-"));
const chrome = spawn(CHROME, [
  "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
  `--remote-debugging-port=${debugPort}`, `--user-data-dir=${profile}`,
  `http://127.0.0.1:${appPort}/ui/#/player`,
], { stdio: ["ignore", "ignore", "pipe"] });

async function fetchJson(u) {
  for (let i = 0; i < 120; i += 1) {
    try { return await (await fetch(u)).json(); } catch { await sleep(50); }
  }
  throw new Error(u);
}
const targets = await fetchJson(`http://127.0.0.1:${debugPort}/json/list`);
const ws = new WebSocket(targets.find((t) => t.type === "page").webSocketDebuggerUrl);
await new Promise((done, fail) => { ws.once("open", done); ws.once("error", fail); });
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
  if (r.exceptionDetails) throw new Error((r.exceptionDetails.exception?.description || r.exceptionDetails.text) + " :: " + String(expression).slice(0, 100));
  return r.result.value;
}
async function waitFor(expression, timeout = 15000) {
  const dl = Date.now() + timeout;
  while (Date.now() < dl) { if (await evaluate(expression)) return true; await sleep(40); }
  return false;
}
async function viewport(w, h) { await cdp("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: false }); await sleep(180); }
async function route(n) { await evaluate(`location.hash = '#/${n}'`); await waitFor(`document.body.dataset.ui008Route === '${n}'`); }
async function capture(name) {
  if (!EVIDENCE_DIR) return;
  // Force two fresh compositor frames before reading the surface so the
  // screenshot reflects the asserted DOM state, not a stale frame.
  await evaluate("new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
  await sleep(250);
  await mkdir(EVIDENCE_DIR, { recursive: true });
  const shot = await cdp("Page.captureScreenshot", { format: "png", fromSurface: true });
  await writeFile(join(EVIDENCE_DIR, name), Buffer.from(shot.data, "base64"));
}

await cdp("Runtime.enable");
await cdp("Page.enable");
try {
  await waitFor("document.documentElement?.dataset.ui008ShellReady === 'true'");
  await waitFor("document.getElementById('player-state').textContent === 'Unavailable'", 15000); // fresh boot: VALID_UNAVAILABLE
} catch (error) {
  console.error("BOOT FAILURE — collected page errors:");
  console.error(pageErrors.join("\n---\n"));
  throw error;
}

const state = "document.getElementById('player-state').textContent";

/* ---------------- PLAYER STATES ---------------- */

await test("state: fresh boot with unavailable transport reads honestly", async () => {
  assert.equal(await evaluate(state), "Unavailable");
  assert.equal(await evaluate("[...document.querySelectorAll('#btn-play,#btn-pause,#btn-resume,#btn-stop')].every(b => b.disabled)"), true);
});

await test("state: no song selected (idle engine) reads No song", async () => {
  setIdle();
  const reached = await waitFor(`document.getElementById('player-state').textContent === 'No song'`, 8000);
  assert.equal(reached, true);
  // Accepted UI-002 semantics: Play requires an explicit selection.
  assert.equal(await evaluate("document.getElementById('btn-play').disabled"), true);
});

await test("state: explicit selection presents Stopped with real metadata", async () => {
  await evaluate("document.querySelectorAll('.btn-song')[0].click()");
  await waitFor("document.getElementById('player-title').textContent === 'Sonata in A'");
  assert.equal(await evaluate(state), "Stopped");
});

await test("timeline: read-only by Core contract — no seek control exists", async () => {
  assert.equal(await evaluate("!!document.querySelector('#player-progress[aria-readonly=true]')"), true);
  assert.equal(await evaluate("!document.querySelector('#ui008-seek') && ![...document.querySelectorAll('.timeline input,.timeline button')].length"), true,
    "timeline exposes no interactive seek elements");
  assert.equal(await evaluate("document.getElementById('player-progress').getAttribute('role')"), "progressbar");
});

await test("transport: Play issues exactly one command and reconciles to PLAYING", async () => {
  core.posts.play.length = 0;
  await evaluate("document.getElementById('btn-play').click()");
  await waitFor(`document.getElementById('player-state').textContent === 'Playing'`, 8000);
  await sleep(300);
  assert.equal(core.posts.play.length, 1, JSON.stringify(core.posts.play));
  assert.equal(core.posts.play[0].song_id, "song-a");
  assert.equal(typeof core.posts.play[0].tempo_multiplier, "number");
});

await test("transport: Pause issues exactly one command and reconciles to PAUSED", async () => {
  core.posts.pause.length = 0;
  await evaluate("document.getElementById('btn-pause').click()");
  await waitFor(`document.getElementById('player-state').textContent === 'Paused'`, 8000);
  await sleep(200);
  assert.equal(core.posts.pause.length, 1);
});

await test("timeline: authoritative position and duration format honestly", async () => {
  core.playback = { ...core.playback, state: "PAUSED", position_seconds: 83, duration_seconds: 240 };
  await sleep(1300);
  assert.equal(await evaluate("document.getElementById('player-position').textContent"), "1:23");
  assert.equal(await evaluate("document.getElementById('player-remaining').textContent"), "−2:37");
  const aria = await evaluate("Number(document.getElementById('player-progress').getAttribute('aria-valuenow'))");
  assert.ok(aria >= 34 && aria <= 36, `aria ${aria}`);
});

await test("state: ENDED (COMPLETED) presents consumer wording without reset", async () => {
  core.playback = { ...core.playback, state: "COMPLETED", position_seconds: 240, duration_seconds: 240 };
  await sleep(1300);
  assert.equal(await evaluate(state), "Completed");
  assert.equal(await evaluate("document.getElementById('player-position').textContent"), "4:00");
});

await test("denial: failed Play never claims Playing (reconciles to authoritative truth)", async () => {
  core.denyNextPlay = true;
  core.posts.play.length = 0;
  await evaluate("document.getElementById('btn-play').click()");
  await waitFor("!document.getElementById('player-error').hidden", 8000);
  await sleep(600);
  assert.equal(core.posts.play.length, 1);
  assert.equal(await evaluate(state), "Completed", "state stays authoritative (COMPLETED), not optimistic Playing");
  assert.ok((await evaluate("document.getElementById('player-error').textContent")).length > 0);
});

await test("denial: pause rejection surfaces the failure without optimistic Paused", async () => {
  core.playback = { ...core.playback, state: "PLAYING" };
  assert.equal(await waitFor(`document.getElementById('player-state').textContent === 'Playing' && !document.getElementById('btn-pause').disabled`, 8000), true);
  core.denyNextPause = true;
  core.posts.pause.length = 0;
  await evaluate("document.getElementById('btn-pause').click()");
  await waitFor("!document.getElementById('player-error').hidden", 8000);
  await sleep(500);
  assert.equal(core.posts.pause.length, 1);
  assert.equal(await evaluate(state), "Playing", "state stays authoritative after rejection");
});

/* ---------------- SECONDARY CONTROLS + LED ---------------- */

await test("secondary: tempo + lights live in the More sheet on every viewport", async () => {
  // IMMERSIVE composition update: the stage keeps the main screen
  // essential-only, so the secondary controls live in the accepted sheet
  // on ALL viewports and the More button is always offered.
  await viewport(1024, 768);
  assert.equal(await evaluate("document.getElementById('ui008-player-options').hidden"), false);
  assert.equal(await evaluate("document.getElementById('ui008-player-secondary').parentElement.id"), "ui008-player-secondary-sheet");
  assert.equal(await evaluate("!!document.getElementById('tempo')"), true);
  await evaluate("document.getElementById('ui008-player-options').click()");
  assert.equal(await evaluate("!!document.getElementById('tempo')"), true);
  assert.equal(await evaluate("document.getElementById('tempo').closest('#ui008-player-sheet') !== null"), true);
});

await test("secondary: phone moves the SAME control set into the sheet", async () => {
  await viewport(390, 844);
  assert.equal(await evaluate("!document.getElementById('ui008-player-options').hidden"), true);
  assert.equal(await evaluate("document.getElementById('ui008-player-secondary').parentElement.id"), "ui008-player-secondary-sheet");
  await evaluate("document.getElementById('ui008-player-options').click()");
  await waitFor("!document.getElementById('ui008-player-sheet').hidden");
  assert.equal(await evaluate("document.getElementById('tempo').closest('#ui008-player-sheet') !== null"), true);
  const sheetRole = await evaluate("document.querySelector('#ui008-player-sheet .bottom-sheet').getAttribute('role')");
  assert.equal(sheetRole, "dialog");
  await cdp("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape" });
  await cdp("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape" });
  await waitFor("document.getElementById('ui008-player-sheet').hidden");
  await viewport(1024, 768);
});

await test("LED quick toggle uses the existing semantic PUT exactly once", async () => {
  core.ledConfigured = true;
  core.led = { ...core.led, configured: true, enabled: false, connection: "CONNECTED" };
  await sleep(1300);
  assert.equal(await evaluate("document.getElementById('ui008-led-quick').disabled"), false);
  core.posts.led.length = 0;
  await evaluate("document.getElementById('ui008-led-quick').click()");
  await waitFor("document.getElementById('ui008-led-quick').getAttribute('aria-pressed') === 'true'", 8000);
  await sleep(300);
  assert.deepEqual(core.posts.led, [{ enabled: true }], JSON.stringify(core.posts.led));
});

/* ---------------- VISUALIZATION WORKSPACE ---------------- */

await test("workspace: one presented scene; keyboard and visual are Learn workspace views", async () => {
  // IMMERSIVE composition update: the shared workspace presents exactly
  // ONE scene view at a time; the keyboard appears in the Piano view.
  await route("learn");
  const switchView = async (view) => {
    await evaluate(`(() => { const el = document.getElementById('ui008-view-learn'); el.value = '${view}'; el.dispatchEvent(new Event('change', { bubbles: true })); })()`);
    await sleep(150);
  };
  await switchView("falling");
  const falling = await evaluate(`(!document.getElementById('visual').hidden) && document.getElementById('piano-view').hidden && document.getElementById('hands').hidden`);
  assert.equal(falling, true, "Falling view presents the notes scene");
  await switchView("piano");
  const kb = await evaluate(`(() => {
    const piano = document.getElementById('live-keyboard');
    const r = piano.getBoundingClientRect();
    return { w: r.width, keys: piano.querySelectorAll('.piano-key').length, visible: !document.getElementById('piano-view').hidden };
  })()`);
  assert.ok(kb.visible && kb.w > 300 && kb.keys === 88, JSON.stringify(kb));
  await switchView("hands");
  assert.equal(await evaluate("!document.getElementById('hands').hidden"), true, "Hands view presents the hands scene");
  await switchView("falling");
});

await test("visualization: only implemented modes exist; switching is presentation-only", async () => {
  core.learning = { ...core.learning, state: "WAITING", song_id: "song-a", title: "Sonata in A", session_id: "sess-1", mode: "WAIT_FOR_NOTE", version: 3, step_index: 0, total_steps: 2, target: { target_id: "t:0", notes: [60], names: ["C4"], chord: false, onset_seconds: 4 } };
  await sleep(1300);
  const before = await evaluate(`JSON.stringify({
    position: document.getElementById('player-position').textContent,
    state: document.getElementById('player-state').textContent,
    session: document.getElementById('practice-target').textContent,
  })`);
  const modes = await evaluate(`[...document.querySelectorAll(".visual-mode-switch[aria-label='Visual mode'] button")].map(b => b.textContent.trim())`);
  assert.deepEqual(modes, ["Sheet", "Falling notes", "Hybrid"]);
  assert.equal(await evaluate("document.querySelectorAll('#visual [id*=keys-]').length"), 0, "no placeholder Keys mode");
  const postsBefore = core.posts.play.length + core.posts.pause.length + core.posts.resume.length + core.posts.stop.length;
  for (const id of ["visual-mode-sheet", "visual-mode-falling", "visual-mode-hybrid"]) {
    await evaluate(`document.getElementById('${id}').click()`);
    await sleep(120);
  }
  await sleep(400);
  const after = await evaluate(`JSON.stringify({
    position: document.getElementById('player-position').textContent,
    state: document.getElementById('player-state').textContent,
    session: document.getElementById('practice-target').textContent,
  })`);
  assert.equal(after, before, "visual switching must not move transport or Learning truth");
  const postsAfter = core.posts.play.length + core.posts.pause.length + core.posts.resume.length + core.posts.stop.length;
  assert.equal(postsAfter, postsBefore, "visual switching must not emit playback commands");
  // Restore the idle Learning session: the accepted practice/playback
  // mutex correctly disables Play while a session is WAITING, and later
  // evidence steps must be able to play.
  core.learning = { ...core.learning, state: "IDLE", song_id: null, title: null, session_id: null, step_index: null, total_steps: null, progress: 0, target: null, version: 4 };
  await sleep(1300);
});

await test("hands: UI-007 renderer initializes unchanged on the workspace", async () => {
  // IMMERSIVE composition update: present the Hands view first — the
  // renderer initializes lazily on first presentation (no background GLB
  // work for an unused view); the frozen renderer/asset contract below is
  // asserted unchanged.
  await evaluate(`(() => { const el = document.getElementById('ui008-view-learn'); el.value = 'hands'; el.dispatchEvent(new Event('change', { bubbles: true })); })()`);
  await waitFor("document.getElementById('hands')?.hidden === false");
  await viewport(1024, 768);
  await route("learn");
  await waitFor("globalThis.__pianoHands && globalThis.__pianoHands.info()", 60000);
  await waitFor("globalThis.__pianoHands.info().assetState === 'ready'", 30000);
  const info = await evaluate("globalThis.__pianoHands.info()");
  assert.ok(["webgpu", "webgl2"].includes(info.backend), info.backend);
  const rect = await evaluate(`(() => { const r = document.getElementById('hands-stage').getBoundingClientRect(); return { w: r.width, h: r.height }; })()`);
  assert.ok(rect.w >= 560 && rect.h >= 160, JSON.stringify(rect)); // immersive: the hands stage fills the stage scene's leftover height
});

await test("production GLB served byte-identical (hash verified)", async () => {
  const glb = await readFile(join(STATIC, "assets/piano-hand-tier-a.glb"));
  const hash = createHash("sha256").update(glb).digest("hex");
  assert.equal(hash, "e9e6b5981058468f3acdc7af07c77e029090c8ff91c14690e97782ad466970d3");
});

/* ---------------- PERFORMANCE MODE ---------------- */

await test("performance mode: denial fallback still dominates the workspace", async () => {
  // Immersive: performance dominance is measured on the PRESENTED notes
  // scene, so normalize the workspace to the Falling view first.
  await route("player");
  await evaluate(`(() => {
    globalThis.__fsDenied = 0;
    document.documentElement.requestFullscreen = () => {
      globalThis.__fsDenied += 1;
      return Promise.reject(new Error("denied"));
    };
  })()`);
  core.playback = { ...core.playback, state: "PLAYING" };
  await sleep(1300);
  await evaluate("document.getElementById('ui008-performance-btn').click()");
  await waitFor("document.body.dataset.ui008Performance === 'on'", 5000);
  assert.equal(await evaluate("globalThis.__fsDenied"), 1, "fullscreen requested exactly once");
  const layout = await evaluate(`(() => ({
    navHidden: getComputedStyle(document.querySelector('.primary-nav')).display === 'none',
    topbarHidden: getComputedStyle(document.querySelector('.topbar')).display === 'none',
    // U8P3-R1: an off-screen element also has height > 0 — the transport
    // must be inside the viewport AND hit-testable, not merely laid out.
    transport: (() => {
      const play = document.querySelector('#player .transport-btn--primary:not([hidden])');
      const r = play.getBoundingClientRect();
      const contained = r.width > 0 && r.height > 0 && r.bottom > 0 && r.top >= 0
        && r.top < innerHeight && r.bottom <= innerHeight && r.left >= 0 && r.right <= innerWidth;
      const hitEl = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      const hit = !!hitEl && (hitEl === play || play.contains(hitEl));
      return { contained, hit, top: Math.round(r.top), bottom: Math.round(r.bottom) };
    })(),
    playEnabled: !document.getElementById('btn-play').disabled,
    route: document.body.dataset.ui008Route,
    visualHeight: document.querySelector('.now-playing').getBoundingClientRect().height,
    viewportH: innerHeight,
  }))()`);
  assert.ok(layout.navHidden && layout.topbarHidden, JSON.stringify(layout));
  assert.ok(layout.transport.contained, `transport not contained in viewport: ${JSON.stringify(layout.transport)}`);
  assert.ok(layout.transport.hit, `transport not hit-testable: ${JSON.stringify(layout.transport)}`);
  assert.ok(layout.visualHeight >= layout.viewportH * 0.3, `workspace dominant: ${layout.visualHeight} of ${layout.viewportH}`);
  assert.equal(layout.route, "player");
});

await test("performance mode: Escape exits and the route survives without commands", async () => {
  const postsBefore = core.posts.play.length + core.posts.pause.length + core.posts.stop.length;
  await cdp("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape" });
  await cdp("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape" });
  await waitFor("document.body.dataset.ui008Performance === 'off'", 5000);
  assert.equal(await evaluate("document.body.dataset.ui008Route"), "player");
  await sleep(300);
  const postsAfter = core.posts.play.length + core.posts.pause.length + core.posts.stop.length;
  assert.equal(postsAfter, postsBefore, "performance exit must not emit commands");
  assert.equal(await evaluate("document.getElementById('player-state').textContent"), "Playing", "transport truth unchanged");
});

await test("performance mode: granted fullscreen integrates cleanly", async () => {
  await evaluate(`(() => {
    globalThis.__fsGranted = 0;
    Object.defineProperty(document, 'fullscreenElement', { configurable: true, get: () => globalThis.__fsGranted ? document.documentElement : null });
    document.documentElement.requestFullscreen = () => { globalThis.__fsGranted += 1; return Promise.resolve(); };
  })()`);
  await evaluate("document.getElementById('ui008-performance-btn').click()");
  await waitFor("document.body.dataset.ui008Performance === 'on'", 5000);
  assert.equal(await evaluate("document.getElementById('ui008-performance-btn').getAttribute('aria-pressed')"), "true");
  await evaluate("document.getElementById('ui008-performance-btn').click()");
  await waitFor("document.body.dataset.ui008Performance === 'off'", 5000);
  await evaluate("globalThis.__fsGranted = 0");
  await sleep(200);
});

await test("performance mode: leaving the Player route exits presentation only", async () => {
  await evaluate("document.getElementById('ui008-performance-btn').click()");
  await waitFor("document.body.dataset.ui008Performance === 'on'", 5000);
  await route("library");
  await waitFor("document.body.dataset.ui008Performance === 'off'", 5000);
  assert.equal(await evaluate("document.getElementById('player-state').textContent"), "Playing");
  await route("player");
});

await test("performance mode (U8P3-R1): pinned transport visible and reachable at every canonical viewport", async () => {
  await route("player");
  core.playback = { ...core.playback, state: "PLAYING" };
  await sleep(1300);
  await evaluate("document.getElementById('ui008-performance-btn').click()");
  await waitFor("document.body.dataset.ui008Performance === 'on'", 5000);
  const problems = [];
  for (const [w, h] of [[390, 844], [768, 1024], [899, 900], [900, 900], [1024, 768], [1440, 900], [1920, 1080]]) {
    await viewport(w, h);
    const m = await evaluate(`(() => {
      const inViewport = (el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top >= 0
          && r.top < innerHeight && r.bottom <= innerHeight && r.left >= 0 && r.right <= innerWidth;
      };
      const play = document.getElementById('btn-play');
      const pause = document.getElementById('btn-pause');
      const stop = document.getElementById('btn-stop');
      const r = pause.getBoundingClientRect();
      const hitEl = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      const pauseHit = !!hitEl && (hitEl.id === 'btn-pause' || !!hitEl.closest('#btn-pause'));
      const layout = document.querySelector('.layout');
      const de = document.documentElement;
      const stage = document.querySelector('.now-playing').getBoundingClientRect();
      return {
        playHidden: play.hidden, pause: inViewport(pause), stop: inViewport(stop),
        pauseEnabled: !pause.disabled, stopEnabled: !stop.disabled, pauseHit,
        stagePctVh: Math.round((stage.height / innerHeight) * 100),
        layoutScrolls: layout.scrollHeight > layout.clientHeight + 1,
        documentScrolls: de.scrollHeight > de.clientHeight + 1,
        overflowX: Math.max(de.scrollWidth - de.clientWidth, document.body.scrollWidth - document.body.clientWidth),
        route: document.body.dataset.ui008Route,
        perfFlag: document.body.dataset.ui008Performance,
      };
    })()`);
    const failures = [];
    if (!m.playHidden) failures.push("inactive Play should be hidden during playback");
    if (!m.pause) failures.push("pause not in viewport");
    if (!m.stop) failures.push("stop not in viewport");
    if (!m.pauseEnabled) failures.push("pause disabled");
    if (!m.stopEnabled) failures.push("stop disabled");
    if (!m.pauseHit) failures.push("pause not hit-testable");
    if (m.stagePctVh < 30) failures.push(`stage ${m.stagePctVh}%vh`);
    if (m.documentScrolls) failures.push("document scrolls instead of workspace");
    if (m.overflowX > 1) failures.push(`overflowX ${m.overflowX}`);
    if (m.route !== "player") failures.push(`route ${m.route}`);
    if (m.perfFlag !== "on") failures.push("performance flag lost");
    if (failures.length) problems.push({ w, failures, ...m });
  }
  await evaluate("document.getElementById('ui008-performance-btn').click()");
  await waitFor("document.body.dataset.ui008Performance === 'off'", 5000);
  await viewport(1024, 768);
  assert.deepEqual(problems, [], JSON.stringify(problems, null, 1));
});

/* ---------------- RESPONSIVE / ACCESSIBILITY ---------------- */

const matrix = [[390, 844, "bottom"], [768, 1024, "bottom"], [899, 900, "bottom"], [900, 900, "rail"], [1024, 768, "rail"], [1440, 900, "rail"], [1920, 1080, "rail"]];

await test("responsive: transport + timeline usable at all viewports without overflow", async () => {
  const problems = [];
  for (const [w, h, mode] of matrix) {
    await viewport(w, h);
    const m = await evaluate(`(() => {
      const rail = getComputedStyle(document.querySelector('.primary-nav')).display !== 'none';
      const bottom = getComputedStyle(document.querySelector('.bottom-nav')).display !== 'none';
      const play = document.querySelector('#player .transport-btn--primary:not([hidden])');
      const pr = play.getBoundingClientRect();
      const cs = getComputedStyle(play);
      const progress = document.getElementById('player-progress').getBoundingClientRect();
      return {
        rail, bottom,
        performance: document.body.dataset.ui008Performance,
        route: document.body.dataset.ui008Route,
        playDisplayed: cs.display !== 'none',
        playVisible: pr.width > 0 && pr.height > 0 && pr.bottom <= innerHeight + 1,
        progressVisible: progress.width >= Math.min(200, innerWidth * 0.5) && progress.height > 0,
      };
    })()`);
    if (m.rail !== (mode === "rail") || m.bottom !== (mode === "bottom") || m.overflow > 0 || !m.playVisible || !m.progressVisible) {
      problems.push({ w, ...m, mode });
    }
  }
  assert.deepEqual(problems, [], JSON.stringify(problems));
});

await test("accessibility: labels, roles, targets and keyboard contract", async () => {
  await viewport(390, 844);
  const a11y = await evaluate(`(() => ({
    transportGroup: document.querySelector('.transport-controls').getAttribute('role'),
    labelled: [...document.querySelectorAll('.transport-controls .btn')].every(b => (b.textContent.trim().length > 0 || b.getAttribute('aria-label'))),
    playSize: (() => { const r = document.querySelector('#player .transport-btn--primary:not([hidden])').getBoundingClientRect(); return [r.width, r.height]; })(),
    small: [...document.querySelectorAll('#player button,#player input,#player summary')]
      .filter(el => { const s = getComputedStyle(el), r = el.getBoundingClientRect();
        return !el.disabled && s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0; })
      .filter(el => { const r = el.getBoundingClientRect(); return r.width < 44 || r.height < 44; })
      .map(el => el.id),
    ariaNow: document.getElementById('player-progress').getAttribute('aria-valuenow') !== null,
    liveRegion: document.getElementById('player-freshness').getAttribute('aria-live'),
  }))()`);
  assert.equal(a11y.transportGroup, "group");
  assert.equal(a11y.labelled, true);
  assert.ok(a11y.playSize[0] >= 56 && a11y.playSize[1] >= 56, JSON.stringify(a11y.playSize));
  assert.deepEqual(a11y.small, [], JSON.stringify(a11y.small));
  assert.equal(a11y.ariaNow, true);
  assert.equal(a11y.liveRegion, "polite");
});

await test("reduced motion removes workspace animations", async () => {
  await cdp("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
  await route("library");
  await route("player");
  assert.equal(await evaluate("getComputedStyle(document.getElementById('hands')).animationName"), "none");
  await cdp("Emulation.setEmulatedMedia", { features: [] });
});

/* ---------------- RESOURCE LIFECYCLE ---------------- */

await test("lifecycle: repeated Player→Library→Player stays stable (no leaks)", async () => {
  await viewport(1024, 768);
  const before = await evaluate(`(() => ({
    transports: document.querySelectorAll('.transport-controls').length,
    canvases: document.querySelectorAll('canvas').length,
    playButtons: document.querySelectorAll('#btn-play').length,
    results: document.querySelectorAll('#results .song-item').length,
  }))()`);
  for (const dest of ["library", "player", "library", "player", "home", "player"]) {
    await route(dest);
    await sleep(150);
  }
  const after = await evaluate(`(() => ({
    transports: document.querySelectorAll('.transport-controls').length,
    canvases: document.querySelectorAll('canvas').length,
    playButtons: document.querySelectorAll('#btn-play').length,
    results: document.querySelectorAll('#results .song-item').length,
  }))()`);
  assert.deepEqual(after, before, `${JSON.stringify(before)} -> ${JSON.stringify(after)}`);
  // One click still means exactly one command after all the routing.
  core.posts.pause.length = 0;
  core.playback = { ...core.playback, state: "PLAYING" };
  await sleep(1300);
  await evaluate("document.getElementById('btn-pause').click()");
  await waitFor(`document.getElementById('player-state').textContent === 'Paused'`, 8000);
  await sleep(200);
  assert.equal(core.posts.pause.length, 1);
});

/* ---------------- VISUAL EVIDENCE ---------------- */

const debugSnap = () => evaluate(`JSON.stringify({
  chip: document.getElementById('player-state').textContent,
  title: document.getElementById('player-title').textContent,
  pos: document.getElementById('player-position').textContent,
  rem: document.getElementById('player-remaining').textContent,
  playDisabled: document.getElementById('btn-play').disabled,
})`);
const mustWait = async (label, expression, timeout = 8000) => {
  const ok = await waitFor(expression, timeout);
  if (!ok) throw new Error(`${label} failed: ${await debugSnap()} posts=${JSON.stringify(core.posts)}`);
};

await test("fresh visual evidence captures the Player workspace", async () => {
  // no song first, from a clean reload (no selection, honest empty state)
  setIdle();
  core.learning = { available: true, state: "IDLE", song_id: null, title: null, step_index: null, total_steps: null, completed_steps: 0, incorrect_attempts: 0, progress: 0, started_at: null, ended_at: null, error: null, midi_connection: "CONNECTED", session_id: null, mode: "WAIT_FOR_NOTE", hand_selection: null, section: null, target_tempo: null, accepted_notes: [], latest_outcome: "NONE", generation: 0, version: 0, updated_at: null, target: null, repetition: { current: 0, required: 1, successful: 0, failed: 0 } };
  core.posts.play.length = 0; core.posts.pause.length = 0; core.posts.stop.length = 0;
  await cdp("Page.reload", { ignoreCache: true });
  await waitFor("document.documentElement?.dataset.ui008ShellReady === 'true'");
  await mustWait("no-song", `document.getElementById('player-state').textContent === 'No song'`);
  await viewport(1024, 768);
  await sleep(300);
  await capture("player-tablet-1024x768-no-song.png");
  // ready
  await evaluate("document.querySelectorAll('.btn-song')[1].click()");
  await mustWait("ready-title", "document.getElementById('player-title').textContent === 'Sonata in B'");
  await mustWait("ready-chip", `document.getElementById('player-state').textContent === 'Stopped'`);
  await sleep(300);
  await capture("player-tablet-1024x768-ready.png");
  // playing
  await evaluate("document.getElementById('btn-play').click()");
  await mustWait("playing", `document.getElementById('player-state').textContent === 'Playing'`);
  core.playback = { ...core.playback, position_seconds: 96 };
  await sleep(1300);
  await mustWait("playing-held", `document.getElementById('player-state').textContent === 'Playing'`);
  await capture("player-tablet-1024x768-playing.png");
  // paused
  await evaluate("document.getElementById('btn-pause').click()");
  await mustWait("paused", `document.getElementById('player-state').textContent === 'Paused'`);
  await sleep(250);
  await capture("player-tablet-1024x768-paused.png");
  // desktop + wide + portrait + phone
  for (const [w, h, name] of [[1440, 900, "player-desktop-1440x900-playing-idle.png"], [1920, 1080, "player-wide-1920x1080.png"], [768, 1024, "player-tablet-portrait-768x1024.png"]]) {
    await viewport(w, h);
    await sleep(300);
    await capture(name);
  }
  await viewport(390, 844);
  await sleep(300);
  await capture("player-phone-390x844.png");
  // performance mode capture (fallback or granted)
  await viewport(1024, 768);
  await evaluate("document.getElementById('ui008-performance-btn').click()");
  await waitFor("document.body.dataset.ui008Performance === 'on'", 5000);
  await sleep(400);
  await capture("player-performance-mode-1024x768.png");
  await evaluate("document.getElementById('ui008-performance-btn').click()");
  await waitFor("document.body.dataset.ui008Performance === 'off'", 5000);
  measures.captures = 9;
  assert.equal(true, true);
});

ws.close();
chrome.kill("SIGTERM");
await new Promise((done) => server.close(done));
await Promise.race([new Promise((done) => chrome.once("exit", done)), sleep(2000)]);
await rm(profile, { recursive: true, force: true });

if (failures) {
  console.error(`FAIL ${failures}; PASS ${passes}`);
  process.exit(1);
}
console.log(`PASS ${passes}/${passes}`);
console.log(`P3_MEASURES ${JSON.stringify(measures)}`);
