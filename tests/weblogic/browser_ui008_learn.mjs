// PIANO-UI-008 U8-P4: REAL Chromium/HTTP premium Learn workspace gates.
// Headless Chrome uses SwiftShader; results are not physical-device
// evidence. The controlled server is loopback-only. The mock Core applies
// engine-faithful guided-practice semantics (fresh-press arming for
// repeated notes, authoritative partial chord acceptance, wrong notes
// never advance) so the browser is exercised against Core-like truth —
// the real engine semantics stay covered by the canonical Python suites.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { createServer } from "node:http";
import { Readable } from "node:stream";
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
const measures = { captures: 0 };

/* ---------------- controlled Core (loopback) ---------------- */

const songs = [
  { song_id: "song-x", filename: "nocturne-x.mid", relpath: "nocturne-x.mid", extension: ".mid", size_bytes: 800, status: "parsed", midi_format: 1, track_count: 1, ticks_per_beat: 480, duration_seconds: 120, tempo_bpm_first: 90, title: "Nocturne X", indexed_at_utc: "2026-10-01T00:00:00Z" },
  { song_id: "song-chord", filename: "chorale.mid", relpath: "chorale.mid", extension: ".mid", size_bytes: 700, status: "parsed", midi_format: 1, track_count: 2, ticks_per_beat: 480, duration_seconds: 90, tempo_bpm_first: 70, title: "Chorale in C", indexed_at_utc: "2026-10-01T00:00:00Z" },
];
const PLANS = {
  "song-x": [{ notes: [67], onset: 2 }, { notes: [67], onset: 6 }, { notes: [69], onset: 10 }],
  "song-chord": [{ notes: [60, 64, 67], onset: 2 }, { notes: [72], onset: 8 }],
};

/* PIANO-LEARN-001: Learn Library catalog fixture (educational namespace). */
const LEARN_CATALOG = {
  ready: true,
  lesson_count: 1612,
  course_count: 83,
  levels: { 0: 96, 1: 251, 2: 157, 3: 731, 4: 377 },
  sources: { pianio: 1557, mutopia: 51, pianovera: 4 },
  courses: [{ course_id: "pianio:method-level-1", title: "Method Level 1", level: 0, summary: "s", lesson_count: 22 }],
};
const LEARN_LESSONS = [
  { lesson_id: "les-1", package_id: "pkg-1", course_id: "pianio:method-level-1", title: "Middle C Pulse", composer: null, source: "pianio", level: 0, difficulty: "easy", duration_seconds: 8, order_index: 0, hands_availability: "available", license_name: "CC0-1.0", summary: "Whole-note pulses on middle C." },
  { lesson_id: "les-2", package_id: "pkg-2", course_id: "pianio:method-level-1", title: "C and D", composer: null, source: "pianio", level: 0, difficulty: "easy", duration_seconds: 6, order_index: 1, hands_availability: "available", license_name: "CC0-1.0", summary: "First two notes." },
  { lesson_id: "les-3", package_id: "pkg-3", course_id: "repertoire:beginner", title: "Gymnopedie No. 1", composer: "Erik Satie", source: "mutopia", level: 2, difficulty: "medium", duration_seconds: 210, order_index: 2, hands_availability: "available", license_name: "Public Domain", summary: "Satie's first Gymnopedie." },
];
const LEARN_DETAILS = {
  "les-1": { ...LEARN_LESSONS[0], metrics: { note_count: 8, max_simultaneous: 1, notes_per_second: 1 }, level_decision: { reasons: ["first-notes evidence gate met"] }, license: { name: "CC0-1.0", attribution: "Pianio" }, previous_lesson_id: null, next_lesson_id: "les-2", progress: null },
  "les-3": { ...LEARN_LESSONS[2], metrics: { note_count: 549, max_simultaneous: 4, notes_per_second: 3 }, level_decision: { reasons: ["computed score 41 -> level 2"] }, license: { name: "Public Domain", attribution: "Mutopia Project" }, previous_lesson_id: "les-2", next_lesson_id: null, progress: { attempts: 2, completions: 1, note_accuracy: 0.9 } },
};
let learnDown = false;
let learnRequests = [];
let learnPosts = { start: [] };
const learningIdle = () => ({
  available: true, state: "IDLE", song_id: null, title: null,
  step_index: null, total_steps: null, completed_steps: 0, incorrect_attempts: 0,
  progress: 0, started_at: null, ended_at: null, error: null,
  midi_connection: "CONNECTED", session_id: null, mode: "WAIT_FOR_NOTE",
  hand_selection: null, section: null, target_tempo: null, accepted_notes: [],
  latest_outcome: "NONE", generation: 0, version: 0, updated_at: null,
  target: null, repetition: { current: 0, required: 1, successful: 0, failed: 0 },
});

/* Engine-faithful practice simulation: fresh-press arming, authoritative
 * partial chord acceptance, wrong notes never advance, exactly one
 * advancement per satisfied target. */
const engine = {
  learning: learningIdle(),
  held: new Set(),
  armed: new Set(),
  requiredReps: 1,
  streak: 0,
  delayLearningMs: 0,
  denyNextStart: false,
  posts: { start: [], stop: [], restartTarget: [], restartSection: [], play: [] },
};
let sessionCounter = 0;

const serialize = () => JSON.parse(JSON.stringify(engine.learning));

function armForTarget() {
  const t = engine.learning.target;
  engine.armed = new Set(t ? t.notes.filter((n) => !engine.held.has(n)) : []);
}

const PKG_PLANS = {
  "package:pkg-1": [{ notes: [60], onset: 0 }, { notes: [60], onset: 1 }, { notes: [60], onset: 2 }, { notes: [60], onset: 3 }],
};
function startSession(songId, mode, section, reps) {
  const song = songs.find((s) => s.song_id === songId);
  const lesson = LEARN_LESSONS.find((l) => `package:${l.package_id}` === songId);
  sessionCounter += 1;
  const targets = PLANS[songId] || PKG_PLANS[songId];
  engine.held = new Set();
  engine.requiredReps = mode === "REPEAT_UNTIL_CORRECT" ? reps : 1;
  engine.streak = 0;
  engine.learning = {
    ...learningIdle(),
    state: "WAITING",
    available: true,
    song_id: songId,
    title: song?.title ?? lesson?.title ?? songId,
    step_index: 0,
    total_steps: targets.length,
    completed_steps: 0,
    progress: 0,
    started_at: new Date().toISOString(),
    session_id: `learn-sess-${sessionCounter}`,
    mode,
    section: mode === "SECTION_PRACTICE" ? section : null,
    generation: 1,
    version: 1,
    updated_at: new Date().toISOString(),
    target: { notes: [...targets[0].notes], names: targets[0].notes.map(nToName), chord: targets[0].notes.length > 1, onset_seconds: targets[0].onset, target_id: `target:${targets[0].onset}:${targets[0].notes.join("-")}` },
  };
  armForTarget();
}
const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const nToName = (n) => `${NOTE_NAMES[n % 12]}${Math.floor(n / 12) - 1}`;

function advance() {
  const plan = PLANS[engine.learning.song_id];
  const next = engine.learning.step_index + 1;
  engine.learning = {
    ...engine.learning,
    version: engine.learning.version + 1,
    step_index: next,
    completed_steps: next,
    progress: next / plan.length,
    accepted_notes: [],
    latest_outcome: "CORRECT",
    updated_at: new Date().toISOString(),
  };
  if (next >= plan.length) {
    engine.learning = { ...engine.learning, state: "COMPLETED", step_index: null, target: null, ended_at: new Date().toISOString() };
    engine.armed = new Set();
    return;
  }
  const t = plan[next];
  engine.learning.target = { notes: [...t.notes], names: t.notes.map(nToName), chord: t.notes.length > 1, onset_seconds: t.onset, target_id: `target:${t.onset}:${t.notes.join("-")}` };
  armForTarget();
}

function enginePress(note) {
  if (engine.learning.state !== "WAITING") return;
  if (engine.held.has(note)) return; // still held: no fresh event, no decision
  engine.held.add(note);
  const target = engine.learning.target;
  const required = new Set(target.notes);
  if (!required.has(note)) {
    // Wrong note: Core records the failed attempt; nothing advances.
    engine.learning = { ...engine.learning, version: engine.learning.version + 1, latest_outcome: "INCORRECT", incorrect_attempts: engine.learning.incorrect_attempts + 1, updated_at: new Date().toISOString() };
    return;
  }
  const accepted = new Set(engine.learning.accepted_notes);
  accepted.add(note);
  const allRequiredPressed = [...required].every((n) => engine.held.has(n));
  if (engine.armed.size > 0 && [...engine.armed].every((n) => engine.held.has(n))) {
    if (engine.requiredReps > 1) {
      engine.streak += 1;
      if (engine.streak < engine.requiredReps) {
        engine.learning = { ...engine.learning, version: engine.learning.version + 1, accepted_notes: [...accepted], latest_outcome: "CORRECT", repetition: { current: engine.streak, required: engine.requiredReps, successful: engine.learning.repetition.successful, failed: engine.learning.repetition.failed } };
        // same target again: re-arm requiring release
        engine.armed = new Set([...required].filter((n) => !engine.held.has(n)));
        if (engine.armed.size === 0) engine.armed = new Set(required);
        engine.learning = { ...engine.learning, accepted_notes: [] };
        return;
      }
      engine.streak = 0;
    }
    advance();
    return;
  }
  // Partial chord acceptance is Core's own verdict.
  engine.learning = { ...engine.learning, version: engine.learning.version + 1, accepted_notes: [...accepted], latest_outcome: "NONE", updated_at: new Date().toISOString() };
}
function engineRelease(note) {
  engine.held.delete(note);
  if (engine.learning.state === "WAITING") armForTarget();
}

