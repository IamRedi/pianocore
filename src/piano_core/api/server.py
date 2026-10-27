"""HTTP server for the local control API (PIANO-P012).

Stack decision (recorded in docs/decisions/ADR-CONTROL-API-V1.md): the
mature Python standard library ``http.server.ThreadingHTTPServer`` — ONE
thread per connection, bounded request handling, clean shutdown, and ZERO
new runtime dependencies, preserving the appliance's offline/reproducible
deployment model.

Boundaries enforced here:

- the API is a typed request/response boundary: every response is a
  deterministic JSON document with an explicit Content-Length; every
  failure is a stable machine-readable error payload;
- unexpected exceptions are translated through the deterministic error
  model and logged SERVER-SIDE only — clients never see tracebacks;
- request bodies are strictly bounded (64 KiB) and must be
  ``application/json`` when present;
- handler threads never touch the application lifecycle; the server
  thread shuts down cleanly and joins within a bounded timeout.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from piano_core.api.errors import ApiError, translate_exception
from piano_core.api.handlers import ROUTES
from piano_core.api.streaming import ApiStream
from piano_core.web import UI_ASSETS, read_ui_asset, resolve_ui_asset

__all__ = ["ApiRequest", "ApiServer", "MAX_BODY_BYTES", "MAX_UPLOAD_BODY_BYTES"]

#: Hard upper bound for one request body (local JSON control payloads).
MAX_BODY_BYTES = 64 * 1024

#: M5: the single defined practice-package upload route accepts one tar
#: envelope with its own bound (mirrors the importer's package bound;
#: one source of truth in the packages contract).
MAX_UPLOAD_BODY_BYTES = 32 * 1024 * 1024
_UPLOAD_ROUTE = "/api/v1/packages/upload"
_UPLOAD_CONTENT_TYPE = "application/x-tar"

_CONTENT_TYPE = "Content-Type"
_JSON_TYPE = "application/json"

#: Conservative cache policy for the appliance UI phase (PIANO-P013):
#: correctness over cleverness — a deployment can never strand a browser
#: on stale JS/CSS. Documented in docs/operations/UI.md.
_UI_CACHE_HEADER = ("Cache-Control", "no-cache")


def _process_fd_headers() -> tuple[tuple[str, str], ...]:
    """Read-only Linux process sample for appliance resource verification.

    Enumerating descriptors is not atomic. listdir closes its own directory
    handle before readlink; handles concurrently closed are skipped. Any other
    read failure makes the sample unavailable, never a misleading zero.
    No descriptor paths or database contents leave the process.
    """
    identity = (("X-Piano-Process-PID", str(os.getpid())),)
    try:
        names = os.listdir("/proc/self/fd")
        count = alsa = 0
        for name in names:
            if not name.isdecimal():
                continue
            try:
                target = os.readlink("/proc/self/fd/" + name)
            except FileNotFoundError:
                continue
            count += 1
            alsa += target == "/dev/snd/seq"
    except OSError:
        return identity + (("X-Piano-FD-Sample", "unavailable"),)
    return identity + (
        ("X-Piano-FD-Sample", "sampled"),
        ("X-Piano-Process-FDs", str(count)),
        ("X-Piano-ALSA-FDs", str(alsa)),
    )


class ApiRequest:
    """One parsed API request (immutable view for handlers)."""

    __slots__ = ("method", "url_path", "query", "body", "content_type")

    def __init__(
        self,
        method: str,
        path: str,
        query: dict[str, str],
        body: bytes,
        content_type: str,
    ) -> None:
        self.method = method
        self.url_path = path
        self.query = query
        self.body = body
        self.content_type = content_type


class _ApiHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP server carrying the routing context."""

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 16

    def __init__(self, address, context, routes, serve_ui: bool = True) -> None:
        self.context = context
        self.routes = routes
        self.serve_ui = serve_ui
        self._requests_changed = threading.Condition()
        self._requests: dict[socket.socket, threading.Thread] = {}
        self._draining = False
        super().__init__(address, _ApiRequestHandler)

    def begin_shutdown(self) -> None:
        # Admission is linearized before publication closes; no I/O under lock.
        with self._requests_changed:
            self._draining = True
        channel = getattr(self.context, "live_notes", None)
        if channel is not None:
            channel.close()

    @property
    def draining(self) -> bool:
        with self._requests_changed:
            return self._draining

    def process_request(self, request, client_address) -> None:
        # Register before start: shutdown must also own accepted/not-yet-run work.
        thread = threading.Thread(
            target=self.process_request_thread, args=(request, client_address),
            name="piano-core-api-request", daemon=True,
        )
        with self._requests_changed:
            self._requests[request] = thread
        try:
            thread.start()
        except BaseException:
            with self._requests_changed:
                self._requests.pop(request, None)
                self._requests_changed.notify_all()
            self.shutdown_request(request)
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._requests_changed:
                self._requests.pop(request, None)
                self._requests_changed.notify_all()

    def drain_requests(self, deadline: float) -> None:
        with self._requests_changed:
            threads = tuple(self._requests.values())
            self._requests_changed.wait_for(
                lambda: not self._requests,
                max(0.0, min(0.25, (deadline - time.monotonic()) / 2)),
            )
        self.cancel_requests()
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        with self._requests_changed:
            if self._requests or any(thread.is_alive() for thread in threads):
                raise TimeoutError("API request handlers did not drain before shutdown deadline")

    def cancel_requests(self) -> None:
        with self._requests_changed:
            sockets = tuple(self._requests)
        # Idle keep-alive, partial headers/body or a slow peer cannot consume
        # the 30s request timeout during shutdown. Wake only owned sockets.
        for connection in sockets:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError as exc:
                # Only an already-closed/disconnected owned peer is expected.
                if exc.errno not in (errno.EBADF, errno.ENOTCONN):
                    raise


