// Deterministic unit tests for the pure UI logic exported by the
// packaged app.js (PIANO-P013). Run by tests/test_web_logic.py with
// node (skipped when node is unavailable). The module's browser-only
// wiring does not execute here (no `document` in node).
import assert from "node:assert/strict";
import {
  TEMPO_MIN,
  TEMPO_MAX,
  clampTempo,
  formatTempo,
  formatDuration,
  noteName,
  controlEnablement,
  playbackSummary,
  statusTone,
  errorText,
  searchUrl,
  isStaleResponse,
  LED_MODES,
  LED_BRIGHTNESS_MIN,
  LED_BRIGHTNESS_MAX,
  clampBrightness,
  ledSummary,
  ledStatusTone,
  ledControlEnablement,
  isLedConfigured,
  ApiClient,
  ApiError,
  practiceTargetText,
  practiceSummary,
  practiceStatusTone,
  practiceView,
  practiceConflictWithPlayback,
  practiceControlEnablement,
  LEARNING_MODES,
  learningStartConfig,
  PLAYBACK_FRESHNESS_MS,
  clampPlaybackPosition,
  createPlaybackAnchor,
  playbackFrame,
  playerPresentationState,
  playbackNeedsReanchor,
  PLAYBACK_PAYLOAD_CLASS,
  classifyPlaybackPayload,
  isValidPlaybackResponse,
  isUsablePlaybackSnapshot,
  PLAYBACK_TEMPO_MIN,
  PLAYBACK_TEMPO_MAX,
  LEARNING_FRESHNESS_MS,
  LEARNING_STATES,
  isValidLearningSnapshot,
  LearningCoordinator,
} from "../../src/piano_core/web/static/app.js";

function test(name, fn) {
  try {
    fn();
    console.log(`ok - ${name}`);
  } catch (error) {
    console.error(`FAIL - ${name}`);
    console.error(error);
    process.exitCode = 1;
  }
}

// ---- tempo -----------------------------------------------------------

test("tempo bounds are canonical (0.25..4.0)", () => {
  assert.equal(TEMPO_MIN, 0.25);
  assert.equal(TEMPO_MAX, 4.0);
});

test("clampTempo clamps low", () => {
  assert.equal(clampTempo(0.1), 0.25);
  assert.equal(clampTempo(-2), 0.25);
});

test("clampTempo clamps high", () => {
  assert.equal(clampTempo(5), 4.0);
  assert.equal(clampTempo(1000), 4.0);
});

test("clampTempo quantizes to 2 decimals", () => {
  assert.equal(clampTempo(1.234), 1.23);
  assert.equal(clampTempo(0.999), 1.0);
});

test("clampTempo rejects non-numeric input", () => {
  assert.equal(clampTempo("abc"), 1.0);
  assert.equal(clampTempo(NaN), 1.0);
});

test("formatTempo renders bounded label", () => {
  assert.equal(formatTempo(1), "1.00×");
  assert.equal(formatTempo("9"), "4.00×");
});

// ---- durations / notes -------------------------------------------------

test("formatDuration handles null and seconds", () => {
  assert.equal(formatDuration(null), "—");
  assert.equal(formatDuration(undefined), "—");
  assert.equal(formatDuration(0), "0:00");
  assert.equal(formatDuration(62), "1:02");
  assert.equal(formatDuration(150.6), "2:30");
  assert.equal(formatDuration(3671), "1:01:11");
});

test("noteName maps MIDI numbers", () => {
  assert.equal(noteName(60), "C4");
  assert.equal(noteName(21), "A0");
  assert.equal(noteName(69), "A4");
  assert.equal(noteName(108), "C8");
  assert.equal(noteName(-1), "#-1");
  assert.equal(noteName(200), "#200");
});

// ---- control enablement --------------------------------------------------

const all = { play: true, pause: true, resume: true, stop: true };
const none = {
  play: false,
  pause: false,
  resume: false,
  stop: false,
};

function enablement(state, available = true) {
  return controlEnablement({ available, state });
}

test("enablement: IDLE allows play only", () => {
  assert.deepEqual(enablement("IDLE"), { ...none, play: true });
});

test("enablement: COMPLETED and FAILED allow play (new session)", () => {
  assert.deepEqual(enablement("COMPLETED"), { ...none, play: true });
  assert.deepEqual(enablement("FAILED"), { ...none, play: true });
});

test("enablement: PLAYING allows pause+stop", () => {
  assert.deepEqual(enablement("PLAYING"), { ...none, pause: true, stop: true });
});

test("enablement: PAUSED allows resume+stop", () => {
  assert.deepEqual(enablement("PAUSED"), { ...none, resume: true, stop: true });
});

test("enablement: STOPPING disables everything", () => {
  assert.deepEqual(enablement("STOPPING"), none);
});

test("enablement: unavailable or missing snapshot disables everything", () => {
  assert.deepEqual(enablement("PLAYING", false), none);
  assert.deepEqual(controlEnablement(null), none);
  assert.deepEqual(controlEnablement({ available: true }), none);
  assert.notDeepEqual(enablement("PLAYING"), all);
});

test("playbackSummary covers unavailable", () => {
  assert.equal(playbackSummary({ available: true, state: "PAUSED" }), "PAUSED");
  assert.equal(playbackSummary(null), "unavailable");
  assert.equal(playbackSummary({ available: false, state: null }), "unavailable");
});

// ---- professional player clock / state ------------------------------------

const playingSnapshot = {
  available: true,
  state: "PLAYING",
  song_id: "song-1",
  position_seconds: 12,
  duration_seconds: 60,
  tempo_multiplier: 1.5,
};

test("player freshness window is finite and explicit", () => {
  assert.equal(PLAYBACK_FRESHNESS_MS, 3500);
});

