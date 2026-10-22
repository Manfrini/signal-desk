"""Starting when port 8000 is already taken.

8000 is one of the busiest ports on a developer machine (Django, `python -m
http.server`, countless dev tools). Binding it blindly crashed with an
`OSError` traceback -- unreadable for someone who just double-clicked
`start.command` -- and the launch scripts then opened the browser on whatever
*other* application was answering there.
"""

from __future__ import annotations

import json
import socket
import threading
import urllib.request

import pytest

from src import server as server_mod
from src.server import APP_MARKER, find_free_port, make_handler, run_server


@pytest.fixture()
def occupied_port():
    """Hold a port the way another application would."""
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    yield sock.getsockname()[1]
    sock.close()


def test_finds_the_next_free_port_when_the_first_is_taken(occupied_port):
    chosen = find_free_port("127.0.0.1", occupied_port, attempts=10)

    assert chosen != occupied_port
    assert occupied_port < chosen <= occupied_port + 10


def test_returns_the_requested_port_when_it_is_free():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    free = sock.getsockname()[1]
    sock.close()

    assert find_free_port("127.0.0.1", free, attempts=10) == free


def test_gives_up_with_a_readable_error_when_nothing_is_free(occupied_port):
    with pytest.raises(OSError) as exc:
        find_free_port("127.0.0.1", occupied_port, attempts=1)

    # The message reaches a non-technical user in a terminal window.
    assert "porta" in str(exc.value).lower()


def test_server_starts_on_another_port_instead_of_crashing(occupied_port, tmp_path, capsys):
    dashboard = tmp_path / "index.html"
    dashboard.write_text("<html></html>", encoding="utf-8")
    started = threading.Event()
    box = {}

    def on_ready(url: str) -> None:
        box["url"] = url
        started.set()

    thread = threading.Thread(
        target=run_server,
        kwargs={"port": occupied_port, "dashboard_path": str(dashboard), "on_ready": on_ready},
        daemon=True,
    )
    thread.start()

    assert started.wait(5), "the server never came up"
    assert box["url"] != f"http://127.0.0.1:{occupied_port}"
    server_mod.shutdown_server()


def test_explicit_port_is_honoured_and_fails_loudly(occupied_port, tmp_path):
    # Someone who passed --port meant that port; silently moving would be worse.
    dashboard = tmp_path / "index.html"
    dashboard.write_text("<html></html>", encoding="utf-8")

    with pytest.raises(OSError):
        run_server(port=occupied_port, dashboard_path=str(dashboard), allow_fallback=False)


def test_status_identifies_the_application(tmp_path):
    """The launch scripts must be able to tell our server from a stranger's."""
    from http.server import ThreadingHTTPServer

    dashboard = tmp_path / "index.html"
    dashboard.write_text("<html></html>", encoding="utf-8")
    handler = make_handler(
        runner=server_mod.AnalysisRunner(runner=lambda: {"ok": True}),
        dashboard_path=str(dashboard),
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    with urllib.request.urlopen(base + "/api/status") as resp:
        body = json.loads(resp.read().decode())

    httpd.shutdown()
    assert body["app"] == APP_MARKER
