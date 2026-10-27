/* PIANO-UI-008 U8-P4: premium Learn workspace presentation controller.
 *
 * Presentation ONLY over the accepted Practice/Learning authority
 * (PIANO-P015 + PIANO-UI-004). The existing app.js Learning coordinator
 * remains the single command boundary; this module never issues a command
 * and never decides correctness. It owns exactly:
 * - LEARN_FEATURES: the honest capability map (what the CURRENT accepted
 *   Core exposes — future Learning Engine V2 modes stay absent);
 * - consumer wording models: state chip, guidance line, feedback,
 *   progress text (authoritative counters only) and mode labels;
 * - target presentation: note chips split into accepted/remaining parts
 *   from the AUTHORITATIVE accepted_notes field (never browser inference);
 * - controls enablement including the Playback/Practice mutex truth.
 */

export const LEARN_FEATURES = Object.freeze({
  selectSong: true, // via the accepted Library → Learn path
  start: true, // POST /learning/start
  stop: true, // POST /learning/stop
  restartTarget: true, // WAITING only, Core-guarded
  restartSection: true, // WAITING only, Core-guarded
  waitMode: true, // WAIT_FOR_NOTE
  sectionMode: true, // SECTION_PRACTICE (Core requires a section)
  repeatUntilCorrectMode: true, // + required_repetitions (1..16)
  pauseResume: false, // Wait has no pause semantics
  flowMode: true,
  flowPauseResume: true,
  tempoMutation: false, // target_tempo is observational metadata only
  handSelection: false, // library songs carry no hand data (packages aside)
  watchListenFollow: false, // planned Learning Engine V2 — absent
  scoreMetrics: false, // no accuracy/stars/mastery/streaks exist
  ledIndicator: true, // truthful Core LED status only, never correctness
  visualModes: Object.freeze(["sheet", "falling", "hybrid"]),
  handsCameraModes: Object.freeze(["full", "follow"]),
});

/** The three real Core learning strategies with consumer wording. */
export const LEARN_MODES = Object.freeze([
  Object.freeze({
    value: "WAIT_FOR_NOTE",
    label: "Wait for me",
    hint: "Core waits for every highlighted note or chord.",
  }),
  Object.freeze({
    value: "SECTION_PRACTICE",
    label: "Section practice",
    hint: "Practice one time range of the song.",
  }),
  Object.freeze({
    value: "REPEAT_UNTIL_CORRECT",
    label: "Repeat until correct",
    hint: "Repeat the target cleanly, in a row, before moving on.",
  }),
  Object.freeze({ value: "FLOW", label: "Flow / play along", hint: "Play with the musical clock. Core measures your attacks." }),
]);

/* ---------------------------------------------------------------------
 * Pure logic (runs under node too)
 * ------------------------------------------------------------------- */

/** Consumer label for the real Core mode value (never the enum raw). */
export function learnModeLabel(mode) {
  const found = LEARN_MODES.find((m) => m.value === mode);
  return found ? found.label : "Guided practice";
}

/**
 * Consumer state chip: maps ONLY real Core states (+ honest local truth
 * like stale/unavailable/conflict) to human wording. Never exposes
 * internal enum names.
 */
export function learnStateChip({ session, stale, selectedSong, startBlocked }) {
  if (stale) return { label: "Reconnecting", tone: "wait" };
  if (session && session.available !== true) {
    return { label: "Unavailable", tone: "bad" };
  }
  switch (session ? session.state : "IDLE") {
    case "WAITING":
      return { label: "Your turn", tone: "active" };
    case "COMPLETED":
      return { label: "Complete", tone: "ok" };
    case "FAILED":
      return { label: "Stopped", tone: "bad" };
    case "IDLE":
    default:
      if (startBlocked) return { label: "Piano in use", tone: "wait" };
      return selectedSong
        ? { label: "Ready", tone: "ready" }
        : { label: "No song", tone: "idle" };
  }
}

/**
 * Authoritative target presentation: the Core publishes `accepted_notes`
 * for the CURRENT target, so chips may distinguish accepted vs remaining
 * without any browser-side correctness decision. No target → no chips.
 */
