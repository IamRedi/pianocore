// Music surface: device-local playlists and the play queue (Node, no DOM).
import assert from "node:assert/strict";
import {
  PLAYLIST_LIMIT,
  createPlayQueue,
  createPlaylistStore,
  songArt,
  songEntry,
} from "../../src/piano_core/web/static/ui008/music.js";

let passes = 0;
function test(name, fn) {
  fn();
  passes += 1;
  console.log(`ok - ${name}`);
}

function memoryStorage() {
  const data = new Map();
  return {
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => data.set(key, String(value)),
    removeItem: (key) => data.delete(key),
  };
}

const ode = { song_id: "ode", title: "Ode to Joy", duration_seconds: 19, track_count: 3, midi_format: 1, status: "parsed" };
const minuet = { song_id: "minuet", filename: "minuet.mid" };

test("song entries keep canonical fields only and never invent a title", () => {
  assert.deepEqual(songEntry(ode), {
    song_id: "ode", title: "Ode to Joy", filename: null, duration_seconds: 19, track_count: 3, midi_format: 1,
  });
  assert.equal(songEntry(minuet).title, "minuet.mid");
  assert.equal(songEntry({ title: "no id" }), null);
  assert.equal(songEntry(null), null);
});

test("playlists persist, refuse empty names and add each song once", () => {
  const storage = memoryStorage();
  const store = createPlaylistStore({ storage });
  assert.equal(store.create("   "), null);
  const list = store.create("  Evening   pieces ");
  assert.equal(list.name, "Evening pieces");
  assert.equal(store.addSong(list.id, ode), "added");
  assert.equal(store.addSong(list.id, ode), "exists");
  assert.equal(store.addSong(list.id, minuet), "added");
  const reloaded = createPlaylistStore({ storage }).get(list.id);
  assert.deepEqual(reloaded.songs.map((song) => song.song_id), ["ode", "minuet"]);
  assert.equal(store.removeSong(list.id, "ode"), true);
  assert.deepEqual(store.get(list.id).songs.map((song) => song.song_id), ["minuet"]);
  assert.equal(store.remove(list.id), true);
  assert.equal(store.list().length, 0);
});

test("playlist storage failures and corrupt data never break the surface", () => {
  const broken = { getItem: () => "{not json", setItem: () => { throw new Error("quota"); } };
  const store = createPlaylistStore({ storage: broken });
  assert.deepEqual(store.list(), []);
  assert.ok(store.create("Still works"));
  const none = createPlaylistStore({ storage: null });
  assert.deepEqual(none.list(), []);
});

test("playlist count is bounded", () => {
  const store = createPlaylistStore({ storage: memoryStorage() });
  for (let i = 0; i < PLAYLIST_LIMIT; i += 1) assert.ok(store.create(`List ${i}`));
  assert.equal(store.create("One too many"), null);
});

test("queue walks the list a song was started from", () => {
  const queue = createPlayQueue();
  queue.set([ode, minuet, { song_id: "twinkle" }], 1, "search");
  assert.equal(queue.current().song_id, "minuet");
  assert.equal(queue.peekPrevious().song_id, "ode");
  assert.equal(queue.peekNext().song_id, "twinkle");
  assert.equal(queue.step(1).song_id, "twinkle");
  assert.equal(queue.step(1), null, "no wrap past the end");
  assert.equal(queue.current().song_id, "twinkle");
  queue.focus({ song_id: "elsewhere", title: "Elsewhere" });
  assert.equal(queue.size, 1, "a song outside the queue plays alone");
});

test("only the armed song finishing naturally advances the queue", () => {
  const queue = createPlayQueue();
  queue.set([ode, minuet], 0);
  queue.arm("ode");
  assert.equal(queue.takeCompletion({ song_id: "ode", state: "PLAYING" }), false);
  assert.equal(queue.takeCompletion({ song_id: "other", state: "COMPLETED" }), false);
  assert.equal(queue.takeCompletion({ song_id: "ode", state: "COMPLETED" }), true);
  assert.equal(queue.takeCompletion({ song_id: "ode", state: "COMPLETED" }), false, "exactly once");
  queue.arm("minuet");
  assert.equal(queue.takeCompletion({ song_id: "minuet", state: "IDLE" }), false, "a Stop disarms");
  assert.equal(queue.takeCompletion({ song_id: "minuet", state: "COMPLETED" }), false);
});

test("song covers are stable per song and bounded", () => {
  const a = songArt({ song_id: "abc" });
  assert.deepEqual(songArt({ song_id: "abc" }), a, "same song, same cover");
  assert.equal(a.heights.length, 9);
  assert.ok(a.heights.every((h) => h >= 1 && h <= 8));
  assert.ok(Number(a.tone) >= 0 && Number(a.tone) < 8);
  assert.equal(songArt({ song_id: "abc" }, 24).heights.length, 24);
});

console.log(`PASS ${passes}/${passes}`);
