/* PIANO-UI-008 U8-P1: one bounded transient-layer controller.
 * Back and Escape close the active layer before presentation routing changes.
 */

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

export function createLayerController({
  windowRef = globalThis.window,
  documentRef = globalThis.document,
  root,
  closeButton,
  layerId,
} = {}) {
  if (!root || !layerId) throw new TypeError("root and layerId are required");

  let open = false;
  let returnFocus = null;
  let pendingAction = null;

  function focusableItems() {
    return [...root.querySelectorAll(FOCUSABLE)].filter(
      (item) => !item.hidden && item.getAttribute("aria-hidden") !== "true"
    );
  }

  function closeInternal() {
    if (!open) return;
    open = false;
    root.hidden = true;
    documentRef.body.classList.remove("ui008-layer-open");
    const target = returnFocus;
    returnFocus = null;
    if (target && typeof target.focus === "function") target.focus();
    const action = pendingAction;
    pendingAction = null;
    if (action) action();
  }

  function requestClose(afterClose = null) {
    if (!open) {
      if (afterClose) afterClose();
      return;
    }
    pendingAction = afterClose;
    if (windowRef.history.state?.ui008Layer === layerId) {
      windowRef.history.back();
    } else {
      closeInternal();
    }
  }

  function onPopState(event) {
    if (open && event.state?.ui008Layer !== layerId) closeInternal();
  }

  function onKeyDown(event) {
    if (!open) return;
    if (event.key === "Escape") {
      event.preventDefault();
      requestClose();
      return;
    }
    if (event.key !== "Tab") return;
    const items = focusableItems();
    if (items.length === 0) {
      event.preventDefault();
      root.focus();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && documentRef.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && documentRef.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  windowRef.addEventListener("popstate", onPopState);
  documentRef.addEventListener("keydown", onKeyDown);
  closeButton?.addEventListener("click", () => requestClose());

  return Object.freeze({
    open(opener = documentRef.activeElement) {
      if (open) return;
      open = true;
      returnFocus = opener;
      root.hidden = false;
      documentRef.body.classList.add("ui008-layer-open");
      windowRef.history.pushState(
        { ...(windowRef.history.state || {}), ui008Layer: layerId },
        "",
        windowRef.location.href
      );
      (closeButton || focusableItems()[0] || root).focus();
    },
    close: requestClose,
    isOpen() {
      return open;
    },
    destroy() {
      windowRef.removeEventListener("popstate", onPopState);
      documentRef.removeEventListener("keydown", onKeyDown);
      closeInternal();
    },
  });
}