export function learnTargetChips(target, acceptedNotes) {
  if (!target || !Array.isArray(target.names) || target.names.length === 0) {
    return [];
  }
  const accepted = new Set(
    Array.isArray(acceptedNotes) ? acceptedNotes.map(Number) : []
  );
  return target.names.map((name, index) => ({
    name,
    midi: Array.isArray(target.notes) ? target.notes[index] : null,
    accepted: accepted.has(target.notes?.[index]),
  }));
}

/**
 * Progress model: derived ONLY from authoritative counters. When
 * total_steps is unavailable the percentage is omitted, never faked.
 */
export function learnProgress(session) {
  const total = Number(session && session.total_steps);
  if (!Number.isFinite(total) || total <= 0) {
    return { text: "", pct: null, barAllowed: false };
  }
  const step = Number(session.step_index);
  const completed = Number(session.completed_steps);
  const isWaiting = session && session.state === "WAITING";
  const shown = isWaiting && Number.isFinite(step)
    ? Math.min(total, Math.max(0, step + 1))
    : Math.min(total, Math.max(0, Number.isFinite(completed) ? completed : 0));
  const pct = Math.round(
    (Math.min(1, Math.max(0, Number(session.progress) || 0)) * 100)
  );
  return {
    text: isWaiting ? `Step ${shown} of ${total}` : `${shown} of ${total}`,
    pct,
    barAllowed: true,
  };
}

/**
 * Feedback model from the AUTHORITATIVE latest_outcome only. The browser
 * never grades input; "INCORRECT" is Core's own verdict.
 */
export function learnFeedback(session) {
  if (session?.mode === "FLOW") {
    const labels = { CORRECT: "Correct attack", EARLY: "Early", LATE: "Late", MISSED: "Missed event", INCORRECT: "Wrong note", INPUT_UNAVAILABLE: "Connect your piano, then resume" };
    return { text: labels[session.latest_outcome] || "", tone: session.latest_outcome === "CORRECT" ? "ok" : "warn" };
  }
  switch (session && session.latest_outcome) {
    case "CORRECT":
      return { text: "That's the one — Core is moving you forward.", tone: "ok" };
    case "INCORRECT":
      return {
        text: "Not this one. The target stays put — try the highlighted key(s).",
        tone: "warn",
      };
    case "INPUT_UNAVAILABLE":
      return { text: "Waiting for your piano input…", tone: "wait" };
    default:
      return { text: "", tone: "" };
  }
}

/** Repetition line for REPEAT_UNTIL_CORRECT from authoritative counters. */
export function learnRepetition(session) {
  if (!session || session.mode !== "REPEAT_UNTIL_CORRECT") return "";
  const r = session.repetition || {};
  return `In a row ${r.current ?? 0} of ${r.required ?? 1} · clean ${r.successful ?? 0}`;
}

/**
 * One consumer guidance line for the whole lesson — what should I do now?
 * Uses only real state; never enum names, never device internals.
 */
export function learnGuidance({ session, stale, selectedSong, startBlocked }) {
  if (!stale && session?.mode === "FLOW") {
    if (session.state === "COMPLETED") return "Flow complete. Review your events and timing below.";
    if (session.state === "WAITING") return session.flow?.paused ? "Flow is paused. Resume when you are ready." : "Play as the notes meet the keyboard. The clock keeps moving.";
  }
  if (stale) return "Reconnecting to Piano Core — your progress is safe.";
  if (session && session.available !== true) {
    return "Learning is unavailable right now. It needs the song library.";
  }
  switch (session ? session.state : "IDLE") {
    case "WAITING":
      return "Play the highlighted key(s) on your piano.";
    case "COMPLETED":
      return "You played the whole piece. Start again anytime or pick a new song.";
    case "FAILED":
      return "Practice stopped with a problem. You can start again.";
    case "IDLE":
    default:
      if (startBlocked) {
        return "Playback is using the piano right now — stop it to practice.";
      }
      return selectedSong
        ? "Ready when you are. Start practice, then play the highlighted keys."
        : "Pick a song from your library to begin.";
  }
}