test("position clamping never invents negative or beyond-duration time", () => {
  assert.equal(clampPlaybackPosition(-2, 60), 0);
  assert.equal(clampPlaybackPosition(70, 60), 60);
  assert.equal(clampPlaybackPosition(7, null), 7);
  assert.equal(clampPlaybackPosition("bad", 60), 0);
});

test("PLAYING interpolation follows Core tempo inside freshness window", () => {
  const anchor = createPlaybackAnchor(playingSnapshot, 1000);
  const frame = playbackFrame(anchor, 3000);
  assert.equal(frame.position, 15);
  assert.equal(frame.remaining, 45);
  assert.equal(frame.progress, 0.25);
  assert.equal(frame.stale, false);
});

test("PAUSED stays frozen and COMPLETED resolves to authoritative duration", () => {
  const paused = { ...playingSnapshot, state: "PAUSED", position_seconds: 22 };
  assert.equal(playbackFrame(createPlaybackAnchor(paused, 0), 3000).position, 22);
  const complete = { ...playingSnapshot, state: "COMPLETED", position_seconds: 59.8 };
  const frame = playbackFrame(createPlaybackAnchor(complete, 0), 3000);
  assert.equal(frame.position, 60);
  assert.equal(frame.remaining, 0);
  assert.equal(frame.progress, 1);
});

test("stale PLAYING state freezes at the bounded horizon", () => {
  const anchor = createPlaybackAnchor(playingSnapshot, 1000);
  const atHorizon = playbackFrame(anchor, 4500);
  const muchLater = playbackFrame(anchor, 90000);
  assert.equal(atHorizon.position, muchLater.position);
  assert.equal(muchLater.position, 17.25);
  assert.equal(muchLater.stale, true);
  assert.deepEqual(controlEnablement(playingSnapshot, false), none);
});

test("presentation states cover load, reload, terminal, error, and unavailable", () => {
  assert.equal(playerPresentationState(null, false, true, true), "loading");
  assert.equal(playerPresentationState(null), "unavailable");
  // Canonical Core IDLE payload shape (position/duration/tempo always
  // published as real numbers for a live session).
  const idleSnapshot = {
    available: true,
    state: "IDLE",
    position_seconds: 0,
    duration_seconds: 0,
    tempo_multiplier: 1,
  };
  assert.equal(playerPresentationState(idleSnapshot), "unloaded");
  assert.equal(playerPresentationState(idleSnapshot, true), "ready");
  assert.equal(playerPresentationState(playingSnapshot), "playing");
  assert.equal(playerPresentationState({ ...playingSnapshot, state: "PAUSED" }), "paused");
  // COMPLETED carries Core's canonical terminal position (== duration).
  assert.equal(
    playerPresentationState({ ...playingSnapshot, state: "COMPLETED", position_seconds: 60 }),
    "completed"
  );
  assert.equal(playerPresentationState({ ...playingSnapshot, state: "FAILED" }), "error");
  assert.equal(playerPresentationState(playingSnapshot, false, false), "unavailable");
});

test("re-anchor detects authoritative state changes and position jumps", () => {
  const anchor = createPlaybackAnchor(playingSnapshot, 1000);
  assert.equal(
    playbackNeedsReanchor(anchor, { ...playingSnapshot, position_seconds: 15.1 }, 3000),
    false
  );
  assert.equal(
    playbackNeedsReanchor(anchor, { ...playingSnapshot, position_seconds: 20 }, 3000),
    true
  );
  assert.equal(playbackNeedsReanchor(anchor, { ...playingSnapshot, state: "PAUSED" }, 3000), true);
  assert.equal(playbackNeedsReanchor(anchor, { ...playingSnapshot, song_id: "song-2" }, 3000), true);
});

test("statusTone is never color-only (tones pair with text)", () => {
  assert.equal(statusTone("app", "READY"), "ok");
  assert.equal(statusTone("app", "STOPPING"), "warn");
  assert.equal(statusTone("midi", "CONNECTED"), "ok");
  assert.equal(statusTone("midi", "DISCONNECTED"), "warn");
  assert.equal(statusTone("library", true), "ok");
  assert.equal(statusTone("library", false), "bad");
  assert.equal(statusTone("playback", "PLAYING"), "ok");
  assert.equal(statusTone("playback", "IDLE"), "warn");
  assert.equal(statusTone("app", null), "bad");
});

// ---- error rendering -------------------------------------------------------

test("errorText maps stable API codes to human messages", () => {
  assert.match(errorText("song_not_found"), /not found in the library/i);
  assert.match(errorText("playback_already_active"), /already active/i);
  assert.match(errorText("invalid_tempo"), /0\.25/);
});

test("errorText never returns raw internal messages", () => {
  assert.equal(errorText("unknown_code_xyz", undefined), "Something went wrong.");
  assert.equal(errorText("unknown_code_xyz", "custom fallback"), "custom fallback");
});

// ---- search URL --------------------------------------------------------------

function parse(url) {
  return new URL(url, "http://appliance.invalid");
}

test("searchUrl hits the bounded /api/v1 endpoint", () => {
  const url = parse(searchUrl("sonata", 50));
  assert.equal(url.pathname, "/api/v1/library/songs");
  assert.equal(url.searchParams.get("q"), "sonata");
  assert.equal(url.searchParams.get("limit"), "50");
});

test("searchUrl clamps limit into 1..100", () => {
  assert.equal(parse(searchUrl("x", 5000)).searchParams.get("limit"), "100");
  assert.equal(parse(searchUrl("x", 0.5)).searchParams.get("limit"), "1");
});

test("searchUrl omits q for unfiltered listing and uses offset", () => {
  const url = parse(searchUrl("", 50, 100));
  assert.equal(url.searchParams.get("q"), null);
  assert.equal(url.searchParams.get("offset"), "100");
});

