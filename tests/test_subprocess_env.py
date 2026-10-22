"""What the LLM CLI subprocess inherits from the parent environment.

The CLI is fed third-party web text and, on the codex provider, can execute
shell commands (verified empirically: `codex exec -s read-only` reads files
outside its `-C` working directory). Whatever sits in this process's
environment is therefore readable by a successful prompt injection, so the
subprocess gets an allow-list of what it needs rather than everything.
"""

from __future__ import annotations

from src.processors.claude_cli_analyzer import _clean_subprocess_env


def test_does_not_leak_unrelated_secrets(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws_secret")
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pw@host/db")

    env = _clean_subprocess_env()

    assert "GITHUB_TOKEN" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "DATABASE_URL" not in env


def test_keeps_what_the_cli_needs_to_run(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/Users/someone")
    monkeypatch.setenv("LANG", "it_IT.UTF-8")

    env = _clean_subprocess_env()

    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/Users/someone"
    assert env["LANG"] == "it_IT.UTF-8"


def test_keeps_provider_credentials_so_login_still_works(monkeypatch) -> None:
    # Stripping these would break API-key auth for codex users.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("CODEX_HOME", "/Users/someone/.codex")

    env = _clean_subprocess_env()

    assert env["OPENAI_API_KEY"] == "sk-test"
    assert env["CODEX_HOME"] == "/Users/someone/.codex"


def test_still_strips_the_parent_claude_session_variables(monkeypatch) -> None:
    # The original reason this function exists: an inherited ANTHROPIC_BASE_URL
    # points the nested CLI at the parent's proxy and makes it hang.
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://parent-proxy")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.setenv("CLAUDECODE", "1")

    env = _clean_subprocess_env()

    assert "ANTHROPIC_BASE_URL" not in env
    assert "CLAUDE_CODE_ENTRYPOINT" not in env
    assert "CLAUDECODE" not in env
