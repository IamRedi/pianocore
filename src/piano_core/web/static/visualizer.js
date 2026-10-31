/**
 * Piano Core — Visual Learning System renderers (PIANO-UI-005).
 *
 * Presentation ONLY. These canvas renderers consume:
 *   - one canonical score payload (Core-derived note spans; never raw MIDI),
 *   - one presentation position (authoritative Core snapshot + the accepted
 *     UI-002 bounded interpolation — computed in app.js, injected here),
 *   - the accepted UI-003 88-key geometry (pure mappers injected from
 *     app.js — there is no second keyboard geometry model in this file),
 *   - Core Learning snapshots for expected-target/section overlays.
 *
 * No renderer parses MIDI, derives musical truth, grades input, advances
 * learning or schedules output. Animation interpolates presentation only
 * and freezes when the injected frame says Core truth is stale.
 *
 * This module has no app.js import (app.js imports it): everything pure
 * arrives through the injected `mappers` object, which keeps a single
 * module graph direction and makes the geometry replaceable in tests.
 */

export const FALLING_KEYBOARD_MIN_PX = 44;
export const FALLING_KEYBOARD_MAX_PX = 96;
const STALE_MESSAGE = "Core state stale — visual frozen";

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function currentDpr() {
  const dpr = globalThis.devicePixelRatio;
  return typeof dpr === "number" && Number.isFinite(dpr) && dpr > 0 ? dpr : 1;
}

/** One canvas + its sizing/HiDPI/quality surface (resize-stable). */
class CanvasPane {
  constructor(canvas, quality, sizeMapper) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.quality = quality;
    this.sizeMapper = sizeMapper;
    this.cssWidth = 0;
    this.cssHeight = 0;
    this.backing = { width: 1, height: 1, ratio: 1 };
    this.dirty = true;
  }

  /** Reconcile backing size with the CSS box; records whether it changed. */
  sync() {
    const rect = this.canvas.getBoundingClientRect();
    const cssWidth = Math.max(0, Math.floor(rect.width));
    const cssHeight = Math.max(0, Math.floor(rect.height));
    const backing = this.sizeMapper(
      cssWidth,
      cssHeight,
      currentDpr(),
      this.quality.resolutionScale
    );
    const changed =
      this.dirty ||
      cssWidth !== this.cssWidth ||
      cssHeight !== this.cssHeight ||
      backing.width !== this.backing.width ||
      backing.height !== this.backing.height;
    this.cssWidth = cssWidth;
    this.cssHeight = cssHeight;
    this.backing = backing;
    this.dirty = false;
    if (changed) {
      this.canvas.width = backing.width;
      this.canvas.height = backing.height;
    }
    this.sizeChanged = changed;
    return { width: cssWidth, height: cssHeight };
  }

  beginFrame() {
    const { ratio } = this.backing;
    this.ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    this.ctx.clearRect(0, 0, this.cssWidth, this.cssHeight);
    return this.ctx;
  }
}

/**
 * Falling-notes renderer: vertical time axis, strike line at the top edge
 * of the drawn 88-key strip, exact shared key geometry.
 */
export class FallingNotesRenderer {
  constructor(canvas, mappers, quality) {
    this.mappers = mappers;
    this.quality = quality;
    this.pane = new CanvasPane(canvas, quality, mappers.visualCanvasSize);
  }