test("searchUrl URL-encodes hostile queries", () => {
  const url = parse(searchUrl('<script>alert(1)</script>&x"', 50));
  assert.equal(url.searchParams.get("q"), '<script>alert(1)</script>&x"');
  assert.ok(!url.href.includes("<script>"));
});

test("isStaleResponse discards outdated search responses", () => {
  assert.equal(isStaleResponse(1, 2), true);
  assert.equal(isStaleResponse(2, 2), false);
});

// ---- ApiClient ------------------------------------------------------------

function fakeFetch(handler) {
  const calls = [];
  const impl = async (url, options) => {
    calls.push({ url, options });
    return handler(url, options);
  };
  impl.calls = calls;
  return impl;
}

const okPayload = { ok: true, json: async () => ({ state: "IDLE" }) };

test("ApiClient.play posts song_id (never a path) with clamped tempo", async () => {
  const fetchImpl = fakeFetch(() => okPayload);
  const client = new ApiClient(fetchImpl);
  await client.play("some-song-id", 9);
  const call = fetchImpl.calls[0];
  assert.equal(call.url, "/api/v1/playback/play");
  assert.equal(call.options.method, "POST");
  const body = JSON.parse(call.options.body);
  assert.deepEqual(Object.keys(body).sort(), ["song_id", "tempo_multiplier"]);
  assert.equal(body.song_id, "some-song-id");
  assert.equal(body.tempo_multiplier, 4.0);
});

test("ApiClient hydrates an active song by encoded canonical song_id", async () => {
  const fetchImpl = fakeFetch(() => okPayload);
  const client = new ApiClient(fetchImpl);
  await client.song("song/id with spaces");
  assert.equal(fetchImpl.calls[0].url, "/api/v1/library/songs/song%2Fid%20with%20spaces");
  assert.equal(fetchImpl.calls[0].options.method, "GET");
});

test("ApiClient uses relative same-origin paths only", async () => {
  const fetchImpl = fakeFetch(() => okPayload);
  const client = new ApiClient(fetchImpl);
  await client.health();
  await client.state();
  await client.playback();
  await client.pause();
  await client.resume();
  await client.stop();
  for (const call of fetchImpl.calls) {
    assert.ok(call.url.startsWith("/api/v1/"), call.url);
    assert.ok(!call.url.includes("http://") && !call.url.includes("https://"));
  }
});

test("ApiClient normalizes API error payloads", async () => {
  const fetchImpl = fakeFetch(() => ({
    ok: false,
    status: 409,
    json: async () => ({
      error: { code: "playback_already_active", message: "busy", status: 409 },
    }),
  }));
  const client = new ApiClient(fetchImpl);
  await assert.rejects(
    client.pause(),
    (error) =>
      error instanceof ApiError && error.code === "playback_already_active" && error.status === 409
  );
});

test("ApiClient surfaces network failure as one deterministic error", async () => {
  const fetchImpl = async () => {
    throw new TypeError("connection refused");
  };
  const client = new ApiClient(fetchImpl, 50);
  await assert.rejects(
    client.health(),
    (error) => error instanceof ApiError && error.code === "network_error"
  );
});

test("ApiClient normalizes non-JSON failure responses", async () => {
  // Complete 502 body whose content is not JSON: the body transported fine,
  // so this is a PHASE 4 response problem -> invalid_response with the real
  // status (a mutating command maps this to outcome unknown).
  const fetchImpl = fakeFetch(() => ({
    ok: false,
    status: 502,
    text: async () => "<html>bad gateway</html>",
  }));
  const client = new ApiClient(fetchImpl);
  await assert.rejects(
    client.health(),
    (error) =>
      error instanceof ApiError &&
      error.status === 502 &&
      error.code === "invalid_response" &&
      error.phase === "classify"
  );
});

test("ApiClient classifies an unreadable failure body as transport failure", async () => {
  // The 502 status alone proves nothing when the body never completes
  // (PHASE 2): classification is by request phase, never by status or the
  // exception message, so the deterministic surface is network_error.
  const fetchImpl = fakeFetch(() => ({
    ok: false,
    status: 502,
    json: async () => {
      throw new Error("not json");
    },
  }));
  const client = new ApiClient(fetchImpl);
  await assert.rejects(
    client.health(),
    (error) =>
      error instanceof ApiError &&
      error.status === 0 &&
      error.code === "network_error" &&
      error.phase === "body"
  );
});

// ---- LED logic (PIANO-P014) -------------------------------------------

test("LED brightness bounds are canonical (0..100)", () => {
  assert.equal(LED_BRIGHTNESS_MIN, 0);
  assert.equal(LED_BRIGHTNESS_MAX, 100);
});

test("clampBrightness clamps and quantizes", () => {
  assert.equal(clampBrightness(-5), 0);
  assert.equal(clampBrightness(101), 100);
  assert.equal(clampBrightness(49.6), 50);
  assert.equal(clampBrightness("60"), 60);
  assert.equal(clampBrightness("abc"), 100);
  assert.equal(clampBrightness(NaN), 100);
});

test("LED modes are the bounded canonical set", () => {
  assert.deepEqual(LED_MODES, ["off", "playback", "physical", "all"]);
});

test("ledSummary distinguishes connected/listening/disabled/unavailable", () => {
  assert.equal(ledSummary(null), "unavailable");
  assert.equal(ledSummary({ configured: false }), "unavailable");
  assert.equal(ledSummary({ configured: true, enabled: false }), "disabled");
  assert.equal(ledSummary({ configured: true, enabled: true, available: false }), "unavailable");
  assert.equal(ledSummary({ configured: true, enabled: true, available: true, connection: "CONNECTED" }), "connected");
  assert.equal(ledSummary({ configured: true, enabled: true, available: true, connection: "LISTENING" }), "listening");
});

