/* PIANO-UI-008 U8-P3: premium Player workspace presentation controller.
 *
 * Presentation ONLY. The accepted PlayerCoordinator in app.js remains the
 * single transport/command authority; this module never issues a playback
 * or Learning command and never touches musical state. It owns exactly:
 * - PLAYER_FEATURES: the honest capability map that decides which Player
 *   controls may exist (Core has play/pause/resume/stop only — NO seek,
 *   so the timeline stays read-only);
 * - Performance Mode: a presentation shell (browser Fullscreen when
 *   available + CSS workspace dominance with graceful denial fallback);
 * - secondary-controls placement: one control set that lives inline at
 *   >=600px and inside the accepted bottom-sheet pattern on phone;
 * - consumer wording for the compact performance header.
 */

import { createLayerController } from "./layers.js";

export const PLAYER_FEATURES = Object.freeze({
  play: true,
  pause: true,
  resume: true,
  stop: true,
  seek: false, // no Core seek command exists; timeline is read-only
  liveTempo: false, // tempo applies at play start only
  transpose: false,
  abLoop: false,
  handSelection: false,
  metronome: false,
  countIn: false,
  sections: false,
  queue: false,
  favorites: false,
  ledControls: true, // existing semantic device commands (GET/PUT /led)
  fullscreen: true, // presentation-only
  visualModes: Object.freeze([]),
  handsCameraModes: Object.freeze([]),
});

export const SECONDARY_BREAKPOINT = "(min-width: 600px)";

/* ---------------------------------------------------------------------
 * Pure logic (runs under node too)
 * ------------------------------------------------------------------- */

/**
 * Consumer wording for the compact Performance Mode header. Uses only
 * presentation states already produced by the accepted app.js state
 * machine (no second state model).
 */
export function performanceHeaderModel({ title, stateLabel, fresh = true }) {
  const heading = title && String(title).trim() ? String(title).trim() : "Nothing playing";
  const status = fresh && stateLabel ? String(stateLabel) : "Reconnecting…";
  return Object.freeze({ heading, status });
}

/**
 * Performance Mode state machine (presentation flags only).
 * - request(active=false) -> "engaging" (fullscreen ask may be pending)
 * - confirm()             -> "active"
 * - exit()                -> "inactive" (idempotent)
 * The caller applies/removed the body flag from the returned state; no
 * musical or route action is ever taken here.
 */
export function createPerformanceState() {
  let state = "inactive";
  return Object.freeze({
    get current() {
      return state;
    },
    request() {
      if (state === "inactive") state = "engaging";
      return state;
    },
    confirm() {
      if (state === "engaging") state = "active";
      return state;
    },
    exit() {
      state = "inactive";
      return state;
    },
  });
}

/**
 * Decide where the one secondary-controls container lives. Exactly one
 * home at any time — the SAME DOM node is moved, never duplicated, so
 * listener identity is preserved.
 */
export function secondaryPlacement(matchesWide) {
  return matchesWide ? "inline" : "sheet";
}

/** LED quick-toggle payload uses ONLY the existing semantic PUT fields. */
export function ledTogglePayload(ledSnapshot) {
  const enabled = !(ledSnapshot && ledSnapshot.enabled === true);
  return { enabled };
}

/** Timeline is read-only by Core contract: expose progress, never a slider. */
export function timelineAria(progressFraction) {
  const clamped = Math.min(1, Math.max(0, Number(progressFraction) || 0));
  return { valuemin: 0, valuemax: 100, valuenow: Math.round(clamped * 100) };
}

/* ---------------------------------------------------------------------
 * DOM controllers (browser only)
 * ------------------------------------------------------------------- */

function el(id, documentRef) {
  return documentRef.getElementById(id);
}

/**
 * Performance Mode: requests browser fullscreen when available (denial is
 * fine — the CSS workspace dominance still applies), keeps Escape as the
 * universal exit, restores everything on route change, and NEVER emits a
 * musical command or a route change of its own.
 */