  render(frame) {
    const pane = this.pane;
    pane.sync();
    const ctx = pane.beginFrame();
    const width = pane.cssWidth;
    const height = pane.cssHeight;
    if (width <= 1 || height <= 1) return 0;

    const keyboardHeight = clamp(
      height * 0.18,
      FALLING_KEYBOARD_MIN_PX,
      FALLING_KEYBOARD_MAX_PX
    );
    const strikeY = Math.max(1, height - keyboardHeight);
    const range = frame.range || { left: 0, right: 1 };
    const fullWidth = width / (range.right - range.left);
    ctx.fillStyle = "#0d0e17"; ctx.fillRect(0, 0, width, strikeY);
    for (const key of this.mappers.PIANO_KEYS) {
      if (!key.black) continue;
      const center = this.mappers.noteCenterFraction(key.midi);
      const w = this.mappers.noteWidthFraction(key.midi) * fullWidth;
      ctx.fillStyle = "#090a11";
      ctx.fillRect((center - range.left) * fullWidth - w / 2, 0, w, strikeY);
    }
    this._drawKeyboard(ctx, width, strikeY, height, frame);

    const position = frame.position;
    const pxPerSecond = this.mappers.fallingPixelsPerSecond(strikeY);
    const window = this.mappers.fallingTimeWindow({
      positionSeconds: position,
      heightPx: strikeY,
      pxPerSecond,
    });
    let visible = 0;

    ctx.save();
    ctx.beginPath();
    ctx.rect(0, 0, width, strikeY);
    ctx.clip();

    if (window) {
      this._drawSectionBand(ctx, width, position, pxPerSecond, strikeY, frame);
      const slice = this.mappers.visibleNoteSlice(
        frame.keyboardNotes,
        window.pastSeconds - 0.25,
        window.futureSeconds,
        frame.maxSpanDuration
      );
      const minH = this.mappers.FALLING_MIN_NOTE_HEIGHT_PX;
      for (let i = slice.start; i < slice.end; i += 1) {
        const note = frame.keyboardNotes[i];
        visible += this._drawNote(ctx, note, width, position, pxPerSecond, minH, frame, strikeY)
          ? 1
          : 0;
      }
      this._drawTargetMarker(ctx, width, position, pxPerSecond, strikeY, frame);
    }
    ctx.restore();

    // Strike line on top of the (clipped) stream.
    ctx.fillStyle = "rgba(230, 240, 255, 0.9)";
    ctx.fillRect(0, strikeY - 1, width, 2);
    return visible;
  }

  _drawNote(ctx, note, width, position, pxPerSecond, minH, frame, strikeY) {
    const center = this.mappers.noteCenterFraction(note.midi);
    const widthFraction = this.mappers.noteWidthFraction(note.midi);
    if (center === null || widthFraction === null) return false;
    const range = frame.range || { left: 0, right: 1 };
    const scale = range.right - range.left;
    const w = widthFraction * width / scale;
    const x = (center - range.left) * width / scale - w / 2;
    const duration = note.duration_seconds;
    const top = this.mappers.fallingNoteY(
      note.onset_seconds + duration,
      position,
      strikeY,
      pxPerSecond
    );
    const h = Math.max(minH, duration * pxPerSecond);
    if (top > strikeY || top + h < 0) return false;
    const state = this.mappers.notePlayState(note.onset_seconds, duration, position);
    const black = this.mappers.isVisualBlackKey(note.midi);
    const isTarget =
      frame.learning.active &&
      frame.learning.targetOnset === note.onset_seconds &&
      frame.learning.targetNotes.includes(note.midi);
    const inSection = this.mappers.isOnsetInHalfOpenSection(
      note.onset_seconds,
      frame.clippedSection
    );
    let fill;
    const advice = frame.fingeringByIndex?.get(note.index);
    const left = advice?.hand === "LEFT";
    if (state === "active") fill = left ? "#b8e6ff" : "#cdc4ff";
    else fill = advice ? (left ? "#5ab8ee" : "#8b7bff") : "#a4a8c8";
    ctx.globalAlpha = !inSection ? 0.3 : state === "upcoming" ? 0.55 + note.velocity / 320 : 1;
    ctx.fillStyle = fill;
    if (ctx.roundRect) { ctx.beginPath(); ctx.roundRect(x + 0.5, top, Math.max(1, w - 1), h, Math.min(4, w / 3, h / 2)); ctx.fill(); }
    else ctx.fillRect(x, top, w, h);
    const fingering = frame.fingeringByIndex?.get(note.index);
    if (fingering && w >= 10 && h >= 11) {
      ctx.globalAlpha = 1;
      ctx.fillStyle = "#0d0e17";
      ctx.font = "bold 9px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(`${fingering.hand === "LEFT" ? "L" : "R"}${fingering.finger}`, x + w / 2, top + 10);
      ctx.textAlign = "left";
    }
    if (isTarget) {
      ctx.globalAlpha = 1;
      ctx.lineWidth = 2;
      ctx.strokeStyle = "#ffd27a";
      ctx.strokeRect(x - 1, top - 1, w + 2, h + 2);
    }
    ctx.globalAlpha = 1;
    return true;
  }

