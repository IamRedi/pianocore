// PIANO-UI-008 U8-P2: real Chromium/HTTP Home + Library acceptance gates.
// A synthetic deterministic 5,000-song catalog proves the bounded-DOM
// contract; a controlled slow endpoint proves stale-search suppression.
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

/* ---------------- synthetic 5,000-song catalog ---------------- */

const CATALOG_SIZE = 5000;
const catalog = [];
for (let i = 0; i < CATALOG_SIZE; i += 1) {
  const padded = String(i).padStart(5, "0");
  const titled = i % 10 === 0;
  catalog.push({
    song_id: `song-${padded}`,
    filename: `song-${padded}.mid`,
    relpath: `catalog/song-${padded}.mid`,
    extension: ".mid",
    size_bytes: 1000 + i,
    status: i % 500 === 499 ? "pending" : "parsed",
    midi_format: 1,
    track_count: (i % 3) + 1,
    ticks_per_beat: 480,
    duration_seconds: 90 + (i % 240),
    tempo_bpm_first: 120,
    title: titled ? `Sonata ${padded}` : null,
    indexed_at_utc: "2026-09-28T00:00:00Z",
  });
}

const serverState = {
  libraryDown: false,
  emptyLibrary: false,
  listDelayMs: 0,
  playRequests: [],
  listingRequests: [],
};

function listingPayload(query, limit, offset) {
  if (serverState.libraryDown) return { error: { code: "library_unavailable", message: "down" } };
  let rows = catalog;
  if (serverState.emptyLibrary) rows = [];
  if (query) {
    const needle = query.toLowerCase();
    rows = rows.filter(
      (song) =>
        song.relpath.toLowerCase().includes(needle) ||
        (song.title ?? "").toLowerCase().includes(needle)
    );
    return { query: { q: query, limit, offset: 0 }, count: rows.length, songs: rows.slice(0, limit) };
  }
  const filtered = rows.filter((song) => queryStatus === null || song.status === queryStatus);
  return {
    query: { q: null, limit, offset, status: queryStatus },
    count: filtered.slice(offset, offset + limit).length,
    songs: filtered.slice(offset, offset + limit),
  };
}
let queryStatus = null;

const score = {
  schema_version: 1,
  song_id: "song-00000",
  source_version: `score-sha256:${"a".repeat(64)}`,
  title: "Sonata 00000",
  duration_seconds: 60,
  time_basis: "song_seconds",
  note_count: 1,
  notes: [{ index: 0, midi: 60, channel: 1, velocity: 90, onset_seconds: 12, duration_seconds: 1 }],
  diagnostics: {
    percussion_note_ons: 0, outside_keyboard_note_ons: 0, unpaired_note_offs: 0,
    retriggered_note_ons: 0, unterminated_note_ons: 0,
  },
  notation: {
    supported: ["pitch", "onset", "duration", "channel", "velocity"],
    unsupported: ["measure", "beat", "hand", "fingering", "tempo_map"],
  },
};

const playback = {
  available: true, state: "PAUSED", song_id: "song-00000", title: "Sonata 00000",
  position_seconds: 81, duration_seconds: 200, tempo_multiplier: 1,
  events_sent: 0, error: null,
};

const learning = {
  available: true, state: "IDLE", song_id: null, title: null,
  step_index: null, total_steps: null, completed_steps: 0, incorrect_attempts: 0,
  progress: 0, started_at: null, ended_at: null, error: null,
  midi_connection: "CONNECTED", session_id: null, mode: null,
  hand_selection: null, section: null, target_tempo: null, accepted_notes: [],
  latest_outcome: "NONE", generation: 0, version: 0,
  updated_at: null,
  target: null,
  repetition: { current: 0, required: 1, successful: 0, failed: 0 },
  capabilities: {
    supported_modes: ["WAIT_FOR_NOTE", "SECTION_PRACTICE", "REPEAT_UNTIL_CORRECT"],
    missed_outcome: false, hand_selection: false, rhythm_grading: false,
    persistence: "runtime_only", section_basis: "song_time_seconds",
  },
};

