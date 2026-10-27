/**
 * Learn Library catalog tests (PIANO-LEARN-001).
 * Run: node tests/weblogic/ui008_learnlibrary.test.mjs
 */
import assert from "node:assert/strict";
import {
  LEARN_LIBRARY_FEATURES,
  LEARN_LEVELS,
  LEARN_LIBRARY_PAGE_SIZE,
  learnLibraryOverviewModel,
  learnLessonRowModel,
  learnLessonDetailModel,
  learnLibraryQuery,
  createLearnLibraryController,
} from "../../src/piano_core/web/static/ui008/learnlibrary.js";

let passes = 0;
let failures = 0;
function test(name, fn) {
  try {
    fn();
    passes += 1;
    console.log(`ok - ${name}`);
  } catch (exc) {
    failures += 1;
    console.log(`FAIL - ${name}`);
    console.log(String(exc && exc.stack ? exc.stack : exc));
  }
}

const OVERVIEW = {
  ready: true,
  lesson_count: 1612,
  course_count: 83,
  levels: { 0: 96, 1: 251, 2: 157, 3: 731, 4: 377 },
  sources: { pianio: 1557, mutopia: 51, pianovera: 4 },
  courses: [
    { course_id: "pianio:method-level-1", title: "Method Level 1", level: 0, lesson_count: 22 },
  ],
};

test("capability map is honest and frozen", () => {
  assert.equal(LEARN_LIBRARY_FEATURES.catalog, true);
  assert.equal(LEARN_LIBRARY_FEATURES.handDisplay, "evidence");
  assert.equal(Object.isFrozen(LEARN_LIBRARY_FEATURES), true);
  assert.equal(Object.isFrozen(LEARN_LEVELS), true);
  assert.equal(LEARN_LIBRARY_PAGE_SIZE, 50);
});

test("overview model summarizes counts and levels", () => {
  const model = learnLibraryOverviewModel(OVERVIEW);
  assert.equal(model.ready, true);
  assert.match(model.stats, /1612 lessons/);
  assert.equal(model.levels.length, 5);
  assert.equal(model.levels[0].count, 96);
  assert.equal(model.levels[4].name, "Advanced");
});

test("overview model degrades honestly", () => {
  const model = learnLibraryOverviewModel(null);
  assert.equal(model.ready, false);
  assert.equal(model.stats, "");
});

test("lesson row model maps fields and completion", () => {
  const row = learnLessonRowModel({
    lesson_id: "L1",
    package_id: "P1",
    title: "Middle C Pulse",
    composer: null,
    level: 0,
    difficulty: "easy",
    duration_seconds: 8,
    hands_availability: "available",
    license_name: "CC0-1.0",
    progress: { attempts: 2, completions: 1 },
  });
  assert.equal(row.title, "Middle C Pulse");
  assert.equal(row.levelName, "First Notes");
  assert.equal(row.completed, true);
  assert.equal(row.attempts, 2);
  assert.equal(row.durationText, "8s");
  assert.equal(row.hands, "both hands shown");
});

test("row model rejects malformed lessons", () => {
  assert.equal(learnLessonRowModel(null), null);
  assert.equal(learnLessonRowModel({}), null);
});

test("detail model carries license, neighbors and progress", () => {
  const detail = learnLessonDetailModel({
    lesson_id: "L1",
    package_id: "P1",
    title: "Gymnopédie No. 1",
    composer: "Erik Satie",
    level: 2,
    difficulty: "medium",
    duration_seconds: 210,
    hands_availability: "partial",
    license_name: "Public Domain",
    summary: "First Gymnopédie.",
    license: { name: "Public Domain", attribution: "Mutopia Project" },
    previous_lesson_id: "L0",
    next_lesson_id: "L2",
    progress: { attempts: 3, completions: 1, note_accuracy: 0.87 },
    metrics: { note_count: 549, max_simultaneous: 4, notes_per_second: 3.1 },
    level_decision: { reasons: ["computed score 41.0 -> level 2"] },
  });
  assert.equal(detail.title, "Gymnopédie No. 1");
  assert.match(detail.licenseLine, /Public Domain/);
  assert.match(detail.licenseLine, /Mutopia Project/);
  assert.equal(detail.previousLessonId, "L0");
  assert.equal(detail.nextLessonId, "L2");
  assert.match(detail.progressText, /3 practice runs/);
  assert.match(detail.progressText, /87% notes played/);
  assert.match(detail.metricsLine, /549 notes/);
  assert.match(detail.handsNote, /part of this piece/);
});

test("hands honesty: no fabricated hand claim when unknown", () => {
  const detail = learnLessonDetailModel({
    lesson_id: "L2",
    package_id: "P2",
    title: "Mystery piece",
    level: 1,
    difficulty: "easy",
    duration_seconds: 30,
    hands_availability: "unknown",
    license: {},
  });
  assert.match(detail.handsNote, /not proven/);
});

test("query builder is bounded and filtered", () => {
  const qs = learnLibraryQuery({ course: "c1", level: 2, query: "  gymno  " });
  assert.match(qs, /course=c1/);
  assert.match(qs, /level=2/);
  assert.match(qs, /q=gymno/);
  assert.match(qs, /limit=50/);
  const bare = learnLibraryQuery({ course: "", level: -1, query: "" });
  assert.match(bare, /limit=50/);
  assert.doesNotMatch(bare, /level/);
  const paged = learnLibraryQuery({ course: "", level: -1, query: "", offset: 50 });
  assert.match(paged, /offset=50/);
});

