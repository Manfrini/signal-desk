"""The /api/llm-settings route: reading and switching the LLM provider."""

from __future__ import annotations

import json as _json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from src import server as server_mod
from src.server import make_handler
from src.storage import json_storage


@pytest.fixture()
def http_server(tmp_path, monkeypatch):
    import src.settings as settings_mod

    overrides_path = tmp_path / "overrides.json"
    real_load = settings_mod.load_settings
    real_save = settings_mod.save_settings_overrides

    monkeypatch.setattr(
        settings_mod, "load_settings",
        lambda **kwargs: real_load(
            settings_path=settings_mod.DEFAULT_SETTINGS_PATH, overrides_path=overrides_path
        ),
    )
    monkeypatch.setattr(
        settings_mod, "save_settings_overrides",
        lambda updates, overrides_path=overrides_path: real_save(updates, overrides_path=overrides_path),
    )

    dashboard = tmp_path / "index.html"
    dashboard.write_text("<html></html>", encoding="utf-8")
    handler = make_handler(
        runner=server_mod.AnalysisRunner(runner=lambda: {"ok": True}),
        dashboard_path=str(dashboard),
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base
    httpd.shutdown()


def _get(base, path):
    req = urllib.request.Request(base + path, headers={"Origin": base})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, _json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, _json.loads(exc.read().decode())


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


def test_get_lists_known_providers(http_server):
    status, body = _get(http_server, "/api/llm-settings")
    assert status == 200
    ids = {p["id"] for p in body["providers"]}
    assert {"claude", "codex"} <= ids
    assert "gemini" not in ids  # deferred: not verifiable, not in the table


def test_get_defaults_to_claude_and_italian(http_server):
    _, body = _get(http_server, "/api/llm-settings")
    assert body["provider"] == "claude"
    assert body["language"] == "it"
    assert body["ui_language"] == "it"


def test_post_keeps_application_and_content_languages_independent(http_server):
    status, body = _post(
        http_server, "/api/llm-settings", {"language": "it", "ui_language": "en"}
    )
    assert status == 200
    assert body["language"] == "it"
    assert body["ui_language"] == "en"


def test_post_switches_provider(http_server):
    status, body = _post(http_server, "/api/llm-settings", {"provider": "codex"})
    assert status == 200
    assert body["provider"] == "codex"

    _, body = _get(http_server, "/api/llm-settings")
    assert body["provider"] == "codex"


def test_post_rejects_unknown_provider(http_server):
    status, body = _post(http_server, "/api/llm-settings", {"provider": "not-a-provider"})
    assert status == 400


def test_post_rejects_unsupported_language(http_server):
    status, body = _post(http_server, "/api/llm-settings", {"language": "fr"})
    assert status == 400


def test_post_accepts_a_plain_model_string(http_server):
    status, body = _post(http_server, "/api/llm-settings", {"provider": "claude", "model": "opus"})
    assert status == 200
    assert body["model"] == "opus"


def test_post_rejects_a_model_string_with_shell_metacharacters(http_server):
    status, _ = _post(http_server, "/api/llm-settings", {"model": "opus; rm -rf /"})
    assert status == 400


def test_post_rejects_forged_origin(http_server):
    req = urllib.request.Request(
        http_server + "/api/llm-settings",
        data=b'{"provider": "codex"}',
        method="POST",
        headers={"Origin": "https://evil.example.com"},
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(req)
    assert excinfo.value.code == 403
