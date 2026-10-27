// PIANO-UI-003: deterministic canonical model and replica tests.
import assert from "node:assert/strict";
import {
  PIANO_KEYS,
  PIANO_KEY_COUNT,
  PIANO_NOTE_MIN,
  PIANO_NOTE_MAX,
  PIANO_WHITE_KEY_COUNT,
  activeKeySources,
  isValidLiveNoteSnapshot,
  LiveNoteCoordinator,
  LiveNoteReplica,
} from "../../src/piano_core/web/static/app.js";

let failures = 0;
function test(name, fn) {
  try {
    fn();
    console.log(`ok - ${name}`);
  } catch (error) {
    failures += 1;
    console.error(`FAIL - ${name}`);
    console.error(error);
  }
}

async function asyncTest(name, fn) {
  try {
    await fn();
    console.log(`ok - ${name}`);
  } catch (error) {
    failures += 1;
    console.error(`FAIL - ${name}`);
    console.error(error);
  }
}

class FakeEventSource {
  constructor() {
    this.listeners = new Map();
    this.onerror = null;
    this.closed = false;
  }

  addEventListener(name, callback) { this.listeners.set(name, callback); }
  close() { this.closed = true; }
  emit(value) {
    this.listeners.get("snapshot")({ data: JSON.stringify(value) });
  }
  fail() { this.onerror?.(); }
}

class ManualTimers {
  constructor() {
    this.now = 0;
    this.nextId = 0;
    this.tasks = new Map();
  }

  set(callback, delay) {
    const id = ++this.nextId;
    this.tasks.set(id, { at: this.now + delay, callback });
    return id;
  }

  clear(id) { this.tasks.delete(id); }

  advance(milliseconds) {
    const target = this.now + milliseconds;
    while (true) {
      const due = [...this.tasks.entries()]
        .filter(([, task]) => task.at <= target)
        .sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      const [id, task] = due;
      this.tasks.delete(id);
      this.now = task.at;
      task.callback();
    }
    this.now = target;
  }
}

const settlePromises = async () => {
  for (let index = 0; index < 4; index += 1) await Promise.resolve();
};

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function snapshot(sequence, physical = [], playback = [], epoch = "runtime-a") {
  return {
    schema_version: 1,
    runtime_epoch: epoch,
    sequence,
    physical: { notes: physical, midi_connection: "CONNECTED", sustain: { pressed: false, value: 0 }, revision: sequence },
    playback: { available: true, notes: playback, voices: [], sustain_channels: [] },
  };
}

test("canonical model is exactly MIDI 21 through 108", () => {
  assert.equal(PIANO_KEY_COUNT, 88);
  assert.equal(PIANO_NOTE_MIN, 21);
  assert.equal(PIANO_NOTE_MAX, 108);
  assert.equal(PIANO_KEYS.length, 88);
  assert.equal(PIANO_KEYS[0].midi, 21);
  assert.equal(PIANO_KEYS[0].label, "A0");
  assert.equal(PIANO_KEYS.at(-1).midi, 108);
  assert.equal(PIANO_KEYS.at(-1).label, "C8");
  assert.equal(PIANO_KEYS.filter((key) => !key.black).length, PIANO_WHITE_KEY_COUNT);
});

test("black and white classification and octave labels are correct", () => {
  const byMidi = new Map(PIANO_KEYS.map((key) => [key.midi, key]));
  assert.equal(byMidi.get(21).black, false); // A0
  assert.equal(byMidi.get(22).black, true);  // A#0
  assert.equal(byMidi.get(23).black, false); // B0
  assert.equal(byMidi.get(60).label, "C4");
  assert.equal(byMidi.get(61).pitchClass, "C♯");
  assert.equal(byMidi.get(61).black, true);
});

test("physical, playback, overlap and chord sources remain explicit", () => {
  const sources = activeKeySources(snapshot(1, [60, 64, 67], [60, 62, 67]));
  assert.equal(sources.get(64), "physical");
  assert.equal(sources.get(62), "playback");
  assert.equal(sources.get(60), "both");
  assert.equal(sources.get(67), "both");
  assert.equal(sources.size, 4);
});