/**
 * Controls enablement for the Learn surface. Reuses the accepted
 * PracticeControl rules: START needs an available subsystem, a selected
 * song, no running session and no active playback (mutex); STOP and the
 * guarded restarts exist only while WAITING.
 */
export function learnControls({ session, selectedSong, playbackSnapshot }) {
  const available = !!session && session.available === true;
  const waiting = !!session && session.state === "WAITING";
  const playbackActive =
    !!playbackSnapshot &&
    playbackSnapshot.available === true &&
    ["PLAYING", "PAUSED", "STOPPING"].includes(playbackSnapshot.state);
  return {
    start: available && !waiting && !!selectedSong && !playbackActive,
    stop: waiting,
    restartTarget: waiting && session?.mode !== "FLOW",
    restartSection: waiting && session?.mode !== "FLOW",
    startBlockedByPlayback: available && !waiting && !!selectedSong && playbackActive,
  };
}

/**
 * The full premium lesson view model (pure). Everything the Learn surface
 * renders derives from this one object, which itself derives only from
 * authoritative snapshots and honest local connection truth.
 */
export function learnLessonModel({
  session = null,
  stale = false,
  selectedSong = null,
  playbackSnapshot = null,
  led = null,
} = {}) {
  const controls = learnControls({ session, selectedSong, playbackSnapshot });
  const chip = learnStateChip({ session, stale, selectedSong, startBlocked: controls.startBlockedByPlayback });
  const waiting = !!session && session.state === "WAITING" && !stale;
  const target = waiting ? session.target : null;
  const chips = learnTargetChips(target, waiting ? session.accepted_notes : []);
  const progress = learnProgress(stale ? null : session);
  const feedback = learnFeedback(stale ? null : session);
  const completedText =
    !stale && session && session.state === "COMPLETED" && Number(session.total_steps) > 0
      ? session.mode === "FLOW" ? `Flow complete · ${session.flow?.correct_events ?? 0} of ${session.total_steps} events correct.` : `All ${session.total_steps} steps played.`
      : "";
  const notes = [];
  if (waiting && session.midi_connection !== "CONNECTED") {
    notes.push("Your piano input is not connected, so practice cannot progress.");
  }
  if (
    waiting &&
    led &&
    led.configured === true &&
    led.available !== true
  ) {
    notes.push("Keyboard lights are unavailable — follow the note names above.");
  }
  return {
    stage: stale
      ? "stale"
      : session && session.available !== true
        ? "unavailable"
        : !session || session.state === "IDLE"
          ? (controls.startBlockedByPlayback
              ? "conflict"
              : selectedSong
                ? "ready"
                : "no-song")
          : session.state === "WAITING"
            ? "waiting"
            : session.state === "COMPLETED"
              ? "completed"
              : session.state === "FAILED"
                ? "failed"
                : "no-song",
    chip,
    guidance: learnGuidance({ session, stale, selectedSong, startBlocked: controls.startBlockedByPlayback }),
    title: (session && (session.title || session.song_id)) || (selectedSong
      ? selectedSong.title || selectedSong.filename || selectedSong.song_id
      : ""),
    modeLabel: learnModeLabel(session && session.mode),
    sectionLabel:
      session && session.section
        ? `${session.section.start_seconds.toFixed(0)}s–${session.section.end_seconds.toFixed(0)}s`
        : "Full song",
    repetitionText: learnRepetition(session),
    targetText: waiting
      ? (session.target && Array.isArray(session.target.names)
          ? session.target.names.join(" + ")
          : "")
      : "",
    chips,
    chord: !!(target && target.chord),
    targetId: waiting && session.target ? session.target.target_id : null,
    progress,
    feedback,
    completedText,
    errorText:
      !stale && session && session.state === "FAILED"
        ? session.error || "Practice stopped with a problem."
        : "",
    notes,
    controls,
    ledGuidance:
      waiting &&
      led &&
      led.configured === true &&
      led.available === true &&
      ["physical", "all"].includes(led.mode)
        ? "Keyboard lights are guiding you."
        : "",
  };
}
