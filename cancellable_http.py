"""Blocking HTTP stream transport whose active socket can be interrupted by Cancel."""
from __future__ import annotations

import http.client
from email.message import Message
import socket
import ssl
import threading
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request
from typing import Callable

_READ_POLL_SECONDS = 0.25


class InterruptibleHTTPResponse:
    """Small streaming HTTP/1.x reader with cancel-aware polling and no total deadline."""
    def __init__(self, sock: socket.socket, cancel: threading.Event):
        self.sock = sock
        self.cancel = cancel
        self.status = 0
        self.reason = ""
        self.headers = Message()
        self._wire = bytearray()
        self._body = bytearray()
        self._remaining: int | None = None
        self._chunked = False
        self._chunk_remaining = 0
        self._eof = False

    def _receive(self) -> bytes:
        while True:
            if self.cancel.is_set():
                raise ConnectionAbortedError("Request cancelled while waiting for response data")
            try:
                self.sock.settimeout(_READ_POLL_SECONDS)
                data = self.sock.recv(65536)
            except socket.timeout:
                continue
            if not data:
                self._eof = True
            return data

    def _wire_line(self) -> bytes:
        while True:
            marker = self._wire.find(b"\n")
            if marker >= 0:
                result = bytes(self._wire[:marker + 1])
                del self._wire[:marker + 1]
                return result
            data = self._receive()
            if not data:
                if self._wire:
                    result = bytes(self._wire)
                    self._wire.clear()
                    return result
                return b""
            self._wire.extend(data)

    def begin(self) -> None:
        while True:
            status_line = self._wire_line().decode("iso-8859-1").strip("\r\n")
            if not status_line:
                raise ConnectionError("Provider closed the connection before response headers arrived.")
            parts = status_line.split(None, 2)
            if len(parts) < 2 or not parts[0].startswith("HTTP/"):
                raise ConnectionError(f"Invalid HTTP response status line: {status_line[:160]}")
            try:
                self.status = int(parts[1])
            except ValueError as exc:
                raise ConnectionError(f"Invalid HTTP response status code: {parts[1]!r}") from exc
            self.reason = parts[2] if len(parts) == 3 else ""
            headers = Message()
            while True:
                line = self._wire_line()
                if not line:
                    raise ConnectionError("Provider closed the connection before response headers completed.")
                if line in (b"\r\n", b"\n"):
                    break
                decoded = line.decode("iso-8859-1").strip("\r\n")
                name, separator, value = decoded.partition(":")
                if not separator:
                    raise ConnectionError(f"Invalid HTTP response header: {decoded[:160]}")
                headers.add_header(name.strip(), value.strip())
            if 100 <= self.status < 200 and self.status != 101:
                continue
            self.headers = headers
            transfer_encoding = headers.get("Transfer-Encoding", "")
            self._chunked = "chunked" in transfer_encoding.casefold()
            if not self._chunked:
                try:
                    length = headers.get("Content-Length")
                    self._remaining = int(length) if length is not None else None
                except ValueError:
                    self._remaining = None
            self._eof = self._remaining == 0
            return

    def _read_wire(self, amount: int) -> bytes:
        while len(self._wire) < amount:
            data = self._receive()
            if not data:
                break
            self._wire.extend(data)
        result = bytes(self._wire[:amount])
        del self._wire[:len(result)]
        return result

    def _next_body_chunk(self) -> bytes:
        if self._eof:
            return b""
        if self._chunked:
            if self._chunk_remaining == 0:
                line = self._wire_line().strip()
                if not line:
                    raise ConnectionError("Provider ended before the next HTTP chunk.")
                try:
                    self._chunk_remaining = int(line.split(b";", 1)[0], 16)
                except ValueError as exc:
                    raise ConnectionError("Provider sent an invalid HTTP chunk size.") from exc
                if self._chunk_remaining == 0:
                    while self._wire_line() not in (b"\r\n", b"\n", b""):
                        pass
                    self._eof = True
                    return b""
            data = self._read_wire(min(self._chunk_remaining, 65536))
            if not data:
                raise ConnectionError("Provider closed during an HTTP chunk.")
            self._chunk_remaining -= len(data)
            if self._chunk_remaining == 0:
                ending = self._read_wire(2)
                if ending != b"\r\n":
                    raise ConnectionError("Provider sent an invalid HTTP chunk terminator.")
            return data
        if self._remaining is not None:
            if self._remaining <= 0:
                self._eof = True
                return b""
            data = self._read_wire(min(self._remaining, 65536))
            if not data:
                raise ConnectionError("Provider closed before the declared HTTP response length.")
            self._remaining -= len(data)
            return data
        data = self._read_wire(65536)
        if not data:
            self._eof = True
        return data

    def readline(self, limit: int = -1) -> bytes:
        while True:
            marker = self._body.find(b"\n")
            if marker >= 0:
                end = marker + 1
                if limit >= 0:
                    end = min(end, limit)
                result = bytes(self._body[:end])
                del self._body[:end]
                return result
            if limit >= 0 and len(self._body) >= limit:
                result = bytes(self._body[:limit])
                del self._body[:limit]
                return result
            if self._eof:
                result = bytes(self._body)
                self._body.clear()
                return result
            data = self._next_body_chunk()
            if data:
                self._body.extend(data)

    def read(self, amount: int = -1) -> bytes:
        if amount == 0:
            return b""
        while not self._eof and (amount < 0 or len(self._body) < amount):
            data = self._next_body_chunk()
            if data:
                self._body.extend(data)
        if amount < 0:
            result = bytes(self._body)
            self._body.clear()
            return result
        result = bytes(self._body[:amount])
        del self._body[:len(result)]
        return result

    def close(self) -> None:
        self._eof = True