  _drawSectionBand(ctx, width, position, pxPerSecond, strikeY, frame) {
    const section = frame.clippedSection;
    if (!section) return;
    const yStart = this.mappers.fallingNoteY(section.start, position, strikeY, pxPerSecond);
    const yEnd = this.mappers.fallingNoteY(section.end, position, strikeY, pxPerSecond);
    const top = clamp(Math.min(yStart, yEnd), 0, strikeY);
    const bottom = clamp(Math.max(yStart, yEnd), 0, strikeY);
    ctx.fillStyle = "rgba(255, 200, 87, 0.10)";
    if (bottom > top) ctx.fillRect(0, top, width, bottom - top);
    ctx.strokeStyle = "rgba(255, 200, 87, 0.8)";
    ctx.setLineDash([6, 4]);
    ctx.lineWidth = 1;
    for (const y of [yStart, yEnd]) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
    }
    ctx.setLineDash([]);
    // Outside-section dimming (subtle, decorative-free).
    ctx.fillStyle = "rgba(6, 10, 20, 0.35)";
    if (top > 0) ctx.fillRect(0, 0, width, top);
    if (bottom < strikeY) ctx.fillRect(0, bottom, width, strikeY - bottom);
  }

  _drawTargetMarker(ctx, width, position, pxPerSecond, strikeY, frame) {
    const learning = frame.learning;
    if (!learning.active || learning.targetOnset === null) return;
    const y = this.mappers.fallingNoteY(learning.targetOnset, position, strikeY, pxPerSecond);
    if (y < 0 || y > strikeY) return;
    ctx.strokeStyle = "rgba(255, 200, 87, 0.9)";
    ctx.setLineDash([2, 6]);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  _drawKeyboard(ctx, width, top, height, frame) {
    const m = this.mappers;
    const range = frame.range || { left: 0, right: 1 };
    width /= range.right - range.left;
    ctx.save();
    ctx.translate(-range.left * width, 0);
    const keyboardHeight = height - top;
    const whiteWidth = width / m.PIANO_WHITE_KEY_COUNT;
    // White keys first, then black keys on top (same layering as the DOM
    // renderer; geometry fractions are the same accepted model).
    for (const key of m.PIANO_KEYS) {
      if (key.black) continue;
      const source = frame.keyboardSources.get(key.midi) || "idle";
      ctx.fillStyle = this._keyFill(source, false, frame, key.midi);
      ctx.fillRect(key.whiteIndex * whiteWidth + 0.5, top, whiteWidth - 1, keyboardHeight - 2);
    }
    const blackWidth = whiteWidth * 0.62;
    for (const key of m.PIANO_KEYS) {
      if (!key.black) continue;
      const source = frame.keyboardSources.get(key.midi) || "idle";
      // Black keys carry their boundary via leftPercent (whiteIndex is
      // null for black keys in the accepted model).
      const x = (key.leftPercent / 100) * width - blackWidth / 2;
      ctx.fillStyle = this._keyFill(source, true, frame, key.midi);
      ctx.fillRect(x, top, blackWidth, keyboardHeight * 0.62);
    }
    // Octave labels on C keys when the strip is tall enough.
    if (keyboardHeight >= 56) {
      ctx.fillStyle = "rgba(160, 175, 205, 0.9)";
      ctx.font = "10px system-ui, sans-serif";
      ctx.textAlign = "center";
      for (const key of m.PIANO_KEYS) {
        if (key.black || key.pitchClass !== "C") continue;
        const x = (key.whiteIndex + 0.5) * whiteWidth;
        if (x > 8 && x < width - 8) {
          ctx.fillText(key.label, x, height - 6);
        }
      }
    }
    // Expected-target outline is drawn last so it is never covered.
    if (frame.learning.active && frame.learning.targetNotes.length > 0) {
      ctx.strokeStyle = "#ffd27a";
      ctx.lineWidth = 2;
      for (const midi of frame.learning.targetNotes) {
        const key = m.PIANO_KEYS[midi - m.PIANO_NOTE_MIN];
        if (!key) continue;
        if (key.black) {
          const x = (key.leftPercent / 100) * width - blackWidth / 2;
          ctx.strokeRect(x - 1, top - 1, blackWidth + 2, keyboardHeight * 0.62 + 2);
        } else {
          ctx.strokeRect(
            key.whiteIndex * whiteWidth - 0.5,
            top - 1,
            whiteWidth + 1,
            keyboardHeight - 1
          );
        }
      }
    }
    ctx.restore();
  }

  _keyFill(source, black, frame, midi) {
    if (source === "physical" && frame.learningFeedback?.outcome === "INCORRECT" && frame.learningFeedback.note === midi) return black ? "#b0525e" : "#ff9fa8";
    if (source === "physical" && frame.learningFeedback?.accepted?.includes(midi)) return black ? "#3e9a7a" : "#9fe8c8";
    if (source === "physical") return black ? "#2f8fc4" : "#a6dcff";
    if (source === "playback") return black ? "#6a5cf0" : "#c4bbff";
    if (source === "both") return black ? "#5a74e0" : "#d6e2ff";
    return black ? "#15161f" : "#eceef4";
  }
}

