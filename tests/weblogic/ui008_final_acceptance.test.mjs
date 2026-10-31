// PIANO-UI-008 U8-P6: deterministic final-acceptance gates — bounded
// hygiene (no duplicate dom entries), cross-surface structural
// consistency, consumer-copy retention, accepted option/value contracts,
// and the documented P6-C polish CSS contracts. Static and pure-model
// checks only; the integrated runtime loop lives in the browser suite.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { LEARN_FEATURES } from "../../src/piano_core/web/static/ui008/learn.js";
import { SETTINGS_FEATURES } from "../../src/piano_core/web/static/ui008/settings.js";
import {
  PIANO_FEATURES,
  pianoIdentityModel,
} from "../../src/piano_core/web/static/ui008/piano.js";

const ROOT = resolve(import.meta.dirname, "../..");
const APP = readFileSync(resolve(ROOT, "src/piano_core/web/static/app.js"), "utf8");
const HTML = readFileSync(resolve(ROOT, "src/piano_core/web/static/index.html"), "utf8");
const CSS = readFileSync(resolve(ROOT, "src/piano_core/web/static/styles.css"), "utf8");

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

/* ---------------- bounded hygiene (U8P5-N1 closure) ---------------- */

test("hygiene: the dom map declares no duplicate element keys", () => {
  const domStart = APP.indexOf("const dom = {");
  assert.ok(domStart > 0, "dom map not found");
  const domEnd = APP.indexOf("};", domStart);
  const domBody = APP.slice(domStart, domEnd);
  const keys = [...domBody.matchAll(/^    ([A-Za-z0-9_]+): \$\("/gm)].map((m) => m[1]);
  assert.ok(keys.length > 50, `dom map unexpectedly small: ${keys.length}`);
  const seen = new Set();
  const dups = [];
  for (const key of keys) {
    if (seen.has(key)) dups.push(key);
    seen.add(key);
  }
  assert.deepEqual(dups, [], `duplicate dom keys: ${dups.join(", ")}`);
});

test("hygiene: removed ids no longer referenced by app.js lookups", () => {
  assert.ok(!APP.includes('$("piano-midi")'), "stale piano-midi lookup");
  assert.equal(HTML.includes('id="piano-midi"'), false, "stale piano-midi markup");
});

/* ---------------- structural cross-surface consistency ---------------- */

test("structure: all five top-level routes have surfaces and navigation", () => {
  // The song library lives inside Music (player): no separate Library route.
  assert.equal(HTML.includes('data-ui008-route="library"'), false, "no separate Library navigation");
  for (const route of ["home", "player", "learn", "piano", "settings"]) {
    assert.ok(
      HTML.includes(`data-ui008-route="${route}"`),
      `navigation missing for ${route}`
    );
    assert.ok(
      /data-ui008-surface="[^"]*\broute\b[^"]*"/.test(HTML) === false &&
        new RegExp(`data-ui008-surface="[^"]*\\b${route}\\b[^"]*"`).test(HTML),
      `surface missing for ${route}`
    );
  }
  assert.equal(HTML.split('id="live-keyboard"').length - 1, 1, "keyboard must be unique");
});

test("structure: one status-chip family across Learn, Piano, LED and Settings", () => {
  for (const id of ["ui008-learn-state", "piano-state", "led-chip", "settings-led-chip"]) {
    assert.ok(HTML.includes(`id="${id}"`), `chip missing: ${id}`);
  }
  const chipClasses = [...HTML.matchAll(/id="(ui008-learn-state|piano-state|led-chip|settings-led-chip)" class="([^"]+)"/g)].map((m) => m[2]);
  assert.ok(chipClasses.every((c) => c.split(/\s+/).includes("learn-state")), `inconsistent chip classes: ${chipClasses}`);
});

test("structure: Settings visual switcher reuses the accepted visual-mode classes", () => {
  const settingsSwitch = HTML.match(/id="settings-visual-mode" class="([^"]+)"/);
  const playerSwitch = HTML.match(/class="(visual-mode-switch)"/);
  assert.ok(settingsSwitch && playerSwitch);
  assert.equal(settingsSwitch[1], playerSwitch[1]);
});

/* ---------------- consumer copy retention ---------------- */

test("copy: primary visible markup keeps the consumer-copy ban list", () => {
  const withoutDisclosure = HTML.replace(
    /<section class="card settings-foundation[\s\S]*?<\/section>/,
    ""
  ).replace(/<script[\s\S]*?<\/script>/g, "");
  const text = withoutDisclosure.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
  for (const banned of ["StateStore", "websocket", "WebSocket", "endpoint", "mutex", "localhost", "127.0.0.1", "NOTE_ON", "NOTE_OFF", "WAIT_FOR_NOTE", "SECTION_PRACTICE", "REPEAT_UNTIL_CORRECT", "activity_conflict", "playback_already_active", "service journal", "Core output ownership", "MIDI input"]) {
    assert.ok(!text.includes(banned), `leak: ${banned}`);
  }
});

test("copy: consumer mode labels present next to their Core values", () => {
  for (const [value, label] of [["WAIT_FOR_NOTE", "Wait for me"], ["SECTION_PRACTICE", "Section practice"], ["REPEAT_UNTIL_CORRECT", "Repeat until correct"]]) {
    assert.ok(HTML.includes(`value="${value}"`), `enum value missing: ${value}`);
    assert.ok(HTML.includes(`>${label}<`), `consumer label missing: ${label}`);
  }
});

test("copy: error text stays bounded and consumer-safe", async () => {
  const { errorText } = await import("../../src/piano_core/web/static/app.js");
  for (const code of ["activity_conflict", "playback_already_active", "internal_error", "malformed_request", "library_unavailable"]) {
    const message = errorText(code);
    assert.ok(!/\b\d{3}\b/.test(message), `raw status code in ${code}: ${message}`);
    assert.ok(!/journal|StateStore|endpoint|HTTP/i.test(message), `technical leak in ${code}: ${message}`);
  }
  // retained accepted phrasing contracts
  assert.match(errorText("playback_already_active"), /already active/i);
  assert.match(errorText("activity_conflict"), /cannot run together/);
});

/* ---------------- accepted option/value contracts unchanged ---------------- */

test("contracts: LED mode options remain exactly the real Core modes", () => {
  const select = HTML.match(/<select id="led-mode">([\s\S]*?)<\/select>/)[1];
  const values = [...select.matchAll(/value="([^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual(values, ["off", "playback", "physical", "all"]);
});

test("contracts: identity model keeps the name/code split used by P6 polish", () => {
  const m = pianoIdentityModel({ product: "Piano Core", display_code: "AB-01", device_id: "u" });
  assert.deepEqual(m, { name: "Piano Core", code: "AB-01" });
  assert.ok(!JSON.stringify(m).includes("u"), "UUID leaked");
});

test("contracts: frozen feature maps unchanged by P6", () => {
  assert.ok(Object.isFrozen(LEARN_FEATURES) && Object.isFrozen(SETTINGS_FEATURES) && Object.isFrozen(PIANO_FEATURES));
  assert.equal(SETTINGS_FEATURES.durablePersistence, false);
  assert.equal(PIANO_FEATURES.midiConnectCommand, false);
  assert.equal(LEARN_FEATURES.scoreMetrics, false);
});

/* ---------------- P6-C polish CSS contracts ---------------- */

test("polish: identity code renders as its own muted line", () => {
  assert.ok(HTML.includes('id="piano-identity-code"'), "identity code element missing");
  assert.ok(/\.piano-identity-code\s*\{/.test(CSS), "identity code CSS missing");
  assert.ok(/display:\s*block/.test(CSS.match(/\.piano-identity-code\s*\{[^}]*\}/)[0]));
});

test("polish: LED rows reflow and toast clears the bottom nav on phone", () => {
  assert.ok(/@media \(max-width: 899px\)[\s\S]*?\.led-row \{ flex-wrap: wrap;/.test(CSS), "LED row reflow missing");
  assert.ok(/@media \(max-width: 899px\)[\s\S]*?\.toast,\s*\n\s*#toast \{\s*\n\s*bottom: calc\(4\.5rem/.test(CSS), "toast bottom-nav clearance missing");
});

if (failures) {
  console.error(`FAIL ${failures}; PASS ${passes}`);
  process.exit(1);
}
console.log(`PASS ${passes}/${passes}`);
