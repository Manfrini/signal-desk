"""Items with no publication date get the date of the run that first saw them.

Some pages publish without a date anywhere we can read — GitHub Trending,
documentation-style listings. Leaving those items dateless had a quiet cost:
the recency window keeps undated items (nothing proves them stale), so they
never aged out and could sit on the page indefinitely.

The run that first collected an item is an upper bound on its publication
date: it cannot have been published after we saw it. Using that bound treats
the item as fresh as it could possibly be — never fresher — and, crucially,
makes it age like everything else.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from src.collectors.base import SourceConfig, normalize_item
from src.storage.json_storage import load_recent_processed_news

TODAY = date(2026, 9, 23)


def _source() -> SourceConfig:
    return SourceConfig(
        id="github_trending",
        name="GitHub Trending",
        company="GitHub",
        url="https://github.com/trending",
        type="html",
        category_hint="coding_ai",
        enabled=True,
        notes="test",
        allowed_domains=("github.com",),
    )


def test_an_undated_item_is_stamped_with_the_collection_date():
    item = normalize_item(_source(), title="Un repo", url="/owner/repo", published_at="")

    assert item["published_at"], "an undated item must still carry a usable date"
    assert item["published_at"][:10] == item["collected_at"][:10]
    assert item["date_quality"] == "estimated"


def test_a_real_publication_date_is_never_overwritten():
    item = normalize_item(
        _source(), title="Un articolo", url="/owner/repo", published_at="2026-09-01T10:00:00+00:00"
    )

    assert item["published_at"].startswith("2026-09-01")
    assert item["date_quality"] != "estimated"


def test_an_estimated_date_lets_the_item_age_out_of_the_window(tmp_path):
    # The point of the estimate: without it this item would stay forever.
    run_day = TODAY - timedelta(days=6)
    folder = tmp_path / run_day.isoformat()
    folder.mkdir(parents=True)
    stale = (TODAY - timedelta(days=20)).isoformat()
    folder.joinpath("news.json").write_text(
        json.dumps(
            [
                {
                    "title": "visto-venti-giorni-fa",
                    "url": "https://github.com/a/b",
                    "published_at": "",
                    "collected_at": f"{stale}T09:00:00+00:00",
                }
            ]
        ),
        encoding="utf-8",
    )

    items = load_recent_processed_news(base_dir=tmp_path, date=TODAY, days=7)

    assert [i["title"] for i in items] == []


def test_an_undated_item_first_seen_recently_is_kept(tmp_path):
    run_day = TODAY - timedelta(days=2)
    folder = tmp_path / run_day.isoformat()
    folder.mkdir(parents=True)
    folder.joinpath("news.json").write_text(
        json.dumps(
            [
                {
                    "title": "visto-due-giorni-fa",
                    "url": "https://github.com/a/b",
                    "published_at": "",
                    "collected_at": f"{run_day.isoformat()}T09:00:00+00:00",
                }
            ]
        ),
        encoding="utf-8",
    )

    items = load_recent_processed_news(base_dir=tmp_path, date=TODAY, days=7)

    assert [i["title"] for i in items] == ["visto-due-giorni-fa"]
