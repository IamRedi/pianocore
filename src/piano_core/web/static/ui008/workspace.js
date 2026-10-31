/* PIANO-IMMERSIVE-UI: shared visual workspace presentation controller.
 *
 * Presentation ONLY. One scene container (the existing visual-stage panes,
 * the existing 88-key keyboard and the existing 3D-hands stage — all single
 * renderer instances) is PRESENTED inside the active stage route by moving
 * the container between per-route slots (the accepted node-move pattern —
 * listeners and renderer instances are never duplicated).
 *
 * The workspace view (falling | piano | hands | sheet) is browser-local
 * presentation state. It is deliberately SEPARATE from the accepted
 * sheet/falling/hybrid renderer-mode contract: switching views never sends
 * a value outside that contract, never issues a musical command, never
 * restarts playback or practice, and never touches musical time. Inactive
 * views stop painting (the app gates its render ticks on activeRenderer)
 * while the musical poll and engines keep running.
 */

export const WORKSPACE_VIEWS = Object.freeze(["falling", "piano", "hands", "sheet"]);
export const DEFAULT_WORKSPACE_VIEW = "falling";
export const WORKSPACE_STORAGE_KEY = "piano-core.workspace-view";
export const WORKSPACE_ROUTES = Object.freeze(["learn", "piano", "player"]);
/* Now Playing shows the cover by default; Notes and Hands present the same
 * shared scene while the song plays. Stored apart from the Learn view. */
export const PLAYER_VIEWS = Object.freeze(["cover", "falling", "hands"]);
export const DEFAULT_PLAYER_VIEW = "cover";
export const PLAYER_VIEW_STORAGE_KEY = "piano-core.player-view";

const VIEW_SET = new Set(WORKSPACE_VIEWS);

export function loadWorkspaceView(storage, key = WORKSPACE_STORAGE_KEY) {
  try {
    const raw = storage ? storage.getItem(key) : null;
    return VIEW_SET.has(raw) ? raw : DEFAULT_WORKSPACE_VIEW;
  } catch {
    return DEFAULT_WORKSPACE_VIEW;
  }
}

export function storeWorkspaceView(storage, view, key = WORKSPACE_STORAGE_KEY) {
  if (!VIEW_SET.has(view)) return;
  try {
    storage?.setItem(key, view);
  } catch {
    // Preference persistence is optional; presentation continues.
  }
}

export function loadPlayerView(storage, key = PLAYER_VIEW_STORAGE_KEY) {
  try {
    const raw = storage ? storage.getItem(key) : null;
    return PLAYER_VIEWS.includes(raw) ? raw : DEFAULT_PLAYER_VIEW;
  } catch {
    return DEFAULT_PLAYER_VIEW;
  }
}

/** The Now Playing view actually presented: Hands falls back to Notes when
 * the hands preference is off. */
export function resolvePlayerView(requested, { handsEnabled = true } = {}) {
  if (!PLAYER_VIEWS.includes(requested)) return DEFAULT_PLAYER_VIEW;
  if (requested === "hands" && handsEnabled !== true) return "falling";
  return requested;
}

/** Hands view requires the hands preference; every route pins the keyboard. */
export function availableWorkspaceViews({ handsEnabled = true, route = "learn" } = {}) {
  if (route === "piano") return ["piano"];
  return WORKSPACE_VIEWS.filter((view) => view !== "hands" || handsEnabled === true);
}

export function resolveWorkspaceView(requested, available) {
  if (available.includes(requested)) return requested;
  if (available.includes(DEFAULT_WORKSPACE_VIEW)) return DEFAULT_WORKSPACE_VIEW;
  return available[0] ?? DEFAULT_WORKSPACE_VIEW;
}

/**
 * Which renderer family the visible view paints. "visual" = the existing
 * falling/sheet stage (mode follows the accepted renderer-mode contract),
 * "piano" = the 88-key keyboard, "hands" = the 3D hands stage. null when
 * no stage route is active (the scene is simply not presented).
 */
export function activeRendererFor(view, route = "learn") {
  if (!WORKSPACE_ROUTES.includes(route)) return null;
  if (route === "piano") return "piano";
  if (route === "player") {
    if (view === "hands") return "hands";
    return view === "falling" ? "visual" : null;
  }
  if (view === "piano") return "piano";
  if (view === "hands") return "hands";
  return "visual";
}

/**
 * The renderer-mode value (of the ACCEPTED sheet/falling/hybrid contract)
 * that a notes-scene view presents. piano/hands views never touch the
 * renderer-mode contract (null).
 */
