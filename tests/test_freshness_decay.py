"""Items must age between runs, not keep the freshness they once earned.

`signal_score` is computed once, when the item is processed, and stored. Its
recency component (`_novelty_points`: +18 within a day, +8 within three, +2
within a week) was therefore frozen: an item processed on Monday still
carried Monday's +18 on Friday, competing with that day's news on equal
terms. The dashboard merges several days of items on purpose, so this is the
normal state, not an edge case.
"""

from __future__ import annotations

from datetime import date, timedelta

from src.outputs.dashboard_output import current_signal_score

TODAY = date(2026, 9, 22)


def _item(published: date, **overrides):
    item = {
        "title": "Un articolo qualunque",
        "url": f"https://example.com/{published.isoformat()}",
        "summary": "Riassunto.",
        "source_id": "example",
        "relevance_score": 5,
        "content_quality": "full",
        "signal_score": 100,  # what it scored on the day it was processed
        "published_at": f"{published.isoformat()}T09:00:00+00:00",
        "is_valid": True,
    }
    item.update(overrides)
    return item


def test_a_week_old_item_scores_below_todays():
    fresh = current_signal_score(_item(TODAY), today=TODAY)
    stale = current_signal_score(_item(TODAY - timedelta(days=6)), today=TODAY)

    assert stale < fresh


def test_the_stored_score_is_not_trusted_for_recency():
    # Same stored 100, six days apart: the score shown must differ.
    stale = current_signal_score(_item(TODAY - timedelta(days=6)), today=TODAY)

    assert stale != 100


def test_aging_does_not_bury_a_much_stronger_item():
    # Recency is a component, not an override. The whole swing is 18 points,
    # so a genuinely strong week-old item must still beat a weak fresh one.
    strong_old = current_signal_score(
        _item(TODAY - timedelta(days=6), signal_score=100), today=TODAY
    )
    weak_today = current_signal_score(_item(TODAY, signal_score=60), today=TODAY)

    assert strong_old > weak_today


def test_an_item_without_a_date_keeps_its_stored_score():
    # Nothing to age it by; dropping it to zero would bury it for no reason.
    undated = _item(TODAY)
    undated["published_at"] = ""

    assert current_signal_score(undated, today=TODAY) == 100


def test_sorting_puts_todays_news_above_an_equally_scored_old_one():
    from src.outputs.dashboard_output import _sort_dashboard_items

    old = _item(TODAY - timedelta(days=6), title="Vecchia")
    new = _item(TODAY, title="Nuova")

    ordered = _sort_dashboard_items([old, new], today=TODAY)

    assert [i["title"] for i in ordered] == ["Nuova", "Vecchia"]


def test_regenerating_the_page_does_not_age_items_twice():
    """The decay must be idempotent across rebuilds.

    `serve` regenerates the page from the items embedded in the previous one.
    If the aged score were written back over `signal_score`, every restart
    would subtract the recency bonus again and old items would sink without
    limit.
    """
    import json
    import re
    from datetime import date as date_type

    from src.outputs.dashboard_output import DashboardOutput

    item = _item(TODAY - timedelta(days=6))
    output = DashboardOutput(date=date_type(2026, 9, 22))

    def embedded(html: str) -> dict:
        raw = re.search(
            r'<script id="news-data" type="application/json">(.*?)</script>', html, re.S
        ).group(1)
        return json.loads(raw.replace("\\u003c", "<"))[0]

    once = embedded(output.render_html([item]))
    twice = embedded(output.render_html([once]))

    assert once["signal_score"] == 100, "the stored score must survive untouched"
    assert twice["display_signal_score"] == once["display_signal_score"]
