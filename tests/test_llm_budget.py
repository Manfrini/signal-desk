"""The optional daily LLM spend cap (src/processors/llm_budget.py)."""

from __future__ import annotations

import json

from src.processors import llm_budget
from src.storage.json_storage import save_json


def _write_log(tmp_path, records):
    save_json(tmp_path / "llm_usage_log.json", {"runs": records})


def test_no_cap_configured_never_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_budget, "llm_daily_budget_usd", lambda **_: None)
    _write_log(tmp_path, [{"date": "2026-09-05", "total_cost_usd": 999.0}])

    assert llm_budget.is_budget_exceeded(base_dir=tmp_path) is False


def test_today_spend_sums_only_todays_records(tmp_path):
    _write_log(
        tmp_path,
        [
            {"date": "2026-09-04", "total_cost_usd": 5.0},
            {"date": "2026-09-05", "total_cost_usd": 1.5},
            {"date": "2026-09-05", "total_cost_usd": 0.25},
        ],
    )

    assert llm_budget.today_spend_usd(base_dir=tmp_path, today="2026-09-05") == 1.75


def test_missing_log_file_means_zero_spend(tmp_path):
    assert llm_budget.today_spend_usd(base_dir=tmp_path, today="2026-09-05") == 0


def test_exceeded_when_spend_reaches_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_budget, "llm_daily_budget_usd", lambda **_: 2.0)
    _write_log(tmp_path, [{"date": "2026-09-05", "total_cost_usd": 2.0}])

    assert llm_budget.is_budget_exceeded(base_dir=tmp_path, today="2026-09-05") is True


def test_not_exceeded_when_below_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_budget, "llm_daily_budget_usd", lambda **_: 2.0)
    _write_log(tmp_path, [{"date": "2026-09-05", "total_cost_usd": 1.99}])

    assert llm_budget.is_budget_exceeded(base_dir=tmp_path, today="2026-09-05") is False


def test_estimated_cost_records_count_toward_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_budget, "llm_daily_budget_usd", lambda **_: 1.0)
    _write_log(
        tmp_path,
        [{"date": "2026-09-05", "total_cost_usd": 1.5, "cost_estimated": True}],
    )

    assert llm_budget.is_budget_exceeded(base_dir=tmp_path, today="2026-09-05") is True


def test_budget_status_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_budget, "llm_daily_budget_usd", lambda **_: 5.0)
    _write_log(tmp_path, [{"date": "2026-09-05", "total_cost_usd": 1.2345}])

    status = llm_budget.budget_status(base_dir=tmp_path, today="2026-09-05")

    assert status == {"budget_usd": 5.0, "spent_usd": 1.2345, "exceeded": False}


def test_today_spend_uses_the_utc_day_not_the_local_one(tmp_path, monkeypatch):
    # Regression: every writer stamps today_utc(); comparing against the
    # local date summed the wrong day (reading 0.0 and resetting the cap
    # early) on any machine not set to UTC.
    from src.storage.json_storage import today_utc

    monkeypatch.setattr(llm_budget, "llm_daily_budget_usd", lambda **_: 1.0)
    _write_log(tmp_path, [{"date": today_utc().isoformat(), "total_cost_usd": 5.0}])

    assert llm_budget.today_spend_usd(base_dir=tmp_path) == 5.0
    assert llm_budget.is_budget_exceeded(base_dir=tmp_path) is True


def test_section_briefs_do_not_build_their_own_analyzer_when_over_budget(monkeypatch):
    # Regression: callers signal "over budget" by passing analyzer=None, but
    # the guard's is_available() arm let _generate_with_llm build a fresh
    # analyzer and make a real paid call anyway -- uncapped, and unlogged
    # (the caller's usage append is gated on the analyzer it thinks is None).
    from src.processors import section_briefs

    monkeypatch.setattr(section_briefs, "is_available", lambda *a, **k: True)
    monkeypatch.setattr(section_briefs, "is_budget_exceeded", lambda *a, **k: True)

    def _explode(*a, **k):
        raise AssertionError("built an analyzer despite the budget cap")

    monkeypatch.setattr(section_briefs, "ClaudeCliAnalyzer", _explode)

    briefs = section_briefs.generate_section_briefs(
        [{"title": "x", "url": "https://a.com/1", "signal_type": "official_news"}],
        llm_enabled=True,
    )

    assert set(briefs.keys()) == set(section_briefs.SECTION_KEYS)  # deterministic fallback


def test_newsletter_does_not_build_its_own_analyzer_when_over_budget(monkeypatch):
    from src.processors import newsletter

    monkeypatch.setattr(newsletter, "is_available", lambda *a, **k: True)
    monkeypatch.setattr(newsletter, "is_budget_exceeded", lambda *a, **k: True)

    def _explode(*a, **k):
        raise AssertionError("built an analyzer despite the budget cap")

    monkeypatch.setattr(newsletter, "ClaudeCliAnalyzer", _explode)

    nl = newsletter.generate_newsletter(
        [{"title": "x", "url": "https://a.com/1", "company": "OpenAI",
          "published_at": "2026-07-20", "signal_score": 10, "summary": "s"}],
        date_from="2026-07-19", date_to="2026-07-25", llm_enabled=True,
    )

    assert nl["intro"]  # deterministic fallback text, no LLM call
