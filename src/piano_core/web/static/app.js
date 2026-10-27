/* Piano Core appliance UI (PIANO-P013).
 *
 * Browser-native ES module — no build step, no dependencies, no CDN.
 * The UI is a CLIENT of the /api/v1 control API only: it holds no
 * second truth store and never reimplements backend logic. Canonical
 * backend state is authoritative; after every command the UI
 * reconciles with the API snapshot.
 *
 * Structure:
 *   - PURE logic functions (exported; unit-tested with node)
 *   - ApiClient: the single fetch boundary (same-origin, /api/v1)
 *   - UI wiring (DOM rendering, polling, search, control actions)
 *
 * Security contract: all server-derived strings (song filenames,
 * titles, error messages) are rendered with textContent/createElement
 * ONLY — raw HTML markup construction is never used for server data.
 * No eval, no dynamic script injection, no hardcoded hostnames: every
 * request is same-origin relative.
 */

import { VisualStage } from "./visualizer.js";
import { createMotionView, evaluate as evaluateMotion } from "./handmotion.js";
import {
  CAMERA_MODES as HANDS_CAMERA_MODES,
  HandRenderer,
  QUALITY_MODES as HANDS_QUALITY_MODES,
  RENDERER_STATUS as HANDS_RENDERER_STATUS,
  RendererUnavailableError,
} from "./handrender.js";
import { initializePremiumShell } from "./ui008/shell.js";
import { createLayerController } from "./ui008/layers.js";
import { createLibraryController } from "./ui008/library.js";
import { createHomeController, createRecentsStore } from "./ui008/home.js";
import {
  createPerformanceController,
  createSecondaryPlacement,
  ledTogglePayload,
} from "./ui008/player.js";
import { createStudioController, focusRange, flowVisualPosition } from "./ui008/studio.js";
import { learnLessonModel } from "./ui008/learn.js";
import {
  createLearnLibraryController,
  LEARN_LIBRARY_FEATURES,
} from "./ui008/learnlibrary.js";
import {
  createWorkspaceController,
  rendererModeForView,
} from "./ui008/workspace.js";
import {
  pianoActivityModel,
  pianoConnectionModel,
  pianoIdentityModel,
  pianoLedModel,
  pianoTechRows,
} from "./ui008/piano.js";
import { settingsModel } from "./ui008/settings.js";

const API_BASE = "/api/v1";

/* --------------------------------------------------------------------
 * Pure logic (exported for deterministic node tests)
 * -------------------------------------------------------------------- */

export const TEMPO_MIN = 0.25;
export const TEMPO_MAX = 4.0;
export const PLAYBACK_FRESHNESS_MS = 3500;
export const PLAYBACK_DRIFT_TOLERANCE_SECONDS = 0.35;
export const PLAYBACK_DRIFT_CORRECTION_MS = 2000;

/** Quantize to 2 decimals and clamp to the canonical 0.25..4.0 range. */
export function clampTempo(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 1.0;
  const quantized = Math.round(n * 100) / 100;
  return Math.min(TEMPO_MAX, Math.max(TEMPO_MIN, quantized));
}

export function formatTempo(value) {
  return `${clampTempo(value).toFixed(2)}×`;
}

/** Seconds -> "m:ss" / "h:mm:ss"; null/undefined -> "—". */
export function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) {
    return "—";
  }
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = h > 0 ? String(m).padStart(2, "0") : String(m);
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

const NOTE_NAMES = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"];

/** MIDI note number -> name (e.g. 60 -> "C4"); out of range -> "#n". */
export function noteName(note) {
  const n = Number(note);
  if (!Number.isInteger(n) || n < 0 || n > 127) return `#${note}`;
  return `${NOTE_NAMES[n % 12]}${Math.floor(n / 12) - 1}`;
}

/* --------------------------------------------------------------------
 * Live 88-key piano (PIANO-UI-003; pure model/reducer + DOM adapter)
 * -------------------------------------------------------------------- */

export const PIANO_NOTE_MIN = 21;
export const PIANO_NOTE_MAX = 108;
export const PIANO_KEY_COUNT = 88;
export const PIANO_WHITE_KEY_COUNT = 52;
export const LIVE_NOTE_SCHEMA_VERSION = 1;
export const LIVE_NOTE_RESNAPSHOT_MS = 2000;
export const LIVE_NOTE_FRESHNESS_MS = 5000;
const BLACK_PITCH_CLASSES = new Set([1, 3, 6, 8, 10]);

/** Build the one canonical A0..C8 display model. */
export function buildPianoKeyModel() {
  const keys = [];
  let whiteIndex = 0;
  for (let midi = PIANO_NOTE_MIN; midi <= PIANO_NOTE_MAX; midi += 1) {
    const pitchClassIndex = midi % 12;
    const black = BLACK_PITCH_CLASSES.has(pitchClassIndex);
    const key = {
      id: `midi-${midi}`,
      midi,
      pitchClass: NOTE_NAMES[pitchClassIndex],
      octave: Math.floor(midi / 12) - 1,
      label: noteName(midi),
      black,
      whiteIndex: black ? null : whiteIndex,
      // Black keys are centered on the boundary after the preceding white.
      leftPercent: black ? (whiteIndex / PIANO_WHITE_KEY_COUNT) * 100 : null,
    };
    keys.push(Object.freeze(key));
    if (!black) whiteIndex += 1;
  }
  if (keys.length !== PIANO_KEY_COUNT || whiteIndex !== PIANO_WHITE_KEY_COUNT) {
    throw new Error("invalid canonical piano geometry");
  }
  return Object.freeze(keys);
}

export const PIANO_KEYS = buildPianoKeyModel();

function boundedNoteSet(values) {
  const notes = new Set();
  if (!Array.isArray(values)) return notes;
  for (const value of values) {
    const note = Number(value);
    if (Number.isInteger(note) && note >= PIANO_NOTE_MIN && note <= PIANO_NOTE_MAX) {
      notes.add(note);
    }
  }
  return notes;
}

/** Derive presentation source per active key from one full Core snapshot. */
export function activeKeySources(snapshot) {
  const result = new Map();
  if (!snapshot || !snapshot.physical || !snapshot.playback) return result;
  const physical = boundedNoteSet(snapshot.physical.notes);
  const playback = boundedNoteSet(snapshot.playback.notes);
  for (const note of physical) result.set(note, "physical");
  for (const note of playback) {
    result.set(note, result.has(note) ? "both" : "playback");
  }
  return result;
}

/** Validate the stable public envelope before it can replace the replica. */
export function isValidLiveNoteSnapshot(snapshot) {
  return !!snapshot &&
    snapshot.schema_version === LIVE_NOTE_SCHEMA_VERSION &&
    typeof snapshot.runtime_epoch === "string" && snapshot.runtime_epoch.length > 0 &&
    Number.isInteger(snapshot.sequence) && snapshot.sequence >= 0 &&
    !!snapshot.physical && Array.isArray(snapshot.physical.notes) &&
    !!snapshot.playback && Array.isArray(snapshot.playback.notes);
}

/** Ordered, generation-aware client replica. It owns presentation only. */
export class LiveNoteReplica {
  constructor() {
    this.generation = 0;
    this.snapshot = null;
    this.status = "disconnected";
  }

  beginGeneration() {
    this.generation += 1;
    this.status = "synchronizing";
    return this.generation;
  }

  accept(snapshot, generation) {
    if (generation !== this.generation || !isValidLiveNoteSnapshot(snapshot)) {
      return false;
    }
    if (
      this.snapshot &&
      this.snapshot.runtime_epoch === snapshot.runtime_epoch &&
      snapshot.sequence < this.snapshot.sequence
    ) {
      return false;
    }
    this.snapshot = snapshot;
    this.status = "live";
    return true;
  }

  markStale(generation) {
    if (generation !== this.generation) return false;
    this.status = "stale";
    return true;
  }
}

/** Efficient DOM renderer: create 88 nodes once, then touch changed keys only. */
export class PianoKeyboardRenderer {
  constructor(root, summary) {
    if (!root || !summary) throw new TypeError("keyboard root and summary are required");
    this.root = root;
    this.summary = summary;
    this.elements = new Map();
    this.sources = new Map();
    this.expected = new Set();
    const whiteLayer = document.createElement("div");
    whiteLayer.className = "piano-white-keys";
    const blackLayer = document.createElement("div");
    blackLayer.className = "piano-black-keys";
    for (const key of PIANO_KEYS) {
      const element = document.createElement("div");
      element.className = `piano-key piano-key--${key.black ? "black" : "white"}`;
      element.dataset.midi = String(key.midi);
      element.dataset.source = "idle";
      element.dataset.expected = "false";
      element.setAttribute("aria-hidden", "true");
      element.title = key.label;
      if (key.black) {
        element.style.left = `${key.leftPercent}%`;
        blackLayer.append(element);
      } else {
        if (key.label === "A0" || key.pitchClass === "C") {
          const label = document.createElement("span");
          label.className = "piano-key-label";
          label.textContent = key.label;
          element.append(label);
        }
        whiteLayer.append(element);
      }
      this.elements.set(key.midi, element);
    }
    root.replaceChildren(whiteLayer, blackLayer);
    this.render(null, "synchronizing");
  }

  render(snapshot, status = "live") {
    const next = status === "live" ? activeKeySources(snapshot) : new Map();
    const touched = new Set([...this.sources.keys(), ...next.keys()]);
    let updates = 0;
    for (const note of touched) {
      const source = next.get(note) || "idle";
      if ((this.sources.get(note) || "idle") === source) continue;
      const element = this.elements.get(note);
      if (element) {
        element.dataset.source = source;
        updates += 1;
      }
    }
    this.sources = next;
    this.root.dataset.state = status;
    if (isValidLiveNoteSnapshot(snapshot)) {
      this.root.dataset.appliedSequence = String(snapshot.sequence);
      this.root.dataset.runtimeEpoch = snapshot.runtime_epoch;
    }
    const counts = { physical: 0, playback: 0, both: 0 };
    for (const source of next.values()) counts[source] += 1;
    const total = next.size;
    const statusText = status === "live"
      ? total === 0
        ? "No keys lit — play something."
        : `${total} key${total === 1 ? "" : "s"} lit · ${counts.physical + counts.both} by you · ${counts.playback + counts.both} by the song`
      : status === "synchronizing"
        ? "Keeping in sync with your piano…"
        : "Live key view is unavailable right now — highlights cleared";
    this.summary.textContent = statusText;
    this.root.setAttribute("aria-label", `88-key piano, ${statusText}`);
    return updates;
  }

  /** Apply Core Learning expected-note truth without changing grading. */
  renderExpected(notes) {
    const next = boundedNoteSet(notes);
    const touched = new Set([...this.expected, ...next]);
    let updates = 0;
    for (const note of touched) {
      const expected = next.has(note);
      if (this.expected.has(note) === expected) continue;
      const element = this.elements.get(note);
      if (element) {
        element.dataset.expected = expected ? "true" : "false";
        updates += 1;
      }
    }
    this.expected = next;
    return updates;
  }
}

/** One EventSource object is one client connection generation. */
export class LiveNoteCoordinator {
  constructor({
    replica = new LiveNoteReplica(),
    eventSourceFactory = (url) => new EventSource(url),
    snapshotLoader = null,
    onChange = () => {},
    reconnectMs = 1000,
    resnapshotMs = LIVE_NOTE_RESNAPSHOT_MS,
    freshnessMs = LIVE_NOTE_FRESHNESS_MS,
    now = () => (globalThis.performance ? performance.now() : Date.now()),
    setTimer = (callback, delay) => setTimeout(callback, delay),
    clearTimer = (timer) => clearTimeout(timer),
  } = {}) {
    this.replica = replica;
    this._eventSourceFactory = eventSourceFactory;
    this._snapshotLoader = snapshotLoader;
    this._onChange = onChange;
    this._reconnectMs = reconnectMs;
    this._resnapshotMs = resnapshotMs;
    this._freshnessMs = freshnessMs;
    this._now = now;
    this._setTimer = setTimer;
    this._clearTimer = clearTimer;
    this._source = null;
    this._sseGeneration = 0;
    this._reconnectTimer = null;
    this._resnapshotTimer = null;
    this._freshnessTimer = null;
    this._httpRequest = null;
    this._pendingResnapshotGeneration = null;
    this._running = false;
    this._acceptedVersion = 0;
    this._hasFreshSnapshot = false;
    this._lastFreshAt = 0;
  }

  start() {
    // Repeated visible/start notifications belong to the same coordinator
    // lifetime. They must not obsolete or duplicate an in-flight HTTP check.
    if (this._running) return this.replica.generation;
    this._running = true;
    const generation = this.replica.beginGeneration();
    this._acceptedVersion = 0;
    this._hasFreshSnapshot = false;
    this._lastFreshAt = this._now();
    this._onChange(this.replica);
    this._scheduleFreshness(generation);
    void this._resnapshot(generation);
    this._connectSse(generation);
    return generation;
  }

  _connectSse(generation) {
    if (!this._running || generation !== this.replica.generation) return;
    this._clearTimer(this._reconnectTimer);
    this._reconnectTimer = null;
    const sseGeneration = ++this._sseGeneration;
    let source;
    try {
      source = this._eventSourceFactory(`${API_BASE}/live-notes/stream`);
    } catch {
      this._fail(generation, sseGeneration, null);
      return;
    }
    this._source = source;
    source.addEventListener("snapshot", (event) => {
      if (
        !this._running ||
        generation !== this.replica.generation ||
        sseGeneration !== this._sseGeneration ||
        source !== this._source
      ) return;
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch {
        this._fail(generation, sseGeneration, source);
        return;
      }
      this._accept(payload, generation);
    });
    source.onerror = () => this._fail(generation, sseGeneration, source);
  }

  stop(markStale = true) {
    this._running = false;
    this._sseGeneration += 1;
    if (this._httpRequest) this._httpRequest.controller.abort();
    this._clearTimer(this._reconnectTimer);
    this._clearTimer(this._resnapshotTimer);
    this._clearTimer(this._freshnessTimer);
    this._reconnectTimer = null;
    this._resnapshotTimer = null;
    this._freshnessTimer = null;
    this._pendingResnapshotGeneration = null;
    if (this._source) this._source.close();
    this._source = null;
    if (markStale && this.replica.markStale(this.replica.generation)) {
      this._onChange(this.replica);
    }
  }

  _fail(generation, sseGeneration, source) {
    if (
      !this._running ||
      generation !== this.replica.generation ||
      sseGeneration !== this._sseGeneration ||
      (source && source !== this._source)
    ) return;
    if (source) source.close();
    this._source = null;
    // Transport loss is not authority loss while HTTP or a prior SSE snapshot
    // is still inside the independent freshness window.
    if (
      !this._hasFreshSnapshot ||
      this._now() - this._lastFreshAt >= this._freshnessMs
    ) {
      if (this.replica.markStale(generation)) this._onChange(this.replica);
    }
    this._clearTimer(this._reconnectTimer);
    this._reconnectTimer = this._setTimer(() => {
      if (this._running && generation === this.replica.generation) {
        this._connectSse(generation);
      }
    }, this._reconnectMs);
  }

  _accept(payload, generation) {
    if (!this.replica.accept(payload, generation)) return false;
    this._acceptedVersion += 1;
    this._hasFreshSnapshot = true;
    this._lastFreshAt = this._now();
    this._scheduleFreshness(generation);
    this._onChange(this.replica);
    return true;
  }

  async _resnapshot(generation) {
    if (!this._running || generation !== this.replica.generation) return;
    if (typeof this._snapshotLoader !== "function") return;
    if (this._httpRequest) {
      // A request from an intentionally replaced visibility lifetime must
      // finish before the current lifetime verifies. Same-lifetime duplicate
      // starts are idempotent and do not queue redundant work.
      if (this._httpRequest.generation !== generation) {
        this._pendingResnapshotGeneration = generation;
      }
      return;
    }
    const versionAtRequest = this._acceptedVersion;
    const controller = new AbortController();
    const request = { generation, versionAtRequest, controller };
    this._httpRequest = request;
    try {
      const payload = await this._snapshotLoader(controller.signal);
      // Any snapshot accepted while GET was in flight wins. This rejects an
      // older delayed response even when it belongs to a different epoch.
      if (
        this._running &&
        generation === this.replica.generation &&
        versionAtRequest === this._acceptedVersion
      ) {
        this._accept(payload, generation);
      }
    } catch {
      // SSE may still be healthy. The independent freshness deadline below
      // clears authoritative-looking highlights if neither path refreshes.
    } finally {
      if (this._httpRequest === request) this._httpRequest = null;
      const pendingGeneration = this._pendingResnapshotGeneration;
      this._pendingResnapshotGeneration = null;
      if (
        this._running &&
        pendingGeneration === this.replica.generation
      ) {
        void this._resnapshot(pendingGeneration);
      } else if (this._running && generation === this.replica.generation) {
        this._clearTimer(this._resnapshotTimer);
        this._resnapshotTimer = this._setTimer(
          () => void this._resnapshot(generation),
          this._resnapshotMs
        );
      }
    }
  }

  _scheduleFreshness(generation) {
    this._clearTimer(this._freshnessTimer);
    const remaining = Math.max(
      0,
      this._freshnessMs - (this._now() - this._lastFreshAt)
    );
    this._freshnessTimer = this._setTimer(() => {
      if (!this._running || generation !== this.replica.generation) return;
      if (this._now() - this._lastFreshAt >= this._freshnessMs) {
        if (this.replica.markStale(generation)) this._onChange(this.replica);
        return;
      }
      this._scheduleFreshness(generation);
    }, remaining);
  }
}

/**
 * Control enablement derived from the CANONICAL playback snapshot.
 * IDLE/COMPLETED/FAILED allow play; PLAYING allows pause+stop; PAUSED
 * allows resume+stop; anything else (STOPPING/unavailable) disables all.
 */
export function controlEnablement(snapshot, fresh = true) {
  if (!snapshot || snapshot.available !== true || fresh !== true) {
    return { play: false, pause: false, resume: false, stop: false };
  }
  switch (snapshot.state) {
    case "IDLE":
    case "COMPLETED":
    case "FAILED":
      return { play: true, pause: false, resume: false, stop: false };
    case "PLAYING":
      return { play: false, pause: true, resume: false, stop: true };
    case "PAUSED":
      return { play: false, pause: false, resume: true, stop: true };
    default: // STOPPING or unknown: no control is safe to offer
      return { play: false, pause: false, resume: false, stop: false };
  }
}

/** One-line pill summary of the playback snapshot. */
export function playbackSummary(snapshot) {
  if (!snapshot || snapshot.available !== true) return "unavailable";
  return snapshot.state || "unknown";
}

/** Pill tone for a state value (never color-only: the text carries it). */
export function statusTone(kind, value) {
  if (value === null || value === undefined) return "bad";
  if (kind === "app") return value === "READY" ? "ok" : "warn";
  if (kind === "midi") return value === "CONNECTED" ? "ok" : "warn";
  if (kind === "library") return value === true ? "ok" : "bad";
  if (kind === "playback") {
    return value === "PLAYING" || value === "PAUSED" ? "ok" : "warn";
  }
  return "warn";
}

/* --------------------------------------------------------------------
 * LED logic (PIANO-P014; pure, node-tested)
 * -------------------------------------------------------------------- */

export const LED_MODES = ["off", "playback", "physical", "all"];
export const LED_BRIGHTNESS_MIN = 0;
export const LED_BRIGHTNESS_MAX = 100;

/** Quantize to an integer and clamp to the canonical 0..100 range. */
export function clampBrightness(value) {
  const n = Math.round(Number(value));
  if (!Number.isFinite(n)) return LED_BRIGHTNESS_MAX;
  return Math.min(LED_BRIGHTNESS_MAX, Math.max(LED_BRIGHTNESS_MIN, n));
}

/** One-line LED status summary.
 * Distinct states (never conflated):
 *   - configured/enabled + hardware present -> "connected"
 *   - configured/enabled, waiting for hardware -> "listening"
 *   - configured but user-disabled -> "disabled"
 *   - configured but transport failed -> "unavailable"
 *   - not configured on this appliance -> "unavailable"
 */
export function ledSummary(state) {
  if (!state) return "unavailable";
  if (!isLedConfigured(state)) return "unavailable";
  if (state.enabled !== true) return "disabled";
  if (state.available !== true) return "unavailable";
  return (state.connection || "unknown").toLowerCase();
}

/** True when the LED subsystem EXISTS on this appliance (capability is
 * present), whether or not it is currently enabled/connected. A
 * user-disabled subsystem stays toggleable (P014-HF1). Defensive
 * fallback for payloads without the explicit `configured` field. */
export function isLedConfigured(state) {
  if (!state) return false;
  if (state.configured === true) return true;
  return state.enabled === true ||
    (state.mode !== null && state.mode !== undefined);
}

/** Pill tone for the LED state (never color-only: the text carries it). */
export function ledStatusTone(state) {
  if (!isLedConfigured(state)) return "bad";
  if (state.enabled !== true) return "warn";
  if (state.available !== true) return "bad";
  switch (state.connection) {
    case "CONNECTED":
      return "ok";
    case "LISTENING":
      return "warn";
    default:
      return "bad";
  }
}

/**
 * LED control enablement (P014-HF1 contract):
 * - the ENABLE TOGGLE is offered whenever the subsystem is CONFIGURED,
 *   even if disabled/unavailable — disabling must never remove the
 *   ability to re-enable;
 * - mode/brightness/clear operate the live transport, so they require
 *   the subsystem to be available (enabled + not unavailable).
 */
export function ledControlEnablement(state) {
  const configured = isLedConfigured(state);
  const usable = configured && state.available === true;
  return { toggle: configured, mode: usable, brightness: usable, clear: usable };
}

/** Human message for a stable API error code (no tracebacks, ever). */
export function errorText(code, fallback) {
  const messages = {
    song_not_found: "Song not found in the library — refresh and try again.",
    song_source_missing: "The song file is missing on the appliance — rescan needed.",
    playback_already_active: "Playback is already active — stop the current song first.",
    invalid_state_transition: "That control is not valid for the current playback state.",
    song_unplayable: "This file is not a playable MIDI song.",
    library_unavailable: "The library is unavailable right now.",
    playback_unavailable: "Playback is unavailable (MIDI output not open).",
    led_unavailable: "The LED subsystem is not configured on this appliance.",
    practice_unavailable: "Guided practice is unavailable right now.",
    practice_already_active: "A practice session is already running.",
    activity_conflict: "Stop the other activity first — practice and playback cannot run together.",
    invalid_learning_mode: "That learning mode is not supported by Core.",
    invalid_learning_configuration: "Check the learning session settings.",
    invalid_section: "Enter a valid time section inside the song.",
    lesson_unavailable: "This lesson could not be loaded. Check the connection and try again.",
    stale_learning_command: "Learning state changed — refreshed before applying that command.",
    not_ready: "Piano Core is not ready yet.",
    invalid_tempo: "Tempo must be between 0.25× and 4.0×.",
    invalid_song_id: "No song selected.",
    malformed_request: "That action could not be completed. Try again.",
    payload_too_large: "The request was too large.",
    unsupported_media_type: "The request format was rejected.",
    method_not_allowed: "That action is not supported here.",
    not_found: "Not found.",
    internal_error: "Piano Core ran into a problem. Try again in a moment.",
    invalid_response: "Piano Core returned an invalid response.",
  };
  return messages[code] || fallback || "Something went wrong.";
}

/** Build a bounded, encoded search/listing URL (never unbounded). */
export function searchUrl(query, limit, offset = 0, status = null) {
  const bounded = Math.min(100, Math.max(1, Math.floor(Number(limit) || 20)));
  const params = new URLSearchParams();
  params.set("limit", String(bounded));
  if (query) {
    params.set("q", query);
  } else if (offset > 0) {
    params.set("offset", String(Math.max(0, Math.floor(offset))));
  }
  // The status facet is a real Core listing parameter; it applies to the
  // unfiltered listing only, so it is never sent with a search query.
  if (!query && status) {
    params.set("status", String(status));
  }
  return `${API_BASE}/library/songs?${params.toString()}`;
}

/**
 * Search-race guard: returns true when a finished response is STALE
 * (a newer request was issued in the meantime) and must be discarded.
 */
export function isStaleResponse(token, currentToken) {
  return token !== currentToken;
}

/** Clamp a Core position without inventing duration or negative song time. */
export function clampPlaybackPosition(position, duration) {
  const p = Number(position);
  const d = Number(duration);
  if (!Number.isFinite(p)) return 0;
  if (!Number.isFinite(d) || d <= 0) return Math.max(0, p);
  return Math.min(d, Math.max(0, p));
}

