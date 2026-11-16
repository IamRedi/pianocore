/* Native installation only. No service worker, offline API cache or musical commands. */
export function bindInstall({windowRef = globalThis, button, status} = {}) {
  let pending = null;
  let busy = false;
  const standalone = windowRef.matchMedia?.("(display-mode: standalone)");
  const fullscreen = windowRef.matchMedia?.("(display-mode: fullscreen)");
  const installed = () => Boolean(standalone?.matches || fullscreen?.matches || windowRef.navigator?.standalone);
  function refresh() {
    if (button) { button.hidden = !pending || installed() || !windowRef.isSecureContext; button.disabled = busy; }
  }
  function offer(event) {
    if (!windowRef.isSecureContext || installed()) return;
    event.preventDefault();
    pending = event;
    refresh();
  }
  function complete() { pending = null; refresh(); }
  async function prompt() {
    if (!pending || busy || installed()) return;
    const event = pending;
    pending = null;
    busy = true;
    refresh();
    try {
      await event.prompt();
      await event.userChoice;
    } catch {
      if (status) status.textContent = "Installation did not open. Use your browser’s Install app or Add to Home Screen menu.";
    } finally { busy = false; refresh(); }
  }
  windowRef.addEventListener?.("beforeinstallprompt", offer);
  windowRef.addEventListener?.("appinstalled", complete);
  standalone?.addEventListener?.("change", refresh);
  fullscreen?.addEventListener?.("change", refresh);
  button?.addEventListener("click", prompt);
  refresh();
  return () => {
    windowRef.removeEventListener?.("beforeinstallprompt", offer);
    windowRef.removeEventListener?.("appinstalled", complete);
    standalone?.removeEventListener?.("change", refresh);
    fullscreen?.removeEventListener?.("change", refresh);
    button?.removeEventListener("click", prompt);
  };
}
if (globalThis.document) bindInstall({button: document.querySelector("[data-install-app]"), status: document.querySelector("[data-install-status]")});
