"""No canned "why it matters" / "possible action" text.

These two fields used to fall back to one pre-written sentence per topic, so
80 of 165 articles on a real page carried a line they shared with another
article — one of them repeated nineteen times. Filler that reads the same
everywhere is worse than an empty field: it looks like the product has
nothing to say and is hiding it.

When there is no real, item-specific insight the page now says so, and says
what to do about it: re-run the analysis. An honest gap beats a fake answer.
"""

from __future__ import annotations

from datetime import date

from src.outputs.dashboard_output import DashboardOutput, insight_placeholder
from src.processors.intelligence import build_intelligence_fields

RAW = {
    "title": "Un annuncio qualunque",
    "url": "https://example.com/a",
    "source_id": "example",
    "published_at": "2026-09-25T09:00:00+00:00",
}


def _fields(**over):
    return build_intelligence_fields(
        {**RAW, **over},
        title=str(over.get("title", RAW["title"])),
        relevance_score=int(over.get("relevance_score", 4)),
        content_quality="full",
        category="coding_ai",
    )


def test_no_insight_means_an_empty_field_not_a_canned_sentence():
    fields = _fields()

    assert fields["why_it_matters"] == ""
    assert fields["possible_action"] == ""


def test_two_articles_on_the_same_topic_do_not_share_a_sentence():
    a = _fields(title="Primo articolo", url="https://example.com/1")
    b = _fields(title="Secondo articolo", url="https://example.com/2")

    assert not (a["why_it_matters"] and a["why_it_matters"] == b["why_it_matters"])


def test_an_unanalysed_item_says_the_analysis_has_not_run():
    note = insight_placeholder({"url": "https://example.com/a"})

    assert "analisi" in note.lower()
    assert "non ancora eseguita" in note.lower()


def test_an_unanalysed_card_offers_to_analyse_that_one_article():
    # The batch run only covers the last 24h, so a blanket "re-run the
    # analysis" would be a promise the product cannot keep on an older item.
    # The offer is per article, and the reader decides if it is worth it.
    item = {**RAW, "summary": "Un riassunto.", "relevance_score": 4,
            "content_quality": "full", "category": "coding_ai", "is_valid": True,
            "why_it_matters": "", "possible_action": ""}

    html = DashboardOutput(date=date(2026, 9, 25)).render_html([item])

    assert 'data-insight-url="https://example.com/a"' in html
    assert "Analizza questo articolo" in html


def test_an_already_analysed_card_is_not_offered_the_button():
    item = {**RAW, "summary": "Un riassunto.", "relevance_score": 4,
            "content_quality": "full", "category": "coding_ai", "is_valid": True,
            "why_it_matters": "", "possible_action": "", "insight_source": "claude_cli"}

    html = DashboardOutput(date=date(2026, 9, 25)).render_html([item])

    assert 'data-insight-url="https://example.com/a"' not in html


def test_an_item_the_ai_answered_incompletely_says_so():
    # The analysis did run — telling the reader to re-run it would be wrong.
    note = insight_placeholder({"url": "https://example.com/a", "insight_source": "claude_cli"})

    assert "rilancia" not in note.lower()
    assert note.strip()


def test_the_page_never_prints_the_old_filler():
    item = {**RAW, "summary": "Un riassunto.", "relevance_score": 4,
            "content_quality": "full", "category": "coding_ai", "is_valid": True,
            "why_it_matters": "", "possible_action": ""}

    html = DashboardOutput(date=date(2026, 9, 25)).render_html([item])

    assert "Questo segnale può influenzare scelte di prodotto o priorità." not in html
    assert "Valuta se cambia le tue priorità attuali." not in html


def test_a_real_insight_is_shown_untouched():
    item = {**RAW, "summary": "Un riassunto.", "relevance_score": 4,
            "content_quality": "full", "category": "coding_ai", "is_valid": True,
            "why_it_matters": "Conta perché cambia il costo per token.",
            "possible_action": "Ricalcola il budget di inferenza.",
            "insight_source": "claude_cli"}

    html = DashboardOutput(date=date(2026, 9, 25)).render_html([item])

    assert "Conta perché cambia il costo per token." in html
    assert "Ricalcola il budget di inferenza." in html
