/* PIANO-UI-008 U8-P2: premium Library presentation controller.
 *
 * Search-first catalog experience over the EXISTING bounded Core API:
 * - GET /api/v1/library/songs supports an unfiltered offset listing
 *   (relpath A–Z order) and a relpath-ordered limited search only. There
 *   is no artist/genre/difficulty/recency contract, so none is shown.
 * - Request identity: every query owns a monotonic token plus an
 *   AbortController; a slower older response can never replace a newer one.
 * - Bounded DOM: at most LIBRARY_MAX_ROWS result rows are ever attached,
 *   regardless of catalog size (ADR-019 §5.2).
 * - The controller never issues playback or Learning commands: Play and
 *   Learn are delegated to app.js through callbacks (the accepted entry
 *   points), so no musical authority is duplicated here.
 */

import { createLayerController } from "./layers.js";

export const LIBRARY_PAGE_SIZE = 50;
export const LIBRARY_MAX_ROWS = 100;
export const LIBRARY_DEBOUNCE_MS = 300;

/* ---------------------------------------------------------------------
 * Pure logic (runs under node too)
 * ------------------------------------------------------------------- */

function formatClock(seconds) {
  const total = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

/**
 * Decide how one page of results maps onto the bounded DOM window.
 * `renderedCount` rows are already attached; `count` is the size of the
 * payload that just arrived. Browse listing may append pages up to
 * `maxRows`; a search answer replaces the window in one bounded page
 * (the API provides no search offset).
 */
export function planResultWindow({
  query,
  offset = 0,
  renderedCount = 0,
  count = 0,
  pageSize = LIBRARY_PAGE_SIZE,
  maxRows = LIBRARY_MAX_ROWS,
}) {
  const searching = typeof query === "string" && query.length > 0;
  if (searching) {
    return {
      append: false,
      rows: Math.min(count, maxRows),
      canLoadMore: false,
      capped: false,
      moreRemaining: count >= pageSize,
    };
  }
  const freeRows = Math.max(0, maxRows - renderedCount);
  const rows = Math.min(count, freeRows);
  const attachedAfter = renderedCount + rows;
  const truncated = count > rows;
  // "Capped" means the bound is now load-bearing: this page was truncated,
  // or the window reached the ceiling with a full page still flowing.
  const capped = truncated || (count >= pageSize && attachedAfter >= maxRows);
  return {
    append: offset > 0,
    rows,
    canLoadMore: !truncated && count >= pageSize && attachedAfter < maxRows,
    capped,
    moreRemaining: false,
  };
}

/**
 * Presentation model for one result row using ONLY canonical SongRecord
 * fields (title, duration_seconds, track_count, midi_format, status).
 * Nothing is inferred or invented.
 */
export function libraryRowModel(record) {
  if (!record || typeof record !== "object") return null;
  const meta = [];
  if (typeof record.duration_seconds === "number" && record.duration_seconds > 0) {
    meta.push(formatClock(record.duration_seconds));
  }
  if (typeof record.track_count === "number" && record.track_count > 0) {
    meta.push(`${record.track_count} track${record.track_count === 1 ? "" : "s"}`);
  }
  if (record.midi_format !== null && record.midi_format !== undefined) {
    meta.push(`MIDI ${record.midi_format}`);
  }
  return {
    id: String(record.song_id ?? ""),
    title: record.title || record.filename || record.song_id,
    meta: meta.join(" · "),
    statusChip:
      record.status && record.status !== "parsed" ? String(record.status) : null,
  };
}

/**
 * Monotonic request identity for search/listing: the newest token wins,
 * older in-flight requests are aborted and their results discarded.
 */
export function createSearchGate() {
  let latest = 0;
  let controller = null;
  return Object.freeze({
    begin() {
      latest += 1;
      if (controller) controller.abort();
      controller = typeof AbortController === "function" ? new AbortController() : null;
      const token = latest;
      return {
        token,
        signal: controller ? controller.signal : undefined,
        stale: (candidate) => candidate !== latest,
      };
    },
    isLatest: (token) => token === latest,
    abortPending() {
      if (controller) controller.abort();
    },
  });
}

/* ---------------------------------------------------------------------
 * DOM controller (browser only)
 * ------------------------------------------------------------------- */

function el(id, documentRef) {
  return documentRef.getElementById(id);
}

export function createLibraryController({
  documentRef = globalThis.document,
  windowRef = globalThis.window,
  fetchSongs,
  onPlay,
  onLearn,
  onSelect,
  pageSize = LIBRARY_PAGE_SIZE,
  maxRows = LIBRARY_MAX_ROWS,
  debounceMs = LIBRARY_DEBOUNCE_MS,
} = {}) {
  if (typeof fetchSongs !== "function") {
    throw new TypeError("fetchSongs(query, limit, offset, signal) is required");
  }

  const dom = {
    summary: el("library-catalog", documentRef),
    search: el("search", documentRef),
    searchForm: el("search-form", documentRef),
    btnClear: el("btn-clear", documentRef),
    results: el("results", documentRef),
    feedback: el("library-feedback", documentRef),
    btnMore: el("btn-more", documentRef),
    btnRetry: el("library-retry", documentRef),
    chipReady: el("library-filter-ready", documentRef),
    skeleton: el("library-skeleton", documentRef),
    emptyState: el("library-empty", documentRef),
    sheet: el("ui008-song-sheet", documentRef),
    sheetClose: el("ui008-song-sheet-close", documentRef),
    sheetTitle: el("ui008-song-sheet-title", documentRef),
    sheetMeta: el("ui008-song-sheet-meta", documentRef),
    sheetPlay: el("ui008-song-sheet-play", documentRef),
    sheetLearn: el("ui008-song-sheet-learn", documentRef),
    sheetDetails: el("ui008-song-sheet-details", documentRef),
  };

  const gate = createSearchGate();
  const state = {
    query: "",
    offset: 0,
    renderedIds: new Set(),
    renderedCount: 0,
    lastRecord: null,
    debounce: null,
    focusRequested: false,
    selectedId: null,
    playback: null,
    fresh: false,
    pending: false,
  };

  const sheetLayer =
    dom.sheet && dom.sheetClose
      ? createLayerController({
          windowRef,
          documentRef,
          root: dom.sheet,
          closeButton: dom.sheetClose,
          layerId: "song-actions",
        })
      : null;

  function setText(node, text) {
    if (node) node.textContent = text;
  }

  function show(node, visible) {
    if (node) node.hidden = !visible;
  }

  function setFeedback(text) {
    setText(dom.feedback, text || "");
    show(dom.feedback, Boolean(text));
  }

  function setBusy(busy) {
    show(dom.skeleton, busy);
    dom.results?.setAttribute("aria-busy", String(busy));
  }

  /* ---------------- rendering ---------------- */

  function buildRow(record) {
    const model = libraryRowModel(record);
    const item = documentRef.createElement("li");
    item.className = "song-item";
    item.dataset.songId = model.id;
    item.setAttribute("aria-selected", "false");

    const button = documentRef.createElement("button");
    button.type = "button";
    button.className = "btn btn-song";
    button.dataset.action = "details";
    button.setAttribute(
      "aria-label",
      `${model.title}${model.meta ? `, ${model.meta}` : ""}. Show details.`
    );

    const name = documentRef.createElement("span");
    name.className = "song-name";
    setText(name, model.title);

    const sub = documentRef.createElement("span");
    sub.className = "song-sub";
    setText(sub, model.meta || "Catalog entry");

    const cover = documentRef.createElement("span");
    cover.className = "song-cover";
    cover.setAttribute("aria-hidden", "true");
    const copy = documentRef.createElement("span");
    copy.className = "song-copy";
    const playing = documentRef.createElement("span");
    playing.className = "song-playing";
    playing.hidden = true;
    copy.append(name, sub, playing);
    button.append(cover, copy);

    if (model.statusChip) {
      const chip = documentRef.createElement("span");
      chip.className = "song-chip";
      chip.dataset.chip = model.statusChip;
      setText(chip, model.statusChip);
      button.append(chip);
    }

    const actions = documentRef.createElement("span");
    actions.className = "song-actions";
    for (const [action, label] of [
      ["play", "Play"],
      ["learn", "Learn"],
    ]) {
      const actionButton = documentRef.createElement("button");
      actionButton.type = "button";
      actionButton.className = `btn song-action-btn${action === "play" ? " song-play" : ""}`;
      actionButton.dataset.action = action;
      actionButton.setAttribute("aria-label", `${label} ${model.title}`);
      setText(actionButton, label);
      actions.append(actionButton);
    }

    item.append(button, actions);
    syncRow(item);
    return { item, model };
  }

  function syncRow(row) {
    const isSelected = row.dataset.songId === state.selectedId;
    row.setAttribute("aria-selected", String(isSelected));
    const active = state.fresh && state.playback?.song_id === row.dataset.songId && ["PLAYING", "PAUSED"].includes(state.playback.state);
    row.dataset.playback = active ? state.playback.state.toLowerCase() : "none";
    const badge = row.querySelector(".song-playing");
    badge.hidden = !active;
    badge.textContent = active ? state.playback.state === "PLAYING" ? "Now playing" : "Paused" : "";
    for (const button of row.querySelectorAll("[data-action='play']")) button.disabled = state.pending;
  }

  function markSelection(songId) {
    state.selectedId = String(songId);
    for (const row of dom.results?.children ?? []) {
      row.setAttribute("aria-selected", String(row.dataset.songId === songId));
    }
  }

  function clearSelectionHighlight() {
    state.selectedId = null;
    for (const row of dom.results?.children ?? []) {
      row.setAttribute("aria-selected", "false");
    }
  }

  function attachRowListeners(item, record) {
    item.addEventListener("click", (event) => {
      const target = event.target.closest("[data-action]");
      const action = target?.dataset.action;
      if (action === "play") {
        event.preventDefault();
        markSelection(record.song_id);
        onPlay?.(record);
      } else if (action === "learn") {
        event.preventDefault();
        markSelection(record.song_id);
        onLearn?.(record);
      } else if (action === "details") {
        event.preventDefault();
        // ADR-019 §5.2: direct Play/Learn actions at tablet sizes; phone
        // opens the labeled action sheet. The CSS breakpoint owns the mode.
        const inlineActions =
          typeof windowRef.matchMedia === "function" &&
          windowRef.matchMedia("(min-width: 600px)").matches;
        if (inlineActions) selectRecord(record, item);
        else openSheet(record, item);
      }
    });
  }

  function selectRecord(record, item, { intent = null } = {}) {
    if (onSelect?.(record, intent) === false) return false;
    state.lastRecord = record;
    if (item) markSelection(record.song_id);
    return true;
  }

  function renderRows(records, plan) {
    if (!plan.append) {
      const fragment = documentRef.createDocumentFragment();
      const bounded = records.slice(0, plan.rows);
      state.renderedIds = new Set();
      state.renderedCount = 0;
      for (const record of bounded) {
        const { item } = buildRow(record);
        attachRowListeners(item, record);
        fragment.append(item);
        state.renderedIds.add(String(record.song_id));
        state.renderedCount += 1;
      }
      dom.results.replaceChildren(fragment);
      return;
    }
    const fragment = documentRef.createDocumentFragment();
    let added = 0;
    for (const record of records) {
      if (added >= plan.rows) break;
      if (state.renderedIds.has(String(record.song_id))) continue;
      const { item } = buildRow(record);
      attachRowListeners(item, record);
      fragment.append(item);
      state.renderedIds.add(String(record.song_id));
      added += 1;
    }
    dom.results.append(fragment);
    state.renderedCount += added;
  }

  function renderStateMessage({ query, count, plan }) {
    if (query) {
      if (count === 0) {
        setFeedback(`No songs match “${query}”. Clear the search to see the catalog.`);
      } else if (plan.moreRemaining) {
        setFeedback(
          `Showing the first ${count} matches — refine your search to narrow it down.`
        );
      } else {
        setFeedback("");
      }
      return;
    }
    if (count === 0 && state.renderedCount === 0) {
      setFeedback("");
      show(dom.emptyState, true);
      return;
    }
    show(dom.emptyState, false);
    if (plan.capped) {
      setFeedback(
        `Showing the first ${state.renderedCount} of many songs — search to find what you need faster.`
      );
    } else {
      setFeedback("");
    }
  }

  /* ---------------- query pipeline ---------------- */

  async function run(query, offset) {
    const request = gate.begin();
    // The request identity is recorded at REQUEST time so a retry after a
    // failure repeats exactly the same query, never an older one.
    state.query = query;
    state.offset = offset;
    setBusy(true);
    show(dom.btnRetry, false);
    show(dom.emptyState, false);
    try {
      const readyOnly = dom.chipReady?.getAttribute("aria-pressed") === "true";
      const status = !query && readyOnly ? "parsed" : undefined;
      const payload = await fetchSongs(query, pageSize, offset, {
        signal: request.signal,
        status,
      });
      if (request.stale(request.token)) return;
      const records = Array.isArray(payload?.songs) ? payload.songs : [];
      const count = Number(payload?.count ?? records.length) || 0;
      const plan = planResultWindow({
        query,
        offset,
        renderedCount: offset === 0 ? 0 : state.renderedCount,
        count,
        pageSize,
        maxRows,
      });
      renderRows(records, plan);
      syncChipAvailability();
      if (count === 0 && !query && offset === 0) {
        state.renderedCount = 0;
        state.renderedIds = new Set();
      }
      renderStateMessage({ query, count, plan });
      show(dom.btnMore, plan.canLoadMore);
    } catch (error) {
      if (request.stale(request.token)) return;
      if (error?.name === "AbortError") return;
      if (state.renderedCount === 0) dom.results.replaceChildren();
      setFeedback(
        error?.code === "library_unavailable"
          ? "The library is unavailable right now. Retry in a moment."
          : "The library could not be loaded. Check the connection and retry."
      );
      show(dom.btnRetry, true);
    } finally {
      if (gate.isLatest(request.token)) setBusy(false);
    }
  }

  function syncChipAvailability() {
    if (!dom.chipReady) return;
    const searching = state.query.length > 0;
    dom.chipReady.disabled = searching;
    if (searching) {
      // The Core search contract has no status facet; the chip must never
      // pretend to filter search results.
      dom.chipReady.setAttribute("aria-pressed", "false");
    }
  }

  function queueSearch(rawQuery) {
    const query = String(rawQuery ?? "").trim();
    state.offset = 0;
    show(dom.btnClear, query.length > 0);
    clearTimeout(state.debounce);
    state.debounce = setTimeout(() => {
      void run(query, 0);
    }, debounceMs);
  }

  function submitImmediate() {
    clearTimeout(state.debounce);
    state.offset = 0;
    void run(String(dom.search?.value ?? "").trim(), 0);
  }

  function retry() {
    show(dom.btnRetry, false);
    void run(state.query, state.offset);
  }

  /* ---------------- wiring ---------------- */

  dom.search?.addEventListener("input", () => queueSearch(dom.search.value));
  dom.searchForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    submitImmediate();
  });
  dom.btnClear?.addEventListener("click", () => {
    if (dom.search) dom.search.value = "";
    show(dom.btnClear, false);
    clearTimeout(state.debounce);
    state.offset = 0;
    void run("", 0);
    dom.search?.focus();
  });
  dom.btnMore?.addEventListener("click", () => {
    state.offset += pageSize;
    void run(state.query, state.offset);
  });
  dom.btnRetry?.addEventListener("click", retry);
  dom.chipReady?.addEventListener("click", () => {
    if (state.query || dom.chipReady.disabled) return;
    const pressed = dom.chipReady.getAttribute("aria-pressed") === "true";
    dom.chipReady.setAttribute("aria-pressed", String(!pressed));
    void run("", 0);
  });

  windowRef.addEventListener("piano-ui-route-change", (event) => {
    if (event.detail?.route !== "library") return;
    if (state.focusRequested) {
      state.focusRequested = false;
      dom.search?.focus({ preventScroll: true });
      dom.search?.select?.();
    }
  });

  /* ---------------- song action sheet ---------------- */

  function openSheet(record, opener) {
    if (!sheetLayer) {
      selectRecord(record);
      return;
    }
    const model = libraryRowModel(record);
    setText(dom.sheetTitle, model.title);
    setText(dom.sheetMeta, model.meta || "Catalog entry");
    state.lastRecord = record;
    sheetLayer.open(opener);
  }

  dom.sheetPlay?.addEventListener("click", () => {
    const record = state.lastRecord;
    if (record) markSelection(record.song_id);
    sheetLayer?.close(() => onPlay?.(record));
  });
  dom.sheetLearn?.addEventListener("click", () => {
    const record = state.lastRecord;
    if (record) markSelection(record.song_id);
    sheetLayer?.close(() => onLearn?.(record));
  });
  dom.sheetDetails?.addEventListener("click", () => {
    const record = state.lastRecord;
    if (record) markSelection(record.song_id);
    sheetLayer?.close(() => onSelect?.(record));
  });

  return Object.freeze({
    queueSearch,
    submitImmediate,
    retry,
    run,
    openSheet,
    selectRecord,
    clearSelectionHighlight,
    setPlayback(snapshot, fresh) {
      state.playback = snapshot;
      state.fresh = fresh;
      for (const row of dom.results?.children ?? []) syncRow(row);
    },
    setPending(pending) {
      state.pending = pending;
      if (dom.sheetPlay) dom.sheetPlay.disabled = pending;
      for (const row of dom.results?.children ?? []) syncRow(row);
    },
    requestSearchFocus() {
      state.focusRequested = true;
    },
    setCatalogSummary(text) {
      setText(dom.summary, text);
    },
    renderedCount: () => state.renderedCount,
    currentQuery: () => state.query,
  });
}
