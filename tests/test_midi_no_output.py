"""PIANO-P007 boundary tests: contained output, protected input.

History: PIANO-P004 established the MIDI layer as INPUT ONLY and these
checks enforced that. PIANO-P007 sanctioned a minimal canonical OUTPUT
surface in designated modules, so the boundary became:

- the INPUT surface (``input.py``, the input backend class, capture,
  events) must never gain an output/send capability;
- output-capable code may exist ONLY in the designated modules
  (``output.py``, ``output_events.py``, and the output backend class in
  ``backends.py``);
- raw mido/rtmidi imports stay inside the ``piano_core.midi`` package;
- playback machinery stays inside the designated playback package.

PIANO-P009 update: the P009 Playback Core sanctions a second raw-mido
site (``piano_core/playback/timeline.py``, the SMF loader, lazy import)
and confines ALL playback machinery to ``piano_core/playback/`` — every
other module, including the whole protected input surface, stays
forbidden. Playback is composed ONLY of the engine + the canonical
output adapter; nothing here allows input->output routing.

PIANO-P010 update: the Song Library Foundation sanctions a third raw-mido
site (``piano_core/library/metadata.py``, read-only metadata extraction,
lazy import) and confines library machinery to ``piano_core/library/``.
The library READS metadata only: no output/send surface, no StateStore
access, no playback scheduling (its own boundary tests live in
``tests/test_library.py``); the protected input surface stays forbidden
to it as everywhere else.

PIANO-P012 update: the Control API sanctions one token site —
``piano_core/api/errors.py`` may reference the typed
``MidiFileError`` CLASS (imported to map it deterministically onto the
HTTP error model); the API never interprets MIDI files, never imports
mido/rtmidi, never touches the output adapter or the engine directly
(everything flows through the canonical PlaybackManager), never mutates
the StateStore, and can never reach application lifecycle APIs. Dedicated
API boundary tests below enforce all of this.
"""

import glob
import inspect
import unittest
from pathlib import Path

from piano_core.midi.backends import MidoBackend, MidoOutputBackend
from piano_core.midi.input import MidiInputAdapter

_MIDI_DIR = Path(__file__).resolve().parent.parent / "src" / "piano_core" / "midi"
_PLAYBACK_DIR = (
    Path(__file__).resolve().parent.parent / "src" / "piano_core" / "playback"
)
_SRC_DIR = _MIDI_DIR.parent

# Output-capable constructs; only designated modules may contain them.
_OUTPUT_TOKENS = (
    "open_output",
    "OutputPort",
    ".send(",
    "send_message",
    "send_note_on",
    "send_note_off",
    "send_control_change",
    "play_note",
)

#: Modules where the sanctioned PIANO-P007 output surface may live.
_OUTPUT_ALLOWED_MODULES = frozenset(
    {"backends.py", "output.py", "output_events.py"}
)

# Playback machinery that must not exist anywhere yet.
_PLAYBACK_TOKENS = (
    "MidiFile",
    "play_midi",
    "midifile",
    "asyncio",
    "Scheduler",
)


class InputBoundaryTests(unittest.TestCase):
    """The input surface stays strictly input-only (PIANO-P004 rule)."""

    def test_input_module_sources_contain_no_output_constructs(self):
        text = (_MIDI_DIR / "input.py").read_text(encoding="utf-8")
        offenders = [token for token in _OUTPUT_TOKENS if token in text]
        self.assertEqual(offenders, [])

    def test_input_class_surfaces_have_no_send_or_output_members(self):
        for cls in (MidoBackend, MidiInputAdapter):
            for name in dir(cls):
                lowered = name.lower()
                for token in ("send", "output", "write", "play"):
                    self.assertFalse(
                        token in lowered,
                        f"{cls.__name__}.{name} looks like an output surface",
                    )

    def test_adapter_callback_is_inbound_only(self):
        """The only event path is the inbound callback (constructor arg)."""
        params = inspect.signature(MidiInputAdapter.__init__).parameters
        self.assertIn("event_callback", params)
        for name in params:
            self.assertNotIn("output", name.lower())

    def test_non_output_modules_contain_no_output_constructs(self):
        for path in sorted(glob.glob(str(_MIDI_DIR / "*.py"))):
            name = Path(path).name
            if name in _OUTPUT_ALLOWED_MODULES:
                continue
            text = Path(path).read_text(encoding="utf-8")
            offenders = [token for token in _OUTPUT_TOKENS if token in text]
            self.assertEqual(
                offenders, [], f"{name} must not gain an output surface"
            )


