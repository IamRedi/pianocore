"""MIDI backend providers.

Backends own the actual ALSA/mido surface and are injected into the
adapters, so application logic and tests never import mido (tests use
scripted fakes; the real backends import mido lazily).

Input and output are deliberately separate classes on the same proven
mido/python-rtmidi stack (PIANO-P007): :class:`MidoBackend` is INPUT
ONLY (list/open/iterate input ports, no send surface), while
:class:`MidoOutputBackend` is OUTPUT ONLY (list/open output ports and
send canonical primitives). Raw mido messages never leave this module:
output methods accept canonical 1-based channels plus plain numbers and
serialize internally.
"""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path

_LOGGER = logging.getLogger("piano_core.midi.backends")


class OutputOpenCleanupError(RuntimeError):
    """An unreturned output still needs an owner after open cleanup failed."""

    def __init__(self, output, open_error, close_error):
        super().__init__(f"output initialization failed: {open_error!r}; close failed: {close_error!r}")
        self.output = output


def _alsa_output_connected(snapshot: str, client_name: str, destination: str) -> bool:
    """Select exactly our client; never accept another client's subscription."""
    matches = 0
    selected = False
    connected = False
    for line in snapshot.splitlines():
        client = re.match(r'^Client\s+\d+\s+:\s+"([^"]+)"', line)
        if client:
            selected = client.group(1) == client_name
            matches += int(selected)
        elif selected and line.strip().startswith("Connecting To:"):
            connected |= destination in re.findall(r"\b\d+:\d+\b", line)
    return matches == 1 and connected


class MidoBackend:
    """Input backend backed by the proven legacy ``mido``/``python-rtmidi`` stack.

    mido is imported lazily so the rest of the package (and every unit test)
    works without mido installed. This class is INPUT ONLY: only
    ``list_input_ports``, ``open_input`` (input ports only) and
    ``iter_pending``.
    """

    def list_input_ports(self) -> list[str]:
        import mido  # lazy: keeps mido off the local-test path

        ports = list(mido.get_input_names())
        _LOGGER.debug("midi input ports: %s", ports)
        return ports

    def open_input(self, port_name: str):
        """Open the named input port; returns a handle with ``iter_pending``."""
        import mido  # lazy

        midi_in = mido.open_input(port_name)
        _LOGGER.info("opened midi input port: %s", port_name)
        return midi_in

    @staticmethod
    def iter_pending(handle):
        """Yield pending raw messages from an open input port."""
        yield from handle.iter_pending()


def _default_output_message_factory(msg_type: str, **parameters):
    """Build one raw backend message (the only mido construction site)."""
    import mido  # lazy: keeps mido off the local-test path

    return mido.Message(msg_type, **parameters)


class MidoOutputBackend:
    """Output backend on the same proven ``mido``/``python-rtmidi`` stack.

    OUTPUT ONLY (PIANO-P007). Methods accept canonical primitives (1-based
    channels, plain ints) and serialize to raw backend messages via the
    injectable ``message_factory``; callers never see mido objects.
    Canonical channels 1..16 map to backend channels 0..15.
    """

    def __init__(
        self, message_factory=_default_output_message_factory
    ) -> None:
        self._message_factory = message_factory
        self._client_name = f"piano-core-output-{uuid.uuid4().hex}"

    def list_output_ports(self) -> list[str]:
        import mido  # lazy

        ports = list(mido.get_output_names())
        _LOGGER.debug("midi output ports: %s", ports)
        return ports

    def open_output(self, port_name: str):
        """Open the named output port; returns an opaque send handle."""
        import mido  # lazy

        # Mido 1.3.3 treats client_name as virtual=True in _open_port,
        # even when virtual=False is supplied. Open the discovered physical
        # endpoint first, then name its ALSA client for subscription health.
        output = mido.open_output(port_name)
        try:
            if output.api == "LINUX_ALSA":
                # Mido exposes no client-rename method; its pinned RtMidi
                # handle has the ALSA-only public set_client_name API.
                output._rt.set_client_name(self._client_name)
        except Exception as open_error:
            try:
                self.close_output(output)
            except Exception as close_error:
                raise OutputOpenCleanupError(output, open_error, close_error) from close_error
            raise
        _LOGGER.info("opened midi output port: %s", port_name)
        return output

    def is_output_connected(self, output) -> bool:
        """Check the actual ALSA subscription, not just RtMidi's open flag.

        USB can remove a subscription while the handle remains open and the
        same endpoint name subsequently returns. A unique client name binds
        this read-only kernel snapshot to this backend's output only.
        """
        if output.closed:
            return False
        if output.api != "LINUX_ALSA":
            return True
        destination = re.search(r" (\d+:\d+)$", output.name)
        if destination is None:
            raise ValueError("ALSA output endpoint has no client:port identity")
        return _alsa_output_connected(
            Path("/proc/asound/seq/clients").read_text(),
            self._client_name,
            destination.group(1),
        )

    def send_note_on(self, output, channel: int, note: int, velocity: int) -> None:
        output.send(
            self._message_factory(
                "note_on", channel=channel - 1, note=note, velocity=velocity
            )
        )

    def send_note_off(
        self, output, channel: int, note: int, release_velocity: int
    ) -> None:
        output.send(
            self._message_factory(
                "note_off",
                channel=channel - 1,
                note=note,
                velocity=release_velocity,
            )
        )

    def send_control_change(
        self, output, channel: int, control: int, value: int
    ) -> None:
        output.send(
            self._message_factory(
                "control_change",
                channel=channel - 1,
                control=control,
                value=value,
            )
        )

    def close_output(self, output) -> None:
        """Confirm release or raise; failure never transfers ownership away.

        RtMidi1.5.8 exposes is_deleted without dereferencing its native
        pointer. A previous close may have deleted it before raising;
        never invoke Mido._close on that deleted pointer again.
        Otherwise Mido.close retries the documented repeatable physical
        close_port followed by delete. Exceptions remain visible.
        """
        native = getattr(output, "_rt", None)
        if native is not None and getattr(native, "is_deleted", False) is True:
            output.closed = True
            return
        output.close()
        if not output.closed:
            raise RuntimeError("output close returned without confirming release")
        if native is not None and getattr(native, "is_deleted", None) is False:
            raise RuntimeError("output wrapper closed but native release is unconfirmed")
