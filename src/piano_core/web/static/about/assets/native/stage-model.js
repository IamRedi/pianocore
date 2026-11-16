/* Pure display geometry copied from the app; see tools/sync_presentation_assets.py. */
const NOTE_NAMES = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"];

/** MIDI note number -> name (e.g. 60 -> "C4"); out of range -> "#n". */
export function noteName(note) {
  const n = Number(note);
  if (!Number.isInteger(n) || n < 0 || n > 127) return `#${note}`;
  return `${NOTE_NAMES[n % 12]}${Math.floor(n / 12) - 1}`;
}

/* --------------------------------------------------------------------
 * Live 88-key piano (PIANO-UI-003; pure model/reducer + DOM adapter)
 * -------------------------------------------------------------------- */

export const PIANO_NOTE_MIN = 21;
export const PIANO_NOTE_MAX = 108;
export const PIANO_KEY_COUNT = 88;
export const PIANO_WHITE_KEY_COUNT = 52;
export const LIVE_NOTE_SCHEMA_VERSION = 1;
export const LIVE_NOTE_RESNAPSHOT_MS = 2000;
export const LIVE_NOTE_FRESHNESS_MS = 5000;
const BLACK_PITCH_CLASSES = new Set([1, 3, 6, 8, 10]);

/** Build the one canonical A0..C8 display model. */
export function buildPianoKeyModel() {
  const keys = [];
  let whiteIndex = 0;
  for (let midi = PIANO_NOTE_MIN; midi <= PIANO_NOTE_MAX; midi += 1) {
    const pitchClassIndex = midi % 12;
    const black = BLACK_PITCH_CLASSES.has(pitchClassIndex);
    const key = {
      id: `midi-${midi}`,
      midi,
      pitchClass: NOTE_NAMES[pitchClassIndex],
      octave: Math.floor(midi / 12) - 1,
      label: noteName(midi),
      black,
      whiteIndex: black ? null : whiteIndex,
      // Black keys are centered on the boundary after the preceding white.
      leftPercent: black ? (whiteIndex / PIANO_WHITE_KEY_COUNT) * 100 : null,
    };
    keys.push(Object.freeze(key));
    if (!black) whiteIndex += 1;
  }
  if (keys.length !== PIANO_KEY_COUNT || whiteIndex !== PIANO_WHITE_KEY_COUNT) {
    throw new Error("invalid canonical piano geometry");
  }
  return Object.freeze(keys);
}

export const PIANO_KEYS = buildPianoKeyModel();

export const VISUAL_MODES = Object.freeze(["sheet", "falling", "hybrid"]);
export const VISUAL_MODE_STORAGE_KEY = "piano-core.visual-mode";
export const FALLING_MIN_NOTE_HEIGHT_PX = 3;
export const FALLING_PX_PER_SECOND_MIN = 40;
export const FALLING_PX_PER_SECOND_MAX = 180;
export const FALLING_HORIZON_SECONDS_TARGET = 5;
export const SHEET_POSITION_ANCHOR_FRACTION = 0.35;
export const VISUAL_RESOLUTION_SCALES = Object.freeze([1, 0.75, 0.5]);

/**
 * The canonical 21..108 geometry fractions derived from the ONE accepted
 * UI-003 key model (buildPianoKeyModel). Black keys sit on the white-key
 * boundary exactly like PianoKeyboardRenderer's leftPercent; this is the
 * same geometry, never a second model.
 */
const NOTE_GEOMETRY_FRACTIONS = (() => {
  const centers = new Map();
  const widths = new Map();
  for (const key of PIANO_KEYS) {
    if (key.black) {
      // Black keys carry their boundary only via leftPercent (their
      // whiteIndex is null in the accepted model).
      centers.set(key.midi, key.leftPercent / 100);
      widths.set(key.midi, (1 / PIANO_WHITE_KEY_COUNT) * 0.62);
    } else {
      centers.set(key.midi, (key.whiteIndex + 0.5) / PIANO_WHITE_KEY_COUNT);
      widths.set(key.midi, (1 / PIANO_WHITE_KEY_COUNT) * 0.92);
    }
  }
  return { centers, widths };
})();

