import json
import subprocess

import pytest

from src.processors import llm_health


def _cp(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(
        args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    llm_health.reset_health_cache()
    yield
    llm_health.reset_health_cache()


def test_not_installed_when_binary_missing(monkeypatch):
    monkeypatch.setattr(llm_health.shutil, "which", lambda _: None)
    result = llm_health.claude_cli_health(_run=lambda cmd: _cp())
    assert result["ok"] is False
    assert result["reason"] == "not_installed"


def test_ok_when_logged_in(monkeypatch):
    monkeypatch.setattr(llm_health.shutil, "which", lambda _: "/usr/bin/claude")
    result = llm_health.claude_cli_health(
        _run=lambda cmd: _cp(json.dumps({"loggedIn": True}))
    )
    assert result["ok"] is True
    assert result["reason"] == "ok"
    assert result["detail"] == ""


def test_logged_out_when_flag_false(monkeypatch):
    monkeypatch.setattr(llm_health.shutil, "which", lambda _: "/usr/bin/claude")
    result = llm_health.claude_cli_health(
        _run=lambda cmd: _cp(json.dumps({"loggedIn": False}))
    )
    assert result["ok"] is False
    assert result["reason"] == "logged_out"


def test_codex_not_logged_in_is_not_a_false_positive(monkeypatch):
    monkeypatch.setattr(llm_health.shutil, "which", lambda _: "/usr/bin/codex")
    result = llm_health.claude_cli_health(
        provider="codex", _run=lambda cmd: _cp(stderr="Not logged in")
    )
    assert result["ok"] is False
    assert result["reason"] == "logged_out"


def test_error_on_bad_json(monkeypatch):
    monkeypatch.setattr(llm_health.shutil, "which", lambda _: "/usr/bin/claude")
    result = llm_health.claude_cli_health(_run=lambda cmd: _cp("not json"))
    assert result["reason"] == "error"


def test_error_on_nonzero_exit_without_json(monkeypatch):
    monkeypatch.setattr(llm_health.shutil, "which", lambda _: "/usr/bin/claude")
    result = llm_health.claude_cli_health(_run=lambda cmd: _cp("boom", returncode=1))
    assert result["reason"] == "error"


def test_logged_out_when_nonzero_exit_but_json_says_so(monkeypatch):
    # `claude auth status --json` really does exit 1 while printing loggedIn:false.
    monkeypatch.setattr(llm_health.shutil, "which", lambda _: "/usr/bin/claude")
    result = llm_health.claude_cli_health(
        _run=lambda cmd: _cp(json.dumps({"loggedIn": False}), returncode=1)
    )
    assert result["reason"] == "logged_out"
    assert result["ok"] is False


def test_error_on_timeout(monkeypatch):
    monkeypatch.setattr(llm_health.shutil, "which", lambda _: "/usr/bin/claude")

    def _boom(cmd):
        raise subprocess.TimeoutExpired(cmd, 10)

    result = llm_health.claude_cli_health(_run=_boom)
    assert result["reason"] == "error"
    assert len(result["detail"]) <= 200


def test_cache_hits_within_ttl(monkeypatch):
    monkeypatch.setattr(llm_health.shutil, "which", lambda _: "/usr/bin/claude")
    calls = []

    def _run(cmd):
        calls.append(cmd)
        return _cp(json.dumps({"loggedIn": True}))

    clock = [1000.0]
    llm_health.claude_cli_health(_run=_run, _now=lambda: clock[0])
    llm_health.claude_cli_health(_run=_run, _now=lambda: clock[0] + 30)
    assert len(calls) == 1
    llm_health.claude_cli_health(_run=_run, _now=lambda: clock[0] + 61)
    assert len(calls) == 2


def test_status_roundtrip_and_last_ok_preserved(tmp_path):
    llm_health.write_llm_status(
        {
            "ok": True,
            "reason": "ok",
            "detail": "",
            "checked_at": "2026-08-28T10:00:00+00:00",
        },
        base_dir=tmp_path,
    )
    llm_health.write_llm_status(
        {
            "ok": False,
            "reason": "logged_out",
            "detail": "x",
            "checked_at": "2026-08-28T12:00:00+00:00",
        },
        base_dir=tmp_path,
    )
    status = llm_health.read_llm_status(tmp_path)
    assert status["ok"] is False
    assert status["reason"] == "logged_out"
    assert status["last_ok_at"] == "2026-08-28T10:00:00+00:00"


def test_read_status_missing_file(tmp_path):
    assert llm_health.read_llm_status(tmp_path) == {}


def test_detail_never_echoes_raw_subprocess_output(monkeypatch):
    # `detail` is rendered in the browser: subprocess stderr can carry paths
    # and command lines, so it belongs in the log, not in the HTTP response.
    monkeypatch.setattr(llm_health.shutil, "which", lambda _: "/usr/bin/claude")
    secret = "/Users/someone/.claude/.credentials.json is unreadable"

    result = llm_health.claude_cli_health(
        ttl_seconds=0, _run=lambda cmd: _cp("", stderr=secret, returncode=2)
    )

    assert result["reason"] == "error"
    assert secret not in result["detail"]
    assert "someone" not in result["detail"]


def test_detail_never_echoes_the_exception_text(monkeypatch):
    monkeypatch.setattr(llm_health.shutil, "which", lambda _: "/usr/bin/claude")

    def _boom(cmd):
        raise OSError("exec /Users/someone/bin/claude failed")

    result = llm_health.claude_cli_health(ttl_seconds=0, _run=_boom)

    assert result["reason"] == "error"
    assert "someone" not in result["detail"]


def test_codex_probe_reads_logged_in_status_from_stderr(monkeypatch):
    # Verified live: `codex login status` prints "Logged in using ChatGPT"
    # to STDERR with exit 0, stdout empty -- a stdout-only check would
    # silently misreport a logged-in session as logged out.
    monkeypatch.setattr(llm_health.shutil, "which", lambda _: "/usr/bin/codex")
    result = llm_health.claude_cli_health(
        provider="codex",
        _run=lambda cmd: _cp("", stderr="Logged in using ChatGPT\n", returncode=0),
    )
    assert result == {"ok": True, "reason": "ok", "detail": "", "checked_at": result["checked_at"],
                      "auth_mode": "account"}


def test_codex_probe_treats_nonzero_exit_as_logged_out_not_error(monkeypatch):
    monkeypatch.setattr(llm_health.shutil, "which", lambda _: "/usr/bin/codex")
    result = llm_health.claude_cli_health(
        provider="codex", _run=lambda cmd: _cp("", stderr="Not logged in", returncode=1)
    )
    assert result["ok"] is False
    assert result["reason"] == "logged_out"


def test_codex_probe_reports_not_installed(monkeypatch):
    monkeypatch.setattr(llm_health.shutil, "which", lambda binary: None)
    result = llm_health.claude_cli_health(provider="codex")
    assert result["reason"] == "not_installed"
    assert "codex" in result["detail"]


def test_claude_and_codex_health_are_cached_independently(monkeypatch):
    monkeypatch.setattr(llm_health.shutil, "which", lambda _: "/usr/bin/x")
    calls = []

    def _run(cmd):
        calls.append(cmd)
        if cmd[0] == "claude":
            return _cp(json.dumps({"loggedIn": True}))
        return _cp("", stderr="Logged in using ChatGPT")

    llm_health.claude_cli_health(provider="claude", _run=_run)
    llm_health.claude_cli_health(provider="codex", _run=_run)
    llm_health.claude_cli_health(provider="claude", _run=_run)  # should hit cache

    assert len(calls) == 2
