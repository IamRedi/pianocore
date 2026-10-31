// PIANO-IMMERSIVE-UI: workspace view-state unit tests (pure logic).
// The workspace view is presentation state only: it must never map onto
// the accepted sheet/falling/hybrid renderer-mode contract with a foreign
// value, must default to Falling, must drop Hands when hands are disabled,
// and must pin the keyboard on the Piano route.
import assert from "node:assert/strict";
import {
  WORKSPACE_VIEWS,
  DEFAULT_WORKSPACE_VIEW,
  loadWorkspaceView,
  storeWorkspaceView,
  availableWorkspaceViews,
  resolveWorkspaceView,
  activeRendererFor,
  loadPlayerView,
  resolvePlayerView,
  rendererModeForView,
  workspaceViewLabel,
} from "../../src/piano_core/web/static/ui008/workspace.js";

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

test("workspace views are exactly falling/piano/hands/sheet; default Falling", () => {
  assert.deepEqual([...WORKSPACE_VIEWS], ["falling", "piano", "hands", "sheet"]);
  assert.equal(DEFAULT_WORKSPACE_VIEW, "falling");
});

test("persisted view loads only known values; junk falls back to Falling", () => {
  const store = new Map();
  const fake = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) };
  assert.equal(loadWorkspaceView(fake), "falling");
  store.set("piano-core.workspace-view", "hands");
  assert.equal(loadWorkspaceView(fake), "hands");
  store.set("piano-core.workspace-view", "hybrid");
  assert.equal(loadWorkspaceView(fake), "falling", "hybrid is not a workspace view");
  store.set("piano-core.workspace-view", "piano");
  assert.equal(loadWorkspaceView(fake), "piano");
  assert.doesNotThrow(() => loadWorkspaceView(null));
  assert.doesNotThrow(() => storeWorkspaceView(fake, "bogus"));
  assert.equal(store.get("piano-core.workspace-view"), "piano", "bogus value never stored");
});

test("availability: hands requires the hands preference; piano route pins piano", () => {
  assert.deepEqual(
    availableWorkspaceViews({ handsEnabled: true, route: "learn" }),
    ["falling", "piano", "hands", "sheet"]
  );
  assert.deepEqual(
    availableWorkspaceViews({ handsEnabled: false, route: "learn" }),
    ["falling", "piano", "sheet"]
  );
  assert.deepEqual(availableWorkspaceViews({ handsEnabled: true, route: "piano" }), ["piano"]);
});

test("resolve falls back to Falling, then to the first available view", () => {
  assert.equal(resolveWorkspaceView("hands", ["falling", "piano", "sheet"]), "falling");
  assert.equal(resolveWorkspaceView("piano", ["piano"]), "piano");
  assert.equal(resolveWorkspaceView("falling", ["sheet"]), "sheet");
});

test("active renderer: notes for falling/sheet, keyboard on piano route, hands only in hands view", () => {
  assert.equal(activeRendererFor("falling", "learn"), "visual");
  assert.equal(activeRendererFor("sheet", "learn"), "visual");
  assert.equal(activeRendererFor("piano", "learn"), "piano");
  assert.equal(activeRendererFor("hands", "learn"), "hands");
  assert.equal(activeRendererFor("falling", "piano"), "piano", "piano route pins the keyboard");
  assert.equal(activeRendererFor("cover", "player"), null, "Now Playing cover paints no scene");
  assert.equal(activeRendererFor("falling", "player"), "visual");
  assert.equal(activeRendererFor("hands", "player"), "hands");
  assert.equal(activeRendererFor("falling", "home"), null, "no stage route, no renderer");
});

test("Now Playing view: cover by default, hands falls back to notes when off", () => {
  assert.equal(loadPlayerView(null), "cover");
  assert.equal(loadPlayerView({ getItem: () => "hands" }), "hands");
  assert.equal(loadPlayerView({ getItem: () => "sheet" }), "cover", "unknown values fail closed");
  assert.equal(resolvePlayerView("hands", { handsEnabled: false }), "falling");
  assert.equal(resolvePlayerView("hands", { handsEnabled: true }), "hands");
  assert.equal(resolvePlayerView("bogus"), "cover");
});

test("renderer-mode contract: only accepted sheet/falling values ever map", () => {
  assert.equal(rendererModeForView("falling"), "falling");
  assert.equal(rendererModeForView("sheet"), "sheet");
  assert.equal(rendererModeForView("piano"), null, "piano view never touches the mode contract");
  assert.equal(rendererModeForView("hands"), null, "hands value never reaches the mode contract");
  for (const view of WORKSPACE_VIEWS) {
    const mode = rendererModeForView(view);
    assert.ok(mode === null || ["sheet", "falling", "hybrid"].includes(mode));
  }
});

test("labels stay consumer wording", () => {
  assert.equal(workspaceViewLabel("falling"), "Falling");
  assert.equal(workspaceViewLabel("hands"), "Hands");
});

if (failures) {
  console.error(`FAIL ${failures}; PASS ${passes}`);
  process.exit(1);
}
console.log(`PASS ${passes}/${passes}`);