def open_http_stream(request: Request, cancel: threading.Event,
                     register: Callable[[http.client.HTTPConnection | None,
                                         http.client.HTTPResponse | None], None],
                     on_dispatched: Callable[[], None] | None = None,
                     on_headers: Callable[[http.client.HTTPResponse], None] | None = None) -> http.client.HTTPResponse:
    """Open a streaming response without an inference deadline and expose its socket.

    ``urllib.request.urlopen`` does not expose its connection until response headers
    arrive, so closing it cannot interrupt a request stuck waiting for those headers.
    This local-provider transport registers the connection before sending the request;
    callers can then shut down that socket from their request-only Cancel handler.
    """
    if cancel.is_set():
        raise ConnectionAbortedError("Request cancelled before dispatch")
    parsed = urlsplit(request.full_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Local inference endpoint must use an HTTP or HTTPS URL.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if parsed.scheme == "https":
        connection: http.client.HTTPConnection = http.client.HTTPSConnection(
            parsed.hostname, port, timeout=10, context=ssl.create_default_context())
    else:
        connection = http.client.HTTPConnection(parsed.hostname, port, timeout=10)
    register(connection, None)
    if cancel.is_set():
        connection.close()
        raise ConnectionAbortedError("Request cancelled before dispatch")

    headers = dict(request.header_items())
    if not any(name.lower() == "connection" for name in headers):
        headers["Connection"] = "close"
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    try:
        # Bound only DNS/TCP/TLS establishment. Upload without an inactivity
        # deadline, then poll blocking reads so Cancel is observed on Windows,
        # where closing a socket from another Python thread may not wake recv.
        connection.connect()
        if connection.sock is not None:
            connection.sock.settimeout(None)
        if cancel.is_set():
            raise ConnectionAbortedError("Request cancelled while connecting")
        connection.request(request.get_method(), target, body=request.data, headers=headers)
        if on_dispatched:
            on_dispatched()
        if cancel.is_set():
            raise ConnectionAbortedError("Request cancelled while dispatching")
        if connection.sock is None:
            raise ConnectionError("HTTP connection closed before response headers arrived.")
        response = InterruptibleHTTPResponse(connection.sock, cancel)
        register(connection, response)
        response.begin()
        if on_headers:
            on_headers(response)
        if response.status >= 400:
            raise HTTPError(request.full_url, response.status, response.reason, response.headers, response)
        return response
    except BaseException:
        if cancel.is_set():
            connection.close()
        raise


def interrupt_connection(connection: http.client.HTTPConnection | None,
                          response: http.client.HTTPResponse | None = None,
                          *, close: bool = True) -> None:
    """Interrupt socket I/O first, then release HTTP objects safely."""
    sock = connection.sock if connection is not None else None
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    # Closing HTTPResponse first can wait on the same buffered-reader lock held
    # by a thread blocked in readline(). Shut down and close the connection
    # first so that read wakes; final cleanup closes the response afterward.
    if close and connection is not None:
        try:
            connection.close()
        except OSError:
            pass
    if close and response is not None:
        try:
            response.close()
        except OSError:
            pass