/** Horizontal center of one key as a [0..1] fraction of the 88-key span. */
export function noteCenterFraction(midi) {
  return NOTE_GEOMETRY_FRACTIONS.centers.has(midi)
    ? NOTE_GEOMETRY_FRACTIONS.centers.get(midi)
    : null;
}

/** Visual note width as a fraction of the 88-key span (black keys narrower). */
export function noteWidthFraction(midi) {
  return NOTE_GEOMETRY_FRACTIONS.widths.has(midi)
    ? NOTE_GEOMETRY_FRACTIONS.widths.get(midi)
    : null;
}

/** True when the MIDI pitch is a black key (diatonic geometry only). */
export function isVisualBlackKey(midi) {
  return BLACK_PITCH_CLASSES.has(((midi % 12) + 12) % 12);
}

export function isOnsetInHalfOpenSection(onsetSeconds, clipped) {
  if (!clipped) return true;
  return onsetSeconds >= clipped.start && onsetSeconds < clipped.end;
}

export function fallingPixelsPerSecond(heightPx) {
  const height = Number(heightPx);
  if (!Number.isFinite(height) || height <= 0) {
    return FALLING_PX_PER_SECOND_MIN;
  }
  const derived = height / FALLING_HORIZON_SECONDS_TARGET;
  return Math.min(
    FALLING_PX_PER_SECOND_MAX,
    Math.max(FALLING_PX_PER_SECOND_MIN, derived)
  );
}

export function fallingTimeWindow({ positionSeconds, heightPx, pxPerSecond }) {
  const position = Number(positionSeconds);
  const height = Number(heightPx);
  const rate = Number(pxPerSecond);
  if (
    !Number.isFinite(position) ||
    !Number.isFinite(height) ||
    !Number.isFinite(rate) ||
    height <= 0 ||
    rate <= 0
  ) {
    return null;
  }
  const horizon = height / rate;
  return { pastSeconds: position, futureSeconds: position + horizon };
}

export function fallingNoteY(onsetSeconds, positionSeconds, strikeY, pxPerSecond) {
  return strikeY - (onsetSeconds - positionSeconds) * pxPerSecond;
}

export function notePlayState(onsetSeconds, durationSeconds, positionSeconds) {
  if (positionSeconds >= onsetSeconds + durationSeconds) return "past";
  if (positionSeconds >= onsetSeconds) return "active";
  return "upcoming";
}

export function visibleNoteSlice(notes, fromSeconds, toSeconds, maxDurationSeconds) {
  if (!Array.isArray(notes) || notes.length === 0) return { start: 0, end: 0 };
  const from = Number(fromSeconds);
  const to = Number(toSeconds);
  if (!Number.isFinite(from) || !Number.isFinite(to) || to <= from) {
    return { start: 0, end: 0 };
  }
  const maxDuration = Math.max(0, Number(maxDurationSeconds) || 0);
  let lo = 0;
  let hi = notes.length;
  const threshold = from - maxDuration;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (notes[mid].onset_seconds < threshold) lo = mid + 1;
    else hi = mid;
  }
  const start = lo;
  lo = start;
  hi = notes.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (notes[mid].onset_seconds <= to) lo = mid + 1;
    else hi = mid;
  }
  return { start, end: lo };
}

export function visualCanvasSize(clientWidth, clientHeight, dpr, resolutionScale) {
  const scale = VISUAL_RESOLUTION_SCALES.includes(resolutionScale)
    ? resolutionScale
    : 1;
  const ratio = Math.min(3, Math.max(1, Number(dpr) || 1));
  const width = Math.max(1, Math.floor((Number(clientWidth) || 0) * ratio * scale));
  const height = Math.max(1, Math.floor((Number(clientHeight) || 0) * ratio * scale));
  return { width, height, ratio: ratio * scale };
}