/**
 * Capture one authoritative Core snapshot at client monotonic receipt time.
 * The returned object is a presentation anchor only: it cannot schedule MIDI.
 */
export function createPlaybackAnchor(snapshot, receivedAtMs) {
  if (!snapshot || !Number.isFinite(Number(receivedAtMs))) return null;
  return {
    snapshot,
    receivedAtMs: Number(receivedAtMs),
    positionSeconds: clampPlaybackPosition(
      snapshot.position_seconds,
      snapshot.duration_seconds
    ),
  };
}

/**
 * Derive one visual frame from an authoritative anchor. PLAYING may advance
 * only inside the finite freshness window; every other state stays frozen.
 */
export function playbackFrame(
  anchor,
  nowMs,
  freshnessMs = PLAYBACK_FRESHNESS_MS
) {
  if (!anchor || !anchor.snapshot) {
    return { position: 0, remaining: 0, progress: 0, stale: true };
  }
  const snapshot = anchor.snapshot;
  const duration = Math.max(0, Number(snapshot.duration_seconds) || 0);
  const ageMs = Math.max(0, Number(nowMs) - anchor.receivedAtMs);
  const maxAge = Math.max(0, Number(freshnessMs) || 0);
  const stale = ageMs > maxAge;
  let position = anchor.positionSeconds;
  if (snapshot.state === "PLAYING") {
    const boundedAge = Math.min(ageMs, maxAge);
    const rate = Math.max(0, Number(snapshot.tempo_multiplier) || 1);
    position += (boundedAge / 1000) * rate;
    if (Number.isFinite(anchor.correctionSeconds)) {
      const correctionWindow = Math.max(
        1,
        Number(anchor.correctionDurationMs) || PLAYBACK_DRIFT_CORRECTION_MS
      );
      position += anchor.correctionSeconds * Math.min(1, boundedAge / correctionWindow);
    }
  } else if (snapshot.state === "COMPLETED" && duration > 0) {
    position = duration;
  }
  position = clampPlaybackPosition(position, duration);
  const remaining = duration > 0 ? Math.max(0, duration - position) : 0;
  const progress = duration > 0 ? Math.min(1, position / duration) : 0;
  return { position, remaining, progress, stale };
}

/**
 * Playback payload classification (PIANO-UI-003 playback-contract
 * remediation). The three classes are distinct on purpose:
 *   - VALID_AVAILABLE: authoritative playback truth for anchors,
 *     interpolation and transport control;
 *   - VALID_UNAVAILABLE: legitimate authoritative "playback unavailable"
 *     truth (Core publishes `available:false` with the engine's real
 *     last-session fields, or the all-null shape when the manager is
 *     absent — both are valid responses, not malformed ones);
 *   - INVALID: wrong field types, impossible values or malformed schema.
 */
export const PLAYBACK_PAYLOAD_CLASS = Object.freeze({
  VALID_AVAILABLE: "valid_available",
  VALID_UNAVAILABLE: "valid_unavailable",
  INVALID: "invalid",
});

/** Canonical playback states published by the Core/API contract
 * (`piano_core.playback.engine.PlaybackState` via
 * `playback_session_payload`). No other value is authoritative. */
export const VALID_PLAYBACK_STATES = Object.freeze([
  "IDLE",
  "PLAYING",
  "PAUSED",
  "STOPPING",
  "COMPLETED",
  "FAILED",
]);

/** Core tempo contract: `playback.timeline.validate_tempo_scale` accepts
 * exactly the finite range 0.25..4.0; the API rejects anything else. */
export const PLAYBACK_TEMPO_MIN = 0.25;
export const PLAYBACK_TEMPO_MAX = 4.0;

/* Strict JSON domain helpers: NO numeric coercion. `Number()` would let
 * null -> 0, true -> 1, "12" -> 12 and [] -> 0 pass an authority boundary. */
function isJsonNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}
function isNumberOrNull(value) {
  return value === null || isJsonNumber(value);
}
function isStringOrNull(value) {
  return value === null || typeof value === "string";
}
function isCanonicalStateOrNull(value) {
  return value === null ||
    (typeof value === "string" && VALID_PLAYBACK_STATES.includes(value));
}

/**
 * The single classification boundary for Core playback responses. Strict
 * against invalid data, compatible with every legitimate Core payload:
 *   - shared shape: object with boolean `available`; string-or-null
 *     `song_id`/`title`/`error` when present; `position_seconds`,
 *     `duration_seconds` and `tempo_multiplier` null-or-real-number when
 *     present; `events_sent` a real non-negative integer or null when
 *     present (absent fields are skipped — Core always emits its nine
 *     canonical keys, so "absent" only occurs in contract fixtures);
 *     NO coercion: null/true/"12"/[] can never become numeric truth;
 *   - available:false -> VALID_UNAVAILABLE with `state` null or canonical
 *     (Core publishes the engine's real last-session numbers alongside
 *     `available:false`, or the all-null manager-absent shape);
 *   - available:true -> VALID_AVAILABLE only when `state` is canonical,
 *     position/duration/tempo are REQUIRED real finite numbers (Core's
 *     serializer always publishes them for a live session), position and
 *     duration are >= 0 with position <= duration, COMPLETED keeps
 *     position == duration (Core publishes `_origin_song =
 *     timeline.duration`, including the legitimate 0 == 0 zero-duration
 *     case), and `tempo_multiplier` stays inside the Core 0.25..4.0
 *     contract (`validate_tempo_scale`).
 */
export function classifyPlaybackPayload(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return PLAYBACK_PAYLOAD_CLASS.INVALID;
  }
  if (typeof payload.available !== "boolean") {
    return PLAYBACK_PAYLOAD_CLASS.INVALID;
  }
  for (const field of ["song_id", "title", "error"]) {
    const value = payload[field];
    if (value !== undefined && !isStringOrNull(value)) {
      return PLAYBACK_PAYLOAD_CLASS.INVALID;
    }
  }
  for (const field of ["position_seconds", "duration_seconds", "tempo_multiplier"]) {
    const value = payload[field];
    if (value !== undefined && !isNumberOrNull(value)) {
      return PLAYBACK_PAYLOAD_CLASS.INVALID;
    }
  }
  const events = payload.events_sent;
  if (
    events !== undefined &&
    !(events === null || (isJsonNumber(events) && Number.isInteger(events) && events >= 0))
  ) {
    return PLAYBACK_PAYLOAD_CLASS.INVALID;
  }
  if (
    payload.position_seconds !== undefined &&
    payload.duration_seconds !== undefined &&
    payload.position_seconds !== null &&
    payload.duration_seconds !== null &&
    payload.position_seconds > payload.duration_seconds
  ) {
    return PLAYBACK_PAYLOAD_CLASS.INVALID;
  }

  if (payload.available === false) {
    if (!isCanonicalStateOrNull(payload.state)) {
      return PLAYBACK_PAYLOAD_CLASS.INVALID;
    }
    return PLAYBACK_PAYLOAD_CLASS.VALID_UNAVAILABLE;
  }

  if (
    typeof payload.state !== "string" ||
    !VALID_PLAYBACK_STATES.includes(payload.state)
  ) {
    return PLAYBACK_PAYLOAD_CLASS.INVALID;
  }
  const position = payload.position_seconds;
  const duration = payload.duration_seconds;
  const tempo = payload.tempo_multiplier;
  // A live Core session always publishes real numbers for these three.
  if (!isJsonNumber(position) || !isJsonNumber(duration) || !isJsonNumber(tempo)) {
    return PLAYBACK_PAYLOAD_CLASS.INVALID;
  }
  if (position < 0 || duration < 0 || position > duration) {
    return PLAYBACK_PAYLOAD_CLASS.INVALID;
  }
  if (payload.state === "COMPLETED" && Math.abs(position - duration) > 1e-6) {
    return PLAYBACK_PAYLOAD_CLASS.INVALID;
  }
  if (tempo < PLAYBACK_TEMPO_MIN || tempo > PLAYBACK_TEMPO_MAX) {
    return PLAYBACK_PAYLOAD_CLASS.INVALID;
  }
  return PLAYBACK_PAYLOAD_CLASS.VALID_AVAILABLE;
}

/** True for every VALID response (available or legitimately unavailable). */
export function isValidPlaybackResponse(payload) {
  return classifyPlaybackPayload(payload) !== PLAYBACK_PAYLOAD_CLASS.INVALID;
}

/**
 * True only for VALID_AVAILABLE payloads — the payloads that may become
 * playback authority for anchors, interpolation and transport control.
 * VALID_UNAVAILABLE is authoritative *unavailable* truth and is handled
 * explicitly by the coordinator instead.
 */
export function isUsablePlaybackSnapshot(snapshot) {
  return (
    classifyPlaybackPayload(snapshot) === PLAYBACK_PAYLOAD_CLASS.VALID_AVAILABLE
  );
}

/** Coherent presentation state derived from Core truth plus local UI state. */
export function playerPresentationState(
  snapshot,
  hasSelection = false,
  fresh = true,
  busy = false
) {
  if (busy) return "loading";
  if (!isUsablePlaybackSnapshot(snapshot) || fresh !== true) return "unavailable";
  switch (snapshot.state) {
    case "PLAYING": return "playing";
    case "PAUSED": return "paused";
    case "COMPLETED": return "completed";
    case "FAILED": return "error";
    case "STOPPING": return "stopping";
    case "IDLE": return hasSelection ? "ready" : "unloaded";
    default: return "unavailable";
  }
}

/** Any authoritative discontinuity replaces, rather than merges, the anchor. */
export function playbackNeedsReanchor(
  previousAnchor,
  next,
  receivedAtMs,
  toleranceSeconds = 0.35
) {
  if (!previousAnchor || !previousAnchor.snapshot || !next) return true;
  const previous = previousAnchor.snapshot;
  if (previous.song_id !== next.song_id || previous.state !== next.state) return true;
  if (previous.tempo_multiplier !== next.tempo_multiplier) return true;
  if (previous.duration_seconds !== next.duration_seconds) return true;
  // Pause/resume and every terminal transition install exact Core truth.
  // Smoothing is only meaningful between consecutive PLAYING snapshots.
  if (previous.state !== "PLAYING") return true;
  const expected = playbackFrame(
    previousAnchor,
    receivedAtMs,
    Number.MAX_SAFE_INTEGER
  ).position;
  const actual = clampPlaybackPosition(
    next.position_seconds,
    next.duration_seconds
  );
  return Math.abs(actual - expected) > Math.max(0, Number(toleranceSeconds) || 0);
}

/**
 * Install a Core snapshot as a presentation anchor. Small PLAYING-only drift
 * converges over a bounded window without stepping the visible position back;
 * state/song/tempo/duration changes and large jumps re-anchor immediately.
 */
export function reconcilePlaybackAnchor(
  previousAnchor,
  next,
  receivedAtMs,
  toleranceSeconds = PLAYBACK_DRIFT_TOLERANCE_SECONDS,
  correctionDurationMs = PLAYBACK_DRIFT_CORRECTION_MS
) {
  const exact = createPlaybackAnchor(next, receivedAtMs);
  if (!exact || playbackNeedsReanchor(
    previousAnchor,
    next,
    receivedAtMs,
    toleranceSeconds
  )) {
    return exact;
  }
  const expected = playbackFrame(
    previousAnchor,
    receivedAtMs,
    Number.MAX_SAFE_INTEGER
  ).position;
  const actual = exact.positionSeconds;
  return {
    ...exact,
    positionSeconds: expected,
    correctionSeconds: actual - expected,
    correctionDurationMs: Math.max(1, Number(correctionDurationMs) || 1),
  };
}

/** Native controls and ARIA widgets keep ownership of Space/Enter/Escape. */
export function isInteractiveShortcutTarget(target) {
  if (!target || typeof target.closest !== "function") return false;
  return target.closest(
    "input, textarea, select, button, a, summary, " +
    "[contenteditable]:not([contenteditable='false']), " +
    "[role='button'], [role='link'], [role='checkbox'], [role='radio'], " +
    "[role='slider'], [role='menuitem'], [role='option'], [role='tab'], " +
    "[role='switch'], [role='textbox'], [role='combobox']"
  ) !== null;
}

/** Return the transport intent for a global shortcut, or null when ignored. */
export function transportShortcutAction(event, enablement) {
  if (!event || event.defaultPrevented || event.repeat) return null;
  if (event.metaKey || event.ctrlKey || event.altKey) return null;
  if (isInteractiveShortcutTarget(event.target)) return null;
  if (event.code === "Escape") return enablement.stop ? "stop" : null;
  if (event.code !== "Space") return null;
  if (enablement.pause) return "pause";
  if (enablement.resume) return "resume";
  if (enablement.play) return "play";
  return null;
}

/**
 * Wired player replica coordinator. Core snapshots remain authoritative; this
 * class only orders client requests, presentation anchors and explicit library
 * selection. The browser entrypoint below uses these exact methods.
 */
/** Canonical UI identity boundary; source provenance (pianio, etc.) is separate. */
export function selectionSource(songId) {
  if (typeof songId !== "string" || !songId.trim()) {
    return Object.freeze({ kind: "invalid", code: "invalid_song_id" });
  }
  if (songId.startsWith("package:")) {
    const packageId = songId.slice(8);
    return packageId && !/[\s:/\\?#%]/.test(packageId)
      ? Object.freeze({ kind: "package", songId, packageId })
      : Object.freeze({ kind: "invalid", code: "invalid_package_id" });
  }
  return songId.includes(":")
    ? Object.freeze({ kind: "invalid", code: "invalid_song_id" })
    : Object.freeze({ kind: "gold", songId });
}

export class PlayerCoordinator {
  constructor({ freshnessMs = PLAYBACK_FRESHNESS_MS } = {}) {
    this.freshnessMs = freshnessMs;
    this.snapshot = null;
    this.anchor = null;
    this.fresh = false;
    this.commandGeneration = 0;
    this.commandInFlight = false;
    this.sequence = 0;
    this.appliedSequence = 0;
    this.selectedSong = null;
    this.selectionPending = false;
    this.activeSongMetadata = null;
    this.hydrationToken = 0;
    this.hydrationSongId = null;
    this.hydrationController = null;
    this.metadataError = null;
    this.selectionGeneration = 0;
    this.selectionController = null;
    this.selectionIntentKind = null;
  }

  beginPoll() {
    if (this.commandInFlight) return null;
    return {
      kind: "poll",
      generation: this.commandGeneration,
      sequence: ++this.sequence,
    };
  }

  async runPoll(load, snapshotOf = (value) => value, now = () => performance.now()) {
    const ticket = this.beginPoll();
    if (!ticket) return { status: "suppressed", accepted: false, value: null };
    try {
      const value = await load();
      const accepted = this.acceptPoll(snapshotOf(value), ticket, now());
      return { status: accepted ? "accepted" : "stale", accepted, value };
    } catch (error) {
      return {
        status: this.isPollEligible(ticket) ? "failed" : "stale",
        accepted: false,
        value: null,
        error,
      };
    }
  }

  isPollEligible(ticket) {
    if (!ticket || ticket.kind !== "poll") return false;
    if (ticket.generation !== this.commandGeneration || this.commandInFlight) {
      return false;
    }
    return ticket.sequence >= this.appliedSequence;
  }

  acceptPoll(snapshot, ticket, receivedAtMs) {
    if (!this.isPollEligible(ticket)) return false;
    const klass = classifyPlaybackPayload(snapshot);
    if (klass === PLAYBACK_PAYLOAD_CLASS.INVALID) return false;
    if (klass === PLAYBACK_PAYLOAD_CLASS.VALID_UNAVAILABLE) {
      this._applyUnavailable(snapshot);
    } else {
      this._applySnapshot(snapshot, receivedAtMs, false);
    }
    this.appliedSequence = ticket.sequence;
    return true;
  }

  beginCommand(nowMs) {
    if (this.commandInFlight || !this.evaluateFreshness(nowMs)) return null;
    this.commandGeneration += 1;
    this.commandInFlight = true;
    return {
      kind: "command",
      generation: this.commandGeneration,
      sequence: ++this.sequence,
    };
  }

  acceptCommand(snapshot, ticket, receivedAtMs, action, requestedSongId = null) {
    if (!this._isCurrentCommand(ticket)) return false;
    if (classifyPlaybackPayload(snapshot) !== PLAYBACK_PAYLOAD_CLASS.VALID_AVAILABLE) {
      // Core's mutating handlers always answer 2xx with an available
      // session snapshot, so anything else cannot prove the post-command
      // state: outcome unknown, never a guessed success.
      this.markCommandUnknown(ticket);
      return false;
    }
    this._applySnapshot(snapshot, receivedAtMs, true);
    this.appliedSequence = ticket.sequence;
    if (
      action === "play" && requestedSongId &&
      snapshot && snapshot.song_id === requestedSongId &&
      this.selectedSong && this.selectedSong.song_id === requestedSongId
    ) {
      this.activeSongMetadata = this.selectedSong;
      this.selectionPending = false;
    }
    return true;
  }

  beginReconciliation(commandTicket) {
    if (!this._isCurrentCommand(commandTicket)) return null;
    return {
      kind: "reconcile",
      generation: commandTicket.generation,
      sequence: ++this.sequence,
    };
  }

  acceptReconciliation(snapshot, ticket, receivedAtMs) {
    if (!ticket || ticket.kind !== "reconcile") return false;
    if (!this.commandInFlight || ticket.generation !== this.commandGeneration) {
      return false;
    }
    if (ticket.sequence < this.appliedSequence) return false;
    const klass = classifyPlaybackPayload(snapshot);
    if (klass === PLAYBACK_PAYLOAD_CLASS.INVALID) {
      this.invalidateFreshness();
      return false;
    }
    if (klass === PLAYBACK_PAYLOAD_CLASS.VALID_UNAVAILABLE) {
      this._applyUnavailable(snapshot);
    } else {
      this._applySnapshot(snapshot, receivedAtMs, false);
    }
    this.appliedSequence = ticket.sequence;
    return true;
  }

  finishCommand(ticket) {
    if (this._isCurrentCommand(ticket)) this.commandInFlight = false;
  }

  markCommandUnknown(ticket) {
    if (!this._isCurrentCommand(ticket)) return false;
    this.fresh = false;
    return true;
  }

  async runCommand({
    action,
    invoke,
    reconcile,
    requestedSongId = null,
    now = () => performance.now(),
    onAccepted = null,
  }) {
    const ticket = this.beginCommand(now());
    if (!ticket) return { status: "stale", error: null, snapshot: null };
    // One caller-specific classification for a success-shaped response whose
    // payload cannot prove the command outcome (PHASE 4 unusable payload).
    const unusableOutcome = () => {
      this.markCommandUnknown(ticket);
      return {
        status: "unknown",
        error: new ApiError(200, "invalid_response", "Piano Core returned an invalid response."),
        snapshot: this.snapshot,
      };
    };
    try {
      const snapshot = await invoke();
      // A success response proves the command only as an authoritative
      // AVAILABLE snapshot (invalid payloads AND valid-unavailable payloads
      // cannot prove the post-command state).
      if (classifyPlaybackPayload(snapshot) !== PLAYBACK_PAYLOAD_CLASS.VALID_AVAILABLE) {
        return unusableOutcome();
      }
      if (!this.acceptCommand(
        snapshot,
        ticket,
        now(),
        action,
        requestedSongId
      )) {
        return { status: "superseded", error: null, snapshot: null };
      }
      if (onAccepted) onAccepted(snapshot);
      const reconciliationTicket = this.beginReconciliation(ticket);
      try {
        const authoritative = await reconcile();
        if (classifyPlaybackPayload(authoritative) === PLAYBACK_PAYLOAD_CLASS.INVALID) {
          // An invalid reconciliation payload must never clear
          // outcome-unknown state or become fresh authority.
          this.markCommandUnknown(ticket);
          return unusableOutcome();
        }
        if (!this.acceptReconciliation(authoritative, reconciliationTicket, now())) {
          return { status: "superseded", error: null, snapshot: this.snapshot };
        }
        return { status: "success", error: null, snapshot: this.snapshot };
      } catch (error) {
        this.invalidateFreshness();
        return {
          status: "reconciling",
          error,
          snapshot: this.snapshot,
        };
      }
    } catch (error) {
      const commandUnknown =
        !(error instanceof ApiError) ||
        error.status === 0 ||
        error.code === "invalid_response";
      if (commandUnknown) {
        this.markCommandUnknown(ticket);
        return { status: "unknown", error, snapshot: this.snapshot };
      }
      if (
        action === "play" && requestedSongId &&
        ["song_not_found", "song_source_missing"].includes(error.code)
      ) {
        this.invalidateSelectedSong(requestedSongId);
      }
      return { status: "rejected", error, snapshot: this.snapshot };
    } finally {
      this.finishCommand(ticket);
    }
  }

  invalidateFreshness() {
    this.fresh = false;
  }

  evaluateFreshness(nowMs) {
    if (!this.fresh) return false;
    if (this.anchor === null) {
      // Authoritative unavailable truth has no interpolation anchor; it
      // stays authoritative until a newer poll or reconciliation replaces
      // it (and never drives position interpolation).
      return this.snapshot !== null &&
        classifyPlaybackPayload(this.snapshot) ===
          PLAYBACK_PAYLOAD_CLASS.VALID_UNAVAILABLE;
    }
    if (playbackFrame(this.anchor, nowMs, this.freshnessMs).stale) {
      this.fresh = false;
    }
    return this.fresh;
  }

  currentFrame(nowMs) {
    return playbackFrame(this.anchor, nowMs, this.freshnessMs);
  }

  hasCurrentAuthority(nowMs) {
    return this.evaluateFreshness(nowMs) && !this.commandInFlight;
  }

  beginSelectionIntent(kind) {
    this.selectionController?.abort();
    this.selectionController = new AbortController();
    this.selectionIntentKind = kind;
    return Object.freeze({ kind, generation: ++this.selectionGeneration, signal: this.selectionController.signal });
  }

  isSelectionIntentCurrent(ticket) {
    return !!ticket && ticket.generation === this.selectionGeneration && !ticket.signal.aborted;
  }

  cancelSelectionIntent() {
    this.selectionController?.abort();
    this.selectionController = null;
    this.selectionIntentKind = null;
    this.selectionGeneration += 1;
  }

  selectedGoldSong() {
    return selectionSource(this.selectedSong?.song_id).kind === "gold" ? this.selectedSong : null;
  }

  selectSong(record, ticket = null) {
    const source = selectionSource(record?.song_id);
    if (source.kind === "invalid" || (ticket && (!this.isSelectionIntentCurrent(ticket) || ticket.kind !== source.kind))) return false;
    if (source.kind === "package" && record.package_id !== source.packageId) return false;
    if (!ticket) this.beginSelectionIntent(source.kind);
    // An explicit successful Gold selection is a bounded recovery boundary.
    // Ordinary polls retain the error latch and never retry metadata endlessly.
    if (source.kind === "gold" && this.metadataError?.songId === source.songId) this.invalidateHydration();
    this.selectedSong = record;
    this.selectionPending = true;
    return true;
  }

  invalidateSelectedSong(songId) {
    if (!this.selectedSong || this.selectedSong.song_id !== songId) return false;
    this.selectedSong = null;
    this.selectionPending = false;
    return true;
  }

  beginHydration(songId) {
    if (selectionSource(songId).kind !== "gold" || this.hydrationSongId === songId || this.metadataError?.songId === songId) return null;
    if (this.activeSongMetadata && this.activeSongMetadata.song_id === songId) {
      return null;
    }
    this.hydrationSongId = songId;
    this.hydrationController?.abort();
    this.hydrationController = new AbortController();
    return { songId, token: ++this.hydrationToken, signal: this.hydrationController.signal };
  }

  acceptHydration(record, ticket) {
    if (!ticket || ticket.token !== this.hydrationToken) return false;
    if (!record || record.song_id !== ticket.songId || selectionSource(record.song_id).kind !== "gold" || ticket.signal?.aborted) return false;
    if (!this.snapshot || this.snapshot.song_id !== ticket.songId) return false;
    this.activeSongMetadata = record;
    this.metadataError = null;
    return true;
  }

  rejectHydration(error, ticket) {
    if (!ticket || ticket.token !== this.hydrationToken || ticket.signal?.aborted || this.snapshot?.song_id !== ticket.songId) return false;
    this.metadataError = { songId: ticket.songId, code: error.code || "network_error", message: error.message };
    return true;
  }

  invalidateHydration() {
    this.hydrationController?.abort();
    this.hydrationController = null;
    this.hydrationSongId = null;
    this.hydrationToken += 1;
    this.metadataError = null;
  }

  finishHydration(ticket) {
    if (ticket && ticket.token === this.hydrationToken) {
      this.hydrationSongId = null;
    }
  }

  _isCurrentCommand(ticket) {
    return !!ticket && ticket.kind === "command" && this.commandInFlight &&
      ticket.generation === this.commandGeneration;
  }

  _applySnapshot(snapshot, receivedAtMs, exact) {
    if (classifyPlaybackPayload(snapshot) !== PLAYBACK_PAYLOAD_CLASS.VALID_AVAILABLE) {
      this.invalidateFreshness();
      return false;
    }
    if (this.snapshot?.song_id !== snapshot.song_id) this.invalidateHydration();
    this.anchor = exact
      ? createPlaybackAnchor(snapshot, receivedAtMs)
      : reconcilePlaybackAnchor(this.anchor, snapshot, receivedAtMs);
    this.snapshot = snapshot;
    this.fresh = true;
    if (!snapshot || !snapshot.song_id) this.activeSongMetadata = null;
    if (
      this.activeSongMetadata && snapshot &&
      this.activeSongMetadata.song_id !== snapshot.song_id
    ) {
      this.activeSongMetadata = null;
    }
    return true;
  }

  /**
   * Install authoritative UNAVAILABLE truth: playback has no active
   * authority for interpolation (no anchor), transport stays disabled,
   * and the payload is recorded so the UI renders an honest, synced
   * "unavailable" state instead of freezing on stale playback data.
   */
  _applyUnavailable(snapshot) {
    this.invalidateHydration();
    this.anchor = null;
    this.snapshot = snapshot;
    this.fresh = true;
    this.activeSongMetadata = null;
  }
}

/* --------------------------------------------------------------------
 * Practice logic (PIANO-P015; pure, node-tested)
 * -------------------------------------------------------------------- */

/** Expected notes of the current target, e.g. "C4 + E4 + G4". */
export function practiceTargetText(target) {
  if (!target || !Array.isArray(target.names) || target.names.length === 0) {
    return "";
  }
  return target.names.join(" + ");
}

/** One-line pill summary of the practice snapshot. */
export function practiceSummary(session) {
  if (!session || !session.state) return "unavailable";
  return String(session.state).toLowerCase();
}

/** Pill tone for the practice state (never color-only). */
export function practiceStatusTone(session) {
  if (!session || !session.state) return "bad";
  switch (session.state) {
    case "WAITING":
    case "COMPLETED":
      return "ok";
    case "FAILED":
      return "bad";
    default: // IDLE is the normal rest state
      return "warn";
  }
}

export const LEARNING_MODES = Object.freeze([
  "WAIT_FOR_NOTE",
  "SECTION_PRACTICE",
  "REPEAT_UNTIL_CORRECT",
  "FLOW",
]);

export function learningStartConfig({
  songId,
  packageId = "",
  mode = "WAIT_FOR_NOTE",
  speed = 1,
  hand = null,
  sectionStart = "",
  sectionEnd = "",
  requiredRepetitions = 1,
} = {}) {
  if (!LEARNING_MODES.includes(mode)) throw new TypeError("unsupported learning mode");
  const config = packageId
    ? { package_id: packageId, mode }
    : { song_id: songId, mode };
  if (hand) {
    if (!packageId || !["RH", "LH"].includes(hand)) throw new TypeError("invalid practice hand");
    config.hand = hand;
  }
  if (mode === "FLOW") {
    const value = Number(speed);
    if (!Number.isFinite(value) || value < 0.25 || value > 1) throw new TypeError("invalid Flow speed");
    config.speed = value;
  }
  if (mode === "SECTION_PRACTICE") {
    const start = Number(sectionStart);
    const end = Number(sectionEnd);
    if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start) {
      throw new TypeError("invalid learning section");
    }
    config.section = { start_seconds: start, end_seconds: end };
  }
  if (mode === "REPEAT_UNTIL_CORRECT") {
    const repetitions = Number(requiredRepetitions);
    if (!Number.isInteger(repetitions) || repetitions < 1 || repetitions > 16) {
      throw new TypeError("invalid repetition count");
    }
    config.required_repetitions = repetitions;
  }
  return config;
}

