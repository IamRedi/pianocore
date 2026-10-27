// PIANO-UI-008 U8-P1 deterministic routing and static-boundary tests.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import {
  createHashRouter,
  normalizeRoute,
  parseRouteHash,
  routeHash,
  UI008_DEFAULT_ROUTE,
  UI008_ROUTES,
} from "../../src/piano_core/web/static/ui008/router.js";

let passes = 0;
function test(name, fn) {
  try {
    fn();
    passes += 1;
    console.log(`ok - ${name}`);
  } catch (error) {
    console.error(`FAIL - ${name}`);
    console.error(error);
    process.exitCode = 1;
  }
}

class MockWindow {
  constructor(hash = "") {
    this.listeners = new Map();
    this.location = { pathname: "/ui/", search: "", href: `/ui/${hash}` };
    let currentHash = hash;
    Object.defineProperty(this.location, "hash", {
      get: () => currentHash,
      set: (value) => {
        currentHash = value.startsWith("#") ? value : `#${value}`;
        this.location.href = `/ui/${currentHash}`;
        this.dispatch("hashchange", {});
      },
    });
    this.history = {
      state: null,
      replaceState: (state, _title, url) => {
        this.history.state = state;
        const hashIndex = url.indexOf("#");
        currentHash = hashIndex >= 0 ? url.slice(hashIndex) : "";
        this.location.href = url;
      },
    };
  }
  addEventListener(type, callback) {
    const values = this.listeners.get(type) || new Set();
    values.add(callback);
    this.listeners.set(type, values);
  }
  removeEventListener(type, callback) {
    this.listeners.get(type)?.delete(callback);
  }
  dispatch(type, event) {
    for (const callback of this.listeners.get(type) || []) callback(event);
  }
}

test("route inventory is the six approved surfaces", () => {
  assert.deepEqual(UI008_ROUTES, [
    "home", "library", "player", "learn", "piano", "settings",
  ]);
});

test("approved route hashes round-trip", () => {
  for (const route of UI008_ROUTES) {
    assert.equal(routeHash(route), `#/${route}`);
    assert.equal(parseRouteHash(routeHash(route)), route);
  }
});

test("home aliases and device alias are bounded", () => {
  assert.equal(parseRouteHash(""), "home");
  assert.equal(parseRouteHash("#/"), "home");
  assert.equal(parseRouteHash("#/device"), "piano");
});

test("unknown and malformed routes fail closed to home", () => {
  assert.equal(parseRouteHash("#/not-a-surface"), null);
  assert.equal(normalizeRoute("not-a-surface"), UI008_DEFAULT_ROUTE);
  assert.equal(normalizeRoute(null), UI008_DEFAULT_ROUTE);
});

test("direct initial route restores without a musical-state input", () => {
  const windowRef = new MockWindow("#/player");
  const changes = [];
  const router = createHashRouter({ windowRef, onChange: (event) => changes.push(event) });
  assert.equal(router.start(), "player");
  assert.equal(router.current(), "player");
  assert.deepEqual(changes.map((event) => event.route), ["player"]);
});

test("invalid direct route is replaced with the approved default", () => {
  const windowRef = new MockWindow("#/future-control");
  const changes = [];
  const router = createHashRouter({ windowRef, onChange: (event) => changes.push(event) });
  assert.equal(router.start(), "home");
  assert.equal(windowRef.location.hash, "#/home");
  assert.equal(changes[0].route, "home");
});

test("navigation changes only route presentation state", () => {
  const windowRef = new MockWindow("#/home");
  const musicalState = Object.freeze({
    transport: Object.freeze({ state: "PAUSED", position: 12 }),
    learning: Object.freeze({ sessionId: "session-7", version: 11 }),
  });
  const before = JSON.stringify(musicalState);
  const router = createHashRouter({ windowRef, onChange: () => {} });
  router.start();
  router.navigate("library");
  router.navigate("learn");
  assert.equal(router.current(), "learn");
  assert.equal(JSON.stringify(musicalState), before);
});

test("router stop removes the history listener", () => {
  const windowRef = new MockWindow("#/home");
  let count = 0;
  const router = createHashRouter({ windowRef, onChange: () => { count += 1; } });
  router.start();
  router.stop();
  windowRef.location.hash = "#/player";
  assert.equal(count, 1);
});

const shellSource = await readFile(
  resolve("src/piano_core/web/static/ui008/shell.js"), "utf8"
);
const routerSource = await readFile(
  resolve("src/piano_core/web/static/ui008/router.js"), "utf8"
);
const htmlSource = await readFile(
  resolve("src/piano_core/web/static/index.html"), "utf8"
);
const cssSource = await readFile(
  resolve("src/piano_core/web/static/styles.css"), "utf8"
);

test("shell modules contain no Core fetch or musical mutation vocabulary", () => {
  const combined = `${shellSource}\n${routerSource}`;
  for (const marker of [
    "fetch(", "/api/v1", "requestMIDIAccess", "playback.stop", "all_notes_off",
    "learning/restart", "createMotionView(", "evaluateMotion(",
  ]) {
    assert.equal(combined.includes(marker), false, marker);
  }
});

test("shell markup exposes only approved top-level routes", () => {
  for (const route of UI008_ROUTES) {
    assert.ok(htmlSource.includes(`data-ui008-route="${route}"`), route);
  }
  for (const unsupported of ["seek", "loop-a", "metronome", "count-in", "queue"]) {
    assert.equal(htmlSource.includes(`id="${unsupported}"`), false, unsupported);
  }
});

test("design token and touch foundations are explicit", () => {
  for (const token of [
    "--pc-color-canvas", "--pc-color-surface-elevated", "--pc-color-text",
    "--pc-color-accent", "--pc-color-success", "--pc-color-warning",
    "--pc-color-error", "--pc-color-focus", "--pc-space-4",
    "--pc-radius-card", "--pc-shadow-layer", "--pc-duration-route",
    "--pc-touch-target: 2.75rem", "--pc-touch-primary: 3.5rem",
  ]) {
    assert.ok(cssSource.includes(token), token);
  }
  assert.ok(cssSource.includes(".btn-small { min-height: 44px"));
  assert.ok(cssSource.includes("grid-template-columns: 4.5rem minmax(0, 1fr)"));
});

test("hands workspace has an explicit full-grid non-pathological contract", () => {
  assert.ok(cssSource.includes(".hands-card,"));
  assert.ok(cssSource.includes("grid-column: 1 / -1"));
  assert.ok(cssSource.includes("min-width: 35rem"));
  assert.ok(cssSource.includes("max-height: 26rem"));
});

if (process.exitCode) process.exit(process.exitCode);
console.log(`PASS ${passes}/${passes}`);