test("isLedConfigured accepts explicit and legacy payloads", () => {
  assert.equal(isLedConfigured(null), false);
  assert.equal(isLedConfigured({ configured: false }), false);
  assert.equal(isLedConfigured({ configured: true, enabled: false }), true);
  assert.equal(isLedConfigured({ enabled: true, connection: "CONNECTED" }), true);
  assert.equal(isLedConfigured({ mode: "all", enabled: false }), true);
  assert.equal(isLedConfigured({ available: false, enabled: false }), false);
});

test("ledStatusTone reflects the distinct states", () => {
  assert.equal(ledStatusTone(null), "bad");
  assert.equal(ledStatusTone({ configured: false }), "bad");
  assert.equal(ledStatusTone({ configured: true, enabled: false }), "warn");
  assert.equal(ledStatusTone({ configured: true, enabled: true, available: true, connection: "CONNECTED" }), "ok");
  assert.equal(ledStatusTone({ configured: true, enabled: true, available: true, connection: "LISTENING" }), "warn");
  assert.equal(ledStatusTone({ configured: true, enabled: true, available: false, connection: "UNAVAILABLE" }), "bad");
});

test("P014-HF1: the enable toggle survives disable; operating controls need availability", () => {
  // Not configured at all: nothing is offered.
  assert.deepEqual(ledControlEnablement(null), {
    toggle: false, mode: false, brightness: false, clear: false,
  });
  assert.deepEqual(ledControlEnablement({ configured: false, available: false, enabled: false }), {
    toggle: false, mode: false, brightness: false, clear: false,
  });
  // REGRESSION (real phone bug): configured but user-disabled must keep
  // the toggle reachable so LEDs can be re-enabled from the UI.
  assert.deepEqual(ledControlEnablement({ configured: true, enabled: false, available: false }), {
    toggle: true, mode: false, brightness: false, clear: false,
  });
  // Configured, enabled, but transport failed: still re-toggleable.
  assert.deepEqual(ledControlEnablement({ configured: true, enabled: true, available: false }), {
    toggle: true, mode: false, brightness: false, clear: false,
  });
  // Fully available: everything offered.
  assert.deepEqual(ledControlEnablement({ configured: true, enabled: true, available: true }), {
    toggle: true, mode: true, brightness: true, clear: true,
  });
});


// ---- guided practice (PIANO-P015) -------------------------------------

test("practiceTargetText joins chord names and handles missing targets", () => {
  assert.equal(practiceTargetText({ names: ["C4"] }), "C4");
  assert.equal(practiceTargetText({ names: ["C4", "E4", "G4"] }), "C4 + E4 + G4");
  assert.equal(practiceTargetText({ names: [] }), "");
  assert.equal(practiceTargetText(null), "");
  assert.equal(practiceTargetText(undefined), "");
  assert.equal(practiceTargetText({}), "");
});

test("practiceSummary and practiceStatusTone cover every state", () => {
  assert.equal(practiceSummary(null), "unavailable");
  assert.equal(practiceSummary({ state: "IDLE" }), "idle");
  assert.equal(practiceSummary({ state: "WAITING" }), "waiting");
  assert.equal(practiceSummary({ state: "COMPLETED" }), "completed");
  assert.equal(practiceSummary({ state: "FAILED" }), "failed");
  assert.equal(practiceStatusTone(null), "bad");
  assert.equal(practiceStatusTone({ state: "IDLE" }), "warn");
  assert.equal(practiceStatusTone({ state: "WAITING" }), "ok");
  assert.equal(practiceStatusTone({ state: "COMPLETED" }), "ok");
  assert.equal(practiceStatusTone({ state: "FAILED" }), "bad");
});

test("practiceView renders waiting state (step, target, progress)", () => {
  const view = practiceView({
    state: "WAITING",
    step_index: 13,
    total_steps: 820,
    progress: 14 / 820,
    target: { names: ["C4"] },
    midi_connection: "CONNECTED",
  });
  assert.equal(view.active, true);
  assert.equal(view.showPanel, true);
  assert.equal(view.stepText, "Step 14 / 820");
  assert.equal(view.targetText, "C4");
  assert.equal(view.progressPct, Math.round((14 / 820) * 100));
  assert.equal(view.completionText, "");
  assert.equal(view.errorText, "");
  assert.equal(view.midiDegraded, false);
});

test("practiceView renders chord targets", () => {
  const view = practiceView({
    state: "WAITING",
    step_index: 3,
    total_steps: 10,
    target: { names: ["C4", "E4", "G4"], chord: true },
  });
  assert.equal(view.targetText, "C4 + E4 + G4");
});

test("practiceView renders completed and failed outcomes", () => {
  const done = practiceView({ state: "COMPLETED", total_steps: 3, progress: 1 });
  assert.equal(done.active, false);
  assert.equal(done.showPanel, true);
  assert.equal(done.completionText, "All 3 steps played.");
  const failed = practiceView({ state: "FAILED", error: "internal practice error" });
  assert.equal(failed.showPanel, true);
  assert.equal(failed.errorText, "internal practice error");
  const idle = practiceView({ state: "IDLE" });
  assert.equal(idle.showPanel, false);
  assert.equal(practiceView(null).showPanel, false);
});

test("practiceView flags degraded MIDI input while waiting", () => {
  const degraded = practiceView({
    state: "WAITING",
    step_index: 0,
    total_steps: 2,
    midi_connection: "DISCONNECTED",
  });
  assert.equal(degraded.midiDegraded, true);
  const healthy = practiceView({
    state: "WAITING",
    step_index: 0,
    total_steps: 2,
    midi_connection: "CONNECTED",
  });
  assert.equal(healthy.midiDegraded, false);
});