/**
 * Bounded view model for the Practice panel. `active` is true only
 * while WAITING (the learner still owes notes); the panel also shows
 * the COMPLETED/FAILED outcomes until stop/next start.
 */
export function practiceView(session) {
  if (!session || !session.state) {
    return {
      active: false,
      showPanel: false,
      statusText: "",
      stepText: "",
      targetText: "",
      progressPct: 0,
      completionText: "",
      errorText: "",
      midiDegraded: false,
      modeText: "",
      sectionText: "",
      repetitionText: "",
      feedbackText: "",
    };
  }
  const active = session.state === "WAITING";
  const showPanel =
    active || session.state === "COMPLETED" || session.state === "FAILED";
  const total = session.total_steps ?? 0;
  const stepNumber = active ? (session.step_index ?? 0) + 1 : null;
  let statusText = "";
  if (active) statusText = "Waiting — play the lit key(s) on the piano";
  else if (session.state === "COMPLETED") statusText = "Practice complete";
  else if (session.state === "FAILED") statusText = "Practice failed";
  return {
    active,
    showPanel,
    statusText,
    stepText: stepNumber !== null ? `Step ${stepNumber} / ${total}` : "",
    targetText: practiceTargetText(session.target),
    progressPct: Math.round((session.progress ?? 0) * 100),
    completionText:
      session.state === "COMPLETED" ? session.mode === "FLOW" ? `Flow complete · ${session.flow?.correct_events ?? 0} of ${total} events correct.` : `All ${total} steps played.` : "",
    errorText:
      session.state === "FAILED" ? session.error || "Practice failed." : "",
    midiDegraded: active && session.midi_connection !== "CONNECTED",
    modeText: String(session.mode || "WAIT_FOR_NOTE").replaceAll("_", " "),
    sectionText: session.section
      ? `${session.section.start_seconds.toFixed(1)}s–${session.section.end_seconds.toFixed(1)}s`
      : "Full song",
    repetitionText: session.mode === "REPEAT_UNTIL_CORRECT"
      ? `Consecutive ${session.repetition?.current ?? 0} / ${session.repetition?.required ?? 1} · successful ${session.repetition?.successful ?? 0} · reset ${session.repetition?.failed ?? 0}`
      : "",
    feedbackText: session.latest_outcome === "CORRECT"
      ? "Correct"
      : session.latest_outcome === "INCORRECT"
        ? "Incorrect — target unchanged"
        : session.latest_outcome === "INPUT_UNAVAILABLE"
          ? "Input unavailable — progress paused"
          : "",
  };
}

/** Guided practice and playback are mutually exclusive (409 by design). */
export function practiceConflictWithPlayback(playbackSnapshot) {
  const state =
    playbackSnapshot && playbackSnapshot.available === true
      ? playbackSnapshot.state
      : null;
  return state === "PLAYING" || state === "PAUSED" || state === "STOPPING";
}

/**
 * Practice control enablement: START needs an available (library-backed)
 * subsystem, no running session, and no active playback; STOP exists
 * only while a session is WAITING.
 */
export function practiceControlEnablement(session, playbackSnapshot) {
  const canStart =
    !!session &&
    session.available === true &&
    session.state !== "WAITING" &&
    !practiceConflictWithPlayback(playbackSnapshot);
  return { start: canStart, stop: !!session && session.state === "WAITING" };
}

/* --------------------------------------------------------------------
 * Learning presentation authority (PIANO-UI-004 U4-F3; pure logic)
 * -------------------------------------------------------------------- */

/** Learning snapshots stop authorizing presentation after this long
 * without a valid accepted snapshot (1 s polling => ~5 misses). */
export const LEARNING_FRESHNESS_MS = 5000;

export const LEARNING_STATES = Object.freeze([
  "IDLE",
  "WAITING",
  "COMPLETED",
  "FAILED",
]);

/** Validate the Learning snapshot envelope before it may replace the
 * replica. An `available: false` payload (subsystem not configured) is
 * valid authoritative truth, not a transport failure. */
export function isValidLearningSnapshot(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return false;
  }
  if (typeof payload.available !== "boolean") return false;
  if (!Array.isArray(payload.accepted_notes)) return false;
  if (!Number.isInteger(payload.version) || payload.version < 0) return false;
  if (
    payload.session_id !== null &&
    payload.session_id !== undefined &&
    typeof payload.session_id !== "string"
  ) {
    return false;
  }
  if (payload.available === true) {
    if (!LEARNING_STATES.includes(payload.state)) return false;
    if (typeof payload.mode !== "string") return false;
    if (payload.mode === "FLOW" && payload.state !== "IDLE") {
      const f = payload.flow;
      if (!f || typeof f !== "object" || typeof f.paused !== "boolean") return false;
      for (const key of ["position_seconds", "duration_seconds", "sample_monotonic_seconds", "speed", "count_in_seconds"]) if (!isJsonNumber(f[key])) return false;
      if (f.speed < 0.25 || f.speed > 1 || f.duration_seconds < 0 || f.count_in_seconds < 0) return false;
      for (const key of ["correct_events", "missed_events", "expected_events", "wrong_presses", "early_attacks", "late_attacks", "on_time_attacks", "measured_attacks"]) if (!Number.isInteger(f[key]) || f[key] < 0) return false;
      if (f.expected_events !== payload.total_steps || f.correct_events + f.missed_events > f.expected_events) return false;
      if (f.early_attacks + f.late_attacks + f.on_time_attacks !== f.measured_attacks) return false;
      if (f.mean_absolute_error_ms !== null && (!isJsonNumber(f.mean_absolute_error_ms) || f.mean_absolute_error_ms < 0)) return false;
    }
  } else if (payload.state !== null) {
    return false;
  }
  return true;
}

/**
 * Ordering + freshness lifecycle for the Learning client replica.
 *
 * Learning GETs are ~1 s polls and every mutating Learning command
 * (start/stop/restart-target/restart-section) is an ordering boundary:
 * any poll begun before an accepted command can never overwrite the
 * command result (stale success AND stale failure are ignored), while
 * current results may apply. Valid snapshots refresh a bounded
 * freshness window; beyond LEARNING_FRESHNESS_MS the presentation
 * authority expires (stale) without touching Player/Core/LiveNote.
 */
export class LearningCoordinator {
  constructor({
    freshnessMs = LEARNING_FRESHNESS_MS,
    now = () => (globalThis.performance ? performance.now() : Date.now()),
    onChange = () => {},
  } = {}) {
    this.freshnessMs = freshnessMs;
    this.snapshot = null;
    this.status = "syncing"; // syncing | fresh | stale
    this.generation = 0;
    this.sequence = 0;
    this.appliedSequence = 0;
    this.lastFreshAtMs = 0;
    this._now = now;
    this._onChange = onChange;
  }

  beginPoll() {
    return {
      kind: "poll",
      generation: this.generation,
      sequence: ++this.sequence,
    };
  }

  beginCommand() {
    // Mutating commands are ordering boundaries: they obsolete every
    // request (poll or command) begun before them.
    this.generation += 1;
    return {
      kind: "command",
      generation: this.generation,
      sequence: ++this.sequence,
    };
  }

  _eligible(ticket) {
    return (
      !!ticket &&
      ticket.generation === this.generation &&
      ticket.sequence >= this.appliedSequence
    );
  }

  acceptPoll(payload, ticket) {
    if (!this._eligible(ticket)) { this._diag = (this._diag || { s: 0 }) ; this._diag.s = (this._diag.s || 0) + 1; return false; }
    if (!isValidLearningSnapshot(payload)) { this._diag = (this._diag || {}); this._diag.i = (this._diag.i || 0) + 1; return false; }
    this._apply(payload, ticket);
    return true;
  }

  acceptCommandResult(payload, ticket) {
    if (!ticket || ticket.kind !== "command") return false;
    if (ticket.generation !== this.generation) return false;
    if (!isValidLearningSnapshot(payload)) return false;
    this._apply(payload, ticket);
    return true;
  }

  /** A current-generation poll failure may age freshness; a stale one
   * is ignored entirely. Returns eligibility for observability. */
  notePollFailure(ticket) {
    return this._eligible(ticket);
  }

  _apply(payload, ticket) {
    this.snapshot = payload;
    this.appliedSequence = ticket.sequence;
    this.lastFreshAtMs = this._now();
    this.status = "fresh";
    this._onChange(this);
  }

  /** Expire presentation authority when no valid snapshot arrived
   * within the freshness window. Returns the resulting status. */
  evaluateFreshness(nowMs = this._now()) {
    if (this.status !== "fresh") return this.status;
    if (nowMs - this.lastFreshAtMs < this.freshnessMs) return this.status;
    this.status = "stale";
    this._onChange(this);
    return this.status;
  }

  /** Core is unreachable: Learning presentation de-authorizes now
   * (Player/Core/LiveNote keep their own independent lifecycles). */
  invalidate() {
    if (this.status === "stale") return;
    this.status = "stale";
    this._onChange(this);
  }

  get authorized() {
    return this.status === "fresh" && isValidLearningSnapshot(this.snapshot);
  }
}

/* --------------------------------------------------------------------
 * Visual Learning System (PIANO-UI-005; pure model, node-tested)
 *
 * One canonical score (Core-derived note spans) + one presentation
 * position (the accepted UI-002 playback anchor, or the authoritative
 * Learning target while WAITING) + the accepted UI-003 88-key geometry.
 * Renderers in visualizer.js consume these mappings; none of this code
 * parses MIDI, grades input, or schedules output.
 * -------------------------------------------------------------------- */

export const SCORE_SCHEMA_VERSION = 1;
export const FINGERING_SCHEMA_VERSION = 1;
export const VISUAL_MODES = Object.freeze(["sheet", "falling", "hybrid"]);
export const VISUAL_MODE_STORAGE_KEY = "piano-core.visual-mode";
export const FALLING_MIN_NOTE_HEIGHT_PX = 3;
export const FALLING_PX_PER_SECOND_MIN = 40;
export const FALLING_PX_PER_SECOND_MAX = 180;
export const FALLING_HORIZON_SECONDS_TARGET = 5;
export const SHEET_POSITION_ANCHOR_FRACTION = 0.35;
export const VISUAL_RESOLUTION_SCALES = Object.freeze([1, 0.75, 0.5]);

/**
 * The canonical 21..108 geometry fractions derived from the ONE accepted
 * UI-003 key model (buildPianoKeyModel). Black keys sit on the white-key
 * boundary exactly like PianoKeyboardRenderer's leftPercent; this is the
 * same geometry, never a second model.
 */
const NOTE_GEOMETRY_FRACTIONS = (() => {
  const centers = new Map();
  const widths = new Map();
  for (const key of PIANO_KEYS) {
    if (key.black) {
      // Black keys carry their boundary only via leftPercent (their
      // whiteIndex is null in the accepted model).
      centers.set(key.midi, key.leftPercent / 100);
      widths.set(key.midi, (1 / PIANO_WHITE_KEY_COUNT) * 0.62);
    } else {
      centers.set(key.midi, (key.whiteIndex + 0.5) / PIANO_WHITE_KEY_COUNT);
      widths.set(key.midi, (1 / PIANO_WHITE_KEY_COUNT) * 0.92);
    }
  }
  return { centers, widths };
})();

/** Horizontal center of one key as a [0..1] fraction of the 88-key span. */
export function noteCenterFraction(midi) {
  return NOTE_GEOMETRY_FRACTIONS.centers.has(midi)
    ? NOTE_GEOMETRY_FRACTIONS.centers.get(midi)
    : null;
}

/** Visual note width as a fraction of the 88-key span (black keys narrower). */
export function noteWidthFraction(midi) {
  return NOTE_GEOMETRY_FRACTIONS.widths.has(midi)
    ? NOTE_GEOMETRY_FRACTIONS.widths.get(midi)
    : null;
}

/** True when the MIDI pitch is a black key (diatonic geometry only). */
export function isVisualBlackKey(midi) {
  return BLACK_PITCH_CLASSES.has(((midi % 12) + 12) % 12);
}

/**
 * Strict validation of the canonical score payload before it may become
 * visual truth. The Core serializer always emits this exact shape; a
 * payload that fails here is transport/authority garbage and never
 * reaches a renderer.
 */
export function isValidScorePayload(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return false;
  if (payload.schema_version !== SCORE_SCHEMA_VERSION) return false;
  if (typeof payload.song_id !== "string" || payload.song_id.length === 0) return false;
  if (
    typeof payload.source_version !== "string" ||
    !/^score-sha256:[0-9a-f]{64}$/.test(payload.source_version)
  ) return false;
  if (typeof payload.title !== "string") return false;
  const duration = payload.duration_seconds;
  if (typeof duration !== "number" || !Number.isFinite(duration) || duration < 0) {
    return false;
  }
  if (!Array.isArray(payload.notes)) return false;
  if (payload.note_count !== payload.notes.length) return false;
  let lastIndex = -1;
  let lastOnset = -Infinity;
  for (const note of payload.notes) {
    if (!note || typeof note !== "object" || Array.isArray(note)) return false;
    if (!Number.isInteger(note.index) || note.index < 0) return false;
    if (note.index <= lastIndex) return false; // canonical onset order
    lastIndex = note.index;
    if (!Number.isInteger(note.midi) || note.midi < 0 || note.midi > 127) return false;
    if (!Number.isInteger(note.channel) || note.channel < 1 || note.channel > 16) {
      return false;
    }
    if (!Number.isInteger(note.velocity) || note.velocity < 1 || note.velocity > 127) {
      return false;
    }
    for (const field of ["onset_seconds", "duration_seconds"]) {
      const value = note[field];
      if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
        return false;
      }
    }
    if (note.onset_seconds < lastOnset) return false;
    lastOnset = note.onset_seconds;
  }
  const diagnostics = payload.diagnostics;
  if (!diagnostics || typeof diagnostics !== "object") return false;
  for (const field of [
    "percussion_note_ons",
    "outside_keyboard_note_ons",
    "unpaired_note_offs",
    "retriggered_note_ons",
    "unterminated_note_ons",
  ]) {
    const value = diagnostics[field];
    if (!Number.isInteger(value) || value < 0) return false;
  }
  const notation = payload.notation;
  if (!notation || !Array.isArray(notation.supported) || !Array.isArray(notation.unsupported)) {
    return false;
  }
  return true;
}

/** Notes inside the verified 21..108 keyboard range, canonical order kept. */
export function keyboardRangeNotes(score) {
  if (!score || !Array.isArray(score.notes)) return [];
  return score.notes.filter(
    (note) => note.midi >= PIANO_NOTE_MIN && note.midi <= PIANO_NOTE_MAX
  );
}

/**
 * Token-guarded canonical score replica. One fetch per song; a newer
 * request supersedes an older one; only valid payloads for the requested
 * song may become visual truth.
 */
export class ScoreReplica {
  constructor() {
    this.state = "idle"; // idle | loading | ready | error
    this.songId = null;
    this.score = null;
    this.error = null;
    this._token = 0;
    this.refreshing = false;
  }

  /** Begin an initial fetch or one bounded authoritative revalidation. */
  beginRequest(songId, { revalidate = false } = {}) {
    if (!songId || typeof songId !== "string") return null;
    if (this.songId === songId) {
      if (this.state === "loading" || this.refreshing) return null;
      if (this.state === "ready" && !revalidate) return null;
      if (this.state === "ready") {
        this.refreshing = true;
        this.error = null;
        this._token += 1;
        return {
          songId,
          token: this._token,
          revalidate: true,
          sourceVersion: this.score?.source_version || null,
        };
      }
    }
    this.songId = songId;
    this.state = "loading";
    this.refreshing = false;
    this.score = null;
    this.error = null;
    this._token += 1;
    return { songId, token: this._token };
  }

  /** Accept one fetched payload; only the current token and song may apply. */
  accept(payload, ticket) {
    if (!ticket || ticket.token !== this._token || ticket.songId !== this.songId) {
      return false;
    }
    if (!isValidScorePayload(payload) || payload.song_id !== this.songId) {
      this.noteFailure(ticket);
      return false;
    }
    this.score = payload;
    this.state = "ready";
    this.refreshing = false;
    this.error = null;
    return true;
  }

  /** Record a failed fetch for the ticket (stale failures are ignored). */
  noteFailure(ticket) {
    if (!ticket || ticket.token !== this._token) return false;
    if (ticket.revalidate && this.score) {
      this.refreshing = false;
      this.state = "ready";
      this.error = "score_refresh_unavailable";
      return true;
    }
    this.state = "error";
    this.refreshing = false;
    this.error = "score_unavailable";
    return true;
  }

  /** A different song became authoritative: drop the old score truth. */
  reset() {
    this.state = "idle";
    this.songId = null;
    this.score = null;
    this.error = null;
    this.refreshing = false;
    this._token += 1;
  }
}

/** Strict plan/score correspondence before inferred advice reaches a renderer. */
export function isValidFingeringPayload(payload, score = null) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return false;
  if (payload.schema_version !== FINGERING_SCHEMA_VERSION) return false;
  if (typeof payload.song_id !== "string" || payload.song_id.length === 0) return false;
  if (!/^score-sha256:[0-9a-f]{64}$/.test(payload.source_version || "")) return false;
  if (!/^fingering-sha256:[0-9a-f]{64}$/.test(payload.plan_id || "")) return false;
  if (payload.engine_version !== "balanced-engine-v1") return false;
  if (payload.provenance !== "DETERMINISTIC_INFERENCE") return false;
  if (payload.version !== 1 || payload.generation !== 1) return false;
  if (payload.strategy !== "balanced_v1") return false;
  const config = payload.config;
  if (!config || typeof config !== "object" || Array.isArray(config)) return false;
  if (config.strategy !== payload.strategy) return false;
  if (!Number.isInteger(config.max_hand_span_semitones) ||
      config.max_hand_span_semitones < 5 || config.max_hand_span_semitones > 24) return false;
  if (!Number.isInteger(config.alternative_count) ||
      config.alternative_count < 1 || config.alternative_count > 5) return false;
  if (!Array.isArray(payload.targets) || !Array.isArray(payload.warnings)) return false;
  const seenIndices = new Set();
  const seenTargetIds = new Set();
  for (const target of payload.targets) {
    if (!target || typeof target !== "object" || Array.isArray(target)) return false;
    if (!Number.isInteger(target.score_index) || target.score_index < 0) return false;
    if (seenIndices.has(target.score_index)) return false;
    seenIndices.add(target.score_index);
    if (typeof target.target_id !== "string" ||
        target.target_id !== "score:" + target.score_index ||
        seenTargetIds.has(target.target_id)) return false;
    seenTargetIds.add(target.target_id);
    if (!Number.isInteger(target.midi) || target.midi < 21 || target.midi > 108) return false;
    if (!Number.isInteger(target.channel) || target.channel < 1 || target.channel > 16) return false;
    if (typeof target.onset_seconds !== "number" ||
        !Number.isFinite(target.onset_seconds) || target.onset_seconds < 0) return false;
    if (typeof target.duration_seconds !== "number" ||
        !Number.isFinite(target.duration_seconds) || target.duration_seconds < 0) return false;
    if (!(["LEFT", "RIGHT"].includes(target.hand))) return false;
    if (!Number.isInteger(target.finger) || target.finger < 1 || target.finger > 5) return false;
    if (!Array.isArray(target.alternatives) || target.alternatives.some(
      (finger) => !Number.isInteger(finger) || finger < 1 || finger > 5
    )) return false;
    if (target.alternatives.length > config.alternative_count) return false;
    if (new Set(target.alternatives).size !== target.alternatives.length) return false;
    if (target.alternatives.includes(target.finger)) return false;
  }
  if (score !== null) {
    if (!isValidScorePayload(score)) return false;
    if (payload.song_id !== score.song_id || payload.source_version !== score.source_version) {
      return false;
    }
    const playable = score.notes.filter(
      (note) => note.midi >= PIANO_NOTE_MIN &&
        note.midi <= PIANO_NOTE_MAX && note.channel !== 10
    );
    if (payload.targets.length !== playable.length) return false;
    const scoreByIndex = new Map(playable.map((note) => [note.index, note]));
    for (const target of payload.targets) {
      const note = scoreByIndex.get(target.score_index);
      if (!note || target.midi !== note.midi || target.channel !== note.channel ||
          target.onset_seconds !== note.onset_seconds ||
          target.duration_seconds !== note.duration_seconds) return false;
    }
  }
  return true;
}

