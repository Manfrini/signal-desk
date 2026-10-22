"""The daily LLM budget cap enforced at the /api/deep-dive route.

Deep dive has no deterministic fallback (unlike insights/newsletter), so
when the budget is exhausted the route rejects the request outright with a
clear 429 instead of letting it reach a confusing generic error.
"""

from __future__ import annotations

import json as _json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from src import server as server_mod
from src.server import DeepDiveManager, make_handler


@pytest.fixture()
def http_server(tmp_path, monkeypatch):
    dashboard = tmp_path / "index.html"
    dashboard.write_text("<html></html>", encoding="utf-8")
    deep_dives = DeepDiveManager(generator=lambda item: {"overview": "ok", "points": [], "insights": [], "content_complete": True})
    handler = make_handler(
        runner=server_mod.AnalysisRunner(runner=lambda: {"ok": True}),
        dashboard_path=str(dashboard),
        deep_dives=deep_dives,
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base
    httpd.shutdown()


def _post(base, path, payload):
    body = _json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base + path, data=body, method="POST",
        headers={"Origin": base, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, _json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, _json.loads(exc.read().decode())


def test_deep_dive_blocked_with_429_when_budget_exceeded(http_server, monkeypatch):
    monkeypatch.setattr(server_mod, "_llm_budget_exceeded", lambda: True)

    status, body = _post(http_server, "/api/deep-dive", {"item": {"url": "https://a.com/1"}})

    assert status == 429
    assert "budget" in body["error"]


def test_deep_dive_proceeds_normally_when_within_budget(http_server, monkeypatch):
    monkeypatch.setattr(server_mod, "_llm_budget_exceeded", lambda: False)

    status, body = _post(http_server, "/api/deep-dive", {"item": {"url": "https://a.com/1"}})

    assert status == 202