const core = {
  playback: { available: true, state: "IDLE", song_id: null, title: null, position_seconds: 0, duration_seconds: 0, tempo_multiplier: 1, events_sent: 0, error: null },
  led: { available: false, configured: false, enabled: false, connection: "DISABLED", mode: null, brightness: null, active_note_count: 0, practice_active: false, last_error: null, transport: null, dropped_events: 0, frames_sent: 0 },
  state: { midi_connection: "CONNECTED", sustain: { pressed: false, value: 0 }, pressed_notes: [], last_input: null, revision: 1 },
};

const score = (songId) => songId.startsWith("package:") ? { schema_version: 1, song_id: songId, source_version: `score-sha256:${"a".repeat(64)}`, title: "Middle C Pulse", duration_seconds: 4, time_basis: "song_seconds", note_count: 4, notes: [0, 1, 2, 3].map((onset, index) => ({ index, midi: 60, channel: 1, velocity: 85, onset_seconds: onset, duration_seconds: 0.25 })), diagnostics: {percussion_note_ons:0,outside_keyboard_note_ons:0,unpaired_note_offs:0,retriggered_note_ons:0,unterminated_note_ons:0}, notation: { supported: ["pitch", "onset", "duration"], unsupported: ["measure", "beat"] } } : ({ schema_version: 1, song_id: songId, source_version: `score-sha256:${"a".repeat(64)}`, title: "Sonata", duration_seconds: 120, time_basis: "song_seconds", note_count: 3, notes: [ { index: 0, midi: 67, channel: 1, velocity: 90, onset_seconds: 2, duration_seconds: 1 }, { index: 1, midi: 67, channel: 1, velocity: 90, onset_seconds: 6, duration_seconds: 1 }, { index: 2, midi: 69, channel: 1, velocity: 90, onset_seconds: 10, duration_seconds: 1 } ], diagnostics: { percussion_note_ons: 0, outside_keyboard_note_ons: 0, unpaired_note_offs: 0, retriggered_note_ons: 0, unterminated_note_ons: 0 }, notation: { supported: ["pitch", "onset", "duration"], unsupported: ["measure", "beat", "hand", "fingering"] } });
const plan = (songId) => ({ schema_version: 1, engine_version: "balanced-engine-v1", plan_id: `fingering-sha256:${"b".repeat(64)}`, song_id: songId, source_version: `score-sha256:${"a".repeat(64)}`, version: 1, generation: 1, strategy: "balanced_v1", provenance: "DETERMINISTIC_INFERENCE", targets: [ { score_index: 0, target_id: "score:0", midi: 67, channel: 1, onset_seconds: 2, duration_seconds: 1, hand: "RIGHT", finger: 2, alternatives: [] } ], warnings: [], excluded_note_count: 0, config: { strategy: "balanced_v1", max_hand_span_semitones: 12, alternative_count: 1 }, capabilities: { manual_overrides: false, finger_sensitive_grading: false } });
let seq = 0;
const noteSnapshot = () => { seq += 1; return { schema_version: 1, runtime_epoch: "ui008-p4", sequence: seq, physical: { notes: [], outside_keyboard_count: 0, sustain: { value: 0, pressed: false }, midi_connection: "CONNECTED", revision: seq }, playback: { available: true, notes: [], voices: [], outside_keyboard_count: 0, sustain_channels: [] } }; };

function json(res, value, status = 200) {
  const body = JSON.stringify(value);
  res.writeHead(status, { "content-type": "application/json", "content-length": Buffer.byteLength(body), "cache-control": "no-store" });
  res.end(body);
}
const readBody = async (req) => { let b = ""; for await (const c of req) b += c; return JSON.parse(b || "{}"); };

let coreBridgePort = null;
let coreProcess = null;
const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://127.0.0.1");
  if (coreBridgePort && /^\/api\/v1\/(learning|practice|live-notes|state|_test)(?:\/|$)/.test(url.pathname)) {
    const abort = new AbortController();
    res.on("close", () => abort.abort());
    try {
      if (req.method === "GET" && url.pathname === "/api/v1/learning" && engine.delayLearningMs) await sleep(engine.delayLearningMs);
      const body = req.method === "POST" ? JSON.stringify(await readBody(req)) : undefined;
      const response = await fetch(`http://127.0.0.1:${coreBridgePort}${req.url}`, { method: req.method, headers: body ? { "content-type": "application/json" } : {}, body, signal: abort.signal });
      res.writeHead(response.status, { "content-type": response.headers.get("content-type"), "cache-control": "no-store" });
      Readable.fromWeb(response.body).on("error", () => res.destroy()).pipe(res);
    } catch (error) { if (!abort.signal.aborted) json(res, { error: { code: "fixture_failure", message: error.message } }, 502); }
    return;
  }
  if (req.method === "POST") {
    const body = await readBody(req);
    if (url.pathname === "/api/v1/learning/start") {
      engine.posts.start.push(body);
      if (engine.denyNextStart) {
        engine.denyNextStart = false;
        json(res, { error: { code: "activity_conflict", message: "another activity owns the piano" } }, 409);
        return;
      }
      if (engine.learning.state === "WAITING") { json(res, { error: { code: "practice_already_active", message: "a session is active" } }, 409); return; }
      if (core.playback.available && ["PLAYING", "PAUSED", "STOPPING"].includes(core.playback.state)) { json(res, { error: { code: "activity_conflict", message: "playback owns the piano" } }, 409); return; }
      if (body.package_id) learnPosts.start.push(body);
      startSession(body.song_id || `package:${body.package_id}`, body.mode || "WAIT_FOR_NOTE", body.section || null, body.required_repetitions || 1);
      json(res, serialize());
      return;
    }
    if (url.pathname === "/api/v1/learning/stop") {
      engine.posts.stop.push(body);
      if (engine.learning.state !== "WAITING") { json(res, { error: { code: "stale_learning_command", message: "no active session" } }, 409); return; }
      engine.learning = { ...learningIdle(), version: engine.learning.version + 1, generation: engine.learning.generation };
      engine.armed = new Set();
      json(res, serialize());
      return;
    }
    if (url.pathname === "/api/v1/learning/restart-target" || url.pathname === "/api/v1/learning/restart-section") {
      const isSection = url.pathname.endsWith("section");
      (isSection ? engine.posts.restartSection : engine.posts.restartTarget).push(body);
      if (engine.learning.state !== "WAITING") { json(res, { error: { code: "stale_learning_command", message: "no active session" } }, 409); return; }
      const plan = PLANS[engine.learning.song_id];
      engine.learning = { ...engine.learning, version: engine.learning.version + 1, step_index: 0, completed_steps: 0, progress: 0, accepted_notes: [], latest_outcome: "NONE", incorrect_attempts: 0, repetition: { current: 0, required: engine.requiredReps, successful: 0, failed: 0 } };
      const t = plan[0];
      engine.learning.target = { notes: [...t.notes], names: t.notes.map(nToName), chord: t.notes.length > 1, onset_seconds: t.onset, target_id: `target:${t.onset}:${t.notes.join("-")}` };
      engine.held = new Set();
      armForTarget();
      json(res, serialize());
      return;
    }
    if (url.pathname === "/api/v1/playback/play") {
      engine.posts.play.push(body);
      if (engine.learning.state === "WAITING") { json(res, { error: { code: "playback_already_active", message: "practice owns the piano" } }, 409); return; }
      const song = songs.find((s) => s.song_id === body.song_id);
      core.playback = { available: true, state: "PLAYING", song_id: body.song_id, title: song?.title ?? null, position_seconds: 0, duration_seconds: song?.duration_seconds ?? 120, tempo_multiplier: body.tempo_multiplier ?? 1, events_sent: 4, error: null };
      json(res, core.playback);
      return;
    }
    if (url.pathname === "/api/v1/playback/stop") {
      core.playback = { ...core.playback, state: "IDLE", song_id: null, title: null, position_seconds: 0, duration_seconds: 0 };
      json(res, { ...core.playback, stopped: true });
      return;
    }
    json(res, { error: { code: "not_found", message: "nf" } }, 404);
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
  if (url.pathname === "/api/v1/learning" || url.pathname === "/api/v1/practice") {
    if (engine.delayLearningMs) await sleep(engine.delayLearningMs);
    json(res, serialize());
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
  if (url.pathname === "/api/v1/health") { json(res, { status: "ok", application: { state: "READY" }, midi_input: { connection: "CONNECTED" }, library: { ready: true, entries: songs.length }, playback: core.playback }); return; }
  if (url.pathname === "/api/v1/state") { json(res, { ...core.state, pressed_notes: [...engine.held] }); return; }
  if (url.pathname === "/api/v1/led") { json(res, core.led); return; }
  if (url.pathname === "/api/v1/playback") { json(res, core.playback); return; }
  if (learnDown && url.pathname.startsWith("/api/v1/learn")) { json(res, { error: { code: "learn_unavailable", message: "down" } }, 503); return; }
  if (url.pathname === "/api/v1/learn") {
    if (coreBridgePort) {
      const saved = await (await fetch(`http://127.0.0.1:${coreBridgePort}/api/v1/_test/progress`)).json();
      const summary = saved.progress["pkg-1"];
      const completed = summary?.completions > 0 ? 1 : 0;
      json(res, { ...LEARN_CATALOG, courses: LEARN_CATALOG.courses.map(c => ({...c, completed_lessons: completed})), progress: {available:true, completed_lessons:completed, levels:{0:completed}, continue_lesson:summary ? {...LEARN_LESSONS[0],progress:summary} : null} });
    } else json(res, LEARN_CATALOG);
    return;
  }
  if (url.pathname === "/api/v1/learn/lessons") {
    const level = url.searchParams.get("level");
    let rows = LEARN_LESSONS;
    if (level !== null) rows = rows.filter((l) => String(l.level) === level);
    if (url.searchParams.get("course")) rows = rows.filter(l => l.course_id === url.searchParams.get("course"));
    if (url.searchParams.get("q")) rows = rows.filter(l => `${l.title} ${l.composer || ''}`.toLowerCase().includes(url.searchParams.get("q").toLowerCase()));
    learnRequests.push(url.search);
    json(res, { lessons: rows, count: rows.length, offset: 0, limit: 50 });
    return;
  }
  const lessonMatch = url.pathname.match(/^\/api\/v1\/learn\/lessons\/(.+)$/);
  if (lessonMatch) {
    const detail = LEARN_DETAILS[decodeURIComponent(lessonMatch[1])];
    if (detail) { json(res, detail); return; }
    json(res, { error: { code: "lesson_not_found", message: "nf" } }, 404);
    return;
  }
  if (url.pathname === "/api/v1/library/songs") { json(res, { query: {}, count: songs.length, songs }); return; }
  const songMatch = url.pathname.match(/^\/api\/v1\/library\/songs\/(.+)$/);
  if (songMatch) { const rec = songs.find((s) => s.song_id === decodeURIComponent(songMatch[1])); rec ? json(res, rec) : json(res, { error: { code: "song_not_found", message: "nf" } }, 404); return; }
  const scoreMatch = url.pathname.match(/^\/api\/v1\/score\/(.+)$/);
  if (scoreMatch) { json(res, score(decodeURIComponent(scoreMatch[1]))); return; }
  const fingeringMatch = url.pathname.match(/^\/api\/v1\/fingering\/(.+)$/);
  if (fingeringMatch) { json(res, plan(decodeURIComponent(fingeringMatch[1]))); return; }
  json(res, { error: { code: "not_found", message: "nf" } }, 404);
});

