/* Music surface: Now Playing with Search, Playlists and Recent beside it.
 *
 * Presentation and device-local lists only. app.js stays the single Core
 * integration point: every Play/Learn goes through the callbacks it hands
 * in, so this module never issues a playback or Learning command itself.
 * - Playlists are browser-local (localStorage), like Recent: Core has no
 *   playlist contract, so nothing here pretends to sync across devices.
 * - The play queue is the list a song was started from (search results,
 *   a playlist or Recent); Previous/Next and auto-advance walk that list.
 */

export const PLAYLIST_STORAGE_KEY = "piano-core.playlists.v1";
export const PLAYLIST_LIMIT = 50;
export const PLAYLIST_SONG_LIMIT = 500;
export const PLAYLIST_NAME_MAX = 60;
export const MUSIC_TABS = Object.freeze(["search", "playlists", "recent"]);

/* ---------------------------------------------------------------------
 * Pure logic (runs under node too)
 * ------------------------------------------------------------------- */

/** The canonical SongRecord fields a list entry keeps (nothing invented). */
export function songEntry(record) {
  if (!record || typeof record !== "object") return null;
  const songId = typeof record.song_id === "string" ? record.song_id : "";
  if (!songId) return null;
  return {
    song_id: songId,
    title: record.title || record.filename || songId,
    filename: record.filename ?? null,
    duration_seconds: typeof record.duration_seconds === "number" ? record.duration_seconds : null,
    track_count: typeof record.track_count === "number" ? record.track_count : null,
    midi_format: record.midi_format ?? null,
  };
}

export const COVER_TONES = 8;

/**
 * A stable cover for a song: one of eight palettes and a row of "level
 * meter" bars whose heights come from the song id, so every song keeps the
 * same artwork everywhere it appears.
 */
export function songArt(record, bars = 9) {
  const key = String(record?.song_id || record?.title || "");
  let hash = 2166136261;
  for (let i = 0; i < key.length; i += 1) hash = Math.imul(hash ^ key.charCodeAt(i), 16777619) >>> 0;
  const tone = String(hash % COVER_TONES);
  const heights = [];
  let seed = hash || 1;
  for (let i = 0; i < bars; i += 1) {
    seed = (Math.imul(seed, 1103515245) + 12345) >>> 0;
    heights.push(1 + ((seed >>> 16) % 8));
  }
  return { tone, heights };
}

export function paintCover(node, record, documentRef = globalThis.document, bars = 9) {
  if (!node) return;
  const art = songArt(record, bars);
  node.dataset.tone = art.tone;
  node.textContent = "";
  for (const height of art.heights) {
    const bar = documentRef.createElement("i");
    bar.className = "cover-bar";
    bar.dataset.h = String(height);
    node.append(bar);
  }
}

function cleanName(name) {
  return String(name ?? "").replace(/\s+/g, " ").trim().slice(0, PLAYLIST_NAME_MAX);
}

export function createPlaylistStore({
  storage = globalThis.localStorage,
  key = PLAYLIST_STORAGE_KEY,
  now = () => Date.now(),
} = {}) {
  let counter = 0;

  function load() {
    if (!storage) return [];
    try {
      const parsed = JSON.parse(storage.getItem(key) || "[]");
      if (!Array.isArray(parsed)) return [];
      return parsed
        .filter((list) => list && typeof list.id === "string" && typeof list.name === "string" && Array.isArray(list.songs))
        .slice(0, PLAYLIST_LIMIT)
        .map((list) => ({
          id: list.id,
          name: cleanName(list.name) || "Playlist",
          songs: list.songs.map(songEntry).filter(Boolean).slice(0, PLAYLIST_SONG_LIMIT),
        }));
    } catch {
      return [];
    }
  }

  function save(lists) {
    if (!storage) return false;
    try {
      storage.setItem(key, JSON.stringify(lists));
      return true;
    } catch {
      // Private mode / quota: the lists stay usable for this page view.
      return false;
    }
  }

  function update(id, change) {
    const lists = load();
    const list = lists.find((entry) => entry.id === id);
    if (!list) return null;
    change(list);
    save(lists);
    return list;
  }

  return Object.freeze({
    list: load,
    get(id) {
      return load().find((entry) => entry.id === id) ?? null;
    },
    create(name) {
      const clean = cleanName(name);
      const lists = load();
      if (!clean || lists.length >= PLAYLIST_LIMIT) return null;
      counter += 1;
      const list = { id: `pl-${now().toString(36)}-${counter}`, name: clean, songs: [] };
      lists.push(list);
      save(lists);
      return list;
    },
    remove(id) {
      const lists = load();
      const next = lists.filter((entry) => entry.id !== id);
      if (next.length === lists.length) return false;
      save(next);
      return true;
    },
    /** Adds a song once; returns "added", "exists" or null when refused. */
    addSong(id, record) {
      const entry = songEntry(record);
      if (!entry) return null;
      let outcome = null;
      update(id, (list) => {
        if (list.songs.some((song) => song.song_id === entry.song_id)) {
          outcome = "exists";
        } else if (list.songs.length < PLAYLIST_SONG_LIMIT) {
          list.songs.push(entry);
          outcome = "added";
        }
      });
      return outcome;
    },
    removeSong(id, songId) {
      let removed = false;
      update(id, (list) => {
        const before = list.songs.length;
        list.songs = list.songs.filter((song) => song.song_id !== songId);
        removed = list.songs.length !== before;
      });
      return removed;
    },
  });
}

