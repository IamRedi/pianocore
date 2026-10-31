// PIANO-UI-008 U8-P5: deterministic node tests for the premium Piano /
// Device and Settings presentation models — honest capability maps,
// consumer wording without raw enums or technical leakage, read-only
// status rows, and local-preference option values that exactly match the
// accepted contracts.
import assert from "node:assert/strict";
import {
  PIANO_FEATURES,
  pianoConnectionModel,
  pianoActivityModel,
  pianoLedModel,
  pianoIdentityModel,
  pianoTechRows,
  midiNoteName,
} from "../../src/piano_core/web/static/ui008/piano.js";
import {
  SETTINGS_FEATURES,
  VISUAL_PREFERENCE_OPTIONS,
  HANDS_CAMERA_OPTIONS,
  HANDS_QUALITY_OPTIONS,
  settingsModel,
  settingsLedStatus,
  settingsAboutRows,
} from "../../src/piano_core/web/static/ui008/settings.js";

let passes = 0;
let failures = 0;
function test(name, fn) {
  try {
    fn();
    passes += 1;
    console.log(`ok - ${name}`);
  } catch (error) {
    failures += 1;
    console.error(`FAIL - ${name}`);
    console.error(error);
  }
}

const BANNED_TECH =
  /StateStore|websocket|WebSocket|HTTP\/|endpoint|mutex|localhost|127\.0\.0\.1|NOTE_ON|NOTE_OFF|activity_conflict|playback_already_active|library_unavailable/;
const BANNED_ENUMS =
  /\b(WAIT_FOR_NOTE|SECTION_PRACTICE|REPEAT_UNTIL_CORRECT|CONNECTED|DISCONNECTED|UNAVAILABLE|IDLE|WAITING|PLAYING|PAUSED|COMPLETED|FAILED|INPUT_UNAVAILABLE)\b/;

/* ---------------- honest capability maps ---------------- */

test("PIANO_FEATURES: real capabilities enabled, future tracks absent", () => {
  for (const f of ["connectionStatus", "liveKeyboard", "pressedKeys", "sustain", "lastInput", "deviceIdentity", "ledGuidanceStatus", "ledControls", "ledClear", "technicalDisclosure"]) {
    assert.equal(PIANO_FEATURES[f], true, f);
  }
  // No manual MIDI reconnect command exists; pairing/audio/BLE are
  // separate future tracks and must never appear.
  for (const f of ["midiConnectCommand", "devicePairing", "audioInput", "bleLedKit"]) {
    assert.equal(PIANO_FEATURES[f], false, f);
  }
});

test("SETTINGS_FEATURES: local presentation prefs only; no fake persistence", () => {
  for (const f of ["visualPreference", "handsPreference", "guidanceStatus", "about", "technicalDisclosure", "themeChoice"]) {
    assert.equal(SETTINGS_FEATURES[f], true, f);
  }
  for (const f of ["durablePersistence", "audioOptions", "safeReset", "reducedMotionOverride"]) {
    assert.equal(SETTINGS_FEATURES[f], false, f);
  }
});

test("feature maps are frozen (no runtime capability inflation)", () => {
  assert.ok(Object.isFrozen(PIANO_FEATURES));
  assert.ok(Object.isFrozen(SETTINGS_FEATURES));
  assert.ok(Object.isFrozen(VISUAL_PREFERENCE_OPTIONS));
  assert.ok(Object.isFrozen(HANDS_CAMERA_OPTIONS));
  assert.ok(Object.isFrozen(HANDS_QUALITY_OPTIONS));
});

test("local preference option values exactly match accepted contracts", () => {
  assert.deepEqual(VISUAL_PREFERENCE_OPTIONS.map((o) => o.value), ["sheet", "falling", "hybrid"]);
  assert.deepEqual(HANDS_CAMERA_OPTIONS.map((o) => o.value), ["FULL", "FOLLOW"]);
  assert.deepEqual(
    HANDS_QUALITY_OPTIONS.map((o) => o.value),
    ["AUTO", "PREMIUM", "BALANCED", "BATTERY", "COMPATIBILITY"]
  );
});

/* ---------------- connection presentation ---------------- */

test("connection: connected maps to calm consumer copy", () => {
  const m = pianoConnectionModel({ midiConnection: "CONNECTED" });
  assert.equal(m.state, "connected");
  assert.equal(m.chip.label, "Piano connected");
  assert.ok(m.guidance.includes("sees your piano"));
});

