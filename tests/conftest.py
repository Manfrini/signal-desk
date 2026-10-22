"""Shared test setup."""

import pytest

from src.processors import content_fetcher


@pytest.fixture(autouse=True)
def _reset_transcript_throttle(monkeypatch):
    """The transcript throttle keeps process-wide state; a block hit by one test must not leak into the next."""
    monkeypatch.setattr(content_fetcher, "_transcripts_blocked_until", 0.0)
    monkeypatch.setattr(content_fetcher, "_last_transcript_request", 0.0)


@pytest.fixture(autouse=True)
def _no_transcript_status_on_disk(monkeypatch):
    """Tests that hit YouTube for real must not write data/transcript_status.json."""
    monkeypatch.setattr(content_fetcher, "save_transcript_status", lambda *a, **k: None)
    monkeypatch.setattr(content_fetcher, "load_transcript_status", lambda *a, **k: {})


@pytest.fixture(autouse=True)
def _reset_quota_block(monkeypatch):
    from src.processors import claude_cli_analyzer

    monkeypatch.setattr(claude_cli_analyzer, "_quota_blocked_until", 0.0)
    monkeypatch.setattr(claude_cli_analyzer, "_quota_detail", "")