test("out-of-range and duplicate notes cannot create non-piano keys", () => {
  const sources = activeKeySources(snapshot(1, [0, 21, 21, 109], [108, 127]));
  assert.deepEqual([...sources.entries()], [[21, "physical"], [108, "playback"]]);
});

test("replica rejects malformed and stale same-runtime snapshots", () => {
  const replica = new LiveNoteReplica();
  const generation = replica.beginGeneration();
  assert.equal(isValidLiveNoteSnapshot(snapshot(1)), true);
  assert.equal(replica.accept(snapshot(2, [60]), generation), true);
  assert.equal(replica.accept(snapshot(1, [61]), generation), false);
  assert.deepEqual(replica.snapshot.physical.notes, [60]);
  assert.equal(replica.accept({ bad: true }, generation), false);
});

test("new runtime epoch replaces state and old connection generation loses", () => {
  const replica = new LiveNoteReplica();
  const oldGeneration = replica.beginGeneration();
  replica.accept(snapshot(8, [60], [], "runtime-a"), oldGeneration);
  const currentGeneration = replica.beginGeneration();
  assert.equal(replica.accept(snapshot(9, [61], [], "runtime-a"), oldGeneration), false);
  assert.equal(replica.accept(snapshot(0, [], [72], "runtime-b"), currentGeneration), true);
  assert.equal(replica.snapshot.runtime_epoch, "runtime-b");
  assert.equal(replica.snapshot.sequence, 0);
});

test("stale transition is generation guarded and leaves snapshot recoverable", () => {
  const replica = new LiveNoteReplica();
  const generation = replica.beginGeneration();
  replica.accept(snapshot(1, [60]), generation);
  assert.equal(replica.markStale(generation - 1), false);
  assert.equal(replica.status, "live");
  assert.equal(replica.markStale(generation), true);
  assert.equal(replica.status, "stale");
  assert.deepEqual(replica.snapshot.physical.notes, [60]);
});

await asyncTest("silent stream loss converges through bounded authoritative resnapshot", async () => {
  const timers = new ManualTimers();
  let authoritative = snapshot(0);
  let source;
  const coordinator = new LiveNoteCoordinator({
    eventSourceFactory: () => (source = new FakeEventSource()),
    snapshotLoader: async () => authoritative,
    resnapshotMs: 20,
    freshnessMs: 50,
    now: () => timers.now,
    setTimer: (callback, delay) => timers.set(callback, delay),
    clearTimer: (id) => timers.clear(id),
  });
  coordinator.start();
  await settlePromises();
  source.emit(snapshot(1, [60]));
  authoritative = snapshot(2, []); // release is deliberately not sent on SSE
  timers.advance(20);
  await settlePromises();
  assert.equal(source.closed, false);
  assert.equal(coordinator.replica.snapshot.sequence, 2);
  assert.deepEqual(coordinator.replica.snapshot.physical.notes, []);
  coordinator.stop();
});

await asyncTest("freshness deadline clears highlights when SSE and GET stop advancing", async () => {
  const timers = new ManualTimers();
  let source;
  const coordinator = new LiveNoteCoordinator({
    eventSourceFactory: () => (source = new FakeEventSource()),
    snapshotLoader: async () => { throw new Error("offline"); },
    resnapshotMs: 20,
    freshnessMs: 50,
    now: () => timers.now,
    setTimer: (callback, delay) => timers.set(callback, delay),
    clearTimer: (id) => timers.clear(id),
  });
  coordinator.start();
  await settlePromises();
  source.emit(snapshot(1, [60]));
  timers.advance(50);
  assert.equal(coordinator.replica.status, "stale");
  coordinator.stop();
});