/**
 * Sheet/notation-oriented renderer — the honest notation available from
 * canonical metadata: pitch rows (highest on top), a song-SECONDS ruler
 * (measures/beats do not exist canonically and are never drawn), note
 * spans, playhead, Learning target highlight and section band.
 */
export class SheetRenderer {
  constructor(canvas, mappers, quality) {
    this.mappers = mappers;
    this.quality = quality;
    this.pane = new CanvasPane(canvas, quality, mappers.visualCanvasSize);
    this.rulerHeight = 22;
  }

  render(frame) {
    const pane = this.pane;
    pane.sync();
    const ctx = pane.beginFrame();
    const width = pane.cssWidth;
    const height = pane.cssHeight;
    if (width <= 1 || height <= 1) return 0;
    const drawableTop = this.rulerHeight;
    const drawableHeight = Math.max(1, height - drawableTop - 4);
    const duration = frame.score ? frame.score.duration_seconds : 0;
    const viewportSeconds = this.mappers.sheetViewportSeconds(duration);
    const viewStart = this.mappers.sheetViewStart(frame.position, viewportSeconds, duration);
    const viewEnd = viewStart + viewportSeconds;
    const pitchRange = this.mappers.sheetPitchRange(frame.keyboardNotes);
    let visible = 0;

    this._drawRuler(ctx, width, viewStart, viewEnd, frame);
    if (pitchRange) {
      this._drawGrid(ctx, width, drawableTop, drawableHeight, pitchRange);
      this._drawSectionBand(ctx, width, drawableTop, drawableHeight, viewStart, viewportSeconds, frame);
      const slice = this.mappers.visibleNoteSlice(
        frame.keyboardNotes,
        viewStart,
        viewEnd,
        frame.maxSpanDuration
      );
      const pxPerSecond = width / viewportSeconds;
      for (let i = slice.start; i < slice.end; i += 1) {
        const note = frame.keyboardNotes[i];
        visible += this._drawNote(
          ctx, note, width, drawableTop, drawableHeight, pitchRange,
          viewStart, viewportSeconds, pxPerSecond, frame
        ) ? 1 : 0;
      }
    }
    this._drawPlayhead(ctx, width, drawableTop, drawableHeight, viewStart, viewportSeconds, frame);
    this._drawTargetMarker(ctx, drawableTop, viewStart, viewportSeconds, frame, width);
    return visible;
  }

  _drawRuler(ctx, width, viewStart, viewEnd, frame) {
    ctx.fillStyle = "rgba(10, 14, 24, 0.55)";
    ctx.fillRect(0, 0, width, this.rulerHeight);
    const step = this.mappers.sheetRulerStepSeconds(viewEnd - viewStart);
    ctx.fillStyle = "rgba(170, 185, 215, 0.95)";
    ctx.font = "10px system-ui, sans-serif";
    ctx.textAlign = "left";
    const firstTick = Math.ceil(viewStart / step) * step;
    for (let t = firstTick; t <= viewEnd; t += step) {
      const x = ((t - viewStart) / (viewEnd - viewStart)) * width;
      if (x < 0 || x > width - 2) continue;
      ctx.fillRect(x, 14, 1, 8);
      ctx.fillText(this.mappers.formatDuration(t), x + 3, 12);
    }
    ctx.textAlign = "right";
    ctx.fillText("song time", width - 4, 12);
    ctx.textAlign = "left";
  }

  _drawGrid(ctx, width, top, height, pitchRange) {
    const rows = pitchRange.max - pitchRange.min + 1;
    const rowHeight = height / rows;
    for (let midi = pitchRange.min; midi <= pitchRange.max; midi += 1) {
      const y = this.mappers.sheetMidiToY(midi, pitchRange, top, height);
      const pc = ((midi % 12) + 12) % 12;
      if (pc === 0) {
        ctx.fillStyle = "rgba(140, 160, 200, 0.22)";
        ctx.fillRect(0, y - rowHeight / 2, width, 1);
        if (rowHeight >= 5) {
          ctx.fillStyle = "rgba(150, 165, 195, 0.8)";
          ctx.font = "9px system-ui, sans-serif";
          ctx.textAlign = "left";
          ctx.fillText(this.mappers.noteName(midi), 2, y + 3);
        }
      } else if (!this.mappers.isVisualBlackKey(midi) && rowHeight >= 4) {
        ctx.fillStyle = "rgba(120, 135, 170, 0.07)";
        ctx.fillRect(0, y - rowHeight / 2, width, 1);
      }
    }
  }

