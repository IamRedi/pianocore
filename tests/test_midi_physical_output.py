"""Real installed Mido/RtMidi path, fake only native hardware objects."""
import importlib.metadata
import json
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import mido
import rtmidi

from piano_core.midi.backends import MidoOutputBackend
from piano_core.midi.output import MidiOutputAdapter
from piano_core.midi.output_events import note_on, sustain
from tests.test_midi_input import wait_for


class NativeBoundary:
    def __init__(self, ports, api=rtmidi.API_LINUX_ALSA):
        self.ports = ports
        self.api = api
        self.outputs = []
        self.naming_error = None

    def output(self, **kwargs):
        boundary = self

        class Output:
            def __init__(self):
                self.calls = []
                self.client_name = kwargs.get("name")
                self.sent = []
                self.closed = False
                self.deleted = False
                self.connected = True

            def get_ports(self):
                return list(boundary.ports)

            def get_current_api(self):
                return boundary.api

            def open_port(self, index):
                self.calls.append(("open_port", index))

            def open_virtual_port(self, name):
                self.calls.append(("open_virtual_port", name))

            def set_client_name(self, name):
                self.calls.append(("set_client_name", name))
                if boundary.naming_error:
                    raise boundary.naming_error
                self.client_name = name

            def send_message(self, message):
                self.sent.append(list(message))

            def close_port(self):
                self.closed = True

            def delete(self):
                self.deleted = True

        output = Output()
        self.outputs.append(output)
        return output

    def input(self, **kwargs):
        class Input:
            def get_ports(self):
                return []

            def delete(self):
                pass
        return Input()

    def installed(self):
        stack = ExitStack()
        # Mido functions, Backend, Output and _open_port remain real.
        stack.enter_context(patch.object(rtmidi, "MidiOut", self.output))
        stack.enter_context(patch.object(rtmidi, "MidiIn", self.input))
        return stack


class PhysicalOutputTests(unittest.TestCase):
    def test_real_backend_subscription_loss_reopens_physical_once_and_cleans_owned(self):
        boundary = NativeBoundary(["Other 31:0", "Roland OUT 48:7"])
        backend = MidoOutputBackend()
        adapter = MidiOutputAdapter(backend)
        transitions = []
        real_read = Path.read_text
        def snapshot(path, *args, **kwargs):
            if str(path) != '/proc/asound/seq/clients':
                return real_read(path, *args, **kwargs)
            return ''.join(f'Client {128+i} : "{x.client_name}" [User Legacy]\n'
                           + ('    Connecting To: 48:7\n' if x.connected else '')
                           for i,x in enumerate(boundary.outputs)
                           if x.calls and not x.closed)
        with boundary.installed(), patch.object(Path, 'read_text', snapshot):
            adapter.open()
            old = boundary.outputs[-1]
            adapter.apply(note_on(2, 64, 90))
            adapter.apply(sustain(2, 127))
            adapter.start_monitor(transitions.append, interval=0.01)
            try:
                old.connected = False  # Same discovered name, absent native subscription.
                self.assertTrue(wait_for(lambda:old.closed and adapter.is_open, 2))
                fresh = next(x for x in reversed(boundary.outputs) if x.calls and not x.closed)
                self.assertEqual([x for x in fresh.calls if x[0].startswith('open_')],
                                 [('open_port',1)])
                self.assertEqual(fresh.sent, [[0x81,64,0],[0xB1,64,0]])
                self.assertEqual(adapter.owned_notes, ())
                self.assertEqual(adapter.owned_sustain_channels, ())
                self.assertTrue(wait_for(lambda:transitions == [False,True],2))
                self.assertEqual(sum(bool(x.calls) and not x.closed for x in boundary.outputs),1)
                adapter.apply(note_on(2,67,90))
                self.assertEqual(fresh.sent[-1], [0x91,67,90])
                self.assertEqual(fresh.sent.count([0x91,67,90]),1)
            finally:
                adapter.stop_monitor()
                adapter.stop_automated_notes()
                adapter.close()

    def test_real_mido_discovery_selection_physical_open(self):
        self.assertEqual(importlib.metadata.version("mido"), "1.3.3")
        self.assertEqual(mido.backend.name, "mido.backends.rtmidi")
        ports = ["Other:Other 31:0", "Unused:Unused 32:0", "Roland:Chosen OUT 48:7"]
        boundary = NativeBoundary(ports)
        backend = MidoOutputBackend()
        adapter = MidiOutputAdapter(backend)
        with boundary.installed():
            selected = adapter.open()
            native = boundary.outputs[-1]
            opening = [c for c in native.calls if c[0].startswith("open_")]
            print(json.dumps({"mido": importlib.metadata.version("mido"),
                  "backend": mido.backend.name, "discovery": ports,
                  "selected": selected, "expected_index": 2,
                  "native_calls": native.calls}), flush=True)
            try:
                self.assertEqual(selected, ports[2])
                self.assertEqual(opening, [("open_port", 2)])
                self.assertEqual(native.client_name, backend._client_name)
                adapter.apply(note_on(1, 67, 80))
                self.assertEqual(native.sent, [[0x90, 67, 80]])
            finally:
                adapter.close()
            self.assertTrue(native.closed and native.deleted)

    def test_rediscovery_changes_index_without_hardcoding_or_duplicate_send(self):
        boundary = NativeBoundary(["Other 31:0", "Roland OUT 48:7"])
        adapter = MidiOutputAdapter(MidoOutputBackend())
        with boundary.installed():
            for cycle, index in enumerate([1, 0, 2]):
                boundary.ports = (["Other 31:0"] * index) + ["Roland OUT 48:7"]
                adapter.open()
                native = boundary.outputs[-1]
                self.assertEqual([c for c in native.calls if c[0].startswith("open_")],
                                 [("open_port", index)])
                adapter.apply(note_on(1, 60 + cycle, 80))
                adapter.stop_automated_notes()
                self.assertEqual(native.sent, [[0x90, 60 + cycle, 80], [0x80, 60 + cycle, 0]])
                adapter.close()
                self.assertTrue(native.closed and native.deleted)
            self.assertTrue(all(x.deleted for x in boundary.outputs))
            self.assertEqual(sum(bool(x.calls) and not x.closed for x in boundary.outputs), 0)

    def test_alsa_identity_failure_closes_physical_handle_and_propagates(self):
        boundary = NativeBoundary(["Roland OUT 48:7"])
        boundary.naming_error = ValueError("identity configuration fault")
        with boundary.installed():
            with self.assertRaisesRegex(ValueError, "identity configuration fault"):
                MidoOutputBackend().open_output(boundary.ports[0])
            native = boundary.outputs[-1]
            self.assertEqual(native.calls[0], ("open_port", 0))
            self.assertTrue(native.closed and native.deleted)

    def test_non_alsa_physical_open_does_not_use_alsa_only_naming(self):
        boundary = NativeBoundary(["Roland OUT"], rtmidi.API_UNIX_JACK)
        with boundary.installed():
            output = MidoOutputBackend().open_output(boundary.ports[0])
            native = boundary.outputs[-1]
            self.assertEqual(native.calls, [("open_port", 0)])
            output.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
