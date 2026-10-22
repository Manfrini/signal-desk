"""Redirect targets must pass the same SSRF check as the original URL.

`is_safe_public_url` runs once, before the fetch. Following redirects
automatically let a source that passes that check hand the server a second,
unchecked destination -- a cheaper bypass than the DNS rebinding the module
docstring already documents as an accepted gap.

These tests use a real loopback server rather than a mocked `requests.get`,
because what is under test is precisely how redirects are followed.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.net_guard import fetch_capped_text

# Link-local: `is_safe_public_url` rejects it, and nothing is listening, so a
# test that wrongly follows the redirect fails loudly instead of hanging.
BLOCKED_TARGET = "http://169.254.169.254/latest/meta-data/"


def _server(handler_cls):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


@pytest.fixture()
def redirector():
    """A server that bounces every request to a blocked address."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - required name
            self.send_response(302)
            self.send_header("Location", BLOCKED_TARGET)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    httpd, base = _server(Handler)
    yield base
    httpd.shutdown()


@pytest.fixture()
def plain_server():
    """A server that answers normally, to prove redirects are still followed."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - required name
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", "/end")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = b"arrived"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd, base = _server(Handler)
    yield base
    httpd.shutdown()


def test_refuses_a_redirect_to_a_blocked_address(redirector):
    with pytest.raises(ValueError) as exc:
        fetch_capped_text(redirector + "/article")

    assert "169.254.169.254" in str(exc.value)


def test_still_follows_a_same_origin_redirect(plain_server):
    # The fix must not break the relative-Location hops ordinary sites serve
    # (trailing slashes, canonical paths), which are routine in the catalogue.
    # Same origin means the host was already approved, so it is not re-checked.
    assert fetch_capped_text(plain_server + "/start") == "arrived"