export function createPerformanceController({
  windowRef = globalThis.window,
  documentRef = globalThis.document,
  onStateChange = () => {},
} = {}) {
  const button = el("ui008-performance-btn", documentRef);
  const state = createPerformanceState();

  function applyBody() {
    documentRef.body.dataset.ui008Performance =
      state.current === "active" || state.current === "engaging" ? "on" : "off";
    button?.setAttribute("aria-pressed", String(state.current === "active"));
    onStateChange(state.current);
  }

  async function enter() {
    state.request();
    applyBody();
    const root = documentRef.documentElement;
    try {
      if (typeof root.requestFullscreen === "function") {
        await root.requestFullscreen();
      }
    } catch {
      // Denial is expected on some platforms: the CSS-only workspace
      // dominance remains the honest fallback.
    }
    state.confirm();
    applyBody();
  }

  function exit({ leaveFullscreen = true } = {}) {
    if (state.current === "inactive") return;
    state.exit();
    applyBody();
    if (
      leaveFullscreen &&
      documentRef.fullscreenElement &&
      typeof documentRef.exitFullscreen === "function"
    ) {
      documentRef.exitFullscreen().catch(() => {});
    }
  }

  function toggle() {
    if (state.current === "inactive") void enter();
    else exit();
  }

  button?.addEventListener("click", toggle);

  function onFullscreenChange() {
    // Fullscreen left by any external means (Esc at OS level, tab switch):
    // leave the presentation flag only if we had activated it.
    if (!documentRef.fullscreenElement && state.current === "active") {
      exit({ leaveFullscreen: false });
    }
  }

  function onKeyDown(event) {
    if (event.key !== "Escape") return;
    if (state.current === "active") {
      // Capture phase: while Performance Mode is active, Escape exits the
      // presentation BEFORE the global transport shortcut can see it —
      // exiting the view must never stop the music.
      event.preventDefault();
      event.stopPropagation();
      exit({ leaveFullscreen: false }); // browser exits fullscreen itself
    }
  }

  function onRouteChange(event) {
    if (event.detail?.route !== "player") exit({ leaveFullscreen: false });
  }

  windowRef.addEventListener("keydown", onKeyDown, true);
  documentRef.addEventListener("fullscreenchange", onFullscreenChange);
  windowRef.addEventListener("piano-ui-route-change", onRouteChange);

  return Object.freeze({
    toggle,
    enter,
    exit,
    state: () => state.current,
    destroy() {
      exit({ leaveFullscreen: false });
      windowRef.removeEventListener("keydown", onKeyDown, true);
      documentRef.removeEventListener("fullscreenchange", onFullscreenChange);
      windowRef.removeEventListener("piano-ui-route-change", onRouteChange);
    },
  });
}

/**
 * Secondary-controls placement (PIANO-IMMERSIVE-UI): the single
 * #ui008-player-secondary node lives inside the accepted bottom-sheet on
 * every viewport — the stage composition keeps the main screen essential-
 * only, and "More" opens the sheet. The node is never cloned, so listener
 * identity is preserved. (The old inline/sheet breakpoint placement is
 * retired with the card layout; the pure `secondaryPlacement` model above
 * is retained for compatibility with the accepted contract tests.)
 */
export function createSecondaryPlacement({
  windowRef = globalThis.window,
  documentRef = globalThis.document,
  query = SECONDARY_BREAKPOINT,
} = {}) {
  const secondary = el("ui008-player-secondary", documentRef);
  const sheetSlot = el("ui008-player-secondary-sheet", documentRef);
  const sheet = el("ui008-player-sheet", documentRef);
  const sheetClose = el("ui008-player-sheet-close", documentRef);
  const openButton = el("ui008-player-options", documentRef);
  if (!secondary || !sheetSlot) return null;

  // Never cloned: if some earlier placement left the node elsewhere, it is
  // moved back into its sheet home.
  if (secondary.parentElement !== sheetSlot) sheetSlot.append(secondary);
  if (openButton) openButton.hidden = false;

  const layer =
    sheet && sheetClose
      ? createLayerController({
          windowRef,
          documentRef,
          root: sheet,
          closeButton: sheetClose,
          layerId: "player-options",
        })
      : null;

  openButton?.addEventListener("click", () => layer?.open(openButton));

  return Object.freeze({
    home: () => "sheet",
    destroy() {
      sheetSlot.append(secondary);
    },
  });
}
