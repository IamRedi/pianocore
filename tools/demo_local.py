#!/usr/bin/env python3
"""Run Piano Core locally without a piano, a Raspberry Pi or LED hardware.

The real ``Application`` runs unchanged; only the MIDI hardware boundary is
synthetic (the same approach as ``tests/weblogic/real_backend_fixture.py``).
Everything lives in a temporary directory that is removed on exit.

    python tools/demo_local.py                      # Player + Library
    python tools/demo_local.py --pianio PATH        # + Learn catalog

``--pianio`` points at a checkout of the open Pianio curriculum
(https://github.com/ismaelponce/pianio, content CC0-1.0). The Learn
catalog is then built with the regular ``piano_core.learn`` pipeline and
every lesson is imported as a practice package.

Open http://127.0.0.1:8788/ui/ in a browser. Ctrl+C stops the demo.
"""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import mido

from piano_core.app import Application
from piano_core.device_identity import initialize_identity
from piano_core.library.service import LibraryConfig, LibraryService

# Public-domain melodies, written out note by note for the demo library.
# Each entry: (title, tempo in BPM, [(right-hand notes, left-hand notes, beats)]).
C, D, E, F, G, A, B = 60, 62, 64, 65, 67, 69, 71
_SONGS = {
    "Ode to Joy (Beethoven)": (
        100,
        [((E,), (C - 12,), 1), ((E,), (), 1), ((F,), (), 1), ((G,), (), 1),
         ((G,), (G - 24,), 1), ((F,), (), 1), ((E,), (), 1), ((D,), (), 1),
         ((C,), (C - 12,), 1), ((C,), (), 1), ((D,), (), 1), ((E,), (), 1),
         ((E,), (G - 24,), 1.5), ((D,), (), 0.5), ((D,), (), 2),
         ((E,), (C - 12,), 1), ((E,), (), 1), ((F,), (), 1), ((G,), (), 1),
         ((G,), (G - 24,), 1), ((F,), (), 1), ((E,), (), 1), ((D,), (), 1),
         ((C,), (C - 12,), 1), ((C,), (), 1), ((D,), (), 1), ((E,), (), 1),
         ((D,), (G - 24,), 1.5), ((C,), (), 0.5), ((C,), (C - 12, G - 12), 2)],
    ),
    "Twinkle, Twinkle, Little Star": (
        96,
        [((C,), (C - 12,), 1), ((C,), (), 1), ((G,), (E - 12,), 1), ((G,), (), 1),
         ((A,), (F - 12,), 1), ((A,), (), 1), ((G,), (E - 12,), 2),
         ((F,), (D - 12,), 1), ((F,), (), 1), ((E,), (C - 12,), 1), ((E,), (), 1),
         ((D,), (G - 24,), 1), ((D,), (), 1), ((C,), (C - 12,), 2)],
    ),
    "Minuet in G (Petzold)": (
        120,
        [((D + 12,), (G - 12, B - 12), 1), ((G,), (), 0.5), ((A,), (), 0.5),
         ((B,), (), 0.5), ((C + 12,), (), 0.5),
         ((D + 12,), (B - 12,), 1), ((G,), (), 1), ((G,), (), 1),
         ((E + 12,), (C,), 1), ((C + 12,), (), 0.5), ((D + 12,), (), 0.5),
         ((E + 12,), (), 0.5), ((F + 12 + 1,), (), 0.5),
         ((G + 12,), (B - 12,), 1), ((G,), (), 1), ((G,), (), 1)],
    ),
    "Für Elise, opening (Beethoven)": (
        72,
        [((E + 12,), (), 0.5), ((D + 12 + 1,), (), 0.5), ((E + 12,), (), 0.5),
         ((D + 12 + 1,), (), 0.5), ((E + 12,), (), 0.5), ((B,), (), 0.5),
         ((D + 12,), (), 0.5), ((C + 12,), (), 0.5),
         ((A,), (A - 36,), 1), ((C,), (E - 24,), 0.5), ((E,), (A - 24,), 0.5),
         ((A,), (), 0.5), ((B,), (E - 36,), 1), ((E,), (E - 24, G - 24 + 1), 0.5),
         ((G + 1,), (), 0.5), ((B,), (), 0.5), ((C + 12,), (A - 36,), 1.5)],
    ),
}


class _Handle:
    def close(self) -> None:
        pass


class _SilentInput:
    """A connected keyboard that never sends anything."""

    def list_input_ports(self):
        return ["Demo keyboard"]

    def open_input(self, port):
        return _Handle()

    def iter_pending(self, handle):
        return iter(())


