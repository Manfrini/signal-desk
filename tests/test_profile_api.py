"""The /api/profile* routes: GET/POST profile, and the expansion job."""

from __future__ import annotations

import json as _json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from src import server as server_mod
from src.server import ProfileExpansionManager, make_handler
from src.storage import json_storage


@pytest.fixture()
def http_server(tmp_path, monkeypatch):
    profile_path = tmp_path / "profile.json"
    legacy_path = tmp_path / "no_such_profile.yaml"
    real_load = json_storage.load_profile_config
    real_save = json_storage.save_profile_config

    def fake_load_profile_config(path=profile_path, legacy_yaml_path=legacy_path):
        return real_load(path=profile_path, legacy_yaml_path=legacy_path)

    def fake_save_profile_config(doc, path=profile_path):
        return real_save(doc, path=profile_path)

    monkeypatch.setattr(json_storage, "load_profile_config", fake_load_profile_config)
    monkeypatch.setattr(json_storage, "save_profile_config", fake_save_profile_config)
    # src.processors.profile imported load_profile_config by name at module
    # load time, so patching json_storage alone doesn't reach load_profile()
    # (used by GET /api/profile) -- patch its own bound reference too.
    from src.processors import profile as profile_mod

    monkeypatch.setattr(profile_mod, "load_profile_config", fake_load_profile_config)

    dashboard = tmp_path / "index.html"
    dashboard.write_text("<html></html>", encoding="utf-8")
    profile_expansion = ProfileExpansionManager(generator=lambda about_me: None)
    handler = make_handler(
        runner=server_mod.AnalysisRunner(runner=lambda: {"ok": True}),
        dashboard_path=str(dashboard),
        profile_expansion=profile_expansion,
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, profile_expansion
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


def test_get_profile_on_clean_install_is_empty_and_flagged(http_server):
    base, _ = http_server
    status, body = _get(base, "/api/profile")
    assert status == 200
    assert body["has_profile"] is False
    assert body["profile"]["keywords_boost"] == []


def test_post_profile_saves_and_get_reflects_it(http_server):
    base, _ = http_server
    status, body = _post(
        base,
        "/api/profile",
        {"profile": {"about_me": "product analyst", "keywords_boost": ["SQL", "sql", "ai"]}},
    )
    assert status == 200
    assert body["ok"] is True
    # "ai" is denylisted, "SQL"/"sql" dedupe to one lowercase term.
    assert body["profile"]["keywords_boost"] == ["sql"]

    status, body = _get(base, "/api/profile")
    assert body["has_profile"] is True
    assert body["profile"]["about_me"] == "product analyst"


def test_post_profile_tracks_edited_fields(http_server):
    base, _ = http_server
    _post(base, "/api/profile", {"profile": {"goal": "stay current"}})
    status, body = _post(base, "/api/profile", {"profile": {"keywords_boost": ["sql"]}})

    assert set(body["profile"]["edited_fields"]) == {"goal", "keywords_boost"}
    # The first save's goal must survive the second, narrower save.
    assert body["profile"]["goal"] == "stay current"


def test_post_profile_rejects_forged_origin(http_server):
    base, _ = http_server
    req = urllib.request.Request(
        base + "/api/profile",
        data=b'{"profile": {"goal": "x"}}',
        method="POST",
        headers={"Origin": "https://evil.example.com"},
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(req)
    assert excinfo.value.code == 403


def test_expand_requires_about_me(http_server):
    base, _ = http_server
    status, body = _post(base, "/api/profile/expand", {"about_me": "   "})
    assert status == 400


def test_expand_returns_409_like_busy_and_status_reflects_state(http_server):
    base, manager = http_server
    with manager._lock:
        manager._state = "running"

    status, body = _post(base, "/api/profile/expand", {"about_me": "I am a PM"})
    assert status == 429

    status, body = _get(base, "/api/profile/expand/status")
    assert body["state"] == "running"


def test_expand_end_to_end_with_stub_generator(tmp_path, monkeypatch):
    profile_path = tmp_path / "profile.json"
    real_load = json_storage.load_profile_config

    monkeypatch.setattr(
        json_storage, "load_profile_config",
        lambda path=profile_path, legacy_yaml_path=None: real_load(
            path=profile_path, legacy_yaml_path=tmp_path / "nope.yaml"
        ),
    )

    dashboard = tmp_path / "index.html"
    dashboard.write_text("<html></html>", encoding="utf-8")
    proposal = {"goal": "g", "interests": [], "skills_to_grow": [], "keywords_boost": ["sql"], "projects": []}
    manager = ProfileExpansionManager(generator=lambda about_me: proposal)
    handler = make_handler(
        runner=server_mod.AnalysisRunner(runner=lambda: {"ok": True}),
        dashboard_path=str(dashboard),
        profile_expansion=manager,
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        status, body = _post(base, "/api/profile/expand", {"about_me": "I am a PM"})
        assert status == 202

        end = time.time() + 3
        state = "running"
        while time.time() < end:
            _, body = _get(base, "/api/profile/expand/status")
            state = body["state"]
            if state != "running":
                break
            time.sleep(0.05)
        assert state == "done"
        assert body["proposal"]["keywords_boost"] == ["sql"]
    finally:
        httpd.shutdown()


def test_expand_not_enabled_returns_404_when_no_manager_configured(tmp_path):
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
    try:
        status, _ = _post(base, "/api/profile/expand", {"about_me": "x"})
        assert status == 404
        status, _ = _get(base, "/api/profile/expand/status")
        assert status == 404
    finally:
        httpd.shutdown()
