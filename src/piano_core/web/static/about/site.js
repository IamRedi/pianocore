import { mountCrystalIllustrations } from "./stage.js";
const root = document.documentElement;
const systemDark = matchMedia("(prefers-color-scheme: dark)");
let preference = "auto";
try { const saved = localStorage.getItem("piano-core.theme"); if (["auto", "light", "dark"].includes(saved)) preference = saved; } catch { /* browser-local preference is optional */ }
function apply() {
  const resolved = preference === "auto" ? (systemDark.matches ? "dark" : "light") : preference;
  root.dataset.theme = resolved;
  document.querySelector('meta[name="theme-color"]').content = resolved === "light" ? "#f2eadb" : "#244b49";
  for (const button of document.querySelectorAll("button[data-theme]")) {
    button.setAttribute("aria-pressed", String(button.dataset.theme === preference));
    if (button.dataset.theme === preference) document.querySelector("[data-theme-icon]").replaceChildren(button.querySelector("svg").cloneNode(true));
  }
  for (const button of document.querySelectorAll("[data-scene-theme]")) button.setAttribute("aria-pressed", String(button.dataset.sceneTheme === resolved));
}
function choose(value) {
  preference = value;
  try { localStorage.setItem("piano-core.theme", value); } catch { /* appearance still works */ }
  apply();
}
for (const button of document.querySelectorAll("button[data-theme]")) button.addEventListener("click", () => {
  choose(button.dataset.theme);
  const details = button.closest("details"); details.open = false; details.querySelector("summary").focus();
});
for (const button of document.querySelectorAll("[data-scene-theme]")) button.addEventListener("click", () => choose(button.dataset.sceneTheme));
systemDark.addEventListener("change", () => { if (preference === "auto") apply(); });
document.addEventListener("click", event => { for (const details of document.querySelectorAll(".preferences details[open]")) if (!details.contains(event.target)) details.open = false; });
document.addEventListener("keydown", event => { if (event.key === "Escape") for (const details of document.querySelectorAll(".preferences details[open]")) { details.open = false; details.querySelector("summary").focus(); } });
apply();
mountCrystalIllustrations();

if(location.pathname.startsWith("/ui/about/")){document.querySelector(".native-back").hidden=false;document.documentElement.classList.add("native-view");}
