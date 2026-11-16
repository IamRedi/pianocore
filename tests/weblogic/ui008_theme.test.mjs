import { test } from "node:test";
import assert from "node:assert/strict";
import {
  THEME_OPTIONS, THEME_PREFERENCES, THEME_STORAGE_KEY,
  loadThemePreference, saveThemePreference, resolveTheme, applyTheme, isDaytime,
} from "../../src/piano_core/web/static/ui008/theme.js";

function memoryStorage(initial = {}) {
  const data = { ...initial };
  return { getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); }, data };
}

test("theme preference defaults to auto and only accepts known values", () => {
  assert.equal(loadThemePreference(undefined), "auto");
  assert.equal(loadThemePreference(memoryStorage()), "auto");
  assert.equal(loadThemePreference(memoryStorage({ [THEME_STORAGE_KEY]: "neon" })), "auto");
  assert.equal(loadThemePreference(memoryStorage({ [THEME_STORAGE_KEY]: "light" })), "light");
  assert.equal(loadThemePreference({ getItem() { throw new Error("blocked"); } }), "auto");
  const s = memoryStorage();
  assert.equal(saveThemePreference(s, "auto"), true);
  assert.equal(s.data[THEME_STORAGE_KEY], "auto");
  assert.equal(saveThemePreference(s, "bogus"), false);
  assert.equal(s.data[THEME_STORAGE_KEY], "auto");
  assert.deepEqual(THEME_OPTIONS.map((o) => o.value), [...THEME_PREFERENCES]);
});

test("auto follows the device appearance; explicit choices win", () => {
  assert.equal(resolveTheme("auto", true), "light");
  assert.equal(resolveTheme("auto", false), "dark");
  assert.equal(resolveTheme("dark", true), "dark");
  assert.equal(resolveTheme("light", false), "light");
});

test("applyTheme sets the root attribute and theme colour", () => {
  let color = null;
  const doc = {
    documentElement: { dataset: {} },
    querySelector: () => ({ setAttribute: (_n, v) => { color = v; } }),
  };
  applyTheme(doc, "light");
  assert.equal(doc.documentElement.dataset.theme, "light");
  assert.equal(color, "#efe9e0");
  applyTheme(doc, "weird");
  assert.equal(doc.documentElement.dataset.theme, "dark");
});

test("isDaytime: 07:00-18:59 is day, the rest is night", () => {
  const at = (h, m = 0) => new Date(2026, 10, 16, h, m);
  assert.equal(isDaytime(at(6, 59)), false);
  assert.equal(isDaytime(at(7, 0)), true);
  assert.equal(isDaytime(at(12)), true);
  assert.equal(isDaytime(at(18, 59)), true);
  assert.equal(isDaytime(at(19, 0)), false);
  assert.equal(isDaytime(at(0)), false);
  assert.equal(isDaytime({}), true);
});
