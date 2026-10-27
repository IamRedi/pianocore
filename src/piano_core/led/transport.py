"""Minimal RFC 6455 WebSocket server transport for the keyboard LED
client (PIANO-P014, ADR-LED-KEYBOARD-V1 D1).

The ESP32 keyboard controller is a WebSocket CLIENT that dials Piano Core
(see ADR: this inverts the legacy topology — the legacy service owns
port 8766 and is never touched). Implemented on the standard library only
(``socket``/``hashlib``/``base64``/``struct``): zero new runtime
dependencies, offline deployment model preserved.

Boundaries enforced here:

- EXACTLY ONE accepted client (the keyboard strip is one physical device
  with one writer); any additional connection is closed immediately;
- bounded handshake (4 KiB request head), bounded frame payload (1 KiB —
  the protocol is tiny by construction), bounded socket timeouts;
- unmasked client frames and oversized/fragmented/binary frames are
  treated as protocol violations: the connection is closed, never parsed
  further; nothing here can execute anything;
- server->client writes are serialized (one lock) and bounded by the
  socket timeout; a failing send marks the client gone (the ESP32 owns
  reconnection with its 8 s firmware retry).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import socket
import struct
import threading

from piano_core.led.protocol import CLIENT_MAX_MESSAGE_BYTES

__all__ = ["LedTransport"]

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

_OP_TEXT = 0x1
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA

_HANDSHAKE_MAX_BYTES = 4096
_SOCKET_TIMEOUT = 1.0
#: How long one send may block before it is abandoned (LED latency is
#: never allowed to matter; the queue protects the callers anyway).
_SEND_TIMEOUT = 2.0
_STOP_POLL_SECONDS = 1.0


class _ConnectionLost(Exception):
    """Internal: the client connection must be reaped."""


class LedTransport:
    """Single-client RFC 6455 server endpoint (listening socket owned)."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        on_message=None,  # Callable[[str], None]
        on_connect=None,  # Callable[[], None]
        on_disconnect=None,  # Callable[[], None]
        logger: logging.Logger | None = None,
    ) -> None:
        if not isinstance(host, str) or not host:
            raise ValueError("host must be a non-empty string")
        if not isinstance(port, int) or isinstance(port, bool):
            raise ValueError("port must be an int")
        if not 0 <= port <= 65535:
            raise ValueError(f"port out of range: {port!r}")
        self._host = host
        self._port = port
        self._on_message = on_message
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._log = logger or logging.getLogger("piano_core.led.transport")

        self._listener: socket.socket | None = None
        self._client: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._client_lock = threading.Lock()
        self._accept_thread: threading.Thread | None = None
        self._reader_thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._bound_address: tuple[str, int] | None = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    @property
    def bound_address(self) -> tuple[str, int] | None:
        """Resolved (host, port) after a successful ``start()``."""
        return self._bound_address

    @property
    def client_connected(self) -> bool:
        """True while the single LED client connection is present."""
        with self._client_lock:
            return self._client is not None

    def start(self) -> None:
        """Bind, listen, and start the accept loop. Raises ``OSError``."""
        if self._listener is not None:
            raise RuntimeError("led transport already started")
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self._host, self._port))
        listener.listen(2)
        listener.settimeout(_STOP_POLL_SECONDS)
        self._listener = listener
        self._bound_address = (
            listener.getsockname()[0],
            int(listener.getsockname()[1]),
        )
        self._stopping.clear()
        self._accept_thread = threading.Thread(
            target=self._accept_loop, name="piano-core-led-accept", daemon=True
        )
        self._accept_thread.start()
        self._log.info(
            "led transport listening host=%s port=%d", *self._bound_address
        )

    def stop(self) -> None:
        """Close everything and join the accept thread. Idempotent."""
        self._stopping.set()
        listener = self._listener
        self._listener = None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        self._drop_client()
        thread = self._accept_thread
        self._accept_thread = None
        self._reader_thread = None
        if thread is not None:
            thread.join(_STOP_POLL_SECONDS * 3)
        self._bound_address = None
        self._log.info("led transport stopped")

    # ------------------------------------------------------------------
    # sending
    # ------------------------------------------------------------------
    def send_text(self, text: str, timeout: float = _SEND_TIMEOUT) -> bool:
        """Send one text frame to the client; False when absent/failed."""
        if not isinstance(text, str):
            raise TypeError("text must be str")
        with self._client_lock:
            client = self._client
        if client is None:
            return False
        frame = _encode_text_frame(text.encode("utf-8"))
        try:
            with self._send_lock:
                client.settimeout(timeout)
                client.sendall(frame)
            return True
        except (OSError, ValueError) as exc:
            self._log.warning("led send failed, dropping client: %s", exc)
            self._drop_client()
            return False

    # ------------------------------------------------------------------
    # accept / handshake
    # ------------------------------------------------------------------
    def _accept_loop(self) -> None:
        while not self._stopping.is_set():
            listener = self._listener
            if listener is None:
                return
            try:
                conn, addr = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stopping.is_set():
                    return
                self._log.warning("led accept failed; listener closing")
                return
            with self._client_lock:
                busy = self._client is not None
            if busy:
                # One writer by contract: an extra client is refused.
                self._log.info("led client refused (already connected) %s", addr)
                try:
                    conn.close()
                except OSError:
                    pass
                continue
            try:
                self._handshake(conn)
            except (OSError, _HandshakeError) as exc:
                self._log.info("led handshake rejected from %s: %s", addr, exc)
                try:
                    conn.close()
                except OSError:
                    pass
                continue
            with self._client_lock:
                self._client = conn
            self._reader_thread = threading.Thread(
                target=self._reader_loop,
                args=(conn,),
                name="piano-core-led-reader",
                daemon=True,
            )
            self._reader_thread.start()
            self._log.info("led client connected from %s", addr)
            if self._on_connect is not None:
                try:
                    self._on_connect()
                except Exception:  # observers never break the transport
                    self._log.exception("led on_connect observer failed")

    def _handshake(self, conn: socket.socket) -> None:
        conn.settimeout(_SEND_TIMEOUT)
        head = bytearray()
        while b"\r\n\r\n" not in head:
            if len(head) > _HANDSHAKE_MAX_BYTES:
                raise _HandshakeError("handshake head too large")
            try:
                chunk = conn.recv(_HANDSHAKE_MAX_BYTES - len(head))
            except socket.timeout:
                continue
            if not chunk:
                raise _HandshakeError("client closed during handshake")
            head.extend(chunk)
        text = head.decode("latin-1")
        headers = {}
        for line in text.split("\r\n")[1:]:
            if ":" in line:
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()
        if headers.get("upgrade", "").lower() != "websocket":
            raise _HandshakeError("not a websocket upgrade")
        key = headers.get("sec-websocket-key", "")
        if not key:
            raise _HandshakeError("missing Sec-WebSocket-Key")
        accept = base64.b64encode(
            hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()
        ).decode("ascii")
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
        )
        conn.sendall(response.encode("ascii"))

    # ------------------------------------------------------------------
    # reader
    # ------------------------------------------------------------------
    def _reader_loop(self, conn: socket.socket) -> None:
        try:
            while not self._stopping.is_set():
                try:
                    opcode, payload = _read_frame(conn)
                except socket.timeout:
                    continue
                if opcode == _OP_TEXT:
                    if self._on_message is not None and payload:
                        try:
                            self._on_message(payload.decode("utf-8"))
                        except UnicodeDecodeError:
                            pass  # non-UTF8 text: dropped
                elif opcode == _OP_PING:
                    try:
                        with self._send_lock:
                            conn.sendall(_encode_pong(payload))
                    except OSError:
                        raise _ConnectionLost()
                elif opcode in (_OP_CLOSE, _OP_PONG):
                    if opcode == _OP_CLOSE:
                        raise _ConnectionLost()
                else:
                    # binary / continuation / reserved: protocol violation
                    raise _ConnectionLost()
        except (OSError, _ConnectionLost):
            pass
        finally:
            self._drop_client()
            self._log.info("led client disconnected")
            if self._on_disconnect is not None:
                try:
                    self._on_disconnect()
                except Exception:  # observers never break the transport
                    self._log.exception("led on_disconnect observer failed")

    def _drop_client(self) -> None:
        with self._client_lock:
            client = self._client
            self._client = None
        if client is not None:
            try:
                client.close()
            except OSError:
                pass