test("practice and playback are mutually exclusive conflicts", () => {
  assert.equal(practiceConflictWithPlayback({ available: true, state: "PLAYING" }), true);
  assert.equal(practiceConflictWithPlayback({ available: true, state: "PAUSED" }), true);
  assert.equal(practiceConflictWithPlayback({ available: true, state: "STOPPING" }), true);
  assert.equal(practiceConflictWithPlayback({ available: true, state: "IDLE" }), false);
  assert.equal(practiceConflictWithPlayback({ available: true, state: "COMPLETED" }), false);
  assert.equal(practiceConflictWithPlayback({ available: false, state: "PLAYING" }), false);
  assert.equal(practiceConflictWithPlayback(null), false);
});

test("practiceControlEnablement gates start/stop deterministically", () => {
  const waiting = { available: true, state: "WAITING" };
  const idle = { available: true, state: "IDLE" };
  const completed = { available: true, state: "COMPLETED" };
  const unavailable = { available: false, state: "IDLE" };
  // Waiting: stop offered, start refused (session already active).
  assert.deepEqual(practiceControlEnablement(waiting, null), { start: false, stop: true });
  // Idle + no playback: start offered.
  assert.deepEqual(practiceControlEnablement(idle, null), { start: true, stop: false });
  // Idle + playback active: start blocked (409 by design).
  assert.deepEqual(
    practiceControlEnablement(idle, { available: true, state: "PLAYING" }),
    { start: false, stop: false }
  );
  // Completed: a new session may start.
  assert.deepEqual(practiceControlEnablement(completed, null), { start: true, stop: false });
  // Library unavailable: no start.
  assert.deepEqual(practiceControlEnablement(unavailable, null), { start: false, stop: false });
});

test("practice error codes map to human messages", () => {
  assert.match(errorText("practice_unavailable"), /unavailable/);
  assert.match(errorText("practice_already_active"), /already running/);
  assert.match(errorText("activity_conflict"), /cannot run together/);
});

test("Learning V2 exposes only implemented Core modes", () => {
  assert.deepEqual(LEARNING_MODES, [
    "WAIT_FOR_NOTE",
    "SECTION_PRACTICE",
    "REPEAT_UNTIL_CORRECT",
    "FLOW",
  ]);
  assert.equal(LEARNING_MODES.includes("FOLLOW"), false);
  assert.equal(LEARNING_MODES.includes("LEFT_HAND"), false);
});

test("learning start config validates time section and repetition", () => {
  assert.deepEqual(
    learningStartConfig({
      songId: "song-1",
      mode: "SECTION_PRACTICE",
      sectionStart: "2.5",
      sectionEnd: "8",
    }),
    {
      song_id: "song-1",
      mode: "SECTION_PRACTICE",
      section: { start_seconds: 2.5, end_seconds: 8 },
    }
  );
  assert.deepEqual(
    learningStartConfig({
      songId: "song-1",
      mode: "REPEAT_UNTIL_CORRECT",
      requiredRepetitions: "3",
    }),
    {
      song_id: "song-1",
      mode: "REPEAT_UNTIL_CORRECT",
      required_repetitions: 3,
    }
  );
  assert.throws(
    () => learningStartConfig({ songId: "s", mode: "SECTION_PRACTICE", sectionStart: 2, sectionEnd: 2 }),
    TypeError
  );
  assert.throws(
    () => learningStartConfig({ songId: "s", mode: "REPEAT_UNTIL_CORRECT", requiredRepetitions: 17 }),
    TypeError
  );
});

test("practiceView presents authoritative learning mode, section, repetition and outcome", () => {
  const view = practiceView({
    state: "WAITING",
    mode: "REPEAT_UNTIL_CORRECT",
    section: { start_seconds: 1, end_seconds: 5.5 },
    step_index: 0,
    total_steps: 2,
    progress: 0,
    target: { names: ["C4"] },
    repetition: { current: 2, required: 3, successful: 4, failed: 1 },
    latest_outcome: "INCORRECT",
    midi_connection: "CONNECTED",
  });
  assert.equal(view.modeText, "REPEAT UNTIL CORRECT");
  assert.equal(view.sectionText, "1.0s–5.5s");
  assert.match(view.repetitionText, /2 \/ 3/);
  assert.match(view.feedbackText, /Incorrect/);
});

test("ApiClient exposes the bounded practice surface", async () => {
  const calls = [];
  const fakeFetch = async (path, options) => {
    calls.push({ path, options });
    return new Response(JSON.stringify({ state: "IDLE" }), { status: 200 });
  };
  const client = new ApiClient(fakeFetch);
  await client.practice();
  await client.practiceStart("song-1");
  await client.practiceStop();
  assert.deepEqual(
    calls.map((call) => `${call.options.method} ${call.path}`),
    [
      "GET /api/v1/practice",
      "POST /api/v1/practice/start",
      "POST /api/v1/practice/stop",
    ]
  );
  assert.deepEqual(JSON.parse(calls[1].options.body), { song_id: "song-1" });
  assert.deepEqual(JSON.parse(calls[2].options.body), {});
});

test("ApiClient exposes guarded Learning V2 commands", async () => {
  const calls = [];
  const fakeFetch = async (path, options) => {
    calls.push({ path, options });
    return new Response(JSON.stringify({ state: "WAITING" }), { status: 200 });
  };
  const client = new ApiClient(fakeFetch);
  const session = { session_id: "session-1", version: 7 };
  await client.learning();
  await client.learningStart({ song_id: "song-1", mode: "WAIT_FOR_NOTE" });
  await client.learningRestartTarget(session);
  await client.learningRestartSection(session);
  await client.learningStop(session);
  assert.deepEqual(
    calls.map((call) => `${call.options.method} ${call.path}`),
    [
      "GET /api/v1/learning",
      "POST /api/v1/learning/start",
      "POST /api/v1/learning/restart-target",
      "POST /api/v1/learning/restart-section",
      "POST /api/v1/learning/stop",
    ]
  );
  assert.deepEqual(JSON.parse(calls[2].options.body), {
    expected_session_id: "session-1",
    expected_version: 7,
  });
});