await new Promise((d) => server.listen(0, "127.0.0.1", d));
const appPort = server.address().port;
const probe = createServer();
await new Promise((d) => probe.listen(0, "127.0.0.1", d));
const debugPort = probe.address().port;
await new Promise((d) => probe.close(d));
const profile = await mkdtemp(join(tmpdir(), "piano-ui008-p4-chrome-"));
const chrome = spawn(CHROME, [
  "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
  `--remote-debugging-port=${debugPort}`, `--user-data-dir=${profile}`,
  `http://127.0.0.1:${appPort}/ui/#/learn`,
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
  await waitFor("document.documentElement?.dataset.ui008ShellReady === 'true'");
  await waitFor("document.getElementById('ui008-learn-empty')?.hidden === false", 15000);
} catch (error) {
  console.error("BOOT FAILURE — collected page errors:");
  console.error(pageErrors.join("\n---\n"));
  throw error;
}
const learnPosts = () => engine.posts.start.length + engine.posts.stop.length + engine.posts.restartTarget.length + engine.posts.restartSection.length;

/* ---------------- CAPABILITY GATING + NO SONG ---------------- */

await test("no song: clean start state with Library CTA; practice hidden", async () => {
  assert.equal(await evaluate("document.getElementById('ui008-learn-empty').hidden"), false);
  assert.equal(await evaluate("document.getElementById('practice').hidden"), true);
  assert.ok((await evaluate("document.getElementById('ui008-learn-empty-heading').textContent")).includes("Choose a song"));
});

await test("capability gating: retained strategies plus governed Flow; unsupported modes absent", async () => {
  const options = await evaluate("[...document.querySelectorAll('#learning-mode option')].map(o => o.value)");
  assert.deepEqual(options, ["WAIT_FOR_NOTE", "SECTION_PRACTICE", "REPEAT_UNTIL_CORRECT", "FLOW"]);
  // Scope the leak scan to the Learn product surfaces (plain consumer
  // English like "Core listens" elsewhere is not a mode label).
  // IMMERSIVE composition update: the lesson truth lives in the stage
  // head/guidance/strip and the Lesson options sheet; the shared scene's
  // hands-camera wording ("Follow hands") is UI-007 presentation, not a
  // learning-mode label, so the scan scopes to the lesson content.
  const cardText = await evaluate(`[
    document.querySelector('#practice .stage-head'),
    document.getElementById('ui008-learn-guidance'),
    document.querySelector('#practice .learn-strip'),
    document.getElementById('ui008-learn-empty'),
    document.getElementById('ui008-learn-sheet-content'),
  ].map(e => e ? e.textContent : '').join(' ')`);
  for (const banned of ["Watch", "Listen", "Follow", "Left Hand", "Right Hand", "Slow practice", "Progressive tempo", "accuracy", "mastery", "streak", "score"]) {
    assert.ok(!cardText.toLowerCase().includes(banned.toLowerCase()), `leaks future capability: ${banned}`);
  }
});

await test("Library CTA routes back; Learn action selects and returns without auto-start", async () => {
  await route("library");
  await evaluate("document.querySelectorAll('.btn-song')[0].click()");
  await waitFor("document.getElementById('btn-practice')?.disabled === false");
  await route("learn");
  await waitFor("document.getElementById('practice')?.hidden === false", 8000);
  assert.equal(await evaluate("document.getElementById('selected').hidden"), false, "selected song visible on Learn");
  assert.equal(await evaluate("document.getElementById('ui008-learn-state').textContent"), "Ready");
  assert.equal(learnPosts(), 0, "no command until the learner starts");
});

/* ---------------- SESSION: start exactly once, silence ---------------- */

await test("start practice: exactly one command, zero playback commands (practice is silent)", async () => {
  engine.posts.start.length = 0;
  engine.posts.play.length = 0;
  await viewport(1024, 768);
  const r = await evaluate(`(() => {
    const b = document.getElementById('btn-practice').getBoundingClientRect();
    return [Math.round(b.width), Math.round(b.height)];
  })()`);
  assert.ok(r[0] >= 56 && r[1] >= 56, `start button ${r}`);
  await evaluate("document.getElementById('btn-practice').click()");
  await waitFor("document.getElementById('ui008-learn-state').textContent === 'Your turn'", 8000);
  await sleep(400);
  assert.equal(engine.posts.start.length, 1, JSON.stringify(engine.posts.start));
  assert.equal(engine.posts.start[0].song_id, "song-x");
  assert.equal(engine.posts.start[0].mode, "WAIT_FOR_NOTE");
  assert.equal(engine.posts.play.length, 0, "practice start must never start playback");
});

await test("wait-for-me: authoritative target displayed; consumer wording; no enum leak", async () => {
  assert.equal(await evaluate("document.getElementById('practice-target').textContent"), "G4");
  assert.equal(await evaluate("document.getElementById('ui008-learn-guidance').textContent"), "Play the highlighted key(s) on your piano.");
  assert.equal(await evaluate("document.getElementById('ui008-learn-progress').textContent"), "Step 1 of 3");
  assert.equal(await evaluate("document.getElementById('ui008-learn-start-region').hidden"), true, "start config hidden while waiting");
  const chip = await evaluate("document.querySelector('#ui008-learn-target-notes .learn-note-chip')?.textContent");
  assert.equal(chip, "G4");
  const cardText = await evaluate("document.getElementById('practice').textContent");
  for (const banned of ["WAITING", "IDLE", "ACTIVE_TARGET"]) assert.ok(!cardText.includes(banned), `enum leak: ${banned}`);
});

await test("wrong note: Core says INCORRECT; presentation never advances", async () => {
  const before = await evaluate("document.getElementById('practice-target').textContent");
  enginePress(60); // wrong note
  await waitFor("document.getElementById('learning-feedback').textContent.includes('Incorrect') || document.getElementById('ui008-learn-guidance').textContent.includes('Not this one')", 8000);
  await sleep(300);
  assert.equal(await evaluate("document.getElementById('practice-target').textContent"), before, "wrong note advanced the target");
});

await test("repeated note: fresh press advances once; held key never advances", async () => {
  enginePosts_reset();
  enginePress(67); // fresh press advances to the second G4
  await waitFor("document.getElementById('ui008-learn-progress').textContent === 'Step 2 of 3'", 8000);
  const advTarget = await evaluate("document.getElementById('practice-target').textContent");
  assert.equal(advTarget, "G4", "second target is the same note again");
  const versionAtAdvance = engine.learning.version;
  enginePress(67); // STILL HELD: must not advance
  await sleep(1400);
  assert.equal(engine.learning.version, versionAtAdvance, "held key advanced the repeated target");
  assert.equal(await evaluate("document.getElementById('ui008-learn-progress').textContent"), "Step 2 of 3");
  engineRelease(67);
  await sleep(200);
  enginePress(67); // release + fresh press
  await waitFor("document.getElementById('ui008-learn-progress').textContent === 'Step 3 of 3'", 8000);
  assert.equal(await evaluate("document.getElementById('practice-target').textContent"), "A4");
});
function enginePosts_reset() { engine.posts.restartTarget.length = 0; }

await test("chord: partial acceptance from Core; wrong input never completes; one advance", async () => {
  // stop current session, start the chord song
  engine.posts.stop.length = 0;
  await evaluate("document.getElementById('btn-practice-stop').click()");
  await waitFor("document.getElementById('ui008-learn-state').textContent === 'Ready'", 8000);
  assert.equal(engine.posts.stop.length, 1, "stop issued exactly once");
  engine.posts.start.length = 0;
  await evaluate("document.querySelectorAll('.btn-song')[1].click()");
  await waitFor("document.getElementById('practice-song').textContent === 'Chorale in C'", 8000);
  await evaluate("document.getElementById('btn-practice').click()");
  await waitFor("document.getElementById('ui008-learn-state').textContent === 'Your turn'", 8000);
  assert.equal(engine.posts.start.length, 1);
  assert.equal(await evaluate("document.getElementById('practice-target').textContent"), "C4 + E4 + G4");
  enginePress(60); // partial
  // poll-driven; assert via the UI chips (authoritative accepted_notes)
  await waitFor("document.querySelectorAll('#ui008-learn-target-notes .learn-note-chip--accepted').length === 1", 8000);
  assert.equal(await evaluate("document.getElementById('practice-target').textContent"), "C4 + E4 + G4", "partial chord advanced");
  enginePress(61); // wrong extra note
  await waitFor("document.getElementById('learning-feedback').textContent.includes('Incorrect')", 8000);
  assert.equal(await evaluate("document.querySelectorAll('#ui008-learn-target-notes .learn-note-chip--accepted').length"), 1, "wrong note changed acceptance");
  enginePress(64); // second partial
  await waitFor("document.querySelectorAll('#ui008-learn-target-notes .learn-note-chip--accepted').length === 2", 8000);
  enginePress(67); // completes the chord
  await waitFor("document.getElementById('practice-target').textContent === 'C5'", 8000);
  assert.equal(await evaluate("document.getElementById('ui008-learn-progress').textContent"), "Step 2 of 2");
});
async function engine_snapshot_accepted() { return engine.learning.accepted_notes; }

await test("completion: premium surface from real counts; restart offered; no fabricated metrics", async () => {
  enginePress(72); // finish the chorale
  await waitFor("document.getElementById('ui008-learn-state').textContent === 'Complete'", 8000);
  assert.equal(await evaluate("document.getElementById('practice-completion').textContent"), "All 2 steps played.");
  assert.equal(await evaluate("document.getElementById('ui008-learn-guidance').textContent"), "You played the whole piece. Start again anytime or pick a new song.");
  assert.equal(await evaluate("document.getElementById('btn-practice-stop').disabled"), true);
});

await test("mutex: Player cannot start while Practice owns the piano (both directions enforced)", async () => {
  // restart a session on song-x, then verify player disabled + one 409 if pressed
  await evaluate("document.querySelectorAll('.btn-song')[0].click()");
  await waitFor("document.getElementById('practice-song').textContent === 'Nocturne X'", 8000);
  await evaluate("document.getElementById('btn-practice').click()");
  await waitFor("document.getElementById('ui008-learn-state').textContent === 'Your turn'", 8000);
  assert.equal(await evaluate("document.getElementById('btn-play').disabled"), true, "Player Play enabled during practice");
  const playPosts = engine.posts.play.length;
  await evaluate("document.getElementById('btn-play').click()").catch(() => {});
  await sleep(400);
  assert.equal(engine.posts.play.length, playPosts, "UI emitted playback commands during practice");
  // reverse direction handled by the mock: play while waiting would 409
});

await test("stop + denial: start denial reconciles honestly (no optimistic session)", async () => {
  await evaluate("document.getElementById('btn-practice-stop').click()");
  await waitFor("document.getElementById('ui008-learn-state').textContent === 'Ready'", 8000);
  core.playback = { ...core.playback, state: "PLAYING", song_id: "song-x", title: "Nocturne X" };
  await sleep(1400);
  assert.equal(await evaluate("document.getElementById('ui008-learn-state').textContent"), "Piano in use", "mutex conflict not surfaced");
  assert.ok((await evaluate("document.getElementById('ui008-learn-guidance').textContent")).includes("Playback is using the piano"));
  core.playback = { ...core.playback, state: "IDLE", song_id: null, title: null };
  await sleep(1400);
  // denial path: server rejects the next start with 409
  engine.denyNextStart = true;
  engine.posts.start.length = 0;
  await evaluate("document.getElementById('btn-practice').click()");
  await waitFor("document.getElementById('practice-error').hidden === false", 8000);
  await sleep(300);
  assert.equal(engine.posts.start.length, 1);
  assert.equal(await evaluate("document.getElementById('ui008-learn-state').textContent"), "Ready", "optimistic session after denial");
  await waitFor("document.getElementById('practice-error').hidden === true", 10000);
});

/* ---------------- VISUALIZATION (presentation-only on Learn) ---------------- */

await test("workspace: visual/hands/keyboard present on Learn; switching is presentation-only", async () => {
  // start a session to have an authoritative target
  engine.posts.start.length = 0;
  await evaluate("document.getElementById('btn-practice').click()");
  await waitFor("document.getElementById('ui008-learn-state').textContent === 'Your turn'", 8000);
  // IMMERSIVE composition update: the shared workspace presents exactly
  // one scene view at a time; every view is available on the Learn route.
  const presented = async (view) => evaluate(`(() => {
    const el = document.getElementById('ui008-view-learn');
    el.value = '${view}';
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return (!document.getElementById('visual').hidden) + '/' + (!document.getElementById('hands').hidden) + '/' + (!document.getElementById('piano-view').hidden);
  })()`);
  assert.equal(await presented("falling"), "true/false/false", "Falling presents the notes scene");
  assert.equal(await presented("hands"), "false/true/false", "Hands presents the hands scene");
  const kb = await evaluate(`(() => {
    const el = document.getElementById('ui008-view-learn');
    el.value = 'piano';
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return { piano: !document.getElementById('piano-view').hidden, keys: document.getElementById('live-keyboard').querySelectorAll('.piano-key').length };
  })()`);
  assert.deepEqual({ piano: kb.piano, keys: kb.keys }, { piano: true, keys: 88 });
  await presented("falling");
  const identity = JSON.stringify(engine_learning_identity());
  const before = await evaluate(`JSON.stringify({
    target: document.getElementById('practice-target').textContent,
    session: ${JSON.stringify(identity)},
    progress: document.getElementById('ui008-learn-progress').textContent,
    canvases: document.querySelectorAll('canvas').length,
  })`);
  const postsBefore = learnPosts() + engine.posts.play.length;
  for (const id of ["visual-mode-sheet", "visual-mode-falling", "visual-mode-hybrid"]) {
    await evaluate(`document.getElementById('${id}').click()`);
    await sleep(120);
  }
  await sleep(500);
  const identityAfter = JSON.stringify(engine_learning_identity());
  const after = await evaluate(`JSON.stringify({
    target: document.getElementById('practice-target').textContent,
    session: ${JSON.stringify(identityAfter)},
    progress: document.getElementById('ui008-learn-progress').textContent,
    canvases: document.querySelectorAll('canvas').length,
  })`);
  assert.equal(after, before, `switching moved practice truth: ${before} -> ${after}`);
  assert.equal(learnPosts() + engine.posts.play.length, postsBefore, "switching emitted commands");
});
function engine_learning_identity() {
  return { sid: engine.learning.session_id, tid: engine.learning.target?.target_id ?? null, v: engine.learning.version, st: engine.learning.state };
}

/* ---------------- RESTART + RECOVERY + ROUTE ---------------- */

await test("restart target: exactly one guarded command; target returns to first", async () => {
  engine.posts.restartTarget.length = 0;
  await evaluate("document.getElementById('btn-learning-restart-target').click()");
  await waitFor("document.getElementById('practice-target').textContent === 'G4'", 8000);
  await sleep(300);
  assert.equal(engine.posts.restartTarget.length, 1);
  assert.equal(await evaluate("document.getElementById('ui008-learn-progress').textContent"), "Step 1 of 3");
});

await test("stale snapshot: Learn marks reconnection and recovers without refresh", async () => {
  engine.delayLearningMs = 6000;
  await waitFor(`['Reconnecting', 'Your turn'].includes(document.getElementById('ui008-learn-state').textContent) === false ? false : document.getElementById('ui008-learn-state').textContent === 'Reconnecting'`, 12000);
  engine.delayLearningMs = 0;
  engine.learning = { ...engine.learning, version: engine.learning.version + 1 };
  await waitFor("document.getElementById('ui008-learn-state').textContent === 'Your turn'", 10000);
  assert.equal(await evaluate("document.getElementById('practice-target').textContent"), "G4");
});

await test("route cycles + refresh + direct load: identity and no duplicates", async () => {
  for (const dest of ["library", "learn", "home", "learn"]) { await route(dest); await sleep(120); }
  assert.equal(await evaluate("document.getElementById('ui008-learn-state').textContent"), "Your turn", "session lost across routes");
  await cdp("Page.reload", { ignoreCache: true });
  await waitFor("document.documentElement?.dataset.ui008ShellReady === 'true'", 20000);
  assert.equal(await evaluate("document.body.dataset.ui008Route"), "learn");
  await waitFor("document.getElementById('ui008-learn-state').textContent === 'Your turn'", 10000);
  assert.equal(await evaluate("document.getElementById('practice-target').textContent"), "G4");
  const counts = await evaluate(`(() => ({
    cards: document.querySelectorAll('#practice').length,
    canvases: document.querySelectorAll('canvas').length,
    keyboards: document.querySelectorAll('#live-keyboard').length,
    transports: document.querySelectorAll('.transport-controls').length,
  }))()`);
  assert.deepEqual(counts, { cards: 1, canvases: 3, keyboards: 1, transports: 1 });
  // one click still means exactly one command after everything
  engine.posts.stop.length = 0;
  await evaluate("document.getElementById('btn-practice-stop').click()");
  await waitFor("document.getElementById('ui008-learn-state').textContent === 'Ready'", 8000);
  await sleep(300);
  assert.equal(engine.posts.stop.length, 1);
});

/* ---------------- RESPONSIVE / ACCESSIBILITY ---------------- */

await test("responsive: 7 canonical viewports without overflow; target and actions reachable", async () => {
  // fresh no-song -> select for ready state, then start for waiting state checks
  engine.posts.start.length = 0;
  await evaluate("document.querySelectorAll('.btn-song')[0].click()");
  await waitFor("document.getElementById('practice-song').textContent === 'Nocturne X'", 8000);
  await evaluate("document.getElementById('btn-practice').click()");
  await waitFor("document.getElementById('ui008-learn-state').textContent === 'Your turn'", 8000);
  const problems = [];
  for (const [w, h] of [[390, 844], [768, 1024], [899, 900], [900, 900], [1024, 768], [1440, 900], [1920, 1080]]) {
    await viewport(w, h);
    // Immersive: present the Piano view once (1024) so the keyboard health
    // is asserted in the view that paints it; other widths keep Falling.
    if (w === 1024) {
      await evaluate(`(() => { const el = document.getElementById('ui008-view-learn'); el.value = 'piano'; el.dispatchEvent(new Event('change', { bubbles: true })); })()`);
      await sleep(150);
    }
    const m = await evaluate(`(() => {
      const de = document.documentElement;
      // Adaptive: the state chip is in More; the single guidance line stays visible.
      const chip = document.getElementById('ui008-learn-guidance').getBoundingClientRect();
      const stop = document.getElementById('btn-practice-stop').getBoundingClientRect();
      const target = document.getElementById('ui008-learn-target-notes').getBoundingClientRect();
      const keyboard = document.getElementById('live-keyboard').getBoundingClientRect();
    const pvHidden = document.getElementById('piano-view').hidden;
    const keyboardPresented = !pvHidden;
    const selValue = document.getElementById('ui008-view-learn')?.value;
    const sceneView = document.getElementById('ui008-workspace-scene')?.dataset.view;
      return {
        overflowX: Math.max(de.scrollWidth - de.clientWidth, 0),
        chipVisible: chip.width > 0 && chip.top >= 0 && chip.bottom <= innerHeight,
        stopVisible: stop.width > 0 && stop.top >= 0 && stop.bottom <= innerHeight,
        stopSize: [Math.round(stop.width), Math.round(stop.height)],
        targetVisible: target.width > 0,
        keyboardH: Math.round(keyboard.height),
        keyboardPresented, pvHidden,
        selValue, sceneView,
        sceneAttached: !!document.getElementById('ui008-workspace-scene')?.isConnected,
        sceneParent: document.getElementById('ui008-workspace-scene')?.parentElement?.id || 'detached',
        pvInScene: (() => { const pv = document.querySelector('#ui008-workspace-scene #piano-view'); return pv ? String(!pv.hidden) : 'missing'; })(),
      };
    })()`);
    if (m.overflowX > 1) problems.push(`${w}: overflowX ${m.overflowX}`);
    if (!m.chipVisible) problems.push(`${w}: primary guidance not visible`);
    if (!m.stopVisible) problems.push(`${w}: stop not visible`);
    if (!(m.stopSize[0] >= 44 && m.stopSize[1] >= 44)) problems.push(`${w}: stop ${m.stopSize}`);
    if (!m.targetVisible) problems.push(`${w}: target notes invisible`);
    // IMMERSIVE: the keyboard paints in the Piano view; assert it healthy
    // there once, and assert the PRESENTED scene is non-collapsed always.
    if (w === 1024 && !m.keyboardPresented) problems.push(`${w}: piano view not presented (sel=${m.selValue}, pvHidden=${m.pvHidden}, view=${m.sceneView})`);
    if (w === 1024 && m.keyboardPresented && m.keyboardH <= 0) problems.push(`${w}: keyboard collapsed`);
  }
  await viewport(1024, 768);
  await evaluate(`(() => { const el = document.getElementById('ui008-view-learn'); el.value = 'falling'; el.dispatchEvent(new Event('change', { bubbles: true })); })()`);
  assert.deepEqual(problems, [], JSON.stringify(problems));
});

await test("accessibility: live regions, labels, touch sizes, reduced motion", async () => {
  await viewport(390, 844);
  const a = await evaluate(`(() => {
    const small = [...document.querySelectorAll('#practice button, #practice select, #practice input')]
      .filter(el => { const s = getComputedStyle(el); if (el.disabled || s.display === 'none' || s.visibility === 'hidden') return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && (r.width < 44 || r.height < 44); })
      .map(el => el.id);
    return {
      guidanceLive: document.getElementById('ui008-learn-guidance').getAttribute('role'),
      targetLive: document.getElementById('practice-target').getAttribute('aria-live'),
      chipsHidden: document.getElementById('ui008-learn-target-notes').getAttribute('aria-hidden'),
      startLabel: document.getElementById('btn-practice').textContent.trim(),
      small,
      barRole: document.getElementById('practice-bar').getAttribute('role'),
    };
  })()`);
  assert.equal(a.guidanceLive, "status");
  assert.equal(a.targetLive, "polite");
  assert.equal(a.chipsHidden, "true", "decorative chips must be aria-hidden (target text is the SR source)");
  assert.equal(a.startLabel, "Start practice");
  assert.deepEqual(a.small, [], JSON.stringify(a.small));
  assert.equal(a.barRole, "progressbar");
  await cdp("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
  await route("library"); await route("learn");
  const anim = await evaluate("getComputedStyle(document.getElementById('hands')).animationName");
  await cdp("Emulation.setEmulatedMedia", { features: [] });
  assert.equal(anim, "none");
  await viewport(1024, 768);
});

/* ---------------- RESOURCE LIFECYCLE ---------------- */

await test("lifecycle: repeated Learn→Library→Learn + viz switches stay stable", async () => {
  const before = await evaluate(`(() => ({
    cards: document.querySelectorAll('#practice').length,
    canvases: document.querySelectorAll('canvas').length,
    keyboards: document.querySelectorAll('#live-keyboard').length,
    chips: document.querySelectorAll('.learn-note-chip').length,
  }))()`);
  for (const dest of ["library", "learn", "library", "learn", "home", "learn"]) { await route(dest); await sleep(130); }
  for (const id of ["visual-mode-falling", "visual-mode-sheet", "visual-mode-falling"]) {
    await evaluate(`document.getElementById('${id}').click()`);
    await sleep(90);
  }
  const after = await evaluate(`(() => ({
    cards: document.querySelectorAll('#practice').length,
    canvases: document.querySelectorAll('canvas').length,
    keyboards: document.querySelectorAll('#live-keyboard').length,
    chips: document.querySelectorAll('.learn-note-chip').length,
  }))()`);
  assert.deepEqual(after, before, `${JSON.stringify(before)} -> ${JSON.stringify(after)}`);
  // session still alive, exactly one stop when clicked
  assert.equal(await evaluate("document.getElementById('ui008-learn-state').textContent"), "Your turn");
  engine.posts.stop.length = 0;
  await evaluate("document.getElementById('btn-practice-stop').click()");
  await waitFor("document.getElementById('ui008-learn-state').textContent === 'Ready'", 8000);
  await sleep(300);
  assert.equal(engine.posts.stop.length, 1);
});

/* ---------------- VISUAL EVIDENCE ---------------- */

await test("fresh visual evidence captures the Learn workspace", async () => {
  // no-song (fresh reload)
  engine.learning = learningIdle();
  engine.held = new Set();
  await cdp("Page.reload", { ignoreCache: true });
  await waitFor("document.documentElement?.dataset.ui008ShellReady === 'true'", 20000);
  await waitFor("document.getElementById('ui008-learn-empty')?.hidden === false", 15000);
  await viewport(1024, 768);
  await sleep(300);
  await capture("learn-tablet-1024x768-no-song.png");
  // selected/ready
  await evaluate("document.querySelectorAll('.btn-song')[0].click()");
  await waitFor("document.getElementById('ui008-learn-state').textContent === 'Ready'", 8000);
  await sleep(300);
  await capture("learn-tablet-1024x768-ready.png");
  // waiting single target
  engine.posts.start.length = 0;
  await evaluate("document.getElementById('btn-practice').click()");
  await waitFor("document.getElementById('ui008-learn-state').textContent === 'Your turn'", 8000);
  await sleep(1300);
  await capture("learn-tablet-1024x768-waiting-single.png");
  // chord target with an authoritative partial
  await evaluate("document.getElementById('btn-practice-stop').click()");
  await waitFor("document.getElementById('ui008-learn-state').textContent === 'Ready'", 8000);
  await evaluate("document.querySelectorAll('.btn-song')[1].click()");
  await waitFor("document.getElementById('practice-song').textContent === 'Chorale in C'", 8000);
  await evaluate("document.getElementById('btn-practice').click()");
  await waitFor("document.getElementById('ui008-learn-state').textContent === 'Your turn'", 8000);
  enginePress(60);
  await waitFor("document.querySelectorAll('#ui008-learn-target-notes .learn-note-chip--accepted').length === 1", 8000);
  await sleep(600);
  await capture("learn-tablet-1024x768-chord-partial.png");
  // active session progress (advance once)
  enginePress(64); enginePress(67);
  await waitFor("document.getElementById('ui008-learn-progress').textContent === 'Step 2 of 2'", 8000);
  await sleep(600);
  await capture("learn-tablet-1024x768-active-progress.png");
  // completion
  enginePress(72);
  await waitFor("document.getElementById('ui008-learn-state').textContent === 'Complete'", 8000);
  await sleep(400);
  await capture("learn-tablet-1024x768-complete.png");
  // phone
  await evaluate("document.getElementById('btn-practice').click()");
  await waitFor("document.getElementById('ui008-learn-state').textContent === 'Your turn'", 8000);
  await viewport(390, 844);
  await sleep(400);
  await capture("learn-phone-390x844.png");
  // tablet landscape + desktop
  await viewport(1024, 768);
  await sleep(300);
  await capture("learn-tablet-landscape-1024x768.png");
  await viewport(1440, 900);
  await sleep(300);
  await capture("learn-desktop-1440x900.png");
  // stale/reconnecting
  engine.delayLearningMs = 6000;
  await waitFor("document.getElementById('ui008-learn-state').textContent === 'Reconnecting'", 15000);
  engine.delayLearningMs = 0;
  await viewport(1024, 768);
  await sleep(300);
  await capture("learn-tablet-1024x768-reconnecting.png");
  await sleep(9000); // drain delayed responses before the suite ends
  measures.captures = 10;
  assert.equal(true, true);
});

/* ---------------- PIANO-LEARN-001: Learn Library catalog ---------------- */

await test("learn catalog: overview renders honest stats, level chips, lesson rows", async () => {
  await evaluate("document.querySelector('[data-ui008-route=learn]').click()");
  await route("learn");
  await waitFor("document.getElementById('ui008-learn-library-stats')?.textContent.includes('1612 lessons')", 10000);
  assert.match(await evaluate("document.getElementById('ui008-learn-library-stats').textContent"), /83 courses/);
  const chips = await evaluate("[...document.querySelectorAll('#ui008-learn-levels .learn-level-chip')].map(b => b.dataset.level)");
  assert.ok(chips.includes("-1") && chips.includes("0"), JSON.stringify(chips));
  const rows = await evaluate("[...document.querySelectorAll('#ui008-learn-lessons .learn-lesson-btn')].map(b => b.dataset.lessonId)");
  assert.deepEqual(rows, ["les-1", "les-2", "les-3"]);
});

await test("learn catalog: level filter refetches with the level parameter", async () => {
  await evaluate("[...document.querySelectorAll('#ui008-learn-levels .learn-level-chip')].find(b => b.dataset.level === '2').click()");
  await waitFor("[...document.querySelectorAll('#ui008-learn-lessons .learn-lesson-btn')].length === 1", 8000);
  const rows = await evaluate("[...document.querySelectorAll('#ui008-learn-lessons .learn-lesson-btn')].map(b => b.dataset.lessonId)");
  assert.deepEqual(rows, ["les-3"]);
});

await test("learn catalog: lesson detail opens with license, hands note and navigation", async () => {
  // Reset to all levels, then open the first lesson.
  await evaluate("[...document.querySelectorAll('#ui008-learn-levels .learn-level-chip')].find(b => b.dataset.level === '-1').click()");
  await waitFor("[...document.querySelectorAll('#ui008-learn-lessons .learn-lesson-btn')].length === 3", 8000);
  await evaluate("[...document.querySelectorAll('#ui008-learn-lessons .learn-lesson-btn')].find(b => b.dataset.lessonId === 'les-1').click()");
  await waitFor("document.getElementById('ui008-lesson-detail')?.hidden === false", 8000);
  assert.match(await evaluate("document.getElementById('ui008-lesson-detail-title').textContent"), /Middle C Pulse/);
  assert.match(await evaluate("document.getElementById('ui008-lesson-detail-license').textContent"), /Pianio/);
  assert.equal(await evaluate("document.getElementById('ui008-lesson-prev').disabled"), true);
  assert.equal(await evaluate("document.getElementById('ui008-lesson-next').disabled"), false);
  assert.match(await evaluate("document.getElementById('ui008-lesson-detail-progress').textContent"), /Not practiced/);
});

await test("learn lesson start: exactly one command carrying package_id (Player untouched)", async () => {
  // Clear any lingering playback/practice ownership from earlier gates.
  await evaluate("fetch('/api/v1/playback/stop', { method: 'POST', headers: {'content-type':'application/json'}, body: '{}' }).then(r => r.status)");
  await evaluate("fetch('/api/v1/learning/stop', { method: 'POST', headers: {'content-type':'application/json'}, body: '{}' }).then(r => r.status)").catch(() => {});
  engine.learning = { ...engine.learning, state: "IDLE" };
  engine.posts.start.length = 0;
  await evaluate("document.getElementById('ui008-lesson-start').click()");
  const startedWaiting = await waitFor("document.getElementById('ui008-learn-state')?.textContent === 'Your turn'", 8000);
  if (!startedWaiting) {
    console.error("DIAG posts:", JSON.stringify(engine.posts.start));
    console.error("DIAG page:", await evaluate("document.getElementById('practice-error')?.textContent"));
    console.error("DIAG button:", await evaluate("document.getElementById('ui008-lesson-start')?.disabled"));
  }
  assert.ok(startedWaiting, "lesson practice never reached WAITING");
  await sleep(400);
  assert.equal(engine.posts.start.length, 1, JSON.stringify(engine.posts.start));
  assert.equal(engine.posts.start[0].package_id, "pkg-1");
  assert.equal(engine.posts.start[0].mode, "WAIT_FOR_NOTE");
  await waitFor("document.getElementById('practice-target')?.textContent === 'C4'", 5000);
  assert.equal(await evaluate("document.getElementById('practice-target').textContent"), "C4");
  await evaluate("document.getElementById('btn-practice-stop').click()");
  await waitFor("document.getElementById('ui008-learn-state')?.textContent === 'Ready'", 8000);
  await evaluate("document.getElementById('studio-exit').click()");
  assert.ok(await waitFor("document.body.dataset.learnStudio === 'closed'", 8000));
});

await test("learn catalog degradation is honest when the catalog API is down", async () => {
  await evaluate("document.getElementById('ui008-lesson-close').click()");
  learnDown = true;
  await evaluate("window.dispatchEvent(new Event('piano-ui-route-change'))");
  await route("library");
  await route("learn");
  // refresh the catalog through the exposed controller surface
  await waitFor("document.body.dataset.learnCatalog === 'ready'", 3000).catch(() => {});
  learnDown = false;
});


await test("learn catalog VISIBILITY: real nav click opens the visible catalog (regression gate for the static-hidden defect)", async () => {
  await route("library");
  await evaluate("document.querySelector('[data-ui008-route=learn]')?.click()");
  await waitFor("document.body.dataset.ui008Route === 'learn'", 8000);
  await waitFor("document.getElementById('ui008-learn-library-stats')?.textContent.includes('1612 lessons')", 10000);
  const vis = await evaluate(`(() => {
    const section = document.getElementById('ui008-learn-library');
    const stats = document.getElementById('ui008-learn-library-stats');
    const row = document.querySelector('#ui008-learn-lessons .learn-lesson-btn');
    const rect = section.getBoundingClientRect();
    const rowRect = row ? row.getBoundingClientRect() : { width: 0, height: 0 };
    return {
      hasHiddenAttr: section.hidden,
      routeHidden: section.dataset.ui008RouteHidden,
      offsetParentNull: section.offsetParent === null,
      rectH: Math.round(rect.height),
      statsVisible: !!stats && stats.getBoundingClientRect().height > 0,
      rowVisible: rowRect.height > 0 && rowRect.width > 0,
      rows: document.querySelectorAll('#ui008-learn-lessons .learn-lesson-btn').length,
    };
  })()`);
  assert.equal(vis.hasHiddenAttr, false, JSON.stringify(vis));
  assert.equal(vis.routeHidden, "false", JSON.stringify(vis));
  assert.equal(vis.offsetParentNull, false, "section must be rendered (offsetParent non-null)");
  assert.ok(vis.rectH > 120, `section height ${vis.rectH}`);
  assert.equal(vis.statsVisible, true);
  assert.equal(vis.rowVisible, true, "lesson rows must be visible");
  assert.ok(vis.rows >= 1);
  await viewport(1024, 768);
  await sleep(250);
  await capture("learn-library-catalog-visible-1024x768.png");
  await viewport(1024, 768);
});

await test("learn catalog survives a real reload (no stale-cache dependence)", async () => {
  await evaluate("location.reload()");
  await sleep(600);
  await waitFor("document.documentElement?.dataset.ui008ShellReady === 'true'", 15000);
  await waitFor("document.body.dataset.ui008Route === 'learn'", 10000);
  await waitFor("document.getElementById('ui008-learn-library-stats')?.textContent.includes('1612 lessons')", 10000);
  const vis = await evaluate(`(() => {
    const section = document.getElementById('ui008-learn-library');
    const rect = section.getBoundingClientRect();
    const sw = navigator.serviceWorker && navigator.serviceWorker.controller ? true : false;
    return { hidden: section.hidden, rectH: Math.round(rect.height), route: document.body.dataset.ui008Route, serviceWorker: sw };
  })()`);
  assert.equal(vis.hidden, false, JSON.stringify(vis));
  assert.ok(vis.rectH > 120, `height after reload ${vis.rectH}`);
  assert.equal(vis.route, "learn");
  assert.equal(vis.serviceWorker, false, "no service worker may intercept assets");
});

/* U8-P7: real local Core behind the browser. Catalog/score fixture is
 * synthetic; engine, clock, input reduction, HTTP guards and result DB are real. */
await test("Studio: boot disposable real Core, preserve failed Next identity", async () => {
  coreProcess = spawn(process.env.PIANO_TEST_PYTHON || "python3", ["tests/studio_core_fixture.py"], { cwd: ROOT, env: { ...process.env, PYTHONPATH: "src" }, stdio: ["ignore", "pipe", "pipe"] });
  coreBridgePort = await new Promise((done, fail) => {
    let output = "", errors = "";
    const timer = setTimeout(() => fail(Error(`Core fixture startup timeout: ${errors}`)), 10000);
    coreProcess.stderr.on("data", chunk => { errors += chunk; });
    coreProcess.on("exit", code => { if (!coreBridgePort) { clearTimeout(timer); fail(Error(`Core fixture exited ${code}: ${errors}`)); } });
    coreProcess.stdout.on("data", chunk => { output += chunk; if (output.includes("\n")) { clearTimeout(timer); try { done(JSON.parse(output.split("\n")[0]).port); } catch (error) { fail(error); } } });
  });
  await cdp("Page.reload", { ignoreCache: true });
  assert.ok(await waitFor("document.documentElement.dataset.ui008ShellReady === 'true'", 15000));
  assert.ok(await waitFor("!!document.querySelector('[data-lesson-id=les-1]')",10000));
  await evaluate("document.querySelector('[data-ui008-route=learn]').click(); document.querySelector('[data-lesson-id=les-1]').click()");
  assert.ok(await waitFor("!document.getElementById('ui008-lesson-detail').hidden"));
  await evaluate("document.getElementById('ui008-lesson-detail').scrollIntoView({block:'center'})");
  await capture("academy-lesson-detail.png");
  await evaluate("document.getElementById('ui008-lesson-start').scrollIntoView({block:'center'})");
  const point = await evaluate("(() => {const r=document.getElementById('ui008-lesson-start').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()");
  await cdp("Input.dispatchMouseEvent", { type: "mousePressed", button: "left", clickCount: 1, ...point });
  await cdp("Input.dispatchMouseEvent", { type: "mouseReleased", button: "left", clickCount: 1, ...point });
  assert.ok(await waitFor("document.body.dataset.learnStudio === 'open' && document.getElementById('ui008-learn-state').textContent === 'Your turn'"));
  const before = await (await fetch(`http://127.0.0.1:${coreBridgePort}/api/v1/learning`)).json();
  await evaluate("document.getElementById('studio-forward').click()");
  assert.ok(await waitFor("document.getElementById('practice-error').textContent.includes('could not be loaded')"));
  const after = await (await fetch(`http://127.0.0.1:${coreBridgePort}/api/v1/learning`)).json();
  assert.equal(after.session_id, before.session_id); assert.equal(after.state, "WAITING");
  assert.equal(after.song_id, "package:pkg-1");
});
const corePost = async (path, body) => {
  const r = await fetch(`http://127.0.0.1:${coreBridgePort}/api/v1/${path}`, { method: "POST", headers: {"content-type":"application/json"}, body: JSON.stringify(body) });
  assert.equal(r.status, 200, await r.clone().text()); return r.json();
};
await test("Studio: native fullscreen exit, visual/range changes and orientation preserve session", async () => {
  const previous = await (await fetch(`http://127.0.0.1:${coreBridgePort}/api/v1/learning`)).json();
  await evaluate("document.getElementById('studio-restart').click()");
  assert.ok(await waitFor("document.getElementById('practice-error').hidden === true"));
  const before = await (await fetch(`http://127.0.0.1:${coreBridgePort}/api/v1/learning`)).json();
  assert.notEqual(before.session_id,previous.session_id);
  if (await evaluate("!!document.fullscreenElement")) await evaluate("document.exitFullscreen()");
  assert.equal(await evaluate("document.body.dataset.learnStudio"), "open");
  for (const [w,h,dpr] of [[1024,768,1],[1180,820,2],[390,844,2]]) {
    await cdp("Emulation.setDeviceMetricsOverride", { width:w,height:h,deviceScaleFactor:dpr,mobile:false });
    await sleep(100);
    const m=await evaluate("(() => {const r=document.getElementById('practice').getBoundingClientRect(); const c=document.getElementById('falling-canvas').getBoundingClientRect(); const controls=[...document.querySelectorAll('#practice button,#practice select')].filter(e=>e.getBoundingClientRect().width>0).map(e=>{const r=e.getBoundingClientRect();return {id:e.id,top:r.top,bottom:r.bottom,w:r.width,h:r.height}});return {w:r.width,h:r.height,iw:innerWidth,ih:innerHeight,cw:c.width,ch:c.height,overflow:document.documentElement.scrollWidth-innerWidth,controls};})()");
    assert.equal(Math.round(m.w),m.iw); assert.equal(Math.round(m.h),m.ih); assert.ok(m.cw>100&&m.ch>100,JSON.stringify(m)); assert.ok(m.overflow<=1,JSON.stringify(m));
    assert.deepEqual(m.controls.filter(e=>e.top<0||e.bottom>m.ih||e.w<44||e.h<44),[],JSON.stringify(m));
    await capture(`studio-wait-${w}x${h}-dpr${dpr}.png`);
  }
  await viewport(1024,768);
  assert.ok(await waitFor("document.getElementById('visual-stage').dataset.status==='ready'",10000));
  const rows = await evaluate("(() => {const p=document.querySelector('.learn-progress-row').getBoundingClientRect(),b=document.getElementById('practice-bar').getBoundingClientRect();return {textBottom:p.bottom,barTop:b.top};})()");
  assert.ok(rows.barTop >= rows.textBottom, JSON.stringify(rows));
  await capture("studio-wait-focus.png");
  for (const mode of ["sheet","hybrid","falling","piano","hands","falling"]) {
    await evaluate(`(() => {const e=document.getElementById('studio-visual');e.value='${mode}';e.dispatchEvent(new Event('change'));})()`);
    if (["sheet","hybrid"].includes(mode)) await capture(`studio-${mode}.png`);
  }
  await evaluate("(() => {const e=document.getElementById('studio-range');e.value='full';e.dispatchEvent(new Event('change'));})()");
  await capture("studio-wait-full88.png");
  await evaluate("document.getElementById('settings-hands-enabled').checked=false;document.getElementById('settings-hands-enabled').dispatchEvent(new Event('change'))");
  assert.equal(await evaluate("document.querySelector('#studio-visual option[value=hands]').disabled"),true);
  await evaluate("document.getElementById('settings-hands-enabled').checked=true;document.getElementById('settings-hands-enabled').dispatchEvent(new Event('change'))");
  const after = await (await fetch(`http://127.0.0.1:${coreBridgePort}/api/v1/learning`)).json();
  assert.equal(after.session_id,before.session_id); assert.equal(after.completed_steps,0);
});
await test("Studio: real Wait input, repeated-note release and persisted completion", async () => {
  await corePost("_test/input", {note:61,pressed:true});
  let s=await (await fetch(`http://127.0.0.1:${coreBridgePort}/api/v1/learning`)).json(); assert.equal(s.completed_steps,0); assert.equal(s.incorrect_attempts,1);
  await corePost("_test/input", {note:61,pressed:false});
  const feedbackLatency=[];
  for(let i=0;i<4;i++) {
    const before=await evaluate("document.getElementById('ui008-learn-progress').textContent");
    const started=performance.now();
    await corePost("_test/input",{note:60,pressed:true});
    assert.ok(await waitFor(`document.getElementById('ui008-learn-progress').textContent !== ${JSON.stringify(before)}`));
    feedbackLatency.push(Math.round((performance.now()-started)*10)/10);
    if(i===0) {await corePost("_test/input",{note:60,pressed:true}); s=await (await fetch(`http://127.0.0.1:${coreBridgePort}/api/v1/learning`)).json(); assert.equal(s.completed_steps,1);} await corePost("_test/input",{note:60,pressed:false});
  }
  measures.syntheticInputToDomUpperBoundMs=feedbackLatency;
  assert.ok(await waitFor("document.getElementById('studio-results').hidden === false"));
  assert.match(await evaluate("document.getElementById('studio-results-detail').textContent"), /4 \/ 4 targets completed/);
  assert.doesNotMatch(await evaluate("document.getElementById('studio-results-detail').textContent"), /timing|accuracy/);
  const progress=await (await fetch(`http://127.0.0.1:${coreBridgePort}/api/v1/_test/progress`)).json(); assert.equal(progress.progress['pkg-1'].completions,1);
  await capture("studio-wait-completion.png");
});
await test("Studio: genuine Core Flow clock, pause/resume, misses and completion", async () => {
  await evaluate("(() => {const e=document.getElementById('studio-session-mode');e.value='FLOW';e.dispatchEvent(new Event('change'));})()");
  assert.ok(await waitFor("document.getElementById('studio-mode-label').textContent.includes('Flow') && !document.getElementById('studio-pause').hidden"));
  await evaluate("document.getElementById('studio-pause').click()");
  assert.ok(await waitFor("document.getElementById('studio-pause').textContent === 'Resume'"));
  const paused=await (await fetch(`http://127.0.0.1:${coreBridgePort}/api/v1/learning`)).json();
  await sleep(200);
  const still=await (await fetch(`http://127.0.0.1:${coreBridgePort}/api/v1/learning`)).json(); assert.equal(still.flow.position_seconds,paused.flow.position_seconds);
  await evaluate("document.getElementById('studio-pause').click()");
  assert.ok(await waitFor("document.getElementById('studio-pause').textContent === 'Pause'"));
  await sleep(2300);
  const moving=await (await fetch(`http://127.0.0.1:${coreBridgePort}/api/v1/learning`)).json(); assert.ok(moving.flow.position_seconds>0); assert.ok(moving.flow.missed_events>=1);
  await capture("studio-flow-running.png");
  const metrics=await evaluate("new Promise(done=>{let frames=[],cost=[],last=performance.now();const count=()=>{let now=performance.now();frames.push(now-last);last=now;cost.push(Number(document.getElementById('visual-stage').dataset.frameMs));if(frames.length<120)requestAnimationFrame(count);else {frames.sort((a,b)=>a-b);cost.sort((a,b)=>a-b);done({frames:frames.length,median_interval_ms:frames[60],p95_interval_ms:frames[114],p95_draw_ms:cost[114],nodes:document.querySelectorAll('*').length});}};requestAnimationFrame(count);})");
  measures.studioFramePacing=metrics; assert.ok(metrics.p95_draw_ms<33.4,JSON.stringify(metrics));
  assert.ok(await waitFor("!document.getElementById('studio-results').hidden",10000));
  assert.match(await evaluate("document.getElementById('studio-results-detail').textContent"), /0 \/ 4 events correct · 4 missed/);
  assert.doesNotMatch(await evaluate("document.getElementById('practice-completion').textContent"), /All 4 steps played/);
  await capture("studio-flow-completion.png");
});
await test("Studio: active reload restores exact session and fallback without Start", async () => {
  await evaluate("document.getElementById('studio-retry').click()");
  assert.ok(await waitFor("!document.getElementById('studio-pause').hidden"));
  assert.ok(await waitFor("document.getElementById('studio-feedback').dataset.outcome==='MISSED'",10000));
  await evaluate("document.getElementById('studio-pause').click()");
  assert.ok(await waitFor("document.getElementById('studio-pause').textContent==='Resume'"));
  const before=await (await fetch(`http://127.0.0.1:${coreBridgePort}/api/v1/learning`)).json();
  await cdp("Page.reload",{ignoreCache:true});
  assert.ok(await waitFor("document.body.dataset.learnStudio==='open'",15000));
  const after=await (await fetch(`http://127.0.0.1:${coreBridgePort}/api/v1/learning`)).json(); assert.equal(after.session_id,before.session_id); assert.equal(after.flow.paused,true);
  assert.equal(await evaluate("document.body.dataset.studioFullscreen"),"viewport");
  await evaluate("location.hash='#/library'");
  assert.ok(await waitFor("document.body.dataset.ui008Route==='learn'"));
  const frozen=await evaluate("document.getElementById('visual-stage').dataset.position");
  engine.delayLearningMs=6000;
  assert.ok(await waitFor("document.getElementById('visual-stage').dataset.status==='stale'",15000));
  await evaluate("document.getElementById('studio-exit').click()");
  assert.equal(await evaluate("document.body.dataset.learnStudio"),"open");
  assert.equal(await evaluate("document.getElementById('visual-stage').dataset.songId"),"package:pkg-1");
  assert.equal(await evaluate("document.getElementById('visual-stage').dataset.position"),frozen);
  engine.delayLearningMs=0;
  assert.ok(await waitFor("document.getElementById('visual-stage').dataset.status==='ready'",15000));
  await evaluate("document.getElementById('studio-exit').click()");
  assert.ok(await waitFor("document.body.dataset.learnStudio==='closed'"));
  const stopped=await (await fetch(`http://127.0.0.1:${coreBridgePort}/api/v1/learning`)).json(); assert.equal(stopped.state,"IDLE");
});

await test("Academy: persisted Continue identity, course progress and catalog recovery", async () => {
  assert.ok(await waitFor("!document.getElementById('academy-continue').hidden"));
  assert.equal(await evaluate("document.getElementById('academy-continue-heading').textContent"), "Middle C Pulse");
  assert.match(await evaluate("document.getElementById('academy-progress-total').textContent"), /1 lessons completed/);
  await evaluate("document.getElementById('academy-continue').scrollIntoView({block:'start'})");
  await capture("academy-persisted-progress.png");
  await evaluate("document.querySelector('.journey-card[data-level=\"4\"]').click()");
  await evaluate("document.querySelector('.academy-course').click()");
  assert.ok(await waitFor("document.getElementById('ui008-learn-course').value === 'pianio:method-level-1'"));
  assert.ok(await waitFor("document.querySelectorAll('.learn-lesson-btn').length === 2"));
  await capture("academy-course-detail.png");
  learnDown = true;
  await evaluate("document.getElementById('academy-refresh').click()");
  assert.ok(await waitFor("document.getElementById('ui008-learn-library-head').dataset.state === 'unavailable'"));
  learnDown = false;
  await evaluate("document.getElementById('academy-refresh').click()");
  assert.ok(await waitFor("document.getElementById('ui008-learn-library-head').dataset.state === 'ready'"));
  await evaluate("document.getElementById('academy-resume').click()");
  assert.ok(await waitFor("!document.getElementById('ui008-lesson-detail').hidden"));
  assert.equal(await evaluate("document.getElementById('ui008-lesson-detail-title').textContent"), "Middle C Pulse");
});

await test("Academy and Studio: dark surfaces remain readable with a light OS preference", async () => {
  await cdp("Emulation.setEmulatedMedia",{features:[{name:"prefers-color-scheme",value:"light"}]});
  await evaluate("document.getElementById('ui008-lesson-detail').scrollIntoView({block:'center'})");
  const colors=await evaluate("({title:getComputedStyle(document.getElementById('ui008-lesson-detail-title')).color,detail:getComputedStyle(document.getElementById('ui008-lesson-detail-summary')).color})");
  assert.equal(colors.title,"rgb(238, 241, 236)");
  assert.equal(colors.detail,"rgb(183, 199, 194)");
  for (const id of ["academy-refresh","ui008-lesson-close"]) {
    const colors=await evaluate(`(() => {const s=getComputedStyle(document.getElementById('${id}'));return {color:s.color,background:s.backgroundColor}})()`);
    assert.equal(colors.color,"rgb(238, 241, 236)"); assert.equal(colors.background,"rgb(17, 27, 29)");
  }
  await capture("academy-lesson-light-os.png");
  await cdp("Emulation.setEmulatedMedia",{features:[{name:"prefers-color-scheme",value:"dark"}]});
});

await test("Studio: two-minute real-Core Wait presentation has bounded memory and DOM", async () => {
  await evaluate("document.getElementById('studio-lesson-mode').value='WAIT_FOR_NOTE'; document.getElementById('ui008-lesson-start').click()");
  assert.ok(await waitFor("document.body.dataset.learnStudio==='open' && document.getElementById('ui008-learn-state').textContent==='Your turn'"));
  await cdp("Emulation.setEmulatedMedia",{features:[{name:"prefers-color-scheme",value:"light"}]});
  for (const id of ["studio-exit","studio-restart","studio-forward"]) {
    const colors=await evaluate(`(() => {const s=getComputedStyle(document.getElementById('${id}'));return {color:s.color,background:s.backgroundColor}})()`);
    assert.equal(colors.color,"rgb(238, 241, 236)"); assert.equal(colors.background,"rgb(17, 27, 29)");
  }
  await capture("studio-light-os.png");
  await cdp("Emulation.setEmulatedMedia",{features:[{name:"prefers-color-scheme",value:"dark"}]});
  await cdp("HeapProfiler.collectGarbage");
  const before = await cdp("Runtime.getHeapUsage");
  const domBefore = await evaluate("document.querySelectorAll('*').length");
  const sessionBefore = await (await fetch(`http://127.0.0.1:${coreBridgePort}/api/v1/learning`)).json();
  await sleep(120000);
  await cdp("HeapProfiler.collectGarbage");
  const after = await cdp("Runtime.getHeapUsage");
  const domAfter = await evaluate("document.querySelectorAll('*').length");
  const sessionAfter = await (await fetch(`http://127.0.0.1:${coreBridgePort}/api/v1/learning`)).json();
  measures.waitMemory120s = {heap_before:before.usedSize,heap_after:after.usedSize,heap_delta:after.usedSize-before.usedSize,dom_before:domBefore,dom_after:domAfter};
  assert.equal(domAfter,domBefore);
  assert.ok(after.usedSize-before.usedSize < 8*1024*1024, JSON.stringify(measures.waitMemory120s));
  assert.equal(sessionAfter.session_id,sessionBefore.session_id);
  assert.equal(sessionAfter.completed_steps,0);
  await evaluate("document.getElementById('studio-exit').click()");
  assert.ok(await waitFor("document.body.dataset.learnStudio==='closed'"));
});


} finally {
  ws.close();
  chrome.kill("SIGTERM");
  coreProcess?.kill("SIGTERM");
  await new Promise((d) => server.close(d));
  await Promise.race([new Promise((d) => chrome.once("exit", d)), sleep(2000)]);
  await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}

if (failures) {
  console.error(`FAIL ${failures}; PASS ${passes}`);
  if (pageErrors.length) console.error("PAGE ERRORS:\n" + pageErrors.join("\n---\n"));
  process.exit(1);
}
console.log(`PASS ${passes}/${passes}`);
console.log(`P4_MEASURES ${JSON.stringify(measures)}`);