/**
 * The play queue: an ordered snapshot of the list a song was started from.
 * `armed` names the song this client started; only that song finishing
 * (COMPLETED, never a user Stop) may advance the queue.
 */
export function createPlayQueue() {
  let items = [];
  let index = -1;
  let source = null;
  let armed = null;

  return Object.freeze({
    set(list, start, origin = null) {
      items = (Array.isArray(list) ? list : []).map(songEntry).filter(Boolean);
      index = Math.min(Math.max(0, start), items.length - 1);
      source = origin;
      return this.current();
    },
    /** Keeps the queue when the song is already in it, else plays it alone. */
    focus(record) {
      const entry = songEntry(record);
      if (!entry) return null;
      const at = items.findIndex((song) => song.song_id === entry.song_id);
      if (at >= 0) index = at;
      else this.set([entry], 0, null);
      return this.current();
    },
    current: () => (index >= 0 ? items[index] ?? null : null),
    peekNext: () => (index >= 0 && index + 1 < items.length ? items[index + 1] : null),
    peekPrevious: () => (index > 0 ? items[index - 1] : null),
    step(delta) {
      const target = index + delta;
      if (index < 0 || target < 0 || target >= items.length) return null;
      index = target;
      return items[index];
    },
    arm(songId) {
      armed = songId || null;
    },
    /** True exactly once when the armed song reports COMPLETED. */
    takeCompletion(snapshot) {
      if (!armed || !snapshot || snapshot.song_id !== armed) return false;
      if (snapshot.state === "COMPLETED") {
        armed = null;
        return true;
      }
      if (["IDLE", "FAILED"].includes(snapshot.state)) armed = null;
      return false;
    },
    get source() {
      return source;
    },
    get size() {
      return items.length;
    },
  });
}

