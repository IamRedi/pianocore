/** Studio presentation only: no fetch, input matching or musical authority. */
export function focusRange(notes, keys, whiteCount) {
  const pitches = notes.map(n => n.midi).filter(n => Number.isInteger(n) && n >= 21 && n <= 108);
  if (!pitches.length) return null;
  const low = Math.max(21, pitches.reduce((a, b) => Math.min(a, b), 108) - 3);
  const high = Math.min(108, pitches.reduce((a, b) => Math.max(a, b), 21) + 3);
  const whites = keys.filter(k => !k.black);
  let first = whites.findIndex(k => k.midi >= low);
  let last = whites.findLastIndex(k => k.midi <= high);
  if (last - first < 13) { first = Math.max(0, first - 4); last = Math.min(whites.length - 1, Math.max(last, first + 13)); first = Math.max(0, Math.min(first, last - 13)); }
  return { left: first / whiteCount, right: (last + 1) / whiteCount };
}

export function flowVisualPosition(session, receivedAtMs, nowMs) {
  const f = session?.flow;
  if (!f || !Number.isFinite(f.position_seconds) || !Number.isFinite(f.speed)) return null;
  const age = Math.max(0, nowMs - receivedAtMs);
  const moving = session.state === "WAITING" && !f.paused;
  return { position: f.position_seconds + (moving ? Math.min(age, 350) * f.speed / 1000 : 0), stale: moving && age > 350 };
}

export function createStudioController({ documentRef: doc, onStop, onRestart, onPause, onNext, onVisual, onRange, onExit, onRetrySave, onMode }) {
  let session = null;
  let lesson = null;
  let busy = false;
  const el = id => doc.getElementById(id);
  const text = (id, value) => { if (el(id)) el(id).textContent = value; };
  async function fullscreen() {
    if (doc.fullscreenElement) { await doc.exitFullscreen?.().catch(() => {}); return; }
    try { await doc.documentElement.requestFullscreen?.(); } catch { /* Internal viewport remains usable. */ }
    syncFullscreen();
  }
  function syncFullscreen() {
    const native = Boolean(doc.fullscreenElement);
    doc.body.dataset.studioFullscreen = native ? "native" : "viewport";
    el("studio-fullscreen")?.setAttribute("aria-label", native ? "Exit browser fullscreen" : "Enter browser fullscreen");
  }
  async function exit() {
    if (busy) return;
    busy = true;
    try {
      // The command boundary must confirm fresh inactive truth or Stop.
      // A stale/unknown snapshot or an in-flight Start cannot authorize Exit.
      if (await onStop() !== true) return;
      doc.body.dataset.learnStudio = "closed";
      if (doc.fullscreenElement) await doc.exitFullscreen?.().catch(() => {});
      onExit?.(lesson);
    } finally { busy = false; }
  }
  for (const id of ["studio-exit", "studio-back"]) el(id)?.addEventListener("click", exit);
  for (const id of ["studio-restart", "studio-retry"]) el(id)?.addEventListener("click", async () => { if (busy) return; busy = true; try { await onRestart(); } finally { busy = false; } });
  el("studio-pause")?.addEventListener("click", () => onPause(!session?.flow?.paused));
  el("studio-save-retry")?.addEventListener("click", () => onRetrySave?.());
  el("studio-previous")?.addEventListener("click", () => onNext(lesson?.previousLessonId));
  el("studio-forward")?.addEventListener("click", () => onNext(lesson?.nextLessonId));
  el("studio-session-mode")?.addEventListener("change", () => onMode?.(el("studio-session-mode").value));
  el("studio-next")?.addEventListener("click", () => onNext(lesson?.nextLessonId));
  el("studio-fullscreen")?.addEventListener("click", fullscreen);
  el("studio-range")?.addEventListener("change", () => onRange(el("studio-range").value));
  el("studio-visual")?.addEventListener("change", () => {
    const mode = el("studio-visual").value;
    if (el("studio-notation-note")) el("studio-notation-note").hidden = !["sheet", "hybrid"].includes(mode);
    onVisual(mode);
  });
  doc.addEventListener("fullscreenchange", syncFullscreen);
  return {
    setLesson(model) { lesson = model; },
    enter(model, native = true) {
      lesson = model; doc.body.dataset.learnStudio = "open";
      el("practice").hidden = false;
      // Invoked directly by a lesson-start click: native user activation.
      if (native && !doc.fullscreenElement) void fullscreen(); syncFullscreen();
    },
    update(snapshot, stale) {
      if (snapshot) session = snapshot;
      snapshot = snapshot || session;
      const flow = snapshot?.flow;
      if (el("studio-session-mode") && snapshot?.session_id) el("studio-session-mode").value = snapshot.mode;
      if (el("studio-session-mode")) el("studio-session-mode").disabled = !!stale || busy;
      if (el("studio-previous")) el("studio-previous").disabled = !lesson?.previousLessonId || !!stale;
      if (el("studio-forward")) el("studio-forward").disabled = !lesson?.nextLessonId || !!stale;
      text("studio-mode-label", flow ? `Flow · ${Math.round(flow.speed * 100)}%` : "Wait for me");
      const outcome = snapshot?.latest_outcome;
      const labels = { CORRECT: "Correct", EARLY: "Early", LATE: "Late", MISSED: "Missed", INCORRECT: "Wrong note", INPUT_UNAVAILABLE: "Connect your piano" };
      text("studio-feedback", snapshot?.progress_persistence_error ? "Progress could not be saved · retry saving before leaving" : snapshot?.progress_pending_count ? "Saving progress…" : outcome === "INPUT_UNAVAILABLE" ? "Connect your piano" : stale ? "Reconnecting · visual frozen" : flow?.paused ? "Paused" : flow?.count_in_seconds > 0 ? `Ready · ${Math.ceil(flow.count_in_seconds)}` : labels[outcome] || "Follow the highlighted keys");
      if (el("studio-feedback")) el("studio-feedback").dataset.outcome = outcome || "NONE";
      const active = snapshot?.state === "WAITING";
      if (el("studio-pause")) { el("studio-pause").hidden = !flow || !active; el("studio-pause").disabled = !!stale; el("studio-pause").textContent = flow?.paused ? "Resume" : "Pause"; }
      if (el("studio-restart")) el("studio-restart").disabled = !snapshot?.session_id || !!stale;
      const done = snapshot?.state === "COMPLETED";
      if (el("studio-results")) el("studio-results").hidden = !done;
      if (done) {
        text("studio-results-detail", flow ? `${flow.correct_events} / ${flow.expected_events} events correct · ${flow.missed_events} missed · ${flow.wrong_presses} wrong presses. ${flow.measured_attacks ? `${flow.on_time_attacks} on time, ${flow.early_attacks} early, ${flow.late_attacks} late · average absolute timing error ${flow.mean_absolute_error_ms} ms.` : "No timing samples captured."}` : `${snapshot.completed_steps} / ${snapshot.total_steps} targets completed · ${snapshot.incorrect_attempts} extra presses. At your own pace.`);
      }
      if (el("studio-save-retry")) el("studio-save-retry").hidden = !snapshot?.progress_persistence_error;
      if (el("studio-next")) el("studio-next").disabled = !lesson?.nextLessonId || !!stale || !!snapshot?.progress_persistence_error || snapshot?.progress_pending_count > 0;
    },
    isOpen: () => doc.body.dataset.learnStudio === "open",
  };
}