test("connection: waiting / disconnected / unknown states", () => {
  assert.equal(pianoConnectionModel({ midiConnection: "CONNECTING" }).state, "waiting");
  assert.equal(pianoConnectionModel({ midiConnection: "CONNECTING" }).chip.label, "Waiting for piano");
  assert.equal(pianoConnectionModel({ midiConnection: "DISCONNECTED" }).state, "disconnected");
  assert.equal(pianoConnectionModel({ midiConnection: "UNAVAILABLE" }).state, "disconnected");
  assert.equal(pianoConnectionModel({ midiConnection: "DISCONNECTED" }).chip.label, "Piano disconnected");
  assert.equal(pianoConnectionModel({ midiConnection: null }).state, "unknown");
});

test("connection: offline presentation wins over raw MIDI value", () => {
  const m = pianoConnectionModel({ midiConnection: "CONNECTED", coreOffline: true });
  assert.equal(m.state, "offline");
  assert.equal(m.chip.label, "Reconnecting");
  assert.ok(m.guidance.includes("reconnecting automatically"));
});

test("connection model never leaks raw enums or technical terms", () => {
  for (const midi of ["CONNECTED", "CONNECTING", "DISCONNECTED", "UNAVAILABLE", "SOMETHING_ELSE", null]) {
    for (const coreOffline of [true, false]) {
      const m = pianoConnectionModel({ midiConnection: midi, coreOffline });
      const text = `${m.chip.label} ${m.guidance}`;
      assert.equal(BANNED_ENUMS.test(text), false, `enum leak in: ${text}`);
      assert.equal(BANNED_TECH.test(text), false, `tech leak in: ${text}`);
    }
  }
});

/* ---------------- activity presentation (physical only) ---------------- */

test("activity: pressed keys become consumer names, never MIDI events", () => {
  const m = pianoActivityModel({ pressed_notes: [60, 67], sustain: { pressed: true }, last_input: { type: "NOTE_ON", note: 67 } });
  assert.equal(m.keysHeld.summary, "Holding C4, G4");
  assert.ok(m.keysHeld.playing);
  assert.equal(m.sustain.summary, "Sustain pedal is down.");
  assert.equal(m.lastKey.summary, "Last thing you played: G4");
  assert.equal(midiNoteName(21), "A0");
  assert.equal(midiNoteName(108), "C8");
  assert.equal(midiNoteName(999), "?");
});

test("activity: idle and release wording", () => {
  const m = pianoActivityModel({ pressed_notes: [], sustain: { pressed: false }, last_input: null });
  assert.equal(m.keysHeld.summary, "No keys held right now.");
  assert.equal(m.sustain.summary, "Sustain pedal is up.");
  assert.equal(m.lastKey.summary, "Play a key and it will show up here.");
  const empty = pianoActivityModel(null);
  assert.equal(empty.keysHeld.count, 0);
});

test("activity model never leaks raw event names or enums", () => {
  const m = pianoActivityModel({
    pressed_notes: [21, 60, 108],
    sustain: { pressed: true },
    last_input: { type: "NOTE_OFF", note: 60 },
  });
  const text = JSON.stringify(m);
  assert.equal(BANNED_ENUMS.test(text), false, text);
  assert.equal(BANNED_TECH.test(text), false, text);
});

/* ---------------- LED guidance presentation ---------------- */

test("led: ready / off / unavailable / not configured / unknown", () => {
  const ready = pianoLedModel({ configured: true, enabled: true, available: true });
  assert.equal(ready.state, "ready");
  assert.equal(ready.chip.label, "Guidance lights ready");
  const off = pianoLedModel({ configured: true, enabled: false, available: false });
  assert.equal(off.state, "off");
  assert.equal(off.chip.label, "Lights off");
  const unavailable = pianoLedModel({ configured: true, enabled: true, available: false });
  assert.equal(unavailable.state, "unavailable");
  assert.equal(unavailable.chip.label, "Lights unavailable");
  const unconfigured = pianoLedModel({ configured: false, enabled: false, available: false });
  assert.equal(unconfigured.state, "not_configured");
  assert.equal(unconfigured.chip.label, "No guidance lights");
  const unknown = pianoLedModel(null);
  assert.equal(unknown.state, "unknown");
  assert.equal(unknown.chip.label, "Lights unavailable");
});

test("led model never claims correctness and never leaks enums", () => {
  for (const led of [null, { configured: false }, { configured: true, enabled: false }, { configured: true, enabled: true, available: false }, { configured: true, enabled: true, available: true }]) {
    const m = pianoLedModel(led);
    const text = `${m.chip.label} ${m.guidance}`;
    assert.ok(!/correct|incorrect|answer|grade/i.test(text), `correctness claim: ${text}`);
    assert.equal(BANNED_ENUMS.test(text), false, text);
    assert.equal(BANNED_TECH.test(text), false, text);
  }
});

/* ---------------- identity + technical disclosure ---------------- */

