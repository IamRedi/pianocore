// PIANO-UI-008 U8-P2: deterministic node tests for the Home/Library pure
// logic — bounded result windowing, stale-search identity, honest row
// models, client-local recents, Continue precedence and readiness mapping.
import assert from "node:assert/strict";
import {
  LIBRARY_PAGE_SIZE,
  LIBRARY_MAX_ROWS,
  planResultWindow,
  libraryRowModel,
  createSearchGate,
} from "../../src/piano_core/web/static/ui008/library.js";
import {
  RECENT_STORE_LIMIT,
  createRecentsStore,
  homeContinueDecision,
  pianoReadiness,
  planHomeSections,
} from "../../src/piano_core/web/static/ui008/home.js";

let passes = 0;
let failures = 0;
function test(name, fn) {
  try {
    fn();
    passes += 1;
    console.log(`ok - ${name}`);
  } catch (error) {
    failures += 1;
    console.error(`FAIL - ${name}`);
    console.error(error);
  }
}

/* ---------------- bounded result window ---------------- */

test("planResultWindow: first browse page renders and offers Load more", () => {
  const plan = planResultWindow({ query: "", offset: 0, renderedCount: 0, count: 50 });
  assert.equal(plan.append, false);
  assert.equal(plan.rows, 50);
  assert.equal(plan.canLoadMore, true);
  assert.equal(plan.capped, false);
});

test("planResultWindow: browse appends up to the hard DOM ceiling", () => {
  const plan = planResultWindow({ query: "", offset: 50, renderedCount: 50, count: 50 });
  assert.equal(plan.append, true);
  assert.equal(plan.rows, 50);
  assert.equal(plan.canLoadMore, false);
  assert.equal(plan.capped, true, "ceiling reached with a full page: capped");
});

test("planResultWindow: browse truncates at LIBRARY_MAX_ROWS (never 5000 rows)", () => {
  const plan = planResultWindow({ query: "", offset: 50, renderedCount: 80, count: 50 });
  assert.equal(plan.rows, LIBRARY_MAX_ROWS - 80);
  assert.equal(plan.capped, true);
  assert.equal(plan.canLoadMore, false);
});

test("planResultWindow: a partial final page ends browse", () => {
  const plan = planResultWindow({ query: "", offset: 50, renderedCount: 50, count: 30 });
  assert.equal(plan.rows, 30);
  assert.equal(plan.canLoadMore, false);
  assert.equal(plan.capped, false);
});

test("planResultWindow: search replaces in one bounded page without Load more", () => {
  const plan = planResultWindow({ query: "sonata", offset: 0, renderedCount: 100, count: 50 });
  assert.equal(plan.append, false);
  assert.equal(plan.rows, 50);
  assert.equal(plan.canLoadMore, false);
  assert.equal(plan.moreRemaining, true);
});

test("planResultWindow: search results can never exceed the DOM bound", () => {
  const plan = planResultWindow({ query: "sonata", count: 5000, pageSize: 5000 });
  assert.equal(plan.rows, LIBRARY_MAX_ROWS);
});

/* ---------------- honest row model ---------------- */

const fullRecord = {
  song_id: "abc",
  title: "Sonata",
  filename: "sonata.mid",
  duration_seconds: 150.4,
  track_count: 2,
  midi_format: 1,
  status: "parsed",
};

test("libraryRowModel: canonical fields only", () => {
  const model = libraryRowModel(fullRecord);
  assert.deepEqual(model, {
    id: "abc",
    title: "Sonata",
    meta: "2:30 · 2 tracks · MIDI 1",
    statusChip: null,
  });
});

test("libraryRowModel: falls back to filename then song_id, never invents", () => {
  assert.equal(libraryRowModel({ song_id: "x1", filename: "b.mid" }).title, "b.mid");
  assert.equal(libraryRowModel({ song_id: "x2" }).title, "x2");
  assert.equal(libraryRowModel({ song_id: "x3" }).meta, "");
});

test("libraryRowModel: status chip appears only for non-parsed entries", () => {
  assert.equal(libraryRowModel({ ...fullRecord, status: "pending" }).statusChip, "pending");
  assert.equal(libraryRowModel({ ...fullRecord, status: "invalid" }).statusChip, "invalid");
  assert.equal(libraryRowModel({ ...fullRecord, status: "parsed" }).statusChip, null);
});

test("libraryRowModel: null record is rejected", () => {
  assert.equal(libraryRowModel(null), null);
});

/* ---------------- request identity gate ---------------- */

test("createSearchGate: the newest token wins and older requests abort", () => {
  const gate = createSearchGate();
  const first = gate.begin();
  const second = gate.begin();
  assert.equal(first.token !== second.token, true);
  assert.equal(gate.isLatest(first.token), false);
  assert.equal(gate.isLatest(second.token), true);
  assert.equal(first.stale(first.token), true);
  assert.equal(second.stale(second.token), false);
});

test("createSearchGate: begin() aborts the previous in-flight signal", () => {
  const gate = createSearchGate();
  const first = gate.begin();
  assert.equal(first.signal.aborted, false);
  const second = gate.begin();
  assert.equal(first.signal.aborted, true);
  assert.equal(second.signal.aborted, false);
});

