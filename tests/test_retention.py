"""Data retention: pruning old dated folders and unbounded state files.

Deliberately conservative -- these tests also pin what is NOT touched
(read_urls.json, saved_articles.json, feedback.json, preferences.json).
"""

from __future__ import annotations

from datetime import date

from src.storage import retention
from src.storage.json_storage import load_json, save_json


def _make_folder(base, day):
    folder = base / day
    folder.mkdir()
    (folder / "news.json").write_text("[]", encoding="utf-8")
    return folder


def test_find_old_date_folders_returns_only_folders_past_the_cutoff(tmp_path):
    _make_folder(tmp_path, "2026-01-01")
    _make_folder(tmp_path, "2026-08-01")
    recent = _make_folder(tmp_path, "2026-09-01")

    old = retention.find_old_date_folders(
        base_dir=tmp_path, keep_days=10, today=date(2026, 9, 5)
    )

    names = {p.name for p in old}
    assert names == {"2026-01-01", "2026-08-01"}
    assert recent not in old


def test_find_old_date_folders_ignores_non_date_entries(tmp_path):
    (tmp_path / "not-a-date").mkdir()
    (tmp_path / "profile.json").write_text("{}", encoding="utf-8")

    old = retention.find_old_date_folders(base_dir=tmp_path, keep_days=0, today=date(2026, 9, 5))

    assert old == []


def test_find_old_date_folders_on_missing_base_dir_returns_empty(tmp_path):
    assert retention.find_old_date_folders(base_dir=tmp_path / "nope", keep_days=1) == []


def test_prune_date_folders_deletes_and_reports_what_was_removed(tmp_path):
    old_folder = _make_folder(tmp_path, "2026-01-01")
    recent_folder = _make_folder(tmp_path, "2026-09-01")

    removed = retention.prune_date_folders(base_dir=tmp_path, keep_days=10, today=date(2026, 9, 5))

    assert removed == [old_folder]
    assert not old_folder.exists()
    assert recent_folder.exists()


def test_prune_llm_usage_log_drops_old_records_keeps_recent(tmp_path):
    path = tmp_path / "llm_usage_log.json"
    save_json(path, {"runs": [
        {"date": "2026-01-01", "total_cost_usd": 1.0},
        {"date": "2026-09-01", "total_cost_usd": 2.0},
    ]})

    dropped = retention.prune_llm_usage_log(path=path, keep_days=10, today=date(2026, 9, 5))

    assert dropped == 1
    remaining = load_json(path)["runs"]
    assert [r["date"] for r in remaining] == ["2026-09-01"]


def test_prune_llm_usage_log_on_missing_file_is_a_no_op(tmp_path):
    path = tmp_path / "nope.json"
    assert retention.prune_llm_usage_log(path=path, keep_days=10) == 0


def test_prune_llm_insights_drops_by_generated_at(tmp_path):
    path = tmp_path / "llm_insights.json"
    save_json(path, {"insights": {
        "https://a.com/old": {"summary": "s", "generated_at": "2026-01-01T00:00:00+00:00"},
        "https://a.com/new": {"summary": "s", "generated_at": "2026-09-01T00:00:00+00:00"},
    }})

    dropped = retention.prune_llm_insights(path=path, keep_days=10, today=date(2026, 9, 5))

    assert dropped == 1
    remaining = load_json(path)["insights"]
    assert list(remaining.keys()) == ["https://a.com/new"]


def test_prune_llm_insights_keeps_entries_with_no_generated_at(tmp_path):
    # An entry that predates the generated_at field (or is malformed) is
    # kept rather than guessed at -- pruning is opt-in, not aggressive.
    path = tmp_path / "llm_insights.json"
    save_json(path, {"insights": {"https://a.com/x": {"summary": "s"}}})

    dropped = retention.prune_llm_insights(path=path, keep_days=1, today=date(2026, 9, 5))

    assert dropped == 0
    assert "https://a.com/x" in load_json(path)["insights"]


def test_prune_never_imports_the_untouchable_state_paths():
    # No function in this module accepts these files at all -- this test
    # documents that constraint so it survives a future refactor. Checked as
    # imported symbols (not raw source text, which also contains this same
    # explanation in the module docstring).
    names = set(vars(retention).keys())
    untouchable = {
        "DEFAULT_READ_STATE_PATH",
        "DEFAULT_SAVED_PATH",
        "DEFAULT_FEEDBACK_PATH",
        "DEFAULT_PREFERENCES_PATH",
    }
    assert not (names & untouchable)