for (const oldEpoch of [false, true]) {
  await asyncTest(`delayed HTTP cannot overwrite newer SSE${oldEpoch ? " across epochs" : ""}`, async () => {
    let resolveSnapshot;
    let source;
    const coordinator = new LiveNoteCoordinator({
      eventSourceFactory: () => (source = new FakeEventSource()),
      snapshotLoader: () => new Promise((resolve) => { resolveSnapshot = resolve; }),
      resnapshotMs: 1000,
      freshnessMs: 2000,
    });
    coordinator.start();
    const newer = oldEpoch
      ? snapshot(0, [72], [], "runtime-b")
      : snapshot(2, [72], [], "runtime-a");
    source.emit(newer);
    resolveSnapshot(snapshot(1, [60], [], "runtime-a"));
    await settlePromises();
    assert.equal(coordinator.replica.snapshot.runtime_epoch, newer.runtime_epoch);
    assert.equal(coordinator.replica.snapshot.sequence, newer.sequence);
    assert.deepEqual(coordinator.replica.snapshot.physical.notes, [72]);
    coordinator.stop();
  });
}

await asyncTest("new visible generation immediately resnapshots authoritative state", async () => {
  let authoritative = snapshot(1, [60]);
  const coordinator = new LiveNoteCoordinator({
    eventSourceFactory: () => new FakeEventSource(),
    snapshotLoader: async () => authoritative,
  });
  coordinator.start();
  await settlePromises();
  coordinator.stop();
  authoritative = snapshot(2, [], [67]);
  coordinator.start();
  await settlePromises();
  assert.equal(coordinator.replica.snapshot.sequence, 2);
  assert.deepEqual(coordinator.replica.snapshot.playback.notes, [67]);
  coordinator.stop();
});

await asyncTest("continuous SSE rejection cannot starve one slow healthy HTTP recovery", async () => {
  const timers = new ManualTimers();
  let started = 0;
  let completed = 0;
  let active = 0;
  let maxConcurrent = 0;
  let sseAttempts = 0;
  let accepted = 0;
  const coordinator = new LiveNoteCoordinator({
    eventSourceFactory: () => {
      sseAttempts += 1;
      const source = new FakeEventSource();
      timers.set(() => source.fail(), 0);
      return source;
    },
    snapshotLoader: () => {
      started += 1;
      active += 1;
      maxConcurrent = Math.max(maxConcurrent, active);
      return new Promise((resolve) => timers.set(() => {
        active -= 1;
        completed += 1;
        resolve(snapshot(7, [60]));
      }, 15));
    },
    onChange: (replica) => {
      if (replica.status === "live") accepted += 1;
    },
    reconnectMs: 10,
    resnapshotMs: 20,
    freshnessMs: 50,
    now: () => timers.now,
    setTimer: (callback, delay) => timers.set(callback, delay),
    clearTimer: (id) => timers.clear(id),
  });
  const generation = coordinator.start();
  timers.advance(16);
  await settlePromises();
  timers.advance(19);
  await settlePromises();
  assert.equal(coordinator.replica.generation, generation);
  assert.equal(coordinator.replica.status, "live");
  assert.equal(coordinator.replica.snapshot.sequence, 7);
  assert.equal(started, 1);
  assert.equal(completed, 1);
  assert.equal(maxConcurrent, 1);
  assert.equal(accepted, 1);
  assert.ok(sseAttempts >= 4);
  coordinator.stop();
});

await asyncTest("repeated start is idempotent while one HTTP recovery is pending", async () => {
  const pending = deferred();
  let started = 0;
  const coordinator = new LiveNoteCoordinator({
    eventSourceFactory: () => new FakeEventSource(),
    snapshotLoader: () => {
      started += 1;
      return pending.promise;
    },
  });
  const generation = coordinator.start();
  for (let index = 0; index < 20; index += 1) {
    assert.equal(coordinator.start(), generation);
  }
  assert.equal(started, 1);
  assert.equal(coordinator.replica.generation, generation);
  pending.resolve(snapshot(3, [64]));
  await settlePromises();
  assert.equal(started, 1);
  assert.equal(coordinator.replica.status, "live");
  assert.equal(coordinator.replica.snapshot.sequence, 3);
  coordinator.stop();
});