  _drawNote(ctx, note, width, top, height, pitchRange, viewStart, viewportSeconds, pxPerSecond, frame) {
    const x = this.mappers.sheetTimeToX(note.onset_seconds, viewStart, viewportSeconds, width);
    if (x === null) return false;
    const w = Math.max(2, note.duration_seconds * pxPerSecond);
    if (x + w < 0 || x > width) return false;
    const y = this.mappers.sheetMidiToY(note.midi, pitchRange, top, height);
    if (y === null) return false;
    const rows = pitchRange.max - pitchRange.min + 1;
    const h = Math.max(2, height / rows - 1);
    const state = this.mappers.notePlayState(note.onset_seconds, note.duration_seconds, frame.position);
    const black = this.mappers.isVisualBlackKey(note.midi);
    const isTarget =
      frame.learning.active &&
      frame.learning.targetOnset === note.onset_seconds &&
      frame.learning.targetNotes.includes(note.midi);
    const inSection = this.mappers.isOnsetInHalfOpenSection(note.onset_seconds, frame.clippedSection);
    ctx.fillStyle = state === "active" ? (black ? "#9db8ff" : "#f0f5ff") : black ? "#5472bd" : "#a8bde8";
    ctx.globalAlpha = !inSection ? 0.3 : state === "past" ? 0.45 : 0.6 + note.velocity / 340;
    ctx.fillRect(x, y - h / 2, w, h);
    ctx.globalAlpha = 1;
    const fingering = frame.fingeringByIndex?.get(note.index);
    if (fingering && w >= 12 && h >= 8) {
      ctx.fillStyle = "#0d0e17";
      ctx.font = "bold 8px system-ui, sans-serif";
      ctx.textAlign = "left";
      ctx.fillText(`${fingering.hand === "LEFT" ? "L" : "R"}${fingering.finger}`, x + 2, y + 3);
    }
    if (isTarget) {
      ctx.lineWidth = 2;
      ctx.strokeStyle = "#ffd27a";
      ctx.strokeRect(x - 1, y - h / 2 - 1, w + 2, h + 2);
    }
    return true;
  }

  _drawSectionBand(ctx, width, top, height, viewStart, viewportSeconds, frame) {
    const section = frame.clippedSection;
    if (!section) return;
    const xStart = ((section.start - viewStart) / viewportSeconds) * width;
    const xEnd = ((section.end - viewStart) / viewportSeconds) * width;
    ctx.fillStyle = "rgba(255, 200, 87, 0.08)";
    ctx.fillRect(xStart, top, Math.max(1, xEnd - xStart), height);
    ctx.strokeStyle = "rgba(255, 200, 87, 0.8)";
    ctx.setLineDash([6, 4]);
    ctx.lineWidth = 1;
    for (const x of [xStart, xEnd]) {
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, top + height);
      ctx.stroke();
    }
    ctx.setLineDash([]);
  }

  _drawPlayhead(ctx, width, top, height, viewStart, viewportSeconds, frame) {
    const x = ((frame.position - viewStart) / viewportSeconds) * width;
    if (x < 0 || x > width) return;
    ctx.fillStyle = frame.status === "stale" ? "#8fa3c8" : "#7fd1a8";
    ctx.fillRect(x - 1, top, 2, height);
  }

  _drawTargetMarker(ctx, top, viewStart, viewportSeconds, frame, width) {
    const learning = frame.learning;
    if (!learning.active || learning.targetOnset === null) return;
    const x = ((learning.targetOnset - viewStart) / viewportSeconds) * width;
    if (x < 0 || x > width) return;
    ctx.fillStyle = "#ffd27a";
    ctx.beginPath();
    ctx.moveTo(x - 5, top - 0);
    ctx.lineTo(x + 5, top - 0);
    ctx.lineTo(x, top + 7);
    ctx.closePath();
    ctx.fill();
  }
}

/**
 * VisualStage — mode routing, status surface and one-frame accounting.
 * app.js owns the rAF cadence and computes every frame's data; the stage
 * only draws and updates the DOM status surface (dataset + aria text).
 */