class OutputContainmentTests(unittest.TestCase):
    """The sanctioned P007 output surface stays in its designated modules."""

    def test_output_tokens_confined_to_designated_modules(self):
        confined = []
        for path in sorted(glob.glob(str(_MIDI_DIR / "*.py"))):
            name = Path(path).name
            text = Path(path).read_text(encoding="utf-8")
            present = [token for token in _OUTPUT_TOKENS if token in text]
            if not present:
                continue
            if name not in _OUTPUT_ALLOWED_MODULES:
                confined.append(f"{name}: {present}")
        self.assertEqual(confined, [])

    def test_raw_mido_imports_stay_inside_midi_package_and_timeline_loader(self):
        """Raw mido/rtmidi only in the MIDI package + the P009 SMF loader."""
        offenders = []
        for path in sorted(
            glob.glob(str(_SRC_DIR / "**" / "*.py"), recursive=True)
        ):
            rel = Path(path).relative_to(_SRC_DIR).as_posix()
            if rel.startswith("midi/"):
                continue
            if rel in ("playback/timeline.py",  # PIANO-P009 sanctioned site
                       "library/metadata.py"):  # PIANO-P010 sanctioned site
                continue
            text = Path(path).read_text(encoding="utf-8")
            for token in ("import mido", "import rtmidi", "from mido", "from rtmidi"):
                if token in text:
                    offenders.append(f"{rel}: {token!r}")
        self.assertEqual(offenders, [])

    def test_output_backend_surface_is_output_only(self):
        """MidoOutputBackend exposes no input-side members."""
        for name in dir(MidoOutputBackend):
            lowered = name.lower()
            for token in ("open_input", "list_input", "iter_pending"):
                self.assertNotIn(
                    token, lowered, f"MidoOutputBackend.{name} looks like input"
                )

    def test_playback_machinery_confined_to_playback_package(self):
        """PIANO-P009/P010/P012: playback machinery ONLY in sanctioned spots.

        Rescoped from the P007-era ``test_no_playback_machinery_exists``
        (which forbade playback everywhere): the Playback Core lives in
        ``piano_core/playback/``; the P010 library may mention SMF/MidiFile
        constructs ONLY inside ``piano_core/library/`` (read-only metadata
        extraction); the P012 API may reference the typed ``MidiFileError``
        CLASS ONLY inside ``piano_core/api/errors.py`` (deterministic HTTP
        error mapping — no file interpretation). Every other module —
        including the whole input surface — stays forbidden.
        """
        offenders = []
        for path in sorted(
            glob.glob(str(_SRC_DIR / "**" / "*.py"), recursive=True)
        ):
            rel = Path(path).relative_to(_SRC_DIR).as_posix()
            if rel.startswith("playback/") or rel.startswith("library/"):
                continue
            if rel == "api/errors.py":
                continue  # PIANO-P012 sanctioned site (typed error mapping)
            text = Path(path).read_text(encoding="utf-8")
            for token in _PLAYBACK_TOKENS:
                if token in text:
                    offenders.append(f"{rel}: {token!r}")
        self.assertEqual(offenders, [])

    def test_playback_package_has_no_input_or_statestore_surface(self):
        """The playback package must never reference input events or state."""
        offenders = []
        for path in sorted(glob.glob(str(_PLAYBACK_DIR / "*.py"))):
            text = Path(path).read_text(encoding="utf-8")
            for token in (
                "from piano_core.midi.events",
                "piano_core.state",
                "StateStore(",
                "apply_event(",
                "MidiInputAdapter(",
                "open_input(",
                "iter_pending(",
            ):
                if token in text:
                    offenders.append(f"{Path(path).name}: {token!r}")
        self.assertEqual(offenders, [])


class ApiBoundaryTests(unittest.TestCase):
    """The P012 API stays a pure control surface (no MIDI, no lifecycle)."""

    _API_DIR = _SRC_DIR / "api"

    def test_api_package_never_touches_midi_or_output_directly(self):
        """The API must go through canonical managers, never MIDI/engines."""
        offenders = []
        for path in sorted(glob.glob(str(self._API_DIR / "*.py"))):
            text = Path(path).read_text(encoding="utf-8")
            for token in (
                "MidiOutputAdapter",
                "MidiInputAdapter",
                "PlaybackEngine",
                "open_output",
                ".send(",
                "import mido",
                "from mido",
                "import rtmidi",
                "from rtmidi",
            ):
                if token in text:
                    offenders.append(f"{Path(path).name}: {token!r}")
        self.assertEqual(offenders, [])

    def test_api_never_mutates_statestore_or_lifecycle(self):
        """Read-only over state; request_stop is unreachable from the API."""
        offenders = []
        for path in sorted(glob.glob(str(self._API_DIR / "*.py"))):
            text = Path(path).read_text(encoding="utf-8")
            for token in (
                "apply_event(",
                "set_midi_connection(",
                "request_stop",
                "signal.",
            ):
                if token in text:
                    offenders.append(f"{Path(path).name}: {token!r}")
        self.assertEqual(offenders, [])

    def test_api_accepts_no_filesystem_paths(self):
        """No route/field accepts client-supplied paths (song_id only)."""
        offenders = []
        for path in sorted(glob.glob(str(self._API_DIR / "*.py"))):
            text = Path(path).read_text(encoding="utf-8")
            for token in ('"path"', "'path'", "file_path", "filepath"):
                if token in text:
                    offenders.append(f"{Path(path).name}: {token!r}")
        self.assertEqual(offenders, [])

    def test_panic_is_not_exposed_through_the_api(self):
        """PANIC is intentionally not reachable (global MIDI silence)."""
        from piano_core.api.handlers import ROUTES

        for route in ROUTES:
            self.assertNotIn(
                "panic",
                route.pattern.lower(),
                "PANIC must not be exposed as an API route",
            )


if __name__ == "__main__":
    unittest.main()