// ---- playback payload classifier (PIANO-UI-003 playback contract) -------

const canonicalAvailable = (overrides = {}) => ({
  available: true,
  state: "PLAYING",
  song_id: "song-1",
  title: "Song 1",
  position_seconds: 12,
  duration_seconds: 60,
  tempo_multiplier: 1,
  events_sent: 0,
  error: null,
  ...overrides,
});
// Exact live proof from piano_core.api.serialization.playback_session_payload
// (empty timeline): Core legitimately publishes COMPLETED at 0/0.
const coreZeroDurationCompleted = canonicalAvailable({
  state: "COMPLETED",
  position_seconds: 0.0,
  duration_seconds: 0.0,
  song_id: "eb928e5d-2215-5dd6-b582-612c8d79cd26",
  title: "a.mid",
});
// Exact live proof of legitimate authoritative unavailable truth
// (playback_session_payload after MidiOutputAdapter.close()).
const coreUnavailable = {
  available: false,
  state: "COMPLETED",
  tempo_multiplier: 1.0,
  position_seconds: 0.0,
  duration_seconds: 0.0,
  events_sent: 0,
  error: null,
  song_id: null,
  title: null,
};
// Exact playback_session_payload(None) shape (manager absent).
const coreManagerAbsent = {
  available: false,
  state: null,
  tempo_multiplier: null,
  position_seconds: null,
  duration_seconds: null,
  events_sent: null,
  error: null,
  song_id: null,
  title: null,
};

test("classifier accepts canonical available payloads", () => {
  assert.equal(classifyPlaybackPayload(canonicalAvailable()), PLAYBACK_PAYLOAD_CLASS.VALID_AVAILABLE);
  assert.equal(isUsablePlaybackSnapshot(canonicalAvailable()), true);
  assert.equal(isValidPlaybackResponse(canonicalAvailable()), true);
});

test("zero-duration COMPLETED 0/0 is valid authoritative truth", () => {
  assert.equal(classifyPlaybackPayload(coreZeroDurationCompleted), PLAYBACK_PAYLOAD_CLASS.VALID_AVAILABLE);
  assert.equal(classifyPlaybackPayload(
    canonicalAvailable({ state: "COMPLETED", position_seconds: 60, duration_seconds: 60 })
  ), PLAYBACK_PAYLOAD_CLASS.VALID_AVAILABLE);
  // Core cannot emit COMPLETED materially below duration.
  assert.equal(classifyPlaybackPayload(
    canonicalAvailable({ state: "COMPLETED", position_seconds: 10, duration_seconds: 60 })
  ), PLAYBACK_PAYLOAD_CLASS.INVALID);
  assert.equal(isUsablePlaybackSnapshot(
    canonicalAvailable({ state: "COMPLETED", position_seconds: 10, duration_seconds: 60 })
  ), false);
});

test("available:false is valid unavailable truth, distinct from invalid", () => {
  assert.equal(classifyPlaybackPayload(coreUnavailable), PLAYBACK_PAYLOAD_CLASS.VALID_UNAVAILABLE);
  assert.equal(classifyPlaybackPayload(coreManagerAbsent), PLAYBACK_PAYLOAD_CLASS.VALID_UNAVAILABLE);
  assert.equal(isValidPlaybackResponse(coreUnavailable), true);
  // Unavailable truth is NOT interpolation/transport authority.
  assert.equal(isUsablePlaybackSnapshot(coreUnavailable), false);
  // An unavailable payload with a non-canonical state is invalid.
  assert.equal(classifyPlaybackPayload({ ...coreUnavailable, state: "GARBAGE" }), PLAYBACK_PAYLOAD_CLASS.INVALID);
});

test("numeric fields reject non-number JSON types without coercion", () => {
  const invalidNumerics = [null, true, false, [], ["1"], "12", "1", {}, { value: 1 }];
  for (const field of ["position_seconds", "duration_seconds", "tempo_multiplier"]) {
    for (const value of invalidNumerics) {
      const payload = canonicalAvailable({ [field]: value });
      assert.equal(
        classifyPlaybackPayload(payload),
        PLAYBACK_PAYLOAD_CLASS.INVALID,
        `${field} = ${JSON.stringify(value)} must be invalid`
      );
      assert.equal(isUsablePlaybackSnapshot(payload), false);
    }
  }
});

test("position/duration domain bounds reject negative and Infinity values", () => {
  for (const value of [-1, -0.001, Infinity, -Infinity, NaN]) {
    assert.equal(
      classifyPlaybackPayload(canonicalAvailable({ position_seconds: value })),
      PLAYBACK_PAYLOAD_CLASS.INVALID,
      `position ${value} must be invalid`
    );
    assert.equal(
      classifyPlaybackPayload(canonicalAvailable({ duration_seconds: value })),
      PLAYBACK_PAYLOAD_CLASS.INVALID,
      `duration ${value} must be invalid`
    );
  }
  assert.equal(
    classifyPlaybackPayload(canonicalAvailable({ position_seconds: 61 })),
    PLAYBACK_PAYLOAD_CLASS.INVALID,
    "position beyond duration must be invalid"
  );
});