class _HandshakeError(Exception):
    """A rejected client handshake (bounded, deterministic)."""


def _encode_text_frame(payload: bytes) -> bytes:
    """Server->client text frame (unmasked, single frame)."""
    header = bytearray([0x81])
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length <= 0xFFFF:
        header.append(126)
        header.extend(struct.pack(">H", length))
    else:
        header.append(127)
        header.extend(struct.pack(">Q", length))
    return bytes(header) + payload


def _encode_pong(payload: bytes) -> bytes:
    header = bytearray([0x8A])
    length = len(payload)
    if length < 126:
        header.append(length)
    else:
        # The keyboard client never pings with payload; oversize is dropped.
        header.append(0)
        payload = b""
    return bytes(header) + payload


def _read_exact(conn: socket.socket, count: int) -> bytes:
    """Read exactly ``count`` bytes; raise on EOF (timeouts propagate)."""
    data = bytearray()
    while len(data) < count:
        chunk = conn.recv(count - len(data))
        if not chunk:
            raise _ConnectionLost()
        data.extend(chunk)
    return bytes(data)


def _read_frame(conn: socket.socket) -> tuple[int, bytes]:
    """Read one complete frame; reject everything the protocol forbids."""
    header = _read_exact(conn, 2)
    fin = bool(header[0] & 0x80)
    opcode = header[0] & 0x0F
    masked = bool(header[1] & 0x80)
    length = header[1] & 0x7F
    if not fin:
        raise _ConnectionLost()  # fragmented frames: not part of the protocol
    if length == 126:
        length = struct.unpack(">H", _read_exact(conn, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", _read_exact(conn, 8))[0]
    if length > CLIENT_MAX_MESSAGE_BYTES:
        raise _ConnectionLost()
    if not masked:
        raise _ConnectionLost()  # client->server MUST be masked (RFC 6455)
    mask = _read_exact(conn, 4)
    payload = bytearray(_read_exact(conn, length))
    for i in range(length):
        payload[i] ^= mask[i % 4]
    return opcode, bytes(payload)