class _SilentOutput:
    """A connected sound module that discards every message."""

    def list_output_ports(self):
        return ["Demo keyboard"]

    def open_output(self, port):
        return _Handle()

    def send_note_on(self, output, channel, note, velocity):
        pass

    def send_note_off(self, output, channel, note, release_velocity):
        pass

    def send_control_change(self, output, channel, control, value):
        pass

    def close_output(self, output):
        pass


def _write_song(path: Path, bpm: int, steps) -> None:
    ticks = 480
    file = mido.MidiFile(type=1, ticks_per_beat=ticks)
    conductor = mido.MidiTrack()
    conductor.append(mido.MetaMessage("track_name", name=path.stem))
    conductor.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm)))
    conductor.append(mido.MetaMessage("end_of_track", time=0))
    file.tracks.append(conductor)
    tracks = {"Right hand": [], "Left hand": []}
    now = 0
    for right, left, beats in steps:
        length = int(beats * ticks)
        for name, notes in (("Right hand", right), ("Left hand", left)):
            for note in notes:
                tracks[name].append((now, "note_on", note))
                tracks[name].append((now + length - 10, "note_off", note))
        now += length
    for name, events in tracks.items():
        track = mido.MidiTrack()
        track.append(mido.MetaMessage("track_name", name=name))
        last = 0
        for at, kind, note in sorted(events, key=lambda e: (e[0], e[1] == "note_on")):
            velocity = 72 if kind == "note_on" else 0
            track.append(mido.Message(kind, note=note, velocity=velocity, time=at - last))
            last = at
        track.append(mido.MetaMessage("end_of_track", time=0))
        file.tracks.append(track)
    file.save(path)


def _build_learn(pianio: Path, root: Path) -> tuple[Path, Path, Path]:
    from piano_core.learn.ingest import ingest
    from piano_core.packages.importer import PackageImporter
    from piano_core.packages.store import PackageStore

    sources = root / "learn-sources"
    (sources / "upstream").mkdir(parents=True)
    (sources / "source-b").mkdir()
    (sources / "upstream" / "pianio").symlink_to(pianio.resolve())
    (sources / "source-b" / "sourceb_matches.json").write_text("[]\n")
    commit = subprocess.run(
        ["git", "-C", str(pianio), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False,
    ).stdout.strip() or None
    build = root / "learn-build"
    result = ingest(sources, build, upstream_commit=commit)
    print(f"learn catalog: {result.published} lessons, {result.courses} courses", flush=True)

    packages_root, packages_db = root / "packages", root / "packages.sqlite3"
    packages_root.mkdir()
    importer = PackageImporter(str(packages_root), PackageStore(str(packages_db)))
    for folder in sorted((build / "packages").iterdir()):
        importer.import_from_directory(folder)
    return result.catalog_path, packages_root, packages_db


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--pianio", type=Path, help="Pianio checkout for the Learn catalog")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="piano-core-demo-") as temp:
        root = Path(temp)
        library_root = root / "midi"
        library_root.mkdir()
        for title, (bpm, steps) in _SONGS.items():
            _write_song(library_root / f"{title}.mid", bpm, steps)
        LibraryService(LibraryConfig(str(library_root), str(root / "index.sqlite"))).scan()
        library_root.chmod(0o555)  # the runtime only accepts a read-only source
        identity = root / "identity"
        identity.mkdir(mode=0o700)
        initialize_identity(str(identity))

        env = {
            "MODE": "test", "LOG_LEVEL": "WARNING",
            "MIDI_INPUT_ENABLED": "true", "MIDI_INPUT_PORT_MATCHER": "Demo",
            "MIDI_OUTPUT_ENABLED": "true", "MIDI_OUTPUT_PORT_MATCHER": "Demo",
            "LIBRARY_ENABLED": "true", "LIBRARY_ROOT": str(library_root),
            "LIBRARY_INDEX_PATH": str(root / "index.sqlite"),
            "API_ENABLED": "true", "API_HOST": "127.0.0.1", "API_PORT": str(args.port),
            "UI_ENABLED": "true", "LED_ENABLED": "false",
            "DEVICE_IDENTITY_DIR": str(identity),
        }
        if args.pianio:
            catalog, packages_root, packages_db = _build_learn(args.pianio, root)
            env.update({
                "PACKAGES_ENABLED": "true", "PACKAGES_ROOT": str(packages_root),
                "PACKAGES_DB_PATH": str(packages_db),
                "LEARN_ENABLED": "true", "LEARN_DB_PATH": str(catalog),
            })

        app = Application.from_env(
            {"PIANO_CORE_" + k: v for k, v in env.items()},
            midi_backend_factory=_SilentInput,
            midi_output_backend_factory=_SilentOutput,
            playback_lock_path=str(root / "activity.lock"),
        )
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        app.start()
        print(f"Piano Core demo: http://127.0.0.1:{args.port}/ui/", flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            app.shutdown()
            library_root.chmod(0o755)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