test("tempo obeys the Core 0.25..4.0 contract without clamping", () => {
  assert.equal(PLAYBACK_TEMPO_MIN, 0.25);
  assert.equal(PLAYBACK_TEMPO_MAX, 4.0);
  for (const value of [null, true, "1", 0, 0.1, 100, Infinity, NaN, []]) {
    assert.equal(
      classifyPlaybackPayload(canonicalAvailable({ tempo_multiplier: value })),
      PLAYBACK_PAYLOAD_CLASS.INVALID,
      `tempo ${JSON.stringify(value)} must be invalid`
    );
  }
  assert.equal(classifyPlaybackPayload(canonicalAvailable({ tempo_multiplier: 0.25 })), PLAYBACK_PAYLOAD_CLASS.VALID_AVAILABLE);
  assert.equal(classifyPlaybackPayload(canonicalAvailable({ tempo_multiplier: 4.0 })), PLAYBACK_PAYLOAD_CLASS.VALID_AVAILABLE);
});

test("events_sent is strict: booleans, strings, fractions and negatives are invalid", () => {
  for (const value of [true, false, "1", 1.5, -1, {}]) {
    assert.equal(
      classifyPlaybackPayload(canonicalAvailable({ events_sent: value })),
      PLAYBACK_PAYLOAD_CLASS.INVALID,
      `events_sent ${JSON.stringify(value)} must be invalid`
    );
  }
  assert.equal(classifyPlaybackPayload(canonicalAvailable({ events_sent: 0 })), PLAYBACK_PAYLOAD_CLASS.VALID_AVAILABLE);
  assert.equal(classifyPlaybackPayload(canonicalAvailable({ events_sent: 100 })), PLAYBACK_PAYLOAD_CLASS.VALID_AVAILABLE);
  assert.equal(classifyPlaybackPayload(coreManagerAbsent), PLAYBACK_PAYLOAD_CLASS.VALID_UNAVAILABLE);
});

test("identity and flag field types are strict", () => {
  assert.equal(classifyPlaybackPayload(canonicalAvailable({ song_id: 42 })), PLAYBACK_PAYLOAD_CLASS.INVALID);
  assert.equal(classifyPlaybackPayload(canonicalAvailable({ title: [] })), PLAYBACK_PAYLOAD_CLASS.INVALID);
  assert.equal(classifyPlaybackPayload(canonicalAvailable({ error: 7 })), PLAYBACK_PAYLOAD_CLASS.INVALID);
  assert.equal(classifyPlaybackPayload(canonicalAvailable({ available: "true" })), PLAYBACK_PAYLOAD_CLASS.INVALID);
  assert.equal(classifyPlaybackPayload(canonicalAvailable({ available: 1 })), PLAYBACK_PAYLOAD_CLASS.INVALID);
  assert.equal(classifyPlaybackPayload(null), PLAYBACK_PAYLOAD_CLASS.INVALID);
  assert.equal(classifyPlaybackPayload([]), PLAYBACK_PAYLOAD_CLASS.INVALID);
  assert.equal(classifyPlaybackPayload("PAUSED"), PLAYBACK_PAYLOAD_CLASS.INVALID);
});

// ---- Learning presentation authority (PIANO-UI-004 U4-F3) ----------------

const learningIdle = (overrides = {}) => ({
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
  accepted_notes: [],
  session_id: null,
  mode: "WAIT_FOR_NOTE",
  latest_outcome: "NONE",
  generation: 0,
  version: 1,
  ...overrides,
});

const learningWaiting = (overrides = {}) =>
  learningIdle({
    state: "WAITING",
    song_id: "song-1",
    title: "Song 1",
    step_index: 0,
    total_steps: 4,
    target: { notes: [60, 64], names: ["C4", "E4"], chord: true, target_id: "target:0:60-64" },
    session_id: "session-1",
    version: 3,
    ...overrides,
  });

function makeCoordinator() {
  let nowMs = 1000;
  const changes = [];
  const coordinator = new LearningCoordinator({
    now: () => nowMs,
    onChange: (replica) => changes.push(replica.status),
  });
  return {
    coordinator,
    changes,
    advance: (ms) => {
      nowMs += ms;
    },
    now: () => nowMs,
  };
}

test("learning snapshot validator accepts canonical payloads and rejects malformed ones", () => {
  assert.equal(isValidLearningSnapshot(learningIdle()), true);
  assert.equal(isValidLearningSnapshot(learningWaiting()), true);
  assert.equal(
    isValidLearningSnapshot(learningIdle({ available: false, state: null })),
    true
  );
  assert.equal(isValidLearningSnapshot(null), false);
  assert.equal(isValidLearningSnapshot([]), false);
  assert.equal(isValidLearningSnapshot(learningIdle({ version: -1 })), false);
  assert.equal(isValidLearningSnapshot(learningIdle({ version: "3" })), false);
  assert.equal(isValidLearningSnapshot(learningIdle({ accepted_notes: 5 })), false);
  assert.equal(isValidLearningSnapshot(learningIdle({ state: "FOLLOW" })), false);
  assert.equal(
    isValidLearningSnapshot(learningIdle({ available: false, state: "IDLE" })),
    false
  );
  assert.equal(isValidLearningSnapshot(learningIdle({ available: "yes" })), false);
  assert.deepEqual(LEARNING_STATES, ["IDLE", "WAITING", "COMPLETED", "FAILED"]);
});

test("old Learning poll after Stop cannot resurrect the stopped session", () => {
  const { coordinator } = makeCoordinator();
  coordinator.acceptPoll(learningWaiting(), coordinator.beginPoll());
  assert.equal(coordinator.authorized, true);
  // Stop command accepted: ordering boundary.
  const commandTicket = coordinator.beginCommand();
  coordinator.acceptCommandResult(learningIdle({ version: 4 }), commandTicket);
  assert.equal(coordinator.snapshot.state, "IDLE");
  // A Learning GET that started BEFORE the Stop returns afterwards.
  const preCommandPoll = { kind: "poll", generation: 0, sequence: 1 };
  assert.equal(coordinator.acceptPoll(learningWaiting({ version: 3 }), preCommandPoll), false);
  assert.equal(coordinator.snapshot.state, "IDLE");
  assert.equal(coordinator.authorized, true);
});

