"""LlmAuthManager and the /api/llm-* routes (no test performs a real login)."""

from __future__ import annotations

import json as _json
import sys
import textwrap
import time
import urllib.error
import urllib.request

import pytest

from src import server as server_mod
from src.server import LlmAuthManager, make_handler
from http.server import ThreadingHTTPServer


def _script(tmp_path, body: str) -> list[str]:
    p = tmp_path / "fake_login.py"
    p.write_text(textwrap.dedent(body))
    return [sys.executable, str(p)]


def _wait(mgr, state, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        if mgr.snapshot()["state"] == state:
            return
        time.sleep(0.05)
    raise AssertionError(f"state never became {state}: {mgr.snapshot()}")


def test_success_when_probe_reports_logged_in(tmp_path):
    cmd = _script(
        tmp_path,
        """
        import time
        print("Visit https://claude.com/oauth/authorize?x=1 to continue")
        time.sleep(1)
        """,
    )
    flips = {"n": 0}

    def probe():
        flips["n"] += 1
        return flips["n"] >= 2

    mgr = LlmAuthManager(login_cmd=cmd, status_probe=probe, poll_seconds=0.2)
    assert mgr.start() is True
    _wait(mgr, "success")
    assert mgr.snapshot()["url"].startswith("https://claude.com/oauth/authorize")


def test_error_when_process_exits_and_probe_false(tmp_path):
    cmd = _script(tmp_path, 'print("done")')
    mgr = LlmAuthManager(login_cmd=cmd, status_probe=lambda: False, poll_seconds=0.2)
    mgr.start()
    _wait(mgr, "error")


def test_second_start_returns_false_while_pending(tmp_path):
    cmd = _script(tmp_path, "import time; time.sleep(2)")
    mgr = LlmAuthManager(login_cmd=cmd, status_probe=lambda: False, poll_seconds=0.2)
    assert mgr.start() is True
    assert mgr.start() is False


def test_timeout(tmp_path):
    cmd = _script(tmp_path, "import time; time.sleep(30)")
    mgr = LlmAuthManager(
        login_cmd=cmd, status_probe=lambda: False, poll_seconds=0.2, timeout_seconds=1.0
    )
    mgr.start()
    _wait(mgr, "timeout", timeout=8.0)


def test_snapshot_never_leaks_raw_output(tmp_path):
    cmd = _script(
        tmp_path,
        'print("SECRET-TOKEN-sk-ant-oat01-xyz"); import time; time.sleep(1)',
    )
    mgr = LlmAuthManager(login_cmd=cmd, status_probe=lambda: True, poll_seconds=0.2)
    mgr.start()
    _wait(mgr, "success")
    assert "SECRET-TOKEN" not in str(mgr.snapshot())


def test_codex_device_code_is_exposed_without_raw_pty_output(tmp_path):
    cmd = _script(tmp_path, 'print("Your one-time code is AB12 CD34"); import time; time.sleep(0.2)')
    mgr = LlmAuthManager(login_cmd=cmd, status_probe=lambda: False, provider="codex", poll_seconds=0.05)
    mgr.start()
    _wait(mgr, "error")
    assert mgr.snapshot()["device_code"] == "AB12-CD34"


@pytest.fixture()
def http_server(tmp_path):
    dashboard = tmp_path / "index.html"
    dashboard.write_text("<html></html>", encoding="utf-8")
    handler = make_handler(
        runner=server_mod.AnalysisRunner(runner=lambda: {"ok": True}),
        dashboard_path=str(dashboard),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = __import__("threading").Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield base
    server.shutdown()


def _get(base, path):
    with urllib.request.urlopen(base + path) as resp:
        return resp.status, _json.loads(resp.read().decode())


def _post(base, path):
    req = urllib.request.Request(
        base + path, method="POST", headers={"Origin": base}
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, _json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, _json.loads(exc.read().decode())


def test_llm_status_route(monkeypatch, http_server):
    monkeypatch.setattr(
        server_mod,
        "claude_cli_health",
        lambda **_: {
            "ok": False,
            "reason": "logged_out",
            "detail": "d",
            "checked_at": "2026-08-28T10:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        server_mod, "read_llm_status", lambda *a, **k: {"last_ok_at": "2026-08-17T09:00:00+00:00"}
    )
    status, body = _get(http_server, "/api/llm-status")
    assert status == 200
    assert body["reason"] == "logged_out"
    assert body["last_ok_at"] == "2026-08-17T09:00:00+00:00"


def test_llm_auth_start_and_status_routes(monkeypatch, http_server):
    monkeypatch.setattr(server_mod, "get_llm_auth", lambda: server_mod.llm_auth)
    monkeypatch.setattr(server_mod.llm_auth, "start", lambda: True)
    monkeypatch.setattr(
        server_mod.llm_auth,
        "snapshot",
        lambda: {"state": "pending", "url": "https://x", "message": "m"},
    )
    code, body = _post(http_server, "/api/llm-auth/start")
    assert code == 202
    assert body["started"] is True
    code, body = _get(http_server, "/api/llm-auth/status")
    assert body["state"] == "pending"


def test_llm_auth_start_conflict(monkeypatch, http_server):
    monkeypatch.setattr(server_mod, "get_llm_auth", lambda: server_mod.llm_auth)
    monkeypatch.setattr(server_mod.llm_auth, "start", lambda: False)
    monkeypatch.setattr(
        server_mod.llm_auth,
        "snapshot",
        lambda: {"state": "pending", "url": "", "message": "m"},
    )
    code, body = _post(http_server, "/api/llm-auth/start")
    assert code == 409
    assert body["started"] is False
