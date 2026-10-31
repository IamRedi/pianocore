/* Piano Core colour theme: a browser-local presentation preference.
 *
 * "dark" and "light" are the two themes; "auto" follows the device. The
 * choice lives in this browser only (same family as the visual-mode and
 * hands preferences) and never touches musical truth. Song screens
 * (falling notes, keyboard, 3D hands) stay dark in both themes.
 */

export const THEME_STORAGE_KEY = "piano-core.theme";
export const THEME_PREFERENCES = Object.freeze(["dark", "light", "auto"]);
export const THEME_OPTIONS = Object.freeze([
  Object.freeze({ value: "dark", label: "Dark" }),
  Object.freeze({ value: "light", label: "Light" }),
  Object.freeze({ value: "auto", label: "Auto" }),
]);
export const THEME_COLORS = Object.freeze({ dark: "#12141c", light: "#f3f4f8" });

/** Load the stored preference (storage may be absent or throw). */
export function loadThemePreference(storage) {
  try {
    const value = storage?.getItem?.(THEME_STORAGE_KEY);
    return THEME_PREFERENCES.includes(value) ? value : "dark";
  } catch {
    return "dark";
  }
}

/** Persist a preference; unknown values are ignored. */
export function saveThemePreference(storage, preference) {
  if (!THEME_PREFERENCES.includes(preference)) return false;
  try {
    storage?.setItem?.(THEME_STORAGE_KEY, preference);
    return true;
  } catch {
    return false;
  }
}

/** The concrete theme for a preference ("auto" asks the device). */
export function resolveTheme(preference, prefersLight = false) {
  if (preference === "light" || preference === "dark") return preference;
  return prefersLight ? "light" : "dark";
}

/** Apply a resolved theme to the document root and theme-color meta. */
export function applyTheme(documentRef, theme) {
  const root = documentRef?.documentElement;
  if (!root) return;
  const resolved = theme === "light" ? "light" : "dark";
  root.dataset.theme = resolved;
  const meta = documentRef.querySelector?.('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", THEME_COLORS[resolved]);
}