test("identity: product + display code only; UUID stays out", () => {
  const m = pianoIdentityModel({ product: "Piano Core", device_id: "uuid-123", display_code: "ABCD-1234" });
  assert.equal(m.name, "Piano Core");
  assert.equal(m.code, "ABCD-1234");
  assert.ok(!JSON.stringify(m).includes("uuid-123"), "UUID leaked into consumer identity");
  assert.equal(pianoIdentityModel(null), null);
  assert.equal(pianoIdentityModel({}).name, "Piano Core");
});

test("technical rows: raw identifiers live only behind the disclosure", () => {
  const rows = pianoTechRows({
    device: { device_id: "uuid-123" },
    led: { transport: { host: "127.0.0.1", port: 8765 }, last_error: "boom" },
    midiConnection: "CONNECTED",
  });
  const labels = rows.map((r) => r.label);
  assert.deepEqual(labels, ["Device ID", "MIDI in", "LED transport", "LED last error"]);
  assert.deepEqual(
    rows.map((r) => r.value),
    ["uuid-123", "CONNECTED", "127.0.0.1:8765", "boom"]
  );
  assert.deepEqual(pianoTechRows({}), []);
});

/* ---------------- settings model ---------------- */

test("settings: exactly the real groups in order (visuals, guidance, about)", () => {
  const m = settingsModel({ visualMode: "falling", hands: { enabled: true, camera: "FULL", quality: "AUTO" }, led: null, device: null, midiConnection: "CONNECTED" });
  assert.deepEqual(m.groups.map((g) => g.id), ["visuals", "guidance", "about"]);
  const controls = m.groups[0].controls.map((c) => c.id);
  assert.deepEqual(controls, [
    "settings-visual-mode",
    "settings-hands-enabled",
    "settings-hands-camera",
    "settings-hands-quality",
  ]);
});

test("settings: guidance group is a read-only status, never fake toggles", () => {
  const m = settingsModel({ led: { configured: true, enabled: true, available: true } });
  const guidance = m.groups.find((g) => g.id === "guidance");
  assert.ok(!("controls" in guidance) || guidance.controls === undefined);
  assert.equal(guidance.led.chip.label, "Guidance lights ready");
});

test("settings: guidance status mirrors every LED truth", () => {
  assert.equal(settingsLedStatus(null).chip.label, "Lights unavailable");
  assert.equal(settingsLedStatus({ configured: false }).chip.label, "No guidance lights");
  assert.equal(settingsLedStatus({ configured: true, enabled: false }).chip.label, "Lights off");
  assert.equal(settingsLedStatus({ configured: true, enabled: true, available: false }).chip.label, "Lights unavailable");
  assert.equal(settingsLedStatus({ configured: true, enabled: true, available: true }).chip.label, "Guidance lights ready");
});

test("settings: about rows are consumer values (no UUID, no raw enums)", () => {
  const rows = settingsAboutRows({ device: { product: "Piano Core", display_code: "ABCD-1234", device_id: "uuid-123" }, midiConnection: "CONNECTED" });
  const text = JSON.stringify(rows);
  assert.ok(text.includes("Piano connected"));
  assert.ok(text.includes("ABCD-1234"));
  assert.ok(!text.includes("uuid-123"), "UUID leaked into About");
  assert.equal(BANNED_ENUMS.test(text), false, text);
  const offline = settingsAboutRows({ device: null, midiConnection: "CONNECTED", coreOffline: true });
  assert.equal(offline[0].value, "Reconnecting…");
});

test("settings: persistence note claims browser-local only", () => {
  const m = settingsModel({});
  assert.ok(m.persistenceNotice.includes("this browser"));
  assert.ok(m.persistenceNotice.includes("no other preferences yet"));
});

test("settings model never leaks technical or invented-capability copy", () => {
  const m = settingsModel({
    visualMode: "hybrid",
    hands: { enabled: false, camera: "FOLLOW", quality: "BATTERY" },
    led: { configured: false },
    device: { product: "Piano Core", display_code: "ABCD-1234" },
    midiConnection: "DISCONNECTED",
  });
  const text = JSON.stringify(m);
  for (const banned of ["StateStore", "websocket", "HTTP", "endpoint", "mutex", "localhost", "127.0.0.1", "WAIT_FOR_NOTE", "SECTION_PRACTICE", "REPEAT_UNTIL_CORRECT", "DISCONNECTED", "accuracy", "stars", "mastery", "streak", "microphone", "Bluetooth", "BLE"]) {
    assert.ok(!text.includes(banned), `settings leak: ${banned}`);
  }
});

if (failures) {
  console.error(`FAIL ${failures}; PASS ${passes}`);
  process.exit(1);
}
console.log(`PASS ${passes}/${passes}`);