/** Token-guarded inferred advice, accepted only for an exact score version. */
export class FingeringReplica {
  constructor() {
    this.state = "idle";
    this.songId = null;
    this.sourceVersion = null;
    this.plan = null;
    this.error = null;
    this._token = 0;
  }

  beginRequest(songId, sourceVersion) {
    if (!songId || !sourceVersion) return null;
    if (
      this.songId === songId && this.sourceVersion === sourceVersion &&
      this.state !== "error"
    ) return null;
    const sameSource = this.songId === songId && this.sourceVersion === sourceVersion;
    this.songId = songId;
    this.sourceVersion = sourceVersion;
    this.state = "loading";
    this.plan = null;
    // Keep the last reason visible while the existing retry is pending.
    if (!sameSource) this.error = null;
    this._token += 1;
    return { songId, sourceVersion, token: this._token };
  }

  accept(payload, ticket, score = null) {
    if (!ticket || ticket.token !== this._token || ticket.songId !== this.songId) return false;
    if (
      score === null ||
      !isValidFingeringPayload(payload, score) ||
      payload.song_id !== ticket.songId ||
      payload.source_version !== ticket.sourceVersion
    ) {
      this.noteFailure(ticket);
      return false;
    }
    this.plan = payload;
    this.state = "ready";
    this.error = null;
    return true;
  }

  noteFailure(ticket, code = "fingering_unavailable") {
    if (!ticket || ticket.token !== this._token) return false;
    this.state = "error";
    this.error = code === "fingering_unsupported" ? code : "fingering_unavailable";
    return true;
  }

  reset() {
    this.state = "idle";
    this.songId = null;
    this.sourceVersion = null;
    this.plan = null;
    this.error = null;
    this._token += 1;
  }
}

/**
 * Which authoritative source owns the visual position right now.
 * Learning WAITING wins (it owns the appliance activity lock). A playback
 * snapshot with a song identity remains the position source even when its
 * freshness expired — reported with ``stale: true`` so the visual FREEZES
 * on labeled stale truth instead of pretending no song exists. Only the
 * total absence of playback song identity yields ``none``.
 */
export function visualPositionSource({
  playbackSnapshot = null,
  playbackFresh = false,
  learningSnapshot = null,
  learningAuthorized = false,
} = {}) {
  if (
    learningAuthorized &&
    learningSnapshot &&
    learningSnapshot.state === "WAITING" &&
    learningSnapshot.song_id
  ) {
    return { kind: "learning", songId: learningSnapshot.song_id, stale: false };
  }
  if (
    playbackSnapshot &&
    playbackSnapshot.available === true &&
    playbackSnapshot.song_id
  ) {
    return { kind: "playback", songId: playbackSnapshot.song_id, stale: !playbackFresh };
  }
  return { kind: "none", songId: null, stale: false };
}

/**
 * The frozen visual position while Learning WAITING: the current target's
 * canonical onset. Self-paced learning never advances musical time; the
 * stream simply centers on the owed target.
 */
export function learningTargetPosition(learningSnapshot) {
  if (
    !learningSnapshot ||
    learningSnapshot.state !== "WAITING" ||
    !learningSnapshot.target ||
    typeof learningSnapshot.target.onset_seconds !== "number" ||
    !Number.isFinite(learningSnapshot.target.onset_seconds)
  ) {
    return null;
  }
  return Math.max(0, learningSnapshot.target.onset_seconds);
}

/** Learning expected-target/section overlay from the Core snapshot only. */
export function learningOverlayModel(learningSnapshot, authorized) {
  if (!authorized || !learningSnapshot || learningSnapshot.state !== "WAITING") {
    return { active: false, targetNotes: [], targetOnset: null, section: null };
  }
  const target = learningSnapshot.target;
  const section = learningSnapshot.section;
  return {
    active: true,
    targetNotes: target && Array.isArray(target.notes) ? target.notes.slice() : [],
    targetOnset: learningTargetPosition(learningSnapshot),
    section:
      section &&
      typeof section.start_seconds === "number" &&
      typeof section.end_seconds === "number" &&
      section.end_seconds > section.start_seconds
        ? { start: Math.max(0, section.start_seconds), end: section.end_seconds }
        : null,
  };
}

/** Clamp a Learning/playback section to the song's time span (half-open). */
export function sectionTimeClip(section, durationSeconds) {
  if (
    !section ||
    typeof section.start !== "number" ||
    typeof section.end !== "number" ||
    !Number.isFinite(section.start) ||
    !Number.isFinite(section.end) ||
    section.end <= section.start
  ) {
    return null;
  }
  const duration = Number(durationSeconds);
  const finiteDuration = Number.isFinite(duration) && duration > 0 ? duration : null;
  if (finiteDuration === null) {
    return { start: Math.max(0, section.start), end: section.end };
  }
  const start = Math.max(0, Math.min(section.start, finiteDuration));
  const end = Math.max(start, Math.min(section.end, finiteDuration));
  return end > start ? { start, end } : null;
}

/** Half-open [start, end) onset membership (Learning Engine semantics). */
export function isOnsetInHalfOpenSection(onsetSeconds, clipped) {
  if (!clipped) return true;
  return onsetSeconds >= clipped.start && onsetSeconds < clipped.end;
}

/** Pixels per second of fall: bounded, derived from pane height only. */
export function fallingPixelsPerSecond(heightPx) {
  const height = Number(heightPx);
  if (!Number.isFinite(height) || height <= 0) {
    return FALLING_PX_PER_SECOND_MIN;
  }
  const derived = height / FALLING_HORIZON_SECONDS_TARGET;
  return Math.min(
    FALLING_PX_PER_SECOND_MAX,
    Math.max(FALLING_PX_PER_SECOND_MIN, derived)
  );
}

/**
 * The visible falling-notes time window. Returns null for degenerate
 * geometry (zero height or zero rate) — callers must treat null as "no
 * window" instead of dividing by zero.
 */
export function fallingTimeWindow({ positionSeconds, heightPx, pxPerSecond }) {
  const position = Number(positionSeconds);
  const height = Number(heightPx);
  const rate = Number(pxPerSecond);
  if (
    !Number.isFinite(position) ||
    !Number.isFinite(height) ||
    !Number.isFinite(rate) ||
    height <= 0 ||
    rate <= 0
  ) {
    return null;
  }
  const horizon = height / rate;
  return { pastSeconds: position, futureSeconds: position + horizon };
}

/**
 * Y coordinate (CSS pixels) where a note ONSET sits given the current
 * authoritative position. onset == position lands exactly on the strike
 * line; future notes are above it (smaller y).
 */
export function fallingNoteY(onsetSeconds, positionSeconds, strikeY, pxPerSecond) {
  return strikeY - (onsetSeconds - positionSeconds) * pxPerSecond;
}

/** Play state of one span relative to the authoritative position. */
export function notePlayState(onsetSeconds, durationSeconds, positionSeconds) {
  if (positionSeconds >= onsetSeconds + durationSeconds) return "past";
  if (positionSeconds >= onsetSeconds) return "active";
  return "upcoming";
}

/**
 * [start, end) index slice of canonically ordered notes that could be
 * visible in [fromSeconds, toSeconds]. Uses the score's maximum span
 * duration so long notes starting earlier are included; degenerate
 * inputs return an empty slice (never throws, never scans everything).
 */
export function visibleNoteSlice(notes, fromSeconds, toSeconds, maxDurationSeconds) {
  if (!Array.isArray(notes) || notes.length === 0) return { start: 0, end: 0 };
  const from = Number(fromSeconds);
  const to = Number(toSeconds);
  if (!Number.isFinite(from) || !Number.isFinite(to) || to <= from) {
    return { start: 0, end: 0 };
  }
  const maxDuration = Math.max(0, Number(maxDurationSeconds) || 0);
  let lo = 0;
  let hi = notes.length;
  const threshold = from - maxDuration;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (notes[mid].onset_seconds < threshold) lo = mid + 1;
    else hi = mid;
  }
  const start = lo;
  lo = start;
  hi = notes.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (notes[mid].onset_seconds <= to) lo = mid + 1;
    else hi = mid;
  }
  return { start, end: lo };
}

/** Pitch rows of the sheet view: the used 21..108 span (null when empty). */
export function sheetPitchRange(keyboardNotes) {
  if (!Array.isArray(keyboardNotes) || keyboardNotes.length === 0) return null;
  let min = PIANO_NOTE_MAX;
  let max = PIANO_NOTE_MIN;
  for (const note of keyboardNotes) {
    if (note.midi < min) min = note.midi;
    if (note.midi > max) max = note.midi;
  }
  return { min, max };
}

/** Y center (CSS px) of a pitch row; rows tile the drawable height top-down
 * from the HIGHEST pitch (notation orientation) with a minimum row height. */
export function sheetMidiToY(midi, pitchRange, drawableTop, drawableHeight) {
  if (!pitchRange || !Number.isFinite(drawableHeight) || drawableHeight <= 0) return null;
  const rows = pitchRange.max - pitchRange.min + 1;
  const rowHeight = drawableHeight / rows;
  const row = pitchRange.max - midi;
  return drawableTop + row * rowHeight + rowHeight / 2;
}

/** X (CSS px) of a song time inside the sheet viewport (clamped by caller). */
export function sheetTimeToX(seconds, viewStartSeconds, secondsPerViewport, drawableWidth) {
  if (
    !Number.isFinite(secondsPerViewport) ||
    secondsPerViewport <= 0 ||
    !Number.isFinite(drawableWidth) ||
    drawableWidth <= 0
  ) {
    return null;
  }
  return ((seconds - viewStartSeconds) / secondsPerViewport) * drawableWidth;
}

/**
 * Left edge of the sheet viewport: keep the authoritative position at a
 * fixed anchor fraction of the viewport, clamped to the song span.
 * Degenerate (zero/negative) durations clamp to 0 — no infinite scroll.
 */
export function sheetViewStart(positionSeconds, secondsPerViewport, durationSeconds) {
  const duration = Math.max(0, Number(durationSeconds) || 0);
  const viewport = Number(secondsPerViewport);
  if (!Number.isFinite(viewport) || viewport <= 0) return 0;
  const desired = (Number(positionSeconds) || 0) - viewport * SHEET_POSITION_ANCHOR_FRACTION;
  const maxStart = Math.max(0, duration - viewport);
  return Math.max(0, Math.min(desired, maxStart));
}

/** Deterministic ruler step (seconds) for one sheet viewport. */
export function sheetRulerStepSeconds(secondsPerViewport) {
  const viewport = Number(secondsPerViewport);
  if (!Number.isFinite(viewport) || viewport <= 0) return 5;
  const targets = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
  for (const step of targets) {
    if (viewport / step <= 8) return step;
  }
  return 600;
}

/** Sheet viewport width in seconds: derived from song duration, bounded. */
export function sheetViewportSeconds(durationSeconds) {
  const duration = Number(durationSeconds);
  if (!Number.isFinite(duration) || duration <= 0) return 4;
  return Math.min(30, Math.max(4, duration / 6));
}

/** Bounded canvas backing size (CSS px * dpr * quality scale, caps applied). */
export function visualCanvasSize(clientWidth, clientHeight, dpr, resolutionScale) {
  const scale = VISUAL_RESOLUTION_SCALES.includes(resolutionScale)
    ? resolutionScale
    : 1;
  const ratio = Math.min(3, Math.max(1, Number(dpr) || 1));
  const width = Math.max(1, Math.floor((Number(clientWidth) || 0) * ratio * scale));
  const height = Math.max(1, Math.floor((Number(clientHeight) || 0) * ratio * scale));
  return { width, height, ratio: ratio * scale };
}

/** Pure mode reducer: only canonical modes; switching never resets truth. */
export function nextVisualMode(current, requested) {
  return VISUAL_MODES.includes(requested) ? requested : current;
}

/** Load the persisted visual-mode preference (localStorage may be absent). */
export function loadVisualMode(storage) {
  try {
    const value = storage?.getItem?.(VISUAL_MODE_STORAGE_KEY);
    return VISUAL_MODES.includes(value) ? value : "falling";
  } catch {
    return "falling";
  }
}

/**
 * One coherent visual status from replica states. Renderers display
 * exactly this — never an invented notation view to hide a gap.
 */
export function visualStatusModel({
  scoreState = "idle",
  sourceKind = "none",
  playbackStale = false,
  visibleNoteCount = 0,
} = {}) {
  if (sourceKind === "none") return "no-song";
  if (scoreState === "loading") return "loading";
  if (scoreState === "error") return "unavailable";
  if (scoreState === "ready" && visibleNoteCount === 0) return "empty";
  if (playbackStale) return "stale";
  return "ready";
}

/** Semantic summary for screen readers; updates are throttled by callers. */
export function visualAriaSummary({ mode, status, title, positionSeconds, targetText }) {
  const modeLabel = mode === "sheet" ? "Sheet view" : mode === "hybrid" ? "Hybrid view" : "Falling notes view";
  const statusLabels = {
    "no-song": "no song loaded",
    loading: "loading score",
    unavailable: "score unavailable",
    empty: "no playable keyboard notes",
    stale: "visual frozen — Core state stale",
    ready: "ready",
  };
  const bits = [modeLabel, statusLabels[status] || status];
  if (title) bits.push(String(title));
  if (status === "ready" && Number.isFinite(Number(positionSeconds))) {
    bits.push(formatDuration(Math.max(0, Number(positionSeconds))));
  }
  if (targetText) bits.push(`target ${targetText}`);
  return bits.join(" — ");
}

/* --------------------------------------------------------------------
 * 3D hands (PIANO-UI-007): pure helpers
 *
 * Presentation only. The MotionEngine consumes the validated UI-006
 * plan plus the ONE canonical keyboard geometry (never MIDI, never a
 * second keyboard model); the renderer consumes MotionState only.
 * -------------------------------------------------------------------- */

export const HANDS_STORAGE_KEYS = Object.freeze({
  enabled: "piano-core.hands-enabled",
  camera: "piano-core.hands-camera",
  quality: "piano-core.hands-quality",
});

/** The canonical geometry bundle for the MotionEngine: X fractions come
 * exclusively from the accepted UI-003/UI-005 noteCenterFraction. */
export function buildMotionGeometry() {
  const centerFractions = new Map();
  for (let midi = PIANO_NOTE_MIN; midi <= PIANO_NOTE_MAX; midi += 1) {
    const fraction = noteCenterFraction(midi);
    if (fraction === null) throw new Error("invalid canonical piano geometry");
    centerFractions.set(midi, fraction);
  }
  return { centerFractions };
}

/** Load a bounded preference (localStorage may be absent). */
export function loadHandsPreference(storage, key, allowed, fallback) {
  try {
    const value = storage?.getItem?.(key);
    return allowed.includes(value) ? value : fallback;
  } catch {
    return fallback;
  }
}

/** Persist a bounded preference; failures never break the UI. */
export function storeHandsPreference(storage, key, value) {
  try {
    storage?.setItem?.(key, value);
  } catch {
    // Preference persistence is optional.
  }
}

/** Pure camera reducer: only canonical modes; switching never resets. */
export function nextHandsCamera(current, requested) {
  return HANDS_CAMERA_MODES.includes(requested) ? requested : current;
}

/** Pure quality reducer: only canonical modes. */
export function nextHandsQuality(current, requested) {
  return HANDS_QUALITY_MODES.includes(requested) ? requested : current;
}

/** Identity binding of a motion view (song/source/plan). */
export function motionViewIdentity(songId, sourceVersion, planId) {
  return `${songId || ""}|${sourceVersion || ""}|${planId || ""}`;
}

/**
 * One coherent hands status from renderer + replica states. The browser
 * displays exactly this — never an invented renderer state.
 */
export function handsStatusModel({
  rendererStatus = "idle",
  rendererAvailable = true,
  planState = "idle",
  planError = null,
  omittedHands = [],
} = {}) {
  if (!rendererAvailable) return "compatibility";
  if (rendererStatus === HANDS_RENDERER_STATUS.UNAVAILABLE) return "unavailable";
  if (rendererStatus === HANDS_RENDERER_STATUS.RECOVERING) return "recovering";
  if (rendererStatus === HANDS_RENDERER_STATUS.IDLE) return "idle";
  if (planState !== "ready" && planError === "fingering_unsupported") return "unsupported";
  if (planState !== "ready") {
    return planState === "loading" ? "waiting-score" : "no-plan";
  }
  if (omittedHands.length > 0) return "partial";
  return "ready";
}

/* --------------------------------------------------------------------
 * ApiClient — the single API boundary
 * -------------------------------------------------------------------- */

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

/**
 * Explicit request-phase model (PIANO-UI-003 U3-R3). A failed request is
 * classified by the phase in which the failure occurred — never by matching
 * exception message text. Node (undici) raises "TypeError: terminated" for a
 * truncated body while Chromium raises "TypeError: Failed to fetch"; both are
 * the same PHASE-2 transport failure and both must classify identically.
 *
 * PHASE 1 HEADERS   fetch pending / headers not available
 * PHASE 2 BODY      response received / body consumption in progress
 * PHASE 3 COMPLETE  body fully consumed
 * PHASE 4 CLASSIFY  parse / schema classification of the complete body
 *
 * PHASE 1 failure -> network_error; PHASE 2 failure -> network_error
 * (regardless of the status class: an error status whose body never arrived
 * proves nothing); PHASE 3/4 failure -> invalid_response; a complete typed
 * error body -> typed rejection.
 */
export const REQUEST_PHASES = Object.freeze({
  HEADERS: "headers",
  BODY: "body",
  COMPLETE: "complete",
  CLASSIFY: "classify",
});

export class ApiClient {
  constructor(fetchImpl = null, timeoutMs = 8000) {
    this._fetch = fetchImpl || globalThis.fetch.bind(globalThis);
    this._timeoutMs = timeoutMs;
  }

  async _request(method, path, body, externalSignal = null) {
    const controller = new AbortController();
    const forwardAbort = () => controller.abort();
    if (externalSignal) {
      if (externalSignal.aborted) controller.abort();
      else externalSignal.addEventListener("abort", forwardAbort, { once: true });
    }
    const timer = setTimeout(() => controller.abort(), this._timeoutMs);
    // The phase marker is the single classification input: the phase advances
    // only after the previous phase genuinely completed (headers received,
    // body fully consumed), so "body completed" is never inferred from the
    // HTTP status alone.
    let phase = REQUEST_PHASES.HEADERS;
    const networkError = (cause) => {
      const error = new ApiError(0, "network_error", "Cannot reach Piano Core.");
      error.cause = cause;
      error.phase = phase;
      return error;
    };
    const invalidResponse = (status, cause) => {
      const error = new ApiError(
        status,
        "invalid_response",
        "Piano Core returned an invalid response."
      );
      error.cause = cause;
      error.phase = phase;
      return error;
    };
    try {
      let response;
      try {
        response = await this._fetch(path, {
          method,
          signal: controller.signal,
          headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
          body: body !== undefined ? JSON.stringify(body) : undefined,
        });
      } catch (cause) {
        // PHASE 1: network failure / timeout / abort before usable headers.
        throw networkError(cause);
      }

      phase = REQUEST_PHASES.BODY;
      let payload = null;
      let rawText = "";
      try {
        if (typeof response.text === "function") {
          rawText = await response.text();
        } else if (typeof response.json === "function") {
          // Non-production fallback for deterministic test doubles that expose
          // only json(); real browser and undici bodies always provide text().
          // It must never dictate production semantics: a json() rejection is
          // still a PHASE 2 transport failure below.
          payload = await response.json();
          rawText = JSON.stringify(payload);
        } else {
          throw new TypeError("response body reader is unavailable");
        }
      } catch (cause) {
        // PHASE 2: the body did not survive transport to completion. This is
        // a transport failure for every status class — truncated bodies carry
        // no proof of either success or typed rejection.
        throw networkError(cause);
      }

      phase = REQUEST_PHASES.COMPLETE;
      const trimmed = rawText.trim();
      phase = REQUEST_PHASES.CLASSIFY;
      if (trimmed.length === 0) {
        throw invalidResponse(response.status, new SyntaxError("empty response body"));
      }
      if (payload === null) {
        try {
          payload = JSON.parse(rawText);
        } catch (cause) {
          // PHASE 4: complete body, unparseable JSON — a response problem,
          // not a transport failure.
          throw invalidResponse(response.status, cause);
        }
      }

      if (!response.ok) {
        const err = payload && payload.error ? payload.error : {};
        const error = new ApiError(
          response.status,
          err.code || "internal_error",
          err.message || `Request failed (${response.status}).`
        );
        error.phase = REQUEST_PHASES.CLASSIFY;
        throw error;
      }
      return payload;
    } finally {
      clearTimeout(timer);
      externalSignal?.removeEventListener("abort", forwardAbort);
    }
  }

  health() { return this._request("GET", `${API_BASE}/health`); }
  state() { return this._request("GET", `${API_BASE}/state`); }
  songs(query, limit, offset, { signal = null, status = null } = {}) {
    return this._request(
      "GET",
      searchUrl(query, limit, offset, status),
      undefined,
      signal
    );
  }
  song(songId, { signal = null } = {}) {
    if (selectionSource(songId).kind !== "gold") {
      return Promise.reject(new ApiError(400, "invalid_song_id", "Choose a song from the Player library."));
    }
    return this._request(
      "GET",
      `${API_BASE}/library/songs/${encodeURIComponent(songId)}`,
      undefined,
      signal
    );
  }
  score(songId) {
    return this._request(
      "GET",
      `${API_BASE}/score/${encodeURIComponent(String(songId))}`
    );
  }
  fingering(songId) {
    return this._request(
      "GET",
      `${API_BASE}/fingering/${encodeURIComponent(String(songId))}`
    );
  }
  playback() { return this._request("GET", `${API_BASE}/playback`); }
  liveNotes(signal = null) {
    return this._request("GET", `${API_BASE}/live-notes`, undefined, signal);
  }
  play(songId, tempo) {
    if (selectionSource(songId).kind !== "gold") {
      return Promise.reject(new ApiError(400, "invalid_song_id", "Choose a song from the Player library."));
    }
    return this._request("POST", `${API_BASE}/playback/play`, {
      song_id: songId,
      tempo_multiplier: clampTempo(tempo),
    });
  }
  pause() { return this._request("POST", `${API_BASE}/playback/pause`, {}); }
  resume() { return this._request("POST", `${API_BASE}/playback/resume`, {}); }
  stop() { return this._request("POST", `${API_BASE}/playback/stop`, {}); }
  led() { return this._request("GET", `${API_BASE}/led`); }
  device() { return this._request("GET", `${API_BASE}/device`); }
  ledUpdate(fields) {
    return this._request("PUT", `${API_BASE}/led`, fields);
  }
  ledClear() { return this._request("POST", `${API_BASE}/led/clear`, {}); }
  practice() { return this._request("GET", `${API_BASE}/practice`); }
  practiceStart(songId) {
    return this._request("POST", `${API_BASE}/practice/start`, { song_id: songId });
  }
  practiceStop() { return this._request("POST", `${API_BASE}/practice/stop`, {}); }
  learning({ signal = null } = {}) { return this._request("GET", `${API_BASE}/learning`, undefined, signal); }
  learningStart(config) {
    return this._request("POST", `${API_BASE}/learning/start`, config);
  }
  learningStop(session) {
    return this._request("POST", `${API_BASE}/learning/stop`, {
      expected_session_id: session?.session_id,
      expected_version: session?.version,
    });
  }
  learningRestartTarget(session) {
    return this._request("POST", `${API_BASE}/learning/restart-target`, {
      expected_session_id: session?.session_id,
      expected_version: session?.version,
    });
  }
  learn() {
    return this._request("GET", `${API_BASE}/learn`);
  }

  learnLessons(queryString) {
    const suffix = queryString ? `?${queryString.replace(/^\?/, "")}` : "";
    return this._request("GET", `${API_BASE}/learn/lessons${suffix}`);
  }

  learnLesson(lessonId, { signal = null } = {}) {
    return this._request("GET", `${API_BASE}/learn/lessons/${encodeURIComponent(lessonId)}`, undefined, signal);
  }

  learningRestartSection(session) {
    return this._request("POST", `${API_BASE}/learning/restart-section`, {
      expected_session_id: session?.session_id,
      expected_version: session?.version,
    });
  }
}

/* --------------------------------------------------------------------
 * UI wiring (browser only; the pure logic above runs under node too)
 * -------------------------------------------------------------------- */

const POLL_INTERVAL_MS = 1000;
const SEARCH_DEBOUNCE_MS = 300;
const PAGE_SIZE = 50;

