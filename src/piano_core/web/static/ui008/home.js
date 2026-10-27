/* PIANO-UI-008 U8-P2: premium Home launch surface (ADR-019 §5.1).
 *
 * Home is a launch surface, not a dashboard: Continue, Quick actions,
 * Recent songs and piano readiness — nothing else. Every section shows
 * only what authoritative Core state or real user interaction supports:
 * - Continue derives from the authoritative playback/learning snapshots
 *   app.js already owns; it never issues commands (navigation only).
 * - Recent songs are an explicitly client-local preference recorded from
 *   real selections, stored through the accepted local-preference pattern
 *   (the same localStorage family as the accepted visual-mode key). There
 *   is no durable Core history contract, so this is presentation only and
 *   is never presented as account/device-wide history.
 * - Favorites have no Core contract and are ABSENT, not simulated.
 * - Piano readiness maps the canonical MIDI connection value honestly.
 */

export const RECENT_STORAGE_KEY = "piano-core.ui008-recent";
export const RECENT_STORE_LIMIT = 12;
export const RECENT_RENDER_LIMIT = 6;

/* ---------------------------------------------------------------------
 * Pure logic (runs under node too)
 * ------------------------------------------------------------------- */

/**
 * Client-local recent-songs preference. `storage` follows the
 * localStorage contract (getItem/setItem/removeItem); a corrupt payload
 * is discarded rather than repaired or trusted.
 */
export function createRecentsStore({
  storage = globalThis.localStorage,
  key = RECENT_STORAGE_KEY,
  limit = RECENT_STORE_LIMIT,
} = {}) {
  function load() {
    if (!storage) return [];
    try {
      const raw = storage.getItem(key);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];
      return parsed
        .filter(
          (entry) =>
            entry && typeof entry === "object" &&
            typeof entry.song_id === "string" && entry.song_id
        )
        .slice(0, limit);
    } catch {
      return [];
    }
  }

  function save(entries) {
    if (!storage) return;
    try {
      storage.setItem(key, JSON.stringify(entries));
    } catch {
      // Storage may be unavailable (private mode/quota): recents are an
      // enhancement and must never break the surface.
    }
  }

  return Object.freeze({
    record(song) {
      if (!song || typeof song !== "object") return load();
      const entry = {
        song_id: String(song.song_id ?? ""),
        title: song.title || song.filename || song.song_id || "",
        filename: song.filename ?? null,
        duration_seconds: song.duration_seconds ?? null,
        at: new Date().toISOString(),
      };
      if (!entry.song_id) return load();
      const rest = load().filter((item) => item.song_id !== entry.song_id);
      const next = [entry, ...rest].slice(0, limit);
      save(next);
      return next;
    },
    list: load,
    clear() {
      if (storage) storage.removeItem(key);
      return [];
    },
  });
}

/**
 * The one most recent resumable activity, with deterministic precedence:
 * an actively sounding or paused playback session outranks a waiting
 * Learning session (playback is time-critical); IDLE/COMPLETED sessions
 * never present as resumable.
 */
export function homeContinueDecision({ playback, learning, offline } = {}) {
  if (offline) return { kind: "none" };
  if (
    playback &&
    playback.available &&
    playback.song_id &&
    (playback.state === "PLAYING" || playback.state === "PAUSED")
  ) {
    const title = playback.title || "Untitled song";
    const meta =
      playback.state === "PLAYING"
        ? "Playing now"
        : `Paused${typeof playback.position_seconds === "number" ? ` at ${Math.floor(playback.position_seconds / 60)}:${String(Math.floor(playback.position_seconds % 60)).padStart(2, "0")}` : ""}`;
    return { kind: "playback", title, meta, route: "player" };
  }
  if (
    learning &&
    learning.available &&
    learning.state === "WAITING" &&
    learning.song_id
  ) {
    const title = learning.title || "Untitled song";
    const progress =
      typeof learning.progress === "number"
        ? ` · ${Math.round(learning.progress * 100)}% complete`
        : "";
    return {
      kind: "learning",
      title,
      meta: `Learning session in progress${progress}`,
      route: "learn",
    };
  }
  return { kind: "none" };
}

/**
 * Honest piano readiness wording from the canonical MIDI connection.
 */
export function pianoReadiness(connection, offline) {
  if (offline) return { label: "Status unavailable", tone: "bad" };
  if (connection === "CONNECTED") return { label: "Piano connected", tone: "ok" };
  if (connection === "CONNECTING") {
    return { label: "Looking for your piano", tone: "wait" };
  }
  return { label: "Piano unavailable", tone: "bad" };
}

