"""Blocking HTTP stream transport whose active socket can be interrupted by Cancel."""
from __future__ import annotations

import http.client
import socket
import ssl
import threading
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request
from typing import Callable


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
        # Bound only DNS/TCP/TLS connection establishment. Once connected, leave
        # request upload and model response/header waits unbounded until Cancel.
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
        response = connection.getresponse()
        register(connection, response)
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