/* ---------------- client-local recents ---------------- */

function memoryStorage() {
  const map = new Map();
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
  };
}

const sampleSong = { song_id: "s1", title: "A", filename: "a.mid", duration_seconds: 10 };

test("recents store: records, deduplicates and fronts the newest", () => {
  const storage = memoryStorage();
  const store = createRecentsStore({ storage });
  store.record(sampleSong);
  store.record({ song_id: "s2", title: "B" });
  store.record(sampleSong);
  const list = store.list();
  assert.equal(list.length, 2);
  assert.equal(list[0].song_id, "s1");
  assert.equal(list[1].song_id, "s2");
  assert.equal(list[0].title, "A");
});

test("recents store: bounded at the store limit", () => {
  const store = createRecentsStore({ storage: memoryStorage() });
  for (let i = 0; i < RECENT_STORE_LIMIT + 10; i += 1) {
    store.record({ song_id: `s${i}`, title: `T${i}` });
  }
  assert.equal(store.list().length, RECENT_STORE_LIMIT);
});

test("recents store: corrupt storage is discarded, never trusted", () => {
  const storage = memoryStorage();
  storage.setItem("piano-core.ui008-recent", "not-json{");
  const store = createRecentsStore({ storage });
  assert.deepEqual(store.list(), []);
  store.record(sampleSong);
  assert.equal(store.list().length, 1);
});

test("recents store: entries without song_id are ignored", () => {
  const store = createRecentsStore({ storage: memoryStorage() });
  store.record({ title: "no id" });
  store.record(null);
  assert.deepEqual(store.list(), []);
});

/* ---------------- Continue precedence ---------------- */

const pausedPlayback = {
  available: true, state: "PAUSED", song_id: "p1", title: "Nocturne",
  position_seconds: 81, duration_seconds: 200,
};
const waitingLearning = {
  available: true, state: "WAITING", song_id: "p1", title: "Nocturne",
  progress: 0.42, mode: "WAIT_FOR_NOTE",
};

test("continue: PLAYING playback wins with a playing meta", () => {
  const d = homeContinueDecision({
    playback: { ...pausedPlayback, state: "PLAYING" },
    learning: waitingLearning,
  });
  assert.equal(d.kind, "playback");
  assert.equal(d.meta, "Playing now");
  assert.equal(d.route, "player");
});

test("continue: PAUSED playback shows the authoritative position", () => {
  const d = homeContinueDecision({ playback: pausedPlayback });
  assert.equal(d.kind, "playback");
  assert.equal(d.meta, "Paused at 1:21");
});

test("continue: active learning is offered when playback is idle", () => {
  const d = homeContinueDecision({
    playback: { ...pausedPlayback, state: "IDLE" },
    learning: waitingLearning,
  });
  assert.equal(d.kind, "learning");
  assert.equal(d.route, "learn");
  assert.match(d.meta, /42% complete/);
});

test("continue: nothing resumable yields none", () => {
  assert.equal(homeContinueDecision({}).kind, "none");
  assert.equal(
    homeContinueDecision({ playback: { ...pausedPlayback, state: "COMPLETED" } }).kind,
    "none"
  );
  assert.equal(
    homeContinueDecision({ playback: pausedPlayback, offline: true }).kind,
    "none"
  );
  assert.equal(
    homeContinueDecision({ learning: { ...waitingLearning, state: "COMPLETED" } }).kind,
    "none"
  );
});

/* ---------------- readiness + home sections ---------------- */

test("readiness maps the canonical MIDI connection honestly", () => {
  assert.deepEqual(pianoReadiness("CONNECTED", false), { label: "Piano connected", tone: "ok" });
  assert.deepEqual(pianoReadiness("CONNECTING", false), { label: "Looking for your piano", tone: "wait" });
  assert.deepEqual(pianoReadiness("DISCONNECTED", false), { label: "Piano unavailable", tone: "bad" });
  assert.deepEqual(pianoReadiness("CONNECTED", true), { label: "Status unavailable", tone: "bad" });
});

test("home sections: continue + recents suppress the welcome state", () => {
  const plan = planHomeSections({
    continueDecision: { kind: "playback" },
    recents: [sampleSong],
  });
  assert.equal(plan.continueVisible, true);
  assert.equal(plan.recentVisible, true);
  assert.equal(plan.welcomeVisible, false);
});

test("home sections: empty first run shows exactly the welcome state", () => {
  const plan = planHomeSections({ continueDecision: { kind: "none" }, recents: [] });
  assert.equal(plan.continueVisible, false);
  assert.equal(plan.recentVisible, false);
  assert.equal(plan.welcomeVisible, true);
});

test("library constants stay at the ADR-019 bounded values", () => {
  assert.equal(LIBRARY_PAGE_SIZE, 50);
  assert.equal(LIBRARY_MAX_ROWS, 100);
});

if (failures) {
  console.error(`FAIL ${failures}; PASS ${passes}`);
  process.exit(1);
}
console.log(`PASS ${passes}/${passes}`);
