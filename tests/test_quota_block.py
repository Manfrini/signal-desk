"""An exhausted provider quota stops the remaining LLM calls instead of failing each one."""

from __future__ import annotations

from src.processors import claude_cli_analyzer as cca

QUOTA = "Codex (OpenAI) CLI: You've hit your usage limit. try again at 1:25 PM."


def _items(n):
    return [{"url": f"https://example.com/{i}", "title": f"t{i}"} for i in range(n)]


def test_quota_error_stops_the_remaining_batches(monkeypatch):
    calls = []

    def fail(self, prompt, schema=None):
        calls.append(1)
        raise cca.ClaudeCliError(QUOTA)

    monkeypatch.setattr(cca.ClaudeCliAnalyzer, "_invoke", fail)
    analyzer = cca.ClaudeCliAnalyzer(provider="claude", batch_size=1)

    assert analyzer.analyze_items(_items(5)) == {}
    assert len(calls) == 1
    assert "usage limit" in cca.quota_block()


def test_calls_are_skipped_while_the_quota_is_blocked(monkeypatch):
    monkeypatch.setattr(cca.ClaudeCliAnalyzer, "_invoke", lambda *a, **k: (_ for _ in ()).throw(cca.ClaudeCliError(QUOTA)))
    analyzer = cca.ClaudeCliAnalyzer(provider="claude")
    assert analyzer.run_structured("p", {}) is None

    def must_not_run(*a, **k):
        raise AssertionError("the provider must not be called during the cooldown")

    monkeypatch.setattr(cca.ClaudeCliAnalyzer, "_invoke", must_not_run)
    assert analyzer.run_structured("p", {}) is None


def test_other_errors_do_not_block_later_calls(monkeypatch):
    calls = []

    def fail(self, prompt, schema=None):
        calls.append(1)
        raise cca.ClaudeCliError("malformed output")

    monkeypatch.setattr(cca.ClaudeCliAnalyzer, "_invoke", fail)
    analyzer = cca.ClaudeCliAnalyzer(provider="claude", batch_size=1)

    analyzer.analyze_items(_items(3))
    assert len(calls) == 3
    assert cca.quota_block() == ""
