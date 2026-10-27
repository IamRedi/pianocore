"""PIANO-P014: LED WebSocket transport tests over real sockets.

The transport is a single-client RFC 6455 server speaking the exact
handshake/frame subset the ESP32 WebSockets client (links2004/WebSockets
2.6.1) uses: client frames masked, server frames unmasked, text frames,
no heartbeat required.
"""

import base64
import os
import socket
import struct
import time
import unittest

from piano_core.led.protocol import CLIENT_MAX_MESSAGE_BYTES
from piano_core.led.transport import LedTransport

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _handshake_request() -> bytes:
    key = base64.b64encode(os.urandom(16)).decode()
    return key, (
        "GET / HTTP/1.1\r\n"
        "Host: test\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    ).encode("ascii")


def _masked_frame(opcode: int, payload: bytes) -> bytes:
    mask = os.urandom(4)
    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length <= 0xFFFF:
        header.append(0x80 | 126)
        header.extend(struct.pack(">H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack(">Q", length))
    header.extend(mask)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return bytes(header) + masked


def _read_exact(sock: socket.socket, count: int) -> bytes:
    data = bytearray()
    sock.settimeout(5.0)
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise ConnectionError("closed")
        data.extend(chunk)
    return bytes(data)


def _read_frame(sock: socket.socket) -> tuple[int, bytes]:
    header = _read_exact(sock, 2)
    opcode = header[0] & 0x0F
    length = header[1] & 0x7F
    if length == 126:
        length = struct.unpack(">H", _read_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", _read_exact(sock, 8))[0]
    return opcode, _read_exact(sock, length)


class _ConnectedClient:
    """A minimal WS client mirroring the ESP32's on-wire behavior."""

    def __init__(self, port: int, expect_handshake: bool = True) -> None:
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5.0)
        self.response = b""
        if not expect_handshake:
            return
        _, request = _handshake_request()
        try:
            self.sock.sendall(request)
            response = bytearray()
            while b"\r\n\r\n" not in response:
                response.extend(self.sock.recv(4096))
            self.response = bytes(response)
        except (ConnectionError, OSError):
            pass  # refused clients see a reset; that IS the refusal


class LedTransportTests(unittest.TestCase):
    def setUp(self):
        self.messages: list[str] = []
        self.connected = []
        self.disconnected = []
        self.transport = LedTransport(
            "127.0.0.1",
            0,
            on_message=self.messages.append,
            on_connect=lambda: self.connected.append(True),
            on_disconnect=lambda: self.disconnected.append(True),
        )
        self.transport.start()
        self.port = self.transport.bound_address[1]
        self.addCleanup(self.transport.stop)

    def test_start_binds_and_reports_address(self):
        self.assertEqual(self.transport.bound_address[0], "127.0.0.1")
        self.assertGreater(self.port, 0)
        self.assertFalse(self.transport.client_connected)

    def test_handshake_and_single_client(self):
        client = _ConnectedClient(self.port)
        self.addCleanup(client.sock.close)
        self.assertIn("101 Switching Protocols", client.response.decode())
        self.assertIn(b"Sec-WebSocket-Accept:", client.response)

        # Await the on_connect callback (fires after client_connected is
        # set; a plain client_connected poll would race it).
        deadline = time.monotonic() + 5.0
        while not self.connected and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.connected, [True])
        self.assertTrue(self.transport.client_connected)

    def test_server_send_text_reaches_client(self):
        client = _ConnectedClient(self.port)
        self.addCleanup(client.sock.close)
        deadline = time.monotonic() + 5.0
        while not self.transport.client_connected and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.transport.send_text('{"type":"playback"}'))
        opcode, payload = _read_frame(client.sock)
        self.assertEqual(opcode, 0x1)
        self.assertEqual(payload, b'{"type":"playback"}')

    def test_client_text_message_parsed(self):
        client = _ConnectedClient(self.port)
        self.addCleanup(client.sock.close)
        client.sock.sendall(
            _masked_frame(0x1, b'{"type":"get_state"}')
        )
        deadline = time.monotonic() + 5.0
        while not self.messages and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.messages, ['{"type":"get_state"}'])

    def test_second_client_refused(self):
        first = _ConnectedClient(self.port)
        self.addCleanup(first.sock.close)
        deadline = time.monotonic() + 5.0
        while not self.transport.client_connected and time.monotonic() < deadline:
            time.sleep(0.01)
        # The second client completes TCP connect but is closed instead of
        # upgraded (expect_handshake: the reset/close IS the refusal).
        second = _ConnectedClient(self.port, expect_handshake=False)
        self.addCleanup(second.sock.close)
        second.sock.sendall(b"GET / HTTP/1.1\r\nHost: t\r\n\r\n")
        second.sock.settimeout(2.0)
        refused = False
        try:
            data = second.sock.recv(4096)
            refused = not data or b"101" not in data
        except (ConnectionError, OSError):
            refused = True
        self.assertTrue(refused)
        time.sleep(0.1)
        self.assertTrue(self.transport.client_connected)

    def test_unmasked_client_frame_closes_connection(self):
        client = _ConnectedClient(self.port)
        self.addCleanup(client.sock.close)
        deadline = time.monotonic() + 5.0
        while not self.transport.client_connected and time.monotonic() < deadline:
            time.sleep(0.01)
        # Raw unmasked text frame (protocol violation, RFC 6455).
        client.sock.sendall(b"\x81\x05hello")
        deadline = time.monotonic() + 5.0
        while self.transport.client_connected and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(self.transport.client_connected)

    def test_oversized_client_frame_closes_connection(self):
        client = _ConnectedClient(self.port)
        self.addCleanup(client.sock.close)
        deadline = time.monotonic() + 5.0
        while not self.transport.client_connected and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertGreater(
            CLIENT_MAX_MESSAGE_BYTES, 125
        )  # extended-length path is exercised
        client.sock.sendall(
            _masked_frame(0x1, b"x" * (CLIENT_MAX_MESSAGE_BYTES + 1))
        )
        deadline = time.monotonic() + 5.0
        while self.transport.client_connected and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(self.transport.client_connected)

    def test_client_close_is_observed(self):
        client = _ConnectedClient(self.port)
        deadline = time.monotonic() + 5.0
        while not self.transport.client_connected and time.monotonic() < deadline:
            time.sleep(0.01)
        client.sock.sendall(_masked_frame(0x8, b""))
        deadline = time.monotonic() + 5.0
        while not self.disconnected and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.disconnected, [True])
        self.assertFalse(self.transport.client_connected)

    def test_send_without_client_returns_false(self):
        self.assertFalse(self.transport.send_text("hello"))

    def test_ping_gets_pong(self):
        client = _ConnectedClient(self.port)
        self.addCleanup(client.sock.close)
        deadline = time.monotonic() + 5.0
        while not self.transport.client_connected and time.monotonic() < deadline:
            time.sleep(0.01)
        client.sock.sendall(_masked_frame(0x9, b"hb"))
        opcode, payload = _read_frame(client.sock)
        self.assertEqual(opcode, 0xA)
        self.assertEqual(payload, b"hb")

    def test_stop_is_idempotent_and_disconnects_client(self):
        client = _ConnectedClient(self.port)
        deadline = time.monotonic() + 5.0
        while not self.transport.client_connected and time.monotonic() < deadline:
            time.sleep(0.01)
        self.transport.stop()
        self.transport.stop()
        self.assertFalse(self.transport.client_connected)
        client.sock.settimeout(2.0)
        closed = False
        try:
            while True:
                if not client.sock.recv(4096):
                    closed = True  # clean EOF
                    break
        except (ConnectionError, OSError):
            closed = True  # reset
        self.assertTrue(closed)

    def test_non_websocket_handshake_rejected(self):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5.0)
        self.addCleanup(sock.close)
        sock.sendall(b"GET / HTTP/1.1\r\nHost: test\r\n\r\n")
        sock.settimeout(2.0)
        # The server must close without a 101 upgrade (EOF or reset).
        rejected = False
        try:
            data = sock.recv(4096)
            rejected = not data or b"101" not in data
        except (ConnectionError, OSError):
            rejected = True
        self.assertTrue(rejected)


if __name__ == "__main__":
    unittest.main()