/**
 * Which Home sections render for the given snapshot. Favorites never
 * appears (no Core contract); the welcome state appears exactly when
 * there is nothing to continue and no recents exist.
 */
export function planHomeSections({ continueDecision, recents = [] } = {}) {
  const hasContinue = continueDecision && continueDecision.kind !== "none";
  const recentVisible = Array.isArray(recents) && recents.length > 0;
  return {
    continueVisible: Boolean(hasContinue),
    recentVisible,
    welcomeVisible: !hasContinue && !recentVisible,
  };
}

/* ---------------------------------------------------------------------
 * DOM controller (browser only)
 * ------------------------------------------------------------------- */

function el(id, documentRef) {
  return documentRef.getElementById(id);
}

export function createHomeController({
  documentRef = globalThis.document,
  windowRef = globalThis.window,
  recents = createRecentsStore(),
} = {}) {
  const dom = {
    continueCard: el("ui008-home-continue", documentRef),
    continueTitle: el("ui008-home-continue-title", documentRef),
    continueMeta: el("ui008-home-continue-meta", documentRef),
    continueAction: el("ui008-home-continue-action", documentRef),
    recentSection: el("ui008-home-recent", documentRef),
    recentList: el("ui008-home-recent-list", documentRef),
    welcome: el("ui008-home-welcome", documentRef),
    readiness: el("ui008-home-readiness", documentRef),
    readinessLabel: el("ui008-home-readiness-label", documentRef),
  };

  let lastDecision = { kind: "none" };

  function show(node, visible) {
    if (node) node.hidden = !visible;
  }

  function setText(node, text) {
    if (node) node.textContent = text;
  }

  function renderContinue(decision) {
    lastDecision = decision;
    const visible = decision.kind !== "none";
    show(dom.continueCard, visible);
    if (!visible) return;
    setText(dom.continueTitle, decision.title);
    setText(dom.continueMeta, decision.meta);
    setText(
      dom.continueAction,
      decision.kind === "playback" ? "Continue playing" : "Continue learning"
    );
  }

  function buildRecentItem(entry) {
    const item = documentRef.createElement("li");
    item.className = "home-recent-item";
    const button = documentRef.createElement("button");
    button.type = "button";
    button.className = "btn home-recent-open";
    button.setAttribute("aria-label", `Open ${entry.title} in the library`);
    const name = documentRef.createElement("span");
    name.className = "song-name";
    setText(name, entry.title);
    const sub = documentRef.createElement("span");
    sub.className = "song-sub";
    setText(sub, "Recently viewed");
    button.append(name, sub);
    button.addEventListener("click", () => {
      windowRef.dispatchEvent(
        new CustomEvent("piano-ui-open-recent", { detail: { ...entry } })
      );
    });
    item.append(button);
    return item;
  }

  function renderRecents(entries) {
    const visible = entries.length > 0;
    show(dom.recentSection, visible);
    if (!visible) {
      dom.recentList?.replaceChildren();
      return;
    }
    const fragment = documentRef.createDocumentFragment();
    for (const entry of entries.slice(0, RECENT_RENDER_LIMIT)) {
      fragment.append(buildRecentItem(entry));
    }
    dom.recentList.replaceChildren(fragment);
  }

  function renderReadiness(readiness) {
    if (!dom.readiness) return;
    setText(dom.readinessLabel, readiness.label);
    dom.readiness.dataset.tone = readiness.tone;
  }

  function refreshWelcome() {
    const plan = planHomeSections({
      continueDecision: lastDecision,
      recents: recents.list(),
    });
    show(dom.welcome, plan.welcomeVisible);
  }

  dom.continueAction?.addEventListener("click", () => {
    if (!lastDecision || lastDecision.kind === "none") return;
    windowRef.dispatchEvent(
      new CustomEvent("piano-ui-home-navigate", {
        detail: { route: lastDecision.route },
      })
    );
  });

  return Object.freeze({
    update({ playback = null, learning = null, offline = false, connection = null } = {}) {
      const decision = homeContinueDecision({ playback, learning, offline });
      renderContinue(decision);
      renderRecents(recents.list());
      renderReadiness(pianoReadiness(connection, offline));
      refreshWelcome();
    },
    setOffline(offline) {
      renderReadiness(pianoReadiness(null, offline));
      if (offline) renderContinue({ kind: "none" });
      refreshWelcome();
    },
    refreshRecents() {
      renderRecents(recents.list());
      refreshWelcome();
    },
  });
}
