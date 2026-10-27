/**
 * Learn Library catalog (PIANO-LEARN-001) — pure presentation logic.
 *
 * Zero fetch, zero commands: the controller renders catalog state and
 * reports lesson selection upward via injected callbacks. Musical
 * authority (targets, grading, wait-for-me) stays entirely in Core's
 * guided practice engine; this module only organizes the catalog and
 * mirrors what Core truthfully reports.
 */

export const LEARN_LIBRARY_FEATURES = Object.freeze({
  catalog: true,
  search: true,
  levelFilter: true,
  courseFilter: true,
  lessonNavigation: true,
  lessonProgress: true,
  handDisplay: "evidence", // shown only when the source proved hands
  progressPersistence: "package_sessions",
});

export const LEARN_LEVELS = Object.freeze([
  { level: 0, name: "First Notes", hint: "One note at a time" },
  { level: 1, name: "Beginner", hint: "Simple melodies, one hand" },
  { level: 2, name: "Elementary", hint: "Both hands, simple chords" },
  { level: 3, name: "Intermediate", hint: "Coordination and longer pieces" },
  { level: 4, name: "Advanced", hint: "Advanced repertoire" },
]);

export const LEARN_LIBRARY_PAGE_SIZE = 50;
export const LEARN_LIBRARY_MAX_ROWS = 100;
export const LEARN_LIBRARY_DEBOUNCE_MS = 300;

function formatDuration(seconds) {
  const total = Number(seconds);
  if (!Number.isFinite(total) || total <= 0) return "";
  const mins = Math.floor(total / 60);
  const secs = Math.round(total % 60);
  if (mins <= 0) return `${secs}s`;
  return `${mins}m ${String(secs).padStart(2, "0")}s`;
}

export function learnLibraryOverviewModel(overview) {
  if (!overview || overview.ready !== true || typeof overview.lesson_count !== "number") {
    return { ready: false, stats: "", levels: [], courses: [] };
  }
  const levels = LEARN_LEVELS.map((entry) => ({
    ...entry,
    count: overview.levels ? overview.levels[String(entry.level)] || 0 : 0,
  }));
  return {
    ready: true,
    stats: `${overview.lesson_count} lessons · ${overview.course_count} courses`,
    levels,
    courses: Array.isArray(overview.courses) ? overview.courses : [],
  };
}