await asyncTest("several SSE connection generations do not obsolete pending HTTP", async () => {
  const timers = new ManualTimers();
  const pending = deferred();
  let source;
  let sseAttempts = 0;
  const coordinator = new LiveNoteCoordinator({
    eventSourceFactory: () => {
      sseAttempts += 1;
      source = new FakeEventSource();
      return source;
    },
    snapshotLoader: () => pending.promise,
    reconnectMs: 10,
    freshnessMs: 100,
    now: () => timers.now,
    setTimer: (callback, delay) => timers.set(callback, delay),
    clearTimer: (id) => timers.clear(id),
  });
  const generation = coordinator.start();
  for (let index = 0; index < 4; index += 1) {
    source.fail();
    timers.advance(10);
  }
  assert.equal(sseAttempts, 5);
  assert.equal(coordinator.replica.generation, generation);
  pending.resolve(snapshot(9, [], [72]));
  await settlePromises();
  assert.equal(coordinator.replica.status, "live");
  assert.equal(coordinator.replica.snapshot.sequence, 9);
  assert.deepEqual(coordinator.replica.snapshot.playback.notes, [72]);
  coordinator.stop();
});

await asyncTest("HTTP failure leaves fresh SSE authoritative and retries later", async () => {
  const timers = new ManualTimers();
  let source;
  let requests = 0;
  const coordinator = new LiveNoteCoordinator({
    eventSourceFactory: () => (source = new FakeEventSource()),
    snapshotLoader: async () => {
      requests += 1;
      throw new Error("HTTP unavailable");
    },
    resnapshotMs: 20,
    freshnessMs: 50,
    now: () => timers.now,
    setTimer: (callback, delay) => timers.set(callback, delay),
    clearTimer: (id) => timers.clear(id),
  });
  coordinator.start();
  source.emit(snapshot(4, [67]));
  await settlePromises();
  assert.equal(coordinator.replica.status, "live");
  timers.advance(20);
  await settlePromises();
  assert.equal(requests, 2);
  assert.equal(coordinator.replica.status, "live");
  assert.equal(coordinator.replica.snapshot.sequence, 4);
  coordinator.stop();
});

await asyncTest("visibility replacement cancels old HTTP before one current request", async () => {
  const requests = [];
  let active = 0;
  let maxConcurrent = 0;
  let aborted = 0;
  const coordinator = new LiveNoteCoordinator({
    eventSourceFactory: () => new FakeEventSource(),
    snapshotLoader: (signal) => {
      const item = deferred();
      requests.push(item);
      active += 1;
      maxConcurrent = Math.max(maxConcurrent, active);
      signal.addEventListener("abort", () => {
        aborted += 1;
        item.reject(new Error("aborted"));
      }, { once: true });
      return item.promise.finally(() => { active -= 1; });
    },
  });
  const hiddenGeneration = coordinator.start();
  coordinator.stop();
  const visibleGeneration = coordinator.start();
  coordinator.start();
  assert.notEqual(visibleGeneration, hiddenGeneration);
  assert.equal(requests.length, 1);
  await settlePromises();
  assert.equal(requests.length, 2);
  assert.equal(aborted, 1);
  assert.equal(coordinator.replica.status, "synchronizing");
  requests[1].resolve(snapshot(6, [], [76]));
  await settlePromises();
  assert.equal(maxConcurrent, 1);
  assert.equal(coordinator.replica.status, "live");
  assert.equal(coordinator.replica.snapshot.sequence, 6);
  assert.deepEqual(coordinator.replica.snapshot.playback.notes, [76]);
  coordinator.stop();
});

if (failures > 0) {
  console.error(`${failures} keyboard logic test(s) failed`);
  process.exitCode = 1;
} else {
  console.log("ALL KEYBOARD LOGIC TESTS PASSED");
}
