"""The recency window is about when the news was published.

Two windows used to compound. Collection keeps articles published within the
lookback (7 days from the run), and the dashboard then merged the last 7 run
folders — so an article collected six days ago, already a week old then, was
still on the page at thirteen days. On real data 50 of 190 items were past a
week, every one of them collected six days earlier.

The folders still have to be walked to find the items; what changed is that
an item is judged by its own publication date, not by the run that picked it
up.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from src.storage.json_storage import load_recent_processed_news

TODAY = date(2026, 9, 23)


def _item(published: date | None, title: str) -> dict:
    item = {"title": title, "url": f"https://example.com/{title}", "is_valid": True}
    if published is not None:
        item["published_at"] = f"{published.isoformat()}T09:00:00+00:00"
    else:
        item["published_at"] = ""
    return item


@pytest.fixture()
def data_dir(tmp_path):
    """A run from six days ago holding articles of very different ages."""
    run_day = TODAY - timedelta(days=6)
    folder = tmp_path / run_day.isoformat()
    folder.mkdir(parents=True)
    (folder / "news.json").write_text(
        json.dumps(
            [
                _item(TODAY - timedelta(days=13), "vecchia-di-tredici-giorni"),
                _item(TODAY - timedelta(days=8), "vecchia-di-otto-giorni"),
                _item(TODAY - timedelta(days=6), "dentro-la-finestra"),
                _item(None, "senza-data"),
            ]
        ),
        encoding="utf-8",
    )
    return tmp_path


def _titles(items) -> set[str]:
    return {i["title"] for i in items}


def test_drops_items_published_before_the_window(data_dir):
    items = load_recent_processed_news(base_dir=data_dir, date=TODAY, days=7)

    assert "vecchia-di-tredici-giorni" not in _titles(items)
    assert "vecchia-di-otto-giorni" not in _titles(items)


def test_keeps_items_published_inside_the_window(data_dir):
    items = load_recent_processed_news(base_dir=data_dir, date=TODAY, days=7)

    assert "dentro-la-finestra" in _titles(items)


def test_keeps_items_with_no_usable_date(data_dir):
    # Nothing proves these are stale, and dropping them would silently lose
    # whole sources. They carry no recency bonus and sort last anyway.
    items = load_recent_processed_news(base_dir=data_dir, date=TODAY, days=7)

    assert "senza-data" in _titles(items)


def test_a_wider_window_reaches_further_back(data_dir):
    # The Fonti page asks for long histories; the filter must follow `days`.
    items = load_recent_processed_news(base_dir=data_dir, date=TODAY, days=30)

    assert "vecchia-di-tredici-giorni" in _titles(items)
