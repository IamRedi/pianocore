/* This presentation never fetches or probes a device. Navigation is explicit. */
const root = document.documentElement;
const media = matchMedia("(prefers-color-scheme: dark)");
let preference = "auto";
try { const saved = localStorage.getItem("piano-core.theme"); if (["auto", "light", "dark"].includes(saved)) preference = saved; } catch { /* private browsing */ }
function apply() {
  root.dataset.theme = preference === "auto" ? (media.matches ? "dark" : "light") : preference;
  document.querySelector('meta[name="theme-color"]').content = root.dataset.theme === "light" ? "#efe9e0" : "#141018";
  for (const button of document.querySelectorAll("[data-theme]")) button.setAttribute("aria-pressed", String(button.dataset.theme === preference));
}
for (const button of document.querySelectorAll("[data-theme]")) button.addEventListener("click", () => {
  preference = button.dataset.theme;
  try { localStorage.setItem("piano-core.theme", preference); } catch { /* appearance still works */ }
  apply();
});
media.addEventListener("change", () => { if (preference === "auto") apply(); });
apply();
const form = document.querySelector("#connect-form");
const input = document.querySelector("#piano-address");
const feedback = document.querySelector("#address-feedback");
const confirmation = document.querySelector(".confirm-entry");
input.addEventListener("input", () => { confirmation.hidden = true; confirmation.removeAttribute("href"); feedback.textContent = ""; });
if (location.pathname.startsWith("/ui/about/")) {
  form.hidden = true;
  document.querySelector(".native-entry").hidden = false;
}
form.addEventListener("submit", event => {
  event.preventDefault();
  confirmation.hidden = true;
  try {
    const target = new URL(input.value.trim());
    if (!["http:", "https:"].includes(target.protocol) || target.username || target.password) throw new Error("invalid address");
    const destination = new URL("/ui/#/home", target.origin).href;
    if (target.protocol === "http:") {
      feedback.textContent = form.dataset.http;
      confirmation.href = destination;
      confirmation.hidden = false;
      confirmation.focus();
    } else location.assign(destination);
  } catch { feedback.textContent = form.dataset.invalid; input.focus(); }
});
