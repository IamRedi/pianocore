"""Disposable real Application. Only MIDI hardware boundary is synthetic.

JSON lines on stdin inject raw mido messages through MidiInputAdapter. No API,
score, target, grading, progression, library or transport implementations mocked.
EOF/SIGTERM shuts down this process's Application and disposable resources.
"""
import json
import queue
import signal
import socket
import sys
import tempfile
from pathlib import Path

import mido
from piano_core.app import Application
from piano_core.device_identity import initialize_identity
from piano_core.library.service import LibraryConfig, LibraryService


class InputHandle:
    def close(self):
        pass


class InputBackend:
    def __init__(self):
        self.messages = queue.Queue()
        self.connected = True
        self.read_error = False

    def list_input_ports(self):
        return ["Functional test input"] if self.connected else []

    def open_input(self, port):
        return InputHandle()

    def iter_pending(self, handle):
        if self.read_error:
            self.read_error = False
            raise OSError("Explicit synthetic receive fault")
        if not self.connected:
            return  # Endpoint disappearance is detected by the real adapter port probe.
        while not self.messages.empty():
            yield self.messages.get_nowait()


class OutputBackend:
    def __init__(self):
        self.sent = []

    def list_output_ports(self):
        return ["Functional test output"]

    def open_output(self, port):
        return object()

    def send_note_on(self, output, channel, note, velocity):
        self.sent.append(["note_on", channel, note, velocity])

    def send_note_off(self, output, channel, note, release_velocity):
        self.sent.append(["note_off", channel, note, release_velocity])

    def send_control_change(self, output, channel, control, value):
        self.sent.append(["control_change", channel, control, value])

    def close_output(self, output):
        pass


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def midi(path, groups, tail=0):
    file = mido.MidiFile(type=1, ticks_per_beat=480)
    track = mido.MidiTrack()
    file.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name=path.stem))
    track.append(mido.MetaMessage("set_tempo", tempo=500000))
    for group_index, group in enumerate(groups):
        for i, note in enumerate(group):
            track.append(mido.Message("note_on", note=note, velocity=80, time=240 if i == 0 else 0))
        for i, note in enumerate(group):
            track.append(mido.Message("note_off", note=note, time=(240 + (tail if group_index == len(groups) - 1 else 0)) if i == 0 else 0))
    track.append(mido.MetaMessage("end_of_track", time=0))
    file.save(path)


def main():
    with tempfile.TemporaryDirectory(prefix="piano-functional-") as temp:
        root = Path(temp)
        source = root / "midi"
        source.mkdir()
        midi(source / "01-short.mid", [[60]], tail=0)
        midi(source / "02-repeated.mid", [[60], [60], [62]], tail=9600)
        midi(source / "03-chords.mid", [[60, 64, 67], [62, 65, 69]], tail=9600)
        midi(source / "04-other.mid", [[65], [67]], tail=9600)
        (source / "05-invalid.mid").write_bytes(b"invalid MIDI fixture\n")
        for i in range(60):
            midi(source / f"page-{i:02}.mid", [[60]], tail=9600)
        library = LibraryService(LibraryConfig(str(source), str(root / "index.sqlite")))
        library.scan()
        identity = root / "identity"
        identity.mkdir(mode=0o700)
        initialize_identity(str(identity))
        source.chmod(0o555)
        incoming, outgoing = InputBackend(), OutputBackend()
        api_port, led_port = port(), port()
        env = {
            "MODE": "test", "LOG_LEVEL": "WARNING", "MIDI_INPUT_ENABLED": "true",
            "MIDI_INPUT_PORT_MATCHER": "Functional", "MIDI_OUTPUT_ENABLED": "true",
            "MIDI_OUTPUT_PORT_MATCHER": "Functional", "LIBRARY_ENABLED": "true",
            "LIBRARY_ROOT": str(source), "LIBRARY_INDEX_PATH": str(root / "index.sqlite"),
            "API_ENABLED": "true", "API_HOST": "127.0.0.1", "API_PORT": str(api_port),
            "UI_ENABLED": "true", "LED_ENABLED": "true", "LED_WS_HOST": "127.0.0.1",
            "LED_WS_PORT": str(led_port), "LED_MODE": "all", "LED_BRIGHTNESS": "80",
            "DEVICE_IDENTITY_DIR": str(identity),
        }
        app = Application.from_env({"PIANO_CORE_" + k: v for k, v in env.items()},
            midi_backend_factory=lambda: incoming, midi_output_backend_factory=lambda: outgoing,
            playback_lock_path=str(root / "activity.lock"))
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        try:
            app.start()
            if not app.api_server or not app.library_ready:
                raise RuntimeError("Isolated real API/library failed startup; no fallback")
            print(json.dumps({"ready": True, "origin": f"http://127.0.0.1:{api_port}",
                "led_port": led_port, "backend": "real Application", "io": "synthetic MIDI hardware adapters"}), flush=True)
            for line in sys.stdin:
                command = json.loads(line)
                action = command["action"]
                if action == "midi":
                    incoming.messages.put(mido.Message(**command["message"]))
                elif action == "connection":
                    incoming.connected = command["connected"]
                elif action == "read_error":
                    incoming.read_error = True
                elif action != "output":
                    raise ValueError("unknown fixture action")
                print(json.dumps({"id": command["id"], "sent": outgoing.sent if action == "output" else None}), flush=True)
        finally:
            app.shutdown()
            source.chmod(0o755)


if __name__ == "__main__":
    main()