test("old inactive Learning poll after Start cannot hide the new session", () => {
  const { coordinator } = makeCoordinator();
  coordinator.acceptPoll(learningIdle(), coordinator.beginPoll());
  const commandTicket = coordinator.beginCommand();
  coordinator.acceptCommandResult(learningWaiting({ version: 1 }), commandTicket);
  const preCommandPoll = { kind: "poll", generation: 0, sequence: 1 };
  assert.equal(coordinator.acceptPoll(learningIdle(), preCommandPoll), false);
  assert.equal(coordinator.snapshot.state, "WAITING");
});

test("old Learning poll after restart-target cannot undo the restart", () => {
  const { coordinator } = makeCoordinator();
  coordinator.acceptPoll(learningWaiting({ accepted_notes: [60] }), coordinator.beginPoll());
  const commandTicket = coordinator.beginCommand();
  coordinator.acceptCommandResult(
    learningWaiting({ accepted_notes: [], version: 4, latest_outcome: "NONE" }),
    commandTicket
  );
  const preCommandPoll = { kind: "poll", generation: 0, sequence: 1 };
  assert.equal(
    coordinator.acceptPoll(learningWaiting({ accepted_notes: [60] }), preCommandPoll),
    false
  );
  assert.deepEqual(coordinator.snapshot.accepted_notes, []);
});

test("current Learning polls apply and a current failure only ages freshness", () => {
  const { coordinator, advance } = makeCoordinator();
  const ticketA = coordinator.beginPoll();
  assert.equal(coordinator.acceptPoll(learningWaiting(), ticketA), true);
  const ticketB = coordinator.beginPoll();
  assert.equal(coordinator.notePollFailure(ticketB), true);
  // Within the window the replica stays authorized.
  assert.equal(coordinator.evaluateFreshness(), "fresh");
  advance(1000);
  assert.equal(coordinator.evaluateFreshness(), "fresh");
  // A stale-generation failure is ignored outright.
  const staleTicket = { kind: "poll", generation: 99, sequence: 999 };
  assert.equal(coordinator.notePollFailure(staleTicket), false);
  // Beyond the bounded freshness threshold the replica expires.
  advance(LEARNING_FRESHNESS_MS);
  assert.equal(coordinator.evaluateFreshness(), "stale");
  assert.equal(coordinator.authorized, false);
});

test("repeated Learning 500s expire presentation while recovery restores it", () => {
  const { coordinator, advance, changes } = makeCoordinator();
  coordinator.acceptPoll(learningWaiting(), coordinator.beginPoll());
  assert.equal(coordinator.authorized, true);
  // Five consecutive Learning GET failures (1 s polling) cross the
  // bounded freshness window; earlier misses stay authorized.
  for (let index = 0; index < 4; index += 1) {
    advance(1000);
    assert.equal(coordinator.notePollFailure(coordinator.beginPoll()), true);
    assert.equal(coordinator.evaluateFreshness(), "fresh");
  }
  advance(1000);
  coordinator.notePollFailure(coordinator.beginPoll());
  assert.equal(coordinator.evaluateFreshness(), "stale");
  assert.equal(coordinator.authorized, false);
  assert.ok(changes.includes("stale"));
  // Recovery: one valid snapshot replaces the stale replica and
  // restores the current session/target authority.
  coordinator.acceptPoll(
    learningWaiting({ version: 9, step_index: 2 }),
    coordinator.beginPoll()
  );
  assert.equal(coordinator.status, "fresh");
  assert.equal(coordinator.authorized, true);
  assert.equal(coordinator.snapshot.step_index, 2);
});

test("invalid Learning payloads never replace the replica", () => {
  const { coordinator } = makeCoordinator();
  const ticket = coordinator.beginPoll();
  coordinator.acceptPoll(learningWaiting(), ticket);
  const current = coordinator.snapshot;
  assert.equal(coordinator.acceptPoll({ state: "WAITING" }, coordinator.beginPoll()), false);
  assert.equal(coordinator.acceptPoll(null, coordinator.beginPoll()), false);
  assert.equal(coordinator.snapshot, current);
});

test("page reload rebuilds the Learning replica from one authoritative snapshot", () => {
  // Reload with an ACTIVE session: fresh authority, mutating controls
  // may be offered again by presentation.
  const active = makeCoordinator();
  active.coordinator.acceptPoll(learningWaiting(), active.coordinator.beginPoll());
  assert.equal(active.coordinator.authorized, true);
  assert.equal(active.coordinator.snapshot.session_id, "session-1");
  // Reload with an INACTIVE session: authorized truth, nothing active.
  const inactive = makeCoordinator();
  inactive.coordinator.acceptPoll(learningIdle(), inactive.coordinator.beginPoll());
  assert.equal(inactive.coordinator.authorized, true);
  assert.equal(inactive.coordinator.snapshot.state, "IDLE");
});

test("a newer command boundary supersedes an in-flight command result", () => {
  const { coordinator } = makeCoordinator();
  coordinator.acceptPoll(learningIdle(), coordinator.beginPoll());
  const first = coordinator.beginCommand();
  const second = coordinator.beginCommand();
  // The second command's result lands first.
  coordinator.acceptCommandResult(learningWaiting({ session_id: "s-2" }), second);
  assert.equal(coordinator.snapshot.session_id, "s-2");
  // The first command's late result must not overwrite newer truth.
  assert.equal(
    coordinator.acceptCommandResult(learningWaiting({ session_id: "s-1" }), first),
    false
  );
  assert.equal(coordinator.snapshot.session_id, "s-2");
});

console.log(process.exitCode ? "LOGIC TESTS FAILED" : "ALL LOGIC TESTS PASSED");