export function learnLessonRowModel(lesson) {
  if (!lesson || typeof lesson.lesson_id !== "string" || !lesson.lesson_id ||
      typeof lesson.package_id !== "string" || !lesson.package_id || /[\s:/\\?#%]/.test(lesson.package_id)) return null;
  const levelName = LEARN_LEVELS[lesson.level]
    ? LEARN_LEVELS[lesson.level].name
    : `Level ${lesson.level}`;
  const progress = lesson.progress || null;
  const completed = Boolean(
    progress && Number(progress.completions) > 0
  );
  const attempts = progress && Number(progress.attempts) > 0 ? Number(progress.attempts) : 0;
  return {
    lessonId: lesson.lesson_id,
    packageId: lesson.package_id,
    title: lesson.title || lesson.lesson_id,
    composer: lesson.composer || "",
    source: lesson.source || "",
    level: lesson.level,
    levelName,
    difficulty: lesson.difficulty || "",
    durationText: formatDuration(lesson.duration_seconds),
    durationSeconds: lesson.duration_seconds,
    handSelectionAvailable: lesson.hands_availability === "available",
    hands: lesson.hands_availability === "available" ? "both hands shown" : "",
    completed,
    attempts,
    progressUnavailable: lesson.progress_available === false,
    licenseName: lesson.license_name || "",
  };
}

export function learnLessonDetailModel(detail) {
  if (!detail || typeof detail.lesson_id !== "string") return null;
  const row = learnLessonRowModel(detail);
  if (!row) return null;
  const metrics = detail.metrics || {};
  const progress = detail.progress || null;
  return {
    ...row,
    summary: detail.summary || "",
    metricsLine: [
      metrics.note_count != null ? `${metrics.note_count} notes` : null,
      metrics.max_simultaneous != null
        ? `up to ${metrics.max_simultaneous} together`
        : null,
      metrics.notes_per_second != null
        ? `${Number(metrics.notes_per_second).toFixed(1)} notes/s`
        : null,
    ]
      .filter(Boolean)
      .join(" · "),
    handsNote:
      detail.hands_availability === "available"
        ? "Left/right hand assignment from the verified source."
        : detail.hands_availability === "partial"
          ? "Hand assignment available for part of this piece."
          : "Hand assignment not proven for this piece.",
    licenseLine: buildLicenseLine(detail.license || {}),
    progressText: detail.progress_available === false ? "Progress unavailable" : progressText(progress),
    previousLessonId: detail.previous_lesson_id || null,
    nextLessonId: detail.next_lesson_id || null,
    levelReasons: (detail.level_decision && detail.level_decision.reasons) || [],
  };
}

function buildLicenseLine(license) {
  const parts = [];
  if (license.name) parts.push(license.name);
  if (license.attribution) parts.push(license.attribution);
  return parts.join(" · ");
}

function progressText(progress) {
  if (!progress || Number(progress.attempts) <= 0) return "Not practiced yet";
  const parts = [`${progress.attempts} practice run${progress.attempts === 1 ? "" : "s"}`];
  if (Number(progress.completions) > 0) {
    parts.push(`${progress.completions} complete`);
  }
  if (progress.note_accuracy != null) {
    parts.push(`${Math.round(Number(progress.note_accuracy) * 100)}% notes played`);
  }
  return parts.join(" · ");
}

/**
 * Filter state for the catalog list request.
 */
export function learnLibraryQuery({ course, level, query, offset = 0 }) {
  const params = new URLSearchParams();
  if (course) params.set("course", course);
  if (level !== null && level !== undefined && level >= 0) {
    params.set("level", String(level));
  }
  if (query && String(query).trim()) {
    params.set("q", String(query).trim());
  }
  params.set("limit", String(LEARN_LIBRARY_PAGE_SIZE));
  if (offset > 0) params.set("offset", String(offset));
  const text = params.toString();
  return text ? `?${text}` : "";
}

/**
 * Controller: owns the catalog DOM on the Learn route. Pure rendering +
 * upward callbacks; no fetch, no commands.
 */
export function createLearnLibraryController({
  documentRef,
  onLessonSelected,
  beginSelectionIntent = null,
  isSelectionIntentCurrent = null,
  onSelectionError = null,
}) {
  const doc = documentRef;
  const state = {
    overview: null,
    lessons: [],
    totalLoaded: 0,
    selectedLessonId: null,
    course: "",
    level: -1,
    query: "",
    debounceTimer: 0,
    requestToken: 0,
    detailToken: 0,
    detailController: null,
    offset: 0,
    hasMore: false,
    courseLevel: -1,
  };

  function el(id) {
    return doc.getElementById(id);
  }

  function setText(node, text) {
    if (node) node.textContent = text;
  }

  function renderOverview() {
    const model = learnLibraryOverviewModel(state.overview);
    const head = el("ui008-learn-library-head");
    if (!head) return;
    if (!model.ready) {
      head.dataset.state = "unavailable";
      setText(el("ui008-learn-library-stats"), "Learn library is unavailable.");
      return false;
    }
    head.dataset.state = "ready";
    setText(el("ui008-learn-library-stats"), model.stats);
    doc.body.dataset.learnCatalog = "ready";
    renderLevelChips(model.levels);
    renderCourseOptions(model.courses);
    renderAcademy(model);
  }

  function node(tag, className, text = "") {
    const n = doc.createElement(tag); n.className = className; n.textContent = text; return n;
  }

  function renderAcademy(model) {
    const progress = state.overview?.progress || {};
    const resume = progress.continue_lesson;
    if (el("academy-continue")) el("academy-continue").hidden = !resume;
    setText(el("academy-continue-heading"), resume?.title || "");
    const course = model.courses.find(c => c.course_id === resume?.course_id);
    setText(el("academy-continue-meta"), course ? `${course.title} · ${course.completed_lessons || 0} of ${course.lesson_count} lessons complete` : "");
    setText(el("academy-progress-total"), progress.available ? `${progress.completed_lessons || 0} lessons completed` : "Practice to begin your journey");
    const journey = el("academy-journey");
    if (journey) {
      journey.replaceChildren(...model.levels.map(entry => {
        const done = progress.levels?.[String(entry.level)] || 0;
        const b = node("button", "journey-card"); b.type = "button"; b.dataset.level = String(entry.level);
        b.append(node("span", "journey-number", `0${entry.level + 1}`), node("strong", "", entry.name), node("span", "", progress.available ? `${done} / ${entry.count} lessons · ${Math.max(0, entry.count - done)} remaining` : `${entry.count} lessons · progress unavailable`));
        const meter = doc.createElement("progress"); meter.max = entry.count || 1; meter.value = done; meter.hidden = !progress.available; meter.setAttribute("aria-label", `${entry.name} completed lessons`); b.append(meter); return b;
      }));
    }
    const courses = el("academy-courses");
    if (courses) {
      courses.replaceChildren(...model.courses.filter(c => state.courseLevel < 0 || c.level === state.courseLevel).map(c => {
        const b = node("button", "academy-course"); b.type = "button"; b.dataset.courseId = c.course_id;
        b.append(node("span", "course-level", `LEVEL ${c.level} · ${c.lesson_count} LESSONS`), node("strong", "", c.title), node("span", "course-description", c.summary || LEARN_LEVELS[c.level]?.name || ""), node("span", "course-progress", progress.available ? `${c.completed_lessons || 0} of ${c.lesson_count} complete →` : "Explore course →")); return b;
      }));
    }
  }

  function filterCourse(id) {
    // Opening a course shows its contents, without unrelated search/level filters.
    state.course = id; state.level = -1; state.query = "";
    if (el("ui008-learn-search")) el("ui008-learn-search").value = "";
    renderOverview();
    loadLessons({}); el("ui008-learn-library-head")?.scrollIntoView({ block: "start", behavior: "auto" });
  }

  function renderLevelChips(levels) {
    const host = el("ui008-learn-levels");
    if (!host) return;
    host.textContent = "";
    const all = doc.createElement("button");
    all.type = "button";
    all.className = "learn-level-chip";
    all.dataset.level = "-1";
    all.setAttribute("aria-pressed", state.level === -1 ? "true" : "false");
    all.textContent = "All levels";
    host.appendChild(all);
    for (const entry of levels) {
      if (!entry.count) continue;
      const chip = doc.createElement("button");
      chip.type = "button";
      chip.className = "learn-level-chip";
      chip.dataset.level = String(entry.level);
      chip.setAttribute("aria-pressed", state.level === entry.level ? "true" : "false");
      chip.textContent = `L${entry.level} ${entry.name} (${entry.count})`;
      host.appendChild(chip);
    }
  }

  function renderCourseOptions(courses) {
    const select = el("ui008-learn-course");
    if (!select) return;
    select.textContent = "";
    const all = doc.createElement("option");
    all.value = "";
    all.textContent = "All courses";
    select.appendChild(all);
    for (const course of courses) {
      const option = doc.createElement("option");
      option.value = course.course_id;
      option.textContent = `L${course.level} · ${course.title} (${course.lesson_count})`;
      select.appendChild(option);
    }
    select.value = state.course;
  }

  function lessonRow(item) {
    const li = doc.createElement("li");
    li.className = "learn-lesson-item";
    const button = doc.createElement("button");
    button.type = "button";
    button.className = "btn learn-lesson-btn";
    button.dataset.lessonId = item.lessonId;
    if (item.completed) button.classList.add("learn-lesson-btn--done");
    const name = doc.createElement("span");
    name.className = "learn-lesson-name";
    name.textContent = item.completed ? `✓ ${item.title}` : item.title;
    const sub = doc.createElement("span");
    sub.className = "learn-lesson-sub";
    sub.textContent = [
      item.composer,
      `L${item.level} ${item.levelName}`,
      item.difficulty,
      item.durationText,
      item.hands,
    ]
      .filter(Boolean)
      .join(" · ");
    button.appendChild(name);
    button.appendChild(sub);
    li.appendChild(button);
    return li;
  }

  function renderLessons({ append }) {
    const list = el("ui008-learn-lessons");
    if (!list) return;
    list.textContent = "";
    for (const item of state.lessons) {
      list.appendChild(lessonRow(item));
    }
    const more = el("ui008-learn-more");
    if (more) {
      more.hidden = !state.hasMore;
    }
    if (el("academy-prev-page")) el("academy-prev-page").disabled = state.offset === 0;
    setText(el("academy-page"), `Page ${Math.floor(state.offset / LEARN_LIBRARY_PAGE_SIZE) + 1}`);
    const empty = el("ui008-learn-list-empty");
    if (empty) {
      empty.hidden = state.lessons.length > 0;
    }
  }

  function renderDetail(detailModel) {
    const panel = el("ui008-lesson-detail");
    if (!panel) return;
    if (!detailModel) {
      panel.hidden = true;
      state.selectedLessonId = null;
      return;
    }
    panel.hidden = false;
    state.selectedLessonId = detailModel.lessonId;
    setText(el("ui008-lesson-detail-title"), detailModel.completed ? `✓ ${detailModel.title}` : detailModel.title);
    setText(el("ui008-lesson-detail-sub"), [
      detailModel.composer,
      `L${detailModel.level} ${detailModel.levelName}`,
      detailModel.difficulty,
      detailModel.durationText,
    ].filter(Boolean).join(" · "));
    setText(el("ui008-lesson-detail-summary"), detailModel.summary);
    setText(el("ui008-lesson-detail-metrics"), detailModel.metricsLine);
    setText(el("ui008-lesson-detail-hands"), detailModel.handsNote);
    setText(el("ui008-lesson-detail-license"), detailModel.licenseLine);
    setText(el("ui008-lesson-detail-progress"), detailModel.progressText);
    const prev = el("ui008-lesson-prev");
    const next = el("ui008-lesson-next");
    if (prev) {
      prev.disabled = !detailModel.previousLessonId;
      prev.dataset.lessonId = detailModel.previousLessonId || "";
    }
    if (next) {
      next.disabled = !detailModel.nextLessonId;
      next.dataset.lessonId = detailModel.nextLessonId || "";
    }
  }

  async function loadLessons({ append = false, offset = 0 } = {}) {
    const token = ++state.requestToken;
    const qs = learnLibraryQuery({
      course: state.course,
      level: state.level,
      query: state.query,
      offset,
    });
    let rows;
    try {
      rows = await state.fetchLessons(qs);
    } catch (exc) {
      if (token !== state.requestToken) return;
      const head = el("ui008-learn-library-head");
      if (head) head.dataset.state = "unavailable";
      setText(el("ui008-learn-library-stats"), "Learn library is unavailable.");
      return;
    }
    if (token !== state.requestToken) return;
    const models = (rows.lessons || [])
      .map(learnLessonRowModel)
      .filter(Boolean);
    state.offset = offset;
    state.hasMore = models.length === LEARN_LIBRARY_PAGE_SIZE;
    state.lessons = models;
    renderLessons({ append });
    return true;
  }

  function cancelPendingSelection() {
    state.detailToken += 1;
    state.detailController?.abort();
    state.detailController = null;
  }

  async function openLesson(lessonId, { beforeSelect, intent = null } = {}) {
    cancelPendingSelection();
    const ticket = ++state.detailToken;
    const controller = new AbortController();
    state.detailController = controller;
    intent ??= beginSelectionIntent?.("package") ?? null;
    const abort = () => controller.abort();
    if (intent?.signal.aborted) controller.abort();
    else intent?.signal.addEventListener("abort", abort, { once: true });
    const current = () => ticket === state.detailToken && !controller.signal.aborted &&
      (!intent || !isSelectionIntentCurrent || isSelectionIntentCurrent(intent));
    try {
      if (typeof lessonId !== "string" || !lessonId) throw Object.assign(new Error("Choose a valid lesson."), { code: "invalid_lesson_id" });
      const detail = await state.fetchLesson(lessonId, { signal: controller.signal });
      if (!current()) return null;
      const detailModel = learnLessonDetailModel(detail);
      if (detail?.lesson_id !== lessonId || !detailModel) {
        throw Object.assign(new Error("The returned lesson identity is invalid."), { code: "invalid_response" });
      }
      if (beforeSelect && await beforeSelect(detailModel) !== true) return null;
      if (!current()) return null;
      if (onLessonSelected?.(detailModel, intent) === false) return null;
      if (!current()) return null;
      renderDetail(detailModel);
      return detailModel;
    } catch (exc) {
      if (current()) {
        renderDetail(null);
        onSelectionError?.({ code: exc.code || "network_error", message: exc.message, lessonId });
      }
      return null;
    } finally {
      intent?.signal.removeEventListener("abort", abort);
      if (state.detailController === controller) state.detailController = null;
    }
  }

  const controller = {
    async refresh() {
      let overview;
      try {
        overview = await state.fetchOverview();
      } catch (exc) {
        overview = null;
      }
      state.overview = overview;
      renderOverview();
      if (!learnLibraryOverviewModel(overview).ready) return false;
      return await loadLessons({});
    },
    setFetchers({ fetchOverview, fetchLessons, fetchLesson }) {
      state.fetchOverview = fetchOverview;
      state.fetchLessons = fetchLessons;
      state.fetchLesson = fetchLesson;
    },
    selectLesson(lessonId, options) {
      return openLesson(lessonId, options);
    },
    clearSelection() {
      cancelPendingSelection();
      renderDetail(null);
    },
    cancelPendingSelection,
    selectedLessonId() {
      return state.selectedLessonId;
    },
    attach() {
      el("academy-prev-page")?.addEventListener("click", () => loadLessons({ offset: Math.max(0, state.offset - LEARN_LIBRARY_PAGE_SIZE) }));
      el("academy-resume")?.addEventListener("click", () => { const lesson = state.overview?.progress?.continue_lesson; if (lesson) openLesson(lesson.lesson_id); });
      el("academy-begin")?.addEventListener("click", () => { state.level = 0; state.course = ""; state.query = ""; if (el("ui008-learn-search")) el("ui008-learn-search").value = ""; renderOverview(); loadLessons({}); el("ui008-learn-library-head")?.scrollIntoView({ block: "start" }); });
      el("academy-course-level")?.addEventListener("change", () => { state.courseLevel = Number(el("academy-course-level").value); renderAcademy(learnLibraryOverviewModel(state.overview)); });
      el("academy-courses")?.addEventListener("click", event => { const b = event.target.closest("[data-course-id]"); if (b) filterCourse(b.dataset.courseId); });
      el("academy-journey")?.addEventListener("click", event => { const b = event.target.closest("[data-level]"); if (b) { state.level = Number(b.dataset.level); state.course = ""; renderOverview(); loadLessons({}); el("ui008-learn-library-head")?.scrollIntoView({ block: "start" }); } });
      const levels = el("ui008-learn-levels");
      if (levels) {
        levels.addEventListener("click", (event) => {
          const chip = event.target.closest("[data-level]");
          if (!chip) return;
          state.level = Number(chip.dataset.level);
          renderLevelChips(learnLibraryOverviewModel(state.overview).levels);
          loadLessons({});
        });
      }
      const course = el("ui008-learn-course");
      if (course) {
        course.addEventListener("change", () => {
          state.course = course.value;
          loadLessons({});
        });
      }
      const search = el("ui008-learn-search");
      if (search) {
        search.addEventListener("input", () => {
          window.clearTimeout(state.debounceTimer);
          state.debounceTimer = window.setTimeout(() => {
            state.query = search.value;
            loadLessons({});
          }, LEARN_LIBRARY_DEBOUNCE_MS);
        });
      }
      const list = el("ui008-learn-lessons");
      if (list) {
        list.addEventListener("click", (event) => {
          const button = event.target.closest("[data-lesson-id]");
          if (!button) return;
          openLesson(button.dataset.lessonId);
        });
      }
      const more = el("ui008-learn-more");
      if (more) {
        more.addEventListener("click", () => {
          loadLessons({ offset: state.offset + LEARN_LIBRARY_PAGE_SIZE });
        });
      }
      const prev = el("ui008-lesson-prev");
      if (prev) {
        prev.addEventListener("click", () => {
          if (prev.dataset.lessonId) openLesson(prev.dataset.lessonId);
        });
      }
      const next = el("ui008-lesson-next");
      if (next) {
        next.addEventListener("click", () => {
          if (next.dataset.lessonId) openLesson(next.dataset.lessonId);
        });
      }
      const start = el("ui008-lesson-start");
      if (start) {
        start.addEventListener("click", () => {
          if (typeof controller.onStartRequested === "function") {
            controller.onStartRequested();
          }
        });
      }
      const close = el("ui008-lesson-close");
      if (close) {
        close.addEventListener("click", () => { controller.clearSelection(); });
      }
    },
  };
  return controller;
}