// --- controller against a minimal fake DOM ----------------------------

function fakeElement(id) {
  return {
    id,
    textContent: "",
    hidden: false,
    disabled: false,
    dataset: {},
    value: "",
    children: [],
    listeners: {},
    appendChild(child) {
      this.children.push(child);
    },
    addEventListener(type, fn) {
      this.listeners[type] = fn;
    },
    classList: {
      add() {},
    },
    setAttribute() {},
    closest() {
      return null;
    },
  };
}

function fakeDocument() {
  const elements = new Map();
  const ids = [
    "ui008-learn-library-head",
    "ui008-learn-library-stats",
    "ui008-learn-levels",
    "ui008-learn-course",
    "ui008-learn-search",
    "ui008-learn-lessons",
    "ui008-learn-list-empty",
    "ui008-learn-more",
    "ui008-lesson-detail",
    "ui008-lesson-detail-title",
    "ui008-lesson-detail-sub",
    "ui008-lesson-detail-summary",
    "ui008-lesson-detail-metrics",
    "ui008-lesson-detail-hands",
    "ui008-lesson-detail-license",
    "ui008-lesson-detail-progress",
    "ui008-lesson-prev",
    "ui008-lesson-next",
    "ui008-lesson-start",
    "ui008-lesson-close",
  ];
  const doc = {
    body: { dataset: {} },
    createElement() {
      return fakeElement("created");
    },
  };
  for (const id of ids) {
    const node = fakeElement(id);
    elements.set(id, node);
    doc[id] = node;
  }
  doc.getElementById = (id) => elements.get(id) || null;
  doc._elements = elements;
  return doc;
}

const LESSON_PAGE = {
  lessons: [
    {
      lesson_id: "L1",
      package_id: "P1",
      title: "Middle C Pulse",
      composer: null,
      level: 0,
      difficulty: "easy",
      duration_seconds: 8,
      hands_availability: "available",
      license_name: "CC0-1.0",
    },
    {
      lesson_id: "L2",
      package_id: "P2",
      title: "Gymnopédie No. 1",
      composer: "Erik Satie",
      level: 2,
      difficulty: "medium",
      duration_seconds: 210,
      hands_availability: "available",
      license_name: "Public Domain",
    },
  ],
};

const LESSON_DETAIL = {
  ...LESSON_PAGE.lessons[0],
  summary: "Whole notes on middle C.",
  license: { name: "CC0-1.0", attribution: "Pianio" },
  previous_lesson_id: null,
  next_lesson_id: "L2",
  progress: null,
  metrics: { note_count: 8, max_simultaneous: 1, notes_per_second: 1 },
  level_decision: { reasons: ["first-notes evidence gate met"] },
};

test("controller renders overview, list and detail; selection flows up", async () => {
  const doc = fakeDocument();
  const selected = [];
  const controller = createLearnLibraryController({
    documentRef: doc,
    onLessonSelected: (model) => selected.push(model),
  });
  controller.setFetchers({
    fetchOverview: async () => OVERVIEW,
    fetchLessons: async () => LESSON_PAGE,
    fetchLesson: async () => LESSON_DETAIL,
  });
  controller.attach();
  await controller.refresh();

  const head = doc._elements.get("ui008-learn-library-head");
  assert.equal(head.dataset.state, "ready");
  assert.equal(doc.body.dataset.learnCatalog, "ready");
  assert.match(doc._elements.get("ui008-learn-library-stats").textContent, /1612 lessons/);
  const list = doc._elements.get("ui008-learn-lessons");
  assert.equal(list.children.length, 2);
  await controller.selectLesson("L1");
  const detail = doc._elements.get("ui008-lesson-detail");
  assert.equal(detail.hidden, false);
  assert.match(doc._elements.get("ui008-lesson-detail-title").textContent, /Middle C Pulse/);
  assert.equal(selected.length, 1);
  assert.equal(selected[0].packageId, "P1");
  assert.equal(doc._elements.get("ui008-lesson-next").disabled, false);
  assert.equal(doc._elements.get("ui008-lesson-prev").disabled, true);
});

test("controller degrades when the learn API is down", async () => {
  const doc = fakeDocument();
  const controller = createLearnLibraryController({ documentRef: doc });
  controller.setFetchers({
    fetchOverview: async () => {
      throw new Error("down");
    },
    fetchLessons: async () => {
      throw new Error("down");
    },
    fetchLesson: async () => {
      throw new Error("down");
    },
  });
  controller.attach();
  await controller.refresh();
  const head = doc._elements.get("ui008-learn-library-head");
  assert.equal(head.dataset.state, "unavailable");
  assert.equal(doc.body.dataset.learnCatalog, undefined);
  assert.equal(doc._elements.get("ui008-learn-list-empty").hidden, false);
});

test("controller renders empty state for filters with no matches", async () => {
  const doc = fakeDocument();
  const controller = createLearnLibraryController({ documentRef: doc });
  controller.setFetchers({
    fetchOverview: async () => OVERVIEW,
    fetchLessons: async () => ({ lessons: [] }),
    fetchLesson: async () => null,
  });
  controller.attach();
  await controller.refresh();
  assert.equal(doc._elements.get("ui008-learn-lessons").children.length, 0);
  assert.equal(doc._elements.get("ui008-learn-list-empty").hidden, false);
});

const summary = `node learnlibrary: ${passes} passed, ${failures} failed`;
console.log(summary);
if (failures > 0) process.exit(1);
console.log("ALL LEARNLIBRARY TESTS PASSED");