if (typeof document !== "undefined") {
  const shell = initializePremiumShell();
  initApp(shell);
}

function initApp(shell) {
  globalThis.__initRuns = (globalThis.__initRuns || 0) + 1;
  const $ = (id) => document.getElementById(id);
  const dom = {
    connectionNote: $("connection-note"),
    pills: {
      app: $("pill-app"),
      midi: $("pill-midi"),
      library: $("pill-library"),
      playback: $("pill-playback"),
      practice: $("pill-practice"),
      led: $("pill-led"),
    },
    playerTitle: $("player-title"),
    playerMeta: $("player-meta"),
    playerState: $("player-state"),
    playerPosition: $("player-position"),
    playerRemaining: $("player-remaining"),
    playerProgress: $("player-progress"),
    playerProgressFill: $("player-progress-fill"),
    playerFreshness: $("player-freshness"),
    play: $("btn-play"),
    pause: $("btn-pause"),
    resume: $("btn-resume"),
    stop: $("btn-stop"),
    tempo: $("tempo"),
    tempoValue: $("tempo-value"),
    playerError: $("player-error"),
    librarySummary: $("library-summary"),
    search: $("search"),
    searchForm: $("search-form"),
    btnClear: $("btn-clear"),
    results: $("results"),
    libraryFeedback: $("library-feedback"),
    btnMore: $("btn-more"),
    selected: $("selected"),
    selTitle: $("sel-title"),
    selFile: $("sel-file"),
    selDuration: $("sel-duration"),
    selFormat: $("sel-format"),
    selTracks: $("sel-tracks"),
    selStatus: $("sel-status"),
    liveKeyboard: $("live-keyboard"),
    liveKeyboardSummary: $("live-keyboard-summary"),
    liveKeyboardStatus: $("live-keyboard-status"),
    workspaceScene: $("ui008-workspace-scene"),
    sceneSlotLearn: $("ui008-scene-slot-learn"),
    sceneSlotPiano: $("ui008-scene-slot-piano"),
    viewSelectLearn: $("ui008-view-learn"),
    pianoView: $("piano-view"),
    learnOptionsBtn: $("ui008-learn-options"),
    learnSheet: $("ui008-learn-sheet"),
    learnSheetClose: $("ui008-learn-sheet-close"),
    pianoOptionsBtn: $("ui008-piano-options"),
    pianoSheet: $("ui008-piano-sheet"),
    pianoSheetClose: $("ui008-piano-sheet-close"),
    visualStage: $("visual-stage"),
    visualSummary: $("visual-summary"),
    visualStatus: $("visual-status"),
    visualPaneSheet: $("sheet-pane"),
    visualPaneFalling: $("falling-pane"),
    visualCanvasSheet: $("sheet-canvas"),
    visualCanvasFalling: $("falling-canvas"),
    visualModeSheet: $("visual-mode-sheet"),
    visualModeFalling: $("visual-mode-falling"),
    visualModeHybrid: $("visual-mode-hybrid"),
    ledStatus: $("led-status"),
    ledControls: $("led-controls"),
    ledEnabled: $("led-enabled"),
    ledMode: $("led-mode"),
    ledBrightness: $("led-brightness"),
    ledBrightnessValue: $("led-brightness-value"),
    btnLedClear: $("btn-led-clear"),
    ledError: $("led-error"),
    practiceCard: $("practice"),
    practiceSong: $("practice-song"),
    practiceStatus: $("practice-status"),
    practiceStep: $("practice-step"),
    practiceTarget: $("practice-target"),
    practiceBar: $("practice-bar"),
    practiceBarFill: $("practice-bar-fill"),
    practiceCompletion: $("practice-completion"),
    practiceNote: $("practice-note"),
    learningModeActive: $("learning-mode-active"),
    learningSectionActive: $("learning-section-active"),
    learningRepetitionActive: $("learning-repetition-active"),
    learningFeedback: $("learning-feedback"),
    learningMode: $("learning-mode"),
    learningSectionConfig: $("learning-section-config"),
    learningSectionStart: $("learning-section-start"),
    learningSectionEnd: $("learning-section-end"),
    learningRepetitionConfig: $("learning-repetition-config"),
    learningRepetitions: $("learning-repetitions"),
    btnLearningRestartTarget: $("btn-learning-restart-target"),
    btnLearningRestartSection: $("btn-learning-restart-section"),
    btnPracticeStop: $("btn-practice-stop"),
    practiceError: $("practice-error"),
    btnPractice: $("btn-practice"),
    ui008LearnState: $("ui008-learn-state"),
    ui008LearnGuidance: $("ui008-learn-guidance"),
    ui008LearnProgress: $("ui008-learn-progress"),
    ui008LearnTargetNotes: $("ui008-learn-target-notes"),
    ui008LearnTarget: $("ui008-learn-target"),
    ui008LearnStartRegion: $("ui008-learn-start-region"),
    ui008LearnLed: $("ui008-learn-led"),
    ui008LearnEmpty: $("ui008-learn-empty"),
    ui008LessonStart: $("ui008-lesson-start"),
    ui008LearnEmptyHeading: $("ui008-learn-empty-heading"),
    ui008LearnEmptyNote: $("ui008-learn-empty-note"),
    pianoState: $("piano-state"),
    pianoGuidance: $("piano-guidance"),
    pianoIdentity: $("piano-identity"),
    pianoIdentityName: $("piano-identity-name"),
    pianoIdentityCode: $("piano-identity-code"),
    pianoLedNote: $("piano-led-note"),
    pianoSustain: $("piano-sustain"),
    pianoNotes: $("piano-notes"),
    pianoLast: $("piano-last"),
    ledChip: $("led-chip"),
    settingsLedChip: $("settings-led-chip"),
    settingsLedGuidance: $("settings-led-guidance"),
    settingsAboutRows: $("settings-about-rows"),
    pillDevice: $("pill-device"),
    pillLedTransport: $("pill-led-transport"),
    settingsVisualSheet: $("settings-visual-sheet"),
    settingsVisualFalling: $("settings-visual-falling"),
    settingsVisualHybrid: $("settings-visual-hybrid"),
    settingsHandsEnabled: $("settings-hands-enabled"),
    settingsHandsCameraFull: $("settings-hands-camera-full"),
    settingsHandsCameraFollow: $("settings-hands-camera-follow"),
    settingsHandsQuality: $("settings-hands-quality"),
    toast: $("toast"),
    handsEnabled: $("hands-enabled"),
    handsCameraFull: $("hands-camera-full"),
    handsCameraFollow: $("hands-camera-follow"),
    handsQuality: $("hands-quality"),
    handsStage: $("hands-stage"),
    handsCanvas: $("hands-canvas"),
    handsStatus: $("hands-status"),
    handsSummary: $("hands-summary"),
  };

  const api = new ApiClient();
  const player = new PlayerCoordinator();
  const keyboardRenderer = new PianoKeyboardRenderer(
    dom.liveKeyboard,
    dom.liveKeyboardSummary
  );
  const liveNotes = new LiveNoteCoordinator({
    snapshotLoader: (signal) => api.liveNotes(signal),
    onChange: (replica) => {
      lastLiveReplica = replica;
      // The keyboard paints only while the Piano view presents it; the
      // authoritative live-note state itself is kept by the replica.
      if (!workspace || workspace.activeRenderer() === "piano") {
        keyboardRenderer.render(replica.snapshot, replica.status);
      }
      dom.liveKeyboardStatus.dataset.state = replica.status;
      setText(
        dom.liveKeyboardStatus,
        replica.status === "live"
          ? "Live"
          : replica.status === "synchronizing" ? "Syncing" : "Unavailable"
      );
      // The falling-notes keyboard strip consumes the SAME authoritative
      // live-note truth (physical + owned playback sources).
      visual.keyboardSources =
        replica.status === "live" ? activeKeySources(replica.snapshot) : new Map();
      ensureVisualAnimation();
    },
  });
  const ui = {
    libraryTotal: null,
    pollBusy: false,
    pollTimer: null,
    controlsBusy: false,
    offline: false,
    playbackFrame: null,
    frozenPlaybackFrame: null,
    freshnessTimer: null,
    visualFrameId: null,
    visualAnimating: false,
    lastLearnChipsKey: "",
    ledBusy: false,
    toastTimer: null,
  };

  // Last CANONICAL playback snapshot (from polls and command responses);
  // drives control enablement so stale responses never gate the UI.
  let lastPlayback = null;
  // Last CANONICAL LED snapshot (from polls and command responses).
  let lastLed = null;
  // Last CANONICAL accepted Learning snapshot (PIANO-UI-004). Only the
  // LearningCoordinator may update it: ordering + freshness authority.
  let lastPractice = null;
  // Last live-note replica (keyboard repaint source when the Piano view
  // becomes visible again — state is re-rendered, never stale).
  let lastLiveReplica = null;
  // U8-P5 Piano/Device + Settings presentation inputs (same authoritative
  // poll cycle; identity is a one-shot cached GET /device read).
  let lastHealth = null;
  let lastPhysical = null;
  let lastDevice = null;

  const learning = new LearningCoordinator({
    onChange: (replica) => renderLearning(replica),
  });

  function learningAuthorizedSession() {
    return learning.authorized ? learning.snapshot : null;
  }

  /* ---------------- visual learning system (PIANO-UI-005) ----------------
   * Presentation only: the stage draws canonical score truth at the
   * authoritative position (playback anchor or Learning target); it never
   * parses MIDI, grades input or moves musical time. */

  const scoreReplica = new ScoreReplica();
  const fingeringReplica = new FingeringReplica();
  let visualMode = loadVisualMode(globalThis.localStorage);
  const visual = {
    notes: [],           // in-range canonical note spans of the active score
    maxSpanDuration: 0,  // visible-slice search bound
    keyboardSources: new Map(), // midi -> physical/playback/both (live replica)
    fingeringByIndex: new Map(), // score index -> version-bound inferred advice
  };

  // The one geometry/mapper bundle injected into the renderers (app.js
  // remains the single keyboard-geometry model).
  const visualMappers = {
    PIANO_KEYS,
    PIANO_NOTE_MIN,
    PIANO_WHITE_KEY_COUNT,
    FALLING_MIN_NOTE_HEIGHT_PX,
    noteCenterFraction,
    noteWidthFraction,
    isVisualBlackKey,
    fallingTimeWindow,
    fallingNoteY,
    notePlayState,
    visibleNoteSlice,
    fallingPixelsPerSecond,
    sheetPitchRange,
    sheetMidiToY,
    sheetTimeToX,
    sheetViewStart,
    sheetRulerStepSeconds,
    sheetViewportSeconds,
    isOnsetInHalfOpenSection,
    visualCanvasSize,
    noteName,
    formatDuration,
  };

  const stage = new VisualStage({
    root: dom.visualStage,
    summaryEl: dom.visualSummary,
    statusEl: dom.visualStatus,
    panes: { sheet: dom.visualPaneSheet, falling: dom.visualPaneFalling },
    canvases: { sheet: dom.visualCanvasSheet, falling: dom.visualCanvasFalling },
    mode: visualMode,
    mappers: visualMappers,
  });

  /* Immersive visual workspace controller (created further below, after
   * the hands subsystem); declared here so render gating can never touch
   * an uninitialized binding. */
  let workspace = null;

  function syncVisualModeButtons() {
    dom.visualModeSheet.setAttribute("aria-pressed", String(visualMode === "sheet"));
    dom.visualModeFalling.setAttribute("aria-pressed", String(visualMode === "falling"));
    dom.visualModeHybrid.setAttribute("aria-pressed", String(visualMode === "hybrid"));
    // U8-P5: Settings mirrors the same local preference.
    dom.settingsVisualSheet.setAttribute("aria-pressed", String(visualMode === "sheet"));
    dom.settingsVisualFalling.setAttribute("aria-pressed", String(visualMode === "falling"));
    dom.settingsVisualHybrid.setAttribute("aria-pressed", String(visualMode === "hybrid"));
    const selector = document.getElementById("studio-visual");
    if (selector && workspace?.currentView() !== "hands") selector.value = visualMode;
    const notationNote = document.getElementById("studio-notation-note");
    if (notationNote) notationNote.hidden = workspace?.currentView() === "hands" || !["sheet", "hybrid"].includes(visualMode);
  }

  /** Mode switching is visual state only: no fetch, no reset, no command. */
  function setVisualMode(requested) {
    const next = nextVisualMode(visualMode, requested);
    if (next === visualMode) return;
    visualMode = next;
    try {
      globalThis.localStorage?.setItem(VISUAL_MODE_STORAGE_KEY, next);
    } catch {
      // Preference persistence is optional; the visual itself continues.
    }
    stage.setMode(next);
    syncVisualModeButtons();
    // Workspace view follows the accepted renderer-mode contract only where
    // it maps (Sheet view shows the sheet renderer). Falling keeps whatever
    // notes mode is configured (falling or hybrid); hybrid never leaves the
    // accepted contract — the notes scene simply presents it.
    if (workspace) {
      if (next === "sheet") workspace.setView("sheet");
      else if (workspace.currentView() === "sheet") workspace.setView("falling");
    }
    ensureVisualAnimation();
  }

  let practiceCommandError = null;
  let studio = null;
  let studioRange = "focus";
  let studioFocus = null;
  let studioPassage = null;
  function buildVisualFrame(nowMs) {
    const learningSession = learning.authorized ? learning.snapshot : null;
    let source = visualPositionSource({
      playbackSnapshot: player.snapshot,
      playbackFresh: player.fresh && !ui.offline,
      learningSnapshot: learningSession,
      learningAuthorized: learning.authorized,
    });
    const retainedFlow = studio?.isOpen() && learning.snapshot?.flow && ["WAITING", "COMPLETED", "FAILED"].includes(learning.snapshot.state) ? learning.snapshot : null;
    if (retainedFlow) source = { kind: "learning", songId: retainedFlow.song_id, stale: !learning.authorized };
    let position = 0;
    let playbackStale = false;
    if (source.kind === "learning") {
      const flowFrame = flowVisualPosition(retainedFlow || learningSession, learning.lastFreshAtMs, nowMs);
      position = flowFrame ? flowFrame.position : learningTargetPosition(learningSession) ?? 0;
      playbackStale = source.stale || !!flowFrame?.stale;
    } else if (source.kind === "playback") {
      const playbackFrameNow = player.currentFrame(nowMs);
      position = playbackFrameNow.position;
      // Stale when the snapshot identity aged out OR the frozen anchor
      // extrapolation window expired — either way the visual must freeze
      // on labeled truth, never keep "playing".
      playbackStale = source.stale || playbackFrameNow.stale || ui.offline;
    }
    const overlay = learningOverlayModel(learningSession, learning.authorized);
    const score = scoreReplica.state === "ready" ? scoreReplica.score : null;
    const clippedSection =
      overlay.section && score
        ? sectionTimeClip(overlay.section, score.duration_seconds)
        : null;
    const status = visualStatusModel({
      scoreState: scoreReplica.state,
      sourceKind: source.kind,
      playbackStale,
      visibleNoteCount: visual.notes.length,
    });
    if (studio?.isOpen() && visual.notes.length) {
      const passage = Math.max(0, Math.floor(position / 8));
      // Reframe in eight-second passages only with all physical keys up.
      // Include notes crossing the boundary; fallback frames the whole lesson.
      if (!studioFocus || (passage !== studioPassage && visual.keyboardSources.size === 0)) {
        const start = passage * 8;
        const notes = visual.notes.filter(n => n.onset_seconds + n.duration_seconds >= start && n.onset_seconds < start + 12);
        studioFocus = focusRange(notes.length ? notes : visual.notes, PIANO_KEYS, PIANO_WHITE_KEY_COUNT);
        studioPassage = passage;
      }
    }
    const frame = {
      range: studio?.isOpen() && studioRange === "focus" ? studioFocus : null,
      mode: visualMode,
      songId: source.songId,
      status,
      position,
      score,
      keyboardNotes: visual.notes,
      maxSpanDuration: visual.maxSpanDuration,
      learning: overlay,
      learningFeedback: { accepted: learningSession?.accepted_notes || [], outcome: learningSession?.latest_outcome, note: learningSession?.flow?.last_note },
      clippedSection,
      keyboardSources: visual.keyboardSources,
      fingeringByIndex: visual.fingeringByIndex,
      fingeringStatus: fingeringReplica.state,
      quality: { resolutionScale: 1, effects: true },
    };
    const targetText = overlay.active
      ? practiceTargetText(learningSession?.target)
      : "";
    const summary = visualAriaSummary({
      mode: visualMode,
      status,
      title: source.kind === "none" ? null : score?.title || source.songId,
      positionSeconds: position,
      targetText,
    });
    return { frame, summary, status };
  }

  function renderVisualStage(nowMs = performance.now()) {
    const renderer = workspace ? workspace.activeRenderer() : "visual";
    const { frame, summary } = buildVisualFrame(nowMs);
    // Only the PRESENTED view paints; the musical poll and engines run on.
    if (renderer === "visual") stage.render(frame, summary);
    renderHands(frame, renderer === "hands");
    return frame;
  }

  /** One render per authoritative/visual change; continuous rAF only
   * while PLAYING (the only state whose position actually moves). */
  function tickVisualStage() {
    ui.visualFrameId = null;
    const frame = renderVisualStage();
    const flowMoving = learning.authorized && learning.snapshot?.mode === "FLOW" && learning.snapshot?.state === "WAITING" && !learning.snapshot.flow?.paused;
    const playbackMoving = player.fresh && player.snapshot?.state === "PLAYING";
    const playing = (flowMoving || playbackMoving) && !ui.offline && frame.status === "ready";
    if (playing) {
      ui.visualFrameId = requestAnimationFrame(tickVisualStage);
    } else {
      ui.visualAnimating = false;
    }
  }

  function ensureVisualAnimation() {
    if (ui.visualAnimating) return;
    ui.visualAnimating = true;
    ui.visualFrameId = requestAnimationFrame(tickVisualStage);
  }

  async function ensureFingering(score) {
    if (!score) return;
    const ticket = fingeringReplica.beginRequest(score.song_id, score.source_version);
    if (!ticket) return;
    visual.fingeringByIndex = new Map();
    ensureVisualAnimation();
    try {
      const payload = await api.fingering(score.song_id);
      if (fingeringReplica.accept(payload, ticket, score)) {
        visual.fingeringByIndex = new Map(
          payload.targets.map((target) => [target.score_index, target])
        );
      }
    } catch (error) {
      fingeringReplica.noteFailure(ticket, error.code);
    } finally {
      ensureVisualAnimation();
    }
  }

  /** Fetch the canonical score for whichever song owns the visual. */
  async function ensureVisualScore() {
    const learningSession = learning.authorized ? learning.snapshot : null;
    const source = visualPositionSource({
      playbackSnapshot: player.snapshot,
      playbackFresh: player.fresh && !ui.offline,
      learningSnapshot: learningSession,
      learningAuthorized: learning.authorized,
    });
    if (!source.songId) {
      if (scoreReplica.state !== "idle") {
        scoreReplica.reset();
        fingeringReplica.reset();
        visual.notes = [];
        visual.maxSpanDuration = 0;
        visual.fingeringByIndex = new Map();
      }
      ensureVisualAnimation();
      return;
    }
    const sameReadySong =
      scoreReplica.songId === source.songId && scoreReplica.state === "ready";
    if (scoreReplica.songId === source.songId && scoreReplica.state === "loading") {
      return;
    }
    const ticket = scoreReplica.beginRequest(
      source.songId,
      { revalidate: sameReadySong }
    );
    if (!ticket) return;
    if (!ticket.revalidate) {
      visual.notes = [];
      visual.maxSpanDuration = 0;
      fingeringReplica.reset();
      visual.fingeringByIndex = new Map();
      ensureVisualAnimation();
    }
    try {
      const payload = await api.score(source.songId);
      if (scoreReplica.accept(payload, ticket)) {
        const sourceChanged =
          ticket.revalidate && ticket.sourceVersion !== payload.source_version;
        if (sourceChanged) {
          // The newly observed Core generation invalidates all old advisory
          // truth before the replacement plan request is allowed to settle.
          fingeringReplica.reset();
          visual.fingeringByIndex = new Map();
        }
        visual.notes = keyboardRangeNotes(payload);
        visual.maxSpanDuration = visual.notes.reduce(
          (max, note) => Math.max(max, note.duration_seconds),
          0
        );
        if (sourceChanged || fingeringReplica.state !== "ready") {
          void ensureFingering(payload);
        }
      }
    } catch {
      scoreReplica.noteFailure(ticket);
    } finally {
      ensureVisualAnimation();
    }
  }

  syncVisualModeButtons();
  ensureVisualAnimation();

  /* ---------------- 3D hands (PIANO-UI-007) ----------------
   * The MotionEngine view is built ONLY from the validated UI-006 plan
   * replica and the ONE canonical geometry; the renderer consumes
   * MotionState only. No MIDI parsing, no fingering decisions, no
   * musical authority. A renderer failure degrades to the existing 2D
   * UI (COMPATIBILITY) without touching anything else. */

  const hands = {
    geometry: buildMotionGeometry(),
    renderer: null,
    available: true, // false after an unrecoverable RendererUnavailableError
    initStarted: false,
    qualityRecovery: null,
    view: null,
    viewIdentity: null,
    lastMotion: null,
    enabled: loadHandsPreference(
      globalThis.localStorage,
      HANDS_STORAGE_KEYS.enabled,
      ["on", "off"],
      "on"
    ) === "on",
    camera: loadHandsPreference(
      globalThis.localStorage,
      HANDS_STORAGE_KEYS.camera,
      HANDS_CAMERA_MODES,
      "FULL"
    ),
    quality: loadHandsPreference(
      globalThis.localStorage,
      HANDS_STORAGE_KEYS.quality,
      HANDS_QUALITY_MODES,
      "AUTO"
    ),
  };

  const omittedHandsOf = (motionState) => {
    if (!motionState) return [];
    // evaluate() reports omitted hands as null; the view keeps the reason.
    const view = hands.view;
    return ["LEFT", "RIGHT"].filter((hand) => {
      const viewHand = view ? view.hands[hand] : null;
      return viewHand ? viewHand.omitted : false;
    });
  };

  function updateHandsDom(motionState) {
    const rendererStatus = hands.renderer ? hands.renderer.status : "idle";
    const status =
      hands.quality === "COMPATIBILITY" || !hands.available
        ? "compatibility"
        : handsStatusModel({
            rendererStatus,
            rendererAvailable: hands.available,
            planState: fingeringReplica.state,
            planError: fingeringReplica.error,
            omittedHands: omittedHandsOf(motionState),
          });
    dom.handsStage.dataset.status = status;
    dom.handsStage.dataset.enabled = hands.enabled ? "on" : "off";
    if (hands.renderer) {
      const info = hands.renderer.getStatus();
      dom.handsStage.dataset.backend = info.backend || "";
      dom.handsStage.dataset.quality = info.effectiveQuality;
      dom.handsStage.dataset.camera = info.cameraMode;
      dom.handsStage.dataset.hands = Object.values(info.hands).join("+") || "none";
      dom.handsStage.dataset.poseWarnings = String(info.poseWarnings.length);
    }
    dom.handsStatus.dataset.state = status;
    const labels = {
      compatibility: "Compatibility",
      unavailable: "3D unavailable",
      recovering: "Recovering",
      idle: "Idle",
      "waiting-score": "Waiting",
      "no-plan": "No plan",
      unsupported: "Unsupported",
      partial: "Plan conflict",
      ready: "Live",
    };
    setText(dom.handsStatus, labels[status] || status);
    if (status === "partial") {
      setText(
        dom.handsSummary,
        "The song has fingering for one hand only — that hand is shown, music continues."
      );
    } else if (status === "compatibility") {
      setText(
        dom.handsSummary,
        "3D unavailable on this browser — the 2D views remain fully functional."
      );
    } else if (status === "ready" && hands.lastMotion) {
      setText(dom.handsSummary, "The hands follow the song's fingering.");
    } else if (status === "unsupported") {
      setText(dom.handsSummary, "This song does not support Hands. Choose another song; the other views remain available.");
    } else if (status === "no-plan" || status === "waiting-score") {
      setText(dom.handsSummary, "The hands appear once a song is playing.");
    } else if (!hands.enabled) {
      setText(dom.handsSummary, "3D hands are off.");
    }
  }

  function ensureHandsView(frame) {
    const score = scoreReplica.state === "ready" ? scoreReplica.score : null;
    const plan = fingeringReplica.state === "ready" ? fingeringReplica.plan : null;
    if (!score || !plan) {
      if (hands.view) {
        hands.view = null;
        hands.viewIdentity = null;
      }
      return;
    }
    const identity = motionViewIdentity(
      score.song_id,
      score.source_version,
      plan.plan_id
    );
    if (hands.viewIdentity === identity) return;
    hands.view = createMotionView({
      songId: score.song_id,
      sourceVersion: score.source_version,
      plan,
      geometry: hands.geometry,
    });
    hands.viewIdentity = identity;
  }

  function evaluateHandsForFrame(frame) {
    ensureHandsView(frame);
    if (!hands.view) return null;
    try {
      const tempoRate =
        isUsablePlaybackSnapshot(player.snapshot) &&
        player.snapshot.song_id === frame.songId
          ? player.snapshot.tempo_multiplier
          : 1;
      const motionState = evaluateMotion(hands.view, frame.position, tempoRate);
      return motionState;
    } catch {
      // Never let a presentation failure touch the rest of the UI.
      return null;
    }
  }

  function renderHands(frame, active = true) {
    if (!hands.enabled) return;
    // Hands view never presented yet: zero graphics work — the renderer is
    // initialized LAZILY on first presentation, so an unused view never
    // loads the GLB/WebGL stack or starves the poll loop.
    if (!active && !hands.renderer) {
      hands.lastMotion = hands.lastMotion ?? null;
      updateHandsDom(null);
      return;
    }
    const motionState = hands.available ? evaluateHandsForFrame(frame) : null;
    hands.lastMotion = motionState;
    if (!hands.renderer) {
      updateHandsDom(motionState);
      void ensureHandsInit();
      return;
    }
    // Hands view hidden: keep the cheap motion/status truth current, skip
    // the 3D paint and resize entirely (no background GPU work).
    if (!active) {
      updateHandsDom(motionState);
      return;
    }
    let info = null;
    if (hands.renderer.status === "ready") {
      resizeHandsRenderer();
      info = hands.renderer.render(motionState);
    }
    updateHandsDom(motionState);
    void info;
  }

  function resizeHandsRenderer() {
    if (!hands.renderer || hands.renderer.status !== "ready") return;
    const width = dom.handsStage.clientWidth;
    const height = dom.handsStage.clientHeight;
    if (width <= 0 || height <= 0) return;
    if (
      hands.lastResize &&
      hands.lastResize.width === width &&
      hands.lastResize.height === height
    ) {
      return;
    }
    hands.lastResize = { width, height };
    hands.renderer.resize(width, height);
  }

  function installDebugSurface() {
    // Bounded, read-only test/debug surface (same discipline as
    // __pianoHands): frozen primitive snapshots only.
    if (globalThis.__pianoUI) return;
    globalThis.__pianoUI = Object.freeze({
      selection: () => Object.freeze({
        selectedSongId: player.selectedSong ? player.selectedSong.song_id : null,
        selectedSource: selectionSource(player.selectedSong?.song_id).kind,
        selectionGeneration: player.selectionGeneration,
        metadataError: player.metadataError?.code ?? null,
        learningAuthorized: learning.authorized,
        diag: learning._diag ? { ...learning._diag } : null,
        learningStatus: learning.status,
        pollCount: globalThis.__pianoPollCount || 0,
        visibility: document.visibilityState,
        learningState: learning.authorized && learning.snapshot ? learning.snapshot.state : null,
        playbackState: lastPlayback ? lastPlayback.state : null,
        offline: ui.offline,
        controlsBusy: ui.controlsBusy,
      }),
    });
  }
  installDebugSurface();

  function installHandsDebugSurface() {
    // The ONLY test/debug surface: bounded, read-only, frozen snapshots.
    if (globalThis.__pianoHands) return;
    globalThis.__pianoHands = Object.freeze({
      info: () => {
        if (!hands.renderer) return null;
        return Object.freeze({
          ...hands.renderer.getStatus(),
          identity: hands.viewIdentity,
        });
      },
      motion: () => {
        if (!hands.lastMotion) return null;
        return Object.freeze(JSON.parse(JSON.stringify(hands.lastMotion)));
      },
    });
  }

  async function ensureHandsInit() {
    if (hands.initStarted || !hands.enabled || !hands.available) return;
    hands.initStarted = true;
    try {
      hands.renderer = new HandRenderer({
        canvas: dom.handsCanvas,
        geometry: hands.geometry,
      });
      await hands.renderer.initialize();
      // Tier-A is optional at runtime: loading/validation failure is recorded
      // and the permanent procedural rig remains active for both hands.
      await hands.renderer.loadTierAAsset();
      hands.renderer.setCameraMode(hands.camera);
      hands.renderer.setQuality(hands.quality);
      installHandsDebugSurface();
      resizeHandsRenderer();
    } catch (error) {
      hands.renderer = null;
      if (error instanceof RendererUnavailableError) {
        hands.available = false;
      }
    }
    updateHandsDom(hands.lastMotion);
    ensureVisualAnimation();
  }

  function syncHandsControls() {
    dom.handsCameraFull.setAttribute("aria-pressed", String(hands.camera === "FULL"));
    dom.handsCameraFollow.setAttribute(
      "aria-pressed",
      String(hands.camera === "FOLLOW")
    );
    dom.handsQuality.value = hands.quality;
    if (dom.handsEnabled) dom.handsEnabled.checked = hands.enabled;
    // U8-P5: Settings mirrors the same local preferences.
    dom.settingsHandsCameraFull.setAttribute("aria-pressed", String(hands.camera === "FULL"));
    dom.settingsHandsCameraFollow.setAttribute("aria-pressed", String(hands.camera === "FOLLOW"));
    dom.settingsHandsQuality.value = hands.quality;
    dom.settingsHandsEnabled.checked = hands.enabled;
    show(dom.handsStage, hands.enabled);
    workspace?.refresh();
  }

  /* U8-P5: one applier per local preference — the accepted hands-card
   * controls and the Settings mirrors share these; one change stores the
   * preference once and syncs every mirror. */
  function applyHandsEnabled(on) {
    hands.enabled = on;
    storeHandsPreference(
      globalThis.localStorage,
      HANDS_STORAGE_KEYS.enabled,
      on ? "on" : "off"
    );
    if (on) {
      hands.available = true;
      hands.initStarted = false;
    }
    syncHandsControls();
    updateHandsDom(hands.lastMotion);
    ensureVisualAnimation();
  }

  function applyHandsCamera(mode) {
    hands.camera = nextHandsCamera(hands.camera, mode);
    hands.renderer?.setCameraMode(hands.camera);
    storeHandsPreference(globalThis.localStorage, HANDS_STORAGE_KEYS.camera, hands.camera);
    syncHandsControls();
    ensureVisualAnimation();
  }

  function applyHandsQuality(value) {
    const previousQuality = hands.quality;
    hands.quality = nextHandsQuality(hands.quality, value);
    const renderer = hands.renderer;
    renderer?.setQuality(hands.quality);
    // COMPATIBILITY disposes the GPU stack. A supported quality must
    // resume that same renderer through its existing recovery contract.
    // Serialize rapid preference changes and apply the latest preference.
    if (renderer && previousQuality === "COMPATIBILITY" &&
        hands.quality !== "COMPATIBILITY" && !hands.qualityRecovery) {
      // Three.js disposes its backend asynchronously and loses the old
      // context. Reusing that canvas would race the new initialization.
      const freshCanvas = renderer.canvas.cloneNode(false);
      renderer.canvas.replaceWith(freshCanvas);
      renderer.canvas = freshCanvas;
      dom.handsCanvas = freshCanvas;
      hands.lastResize = null;
      const recovery = renderer.recover();
      hands.qualityRecovery = recovery;
      void recovery.then(() => {
        if (hands.renderer !== renderer) return;
        renderer.setCameraMode(hands.camera);
        renderer.setQuality(hands.quality);
        hands.lastResize = null;
        resizeHandsRenderer();
        updateHandsDom(hands.lastMotion);
        ensureVisualAnimation();
      }).catch(() => {
        if (hands.renderer !== renderer) return;
        hands.available = false;
        updateHandsDom(hands.lastMotion);
      }).finally(() => {
        if (hands.qualityRecovery === recovery) hands.qualityRecovery = null;
      });
    }
    storeHandsPreference(globalThis.localStorage, HANDS_STORAGE_KEYS.quality, hands.quality);
    updateHandsDom(hands.lastMotion);
    ensureVisualAnimation();
  }

  dom.handsEnabled?.addEventListener("change", () => {
    applyHandsEnabled(dom.handsEnabled.checked);
  });
  dom.handsCameraFull.addEventListener("click", () => applyHandsCamera("FULL"));
  dom.handsCameraFollow.addEventListener("click", () => applyHandsCamera("FOLLOW"));
  dom.handsQuality.addEventListener("change", () => {
    applyHandsQuality(dom.handsQuality.value);
  });
  dom.settingsHandsEnabled.addEventListener("change", () => {
    applyHandsEnabled(dom.settingsHandsEnabled.checked);
  });
  dom.settingsHandsCameraFull.addEventListener("click", () => applyHandsCamera("FULL"));
  dom.settingsHandsCameraFollow.addEventListener("click", () => applyHandsCamera("FOLLOW"));
  dom.settingsHandsQuality.addEventListener("change", () => {
    applyHandsQuality(dom.settingsHandsQuality.value);
  });
  if (typeof ResizeObserver === "function") {
    new ResizeObserver(() => {
      resizeHandsRenderer();
      ensureVisualAnimation();
    }).observe(dom.workspaceScene);
  }

  /* ---------------- immersive visual workspace (PIANO-IMMERSIVE-UI) ------
   * Presentation-only view selection over the SINGLE renderer instances.
   * Switching views never issues a musical command, never restarts
   * playback/practice, and never sends a value outside the accepted
   * sheet/falling/hybrid renderer-mode contract. Inactive views stop
   * painting (renderVisualStage gates on activeRenderer) while polls and
   * musical engines keep running. */

  const workspaceReady = createWorkspaceController({
    scene: dom.workspaceScene,
    slots: {
      learn: dom.sceneSlotLearn,
      piano: dom.sceneSlotPiano,
    },
    selects: [dom.viewSelectLearn],
    views: { notes: $("visual"), piano: dom.pianoView, hands: $("hands") },
    isHandsEnabled: () => hands.enabled,
    onPresentChange: ({ view, renderer }) => {
      // The workspace view is authoritative for the notes scene: the Sheet
      // view pins the sheet renderer mode; the Falling view presents the
      // falling mode (or the configured hybrid, which never leaves the
      // accepted contract). `view` comes from the controller — the
      // controller itself is still initializing during this first call.
      if (renderer === "visual") {
        const wanted = view === "sheet" ? "sheet" : visualMode === "hybrid" ? "hybrid" : "falling";
        if (visualMode !== wanted) setVisualMode(wanted);
      }
      if (renderer === "piano" && lastLiveReplica) {
        keyboardRenderer.render(lastLiveReplica.snapshot, lastLiveReplica.status);
      }
      const studioView = document.getElementById("studio-visual");
      if (studioView) {
        studioView.value = renderer === "visual" ? visualMode : view;
        studioView.querySelector('option[value="hands"]').disabled = !hands.enabled;
      }
      const notationNote = document.getElementById("studio-notation-note");
      if (notationNote) notationNote.hidden = renderer !== "visual" || !["sheet", "hybrid"].includes(visualMode);
      ensureVisualAnimation();
    },
  });
  workspace = workspaceReady;
  const connectionHome = dom.connectionNote.parentElement;
  const connectionDetails = document.querySelector(".shell-connection-details");
  const detailsHome = connectionDetails.parentElement;
  function presentWorkspaceRoute(route) {
    workspace.setRoute(route);
    const surface = { player: $("player"), learn: $("practice"), piano: $("piano-status") }[route];
    const connectionSlot = route === "learn" && dom.practiceCard.hidden
      ? $("ui008-learn-empty")
      : surface?.querySelector(".stage-head-copy") ?? connectionHome;
    connectionSlot.append(dom.connectionNote);
    const panel = { player: $("ui008-player-secondary"), learn: $("ui008-learn-sheet-content"), piano: $("ui008-piano-sheet-content") }[route];
    (panel ?? detailsHome).append(connectionDetails);
  }
  window.addEventListener("piano-ui-route-change", (event) => {
    const route = event.detail?.route ?? "home";
    if (studio?.isOpen() && route !== "learn") { shell.router.navigate("learn"); return; }
    presentWorkspaceRoute(route);
  });
  // The shell router started before this controller existed: adopt the
  // initial route so a deep link (e.g. #/learn) presents the right slot.
  presentWorkspaceRoute(shell.router.current() ?? "home");
  // One observer for the empty/active lesson boundary; connection errors
  // remain visible even before a song has been selected.
  new MutationObserver(() => {
    if (shell.router.current() === "learn") presentWorkspaceRoute("learn");
  }).observe(dom.practiceCard, { attributes: true, attributeFilter: ["hidden"] });
  syncHandsControls();

  /* ---------------- rendering helpers (safe DOM only) ---------------- */

  function setText(el, text) {
    if (el === dom.playerFreshness) el.hidden = text === "Synced with Core";
    el.textContent = text === null || text === undefined ? "" : String(text);
  }

  function show(el, on) {
    el.hidden = !on;
  }

  function setPill(pill, label, value, tone) {
    pill.dataset.tone = tone;
    pill.querySelector(".pill-value").textContent = value;
    pill.setAttribute("aria-label", `${label}: ${value}`);
  }

  function toast(message) {
    setText(dom.toast, message);
    show(dom.toast, true);
    clearTimeout(ui.toastTimer);
    ui.toastTimer = setTimeout(() => show(dom.toast, false), 4000);
  }

  function setControls(snapshot) {
    const fresh = player.fresh && !ui.offline;
    const enable = controlEnablement(snapshot, fresh);
    // Learning controls are offered only while the Learning replica is
    // authorized (fresh); staleness disables mutating controls (U4-F3).
    const practice = learning.authorized
      ? practiceControlEnablement(lastPractice, snapshot)
      : { start: false, stop: false };
    const terminal = !!snapshot && ["IDLE", "COMPLETED", "FAILED"].includes(snapshot.state);
    const tempoEnabled = !ui.controlsBusy && fresh && terminal;
    dom.tempo.disabled = !tempoEnabled;
    for (const preset of document.querySelectorAll(".tempo-preset")) {
      preset.disabled = !tempoEnabled;
    }
    // Presentation follows the last authoritative state even during a
    // pending command/reconnect; enablement below still fails closed.
    const practicing = lastPractice?.state === "WAITING";
    dom.btnPractice.hidden = practicing;
    dom.btnPracticeStop.hidden = !practicing;
    dom.play.hidden = ["PLAYING", "PAUSED", "STOPPING"].includes(snapshot?.state);
    dom.pause.hidden = snapshot?.state !== "PLAYING";
    dom.resume.hidden = snapshot?.state !== "PAUSED";
    // Pause/Resume are the primary action while playback owns the piano.
    dom.pause.classList.toggle("transport-btn--primary", snapshot?.state === "PLAYING");
    dom.resume.classList.toggle("transport-btn--primary", snapshot?.state === "PAUSED");
    if (ui.controlsBusy) {
      // keep everything disabled while a command runs
      dom.play.disabled = true;
      dom.pause.disabled = true;
      dom.resume.disabled = true;
      dom.stop.disabled = true;
      dom.btnPractice.disabled = true;
      dom.btnPracticeStop.disabled = true;
      dom.btnLearningRestartTarget.disabled = true;
      dom.btnLearningRestartSection.disabled = true;
      return;
    }
    // Mutual exclusion (PIANO-P015): while practice is WAITING the song
    // cannot be played (the appliance would reject it with a 409).
    dom.play.disabled =
      !(enable.play && player.selectedGoldSong() !== null) || practice.stop;
    dom.pause.disabled = !enable.pause;
    dom.resume.disabled = !enable.resume;
    dom.stop.disabled = !enable.stop;
    dom.btnPractice.disabled = !(practice.start && player.selectedSong !== null);
    dom.btnPracticeStop.disabled = !practice.stop;
    dom.btnLearningRestartTarget.disabled = !practice.stop;
    dom.btnLearningRestartSection.disabled = !practice.stop;
  }

  function renderSelectedSong(record, intent = null) {
    if (!player.selectSong(record, intent)) return false;
    // Selection changes the Learn lesson model (ready stage): re-render the
    // lesson presentation immediately from authoritative state instead of
    // waiting for the next learning poll cycle. No commands are sent.
    renderPractice(learningAuthorizedSession(), { stale: false });
    show(dom.selected, true);
    setText(dom.selTitle, record.title || record.filename || record.song_id);
    setText(dom.selFile, record.filename || "—");
    setText(dom.selDuration, formatDuration(record.duration_seconds));
    setText(
      dom.selFormat,
      record.midi_format !== null && record.midi_format !== undefined
        ? `Format ${record.midi_format}`
        : "—"
    );
    setText(dom.selTracks, record.track_count ?? "—");
    setText(dom.selStatus, record.status || "—");
    return true;
  }

  async function hydrateActiveSong(songId) {
    const ticket = player.beginHydration(songId);
    if (!ticket) return;
    try {
      const record = await api.song(songId, { signal: ticket.signal });
      if (record?.song_id !== songId || selectionSource(record.song_id).kind !== "gold") {
        throw new ApiError(502, "invalid_response", "Song metadata does not match the requested song.");
      }
      if (player.acceptHydration(record, ticket)) renderPlaybackMetadata();
    } catch (error) {
      // Metadata failure is visible, but never changes Core transport truth.
      if (player.rejectHydration(error, ticket)) showPlayerError(error.code || "network_error", error.message);
    } finally {
      player.finishHydration(ticket);
    }
  }

  function playerStateLabel(state) {
    const labels = {
      unloaded: "No song",
      loading: "Working…",
      ready: "Stopped",
      playing: "Playing",
      paused: "Paused",
      stopping: "Stopping…",
      completed: "Completed",
      error: "Playback error",
      unavailable: "Unavailable",
    };
    return labels[state] || "Unavailable";
  }

  function renderProgress(frame) {
    const pct = Math.round(frame.progress * 1000) / 10;
    dom.playerProgressFill.style.transform = `scaleX(${frame.progress})`;
    dom.playerProgress.setAttribute("aria-valuenow", String(Math.round(pct)));
    setText(dom.playerPosition, formatDuration(frame.position));
    setText(dom.playerRemaining, `−${formatDuration(frame.remaining)}`);
    setText($("player-duration"), formatDuration(frame.position + frame.remaining));
  }

  function stopVisualClock() {
    if (ui.playbackFrame !== null) {
      cancelAnimationFrame(ui.playbackFrame);
      ui.playbackFrame = null;
    }
  }

  function stopFreshnessTimer() {
    if (ui.freshnessTimer !== null) {
      clearTimeout(ui.freshnessTimer);
      ui.freshnessTimer = null;
    }
  }

  function freezePlaybackPresentation(nowMs = performance.now()) {
    if (ui.frozenPlaybackFrame === null) {
      ui.frozenPlaybackFrame = player.currentFrame(nowMs);
    }
    stopVisualClock();
    stopFreshnessTimer();
    renderProgress(ui.frozenPlaybackFrame);
  }

  function markPlaybackStale(message = "Core state is stale — controls paused") {
    library.setPlayback(lastPlayback, false);
    dom.playerState.dataset.state = "unavailable";
    setText(dom.playerState, "State stale");
    setText(dom.playerFreshness, message);
    setPill(dom.pills.playback, "Playback", "stale", "bad");
    setControls(lastPlayback);
    // The visual must freeze on labeled stale truth immediately — a
    // pending rAF must not be the only re-render path.
    try {
      renderVisualStage(performance.now());
    } catch {
      // A visual rendering problem never affects control state.
    }
  }

  function evaluatePlaybackFreshness(nowMs = performance.now()) {
    const wasFresh = player.fresh;
    const fresh = player.evaluateFreshness(nowMs);
    if (wasFresh && !fresh) {
      freezePlaybackPresentation(nowMs);
      markPlaybackStale();
    }
    return fresh;
  }

  function scheduleFreshnessExpiry(nowMs = performance.now()) {
    stopFreshnessTimer();
    if (!player.fresh || !player.anchor) return;
    const expiresAt = player.anchor.receivedAtMs + player.freshnessMs;
    const delay = Math.max(1, expiresAt - nowMs + 1);
    ui.freshnessTimer = setTimeout(() => {
      ui.freshnessTimer = null;
      evaluatePlaybackFreshness(performance.now());
    }, delay);
  }

  function tickVisualClock() {
    ui.playbackFrame = null;
    if (!player.anchor || !lastPlayback) return;
    const nowMs = performance.now();
    if (!evaluatePlaybackFreshness(nowMs) || player.commandInFlight || ui.offline) {
      freezePlaybackPresentation(nowMs);
      return;
    }
    const frame = player.currentFrame(nowMs);
    renderProgress(frame);
    if (frame.stale) {
      evaluatePlaybackFreshness(nowMs);
      return;
    }
    if (lastPlayback.state === "PLAYING" && frame.progress < 1) {
      ui.playbackFrame = requestAnimationFrame(tickVisualClock);
    }
  }

  function renderPlaybackMetadata(snapshot = player.snapshot) {
    if (snapshot && snapshot.song_id) {
      setText(dom.playerTitle, snapshot.title || snapshot.song_id);
      const parts = [];
      if (snapshot.tempo_multiplier !== null && snapshot.tempo_multiplier !== undefined) {
        parts.push(`start tempo ${formatTempo(snapshot.tempo_multiplier)}`);
      }
      if (snapshot.duration_seconds) parts.push(formatDuration(snapshot.duration_seconds));
      const activeMetadata = player.activeSongMetadata;
      if (activeMetadata && activeMetadata.song_id === snapshot.song_id) {
        if (activeMetadata.track_count) {
          parts.push(`${activeMetadata.track_count} track${activeMetadata.track_count === 1 ? "" : "s"}`);
        }
        if (activeMetadata.tempo_bpm_first) {
          parts.push(`${Math.round(activeMetadata.tempo_bpm_first)} BPM source`);
        }
      }
      setText(dom.playerMeta, parts.join(" · "));
      hydrateActiveSong(snapshot.song_id);
    } else if (player.selectedGoldSong()) {
      const selected = player.selectedGoldSong();
      setText(dom.playerTitle, selected.title || selected.filename || selected.song_id);
      setText(dom.playerMeta, "Ready to play from the beginning.");
    } else {
      setText(dom.playerTitle, "No song selected");
      setText(dom.playerMeta, "Choose a song from your library.");
    }
  }

  function renderPlayback(receivedAtMs = performance.now()) {
    const snapshot = player.snapshot;
    lastPlayback = snapshot;
    library.setPlayback(snapshot, player.fresh && !ui.offline);
    renderPlaybackMetadata(snapshot);
    stopVisualClock();

    // Authoritative unavailable playback is synced truth, not an error:
    // show Unavailable with a fresh pill while every other subsystem keeps
    // rendering from the same poll cycle.
    if (
      snapshot &&
      classifyPlaybackPayload(snapshot) === PLAYBACK_PAYLOAD_CLASS.VALID_UNAVAILABLE
    ) {
      stopFreshnessTimer();
      setPill(dom.pills.playback, "Playback", "unavailable", statusTone("playback", "unavailable"));
      dom.playerState.dataset.state = "unavailable";
      setText(dom.playerState, "Unavailable");
      setText(dom.playerFreshness, "Synced with Core");
      setControls(snapshot);
      return;
    }

    const hasAuthority = player.hasCurrentAuthority(receivedAtMs) && !ui.offline;
    if (!hasAuthority) {
      stopFreshnessTimer();
      if (ui.frozenPlaybackFrame === null) {
        ui.frozenPlaybackFrame = player.currentFrame(receivedAtMs);
      }
      renderProgress(ui.frozenPlaybackFrame);
      setControls(snapshot);
      return;
    }

    ui.frozenPlaybackFrame = null;
    scheduleFreshnessExpiry(receivedAtMs);
    const state = playerPresentationState(
      snapshot,
      player.selectedGoldSong() !== null,
      true,
      ui.controlsBusy
    );
    const summary = playbackSummary(snapshot);
    setPill(dom.pills.playback, "Playback", summary, statusTone("playback", summary));
    dom.playerState.dataset.state = state;
    setText(dom.playerState, playerStateLabel(state));
    setText(dom.playerFreshness, snapshot ? "Synced with Core" : "Waiting for Core");

    const frame = player.currentFrame(receivedAtMs);
    renderProgress(frame);
    if (snapshot && snapshot.state === "FAILED") {
      showPlayerError("song_unplayable", "Playback failed.");
    }
    if (snapshot && snapshot.state === "PLAYING") {
      ui.playbackFrame = requestAnimationFrame(tickVisualClock);
    }
    setControls(snapshot);
  }

  function renderHealth(health, reconnected = false) {
    lastHealth = health;
    const app = health.application ? health.application.state : null;
    setPill(dom.pills.app, "System", app || "unknown", statusTone("app", app));
    const midi = health.midi_input ? health.midi_input.connection : null;
    setPill(dom.pills.midi, "MIDI in", midi || "unknown", statusTone("midi", midi));
    const lib = health.library || {};
    const libValue = lib.ready === true ? (lib.entries ?? 0).toLocaleString() : "unavailable";
    setPill(dom.pills.library, "Library", libValue, statusTone("library", lib.ready));
    if (lib.ready === true) {
      ui.libraryTotal = lib.entries ?? null;
      setText(
        dom.librarySummary,
        `${(lib.entries ?? 0).toLocaleString()} songs in the library.`
      );
    } else {
      setText(dom.librarySummary, "Library is unavailable right now.");
    }
    dom.connectionNote.textContent = reconnected ? "Reconnected — everything is back in sync." : "Connected";
    if (reconnected) void refreshDeviceIdentity();
  }

  /** One-shot cached device identity read (GET /device is startup-cached
   * on the appliance). Re-fetched only after a full reconnect. */
  async function refreshDeviceIdentity() {
    try {
      lastDevice = await api.device();
    } catch {
      lastDevice = null; // honest absence; identity stays unpublished
    }
    renderPianoAndSettings();
  }

  function renderPhysical(state) {
    lastPhysical = state;
    // U8-P5: the Piano surface + Settings derive from one consumer model.
    renderPianoAndSettings();
  }

  /* U8-P5: premium Piano / Device + Settings presentation. Pure consumer
   * models over the SAME authoritative inputs as the accepted rendering
   * above (health, physical state, LED snapshot, device identity). No
   * commands, no musical semantics, no correctness inference — the LED
   * and pressed keys never decide learning truth here. */
  function renderPianoAndSettings({ coreOffline = ui.offline } = {}) {
    const connection = pianoConnectionModel({
      midiConnection: lastHealth?.midi_input?.connection ?? null,
      coreOffline,
    });
    if (dom.pianoState) {
      setText(dom.pianoState, connection.chip.label);
      dom.pianoState.dataset.state = connection.chip.tone;
    }
    setText(dom.pianoGuidance, connection.guidance);
    const identity = pianoIdentityModel(lastDevice);
    if (identity) {
      // Name and display code render as separate lines so the code never
      // wraps mid-token at tablet width (P6-C). Each span is set
      // individually — the parent text is never replaced wholesale.
      setText(dom.pianoIdentityName, identity.name);
      setText(dom.pianoIdentityCode, identity.code ?? "");
      show(dom.pianoIdentityCode, !!identity.code);
    }
    const activity = pianoActivityModel(lastPhysical);
    setText(dom.pianoNotes, activity.keysHeld.summary);
    setText(dom.pianoSustain, activity.sustain.summary);
    setText(dom.pianoLast, activity.lastKey.summary);
    const led = pianoLedModel(lastLed === undefined ? null : lastLed);
    if (dom.ledChip) {
      setText(dom.ledChip, led.chip.label);
      dom.ledChip.dataset.state = led.chip.tone;
    }
    if (dom.pianoLedNote) {
      setText(dom.pianoLedNote, led.state === "ready" ? "" : led.guidance);
      show(dom.pianoLedNote, led.state !== "ready" && !!led.guidance);
    }
    renderSettingsSurface({ connection, led, coreOffline });
  }

  function renderSettingsSurface({ connection, led, coreOffline }) {
    if (dom.settingsLedChip) {
      setText(dom.settingsLedChip, led.chip.label);
      dom.settingsLedChip.dataset.state = led.chip.tone;
    }
    setText(dom.settingsLedGuidance, led.guidance);
    if (dom.settingsAboutRows) {
      const model = settingsModel({
        visualMode: null,
        hands: null,
        led: lastLed === undefined ? null : lastLed,
        device: lastDevice,
        midiConnection: lastHealth?.midi_input?.connection ?? null,
        coreOffline,
      });
      const about = model.groups.find((group) => group.id === "about");
      dom.settingsAboutRows.replaceChildren(
        ...(about?.rows ?? []).flatMap((row) => {
          const dt = document.createElement("dt");
          dt.textContent = row.label;
          const dd = document.createElement("dd");
          dd.textContent = row.value;
          return [dt, dd];
        })
      );
    }
    // Technical identity stays behind the ALREADY-AUTHORIZED disclosure.
    const tech = pianoTechRows({
      device: lastDevice,
      led: lastLed === undefined ? null : lastLed,
      midiConnection: lastHealth?.midi_input?.connection ?? null,
    });
    setPill(
      dom.pillDevice,
      "Device ID",
      tech.find((row) => row.label === "Device ID")?.value ?? "unavailable",
      "warn"
    );
    setPill(
      dom.pillLedTransport,
      "LED transport",
      tech.find((row) => row.label === "LED transport")?.value ?? "—",
      "warn"
    );
    void connection;
  }

  function setLedControls(state) {
    const enable = ledControlEnablement(state);
    dom.ledEnabled.disabled = !enable.toggle;
    dom.ledMode.disabled = !enable.mode;
    dom.ledBrightness.disabled = !enable.brightness;
    dom.btnLedClear.disabled = !enable.clear;
  }

  function renderLed(state) {
    if (state === null || state === undefined) {
      // A failed LED poll must never masquerade as "not configured".
      setPill(dom.pills.led, "LED", "unreachable", "bad");
      setText(dom.ledStatus, "Keyboard lights are temporarily unavailable.");
      renderPianoAndSettings();
      return;
    }
    if (state.available === true || state.enabled === false) {
      lastLed = state;
    }
    const summary = ledSummary(state);
    setPill(dom.pills.led, "LED", summary, ledStatusTone(state));
    // A fresh canonical snapshot supersedes a stale command error: the
    // authoritative state is back on screen, so the message is retired.
    show(dom.ledError, false);
    // Consumer status line (U8-P5): calm wording only — transport
    // endpoints and raw error text stay behind the technical disclosure.
    const led = pianoLedModel(lastLed === undefined ? null : lastLed);
    const bits = [];
    if (led.state === "ready" && state.active_note_count > 0) {
      bits.push(`${state.active_note_count} key${state.active_note_count === 1 ? "" : "s"} lit right now ·`);
    }
    if (led.state !== "unknown") bits.push(led.guidance);
    setText(dom.ledStatus, bits.join(" "));
    // Reflect canonical state, but never stomp an in-flight user edit.
    if (!ui.ledBusy) {
      dom.ledEnabled.checked = state.enabled === true;
      if (state.mode) dom.ledMode.value = state.mode;
      const b = clampBrightness(state.brightness ?? LED_BRIGHTNESS_MAX);
      dom.ledBrightness.value = String(b);
      setText(dom.ledBrightnessValue, `${b}%`);
    }
    setLedControls(state);
    syncLedQuick(state);
    renderPianoAndSettings();
  }

  function showLedError(code, fallback) {
    setText(dom.ledError, errorText(code, fallback));
    show(dom.ledError, true);
  }

  /* ---------------- practice / learning (PIANO-P015, PIANO-UI-004) ---------------- */

  /** Apply the Learning replica to the DOM. Only coordinator-accepted
   * snapshots reach here; `stale` de-authorizes target presentation
   * without touching Player/Core/LiveNote (U4-F3). */
  function renderLearning(replica) {
    const stale = !!replica && replica.status === "stale";
    renderPractice(learningAuthorizedSession(), { stale });
    studio?.update(learningAuthorizedSession(), stale);
    ensureVisualAnimation();
  }

  /* U8-P4: premium Learn presentation. Derives ONE view model from the
   * same authoritative inputs as the accepted rendering above and updates
   * the premium elements (state chip, guidance, progress text, target
   * chips, LED note, start region, empty state). It never touches musical
   * state and never decides correctness — accepted_notes/latest_outcome
   * come straight from Core. */
  function renderLearnPresentation(model) {
    if (dom.ui008LearnState) {
      setText(dom.ui008LearnState, model.chip.label);
      dom.ui008LearnState.dataset.state = model.stage === "stale" ? "wait" : model.chip.tone;
    }
    setText(dom.ui008LearnGuidance, model.guidance);
    setText(dom.ui008LearnProgress, model.progress.text);
    // Target chips: rebuild only on target/accepted identity change so one
    // Core update yields exactly one DOM transition (P4-Z).
    const chipsKey = model.targetId === null
      ? ""
      : `${model.targetId}|${model.chips.map((c) => (c.accepted ? "a" : "r")).join("")}`;
    if (ui.lastLearnChipsKey !== chipsKey) {
      ui.lastLearnChipsKey = chipsKey;
      dom.ui008LearnTargetNotes.replaceChildren(
        ...model.chips.map((chip) => {
          const el = document.createElement("span");
          el.className = `learn-note-chip${chip.accepted ? " learn-note-chip--accepted" : ""}${
            model.chord ? " learn-note-chip--chord" : ""
          }`;
          el.textContent = chip.name;
          return el;
        })
      );
      dom.ui008LearnTarget.hidden = model.chips.length === 0;
    }
    show(dom.ui008LearnStartRegion, model.stage === "ready" || model.stage === "conflict");
    setText(dom.ui008LearnLed, model.ledGuidance);
    show(dom.ui008LearnLed, !!model.ledGuidance);
    // Empty state: honest heading/note for no-song vs unavailable truth.
    const emptyVisible = model.stage === "no-song" || model.stage === "unavailable";
    if (emptyVisible) {
      const unavailable = model.stage === "unavailable";
      setText(
        dom.ui008LearnEmptyHeading,
        unavailable ? "Learning is unavailable" : "Choose a song to begin"
      );
      setText(dom.ui008LearnEmptyNote, model.guidance);
    }
    return emptyVisible;
  }

  function renderPractice(session, { stale = false } = {}) {
    if (session?.state === "WAITING" && session.song_id && !session.song_id.startsWith("package:")) document.body.dataset.learnEntry = "library-song";
    if (session) lastPractice = session;
    const learnModel = learnLessonModel({
      session: stale ? null : session,
      stale,
      selectedSong: player.selectedSong,
      playbackSnapshot: lastPlayback,
      led: lastLed,
    });
    if (stale) {
      // Explicit Learning stale/unavailable status: no target, no
      // expected overlay, no feedback, mutating controls disabled.
      setPill(dom.pills.practice, "Practice", "stale", "bad");
      show(dom.practiceCard, true);
      setText(dom.practiceSong, lastPractice?.title || lastPractice?.song_id || "");
      setText(dom.practiceStatus, "Learning state is stale — waiting for Piano Core");
      setText(dom.learningModeActive, "—");
      setText(dom.learningSectionActive, "—");
      setText(dom.learningRepetitionActive, "—");
      setText(dom.learningFeedback, "—");
      setText(dom.practiceStep, "");
      setText(dom.practiceTarget, "");
      dom.practiceBarFill.style.width = "0%";
      dom.practiceBar.setAttribute("aria-valuenow", "0");
      show(dom.practiceCompletion, false);
      setText(dom.practiceNote, "");
      show(dom.practiceNote, false);
      setText(dom.practiceError, "");
      show(dom.practiceError, false);
      renderLearnPresentation(learnModel);
      keyboardRenderer.renderExpected([]);
      setControls(lastPlayback);
      return;
    }
    const view = practiceView(session);
    setPill(
      dom.pills.practice,
      "Practice",
      practiceSummary(session),
      practiceStatusTone(session)
    );
    // U8-P4: the lesson card also presents the READY and mutex-conflict
    // states (selected song, no session yet) so Learn has one focused
    // start experience; learn-empty remains only for no-song/unavailable.
    const showCard = view.showPanel || learnModel.stage === "ready" || learnModel.stage === "conflict";
    show(dom.practiceCard, showCard);
    renderLearnPresentation(learnModel);
    if (showCard) {
      setText(dom.practiceSong, learnModel.title);
      setText(dom.practiceStatus, view.statusText);
      // U8P4-N2 (U8-P5): consumer mode wording — never the raw enum.
      setText(dom.learningModeActive, session ? learnModel.modeLabel : "—");
      setText(dom.learningSectionActive, session ? view.sectionText : "—");
      setText(dom.learningRepetitionActive, view.repetitionText || "—");
      setText(dom.learningFeedback, view.feedbackText || "—");
      setText(dom.practiceStep, view.stepText);
      setText(dom.practiceTarget, view.targetText);
      dom.practiceBarFill.style.width = `${view.progressPct}%`;
      dom.practiceBar.setAttribute("aria-valuenow", String(view.progressPct));
      setText(dom.practiceCompletion, view.completionText);
      show(dom.practiceCompletion, !!view.completionText);
      const notes = [];
      if (view.midiDegraded) {
        notes.push("MIDI input is not connected — practice cannot progress.");
      }
      if (lastLed && isLedConfigured(lastLed) && lastLed.available !== true) {
        notes.push("LED targets are unavailable — follow the note names above.");
      }
      setText(dom.practiceNote, notes.join(" "));
      show(dom.practiceNote, notes.length > 0);
      setText(dom.practiceError, view.errorText || practiceCommandError || "");
      show(dom.practiceError, !!(view.errorText || practiceCommandError));
    }
    keyboardRenderer.renderExpected(
      view.active && session?.target ? session.target.notes : []
    );
    setControls(lastPlayback);
  }

  function showPracticeError(code, fallback) {
    dom.practiceError.dataset.errorCode = code;
    dom.practiceError.dataset.selectionError = "false";
    practiceCommandError = errorText(code, fallback);
    setText(dom.practiceError, practiceCommandError);
    show(dom.practiceError, true);
  }

  function showLearnSelectionError(code, fallback) {
    const feedback = $("academy-selection-error");
    feedback.dataset.errorCode = code;
    setText(feedback, errorText(code, fallback));
    show(feedback, true);
    // Academy and Studio have different visible ancestors. Keep the same
    // typed failure on the active surface, without changing selection truth.
    showPracticeError(code, fallback);
    dom.practiceError.dataset.selectionError = "true";
  }

  function clearLearnSelectionError() {
    show($("academy-selection-error"), false);
    if (dom.practiceError.dataset.selectionError === "true") {
      practiceCommandError = null;
      show(dom.practiceError, false);
      dom.practiceError.dataset.selectionError = "false";
    }
  }

  async function runLearnLessonStart() {
    if (!learnLesson || ui.controlsBusy || player.selectedSong?.song_id !== `package:${learnLesson.packageId}` || player.selectedSong?.lesson_id !== learnLesson.lessonId) return;
    ui.controlsBusy = true;
    setControls(lastPlayback);
    practiceCommandError = null;
    show(dom.practiceError, false);
    const commandTicket = learning.beginCommand();
    try {
      const config = learningStartConfig({
        packageId: learnLesson.packageId,
        hand: studio?.isOpen() ? document.getElementById("studio-hand").value || null : null,
        mode: dom.learningMode.value,
        speed: studio?.isOpen() ? document.getElementById("studio-speed").value : 1,
        sectionStart: dom.learningSectionStart.value,
        sectionEnd: dom.learningSectionEnd.value,
        requiredRepetitions: dom.learningRepetitions.value,
      });
      const session = await api.learningStart(config);
      learning.acceptCommandResult(session, commandTicket);
      return true;

    } catch (error) {
      if (error instanceof TypeError) {
        showPracticeError("invalid_learning_configuration", error.message);
        return;
      }
      if (error instanceof ApiError) {
        showPracticeError(error.code, error.message);
        if (error.status === 409) toast(errorText(error.code));
      } else {
        goOffline();
        showPracticeError("network_error");
      }
    } finally {
      ui.controlsBusy = false;
      setControls(lastPlayback);
      poll();
    }
  }

  async function runPracticeStart() {
    if (learnLesson) {
      await runLearnLessonStart();
      return;
    }
    if (!player.selectedGoldSong()) {
      showPracticeError("invalid_song_id");
      return;
    }
    ui.controlsBusy = true;
    setControls(lastPlayback);
    show(dom.practiceError, false);
    // Mutating Learning commands are ordering boundaries (U4-F3): any
    // poll begun before this command can never overwrite its result.
    const commandTicket = learning.beginCommand();
    try {
      const config = learningStartConfig({
        songId: player.selectedSong.song_id,
        mode: dom.learningMode.value,
        sectionStart: dom.learningSectionStart.value,
        sectionEnd: dom.learningSectionEnd.value,
        requiredRepetitions: dom.learningRepetitions.value,
      });
      const session = await api.learningStart(config);
      learning.acceptCommandResult(session, commandTicket);
      toast("Learning started — follow the expected keys.");
    } catch (error) {
      if (error instanceof TypeError) {
        showPracticeError("invalid_learning_configuration", error.message);
        return;
      }
      if (error instanceof ApiError) {
        showPracticeError(error.code, error.message);
        if (error.status === 409) toast(errorText(error.code));
      } else {
        goOffline();
        showPracticeError("network_error");
      }
    } finally {
      ui.controlsBusy = false;
      setControls(lastPlayback);
      poll();
    }
  }

  async function runPracticeStop() {
    ui.controlsBusy = true;
    setControls(lastPlayback);
    practiceCommandError = null;
    show(dom.practiceError, false);
    const commandTicket = learning.beginCommand();
    try {
      const session = await api.learningStop(lastPractice);
      learning.acceptCommandResult(session, commandTicket);
      toast("Learning stopped.");
    } catch (error) {
      if (error instanceof ApiError) {
        showPracticeError(error.code, error.message);
      } else {
        goOffline();
        showPracticeError("network_error");
      }
    } finally {
      ui.controlsBusy = false;
      setControls(lastPlayback);
      poll();
    }
  }

  async function runLearningRestart(section) {
    if (!learning.authorized || !lastPractice || lastPractice.state !== "WAITING") {
      return;
    }
    ui.controlsBusy = true;
    setControls(lastPlayback);
    practiceCommandError = null;
    show(dom.practiceError, false);
    const commandTicket = learning.beginCommand();
    try {
      const session = section
        ? await api.learningRestartSection(lastPractice)
        : await api.learningRestartTarget(lastPractice);
      learning.acceptCommandResult(session, commandTicket);
      toast(section ? "Learning section restarted." : "Learning target restarted.");
    } catch (error) {
      if (error instanceof ApiError) {
        showPracticeError(error.code, error.message);
        if (error.code === "stale_learning_command") void poll();
      } else {
        goOffline();
        showPracticeError("network_error");
      }
    } finally {
      ui.controlsBusy = false;
      setControls(lastPlayback);
    }
  }

  async function runLedUpdate(fields) {
    ui.ledBusy = true;
    setLedControls(null);
    show(dom.ledError, false);
    try {
      const state = await api.ledUpdate(fields);
      lastLed = state;
      renderLed(state);
    } catch (error) {
      if (error instanceof ApiError) {
        showLedError(error.code, error.message);
      } else {
        goOffline();
        showLedError("network_error");
      }
    } finally {
      ui.ledBusy = false;
      setLedControls(lastLed);
    }
  }

  function goOffline(message = "Connection lost — position frozen") {
    library.setPlayback(lastPlayback, false);
    freezePlaybackPresentation(performance.now());
    ui.offline = true;
    player.invalidateFreshness();
    // Learning presentation de-authorizes immediately on full Core
    // unreachability, isolated from Player/Core/LiveNote lifecycles.
    learning.invalidate();
    dom.playerState.dataset.state = "unavailable";
    setText(dom.playerState, "Core unavailable");
    setText(dom.playerFreshness, message);
    homeController.setOffline(true);
    setControls(lastPlayback);
    setPill(dom.pills.app, "System", "unreachable", "bad");
    setPill(dom.pills.midi, "MIDI in", "unreachable", "bad");
    setPill(dom.pills.library, "Library", "unreachable", "bad");
    setPill(dom.pills.playback, "Playback", "unreachable", "bad");
    setPill(dom.pills.practice, "Practice", "unreachable", "bad");
    setPill(dom.pills.led, "LED", "unreachable", "bad");
    setText(dom.connectionNote, "Cannot reach Piano Core — retrying…");
    renderPianoAndSettings({ coreOffline: true });
    // Visual truth de-authorizes with the Player: one frozen frame, no
    // extrapolation (goOffline already froze the playback anchor).
    ensureVisualAnimation();
  }

  /* ---------------- polling (bounded, overlap-safe) ---------------- */

  async function poll() {
    globalThis.__pianoPollCount = (globalThis.__pianoPollCount || 0) + 1;
    if (ui.pollBusy || document.visibilityState !== "visible") return;
    ui.pollBusy = true;
    try {
      const learningTicket = learning.beginPoll();
      const result = await player.runPoll(
        async () => {
          const [health, physical, led, practice] = await Promise.all([
            api.health(),
            api.state(),
            api.led().catch(() => null), // LED is auxiliary: never block the poll
            api.learning().catch(() => null), // learning is auxiliary too
          ]);
          return { health, physical, led, practice };
        },
        (value) => value.health.playback,
        () => performance.now()
      );
      if (result.status === "failed") {
        goOffline();
        return;
      }
      if (!result.accepted) return;
      const { health, physical, led, practice } = result.value;
      const reconnected = ui.offline;
      ui.offline = false;
      renderHealth(health, reconnected);
      if (reconnected && player.metadataError) player.invalidateHydration();
      renderPlayback();
      renderPhysical(physical);
      renderLed(led);
      // Home launch surface follows the same authoritative poll cycle.
      homeController.update({
        playback: health.playback ?? null,
        learning: practice ?? null,
        offline: false,
        connection: health.midi_input?.connection ?? null,
      });
      // Visual surfaces follow the same authoritative poll cycle: score
      // identity, learning overlay and (indirectly) player anchors.
      void ensureVisualScore();
      ensureVisualAnimation();
      if (practice === null || practice === undefined) {
        // Current-generation Learning GET failure: may age freshness,
        // never applies content (U4-F3). A null learning payload does
        // NOT mark Player/Core/LiveNote unavailable.
        learning.notePollFailure(learningTicket);
      } else if (!learning.acceptPoll(practice, learningTicket)) {
        // Stale success: ignored — the command boundary decides.
      }
      learning.evaluateFreshness();
      if (reconnected && shell.router.current() === "learn" && selectionSource(learning.snapshot?.song_id).kind === "package" && (!learnLesson || player.selectedSong?.song_id === learning.snapshot.song_id)) {
        void restoreActiveLesson();
      }
    } catch (error) {
      globalThis.__lastPollError = String((error && error.stack) || error);
      goOffline();
    } finally {
      ui.pollBusy = false;
    }
  }

  function startPolling() {
    clearInterval(ui.pollTimer);
    ui.pollTimer = setInterval(poll, POLL_INTERVAL_MS);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") {
        evaluatePlaybackFreshness(performance.now());
        learning.evaluateFreshness();
        liveNotes.start();
        poll();
        // Reconstruct visual truth from authoritative state after the
        // background/sleep gap (no visual history is relied upon).
        void ensureVisualScore();
        ensureVisualAnimation();
      } else {
        liveNotes.stop();
      }
    });
    poll();
  }

  /* ---------------- library search / listing (U8-P2) ----------------
   * The presentation controller (ui008/library.js) owns the search
   * pipeline, bounded result rendering and library states. app.js remains
   * the single Core integration point: it supplies the bounded API call
   * and the accepted Play/Learn/selection entry points as callbacks. */

  const recents = createRecentsStore();
  const homeController = createHomeController({ recents });

  function recordRecent(record) {
    if (!record || !record.song_id) return;
    recents.record(record);
    homeController.refreshRecents();
  }

  function libraryOnSelect(record, intent = null) {
    if (selectionSource(record?.song_id).kind !== "gold") {
      showPlayerError("invalid_song_id", "Choose a song from the Player library.");
      return false;
    }
    if (!renderSelectedSong(record, intent)) return false;
    clearLearnSelectionError();
    document.body.dataset.learnEntry = "library-song";
    learnLesson = null;
    learnLibrary.clearSelection();
    try { sessionStorage.removeItem("piano-core.learn-selection.v1"); } catch { /* optional tab-local hint */ }
    syncLearnStartButton();
    // Preserve the accepted selection preview when nothing is playing.
    if (!lastPlayback || !lastPlayback.song_id || lastPlayback.state === "IDLE") {
      setText(dom.playerTitle, record.title || record.filename || record.song_id);
      setText(dom.playerMeta, "Ready to play from the beginning.");
      const state = playerPresentationState(
        lastPlayback,
        true,
        player.fresh && !ui.offline,
        false
      );
      dom.playerState.dataset.state = state;
      setText(dom.playerState, playerStateLabel(state));
    }
    setControls(lastPlayback);
    recordRecent(record);
    return true;
  }

  async function libraryPlay(record) {
    if (ui.controlsBusy) return;
    if (!libraryOnSelect(record)) return;
    if (dom.play.disabled) {
      setText(dom.libraryFeedback, "Playback is unavailable right now.");
      show(dom.libraryFeedback, true);
      return;
    }
    library.setPending(true);
    setText(dom.libraryFeedback, "Starting on your piano…");
    show(dom.libraryFeedback, true);
    try {
      const result = await runControl("play");
      const accepted = result?.status === "success";
      setText(dom.libraryFeedback, accepted ? "" : dom.playerError.textContent || "Playback is not confirmed. Check the connection and try again.");
      show(dom.libraryFeedback, !accepted);
      if (accepted) shell.router.navigate("player");
    } finally {
      library.setPending(false);
    }
  }

  function libraryLearn(record) {
    if (!libraryOnSelect(record)) return;
    shell.router.navigate("learn");
  }

  // PIANO-LEARN-001: Learn Library catalog context. A selected lesson
  // owns the guided-practice start (package_id); selecting a Player
  // song clears it, and vice versa — the two catalogs never mix.
  let learnLesson = null;

  function learnSelectLesson(model, intent) {
    const songId = `package:${model.packageId}`;
    const record = { song_id: songId, package_id: model.packageId, lesson_id: model.lessonId,
      title: model.title, filename: model.title, duration_seconds: model.durationSeconds };
    if (!player.selectSong(record, intent)) return false;
    clearLearnSelectionError();
    document.body.dataset.learnEntry = "academy";
    learnLesson = model;
    try { sessionStorage.setItem("piano-core.learn-selection.v1", JSON.stringify({ kind: "package", lesson_id: model.lessonId, package_id: model.packageId })); } catch { /* optional tab-local hint */ }
    studio?.setLesson(model);
    const handSelect = document.getElementById("studio-hand");
    handSelect.disabled = !model.handSelectionAvailable;
    handSelect.value = "";
    studioFocus = null;
    // Hydrate the shared visual workspace from the lesson's package
    // (canonical score/fingering projections; DETERMINISTIC_INFERENCE
    // fingering stays clearly separate from the source's hand truth).
    // The accepted Learn detail is already the canonical package metadata.
    // Shared score/fingering APIs keep the full package-prefixed identity.
    setText(dom.practiceSong, model.title);
    show(dom.selected, false);
    renderPractice(learningAuthorizedSession(), { stale: !learning.authorized });
    renderPlaybackMetadata();
    setControls(lastPlayback);
    syncLearnStartButton();
    return true;
  }

  function syncLearnStartButton() {
    if (dom.ui008LessonStart) {
      dom.ui008LessonStart.disabled = !learnLesson || ui.controlsBusy;
    }
  }

  document.addEventListener("click", event => {
    if (event.target.closest?.('a[data-ui008-route="learn"], button[data-ui008-route="learn"]') && !studio?.isOpen()) {
      document.body.dataset.learnEntry = "academy";
      document.querySelector(".layout").scrollTop = 0;
      void loadAcademyIfVisible();
    }
  }, true);
  const learnLibrary = createLearnLibraryController({
    documentRef: document,
    beginSelectionIntent: kind => player.beginSelectionIntent(kind),
    isSelectionIntentCurrent: ticket => player.isSelectionIntentCurrent(ticket),
    onSelectionError: error => showLearnSelectionError(error.code, error.message),
    onLessonSelected: (model, intent) => {
      if (!learnSelectLesson(model, intent)) return false;
      shell.router.navigate("learn");
      return true;
    },
  });
  async function studioCommand(action, paused) {
    if (ui.controlsBusy || !learning.authorized) return false;
    practiceCommandError = null;
    ui.controlsBusy = true;
    const ticket = learning.beginCommand();
    try {
      const current = learning.snapshot;
      const result = action === "stop" ? await api.learningStop(current) : await api._request("POST", `${API_BASE}/learning/pause`, { expected_session_id: current.session_id, expected_version: current.version, paused });
      learning.acceptCommandResult(result, ticket);
      return true;
    } catch (error) {
      showPracticeError(error.code || "network_error", error.message);
      return false;
    } finally { ui.controlsBusy = false; setControls(lastPlayback); }
  }
  studio = createStudioController({
    documentRef: document,
    onStop: () => {
      if (ui.controlsBusy || !learning.authorized) return false;
      return learning.snapshot?.state === "WAITING" ? studioCommand("stop") : ["IDLE", "COMPLETED", "FAILED"].includes(learning.snapshot?.state);
    },
    onPause: paused => studioCommand("pause", paused),
    onMode: async mode => {
      if (ui.controlsBusy) return;
      try { learningStartConfig({ packageId: learnLesson?.packageId, mode, sectionStart: dom.learningSectionStart.value, sectionEnd: dom.learningSectionEnd.value, requiredRepetitions: dom.learningRepetitions.value, speed: document.getElementById("studio-speed").value }); }
      catch (error) { showPracticeError("invalid_learning_configuration", error.message); document.getElementById("studio-session-mode").value = learning.snapshot?.mode || "WAIT_FOR_NOTE"; return; }
      if (mode === "SECTION_PRACTICE" && Number(dom.learningSectionEnd.value) > learnLesson?.durationSeconds) {
        showPracticeError("invalid_learning_configuration", "Choose a section within this lesson before switching modes.");
        document.getElementById("studio-session-mode").value = learning.snapshot?.mode || "WAIT_FOR_NOTE";
        return;
      }
      if (learning.snapshot?.state === "WAITING" && await studioCommand("stop") !== true) return;
      dom.learningMode.value = mode;
      dom.learningMode.dispatchEvent(new Event("change"));
      document.getElementById("studio-lesson-mode").value = mode;
      await runLearnLessonStart();
    },
    onRestart: async () => { if (learning.snapshot?.state === "WAITING" && await studioCommand("stop") !== true) return; await runLearnLessonStart(); },
    onNext: async id => {
      if (!id || ui.controlsBusy) return;
      const intent = player.beginSelectionIntent("package");
      const selected = await learnLibrary.selectLesson(id, { intent, beforeSelect: async () => learning.snapshot?.state !== "WAITING" || await studioCommand("stop") === true });
      if (!player.isSelectionIntentCurrent(intent)) return;
      if (selected?.lessonId !== id) { showPracticeError("lesson_unavailable", "This lesson could not be loaded. Check the connection and try again."); return; }
      studioFocus = null;
      await runLearnLessonStart();
    },
    onVisual: mode => { if (["hands", "piano"].includes(mode)) workspace.setView(mode); else { workspace.setView(mode === "sheet" ? "sheet" : "falling"); setVisualMode(mode); } ensureVisualAnimation(); },
    onRange: mode => { studioRange = mode; ensureVisualAnimation(); },
    onRetrySave: async () => { try { await api._request("POST", `${API_BASE}/learning/progress/retry`, {}); } catch (error) { showPracticeError(error.code || "network_error", error.message); } },
    onExit: () => { learnLibrary.refresh(); document.getElementById("ui008-lesson-start")?.focus(); },
  });
  learnLibrary.onStartRequested = () => {
    if (ui.controlsBusy) return;
    studioFocus = null;
    dom.learningMode.value = document.getElementById("studio-lesson-mode").value;
    studio.enter(learnLesson);
    workspace.setView("falling"); setVisualMode("falling");
    runLearnLessonStart();
  };
  learnLibrary.setFetchers({
    fetchOverview: () => api.learn(),
    fetchLessons: (queryString) => api.learnLessons(queryString),
    fetchLesson: (lessonId, options) => api.learnLesson(lessonId, options),
  });
  learnLibrary.attach();
  let academyLoaded = false;
  let academyLoading = null;
  async function loadAcademyIfVisible() {
    if (academyLoaded || document.body.dataset.learnEntry === "library-song" || shell.router.current() !== "learn") return;
    if (!academyLoading) academyLoading = learnLibrary.refresh().then(ready => { academyLoaded = ready === true; }).finally(() => { academyLoading = null; });
    await academyLoading;
  }
  document.getElementById("academy-refresh").addEventListener("click", async event => {
    event.currentTarget.disabled = true;
    try { academyLoaded = false; await loadAcademyIfVisible(); }
    finally { document.getElementById("academy-refresh").disabled = false; }
  });
  let lessonRestorePending = null;
  function restoreActiveLesson() {
    if (shell.router.current() !== "learn" || document.body.dataset.learnEntry === "library-song") return Promise.resolve(false);
    if (lessonRestorePending && player.isSelectionIntentCurrent(lessonRestorePending.intent)) return lessonRestorePending.promise;
    const intent = player.beginSelectionIntent("package");
    const pending = { intent, promise: null };
    lessonRestorePending = pending;
    const restoreGeneration = learning.generation;
    const current = () => player.isSelectionIntentCurrent(intent) && shell.router.current() === "learn" && learning.generation === restoreGeneration;
    pending.promise = (async () => {
      const active = await api.learning({ signal: intent.signal });
      if (!current()) return false;
      if (!isValidLearningSnapshot(active)) throw new ApiError(502, "invalid_response", "The learning response is invalid.");
      const source = selectionSource(active.song_id);
      if (active.song_id && source.kind === "invalid") throw new ApiError(502, "invalid_response", "The active learning identity is invalid.");
      const packageSession = source.kind === "package" && active.lesson_id && ["WAITING", "COMPLETED", "FAILED"].includes(active.state);
      let hint = null;
      if (active.state !== "WAITING") {
        if (active.state === "WAITING") return false;
        let raw = null;
        try { raw = sessionStorage.getItem("piano-core.learn-selection.v1"); } catch { /* optional tab-local hint */ }
        if (raw) {
          try { hint = JSON.parse(raw); } catch { /* classified below */ }
          if (!hint || hint.kind !== "package" || typeof hint.lesson_id !== "string" || !hint.lesson_id || typeof hint.package_id !== "string" || selectionSource(`package:${hint.package_id}`).kind !== "package") {
            throw new ApiError(400, "invalid_selection", "The stored lesson is invalid. Choose a lesson from Academy.");
          }
        }
      }
      // Only an active Core session outranks a newer explicit selection hint.
      // Terminal result truth must not replace the lesson selected afterward.
      const retained = packageSession && (active.state === "WAITING" || !hint);
      if (active.state === "WAITING" && !retained) return false;
      await loadAcademyIfVisible();
      if (!current() || (!retained && !hint)) return false;
      const lessonId = retained ? active.lesson_id : hint.lesson_id;
      let verified = null;
      const selected = await learnLibrary.selectLesson(lessonId, { intent, beforeSelect: async model => {
        if (!current()) return false;
        if (model.packageId !== (retained ? source.packageId : hint.package_id)) throw new ApiError(502, "invalid_response", "The lesson does not match the requested package.");
        const ticket = learning.beginPoll();
        verified = await api.learning({ signal: intent.signal });
        if (!current()) return false;
        if (!isValidLearningSnapshot(verified)) throw new ApiError(502, "invalid_response", "The learning response is invalid.");
        const sameSession = retained
          ? verified.session_id === active.session_id && verified.song_id === active.song_id && verified.lesson_id === active.lesson_id && verified.state === active.state
          : verified.session_id === active.session_id && verified.song_id === active.song_id && verified.state === active.state;
        if (!sameSession) return false;
        if (!learning.acceptPoll(verified, ticket)) return false;
        learning.evaluateFreshness();
        return current() && learning.authorized && learning.snapshot === verified;
      } });
      if (!current() || !selected || selected.lessonId !== lessonId) return false;
      if (retained && verified.state === "WAITING") {
        dom.learningMode.value = verified.mode;
        document.getElementById("studio-lesson-mode").value = verified.mode;
        document.getElementById("studio-speed").value = String(verified.flow?.speed ?? 1);
        document.getElementById("studio-hand").value = verified.hand_selection || "";
        studio.enter(learnLesson, false);
      }
      return true;
    })().catch(error => {
      if (current()) showLearnSelectionError(error.code || "network_error", error.message);
      return false;
    }).finally(() => { if (lessonRestorePending === pending) lessonRestorePending = null; });
    return pending.promise;
  }
  window.addEventListener("piano-ui-route-change", event => {
    const route = event.detail?.route;
    if ((player.selectionIntentKind === "package" && route !== "learn") || (player.selectionIntentKind === "gold" && route === "learn")) {
      player.cancelSelectionIntent();
      learnLibrary.cancelPendingSelection();
    }
    void loadAcademyIfVisible();
    if (route === "learn" && !learnLesson) void restoreActiveLesson();
  });
  void restoreActiveLesson();
  let studioPollBusy = false;
  setInterval(async () => {
    if (!studio?.isOpen() || document.visibilityState !== "visible" || studioPollBusy || ui.controlsBusy) return;
    studioPollBusy = true;
    const ticket = learning.beginPoll();
    try { learning.acceptPoll(await api.learning(), ticket); }
    catch { learning.notePollFailure(ticket); }
    finally { studioPollBusy = false; learning.evaluateFreshness(); ensureVisualAnimation(); }
  }, 100);

  function libraryFetchSongs(query, limit, offset, { signal, status } = {}) {
    return api.songs(query, limit, offset, { signal, status });
  }

  const library = createLibraryController({
    fetchSongs: libraryFetchSongs,
    onPlay: libraryPlay,
    onLearn: libraryLearn,
    onSelect: libraryOnSelect,
    pageSize: PAGE_SIZE,
    debounceMs: SEARCH_DEBOUNCE_MS,
  });

  function openRecent(entry) {
    if (!entry || !entry.song_id) return;
    shell.router.navigate("library");
    const intent = player.beginSelectionIntent("gold");
    const report = error => {
      showPlayerError(error.code || "network_error", error.message);
      setText(dom.libraryFeedback, errorText(error.code || "network_error", error.message));
      dom.libraryFeedback.dataset.errorCode = error.code || "network_error";
      show(dom.libraryFeedback, true);
    };
    if (selectionSource(entry.song_id).kind !== "gold") {
      report(new ApiError(400, "invalid_song_id", "This recent entry is not a Player song."));
      return;
    }
    // A recent is an identity hint, never an authoritative metadata fallback.
    api
      .song(entry.song_id, { signal: intent.signal })
      .then((record) => {
        if (!player.isSelectionIntentCurrent(intent)) return;
        if (record?.song_id !== entry.song_id || selectionSource(record.song_id).kind !== "gold") {
          throw new ApiError(502, "invalid_response", "Song metadata does not match this recent entry.");
        }
        library.selectRecord(record, undefined, { intent });
      })
      .catch((error) => {
        if (player.isSelectionIntentCurrent(intent)) report(error);
      });
    shell.router.navigate("library");
  }

  window.addEventListener("piano-ui-home-navigate", (event) => {
    if (event.detail?.route === "learn" && selectionSource(learning.snapshot?.song_id).kind === "package") {
      document.body.dataset.learnEntry = "academy";
      learnLesson = null;
    }
    if (event.detail?.route) shell.router.navigate(event.detail.route);
  });
  window.addEventListener("piano-ui-open-recent", (event) => {
    openRecent(event.detail);
  });
  for (const cta of document.querySelectorAll("[data-ui008-search-cta]")) {
    cta.addEventListener("click", () => library.requestSearchFocus());
  }

  /* ---------------- Player workspace presentation (U8-P3) ----------------
   * Presentation-only wiring: Performance Mode and the secondary-controls
   * placement own no musical authority; the LED quick toggle reuses the
   * exact accepted PUT /led path through runLedUpdate. */

  const performanceCtl = createPerformanceController();
  const playerSecondary = createSecondaryPlacement();
  const learnLayer = createLayerController({
    windowRef: window,
    documentRef: document,
    root: dom.learnSheet,
    closeButton: dom.learnSheetClose,
    layerId: "learn-options",
  });
  dom.learnOptionsBtn?.addEventListener("click", () => {
    learnLayer.open(dom.learnOptionsBtn);
  });
  const pianoLayer = createLayerController({
    windowRef: window,
    documentRef: document,
    root: dom.pianoSheet,
    closeButton: dom.pianoSheetClose,
    layerId: "piano-options",
  });
  dom.pianoOptionsBtn?.addEventListener("click", () => {
    pianoLayer.open(dom.pianoOptionsBtn);
  });
  const ledQuick = $("ui008-led-quick");

  function syncLedQuick(state) {
    if (!ledQuick) return;
    const enable = ledControlEnablement(state);
    const on = state && state.enabled === true;
    ledQuick.disabled = !enable.toggle;
    ledQuick.setAttribute("aria-pressed", String(on));
    setText(ledQuick, on ? "Lights on" : "Lights off");
  }

  ledQuick?.addEventListener("click", () => {
    if (ledQuick.disabled) return;
    runLedUpdate(ledTogglePayload(lastLed));
  });
  syncLedQuick(lastLed);

  function clearInvalidSelection(songId, alreadyInvalidated = false) {
    if (!alreadyInvalidated && !player.invalidateSelectedSong(songId)) return;
    if (player.selectedSong) return;
    show(dom.selected, false);
    library.clearSelectionHighlight();
  }

  /* ---------------- control actions ---------------- */

  function showPlayerError(code, fallback) {
    dom.playerError.dataset.errorCode = code;
    setText(dom.playerError, errorText(code, fallback));
    show(dom.playerError, true);
  }

  async function reconcilePlaybackFromCore() {
    try {
      const result = await player.runPoll(
        () => api.playback(),
        (snapshot) => snapshot,
        () => performance.now()
      );
      if (result.status === "failed") {
        goOffline("Reconciliation pending — position frozen");
        return false;
      }
      if (!result.accepted) return false;
      const reconnected = ui.offline;
      ui.offline = false;
      renderPlayback();
      if (reconnected) {
        setText(dom.connectionNote, "Reconnected — Core state restored.");
      }
      return true;
    } catch {
      goOffline("Reconciliation pending — position frozen");
      return false;
    }
  }

  async function runControl(action) {
    if (ui.controlsBusy) return;
    const nowMs = performance.now();
    if (!evaluatePlaybackFreshness(nowMs)) {
      markPlaybackStale("Core state expired — refresh required before commands");
      void reconcilePlaybackFromCore();
      return;
    }
    const requestedSong = action === "play" ? player.selectedGoldSong() : null;
    if (action === "play" && !requestedSong) {
      showPlayerError("invalid_song_id");
      return;
    }
    freezePlaybackPresentation(nowMs);
    ui.controlsBusy = true;
    dom.playerState.dataset.state = "loading";
    setText(dom.playerState, action === "stop" ? "Stopping…" : "Working…");
    setText(dom.playerFreshness, "Waiting for Core response");
    dom.play.disabled = true;
    dom.pause.disabled = true;
    dom.resume.disabled = true;
    dom.stop.disabled = true;
    dom.btnPractice.disabled = true;
    dom.btnPracticeStop.disabled = true;
    dom.btnLearningRestartTarget.disabled = true;
    dom.btnLearningRestartSection.disabled = true;
    show(dom.playerError, false);
    const result = await player.runCommand({
      action,
      requestedSongId: requestedSong ? requestedSong.song_id : null,
      invoke: () => action === "play"
        ? api.play(requestedSong.song_id, Number(dom.tempo.value))
        : api[action](),
      reconcile: () => api.playback(),
      now: () => performance.now(),
    });
    try {
      if (result.status === "success") {
        renderPlayback();
      } else if (result.status === "unknown") {
        goOffline("Command outcome unknown — reconciling with Core");
        showPlayerError("network_error", "Command outcome unknown until Core reconnects.");
      } else if (result.status === "reconciling") {
        goOffline("Command accepted; Core reconciliation pending");
      } else if (result.status === "rejected") {
        const error = result.error;
        showPlayerError(error.code, error.message);
        if (
          action === "play" && requestedSong &&
          ["song_not_found", "song_source_missing"].includes(error.code)
        ) {
          clearInvalidSelection(requestedSong.song_id, true);
        }
        if (error.code === "playback_already_active" || error.status === 409) {
          toast(errorText(error.code));
        }
      } else if (result.status === "stale") {
        markPlaybackStale("Core state expired — command not sent");
      }
    } finally {
      ui.controlsBusy = false;
      if (["success", "rejected"].includes(result.status) && player.fresh) {
        renderPlayback();
      }
      setControls(lastPlayback);
      if (["unknown", "reconciling", "stale"].includes(result.status) || !player.fresh) {
        void reconcilePlaybackFromCore();
      } else {
        void poll();
      }
    }
    return result;
  }

  /* ---------------- events ---------------- */

  dom.tempo.addEventListener("input", () => {
    setText(dom.tempoValue, formatTempo(dom.tempo.value));
  });
  setText(dom.tempoValue, formatTempo(dom.tempo.value));

  for (const preset of document.querySelectorAll(".tempo-preset")) {
    preset.addEventListener("click", () => {
      dom.tempo.value = String(clampTempo(preset.dataset.tempo));
      setText(dom.tempoValue, formatTempo(dom.tempo.value));
    });
  }

  dom.play.addEventListener("click", () => runControl("play"));
  dom.pause.addEventListener("click", () => runControl("pause"));
  dom.resume.addEventListener("click", () => runControl("resume"));
  dom.stop.addEventListener("click", () => runControl("stop"));
  dom.btnPractice.addEventListener("click", () => runPracticeStart());
  dom.btnPracticeStop.addEventListener("click", () => runPracticeStop());
  dom.btnLearningRestartTarget.addEventListener(
    "click", () => runLearningRestart(false)
  );
  dom.btnLearningRestartSection.addEventListener(
    "click", () => runLearningRestart(true)
  );
  dom.learningMode.addEventListener("change", () => {
    show(dom.learningSectionConfig, dom.learningMode.value === "SECTION_PRACTICE");
    show(
      dom.learningRepetitionConfig,
      dom.learningMode.value === "REPEAT_UNTIL_CORRECT"
    );
  });

  dom.visualModeSheet.addEventListener("click", () => setVisualMode("sheet"));
  dom.visualModeFalling.addEventListener("click", () => setVisualMode("falling"));
  dom.visualModeHybrid.addEventListener("click", () => setVisualMode("hybrid"));
  // U8-P5: Settings mirrors drive the same local preference.
  dom.settingsVisualSheet.addEventListener("click", () => setVisualMode("sheet"));
  dom.settingsVisualFalling.addEventListener("click", () => setVisualMode("falling"));
  dom.settingsVisualHybrid.addEventListener("click", () => setVisualMode("hybrid"));

  dom.ledEnabled.addEventListener("change", () => {
    runLedUpdate({ enabled: dom.ledEnabled.checked });
  });
  dom.ledMode.addEventListener("change", () => {
    const mode = dom.ledMode.value;
    if (LED_MODES.includes(mode)) {
      runLedUpdate({ mode });
    }
  });
  dom.ledBrightness.addEventListener("input", () => {
    setText(dom.ledBrightnessValue, `${clampBrightness(dom.ledBrightness.value)}%`);
  });
  dom.ledBrightness.addEventListener("change", () => {
    runLedUpdate({ brightness: clampBrightness(dom.ledBrightness.value) });
  });
  dom.btnLedClear.addEventListener("click", async () => {
    ui.ledBusy = true;
    setLedControls(null);
    show(dom.ledError, false);
    try {
      const state = await api.ledClear();
      lastLed = state;
      renderLed(state);
      toast("Lights cleared.");
    } catch (error) {
      if (error instanceof ApiError) {
        showLedError(error.code, error.message);
      } else {
        goOffline();
        showLedError("network_error");
      }
    } finally {
      ui.ledBusy = false;
      setLedControls(lastLed);
    }
  });

  document.addEventListener("keydown", (event) => {
    if (studio?.isOpen()) return;
    const action = transportShortcutAction(event, {
      play: !dom.play.disabled,
      pause: !dom.pause.disabled,
      resume: !dom.resume.disabled,
      stop: !dom.stop.disabled,
    });
    if (action) {
      event.preventDefault();
      dom[action].click();
    }
  });

  liveNotes.start();
  startPolling();
  void refreshDeviceIdentity();
  void library.run("", 0);
}
