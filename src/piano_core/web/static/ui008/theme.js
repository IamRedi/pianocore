/* Piano Core colour theme: a browser-local presentation preference.
 *
 * Two looks of the same glass studio: "light" is Day (the studio photo in
 * daylight) and "dark" is Night (the same photo under warm artificial
 * light). "auto" — the default — follows the device appearance. The choice lives in this browser only (same
 * family as the visual-mode and hands preferences) and never touches
 * musical truth. Song screens (falling notes, 3D hands) stay dark glass.
 */

export const THEME_STORAGE_KEY = "piano-core.theme";
export const THEME_PREFERENCES = Object.freeze(["dark", "light", "auto"]);
export const THEME_OPTIONS = Object.freeze([
  Object.freeze({ value: "dark", label: "Night" }),
  Object.freeze({ value: "light", label: "Day" }),
  Object.freeze({ value: "auto", label: "Auto" }),
]);
export const THEME_COLORS = Object.freeze({ dark: "#141018", light: "#efe9e0" });
/** Day runs from 07:00 to 18:59 local time; the rest is Night. */
export const DAY_START_HOUR = 7;
export const NIGHT_START_HOUR = 19;

/** Load the stored preference (storage may be absent or throw). */
export function loadThemePreference(storage) {
  try {
    const value = storage?.getItem?.(THEME_STORAGE_KEY);
    return THEME_PREFERENCES.includes(value) ? value : "auto";
  } catch {
    return "auto";
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

/** True while the local clock is in daytime (for "auto"). */
export function isDaytime(date = new Date()) {
  const hour = date?.getHours?.();
  if (!Number.isInteger(hour)) return true;
  return hour >= DAY_START_HOUR && hour < NIGHT_START_HOUR;
}

/** The concrete theme for a preference ("auto": the device appearance). */
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
