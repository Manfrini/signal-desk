"""The Host/Origin gate, applied to every response and not just to /api/.

The dashboard page is not an empty shell: it embeds the collected items, the
source catalogue and the LLM usage history as inline JSON. Serving it without
the same Host check as the APIs hands all of that to a page that reached the
server under a rebound hostname.
"""

from __future__ import annotations

import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from src import server as server_mod
from src.server import make_handler


@pytest.fixture()
def http_server(tmp_path):
    dashboard = tmp_path / "index.html"
    dashboard.write_text(
        '<html><script id="news-data" type="application/json">[{"url":"secret"}]</script></html>',
        encoding="utf-8",
    )
    handler = make_handler(
        runner=server_mod.AnalysisRunner(runner=lambda: {"ok": True}),
        dashboard_path=str(dashboard),
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _get(base: str, path: str, host: str | None = None) -> tuple[int, bytes]:
    """GET `path`, optionally forging the Host header a rebound page would send."""
    headers = {"Host": host} if host else {}
    req = urllib.request.Request(base + path, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_dashboard_page_is_refused_to_a_foreign_host(http_server):
    # A DNS-rebound page fetches '/' and reads the inline datasets out of it.
    status, body = _get(http_server, "/", host="evil.example:8000")

    assert status == 403
    assert b"secret" not in body


def test_dashboard_page_is_served_to_a_local_host(http_server):
    status, body = _get(http_server, "/")

    assert status == 200
    assert b"secret" in body


def test_design_assets_are_refused_to_a_foreign_host(http_server):
    status, _ = _get(http_server, "/design/mm/tokens.css", host="evil.example:8000")

    assert status == 403


def test_responses_refuse_to_be_framed(http_server):
    # The Host/Origin gate is blind to UI redress: clicks inside a hostile
    # iframe carry this server's own Host and Origin, so framing has to be
    # refused by the browser instead.
    req = urllib.request.Request(http_server + "/")
    with urllib.request.urlopen(req) as resp:
        headers = resp.headers

    assert headers.get("X-Frame-Options") == "DENY"
    assert "frame-ancestors 'none'" in headers.get("Content-Security-Policy", "")


def _post(base: str, path: str, payload: dict):
    import json as _json

    body = _json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base + path, data=body, method="POST",
        headers={"Origin": base, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_saving_an_article_hands_storage_only_whitelisted_fields(http_server, monkeypatch):
    """The route must sanitize before the item reaches storage.

    Observed at the storage boundary rather than by reading the JSON file:
    `add_saved_article` binds its default path at definition time, so a test
    that patched the module attribute would silently write to the real
    data/saved_articles.json instead of a temporary one.
    """
    from src.storage import json_storage

    seen = {}
    monkeypatch.setattr(
        json_storage, "add_saved_article", lambda item, *a, **k: seen.update(item) or {}
    )

    status = _post(
        http_server,
        "/api/save",
        {"item": {"url": "https://a.com/1", "title": "T", "payload": "x" * 500_000}},
    )

    assert status == 200
    assert seen["title"] == "T"
    assert "payload" not in seen


def test_feedback_hands_the_weighting_only_whitelisted_fields(http_server, monkeypatch):
    # Feedback re-weights the stored personalisation preferences, so an
    # unvalidated item here poisons the ranking, not just a JSON file.
    from src.processors import feedback as feedback_mod

    seen = {}

    def fake_record(item, vote, feedback, preferences):
        seen.update(item)
        return feedback, preferences

    monkeypatch.setattr(feedback_mod, "record_feedback", fake_record)

    status = _post(
        http_server,
        "/api/feedback",
        {"item": {"url": "https://a.com/1", "title": "T", "keywords_boost": ["evil"]}, "vote": 1},
    )

    assert status == 200
    assert seen["title"] == "T"
    assert "keywords_boost" not in seen


def test_unknown_routes_near_a_known_prefix_are_not_dispatched(http_server):
    # Four routes matched on a raw-path prefix while the other nineteen used
    # an exact path comparison, so /api/runner reached the pipeline handler.
    assert _post(http_server, "/api/runner", {}) == 404
    assert _post(http_server, "/api/deep-diveXYZ", {"item": {"url": "https://a.com/1"}}) == 404
