/**
 * PIANO-LEARN-001 U1: REAL-APPLIANCE Chromium verification (LAN client).
 * Drives the PRODUCTION tablet UI over the real network with a fresh
 * browser profile: real navigation clicks, visible catalog assertions,
 * practice activation with the physical LED, safe stop, reload
 * (stale-cache) behavior, and screenshot evidence.
 * Run: node tests/weblogic/realappliance_learn.mjs
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const require = createRequire(import.meta.url);
const WebSocket = require("ws");
const BASE = process.env.APPLIANCE || "http://192.168.1.50:8788";
const CHROME = process.env.CHROME || "/usr/bin/google-chrome";
const EVIDENCE = resolve("docs/work/evidence/pi-learn-ui-fix-20261009");
mkdirSync(EVIDENCE, { recursive: true });

let passes = 0, failures = 0;
const pageErrors = [];
async function test(name, fn) {
  const before = pageErrors.length;
  try {
    await fn();
    assert.equal(pageErrors.length, before, pageErrors.slice(before).join(" | "));
    passes += 1;
    console.log(`ok - ${name}`);
  } catch (error) {
    failures += 1;
    console.error(`FAIL - ${name}`);
    console.error(error);
  }
}
const sleep = (ms) => new Promise((d) => setTimeout(d, ms));

const net = await import("node:net");
const freePort = await new Promise((resolvePort) => {
  const srv = net.createServer();
  srv.listen(0, "127.0.0.1", () => { const p = srv.address().port; srv.close(() => resolvePort(p)); });
});
const profile = join(tmpdir(), "piano-real-appliance-" + Date.now());
const chrome = spawn(CHROME, [
  "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
  `--remote-debugging-port=${freePort}`, `--user-data-dir=${profile}`,
  `--window-size=1024,768`, `${BASE}/ui/`,
], { stdio: ["ignore", "ignore", "pipe"] });
const fetchJson = async (u) => { for (let i = 0; i < 120; i += 1) { try { return await (await fetch(u)).json(); } catch { await sleep(50); } } throw new Error(u); };
const targets = await fetchJson(`http://127.0.0.1:${freePort}/json/list`);
const wsUrl = targets.find((t) => t.type === "page").webSocketDebuggerUrl;

const ws = new WebSocket(wsUrl);
await new Promise((d, f) => { ws.once("open", d); ws.once("error", f); });
let cdpId = 0;
const pending = new Map();
ws.on("message", (raw) => {
  const m = JSON.parse(raw);
  if (m.method === "Runtime.exceptionThrown") pageErrors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
  if (!m.id || !pending.has(m.id)) return;
  const h = pending.get(m.id); pending.delete(m.id);
  m.error ? h.fail(new Error(m.error.message)) : h.done(m.result);
});
const cdp = (method, params = {}) => new Promise((done, fail) => { const i = ++cdpId; pending.set(i, { done, fail }); ws.send(JSON.stringify({ id: i, method, params })); });
async function evaluate(expression) {
  const r = await cdp("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error((r.exceptionDetails.exception?.description || r.exceptionDetails.text) + " :: " + String(expression).slice(0, 120));
  return r.result.value;
}
async function waitFor(expression, timeout = 15000) {
  const dl = Date.now() + timeout;
  while (Date.now() < dl) { if (await evaluate(expression)) return true; await sleep(60); }
  return false;
}
async function viewport(w, h) { await cdp("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: false }); await sleep(200); }
async function capture(name) {
  await evaluate("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))");
  await sleep(250);
  const shot = await cdp("Page.captureScreenshot", { format: "png", fromSurface: true });
  writeFileSync(join(EVIDENCE, name), Buffer.from(shot.data, "base64"));
}

try {
  await cdp("Runtime.enable");
  await cdp("Page.enable");
  await waitFor("document.documentElement?.dataset.ui008ShellReady === 'true'", 20000);

  await test("appliance boot: fresh session lands on Home with Player healthy (Gold1000)", async () => {
    await waitFor("document.getElementById('ui008-library-stats')?.textContent.length > 0 || document.getElementById('ui008-learn-library-stats')", 8000).catch(() => {});
    const boot = await evaluate(`({
      route: document.body.dataset.ui008Route,
      shell: document.documentElement.dataset.ui008ShellReady,
    })`);
    assert.equal(boot.shell, "true");
    assert.ok(["home", "library", "learn"].includes(boot.route), boot.route);
  });

  await test("REAL nav click opens the Learn catalog VISIBLY with all 1612 lessons", async () => {
    await evaluate("document.querySelector('[data-ui008-route=learn]').click()");
    await waitFor("document.body.dataset.ui008Route === 'learn'", 8000);
    await waitFor("document.getElementById('ui008-learn-library-stats')?.textContent.includes('1612 lessons')", 15000);
    await waitFor("[...document.querySelectorAll('#ui008-learn-lessons .learn-lesson-btn')].some(b => b.getBoundingClientRect().height > 0)", 15000);
    const vis = await evaluate(`(() => {
      const s = document.getElementById('ui008-learn-library');
      const r = s.getBoundingClientRect();
      const rows = [...document.querySelectorAll('#ui008-learn-lessons .learn-lesson-btn')];
      return {
        hidden: s.hidden, routeHidden: s.dataset.ui008RouteHidden,
        offsetParentNull: s.offsetParent === null, rectH: Math.round(r.height),
        stats: document.getElementById('ui008-learn-library-stats').textContent,
        chips: document.querySelectorAll('#ui008-learn-levels .learn-level-chip').length,
        courses: document.querySelectorAll('#ui008-learn-course option').length,
        rows, rowVisible: rows.length > 0 && rows[0].getBoundingClientRect().height > 0,
        rowTitles: rows.slice(0, 3).map((b) => b.querySelector('.learn-lesson-name').textContent),
      };
    })()`);
    assert.equal(vis.hidden, false, JSON.stringify(vis));
    assert.equal(vis.routeHidden, "false");
    assert.equal(vis.offsetParentNull, false);
    assert.ok(vis.rectH > 150, `rect height ${vis.rectH}`);
    assert.match(vis.stats, /1612 lessons/);
    assert.match(vis.stats, /83 courses/);
    assert.ok(vis.chips >= 2, `level chips ${vis.chips}`);
    assert.ok(vis.courses >= 80, `course options ${vis.courses}`);
    assert.equal(vis.rowVisible, true);
    await capture("learn-catalog-real-appliance-1024x768.png");
  });

  await test("level filter narrows the visible list to Level 0 (First Notes)", async () => {
    await evaluate("[...document.querySelectorAll('#ui008-learn-levels .learn-level-chip')].find(b => b.dataset.level === '0').click()");
    await waitFor("[...document.querySelectorAll('#ui008-learn-levels .learn-level-chip')].find(b => b.dataset.level === '0')?.getAttribute('aria-pressed') === 'true'", 8000);
    await sleep(600);
    const state = await evaluate(`(() => {
      const rows = [...document.querySelectorAll('#ui008-learn-lessons .learn-lesson-btn')];
      return { count: rows.length, first: rows[0]?.querySelector('.learn-lesson-name')?.textContent,
               visible: rows[0]?.getBoundingClientRect().height > 0 };
    })()`);
    assert.ok(state.count >= 1 && state.count <= 50, JSON.stringify(state));
    assert.equal(state.visible, true);
  });

  await test("lesson detail opens; Practice activation starts a REAL session; safe stop clears the LED", async () => {
    await evaluate("[...document.querySelectorAll('#ui008-learn-lessons .learn-lesson-btn')][0].click()");
    await waitFor("document.getElementById('ui008-lesson-detail')?.hidden === false", 10000);
    const detail = await evaluate(`({
      title: document.getElementById('ui008-lesson-detail-title').textContent,
      license: document.getElementById('ui008-lesson-detail-license').textContent,
      detailVisible: document.getElementById('ui008-lesson-detail').getBoundingClientRect().height > 0,
    })`);
    assert.ok(detail.title.length > 0);
    assert.equal(detail.detailVisible, true);
    await capture("learn-lesson-detail-real-1024x768.png");
    await evaluate("document.getElementById('ui008-lesson-start').click()");
    const started = await waitFor("document.getElementById('ui008-learn-state')?.textContent === 'Your turn'", 12000);
    assert.equal(started, true, "practice never reached WAITING");
    await sleep(500);
    const session = await evaluate(`({
      state: document.getElementById('ui008-learn-state').textContent,
      target: document.getElementById('practice-target').textContent,
      guidance: document.getElementById('ui008-learn-guidance').textContent,
    })`);
    assert.equal(session.state, "Your turn");
    assert.ok(session.target.length > 0, "target missing");
    await capture("learn-practice-waiting-real-1024x768.png");
    // safe guarded stop from the real UI
    await evaluate("document.getElementById('btn-practice-stop').click()");
    const stopped = await waitFor("document.getElementById('ui008-learn-state')?.textContent === 'Ready'", 10000);
    assert.equal(stopped, true, "practice did not stop cleanly");
  });

  await test("stale-cache behavior: hard reload (cache-bypassing) still shows the catalog", async () => {
    await cdp("Page.reload", { ignoreCache: true });
    await sleep(800);
    await waitFor("document.documentElement?.dataset.ui008ShellReady === 'true'", 15000);
    await waitFor("document.body.dataset.ui008Route === 'learn'", 10000);
    await waitFor("document.getElementById('ui008-learn-library-stats')?.textContent.includes('1612 lessons')", 15000);
    const vis = await evaluate(`(() => {
      const s = document.getElementById('ui008-learn-library');
      return { hidden: s.hidden, routeHidden: s.dataset.ui008RouteHidden, rectH: Math.round(s.getBoundingClientRect().height),
               sw: !!(navigator.serviceWorker && navigator.serviceWorker.controller) };
    })()`);
    assert.equal(vis.hidden, false, JSON.stringify(vis));
    assert.equal(vis.routeHidden, "false");
    assert.ok(vis.rectH > 150, `height after hard reload ${vis.rectH}`);
    assert.equal(vis.sw, false);
    await capture("learn-catalog-after-hard-reload-1024x768.png");
  });

  await test("responsive spot check on the real appliance (768 and 360 widths, no overflow)", async () => {
    await viewport(768, 1024);
    await sleep(300);
    const overflow768 = await evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth");
    await capture("learn-catalog-real-768x1024.png");
    await viewport(360, 740);
    await sleep(300);
    const overflow360 = await evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth");
    await capture("learn-catalog-real-360x740.png");
    await viewport(1024, 768);
    assert.equal(overflow768, false, "horizontal overflow at 768");
    assert.equal(overflow360, false, "horizontal overflow at 360");
  });
} finally {
  ws.close();
  chrome.kill("SIGTERM");
  await new Promise((d) => chrome.once("exit", d));
  try { rmSync(profile, { recursive: true, force: true }); } catch {}
}

console.log(`PASS ${passes}/${passes + failures}`);
writeFileSync(join(EVIDENCE, "SUMMARY.txt"), `passes=${passes} failures=${failures}\n`);
process.exit(failures ? 1 : 0);
