/* PIANO-UI-008 U8-P5: premium Piano / Device presentation models.
 *
 * Presentation ONLY over the accepted authoritative surfaces (health,
 * StateStore snapshot via /state, LED snapshot, device identity). The
 * existing app.js coordinators remain the single command boundary; this
 * module never issues a command and never infers musical correctness.
 * It owns exactly:
 * - PIANO_FEATURES: the honest capability map (real contracts only);
 * - consumer connection state: connected / waiting / disconnected /
 *   recovering / offline with calm wording (no raw enums);
 * - consumer activity lines: keys held, sustain pedal, last key —
 *   physical observation only, never learning truth;
 * - consumer LED guidance status: ready / off / unavailable /
 *   not set up / unknown — the LED never decides correctness;
 * - consumer identity: product name + display code only (the device
 *   UUID stays behind the existing Settings technical disclosure).
 */

export const PIANO_FEATURES = Object.freeze({
  connectionStatus: true, // health.midi_input.connection (read-only)
  liveKeyboard: true, // canonical accepted 88-key renderer (reused)
  pressedKeys: true, // /state pressed_notes (physical, read-only)
  sustain: true, // /state sustain (read-only)
  lastInput: true, // /state last_input (read-only)
  deviceIdentity: true, // GET /device product + display_code (read-only)
  ledGuidanceStatus: true, // LED snapshot (indicator, never correctness)
  ledControls: true, // PUT /led enabled/mode/brightness (accepted path)
  ledClear: true, // POST /led/clear (accepted bounded diagnostic)
  technicalDisclosure: true, // device UUID / transport behind Settings only
  midiConnectCommand: false, // no Core reconnect command exists
  devicePairing: false, // no pairing/bonding contract exists
  audioInput: false, // future AUDIO track — absent
  bleLedKit: false, // future LEDKIT track — absent
});

/** The real MIDI connection values with consumer wording (map only). */
export function pianoConnectionModel({ midiConnection, coreOffline = false } = {}) {
  if (coreOffline) {
    return {
      state: "offline",
      chip: { label: "Reconnecting", tone: "wait" },
      guidance: "Piano Core is unreachable right now — reconnecting automatically.",
    };
  }
  switch (midiConnection) {
    case "CONNECTED":
      return {
        state: "connected",
        chip: { label: "Piano connected", tone: "ok" },
        guidance: "Piano Core sees your piano. Play a key — it lights up below.",
      };
    case "CONNECTING":
      return {
        state: "waiting",
        chip: { label: "Waiting for piano", tone: "wait" },
        guidance: "Looking for your piano. Check its USB or power connection.",
      };
    case "UNAVAILABLE":
    case "DISCONNECTED":
      return {
        state: "disconnected",
        chip: { label: "Piano disconnected", tone: "bad" },
        guidance: "Your piano is not connected. Plug it in or check its cable — Piano Core is looking.",
      };
    default:
      return {
        state: "unknown",
        chip: { label: "Checking piano", tone: "wait" },
        guidance: "Checking the piano connection…",
      };
  }
}

/** Consumer activity model: physical observation only (never correctness). */
export function pianoActivityModel(physical) {
  const state = physical || {};
  const notes = Array.isArray(state.pressed_notes) ? state.pressed_notes : [];
  const sustain = state.sustain || {};
  const last = state.last_input || null;
  const names = notes.slice(0, 6).map(midiNoteName);
  return {
    keysHeld: {
      count: notes.length,
      playing: notes.length > 0,
      names,
      summary:
        notes.length === 0
          ? "No keys held right now."
          : `Holding ${names.join(", ")}${notes.length > names.length ? " …" : ""}`,
    },
    sustain: {
      down: sustain.pressed === true,
      summary: sustain.pressed ? "Sustain pedal is down." : "Sustain pedal is up.",
    },
    lastKey: {
      known: !!(last && (last.note === 0 || last.note)),
      summary: last
        ? `Last thing you played: ${midiNoteName(last.note)}`
        : "Play a key and it will show up here.",
    },
  };
}

const PIANO_NOTE_NAMES = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"];

/** MIDI note number -> name (same mapping as the accepted keyboard). */
export function midiNoteName(note) {
  const n = Number(note);
  if (!Number.isInteger(n) || n < 0 || n > 127) return "?";
  return `${PIANO_NOTE_NAMES[n % 12]}${Math.floor(n / 12) - 1}`;
}

/**
 * Consumer LED guidance status from the authoritative LED snapshot only.
 * The LED never determines learning correctness and the browser never
 * calculates note→LED mappings.
 */
export function pianoLedModel(led) {
  if (led === null || led === undefined) {
    return {
      state: "unknown",
      chip: { label: "Lights unavailable", tone: "wait" },
      guidance: "Keyboard lights are temporarily unavailable.",
    };
  }
  if (led.configured !== true) {
    return {
      state: "not_configured",
      chip: { label: "No guidance lights", tone: "idle" },
      guidance: "This Piano Core has no keyboard lights set up. You can follow the on-screen keys instead.",
    };
  }
  if (led.enabled !== true) {
    return {
      state: "off",
      chip: { label: "Lights off", tone: "idle" },
      guidance: "Keyboard lights are turned off. Turn them on below for guidance.",
    };
  }
  if (led.available !== true) {
    return {
      state: "unavailable",
      chip: { label: "Lights unavailable", tone: "bad" },
      guidance: "The keyboard lights cannot be reached right now. Everything else keeps working.",
    };
  }
  return {
    state: "ready",
    chip: { label: "Guidance lights ready", tone: "ok" },
    guidance: "Keyboard lights are ready to guide you.",
  };
}

/** Consumer device identity: product + display code only (no UUID here). */
export function pianoIdentityModel(device) {
  if (!device || typeof device !== "object") return null;
  return {
    name: typeof device.product === "string" && device.product ? device.product : "Piano Core",
    code: typeof device.display_code === "string" && device.display_code ? device.display_code : null,
  };
}

/**
 * Rows for the ALREADY-AUTHORIZED Settings technical disclosure. The only
 * place raw identifiers (device UUID, LED transport endpoint, raw MIDI
 * connection value) may appear. Read-only.
 */
export function pianoTechRows({ device = null, led = null, midiConnection = null } = {}) {
  const rows = [];
  if (device && device.device_id) {
    rows.push({ label: "Device ID", value: String(device.device_id) });
  }
  if (midiConnection) {
    rows.push({ label: "MIDI in", value: String(midiConnection) });
  }
  if (led && led.transport && led.transport.host && led.transport.port) {
    rows.push({ label: "LED transport", value: `${led.transport.host}:${led.transport.port}` });
  }
  if (led && led.last_error) {
    rows.push({ label: "LED last error", value: String(led.last_error) });
  }
  return rows;
}
