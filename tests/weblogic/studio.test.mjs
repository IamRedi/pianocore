import assert from 'node:assert/strict';
import { createStudioController, focusRange, flowVisualPosition } from '../../src/piano_core/web/static/ui008/studio.js';
import { PIANO_KEYS, PIANO_WHITE_KEY_COUNT } from '../../src/piano_core/web/static/app.js';
let passed = 0;
async function test(name, fn) { await fn(); passed++; console.log(`ok - ${name}`); }
function harness({ rejectFullscreen = false, stop = true } = {}) {
  const elements = new Map();
  const events = new Map();
  const calls = [];
  const get = id => {
    if (!elements.has(id)) elements.set(id, { hidden: false, dataset: {}, attributes: {}, value: '', listeners: new Map(), addEventListener(t, f) { this.listeners.set(t, f); }, setAttribute(k, v) { this.attributes[k] = v; } });
    return elements.get(id);
  };
  const doc = { body: { dataset: {} }, getElementById: get, fullscreenElement: null, documentElement: {}, addEventListener: (t, f) => events.set(t, f), exitFullscreen: async () => { doc.fullscreenElement = null; events.get('fullscreenchange')?.(); } };
  doc.documentElement.requestFullscreen = async () => { calls.push('fullscreen'); if (rejectFullscreen) throw Error('denied'); doc.fullscreenElement = doc.documentElement; events.get('fullscreenchange')?.(); };
  const controller = createStudioController({ documentRef: doc, onStop: async () => { calls.push('stop'); return stop; }, onRestart: async () => calls.push('restart'), onPause: p => calls.push(['pause', p]), onNext: id => calls.push(['next', id]), onVisual: v => calls.push(['visual', v]), onRange: v => calls.push(['range', v]), onExit: () => calls.push('exit') });
  return { controller, doc, calls, get, events, click: id => get(id).listeners.get('click')?.(), change: (id, v) => { get(id).value = v; return get(id).listeners.get('change')?.(); } };
}
await test('native entry and Escape preserve musical state', async () => {
  const h = harness(); h.controller.enter({ nextLessonId: 'next' }); await Promise.resolve();
  assert.equal(h.doc.body.dataset.studioFullscreen, 'native');
  await h.doc.exitFullscreen();
  assert.equal(h.doc.body.dataset.studioFullscreen, 'viewport');
  assert.equal(h.controller.isOpen(), true);
  assert.deepEqual(h.calls, ['fullscreen']);
});
await test('denied native entry retains usable viewport fallback', async () => {
  const h = harness({ rejectFullscreen: true }); h.controller.enter({}); await Promise.resolve(); await Promise.resolve();
  assert.equal(h.controller.isOpen(), true); assert.equal(h.doc.body.dataset.studioFullscreen, 'viewport');
});
await test('reload adoption issues no native or musical command', async () => {
  const h = harness(); h.controller.enter({}, false); assert.equal(h.controller.isOpen(), true); assert.deepEqual(h.calls, []);
});
await test('safe exit stops active practice exactly once', async () => {
  const h = harness(); h.controller.enter({}, false); h.controller.update({ state: 'WAITING' }, false); await h.click('studio-exit');
  assert.deepEqual(h.calls, ['stop', 'exit']); assert.equal(h.controller.isOpen(), false);
});
await test('failed Stop keeps immersive practice recoverable', async () => {
  const h = harness({ stop: false }); h.controller.enter({}, false); h.controller.update({ state: 'WAITING' }, false); await h.click('studio-exit');
  assert.deepEqual(h.calls, ['stop']); assert.equal(h.controller.isOpen(), true);
});
await test('view and range changes issue only presentation callbacks', async () => {
  const h = harness(); h.controller.enter({}, false); h.change('studio-visual', 'hybrid'); h.change('studio-range', 'full');
  assert.deepEqual(h.calls, [['visual', 'hybrid'], ['range', 'full']]); assert.equal(h.get('studio-notation-note').hidden, false);
});
await test('Flow summaries use actual counts and measured errors', async () => {
  const h = harness(); h.controller.update({ mode: 'FLOW', state: 'COMPLETED', completed_steps: 2, total_steps: 4, flow: { speed: 0.5, correct_events: 2, expected_events: 4, missed_events: 2, wrong_presses: 1, measured_attacks: 2, on_time_attacks: 1, early_attacks: 1, late_attacks: 0, mean_absolute_error_ms: 70 } }, false);
  assert.match(h.get('studio-results-detail').textContent, /2 \/ 4 events correct/);
  assert.match(h.get('studio-results-detail').textContent, /70 ms/); assert.equal(h.get('studio-results').hidden, false);
});
await test('Wait summaries never claim timing measurement', async () => {
  const h = harness(); h.controller.update({ state: 'COMPLETED', completed_steps: 4, total_steps: 4, incorrect_attempts: 1 }, false);
  assert.match(h.get('studio-results-detail').textContent, /At your own pace/); assert.doesNotMatch(h.get('studio-results-detail').textContent, /timing|accuracy|ms/);
});
await test('Flow interpolation freezes at 350ms and respects pause', async () => {
  const s = { state: 'WAITING', flow: { position_seconds: 2, speed: 0.5, paused: false } };
  assert.deepEqual(flowVisualPosition(s, 1000, 1200), { position: 2.1, stale: false });
  assert.deepEqual(flowVisualPosition(s, 1000, 2000), { position: 2.175, stale: true });
  s.flow.paused = true; assert.deepEqual(flowVisualPosition(s, 1000, 2000), { position: 2, stale: false });
});
await test('Focus frames a passage with accepted keyboard geometry', async () => {
  const range = focusRange([{ midi: 60 }, { midi: 64 }], PIANO_KEYS, PIANO_WHITE_KEY_COUNT);
  assert.ok(range.right - range.left < 0.5); assert.ok(range.right - range.left >= 14 / PIANO_WHITE_KEY_COUNT - 1e-10);
  for (const midi of [60, 64]) { const key = PIANO_KEYS.find(k => k.midi === midi); assert.ok(key.whiteIndex / PIANO_WHITE_KEY_COUNT >= range.left); assert.ok((key.whiteIndex + 1) / PIANO_WHITE_KEY_COUNT <= range.right); }
  assert.deepEqual(focusRange([{ midi: 21 }, { midi: 108 }], PIANO_KEYS, PIANO_WHITE_KEY_COUNT), { left: 0, right: 1 });
  assert.equal(focusRange([], PIANO_KEYS, PIANO_WHITE_KEY_COUNT), null);
});
await test('stale Exit still requires the command boundary to confirm Stop', async () => {
  const h = harness({ stop: false }); h.controller.enter({}, false); h.controller.update({ state: 'WAITING', session_id: 'active' }, false); h.controller.update(null, true);
  await h.click('studio-exit'); assert.deepEqual(h.calls, ['stop']); assert.equal(h.controller.isOpen(), true);
});
console.log(`PASS ${passed}/${passed}`);