export class VisualStage {
  constructor({
    root,
    summaryEl,
    statusEl,
    panes,
    canvases,
    mode,
    mappers,
    quality = { resolutionScale: 1, effects: true },
  }) {
    if (!root || !summaryEl || !statusEl || !panes || !canvases) {
      throw new TypeError("visual stage root, summary, status, panes and canvases are required");
    }
    this.root = root;
    this.summaryEl = summaryEl;
    this.statusEl = statusEl;
    this.mode = mode;
    this.mappers = mappers;
    this.quality = quality;
    this.sheet = new SheetRenderer(canvases.sheet, mappers, quality);
    this.falling = new FallingNotesRenderer(canvases.falling, mappers, quality);
    this.panes = panes;
    this.lastFrameMs = 0;
    this.lastVisible = 0;
    this._lastSummary = null;
    this._resizeObserver = null;
    this._applyMode();
    if (typeof ResizeObserver === "function") {
      this._resizeObserver = new ResizeObserver(() => {
        this.root.dataset.resized = String(Date.now());
      });
      for (const pane of [panes.sheet, panes.falling]) {
        if (pane) this._resizeObserver.observe(pane);
      }
    }
  }

  /** Visual state only: pane visibility changes, no data is reset. */
  setMode(mode) {
    if (this.mode === mode) return false;
    this.mode = mode;
    this._applyMode();
    return true;
  }

  _applyMode() {
    const showSheet = this.mode === "sheet" || this.mode === "hybrid";
    const showFalling = this.mode === "falling" || this.mode === "hybrid";
    if (this.panes.sheet) this.panes.sheet.hidden = !showSheet;
    if (this.panes.falling) this.panes.falling.hidden = !showFalling;
    this.root.dataset.mode = this.mode;
  }

  /** Draw one coherent frame; returns visible note count. */
  render(frame, summaryText) {
    const started = typeof performance !== "undefined" ? performance.now() : Date.now();
    let visible = 0;
    if (frame.mode === "sheet" || frame.mode === "hybrid") {
      visible += this.sheet.render(frame);
    }
    if (frame.mode === "falling" || frame.mode === "hybrid") {
      visible += this.falling.render(frame);
    }
    const ended = typeof performance !== "undefined" ? performance.now() : Date.now();
    this.lastFrameMs = ended - started;
    this.lastVisible = visible;
    this.root.dataset.status = frame.status;
    this.root.dataset.songId = frame.songId || "";
    this.root.dataset.scoreSongId = frame.score?.song_id || "";
    this.root.dataset.fingeringStatus = frame.fingeringStatus || "idle";
    this.root.dataset.fingeringCount = String(frame.fingeringByIndex?.size || 0);
    this.root.dataset.section = frame.clippedSection ? JSON.stringify(frame.clippedSection) : "";
    this.root.dataset.targetCount = String(frame.learning.targetNotes.length);
    this.root.dataset.position = String(Math.round(frame.position * 100) / 100);
    this.root.dataset.visible = String(visible);
    this.root.dataset.frameMs = String(Math.round(this.lastFrameMs * 100) / 100);
    this.statusEl.dataset.state = frame.status;
    if (summaryText && summaryText !== this._lastSummary) {
      this.summaryEl.textContent = summaryText;
      for (const canvas of [this.sheet.pane.canvas, this.falling.pane.canvas]) {
        canvas.setAttribute("aria-label", summaryText);
      }
      this._lastSummary = summaryText;
    }
    if (frame.status === "stale" || frame.status === "unavailable") {
      this._drawStaleOverlay(frame);
    }
    return visible;
  }

  _drawStaleOverlay(frame) {
    const target = frame.mode === "sheet" ? this.sheet : this.falling;
    const pane = target.pane;
    const ctx = pane.ctx;
    const { cssWidth: width, cssHeight: height } = pane;
    if (width <= 1 || height <= 1) return;
    const message =
      frame.status === "unavailable" ? "Score unavailable — visual paused" : STALE_MESSAGE;
    ctx.save();
    ctx.fillStyle = "rgba(8, 12, 22, 0.55)";
    ctx.fillRect(0, height / 2 - 18, width, 36);
    ctx.fillStyle = "#e8eefb";
    ctx.font = "13px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(message, width / 2, height / 2 + 4);
    ctx.restore();
  }

  dispose() {
    if (this._resizeObserver) {
      this._resizeObserver.disconnect();
      this._resizeObserver = null;
    }
  }
}