function formatClock(seconds) {
  if (typeof seconds !== "number" || !(seconds > 0)) return "";
  const total = Math.floor(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/* ---------------------------------------------------------------------
 * DOM controller (browser only)
 * ------------------------------------------------------------------- */

export function createMusicController({
  documentRef = globalThis.document,
  playlists = createPlaylistStore(),
  recents,
  onPlay,
  onLearn,
  toast = () => {},
} = {}) {
  const el = (id) => documentRef.getElementById(id);
  const dom = {
    tabs: [...documentRef.querySelectorAll("[data-music-tab]")],
    panels: {
      search: el("music-panel-search"),
      playlists: el("music-panel-playlists"),
      recent: el("music-panel-recent"),
    },
    addBanner: el("music-add-banner"),
    addLabel: el("music-add-label"),
    addCancel: el("music-add-cancel"),
    home: el("music-playlists-home"),
    newForm: el("music-new-playlist"),
    newName: el("music-new-playlist-name"),
    list: el("music-playlist-list"),
    listEmpty: el("music-playlists-empty"),
    detail: el("music-playlist-detail"),
    detailTitle: el("music-playlist-title"),
    detailBack: el("music-playlist-back"),
    detailPlay: el("music-playlist-play"),
    detailDelete: el("music-playlist-delete"),
    detailSongs: el("music-playlist-songs"),
    detailEmpty: el("music-playlist-empty"),
    recentList: el("music-recent-list"),
    recentEmpty: el("music-recent-empty"),
    addCurrent: el("music-add-current"),
    learnCurrent: el("music-learn-current"),
    upNext: el("music-up-next"),
    prev: el("music-prev"),
    next: el("music-next"),
    art: el("music-art"),
    browse: el("music-browse"),
    browseToggle: el("music-browse-toggle"),
    browseClose: el("music-browse-close"),
  };

  const queue = createPlayQueue();
  let tab = "search";
  let openPlaylistId = null;
  let pendingAdd = null; // song waiting for a playlist choice
  let currentSong = null; // what Now Playing shows (selected or playing)
  let busy = false;

  function setText(node, text) {
    if (node) node.textContent = text;
  }

  function show(node, visible) {
    if (node) node.hidden = !visible;
  }

  /* ---------------- library drawer ---------------- */

  let browseOpen = false;
  function setBrowse(open) {
    browseOpen = Boolean(open);
    if (documentRef.body?.dataset) documentRef.body.dataset.musicBrowse = browseOpen ? "open" : "closed";
    dom.browseToggle?.setAttribute("aria-expanded", String(browseOpen));
  }
  const compact = () => Boolean(globalThis.matchMedia?.("(max-width: 759px)")?.matches);
  setBrowse(false);
  dom.browseToggle?.addEventListener("click", () => setBrowse(!browseOpen));
  dom.browseClose?.addEventListener("click", () => {
    setBrowse(false);
    dom.browseToggle?.focus?.();
  });
  documentRef.addEventListener?.("keydown", (event) => {
    if (event.key === "Escape" && browseOpen && compact()) setBrowse(false);
  });

  /* ---------------- tabs ---------------- */

  function selectTab(next, { focus = false, open = false } = {}) {
    if (!MUSIC_TABS.includes(next)) return;
    tab = next;
    for (const button of dom.tabs) {
      const selected = button.dataset.musicTab === next;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
      if (selected && focus) button.focus();
    }
    for (const [name, panel] of Object.entries(dom.panels)) show(panel, name === next);
    if (open) setBrowse(true);
    if (next === "playlists") renderPlaylists();
    if (next === "recent") renderRecent();
  }

  for (const button of dom.tabs) {
    button.addEventListener("click", () => selectTab(button.dataset.musicTab));
    button.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      event.preventDefault();
      const at = MUSIC_TABS.indexOf(tab) + (event.key === "ArrowRight" ? 1 : -1);
      selectTab(MUSIC_TABS[(at + MUSIC_TABS.length) % MUSIC_TABS.length], { focus: true });
    });
  }

  /* ---------------- song rows (playlist / recent) ---------------- */

  function songRow(entry, { onTap, removable = false, onRemove } = {}) {
    const item = documentRef.createElement("li");
    item.className = "song-item";
    item.dataset.songId = entry.song_id;
    const button = documentRef.createElement("button");
    button.type = "button";
    button.className = "btn btn-song";
    button.setAttribute("aria-label", `Play ${entry.title}`);
    const cover = documentRef.createElement("span");
    cover.className = "song-cover";
    cover.setAttribute("aria-hidden", "true");
    paintCover(cover, entry, documentRef);
    const copy = documentRef.createElement("span");
    copy.className = "song-copy";
    const name = documentRef.createElement("span");
    name.className = "song-name";
    setText(name, entry.title);
    const sub = documentRef.createElement("span");
    sub.className = "song-sub";
    setText(sub, formatClock(entry.duration_seconds) || "Song");
    const playing = documentRef.createElement("span");
    playing.className = "song-playing";
    playing.hidden = true;
    copy.append(name, sub, playing);
    button.append(cover, copy);
    button.addEventListener("click", onTap);
    item.append(button);
    if (removable) {
      const actions = documentRef.createElement("span");
      actions.className = "song-actions";
      const remove = documentRef.createElement("button");
      remove.type = "button";
      remove.className = "btn song-action-btn song-action-btn--remove";
      remove.setAttribute("aria-label", `Remove ${entry.title} from this playlist`);
      setText(remove, "✕");
      remove.addEventListener("click", onRemove);
      actions.append(remove);
      item.append(actions);
    }
    return item;
  }

  function markPlaying(list, snapshot) {
    for (const row of list?.children ?? []) {
      const active = snapshot && snapshot.song_id === row.dataset.songId && ["PLAYING", "PAUSED"].includes(snapshot.state);
      row.dataset.playback = active ? snapshot.state.toLowerCase() : "none";
      const badge = row.querySelector(".song-playing");
      if (badge) {
        badge.hidden = !active;
        badge.textContent = active ? (snapshot.state === "PLAYING" ? "Now playing" : "Paused") : "";
      }
    }
  }

  /* ---------------- playlists ---------------- */

  function renderPlaylists() {
    const lists = playlists.list();
    if (openPlaylistId && !lists.some((list) => list.id === openPlaylistId)) openPlaylistId = null;
    const detailOpen = openPlaylistId !== null && pendingAdd === null;
    show(dom.home, !detailOpen);
    show(dom.detail, detailOpen);
    show(dom.addBanner, pendingAdd !== null);
    if (pendingAdd) setText(dom.addLabel, `Add “${pendingAdd.title}” to…`);
    if (detailOpen) {
      renderPlaylistDetail(lists.find((list) => list.id === openPlaylistId));
      return;
    }
    const fragment = documentRef.createDocumentFragment();
    for (const list of lists) {
      const item = documentRef.createElement("li");
      const button = documentRef.createElement("button");
      button.type = "button";
      button.className = "btn music-playlist-btn";
      const name = documentRef.createElement("span");
      name.className = "song-name";
      setText(name, list.name);
      const count = documentRef.createElement("span");
      count.className = "song-sub";
      setText(count, `${list.songs.length} song${list.songs.length === 1 ? "" : "s"}`);
      button.append(name, count);
      button.addEventListener("click", () => {
        if (pendingAdd) {
          addTo(list.id, pendingAdd);
          return;
        }
        openPlaylistId = list.id;
        renderPlaylists();
      });
      item.append(button);
      fragment.append(item);
    }
    dom.list?.replaceChildren(fragment);
    show(dom.listEmpty, lists.length === 0);
  }

  function renderPlaylistDetail(list) {
    setText(dom.detailTitle, list.name);
    if (dom.detailPlay) dom.detailPlay.disabled = list.songs.length === 0 || busy;
    const fragment = documentRef.createDocumentFragment();
    list.songs.forEach((entry, at) => {
      fragment.append(songRow(entry, {
        onTap: () => play(list.songs, at, `playlist:${list.id}`),
        removable: true,
        onRemove: () => {
          playlists.removeSong(list.id, entry.song_id);
          renderPlaylists();
        },
      }));
    });
    dom.detailSongs?.replaceChildren(fragment);
    show(dom.detailEmpty, list.songs.length === 0);
    markPlaying(dom.detailSongs, lastSnapshot);
  }

  function addTo(id, record) {
    const outcome = playlists.addSong(id, record);
    const list = playlists.get(id);
    pendingAdd = null;
    if (outcome === "added") toast(`Added to ${list?.name ?? "playlist"}`);
    else if (outcome === "exists") toast(`Already in ${list?.name ?? "this playlist"}`);
    else toast("The playlist is full.");
    renderPlaylists();
  }

  /** Opens Playlists in "pick a playlist" mode for one song. */
  function requestAdd(record) {
    const entry = songEntry(record);
    if (!entry) return;
    pendingAdd = entry;
    openPlaylistId = null;
    selectTab("playlists", { open: true });
    if (playlists.list().length === 0) dom.newName?.focus();
  }

  dom.addCancel?.addEventListener("click", () => {
    pendingAdd = null;
    renderPlaylists();
  });
  dom.newForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    const created = playlists.create(dom.newName?.value);
    if (!created) {
      toast(dom.newName?.value.trim() ? "No room for another playlist." : "Give the playlist a name.");
      return;
    }
    if (dom.newName) dom.newName.value = "";
    if (pendingAdd) {
      addTo(created.id, pendingAdd);
      return;
    }
    openPlaylistId = created.id;
    renderPlaylists();
  });
  dom.detailBack?.addEventListener("click", () => {
    openPlaylistId = null;
    renderPlaylists();
  });
  dom.detailPlay?.addEventListener("click", () => {
    const list = playlists.get(openPlaylistId);
    if (list?.songs.length) play(list.songs, 0, `playlist:${list.id}`);
  });
  dom.detailDelete?.addEventListener("click", () => {
    const list = playlists.get(openPlaylistId);
    if (!list) return;
    if (typeof globalThis.confirm === "function" && !globalThis.confirm(`Delete “${list.name}”?`)) return;
    playlists.remove(list.id);
    openPlaylistId = null;
    renderPlaylists();
  });

  /* ---------------- recent ---------------- */

  function renderRecent() {
    const entries = (recents?.list?.() ?? []).map(songEntry).filter(Boolean);
    const fragment = documentRef.createDocumentFragment();
    entries.forEach((entry, at) => {
      fragment.append(songRow(entry, { onTap: () => play(entries, at, "recent") }));
    });
    dom.recentList?.replaceChildren(fragment);
    show(dom.recentEmpty, entries.length === 0);
    markPlaying(dom.recentList, lastSnapshot);
  }

  /* ---------------- queue + now playing ---------------- */

  async function play(list, at, origin) {
    if (busy) return;
    if (compact()) setBrowse(false);
    const entry = queue.set(list, at, origin);
    if (!entry) return;
    await start(entry);
  }

  async function start(entry) {
    busy = true;
    syncTransport();
    try {
      const started = await onPlay?.(entry);
      if (started) queue.arm(entry.song_id);
    } finally {
      busy = false;
      syncTransport();
    }
  }

  async function step(delta) {
    if (busy) return;
    const entry = queue.step(delta);
    if (entry) await start(entry);
  }

  dom.prev?.addEventListener("click", () => step(-1));
  dom.next?.addEventListener("click", () => step(1));
  dom.addCurrent?.addEventListener("click", () => {
    if (currentSong) requestAdd(currentSong);
  });
  dom.learnCurrent?.addEventListener("click", () => {
    if (currentSong) onLearn?.(currentSong);
  });

  function syncTransport() {
    if (dom.prev) dom.prev.disabled = busy || !queue.peekPrevious();
    if (dom.next) dom.next.disabled = busy || !queue.peekNext();
    const upcoming = queue.peekNext();
    show(dom.upNext, Boolean(upcoming));
    setText(dom.upNext, upcoming ? `Up next: ${upcoming.title}` : "");
    if (dom.addCurrent) dom.addCurrent.disabled = !currentSong;
    if (dom.learnCurrent) dom.learnCurrent.disabled = !currentSong;
    if (dom.detailPlay && openPlaylistId) {
      dom.detailPlay.disabled = busy || !(playlists.get(openPlaylistId)?.songs.length);
    }
  }

  let lastSnapshot = null;

  return Object.freeze({
    /** Called from outside (Home, search shortcut): also opens the library. */
    selectTab(next, options = {}) {
      selectTab(next, { ...options, open: true });
    },
    setBrowse,
    requestAdd,
    /** A song started from the search list: the visible results become the queue. */
    playFromList(list, record, origin = "search") {
      const at = list.findIndex((song) => song?.song_id === record?.song_id);
      return play(at >= 0 ? list : [record], Math.max(0, at), origin);
    },
    /** Now Playing reflects a selection made anywhere (search, Home, Learn). */
    setCurrentSong(record) {
      currentSong = songEntry(record);
      if (dom.art) {
        if (currentSong) paintCover(dom.art, currentSong, documentRef, 24);
        else { delete dom.art.dataset.tone; dom.art.textContent = ""; }
      }
      syncTransport();
    },
    /** Called on every authoritative playback snapshot. */
    onPlayback(snapshot, fresh) {
      lastSnapshot = fresh ? snapshot : null;
      markPlaying(dom.detailSongs, lastSnapshot);
      markPlaying(dom.recentList, lastSnapshot);
      if (fresh && queue.takeCompletion(snapshot)) {
        const upcoming = queue.peekNext();
        if (upcoming) void step(1);
      }
      syncTransport();
    },
    refreshRecent() {
      if (tab === "recent") renderRecent();
    },
    queue,
    get tab() {
      return tab;
    },
  });
}
