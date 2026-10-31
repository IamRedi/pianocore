/* PIANO-UI-008 U8-P1: bookmarkable presentation routing only.
 *
 * Hash routes keep every destination inside the existing allowlisted web
 * document. Route state selects presentation surfaces; it never reads or
 * mutates playback, Learning, Practice, MIDI, fingering, or renderer state.
 */

export const UI008_ROUTES = Object.freeze([
  "home",
  "player",
  "learn",
  "piano",
  "settings",
]);

export const UI008_DEFAULT_ROUTE = "home";

const ROUTE_SET = new Set(UI008_ROUTES);
// The song library lives inside the Music (player) surface: old #/library
// links land there with search ready.
const ROUTE_ALIASES = Object.freeze({ "": "home", "/": "home", device: "piano", library: "player" });

export function routeHash(route) {
  const normalized = normalizeRoute(route);
  return `#/${normalized}`;
}

export function parseRouteHash(hash) {
  if (typeof hash !== "string") return null;
  const raw = hash.replace(/^#/, "").split(/[?&]/, 1)[0].replace(/^\/+|\/+$/g, "");
  const candidate = ROUTE_ALIASES[raw] ?? raw.toLowerCase();
  return ROUTE_SET.has(candidate) ? candidate : null;
}

export function normalizeRoute(route) {
  if (typeof route !== "string") return UI008_DEFAULT_ROUTE;
  const raw = route.replace(/^#/, "").replace(/^\/+|\/+$/g, "").toLowerCase();
  const candidate = ROUTE_ALIASES[raw] ?? raw;
  return ROUTE_SET.has(candidate) ? candidate : UI008_DEFAULT_ROUTE;
}

function documentUrl(windowRef, route) {
  const { pathname, search } = windowRef.location;
  return `${pathname}${search}${routeHash(route)}`;
}

export function createHashRouter({ windowRef = globalThis.window, onChange } = {}) {
  if (!windowRef || typeof windowRef.addEventListener !== "function") {
    throw new TypeError("windowRef with event support is required");
  }
  if (typeof onChange !== "function") {
    throw new TypeError("onChange callback is required");
  }

  let currentRoute = null;
  let started = false;

  function apply(source) {
    const parsed = parseRouteHash(windowRef.location.hash);
    const route = parsed ?? UI008_DEFAULT_ROUTE;
    if (parsed === null) {
      windowRef.history.replaceState(
        windowRef.history.state,
        "",
        documentUrl(windowRef, route)
      );
    }
    const previousRoute = currentRoute;
    currentRoute = route;
    onChange(Object.freeze({ route, previousRoute, source }));
    return route;
  }

  function onHashChange() {
    apply("history");
  }

  return Object.freeze({
    start() {
      if (started) return currentRoute;
      started = true;
      windowRef.addEventListener("hashchange", onHashChange);
      return apply("initial");
    },
    navigate(route, { replace = false } = {}) {
      const normalized = normalizeRoute(route);
      if (normalized === currentRoute) return currentRoute;
      const url = documentUrl(windowRef, normalized);
      if (replace) {
        windowRef.history.replaceState(windowRef.history.state, "", url);
        return apply("replace");
      }
      windowRef.location.hash = routeHash(normalized);
      return normalized;
    },
    stop() {
      if (!started) return;
      windowRef.removeEventListener("hashchange", onHashChange);
      started = false;
    },
    current() {
      return currentRoute;
    },
  });
}