export function rendererModeForView(view) {
  if (view === "falling") return "falling";
  if (view === "sheet") return "sheet";
  return null;
}

const VIEW_LABELS = Object.freeze({
  falling: "Falling",
  piano: "Piano",
  hands: "Hands",
  sheet: "Sheet",
});

export function workspaceViewLabel(view) {
  return VIEW_LABELS[view] ?? view;
}

/* ---------------------------------------------------------------------
 * DOM controller (browser only)
 * ------------------------------------------------------------------- */

export function createWorkspaceController({
  windowRef = globalThis.window,
  documentRef = globalThis.document,
  storage = globalThis.localStorage,
  scene,
  slots = {},
  selects = [],
  views = {},
  isHandsEnabled = () => true,
  onPresentChange = () => {},
} = {}) {
  if (!scene) throw new TypeError("scene element is required");

  let route = "home";
  let view = loadWorkspaceView(storage);
  let playerView = loadPlayerView(storage);

  function slotFor() {
    return slots[route] ?? slots.learn ?? scene.parentElement;
  }

  function apply({ present = true } = {}) {
    const available = availableWorkspaceViews({ handsEnabled: isHandsEnabled(), route });
    // Piano is a pinned surface; Player and other routes must not overwrite
    // the saved Learn view. Only Learn resolves its own preference.
    if (route === "learn") view = resolveWorkspaceView(view, available);
    const shownPlayerView = resolvePlayerView(playerView, { handsEnabled: isHandsEnabled() });
    const presentedView = route === "piano" ? "piano" : route === "player" ? shownPlayerView : view;
    const renderer = route === "player"
      ? activeRendererFor(shownPlayerView, route)
      : activeRendererFor(view, route);

    const showingKey = renderer === "visual"
      ? "notes"
      : renderer === "piano"
        ? "piano"
        : renderer === "hands"
          ? "hands"
          : null;
    for (const [name, node] of Object.entries(views)) {
      if (node) node.hidden = name !== showingKey;
    }

    // The ONE scene container is moved, never cloned.
    const slot = slotFor();
    if (present && slot && scene.parentElement !== slot) slot.append(scene);
    scene.hidden = !present || !WORKSPACE_ROUTES.includes(route) || renderer === null;

    for (const select of selects) {
      if (!select) continue;
      for (const option of select.options) {
        const allowed = available.includes(option.value);
        option.disabled = !allowed;
        if (!allowed && option.value === "hands") option.selected = false;
      }
      select.value = resolveWorkspaceView(view, available);
    }
    if (scene) scene.dataset.view = WORKSPACE_ROUTES.includes(route) ? presentedView : "none";
    for (const button of playerButtons) {
      button.setAttribute("aria-pressed", String(button.dataset.playerView === shownPlayerView));
      if (button.dataset.playerView === "hands") button.disabled = !isHandsEnabled();
    }
    if (documentRef?.body) documentRef.body.dataset.playerView = shownPlayerView;
    onPresentChange({ view: route === "player" ? shownPlayerView : view, route, renderer });
    return { view, renderer };
  }

  for (const select of selects) {
    select?.addEventListener("change", () => {
      view = resolveWorkspaceView(select.value, availableWorkspaceViews({ handsEnabled: isHandsEnabled(), route }));
      storeWorkspaceView(storage, view);
      apply();
    });
  }

  const playerButtons = [...(documentRef?.querySelectorAll?.("[data-player-view]") ?? [])];
  for (const button of playerButtons) {
    button.addEventListener("click", () => {
      playerView = resolvePlayerView(button.dataset.playerView);
      try {
        storage?.setItem(PLAYER_VIEW_STORAGE_KEY, playerView);
      } catch {
        // Preference persistence is optional; presentation continues.
      }
      apply();
    });
  }

  apply();

  return Object.freeze({
    setRoute(next) {
      route = next;
      return apply();
    },
    setView(requested) {
      view = resolveWorkspaceView(requested, availableWorkspaceViews({ handsEnabled: isHandsEnabled(), route }));
      storeWorkspaceView(storage, view);
      return apply();
    },
    currentView() {
      if (route === "player") return resolvePlayerView(playerView, { handsEnabled: isHandsEnabled() });
      return route === "piano" ? "piano" : view;
    },
    activeRenderer() {
      if (route === "player") {
        return activeRendererFor(resolvePlayerView(playerView, { handsEnabled: isHandsEnabled() }), route);
      }
      return activeRendererFor(view, route);
    },
    refresh() {
      return apply();
    },
    present(present) {
      return apply({ present });
    },
  });
}