class _ApiRequestHandler(BaseHTTPRequestHandler):
    """Bounded, deterministic request dispatch (JSON in, JSON out)."""

    server_version = "piano-core-api"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 30

    def handle(self) -> None:
        try:
            super().handle()
        except (ConnectionError, TimeoutError):
            # Peer disconnect / owned socket cancellation, including JSON
            # responses whose peer disappeared after request-body parsing.
            self.close_connection = True

    # ------------------------------------------------------------------
    # dispatch
    # ------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802 (http.server API)
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802 (http.server API)
        self._dispatch("DELETE")

    def do_PATCH(self) -> None:  # noqa: N802 (http.server API)
        self._dispatch("PATCH")

    def _dispatch(self, method: str) -> None:
        log = logging.getLogger("piano_core.api")
        split = urlsplit(self.path)
        path = split.path
        request = ApiRequest(
            method=method,
            path=path,
            query={key: values[0] for key, values in parse_qs(
                split.query, keep_blank_values=True
            ).items()},
            body=b"",
            content_type=self.headers.get(_CONTENT_TYPE, ""),
        )
        try:
            if self.server.draining:
                self.close_connection = True  # never reinterpret unread bodies
                raise ApiError(503, "api_stopping", "API is stopping")
            if (
                self.server.serve_ui
                and not path.startswith("/api/")
                and self._serve_ui(method, path)
            ):
                return
            route, params = self._match(method, path)
            self._validate_query(route, request)
            if method in ("POST", "PUT"):
                request = self._read_body(request)
            status, payload = route.handler(
                self.server.context, request, **params
            )
        except ApiError as exc:
            self._send_json(exc.status_code, exc.payload())
            return
        except Exception as exc:
            # Unexpected OR product-layer failure: translate through the
            # deterministic error model. Tracebacks are logged on the
            # SERVER only — clients never receive them.
            translated = translate_exception(exc)
            if translated.status_code >= 500:
                log.exception("unhandled API failure on %s %s", method, path)
            self._send_json(translated.status_code, translated.payload())
            return
        if isinstance(payload, ApiStream):
            self._send_stream(status, payload)
        else:
            headers = _process_fd_headers() if (
                method == "GET" and path == "/api/v1/health" and status == 200
            ) else ()
            self._send_json(status, payload, headers=headers)

    def _serve_ui(self, method: str, path: str) -> bool:
        """Serve one allowlisted UI asset (PIANO-P013).

        Returns ``False`` when the path is not a UI path, so dispatch
        falls through to the API route table (deterministic JSON 404).
        The allowlist in :mod:`piano_core.web` is the only path source:
        no filesystem join, no directory listing, traversal impossible.
        """
        resolved = resolve_ui_asset(path)
        if resolved is None:
            return False
        if method != "GET":
            raise ApiError(
                405,
                "method_not_allowed",
                f"method {method} not allowed (allowed: GET)",
            )
        name, content_type = resolved
        body = read_ui_asset(name)
        self.send_response(200)
        self.send_header(_CONTENT_TYPE, content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header(*_UI_CACHE_HEADER)
        self.end_headers()
        self.wfile.write(body)
        return True

    def _match(self, method: str, path: str):
        """Exact route match with single-segment ``<param>`` capture."""
        segments = [segment for segment in path.split("/") if segment]
        allowed_methods: set[str] = set()
        for route in self.server.routes:
            route_segments = [
                segment for segment in route.pattern.split("/") if segment
            ]
            if len(route_segments) != len(segments):
                continue
            params: dict[str, str] = {}
            matched = True
            for pattern_seg, actual in zip(route_segments, segments):
                if pattern_seg.startswith("<") and pattern_seg.endswith(">"):
                    params[pattern_seg[1:-1]] = unquote(actual)
                elif pattern_seg != actual:
                    matched = False
                    break
            if not matched:
                continue
            if route.method == method:
                return route, params
            allowed_methods.add(route.method)
        if allowed_methods:
            raise ApiError(
                405,
                "method_not_allowed",
                f"method {method} not allowed (allowed: "
                f"{', '.join(sorted(allowed_methods))})",
            )
        raise ApiError(404, "not_found", f"unknown route: {path}")

    def _validate_query(self, route, request) -> None:
        unknown = sorted(key for key in request.query if key not in route.query_keys)
        if unknown:
            raise ApiError(
                400,
                "invalid_parameter",
                f"unexpected query parameter(s): {', '.join(unknown)}",
            )

    def _read_body(self, request) -> ApiRequest:
        """Read a strictly bounded POST body (fail closed on abuse)."""
        if self.headers.get("Transfer-Encoding"):
            raise ApiError(
                400,
                "malformed_request",
                "chunked transfer encoding is not supported; send "
                "Content-Length",
            )
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return request  # no body
        try:
            length = int(raw_length)
        except ValueError:
            raise ApiError(
                400, "malformed_request", "invalid Content-Length"
            ) from None
        if length < 0:
            raise ApiError(400, "malformed_request", "invalid Content-Length")
        is_upload = (
            request.method == "POST" and request.url_path == _UPLOAD_ROUTE
        )
        bound = MAX_UPLOAD_BODY_BYTES if is_upload else MAX_BODY_BYTES
        if length > bound:
            # Do not read the body: close the connection after responding.
            self.close_connection = True
            raise ApiError(
                413,
                "payload_too_large",
                f"request body exceeds {bound} bytes",
            )
        body = self.rfile.read(length) if length else b""
        if len(body) != length:  # client disconnected mid-body
            self.close_connection = True
            raise ApiError(400, "malformed_request", "incomplete request body")
        content_type = request.content_type.split(";")[0].strip().lower()
        if body and content_type != _JSON_TYPE and not (
            is_upload and content_type == _UPLOAD_CONTENT_TYPE
        ):
            raise ApiError(
                415,
                "unsupported_media_type",
                "request body must be application/json",
            )
        return ApiRequest(
            method=request.method,
            path=request.url_path,
            query=request.query,
            body=body,
            content_type=request.content_type,
        )

    # ------------------------------------------------------------------
    # responses
    # ------------------------------------------------------------------
    def _send_json(self, status: int, payload: dict, *, headers=()) -> None:
        body = json.dumps(
            payload, separators=(",", ":"), sort_keys=True, allow_nan=False
        ).encode("utf-8")
        self.send_response(status)
        self.send_header(_CONTENT_TYPE, f"{_JSON_TYPE}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if self.close_connection:
            self.send_header("Connection", "close")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_stream(self, status: int, stream: ApiStream) -> None:
        """Write one bounded-capacity realtime stream until peer/shutdown."""
        try:
            self.close_connection = True
            try:
                self.send_response(status)
                self.send_header(_CONTENT_TYPE, stream.content_type)
                self.send_header("Cache-Control", stream.cache_control)
                self.send_header("Connection", "close")
                self.end_headers()
            except (ConnectionError, TimeoutError):
                return
            for chunk in stream.chunks:
                if not isinstance(chunk, bytes):
                    raise TypeError("API stream chunks must be bytes")
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except (ConnectionError, TimeoutError):
                    return
        except Exception:
            # Headers already went out: log the real fault and close, never
            # attempt a second HTTP status/JSON body on an established SSE.
            logging.getLogger("piano_core.api").exception("unhandled API stream failure")
        finally:
            try:
                stream.close()
            except Exception:
                logging.getLogger("piano_core.api").exception("API stream cleanup failed")

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        # Access log at DEBUG; errors are logged explicitly in _dispatch.
        logging.getLogger("piano_core.api").debug(
            "client %s: " + format, self.address_string(), *args
        )


class ApiServer:
    """Owned HTTP server facade (bind, serve thread, clean shutdown)."""

    def __init__(
        self,
        context,
        host: str,
        port: int,
        logger: logging.Logger | None = None,
        serve_ui: bool = True,
    ) -> None:
        self._log = logger or logging.getLogger("piano_core.api")
        # Binding happens here: OSError (address in use, permission,
        # unbindable address) propagates to the composition root, which
        # applies the documented degrade contract.
        self._httpd = _ApiHTTPServer(
            (host, port), context, ROUTES, serve_ui=serve_ui
        )
        self._thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()
        self._shutdown_thread: threading.Thread | None = None
        self._shutdown_error: BaseException | None = None
        self._closed = False

    @property
    def bound_address(self) -> tuple[str, int]:
        """The bound (host, port) — resolved even for port 0 in tests."""
        address = self._httpd.server_address
        return str(address[0]), int(address[1])

    def start(self) -> None:
        """Serve on a daemon thread (process exit can never hang)."""
        with self._lifecycle_lock:
            if self._thread is not None:
                raise RuntimeError("api server already started")
            if self._closed:
                raise RuntimeError("api server is closed; create a fresh server")
            host, port = self.bound_address
            self._thread = threading.Thread(
                target=lambda: self._httpd.serve_forever(poll_interval=0.05),
                name="piano-core-api", daemon=True,
            )
            try:
                self._thread.start()
            except BaseException:
                self._thread = None
                self._httpd.begin_shutdown()
                self._httpd.server_close()
                self._closed = True
                raise
            self._log.info("api listening host=%s port=%d", host, port)

    def shutdown(self, timeout: float = 5.0) -> None:
        """Reject new work, end SSE, then drain handlers within ONE deadline.

        stdlib shutdown() itself blocks; its owned control thread is included
        in the same deadline. A timeout remains an explicit lifecycle failure.
        No generator is closed from a different thread.
        """
        if timeout <= 0:
            raise ValueError("shutdown timeout must be positive")
        deadline = time.monotonic() + timeout
        if not self._lifecycle_lock.acquire(timeout=timeout):
            raise TimeoutError("API shutdown already in progress past deadline")
        try:
            if self._closed:
                return
            self._httpd.begin_shutdown()
            if self._thread is not None:
                if self._shutdown_thread is None:
                    def stop_accepting():
                        try:
                            self._httpd.shutdown()
                        except BaseException as exc:
                            self._shutdown_error = exc
                    self._shutdown_thread = threading.Thread(
                        target=stop_accepting, name="piano-core-api-shutdown", daemon=True,
                    )
                    try:
                        self._shutdown_thread.start()
                    except BaseException:
                        self._shutdown_thread = None
                        raise
                self._shutdown_thread.join(max(0.0, deadline - time.monotonic()))
                self._thread.join(max(0.0, deadline - time.monotonic()))
                if self._shutdown_error is not None:
                    raise self._shutdown_error
                if self._shutdown_thread.is_alive() or self._thread.is_alive():
                    raise TimeoutError("API accept loop did not stop before shutdown deadline")
            self._httpd.server_close()
            self._httpd.drain_requests(deadline)
            self._thread = None
            self._closed = True
            self._log.info("api server stopped")
        except BaseException:
            # A stuck/unexpected handler cannot be killed safely in Python.
            # Cancel its owned socket, retain the failed lifecycle for a later
            # join, and surface the failure rather than claiming quiescence.
            try:
                self._httpd.server_close()
                self._httpd.cancel_requests()
            except Exception:
                self._log.exception("API shutdown socket cleanup failed")
            raise
        finally:
            self._lifecycle_lock.release()