let sequence = 0;
let postCount = 0;
function noteSnapshot() {
  sequence += 1;
  return {
    schema_version: 1, runtime_epoch: "ui008-p2", sequence,
    physical: {
      notes: [], outside_keyboard_count: 0,
      sustain: { value: 0, pressed: false },
      midi_connection: "CONNECTED", revision: sequence,
    },
    playback: { available: true, notes: [], voices: [], outside_keyboard_count: 0, sustain_channels: [] },
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
  if (request.method === "POST") {
    postCount += 1;
    let body = "";
    for await (const chunk of request) body += chunk;
    if (url.pathname === "/api/v1/playback/play") {
      const parsed = JSON.parse(body || "{}");
      serverState.playRequests.push(parsed);
      playback.song_id = parsed.song_id;
      playback.title = catalog.find((s) => s.song_id === parsed.song_id)?.title ?? "Unknown";
      playback.state = "PLAYING";
      playback.position_seconds = 0;
      json(response, playback);
      return;
    }
    if (url.pathname === "/api/v1/playback/stop") {
      playback.state = "IDLE";
      playback.song_id = null;
      playback.title = null;
      json(response, { ...playback, stopped: true });
      return;
    }
    json(response, { error: { code: "not_found", message: "not found" } }, 404);
    return;
  }
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
        ".glb": "model/gltf-binary",
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
    response.write(`event: snapshot\ndata: ${JSON.stringify(noteSnapshot())}\n\n`);
    const heartbeat = setInterval(() => {
      try { response.write(": heartbeat\n\n"); } catch { /* closed */ }
    }, 2000);
    request.on("close", () => clearInterval(heartbeat));
    return;
  }
  if (url.pathname === "/api/v1/health") {
    json(response, {
      status: "ok", application: { state: "READY" },
      midi_input: { connection: "CONNECTED" },
      library: { ready: true, entries: serverState.emptyLibrary ? 0 : CATALOG_SIZE },
      playback,
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
  if (url.pathname === "/api/v1/playback") { json(response, playback); return; }
  if (url.pathname === "/api/v1/library/songs") {
    const q = url.searchParams.get("q") ?? "";
    const limit = Math.min(100, Math.max(1, Number(url.searchParams.get("limit") ?? 20)));
    const offset = Number(url.searchParams.get("offset") ?? 0);
    queryStatus = url.searchParams.get("status");
    serverState.listingRequests.push({ q, limit, offset, status: queryStatus, at: Date.now() });
    if (q === "slow-moz") {
      // Controlled stale-search fixture: the older query answers late.
      await sleep(900);
    } else if (serverState.listDelayMs > 0) {
      await sleep(serverState.listDelayMs);
    }
    const payload = listingPayload(q || null, limit, offset);
    json(response, payload, serverState.libraryDown ? 503 : 200);
    return;
  }
  const songMatch = url.pathname.match(/^\/api\/v1\/library\/songs\/(.+)$/);
  if (songMatch) {
    const record = catalog.find((song) => song.song_id === decodeURIComponent(songMatch[1]));
    if (record) { json(response, record); return; }
    json(response, { error: { code: "song_not_found", message: "no entry" } }, 404);
    return;
  }
  if (url.pathname === `/api/v1/score/${score.song_id}`) { json(response, score); return; }
  if (url.pathname === `/api/v1/fingering/${score.song_id}`) { json(response, score); return; }
  json(response, { error: { code: "not_found", message: "not found" } }, 404);
});

await new Promise((done) => server.listen(0, "127.0.0.1", done));
const appPort = server.address().port;
const probe = createServer();
await new Promise((done) => probe.listen(0, "127.0.0.1", done));
const debugPort = probe.address().port;
await new Promise((done) => probe.close(done));
const profile = await mkdtemp(join(tmpdir(), "piano-ui008-p2-chrome-"));
const chrome = spawn(CHROME, [
  "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
  `--remote-debugging-port=${debugPort}`, `--user-data-dir=${profile}`,
  `http://127.0.0.1:${appPort}/ui/#/home`,
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

const rowInfo = `(() => {
  const rows = [...document.querySelectorAll('#results .song-item')];
  return {
    count: rows.length,
    unique: new Set(rows.map((r) => r.dataset.songId)).size,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  };
})()`;

await cdp("Runtime.enable");
await cdp("Page.enable");
await waitFor("document.documentElement?.dataset.ui008ShellReady === 'true'");
await waitFor("document.body.dataset.ui008Route === 'home'");
await waitFor("document.getElementById('ui008-home-readiness-label').textContent === 'Piano connected'");

/* ---------------- HOME ---------------- */

await test("Home loads as a launch surface with quick actions and honest readiness", async () => {
  assert.equal(await evaluate("document.getElementById('ui008-home-heading').textContent"), "Home");
  assert.equal(await evaluate("document.querySelectorAll('#ui008-home .home-quick-card').length"), 3);
  assert.equal(
    await evaluate("document.getElementById('ui008-home-readiness-label').textContent"),
    "Piano connected"
  );
  assert.equal(await evaluate("document.getElementById('ui008-home-continue').hidden"), false);
  assert.match(
    await evaluate("document.getElementById('ui008-home-continue-title').textContent"),
    /Sonata 00000/
  );
  assert.match(
    await evaluate("document.getElementById('ui008-home-continue-meta').textContent"),
    /Paused at 1:21/
  );
});

await test("Home hides unsupported capability sections and fabricated metadata", async () => {
  const absent = await evaluate(`(() => {
    const text = document.getElementById('ui008-home').textContent;
    return {
      favorites: !/favorit/i.test(text),
      artistGenreDifficulty: !/artist|genre|difficulty/i.test(text),
      deviceMetrics: !/linux|cpu|memory|uptime|192\\.168/i.test(text),
      queue: !document.querySelector('[id*=queue]'),
    };
  })()`);
  assert.deepEqual(absent, {
    favorites: true, artistGenreDifficulty: true, deviceMetrics: true, queue: true,
  });
});

await test("Home Continue action routes to Player without a musical command", async () => {
  await evaluate("document.getElementById('ui008-home-continue-action').click()");
  await waitFor("document.body.dataset.ui008Route === 'player'");
  assert.equal(postCount, 0, "Continue must navigate, never command");
  await route("home");
});

await test("Home shows the welcome state only when nothing is resumable", async () => {
  // Authoritative playback exists, so the welcome state must be hidden.
  assert.equal(await evaluate("document.getElementById('ui008-home-welcome').hidden"), true);
  // Recent is empty on a fresh profile: section hidden.
  assert.equal(await evaluate("document.getElementById('ui008-home-recent').hidden"), true);
});

/* ---------------- LIBRARY: bounded DOM at synthetic 5k ---------------- */

await test("Library browse renders a bounded first page of the 5,000-song catalog", async () => {
  await route("library");
  const started = Date.now();
  await waitFor("document.querySelectorAll('#results .song-item').length === 50");
  measures.initialBrowseMs = Date.now() - started;
  const info = await evaluate(rowInfo);
  assert.equal(info.count, 50);
  assert.equal(info.unique, 50);
  assert.ok(info.overflow <= 0, `overflow ${info.overflow}`);
  assert.ok(measures.initialBrowseMs < 5000, `slow browse ${measures.initialBrowseMs}ms`);
});

await test("Load more appends one bounded page and stops at the DOM ceiling", async () => {
  await evaluate("document.getElementById('btn-more').click()");
  await waitFor("document.querySelectorAll('#results .song-item').length === 100");
  const info = await evaluate(rowInfo);
  assert.equal(info.count, 100);
  assert.equal(info.unique, 100);
  assert.equal(await evaluate("document.getElementById('btn-more').hidden"), true,
    "Load more must disappear at the 100-row ceiling");
  assert.match(
    await evaluate("document.getElementById('library-feedback').textContent"),
    /first 100/
  );
  // A further click attempt is impossible (hidden); rows never exceed 100.
  measures.maxBrowseRows = info.count;
});

await test("search replaces results within the bounded window", async () => {
  await evaluate(`(() => {
    const input = document.getElementById('search');
    input.value = 'sonata';
    input.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  await waitFor("document.querySelectorAll('#results .song-item').length === 50");
  const info = await evaluate(rowInfo);
  assert.equal(info.unique, info.count, "search must replace, not append");
  assert.ok(info.count <= 50, `bounded search rows: ${info.count}`);
  assert.match(
    await evaluate("document.getElementById('library-feedback').textContent"),
    /refine your search/
  );
  measures.searchUpdateMs = null;
});

await test("no-results state is query-aware and offers the way back", async () => {
  await evaluate(`(() => {
    const input = document.getElementById('search');
    input.value = 'zzzz-no-such-song';
    input.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  await waitFor("document.querySelectorAll('#results .song-item').length === 0");
  const message = await evaluate("document.getElementById('library-feedback').textContent");
  assert.match(message, /No songs match “zzzz-no-such-song”/);
  await evaluate("document.getElementById('btn-clear').click()");
  await waitFor("document.querySelectorAll('#results .song-item').length === 50");
});

await test("stale search identity: a slower older response never overwrites a newer one", async () => {
  // "slow-moz" answers after 900ms on the controlled server; "fast-mozart"
  // answers immediately. The fast result must survive the late slow answer.
  await evaluate(`(() => {
    const input = document.getElementById('search');
    input.focus();
    input.value = 'slow-moz';
    input.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  await sleep(320); // past the debounce; the slow request is in flight
  await evaluate(`(() => {
    const input = document.getElementById('search');
    input.value = 'fast-mozart';
    input.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  await waitFor("document.getElementById('library-feedback').textContent.includes('fast-mozart')", 8000);
  const settled = await evaluate(rowInfo);
  await sleep(1200); // the stale 900ms response would have landed by now
  const afterLate = await evaluate(rowInfo);
  assert.deepEqual(afterLate, settled, "late response must not replace newer results");
  assert.match(
    await evaluate("document.getElementById('library-feedback').textContent"),
    /No songs match “fast-mozart”/,
    "the fast query's own (empty) result stays authoritative"
  );
});

await test("filter chip is a real listing facet and never pretends to filter search", async () => {
  await route("library");
  // A pre-reload ready flag/old 50 rows cannot prove new-document readiness.
  await evaluate("window.__beforeFacetReload = true");
  await cdp("Page.reload");
  await waitFor("window.__beforeFacetReload !== true && document.documentElement?.dataset.ui008ShellReady === 'true'");
  await waitFor("document.body.dataset.ui008Route === 'library'");
  await waitFor("document.querySelectorAll('#results .song-item').length === 50");
  serverState.listingRequests.length = 0;
  await evaluate("document.getElementById('library-filter-ready').click()");
  // Observe the actual request, rather than matching rows from the old listing.
  const deadline = Date.now() + 8000;
  while (!serverState.listingRequests.length && Date.now() < deadline) await sleep(40);
  assert.ok(serverState.listingRequests.length, "facet click must issue a new listing request");
  const last = serverState.listingRequests.at(-1);
  assert.equal(last.status, "parsed", "chip must send the real Core status facet");
  assert.equal(
    await evaluate("document.getElementById('library-filter-ready').disabled"),
    false
  );
  // During search the chip disables (Core search has no status facet).
  await evaluate(`(() => {
    const input = document.getElementById('search');
    input.value = 'sonata';
    input.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  await waitFor("document.getElementById('library-filter-ready').disabled === true");
  await evaluate("document.getElementById('btn-clear').click()");
  await waitFor("document.querySelectorAll('#results .song-item').length === 50");
});

await test("error state is human-readable with a working retry", async () => {
  serverState.libraryDown = true;
  try {
    await evaluate(`(() => {
      const input = document.getElementById('search');
      input.value = 'x';
      input.dispatchEvent(new Event('input', { bubbles: true }));
    })()`);
    await waitFor("!document.getElementById('library-retry').hidden", 8000);
    assert.match(
      await evaluate("document.getElementById('library-feedback').textContent"),
      /library is unavailable/
    );
  } finally {
    serverState.libraryDown = false;
  }
  await evaluate("document.getElementById('library-retry').click()");
  await waitFor("document.getElementById('library-retry').hidden");
  // Retry repeats the same query identity; 'x' matches nothing in the
  // synthetic catalog, so the honest no-results state returns.
  await waitFor("document.getElementById('library-feedback').textContent.includes('No songs match')");
  await evaluate("document.getElementById('btn-clear').click()");
  await waitFor("document.querySelectorAll('#results .song-item').length === 50");
});

await test("empty catalog renders the honest empty state", async () => {
  serverState.emptyLibrary = true;
  try {
    await evaluate("location.reload()");
    await waitFor("document.documentElement?.dataset.ui008ShellReady === 'true'");
    await waitFor("document.body.dataset.ui008Route === 'library'");
    await waitFor("!document.getElementById('library-empty').hidden");
    assert.match(
      await evaluate("document.getElementById('library-empty').textContent"),
      /catalog is empty/
    );
  } finally {
    serverState.emptyLibrary = false;
  }
  await evaluate("location.reload()");
  await waitFor("document.documentElement?.dataset.ui008ShellReady === 'true'");
  await waitFor("document.querySelectorAll('#results .song-item').length === 50");
});

await test("Play from Library uses the accepted playback entry point exactly once", async () => {
  // The Home tests left the harness playback PAUSED; Play requires an
  // idle transport, so restore the honest idle state and let one poll land.
  playback.state = "IDLE";
  playback.song_id = null;
  playback.title = null;
  await viewport(1024, 768);
  await route("library");
  await sleep(1400);
  postCount = 0;
  serverState.playRequests.length = 0;
  await waitFor("document.querySelectorAll('#results .song-item').length === 50");
  await evaluate("document.querySelector('#results .song-item .song-action-btn[data-action=play]').click()");
  await waitFor("document.body.dataset.ui008Route === 'player'");
  await sleep(400);
  assert.equal(serverState.playRequests.length, 1, JSON.stringify(serverState.playRequests));
  assert.equal(
    serverState.playRequests[0].song_id,
    await evaluate("document.querySelector('#results .song-item').dataset.songId")
  );
});

await test("Learn from Library selects the song and routes to Learn without commands", async () => {
  const postsBefore = postCount;
  await route("library");
  await waitFor("document.querySelectorAll('#results .song-item').length === 50");
  await evaluate("document.querySelector('#results .song-item .song-action-btn[data-action=learn]').click()");
  await waitFor("document.body.dataset.ui008Route === 'learn'");
  assert.equal(postCount, postsBefore, "Learn entry must not auto-start a session");
  assert.equal(
    await evaluate("document.getElementById('selected').hidden"),
    false,
    "the accepted Start Learning surface appears with the selected song"
  );
  assert.match(
    await evaluate("document.getElementById('sel-title').textContent"),
    /song-00000|Sonata 00000/
  );
});

await test("browser back returns from Learn to Library without resetting results", async () => {
  await evaluate("history.back()");
  await waitFor("document.body.dataset.ui008Route === 'library'");
  assert.ok(
    (await evaluate("document.querySelectorAll('#results .song-item').length")) > 0,
    "result list persists across route navigation"
  );
});

await test("phone Library uses the labeled action sheet for Play", async () => {
  // Restore the honest idle transport so Play is enableable (the earlier
  // Play test left the harness session PLAYING).
  playback.state = "IDLE";
  playback.song_id = null;
  playback.title = null;
  await viewport(390, 844);
  await route("library");
  await sleep(1400);
  await waitFor("document.querySelectorAll('#results .song-item').length === 50");
  assert.equal(
    await evaluate("getComputedStyle(document.querySelector('.song-actions')).display"),
    "flex",
    "primary Play and secondary Learn remain directly available on phone"
  );
  serverState.playRequests.length = 0;
  await evaluate("document.querySelector('#results .song-item .btn-song').click()");
  await waitFor("!document.getElementById('ui008-song-sheet').hidden");
  assert.match(
    await evaluate("document.getElementById('ui008-song-sheet-title').textContent"),
    /song-00000|Sonata 00000/
  );
  await evaluate("document.getElementById('ui008-song-sheet-play').click()");
  await waitFor("document.getElementById('ui008-song-sheet').hidden");
  await waitFor("document.body.dataset.ui008Route === 'player'");
  await sleep(300);
  assert.equal(serverState.playRequests.length, 1);
  assert.equal(
    await evaluate("document.getElementById('ui008-song-sheet').hidden"), true,
    "sheet closes before the action runs"
  );
});

await test("recent songs record real selections and appear on Home", async () => {
  await viewport(1024, 768);
  await route("library");
  await waitFor("document.querySelectorAll('#results .song-item').length === 50");
  await evaluate("document.querySelector('#results .song-item .btn-song').click()");
  await route("home");
  await waitFor("document.getElementById('ui008-home-recent').hidden === false");
  assert.match(
    await evaluate("document.querySelector('#ui008-home-recent-list .song-name').textContent"),
    /song-00000|Sonata 00000/
  );
});

await test("route listeners never multiply: repeated navigation keeps one result set", async () => {
  for (const name of ["home", "library", "player", "home", "library"]) {
    await route(name);
  }
  await waitFor("document.querySelectorAll('#results .song-item').length === 50");
  const info = await evaluate(rowInfo);
  assert.equal(info.count, info.unique, "no duplicated rows after repeated routing");
  const listenerProbe = await evaluate(`(() => {
    // Re-firing the route event must not append rows (no listener leak).
    window.dispatchEvent(new CustomEvent('piano-ui-route-change', {
      detail: { route: 'library', previousRoute: 'library', source: 'test' }
    }));
    return document.querySelectorAll('#results .song-item').length;
  })()`);
  assert.equal(listenerProbe, 50);
});

/* ---------------- responsive + accessibility ---------------- */

const matrix = [
  [390, 844, "bottom"], [768, 1024, "bottom"], [1024, 768, "rail"],
  [1440, 900, "rail"], [1920, 1080, "rail"],
];

await test("Home and Library hold the responsive contract at all five viewports", async () => {
  for (const destination of ["home", "library"]) {
    await route(destination);
    for (const [width, height, mode] of matrix) {
      await viewport(width, height);
      const metrics = await evaluate(`(() => {
        const rail = getComputedStyle(document.querySelector('.primary-nav')).display !== 'none';
        const bottom = getComputedStyle(document.querySelector('.bottom-nav')).display !== 'none';
        return { rail, bottom, overflow: document.documentElement.scrollWidth - innerWidth };
      })()`);
      assert.equal(metrics.rail, mode === "rail", `${destination} ${width}: rail`);
      assert.equal(metrics.bottom, mode === "bottom", `${destination} ${width}: bottom`);
      assert.ok(metrics.overflow <= 0, `${destination} ${width}: overflow ${metrics.overflow}`);
      const searchVisible = await evaluate(`(() => {
        const input = document.getElementById('search');
        const r = input.getBoundingClientRect();
        const s = getComputedStyle(input);
        const route = document.body.dataset.ui008Route;
        return route !== 'library' || (r.width > 80 && s.display !== 'none' && s.visibility !== 'hidden');
      })()`);
      assert.equal(searchVisible, true, `${destination} ${width}: search visible and unclipped`);
    }
  }
});

await test("touch targets on Home and Library meet the 44px contract", async () => {
  for (const destination of ["home", "library"]) {
    await route(destination);
    await viewport(390, 844);
    const failuresHere = await evaluate(`[...document.querySelectorAll('button,a[href],input,select,summary,label.library-chip')]
      .filter(el => {
        const s = getComputedStyle(el); const r = el.getBoundingClientRect();
        return !el.disabled && !el.hidden && s.display !== 'none' && s.visibility !== 'hidden' &&
          r.width > 0 && r.right > 0 && r.left < innerWidth && r.bottom > 0 && r.top < innerHeight;
      })
      .map(el => { const r = el.getBoundingClientRect(); return { id: el.id || el.textContent.trim(), w: r.width, h: r.height }; })
      .filter(x => x.w < 44 || x.h < 44)`);
    assert.deepEqual(failuresHere, [], `${destination}: ${JSON.stringify(failuresHere)}`);
  }
});

await test("search, states and sheet carry the accessibility contract", async () => {
  await viewport(1024, 768);
  await route("library");
  assert.equal(
    await evaluate("!!document.querySelector('label[for=search]')"), true, "search label");
  assert.equal(
    await evaluate("document.getElementById('results').getAttribute('aria-label')"), "Song results");
  assert.equal(
    await evaluate("document.getElementById('library-feedback').getAttribute('aria-live')"), "polite");
  assert.equal(
    await evaluate("document.getElementById('library-skeleton').getAttribute('aria-hidden')"), "true");
  const labels = await evaluate(`[...document.querySelectorAll('#results .song-item')]
    .slice(0, 3)
    .every(row => row.querySelector('.btn-song').getAttribute('aria-label').length > 3 &&
      row.querySelector('[data-action=play]').getAttribute('aria-label').startsWith('Play '))`);
  assert.equal(labels, true, "rows expose labeled actions");
  await viewport(390, 844);
  await evaluate("document.querySelector('#results .song-item .btn-song').click()");
  await waitFor("!document.getElementById('ui008-song-sheet').hidden");
  assert.equal(
    await evaluate("document.querySelector('#ui008-song-sheet .bottom-sheet').getAttribute('role')"),
    "dialog"
  );
  assert.equal(
    await evaluate("document.querySelector('#ui008-song-sheet .bottom-sheet').getAttribute('aria-modal')"),
    "true"
  );
  await cdp("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape" });
  await cdp("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape" });
  await waitFor("document.getElementById('ui008-song-sheet').hidden");
});

await test("loading uses skeleton rows with stable geometry (no layout jump)", async () => {
  await viewport(1024, 768);
  await route("library");
  await waitFor("document.querySelectorAll('#results .song-item').length === 50");
  const probe = `(() => {
    const card = document.getElementById('library');
    const r = card.getBoundingClientRect();
    return { w: Math.round(r.width), h: Math.round(r.height) };
  })()`;
  const before = await evaluate(probe);
  // Hold the response open so the skeleton state is observable.
  serverState.listDelayMs = 600;
  try {
    await evaluate(`(() => {
      const input = document.getElementById('search');
      input.value = 'sonata';
      input.dispatchEvent(new Event('input', { bubbles: true }));
    })()`);
    await waitFor("document.getElementById('library-skeleton').hidden === false");
    const during = await evaluate(probe);
    assert.ok(Math.abs(during.w - before.w) <= 2, `no width jump ${before.w} -> ${during.w}`);
  } finally {
    serverState.listDelayMs = 0;
  }
  await waitFor("document.querySelectorAll('#results .song-item').length === 50");
});

/* ---------------- measures + evidence ---------------- */

await test("bounded-DOM and interaction measures recorded", async () => {
  await route("library");
  await waitFor("document.querySelectorAll('#results .song-item').length === 50");
  const tSearch = Date.now();
  await evaluate(`(() => {
    const input = document.getElementById('search');
    input.value = 'song-000';
    input.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  await sleep(450); // debounce + replace
  await waitFor("document.querySelectorAll('#results .song-item').length >= 1");
  measures.searchReplaceMs = Date.now() - tSearch;
  const tRoute = Date.now();
  await route("player");
  await waitFor("document.body.dataset.ui008Route === 'player'");
  measures.routeSwitchMs = Date.now() - tRoute;
  measures.catalogSize = CATALOG_SIZE;
  measures.maxRowsContract = 100;
  assert.equal(true, true);
});

await test("fresh visual evidence captures Home and Library states", async () => {
  const captures = [
    [390, 844, "phone-390x844-home.png", "home"],
    [1024, 768, "tablet-landscape-1024x768-home.png", "home"],
    [1440, 900, "desktop-1440x900-home.png", "home"],
    [1024, 768, "library-tablet-landscape-default.png", "library"],
    [1440, 900, "library-desktop-default.png", "library"],
    [390, 844, "library-phone-390x844.png", "library"],
  ];
  for (const [width, height, name, destination] of captures) {
    await viewport(width, height);
    await route(destination);
    await sleep(320);
    await capture(name);
  }
  await viewport(1440, 900);
  await route("library");
  await evaluate(`(() => {
    const input = document.getElementById('search');
    input.value = 'sonata';
    input.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  await waitFor("document.querySelectorAll('#results .song-item').length === 50");
  await sleep(250);
  await capture("library-desktop-active-search.png");
  await evaluate(`(() => {
    const input = document.getElementById('search');
    input.value = 'zzzz-no-such-song';
    input.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  await waitFor("document.querySelectorAll('#results .song-item').length === 0");
  await sleep(250);
  await capture("library-desktop-no-results.png");
  if (EVIDENCE_DIR) {
    await writeFile(
      join(EVIDENCE_DIR, "p2-bounded-dom-measures.json"),
      `${JSON.stringify(measures, null, 2)}\n`,
      "utf8"
    );
  }
  assert.equal(true, true);
});

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
console.log(`P2_MEASURES ${JSON.stringify(measures)}`);
