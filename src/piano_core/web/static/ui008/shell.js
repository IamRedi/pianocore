/* PIANO-UI-008 U8-P1 application shell.
 * Presentation composition only: the existing app.js remains the sole Core
 * integration point and owns every musical replica and command boundary.
 */

import { createLayerController } from "./layers.js";
import { createHashRouter, normalizeRoute, UI008_ROUTES } from "./router.js";

const ROUTE_LABELS = Object.freeze({
  home: "Home",
  player: "Music",
  learn: "Learn",
  piano: "Piano",
  settings: "Settings",
});

function routeTokens(element) {
  return new Set((element.dataset.ui008Surface || "").split(/\s+/).filter(Boolean));
}

function setSurfaceRoute(documentRef, route) {
  for (const surface of documentRef.querySelectorAll("[data-ui008-surface]")) {
    surface.dataset.ui008RouteHidden = String(!routeTokens(surface).has(route));
  }
}

function setCurrentNavigation(documentRef, route) {
  for (const link of documentRef.querySelectorAll("[data-ui008-route]")) {
    const current = link.dataset.ui008Route === route;
    if (current) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  for (const more of documentRef.querySelectorAll("[data-ui008-open-more]")) {
    if (["piano", "settings"].includes(route)) more.setAttribute("aria-current", "page");
    else more.removeAttribute("aria-current");
  }
}

export function initializePremiumShell({
  windowRef = globalThis.window,
  documentRef = globalThis.document,
} = {}) {
  if (!windowRef || !documentRef) return null;
  if (documentRef.documentElement.dataset.ui008ShellReady === "true") return null;

  const routeTitle = documentRef.getElementById("ui008-route-title");
  const main = documentRef.getElementById("main");
  const moreSheet = documentRef.getElementById("ui008-more-sheet");
  const moreClose = documentRef.getElementById("ui008-more-close");
  const moreLayer = createLayerController({
    windowRef,
    documentRef,
    root: moreSheet,
    closeButton: moreClose,
    layerId: "more",
  });

  let initial = true;
  const router = createHashRouter({
    windowRef,
    onChange: ({ route, previousRoute, source }) => {
      documentRef.body.dataset.ui008Route = route;
      setSurfaceRoute(documentRef, route);
      setCurrentNavigation(documentRef, route);
      if (routeTitle) routeTitle.textContent = ROUTE_LABELS[route];
      if (!initial && route !== previousRoute && source !== "initial") {
        main?.focus({ preventScroll: true });
      }
      initial = false;
      windowRef.dispatchEvent(new CustomEvent("piano-ui-route-change", {
        detail: Object.freeze({ route, previousRoute, source }),
      }));
    },
  });

  for (const link of documentRef.querySelectorAll("[data-ui008-route]")) {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      const route = normalizeRoute(link.dataset.ui008Route);
      if (!UI008_ROUTES.includes(route)) return;
      if (moreLayer.isOpen()) moreLayer.close(() => router.navigate(route));
      else router.navigate(route);
    });
  }

  for (const opener of documentRef.querySelectorAll("[data-ui008-open-more]")) {
    opener.addEventListener("click", () => moreLayer.open(opener));
  }

  const practice = documentRef.getElementById("practice");
  const learnEmpty = documentRef.getElementById("ui008-learn-empty");
  if (practice && learnEmpty && typeof MutationObserver === "function") {
    const syncLearnEmpty = () => {
      learnEmpty.hidden = !practice.hidden;
    };
    new MutationObserver(syncLearnEmpty).observe(practice, {
      attributes: true,
      attributeFilter: ["hidden"],
    });
    syncLearnEmpty();
  }

  documentRef.documentElement.dataset.ui008ShellReady = "true";
  router.start();
  return Object.freeze({ router, moreLayer });
}
